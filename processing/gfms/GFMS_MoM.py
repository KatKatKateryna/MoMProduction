import csv
import io
import os

import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import settings
from processing.glofas.GloFAS_tool import glofas_lead, glofas_scores
from processing.utilities import (
    lead_hours,
    read_data,
    severity_from_hazard_and_risk_scores,
    gfms_steps_csv,
)

GFMS_HEADER = (
    "pfaf_id,GFMS_TotalArea_km,GFMS_perc_Area,GFMS_MeanDepth,GFMS_MaxDepth,"
    "GFMS_Duration\n"
)
GFMS_SCORE_FIELDS = [
    "GFMS_area_score",
    "GFMS_perc_area_score",
    "MeanD_Score",
    "MaxD_Score",
    "Duration_Score",
    "Sum_Score",
]


def _gfms_score_np(value, wt, min_pt, max_pt):
    """min_pt per wt of value, capped at max_pt (arrays)"""
    value = np.asarray(value, dtype=float)
    wt, min_pt, max_pt = float(wt), float(min_pt), float(max_pt)
    return np.where(value / wt > max_pt, max_pt, min_pt * value / wt)


def _gfms_score(value, wt, min_pt, max_pt):
    """min_pt per wt of value, capped at max_pt"""
    return str(float(_gfms_score_np(value, wt, min_pt, max_pt)))


def gfms_sum_score(area, perc_area, mean_depth, max_depth, duration):
    """the GFMS Sum_Score of gfms_scores, on arrays"""
    weightage = read_data(os.path.join(settings.BASE_DATA_DIR, "GFMS_Weightage.csv"))
    w = {c: weightage[c][0] for c in weightage.columns}
    return (
        _gfms_score_np(area, w["GFMS_Area_wt"], w["GFMS_Area_Min_pt"], w["GFMS_Area_max_pt"])
        + _gfms_score_np(perc_area, w["GFMS_percArea_wt"], w["GFMS_percArea_Minpt"], w["GFMS_percArea_Maxpt"])
        + _gfms_score_np(mean_depth, w["GFMS_Meandepth_wt"], w["GFMS_Meandepth_Minpt"], w["GFMS_Meandepth_Maxpt"])
        + _gfms_score_np(max_depth, w["GFMS_Maxdepth_wt"], w["GFMS_Maxdepth_Minpt"], w["GFMS_Maxdepth_Maxpt"])
        + _gfms_score_np(duration, w["GFMS_Duration_wt"], w["GFMS_Duration_Minpt"], w["GFMS_Duration_Maxpt"])
    )


def _gfms_hazard(gfms, glofas):
    """the MoM hazard of the GFMS score (GloFAS + GFMS, twice GFMS without
    GloFAS), 0 without GFMS"""
    return np.where(gfms > 0, np.where(glofas > 0, glofas + gfms, 2 * gfms), 0)


def gfms_step_durations(steps, history):
    """GFMS_Duration of every step row of a GFMS step file: the history
    (pfaf_id -> GFMS_Duration of the summary 3 h before; 0 without) until the
    first step whose running maximum floods over 100 km2, then 3 h more for
    that step and every later one (the running maximum only grows)"""
    area = steps.pivot_table(index="pfaf_id", columns="step_h", values="area", aggfunc="first")
    hours = range(int(min(area.columns)), int(max(area.columns)) + 1, 3)
    area = area.reindex(columns=hours).fillna(0)
    duration = history.reindex(area.index).fillna(0).to_numpy(dtype=float)
    table = {}
    for h in hours:
        duration = np.where(area[h].to_numpy() > 100.0, duration + 3, duration)
        table[h] = duration
    table = pd.DataFrame(table, index=area.index)
    return table.stack().reindex(
        pd.MultiIndex.from_arrays([steps["pfaf_id"], steps["step_h"]])
    ).to_numpy()


def gfms_lead(gfms_csv, glofas_score, risk, target, alert_func):
    """Lead_h_GFMS per pfaf_id (index of risk): hours from target (YYYYMMDDHH)
    to the first 3 h step of the GFMS window whose GFMS hazard gives an alert
    above Information (alert_func): the GFMS score of the running maximum
    up to that step (each pixel the highest value so far, as the summary
    aggregates the window) with its duration, plus the GloFAS score (twice
    the GFMS score without GloFAS), as the MoM adds them. The duration
    (gfms_step_durations) is that of the summary 3 h before until the
    running maximum floods over 100 km2, then 3 h more per step. At least 0; -1 where the summary's GFMS hazard (the one the MoM uses)
    gives no such alert, or no step does.

    glofas_score: the GloFAS Sum_Score per pfaf_id before the doubling;
    risk: Scaled_Riverine_Risk, Scaled_Coastal_Risk per pfaf_id."""
    path = gfms_steps_csv(gfms_csv)
    if not os.path.exists(path):
        logging.warning(f"no GFMS step file, Lead_h_GFMS -1: {path}")
        return pd.Series(-1, index=risk.index).groupby(level=0).first()
    steps = pd.read_csv(path)
    summary = read_data(gfms_csv).set_index("pfaf_id")
    stamp = datetime.strptime(
        os.path.basename(gfms_csv)[len("Flood_byStor_") : -len(".csv")], "%Y%m%d%H"
    )
    before = os.path.join(
        os.path.dirname(gfms_csv),
        f"Flood_byStor_{(stamp - timedelta(hours=3)).strftime('%Y%m%d%H')}.csv",
    )
    history = (
        read_data(before).set_index("pfaf_id")["GFMS_Duration"]
        if os.path.exists(before)
        else pd.Series(dtype=float)
    )
    duration = gfms_step_durations(steps, history)
    gfms = gfms_sum_score(steps["area"], steps["perc_area"], steps["mean"], steps["max"], duration)
    glofas = steps["pfaf_id"].map(glofas_score).fillna(0).to_numpy()
    steps["Hazard_Score"] = _gfms_hazard(gfms, glofas)
    offset = (stamp - datetime.strptime(target, "%Y%m%d%H")).total_seconds() / 3600
    steps["lead_h"] = (offset + steps["step_h"]).clip(lower=0).astype(int)
    leads = lead_hours(steps, risk, alert_func)

    # the summary's GFMS hazard decides whether GFMS triggers at all
    whole = summary.reset_index()
    whole["Hazard_Score"] = _gfms_hazard(
        gfms_sum_score(
            whole["GFMS_TotalArea_km"], whole["GFMS_perc_Area"],
            whole["GFMS_MeanDepth"], whole["GFMS_MaxDepth"], whole["GFMS_Duration"],
        ),
        whole["pfaf_id"].map(glofas_score).fillna(0).to_numpy(),
    )
    whole["lead_h"] = 0
    triggered = lead_hours(whole, risk, alert_func) >= 0
    return leads.where(triggered, -1)


def gfms_scores(gfms_csv, out_csv):
    """the GFMS summary gfms_csv with the GFMS_SCORE_FIELDS added, scored
    with GFMS_Weightage.csv, written to out_csv; used by the GFMS MoM
    (flood_severity) and the HWRF/HAFS MoM (update_HWRF_MoM)"""
    weightage = read_data(os.path.join(settings.BASE_DATA_DIR, "GFMS_Weightage.csv"))
    w = {c: weightage[c][0] for c in weightage.columns}
    # no gfms_csv: no watershed flooded
    with (
        open(gfms_csv, "r", encoding="UTF-8") if gfms_csv else io.StringIO(GFMS_HEADER)
    ) as GFMS_file, open(out_csv, "w", newline="\n", encoding="utf-8") as csvfile:
        GFMS_w_score = csv.writer(csvfile)
        for row_count, row in enumerate(csv.reader(GFMS_file)):
            if row_count == 0:
                # ['pfaf_id', 'GFMS_TotalArea_km', 'GFMS_perc_Area', 'GFMS_MeanDepth', 'GFMS_MaxDepth', 'GFMS_Duration']
                GFMS_w_score.writerow(row + GFMS_SCORE_FIELDS)
                continue
            area, perc_area, mean_depth, max_depth, duration = row[1:6]
            scores = [
                _gfms_score(area, w["GFMS_Area_wt"], w["GFMS_Area_Min_pt"], w["GFMS_Area_max_pt"]),
                _gfms_score(perc_area, w["GFMS_percArea_wt"], w["GFMS_percArea_Minpt"], w["GFMS_percArea_Maxpt"]),
                _gfms_score(mean_depth, w["GFMS_Meandepth_wt"], w["GFMS_Meandepth_Minpt"], w["GFMS_Meandepth_Maxpt"]),
                _gfms_score(max_depth, w["GFMS_Maxdepth_wt"], w["GFMS_Maxdepth_Minpt"], w["GFMS_Maxdepth_Maxpt"]),
                _gfms_score(duration, w["GFMS_Duration_wt"], w["GFMS_Duration_Minpt"], w["GFMS_Duration_Maxpt"]),
            ]
            Sum_Score = str(
                float(scores[0]) + float(scores[1]) + float(scores[2]) + float(scores[3]) + float(scores[4])
            )
            GFMS_w_score.writerow(row + scores + [Sum_Score])


def mofunc_gfms(row):
    if row["Severity"] >= 0.8 or row["Hazard_Score"] >= 80:
        return "Warning"
    elif row["Severity"] >= 0.6 or row["Hazard_Score"] >= 60:
        return "Watch"
    elif row["Severity"] >= 0.35 or row["Hazard_Score"] >= 35:
        return "Advisory"
    elif row["Severity"] > 0 or row["Hazard_Score"] > 0:
        return "Information"


def flood_severity(GFMS_Table, GloFas_Table, adate):

    # outputs file
    Final_Attributes_csv = os.path.join(
        settings.GFMS_MOM_DIR, "Final_Attributes_{}.csv".format(adate)
    )
    Attributes_Clean_csv = os.path.join(
        settings.GFMS_MOM_DIR, "Attributes_Clean_{}.csv".format(adate)
    )

    # already processed
    if os.path.exists(Final_Attributes_csv) and os.path.exists(Attributes_Clean_csv):
        # print('already processed: ',adate)
        logging.info(f"GFMS MoM already processed: {adate}")
        return

    logging.info(
        f"GFMS MoM inputs for {adate}: "
        f"gfms={os.path.basename(GFMS_Table)}, "
        f"glofas={os.path.basename(GloFas_Table)}"
    )

    # GFMS_Weightage.csv
    weightage = read_data(os.path.join(settings.BASE_DIR, "data", "GFMS_Weightage.csv"))

    GFMS_w_score_csv = os.path.join(
        settings.GFMS_PROC_DIR, "GFMS_w_score_{}.csv".format(adate)
    )
    gfms_scores(GFMS_Table, GFMS_w_score_csv)
    # GFMS Done

    GloFas_w_score_csv, GloFas_w_Avgscore_csv = glofas_scores(
        GloFas_Table, weightage, settings.GFMS_PROC_DIR, adate
    )

    # Read Watershed attribute and join the GloFas and GFMs Score and calculate severity
    GFMS = read_data(GFMS_w_score_csv)
    GloFas = read_data(GloFas_w_Avgscore_csv)
    Attributes = read_data(os.path.join(settings.BASE_DIR, "data", "Attributes.csv"))
    join = pd.merge(
        GloFas.set_index("pfaf_id"),
        GFMS.set_index("pfaf_id"),
        on="pfaf_id",
        how="outer",
    )
    PDC_resilience = read_data(
        os.path.join(settings.BASE_DIR, "data", "Resilience_Index.csv")
    )
    # left, not inner: an inner join dropped the 21 watersheds whose ISO is
    # missing from Resilience_Index.csv (as in HWRF_MoM)
    join1 = pd.merge(
        Attributes,
        PDC_resilience[["ISO", "Resilience_Index", " NormalizedLackofResilience "]],
        on="ISO",
        how="left",
    )
    Final_Attributes = pd.merge(
        join1.set_index("pfaf_id"), join, on="pfaf_id", how="outer"
    )
    Final_Attributes[["Sum_Score_x", "Sum_Score_y"]] = Final_Attributes[
        ["Sum_Score_x", "Sum_Score_y"]
    ].fillna(value=0)
    # GloFAS and GFMS scores before the doubling, for the lead times
    glofas_score = Final_Attributes["Sum_Score_x"].groupby(level=0).first()
    gfms_score = Final_Attributes["Sum_Score_y"].groupby(level=0).first()

    Final_Attributes.loc[Final_Attributes["Sum_Score_y"] == 0, "Sum_Score_x"] = (
        Final_Attributes["Sum_Score_x"] * 2
    )
    Final_Attributes.loc[Final_Attributes["Sum_Score_x"] == 0, "Sum_Score_y"] = (
        Final_Attributes["Sum_Score_y"] * 2
    )
    Final_Attributes = Final_Attributes.assign(
        Hazard_Score=lambda x: Final_Attributes["Sum_Score_x"]
        + Final_Attributes["Sum_Score_y"]
    )
    # Final_Attributes = Final_Attributes.assign(
    # Scaled_Riverine_Risk=lambda x: Final_Attributes['rfr_score'] * 20 * Final_Attributes[' NormalizedLackofResilience '])
    # Kept: dropping these also lost the GloFAS/GFMS columns of watersheds
    # a later source keeps. Culled once, at the end of final_alert_pdc.
    # Final_Attributes = Final_Attributes[Final_Attributes.Hazard_Score != 0]
    Final_Attributes.drop(
        Final_Attributes.index[
            (Final_Attributes["rfr_score"] == 0) & (Final_Attributes["cfr_score"] == 0)
        ],
        inplace=True,
    )
    Final_Attributes = Final_Attributes.assign(
        Scaled_Riverine_Risk=lambda x: Final_Attributes["rfr_score"] * 20
    )
    Final_Attributes = Final_Attributes.assign(
        Scaled_Coastal_Risk=lambda x: Final_Attributes["cfr_score"] * 20
    )
    # Kept: dropping these also lost the GloFAS/GFMS columns of watersheds
    # a later source keeps. Culled once, at the end of final_alert_pdc.
    # Final_Attributes = Final_Attributes[Final_Attributes.Hazard_Score != 0]
    
    ################# TypeError: loop of ufunc does not support argument 0 of type float which has no callable log method
    
    # Final_Attributes = Final_Attributes.assign(
    # Severity=lambda x: scipy.stats.norm(np.log(100 - Final_Attributes['Scaled_Riverine_Risk']), 1).cdf(
    # np.log(Final_Attributes['Hazard_Score'])))
    cols = [
        "Scaled_Riverine_Risk",
        "Scaled_Coastal_Risk",
        "Hazard_Score",
    ]
    Final_Attributes[cols] = Final_Attributes[cols].apply(pd.to_numeric, errors="coerce")

    Final_Attributes["Severity"] = severity_from_hazard_and_risk_scores(Final_Attributes)

    Final_Attributes["Alert"] = Final_Attributes.apply(mofunc_gfms, axis=1)
    risk = Final_Attributes[["Scaled_Riverine_Risk", "Scaled_Coastal_Risk"]]
    leads = {
        "Lead_h_GFMS": lambda: gfms_lead(
            GFMS_Table, glofas_score, risk, adate + "00", mofunc_gfms
        ),
        "Lead_h_GloFAS": lambda: glofas_lead(
            GloFas_Table, glofas_score, gfms_score, risk, adate + "00", mofunc_gfms
        ),
    }
    for column, lead in leads.items():
        try:
            Final_Attributes[column] = Final_Attributes.index.map(lead())
        except Exception as e:
            logging.warning(f"{column} failed for {adate}, set to -1: {e}")
            Final_Attributes[column] = -1
    # Final_Attributes['Mod_Alert'] = Final_Attributes.apply(func, axis=1)
    # Final_Attributes.to_csv('Final_Attributes', encoding='utf-8-sig')
    Final_Attributes.to_csv(Final_Attributes_csv, encoding="utf-8-sig")

    Attributes_Clean = pd.merge(
        join1.set_index("pfaf_id"),
        Final_Attributes[["Alert"]],
        on="pfaf_id",
        how="right",
    )
    # Attributes_Clean = pd.merge(join1.set_index('pfaf_id'), Final_Attributes[['Alert', 'Mod_Alert']], on='pfaf_id', how='right')
    # Attributes_Clean.to_csv('Attributes_Clean.csv', encoding='utf-8-sig')
    Attributes_Clean.to_csv(Attributes_Clean_csv, encoding="utf-8-sig")

    os.remove(GloFas_w_score_csv)
    os.remove(GloFas_w_Avgscore_csv)
    os.remove(GFMS_w_score_csv)


def main():
    # flood_severity("../data/testdata/gfms_fix/Flood_byStor_2020051600.csv","../data/testdata/glofas/threspoints_2020051600.csv","20200516")
    # flood_severity("../data/testdata/gfms_fix/Flood_byStor_2020051700.csv","../data/testdata/glofas/threspoints_2020051700.csv","20200517")
    # flood_severity("../data/testdata/gfms_fix/Flood_byStor_2020051800.csv","../data/testdata/glofas/threspoints_2020051800.csv","20200518")
    # flood_severity("../data/testdata/gfms_fix/Flood_byStor_2020051900.csv","../data/testdata/glofas/threspoints_2020051900.csv","20200519")
    # flood_severity("../data/testdata/gfms_fix/Flood_byStor_2020052000.csv","../data/testdata/glofas/threspoints_2020052000.csv","20200520")
    # flood_severity("../data/testdata/gfms_fix/Flood_byStor_2020052100.csv","../data/testdata/glofas/threspoints_2020052100.csv","20200521")
    # flood_severity("../data/testdata/gfms_fix/Flood_byStor_2020052200.csv","../data/testdata/glofas/threspoints_2020052200.csv","20200522")
    # flood_severity("../data/testdata/gfms_fix/Flood_byStor_2020052300.csv","../data/testdata/glofas/threspoints_2020052300.csv","20200523")
    # flood_severity("../data/testdata/gfms_fix/Flood_byStor_2020052400.csv","../data/testdata/glofas/threspoints_2020052400.csv","20200524")
    # flood_severity("../data/testdata/gfms_fix/Flood_byStor_2020052500.csv","../data/testdata/glofas/threspoints_2020052500.csv","20200525")
    # flood_severity("../data/testdata/gfms_fix/Flood_byStor_2020052600.csv","../data/testdata/glofas/threspoints_2020052600.csv","20200526")
    # flood_severity("../data/testdata/gfms_fix/Flood_byStor_2020052700.csv","../data/testdata/glofas/threspoints_2020052700.csv","20200527")
    # flood_severity("../data/testdata/gfms_fix/Flood_byStor_2020052800.csv","../data/testdata/glofas/threspoints_2020052800.csv","20200528")
    # flood_severity("../data/testdata/gfms_fix/Flood_byStor_2020052900.csv","../data/testdata/glofas/threspoints_2020052900.csv","20200529")
    # for i in range(1,16):
    #     d_st = str(i).zfill(2)
    #     gfms = "../data/testdata/gfms_fix/Flood_byStor_202006%s00.csv" % d_st
    #     glofas = "../data/testdata/glofas/threspoints_202006%s00.csv" % d_st
    #     adate = "202006" + d_st
    #     print(d_st)
    #     flood_severity(gfms,glofas,adate)
    flood_severity(
        "../data/testdata/gfms_fix/Flood_byStor_2020061700.csv",
        "../data/testdata/glofas/threspoints_2020061700.csv",
        "20200617",
        "../data/testdata/flood",
    )


if __name__ == "__main__":
    main()
