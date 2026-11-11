"""
HWRF_MoM.py
    -- HWRF and MoM integration
"""

import csv
import glob
import io
import logging
import os
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

import settings
from processing.gfms.GFMS_MoM import gfms_lead, gfms_scores
from processing.cycle_inputs import (
    dfo_for_cycle,
    gfms_for_cycle,
    rain_for_cycle,
    viirs_for_cycle,
)
from processing.glofas.GloFAS_tool import glofas_download, glofas_lead, glofas_scores
from processing.utilities import (
    get_current_processing_datehour,
    get_latestitems,
    hwrf_today,
    lead_hours,
    read_data,
    severity_from_hazard_and_risk_scores,
    hafs_steps_csv,
)


def hwrf_score(area, perc_area, mean_rain, max_rain, w):
    """HWRF_area_score, HWRF_percarea_score, MeanRain_Score, MaxRain_Score
    (str) and HWRFTot_Score (float) of a rain summary row, with the
    HWRF_Weightage.csv frame w"""
    area, perc_area = float(area), float(perc_area)
    mean_rain, max_rain = float(mean_rain), float(max_rain)
    if area / float(w.HWRF_Area_wt[0]) > float(w.HWRF_Area_max_pt[0]):
        area_score = str(float(w.HWRF_Area_max_pt[0]))
    else:
        area_score = str(
            float(w.HWRF_Area_Min_pt[0]) * area / float(w.HWRF_Area_wt[0])
        )
    if perc_area / float(w.HWRF_percArea_wt[0]) > float(w.HWRF_percArea_Maxpt[0]):
        percarea_score = str(float(w.HWRF_percArea_Maxpt[0]))
    else:
        percarea_score = str(
            float(w.HWRF_percArea_Minpt[0]) * perc_area / float(w.HWRF_percArea_wt[0])
        )
    mean_score = "0"
    if mean_rain >= float(w.HWRF_MeanRain_minwt[0]):
        score = (mean_rain - float(w.HWRF_MeanRain_minwt[0])) / float(
            w.HWRF_MeanRain_increment[0]
        ) + float(w.HWRF_MeanRain_Minpt[0])
        if score > float(w.HWRF_MeanRain_Maxpt[0]):
            score = float(w.HWRF_MeanRain_Maxpt[0])
        mean_score = str(score)
    max_score = "0"
    if max_rain >= float(w.HWRF_MaxRain_minwt[0]):
        score = (max_rain - float(w.HWRF_MaxRain_minwt[0])) / float(
            w.HWRF_MaxRain_increment[0]
        ) + float(w.HWRF_MaxRain_Minpt[0])
        if score > float(w.HWRF_MaxRain_Maxpt[0]):
            score = float(w.HWRF_MaxRain_Maxpt[0])
        max_score = str(score)
    # only count rain area that reaches mean or max, otherwise it
    # counts large area covered with barely any rain
    if float(mean_score) == 0 and float(max_score) == 0:
        area_score = "0"
        percarea_score = "0"
    total = (
        float(area_score)
        + float(percarea_score)
        + float(mean_score)
        + float(max_score)
    ) * 2.5
    return area_score, percarea_score, mean_score, max_score, total


def hafs_lead(rain_csv, cycle, risk, target, alert_func):
    """Lead_h_HAFS per pfaf_id (index of risk): hours from target (YYYYMMDDHH)
    to the first 3 h step of the cycle that gives an alert above Information
    by its HAFS score alone (HWRFTot_Score): of that step's 3 h rain times
    the number of steps, or of the rain total up to that step; valid time cycle +
    step, at least 0. -1 where the cycle's total does not give such an alert
    (HAFS does not trigger it), or no step does"""
    path = hafs_steps_csv(rain_csv)
    if not os.path.exists(path):
        if os.path.exists(rain_csv) and os.path.getsize(rain_csv) > 0:
            logging.warning(f"no HAFS step file, Lead_h_HAFS -1: {path}")
        return pd.Series(-1, index=risk.index).groupby(level=0).first()
    steps = pd.read_csv(path)
    w = read_data(os.path.join(settings.BASE_DATA_DIR, "HWRF_Weightage.csv"))

    def score(columns):
        return [
            hwrf_score(a, p, m, x, w)[4]
            for a, p, m, x in steps[columns].itertuples(index=False)
        ]

    steps["total"] = score(["area", "perc_area", "mean", "max"])
    steps["Hazard_Score"] = steps["total"]
    if "step_mean" in steps:
        alone = score(["step_area", "step_perc_area", "step_mean", "step_max"])
        steps["Hazard_Score"] = np.maximum(steps["total"], alone)
    offset = (
        datetime.strptime(cycle, "%Y%m%d%H") - datetime.strptime(target, "%Y%m%d%H")
    ).total_seconds() / 3600
    steps["lead_h"] = (offset + steps["step_h"]).clip(lower=0).astype(int)
    leads = lead_hours(steps, risk, alert_func)
    # the cycle's total (last step) decides whether HAFS triggers at all
    last = steps[steps["step_h"] == steps["step_h"].max()]
    triggered = lead_hours(last.assign(Hazard_Score=last["total"]), risk, alert_func) >= 0
    return leads.where(triggered, -1)


def mofunc_hwrf(row):
    if row["Severity"] >= 0.8 or row["Hazard_Score"] >= 80:
        return "Warning"
    elif row["Severity"] >= 0.6 or row["Hazard_Score"] >= 60:
        return "Watch"
    elif row["Severity"] >= 0.35 or row["Hazard_Score"] >= 35:
        return "Advisory"
    elif row["Severity"] > 0 or row["Hazard_Score"] > 0:
        return "Information"


def mofunc_dfo(row):
    if row["Severity"] >= 0.8 or row["Hazard_Score"] >= 80:
        return "Warning"
    elif row["Severity"] >= 0.6 or row["Hazard_Score"] >= 60:
        return "Watch"
    elif row["Severity"] >= 0.35 or row["Hazard_Score"] >= 35:
        return "Advisory"
    elif row["Severity"] > 0 or row["Hazard_Score"] > 0:
        return "Information"


def mofunc_viirs(row):
    if row["Severity"] >= 0.8 or row["Hazard_Score"] >= 80:
        return "Warning"
    elif row["Severity"] >= 0.6 or row["Hazard_Score"] >= 60:
        return "Watch"
    elif row["Severity"] >= 0.35 or row["Hazard_Score"] >= 35:
        return "Advisory"
    elif row["Severity"] > 0 or row["Hazard_Score"] > 0:
        return "Information"


# the lead time columns of the forecast sources; Lead_h of the final alert is
# valid only when one of them is
FORECAST_LEADS = ["Lead_h_GFMS", "Lead_h_GloFAS", "Lead_h_HAFS"]


def observed_lead(df, score, alert_func):
    """Lead_h_DFO / Lead_h_VIIRS per row of df: 0 where the score column
    alone, as the Hazard_Score with the row's risk, gives an alert above
    Information (alert_func, mofunc_dfo or mofunc_viirs): the source observes
    the flood; -1 otherwise"""
    w = pd.DataFrame(
        {
            "Hazard_Score": pd.to_numeric(df[score], errors="coerce").fillna(0),
            "Scaled_Riverine_Risk": df["Scaled_Riverine_Risk"],
            "Scaled_Coastal_Risk": df["Scaled_Coastal_Risk"],
        },
        index=df.index,
    )
    if w.empty:
        return pd.Series(-1, index=df.index)
    with np.errstate(divide="ignore", invalid="ignore"):
        w["Severity"] = severity_from_hazard_and_risk_scores(w)
    alert = w.apply(alert_func, axis=1)
    return pd.Series(
        np.where(alert.isin(["Advisory", "Watch", "Warning"]), 0, -1), index=df.index
    )


def make_gfms_summary(adate):
    """make the missing GFMS summaries when the one of adate does not exist"""
    gfms_sum = os.path.join(settings.GFMS_SUM_DIR, "Flood_byStor_{}.csv".format(adate))
    if os.path.exists(gfms_sum):
        return
    logging.warning(f"GFMS summary not found: {gfms_sum}")
    logging.info(f"making the missing GFMS summaries up to: {adate[:8]}")
    from processing.gfms.GFMS_tool import GFMS_processing

    # the day before first: a summary continues the duration of the one
    # before it
    day_before = datetime.strptime(adate[:8], "%Y%m%d") - timedelta(days=1)
    try:
        GFMS_processing([day_before.strftime("%Y%m%d") + "00", adate[:8] + "00"])
    except Exception:
        logging.exception(f"GFMS summaries failed for {adate}")


def update_HWRF_MoM(adate):
    """HWRF MoM for a date: YYYYMMDDHH"""
    print(f"___________update_HWRF_MoM: {adate}")

    make_gfms_summary(adate)
    gfms_sum = gfms_for_cycle(adate)
    if gfms_sum is None:
        logging.error(f"no GFMS available for {adate}, using no GFMS data")

    glofas_sum = glofas_download(adate)

    hwrf_cycle, hwrf_sum = rain_for_cycle(adate)

    # hwrf_sum may not have
    Final_Attributes_csv = os.path.join(
        settings.HWRF_MOM_DIR,
        f"Final_Attributes_{adate}{settings.HWRF_FILE_TAG}Updated.csv",
    )
    Attributes_Clean_csv = os.path.join(
        settings.HWRF_MOM_DIR,
        f"Attributes_Clean_{adate}{settings.HWRF_FILE_TAG}Updated.csv",
    )

    # already processed
    if os.path.exists(Final_Attributes_csv) and os.path.exists(Attributes_Clean_csv):
        logging.info(f"HWRF MoM already processed: {adate}")
        return
    else:
        logging.info(f"processing HWRF MoM: {adate}")
        logging.info(
            f"HWRF MoM inputs for {adate}: "
            f"gfms={os.path.basename(str(gfms_sum))}, "
            f"glofas={os.path.basename(str(glofas_sum))}, "
            f"hwrf={os.path.basename(str(hwrf_sum))}"
        )

    weightage = read_data(os.path.join(settings.BASE_DATA_DIR, "GFMS_Weightage.csv"))
    HWRF_weightage = read_data(
        os.path.join(settings.BASE_DATA_DIR, "HWRF_Weightage.csv")
    )
    add_field_HWRF = [
        "HWRF_area_score",
        "HWRF_percarea_score",
        "MeanRain_Score",
        "MaxRain_Score",
        "HWRFTot_Score",
    ]

    # Read GFMS Processing data and calculte score
    GFMS_w_score_csv = "GFMS_w_score_{}.csv".format(adate)
    GFMS_w_score_csv = os.path.join(settings.HWRF_PROC_DIR, GFMS_w_score_csv)
    gfms_scores(gfms_sum, GFMS_w_score_csv)

    GloFas_w_score_csv, GloFas_w_Avgscore_csv = glofas_scores(
        glofas_sum, weightage, settings.HWRF_PROC_DIR, adate
    )

    ## Read HWRF rainfall processed data and calculate separate hazard Score
    try:
        with open(hwrf_sum, "r", encoding="UTF-8") as HWRF_file:
            logging.info(f"using: {hwrf_sum}")
            HWRF_reader = csv.reader(HWRF_file)
            HWRF_w_score_csv = f"{settings.HWRF_FILE_TAG}_w_score_{adate}.csv"
            HWRF_w_score_csv = os.path.join(settings.HWRF_PROC_DIR, HWRF_w_score_csv)
            csvfile = open(HWRF_w_score_csv, "w", newline="\n", encoding="utf-8")
            HWRF_w_score = csv.writer(csvfile)
            row_count = 1
            # csv_writer = csv.writer(write_obj)
            for row in HWRF_reader:
                if row_count == 1:
                    for x in add_field_HWRF:
                        row.append(x)
                    HWRF_w_score.writerow(row)
                    row_count = row_count + 1
                elif row == []:
                    continue
                else:
                    (
                        HWRF_area_score,
                        HWRF_percarea_score,
                        MeanRain_Score,
                        MaxRain_Score,
                        HWRFTot_Score,
                    ) = hwrf_score(row[1], row[2], row[3], row[4], HWRF_weightage)
                    results_list = [
                        row[0],
                        row[1],
                        row[2],
                        row[3],
                        row[4],
                        HWRF_area_score,
                        HWRF_percarea_score,
                        MeanRain_Score,
                        MaxRain_Score,
                        HWRFTot_Score,
                    ]
                    HWRF_w_score.writerow(results_list)
            csvfile.close()
    except Exception as e:
        if os.path.exists(hwrf_sum):
            logging.warning(f"HWRF scoring failed for {adate}, skipping HWRF: {e}")

    GFMS = read_data(GFMS_w_score_csv)
    GloFas = read_data(GloFas_w_Avgscore_csv)
    Attributes = read_data(os.path.join(settings.BASE_DATA_DIR, "Attributes.csv"))
    # HWRF=read_data('HWRF_w_score.csv')
    join = pd.merge(
        GloFas.set_index("pfaf_id"),
        GFMS.set_index("pfaf_id"),
        on="pfaf_id",
        how="outer",
    )
    # join0=pd.merge(join, HWRF.set_index('pfaf_id'), on='pfaf_id', how='outer')
    PDC_resilience = read_data(
        os.path.join(settings.BASE_DATA_DIR, "Resilience_Index.csv")
    )
    # left, not inner: the resilience columns feed no formula, and an inner
    # join dropped 21 watersheds whose ISO is missing from Resilience_Index.csv
    join1 = pd.merge(
        Attributes,
        PDC_resilience[["ISO", "Resilience_Index", " NormalizedLackofResilience "]],
        on="ISO",
        how="left",
    )
    # Final_Attributes = pd.merge(join1.set_index('pfaf_id'), join0, on='pfaf_id', how='outer')
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
        Final_Attributes.loc[Final_Attributes["Sum_Score_y"] == 0, "Sum_Score_x"] * 2
    )
    Final_Attributes.loc[Final_Attributes["Sum_Score_x"] == 0, "Sum_Score_y"] = (
        Final_Attributes.loc[Final_Attributes["Sum_Score_x"] == 0, "Sum_Score_y"] * 2
    )
    Final_Attributes = Final_Attributes.assign(
        MOM_Score=lambda x: Final_Attributes["Sum_Score_x"]
        + Final_Attributes["Sum_Score_y"]
    )
    Final_Attributes["Hazard_Score"] = Final_Attributes[["MOM_Score"]]
    try:
        HWRF = read_data(HWRF_w_score_csv)
        Final_Attributes = pd.merge(
            Final_Attributes, HWRF.set_index("pfaf_id"), on="pfaf_id", how="outer"
        )
        Final_Attributes["Flag"] = np.where(
            (Final_Attributes["Hazard_Score"] < Final_Attributes["HWRFTot_Score"]),
            1,
            np.nan,
        ).astype(object)
        Final_Attributes["Hazard_Score"] = Final_Attributes[
            ["Hazard_Score", "HWRFTot_Score"]
        ].max(axis=1)
    except Exception as e:
        if os.path.exists(hwrf_sum):
            logging.warning(f"HWRF merge failed for {adate}, skipping HWRF: {e}")
    # Kept: dropping these also lost the GloFAS/GFMS/rain columns of watersheds
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

    ################# TypeError: loop of ufunc does not support argument 0 of type float which has no callable log method
    cols = [
        "Scaled_Riverine_Risk",
        "Scaled_Coastal_Risk",
        "Hazard_Score",
    ]
    Final_Attributes[cols] = Final_Attributes[cols].apply(
        pd.to_numeric, errors="coerce"
    )

    Final_Attributes["Severity"] = severity_from_hazard_and_risk_scores(
        Final_Attributes
    )
    Final_Attributes["Alert"] = Final_Attributes.apply(mofunc_hwrf, axis=1)
    # lead time of each source on its own (the GFMS and GloFAS parts of
    # MOM_Score, the HAFS score); HWRF has no 3 h steps
    risk = Final_Attributes[["Scaled_Riverine_Risk", "Scaled_Coastal_Risk"]]
    leads = {
        "Lead_h_GFMS": lambda: gfms_lead(gfms_sum, glofas_score, risk, adate, mofunc_hwrf),
        "Lead_h_GloFAS": lambda: glofas_lead(
            glofas_sum, glofas_score, gfms_score, risk, adate, mofunc_hwrf
        ),
    }
    if settings.HWRF_SUM_PREFIX == "hafs":
        leads["Lead_h_HAFS"] = lambda: hafs_lead(hwrf_sum, hwrf_cycle, risk, adate, mofunc_hwrf)
    for column, lead in leads.items():
        try:
            Final_Attributes[column] = Final_Attributes.index.map(lead())
        except Exception as e:
            logging.warning(f"{column} failed for {adate}, set to -1: {e}")
            Final_Attributes[column] = -1
    # Final_Attributes["Flag"] = Final_Attributes["Flag"].astype(object)
    Final_Attributes.loc[Final_Attributes["Alert"] == "Information", "Flag"] = ""
    Final_Attributes.loc[Final_Attributes["Alert"] == "Advisory", "Flag"] = ""
    Final_Attributes.to_csv(Final_Attributes_csv, encoding="utf-8-sig")
    # Final_Attributes.to_csv('Final_Attributes_2021081606.csv', encoding='utf-8-sig')
    Attributes_Clean = pd.merge(
        join1.set_index("pfaf_id"),
        Final_Attributes[["Alert"]],
        on="pfaf_id",
        how="right",
    )
    Attributes_Clean.to_csv(Attributes_Clean_csv, encoding="utf-8-sig")
    logging.info(f"generated: {Final_Attributes_csv}")

    # delete temp files
    os.remove(GloFas_w_score_csv)
    os.remove(GloFas_w_Avgscore_csv)
    os.remove(GFMS_w_score_csv)

    try:
        os.remove(HWRF_w_score_csv)
    except:
        pass

    return


DFO_NO_DATA = (
    ",pfaf_id,1-Day_TotalArea_km2,1-Day_perc_Area,1-Day_CS_TotalArea_km2,"
    "1-Day_CS_perc_Area,2-Day_TotalArea_km2,2-Day_perc_Area,3-Day_TotalArea_km2,"
    "3-Day_perc_Area\n0,0,0,0,0,0,0,0,0,0\n"
)
VIIRS_NO_DATA = (
    "pfaf_id,onedayFlood_Area_km,onedayperc_Area,fivedayFlood_Area_km,"
    "fivedayperc_Area\n0,0,0,0,0\n"
)


def update_HWRFMoM_DFO_VIIRS(adate):
    """
    update HWRFMoM with the latest available DFO and VIIRS
    """
    os.chdir(settings.HWRF_PROC_DIR)

    # first check if it is produced
    hwrf_pattern = adate + settings.HWRF_FILE_TAG + "+"
    file_list = os.listdir(settings.HWRF_MOM_DIR)
    file_found = [1 for x in file_list if hwrf_pattern in x]
    if len(file_found) > 0:
        # this date is already processed
        # print(file_found)
        return

    # the DFO and VIIRS summaries of this cycle; '' if not found
    [dfo_date, dfo_summary] = dfo_for_cycle(adate)
    if dfo_date == "":
        logging.warning(f"no DFO summary for {adate}, using no DFO data")
        dfo_date = "none"
        dfo_summary = None
    [viirs_date, summary_viirs_all] = viirs_for_cycle(adate)
    if viirs_date == "":
        logging.warning(f"no VIIRS summary for {adate}, using no VIIRS data")
        viirs_date = "none"
        summary_viirs_all = None

    # geneate HWRF_DFO_MoM
    MOMOutput = os.path.join(
        settings.HWRF_MOM_DIR,
        f"Final_Attributes_{adate}{settings.HWRF_FILE_TAG}Updated.csv",
    )
    if not os.path.exists(MOMOutput):
        # print('can not find ' + MOMOutput)
        return

    DFO = dfo_summary
    # output
    # Final_Attributes_yyyymmddhhMOM+yyyymmddDFOUpdated.csv
    # Attributes_clean_yyyymmddhhMOM+yyyymmddDFOUpdated.csv
    DFO_Final_Attributes_csv = (
        f"Final_Attributes_{adate}{settings.HWRF_FILE_TAG}+{dfo_date}DFOUpdated.csv"
    )
    DFO_Attributes_Clean_csv = (
        f"Attributes_Clean_{adate}{settings.HWRF_FILE_TAG}+{dfo_date}DFOUpdated.csv"
    )

    weightage = read_data(os.path.join(settings.BASE_DATA_DIR, "DFO_Weightage.csv"))
    Attributes = read_data(os.path.join(settings.BASE_DATA_DIR, "Attributes.csv"))
    PDC_resilience = read_data(
        os.path.join(settings.BASE_DATA_DIR, "Resilience_Index.csv")
    )
    add_field_DFO = [
        "DFO_area_1day_score",
        "DFO_percarea_1day_score",
        "DFO_area_2day_score",
        "DFO_percarea_2day_score",
        "DFO_area_3day_score",
        "DFO_percarea_3day_score",
        "DFOTotal_Score",
    ]

    # Read DFO Processing data and calculate score
    with (
        open(DFO, "r", encoding="UTF-8") if DFO else io.StringIO(DFO_NO_DATA)
    ) as DFO_file:
        DFO_reader = csv.reader(DFO_file)
        DFO_w_score_csv = "DFO_w_score_{}.csv".format(dfo_date)
        csvfile = open(DFO_w_score_csv, "w", newline="\n", encoding="utf-8")
        DFO_w_score = csv.writer(csvfile)
        row_count = 1
        # csv_writer = csv.writer(write_obj)
        for row in DFO_reader:
            if row_count == 1:
                for x in add_field_DFO:
                    row.append(x)
                row_count = row_count + 1
            else:
                if float(row[4]) / float(weightage.DFO_Area_wt[0]) > float(
                    weightage.DFO_Area_max_pt[0]
                ):
                    DFO_area_1day_score = str(
                        float(weightage.DFO_Area_max_pt[0])
                        * float(weightage.one_Day_Multiplier[0])
                    )
                else:
                    DFO_area_1day_score = str(
                        float(weightage.DFO_Area_Min_pt[0])
                        * float(weightage.one_Day_Multiplier[0])
                        * float(row[4])
                        / float(weightage.DFO_Area_wt[0])
                    )
                if float(row[5]) / float(weightage.DFO_percArea_wt[0]) > float(
                    weightage.DFO_percArea_Maxpt[0]
                ):
                    DFO_perc_area_1day_score = str(
                        float(weightage.DFO_percArea_Maxpt[0])
                        * float(weightage.one_Day_Multiplier[0])
                    )
                else:
                    DFO_perc_area_1day_score = str(
                        float(weightage.DFO_percArea_Minpt[0])
                        * float(weightage.one_Day_Multiplier[0])
                        * float(row[5])
                        / float(weightage.DFO_percArea_wt[0])
                    )
                if float(row[6]) / float(weightage.DFO_Area_wt[0]) > float(
                    weightage.DFO_Area_max_pt[0]
                ):
                    DFO_area_2day_score = str(
                        float(weightage.DFO_Area_max_pt[0])
                        * float(weightage.two_Day_Multiplier[0])
                    )
                else:
                    DFO_area_2day_score = str(
                        float(weightage.DFO_Area_Min_pt[0])
                        * float(weightage.two_Day_Multiplier[0])
                        * float(row[6])
                        / float(weightage.DFO_Area_wt[0])
                    )
                if float(row[7]) / float(weightage.DFO_percArea_wt[0]) > float(
                    weightage.DFO_percArea_Maxpt[0]
                ):
                    DFO_perc_area_2day_score = str(
                        float(weightage.DFO_percArea_Maxpt[0])
                        * float(weightage.two_Day_Multiplier[0])
                    )
                else:
                    DFO_perc_area_2day_score = str(
                        float(weightage.DFO_percArea_Minpt[0])
                        * float(weightage.two_Day_Multiplier[0])
                        * float(row[7])
                        / float(weightage.DFO_percArea_wt[0])
                    )
                if float(row[8]) / float(weightage.DFO_Area_wt[0]) > float(
                    weightage.DFO_Area_max_pt[0]
                ):
                    DFO_area_3day_score = str(
                        float(weightage.DFO_Area_max_pt[0])
                        * float(weightage.three_Day_Multiplier[0])
                    )
                else:
                    DFO_area_3day_score = str(
                        float(weightage.DFO_Area_Min_pt[0])
                        * float(weightage.three_Day_Multiplier[0])
                        * float(row[8])
                        / float(weightage.DFO_Area_wt[0])
                    )
                if float(row[9]) / float(weightage.DFO_percArea_wt[0]) > float(
                    weightage.DFO_percArea_Maxpt[0]
                ):
                    DFO_perc_area_3day_score = str(
                        float(weightage.DFO_percArea_Maxpt[0])
                        * float(weightage.three_Day_Multiplier[0])
                    )
                else:
                    DFO_perc_area_3day_score = str(
                        float(weightage.DFO_percArea_Minpt[0])
                        * float(weightage.three_Day_Multiplier[0])
                        * float(row[9])
                        / float(weightage.DFO_percArea_wt[0])
                    )

                Sum_Score = str(
                    (
                        float(DFO_area_1day_score)
                        + float(DFO_perc_area_1day_score)
                        + float(DFO_area_2day_score)
                        + float(DFO_perc_area_2day_score)
                        + float(DFO_area_3day_score)
                        + float(DFO_perc_area_3day_score)
                    )
                )
                score_field = [
                    DFO_area_1day_score,
                    DFO_perc_area_1day_score,
                    DFO_area_2day_score,
                    DFO_perc_area_2day_score,
                    DFO_area_3day_score,
                    DFO_perc_area_3day_score,
                    Sum_Score,
                ]
                for x in score_field:
                    row.append(x)
            DFO_w_score.writerow(row)
    csvfile.close()

    DFO = read_data(DFO_w_score_csv)
    DFO = DFO[DFO.DFOTotal_Score > 0.1]
    DFO = DFO.iloc[:, 1:]
    MOM = read_data(MOMOutput)
    MOM.drop(
        columns=[
            "area_km2",
            "ISO",
            "Admin0",
            "Admin1",
            "rfr_score",
            "cfr_score",
            "Resilience_Index",
            " NormalizedLackofResilience ",
            "Severity",
            "Alert",
        ],
        inplace=True,
    )
    Final_Output_0 = pd.merge(
        MOM.set_index("pfaf_id"), DFO.set_index("pfaf_id"), on="pfaf_id", how="outer"
    )
    join1 = pd.merge(
        Attributes,
        PDC_resilience[["ISO", "Resilience_Index", " NormalizedLackofResilience "]],
        on="ISO",
        how="left",
    )
    Final_Output = pd.merge(
        join1.set_index("pfaf_id"), Final_Output_0, on="pfaf_id", how="outer"
    )
    Final_Output[["Hazard_Score"]] = Final_Output[["Hazard_Score"]].fillna(value=0)
    Final_Output.loc[
        (Final_Output["Hazard_Score"] < Final_Output["DFOTotal_Score"]), "Flag"
    ] = 2
    Final_Output["Hazard_Score"] = Final_Output[["Hazard_Score", "DFOTotal_Score"]].max(
        axis=1
    )
    # Kept: dropping these also lost the GloFAS/GFMS/rain columns of watersheds
    # a later source keeps. Culled once, at the end of final_alert_pdc.
    # Final_Output = Final_Output[Final_Output.Hazard_Score != 0]
    Final_Output.drop(
        Final_Output.index[
            (Final_Output["rfr_score"] == 0) & (Final_Output["cfr_score"] == 0)
        ],
        inplace=True,
    )
    Final_Output = Final_Output.assign(
        Scaled_Riverine_Risk=lambda x: Final_Output["rfr_score"] * 20
    )
    Final_Output = Final_Output.assign(
        Scaled_Coastal_Risk=lambda x: Final_Output["cfr_score"] * 20
    )

    ################# TypeError: loop of ufunc does not support argument 0 of type float which has no callable log method
    cols = [
        "Scaled_Riverine_Risk",
        "Scaled_Coastal_Risk",
        "Hazard_Score",
    ]
    Final_Output[cols] = Final_Output[cols].apply(pd.to_numeric, errors="coerce")

    Final_Output["Severity"] = severity_from_hazard_and_risk_scores(Final_Output)
    Final_Output["Alert"] = Final_Output.apply(mofunc_dfo, axis=1)
    Final_Output["Lead_h_DFO"] = observed_lead(Final_Output, "DFOTotal_Score", mofunc_dfo)
    Final_Output["Flag"] = Final_Output["Flag"].astype(object)
    Final_Output.loc[Final_Output["Alert"] == "Information", "Flag"] = ""
    Final_Output.loc[Final_Output["Alert"] == "Advisory", "Flag"] = ""
    Final_Output.to_csv(DFO_Final_Attributes_csv, encoding="utf-8-sig")
    # Final_Output.to_csv('Final_Attributes_20210701_DFOUpdated.csv', encoding='utf-8-sig')
    join1 = pd.merge(
        Attributes,
        PDC_resilience[["ISO", "Resilience_Index", " NormalizedLackofResilience "]],
        on="ISO",
        how="left",
    )
    Attributes_Clean_DFO_Updated = pd.merge(
        join1.set_index("pfaf_id"),
        Final_Output[["Alert", "Flag"]],
        on="pfaf_id",
        how="right",
    )
    Attributes_Clean_DFO_Updated.to_csv(DFO_Attributes_Clean_csv, encoding="utf-8-sig")
    os.remove(DFO_w_score_csv)

    # generate HWRF_DFO_VIIRS_MoM
    DFO_MOMOutput = DFO_Final_Attributes_csv
    VIIRS_summary_csv = summary_viirs_all

    # output files
    Final_Attributes_csv = f"Final_Attributes_{adate}{settings.HWRF_FILE_TAG}+{dfo_date}DFO+{viirs_date}VIIRSUpdated.csv"
    Final_Attributes_csv = os.path.join(settings.HWRF_MOM_DIR, Final_Attributes_csv)
    Attributes_Clean_csv = f"Attributes_clean_{adate}{settings.HWRF_FILE_TAG}+{dfo_date}DFO+{viirs_date}VIIRSUpdated.csv"
    Attributes_Clean_csv = os.path.join(settings.HWRF_MOM_DIR, Attributes_Clean_csv)

    weightage = read_data(os.path.join(settings.BASE_DATA_DIR, "VIIRS_Weightage.csv"))
    Attributes = read_data(os.path.join(settings.BASE_DATA_DIR, "Attributes.csv"))
    PDC_resilience = read_data(
        os.path.join(settings.BASE_DATA_DIR, "Resilience_Index.csv")
    )
    add_field_VIIRS = [
        "VIIRS_area_1day_score",
        "VIIRS_percarea_1day_score",
        "VIIRS_area_5day_score",
        "VIIRS_percarea_5day_score",
        "VIIRSTotal_Score",
    ]

    # Read VIIRS Processing data and calculate score
    with (
        open(VIIRS_summary_csv, "r", encoding="UTF-8")
        if VIIRS_summary_csv
        else io.StringIO(VIIRS_NO_DATA)
    ) as VIIRS_file:
        VIIRS_reader = csv.reader(VIIRS_file)
        VIIRS_w_score_csv = "VIIRS_w_score_{}.csv".format(viirs_date)
        csvfile = open(VIIRS_w_score_csv, "w", newline="\n", encoding="utf-8")
        VIIRS_w_score = csv.writer(csvfile)
        row_count = 1
        # csv_writer = csv.writer(write_obj)
        for row in VIIRS_reader:
            if row_count == 1:
                for x in add_field_VIIRS:
                    row.append(x)
                row_count = row_count + 1
            else:
                if float(row[1]) / float(weightage.VIIRS_Area_wt[0]) > float(
                    weightage.VIIRS_Area_max_pt[0]
                ):
                    VIIRS_area_1day_score = str(
                        float(weightage.VIIRS_Area_max_pt[0])
                        * float(weightage.one_Day_Multiplier[0])
                    )
                else:
                    VIIRS_area_1day_score = str(
                        float(weightage.VIIRS_Area_Min_pt[0])
                        * float(weightage.one_Day_Multiplier[0])
                        * float(row[1])
                        / float(weightage.VIIRS_Area_wt[0])
                    )
                if float(row[2]) / float(weightage.VIIRS_percArea_wt[0]) > float(
                    weightage.VIIRS_percArea_Maxpt[0]
                ):
                    VIIRS_perc_area_1day_score = str(
                        float(weightage.VIIRS_percArea_Maxpt[0])
                        * float(weightage.one_Day_Multiplier[0])
                    )
                else:
                    VIIRS_perc_area_1day_score = str(
                        float(weightage.VIIRS_percArea_Minpt[0])
                        * float(weightage.one_Day_Multiplier[0])
                        * float(row[2])
                        / float(weightage.VIIRS_percArea_wt[0])
                    )
                if float(row[3]) / float(weightage.VIIRS_Area_wt[0]) > float(
                    weightage.VIIRS_Area_max_pt[0]
                ):
                    VIIRS_area_5day_score = str(
                        float(weightage.VIIRS_Area_max_pt[0])
                        * float(weightage.five_Day_Multiplier[0])
                    )
                else:
                    VIIRS_area_5day_score = str(
                        float(weightage.VIIRS_Area_Min_pt[0])
                        * float(weightage.five_Day_Multiplier[0])
                        * float(row[3])
                        / float(weightage.VIIRS_Area_wt[0])
                    )
                if float(row[4]) / float(weightage.VIIRS_percArea_wt[0]) > float(
                    weightage.VIIRS_percArea_Maxpt[0]
                ):
                    VIIRS_perc_area_5day_score = str(
                        float(weightage.VIIRS_percArea_Maxpt[0])
                        * float(weightage.five_Day_Multiplier[0])
                    )
                else:
                    VIIRS_perc_area_5day_score = str(
                        float(weightage.VIIRS_percArea_Minpt[0])
                        * float(weightage.five_Day_Multiplier[0])
                        * float(row[4])
                        / float(weightage.VIIRS_percArea_wt[0])
                    )
                Sum_Score = str(
                    (
                        float(VIIRS_area_1day_score)
                        + float(VIIRS_perc_area_1day_score)
                        + float(VIIRS_area_5day_score)
                        + float(VIIRS_perc_area_5day_score)
                    )
                )
                score_field = [
                    VIIRS_area_1day_score,
                    VIIRS_perc_area_1day_score,
                    VIIRS_area_5day_score,
                    VIIRS_perc_area_5day_score,
                    Sum_Score,
                ]
                for x in score_field:
                    row.append(x)
            VIIRS_w_score.writerow(row)
    csvfile.close()

    VIIRS = read_data(VIIRS_w_score_csv)
    VIIRS = VIIRS[VIIRS.VIIRSTotal_Score > 0.1]
    MOM = read_data(DFO_MOMOutput)
    MOM.drop(
        columns=[
            "area_km2",
            "ISO",
            "Admin0",
            "Admin1",
            "rfr_score",
            "cfr_score",
            "Resilience_Index",
            " NormalizedLackofResilience ",
            "Severity",
            "Alert",
        ],
        inplace=True,
    )
    Final_Output_0 = pd.merge(
        MOM.set_index("pfaf_id"), VIIRS.set_index("pfaf_id"), on="pfaf_id", how="outer"
    )
    join1 = pd.merge(
        Attributes,
        PDC_resilience[["ISO", "Resilience_Index", " NormalizedLackofResilience "]],
        on="ISO",
        how="left",
    )
    Final_Output = pd.merge(
        join1.set_index("pfaf_id"), Final_Output_0, on="pfaf_id", how="right"
    )
    Final_Output[["Hazard_Score"]] = Final_Output[["Hazard_Score"]].fillna(value=0)
    Final_Output.loc[
        (Final_Output["Hazard_Score"] < Final_Output["VIIRSTotal_Score"]), "Flag"
    ] = 3
    Final_Output["Hazard_Score"] = Final_Output[
        ["Hazard_Score", "VIIRSTotal_Score"]
    ].max(axis=1)
    # Kept: dropping these also lost the GloFAS/GFMS/rain columns of watersheds
    # a later source keeps. Culled once, at the end of final_alert_pdc.
    # Final_Output = Final_Output[Final_Output.Hazard_Score != 0]
    Final_Output.drop(
        Final_Output.index[
            (Final_Output["rfr_score"] == 0) & (Final_Output["cfr_score"] == 0)
        ],
        inplace=True,
    )
    Final_Output = Final_Output.assign(
        Scaled_Riverine_Risk=lambda x: Final_Output["rfr_score"] * 20
    )
    Final_Output = Final_Output.assign(
        Scaled_Coastal_Risk=lambda x: Final_Output["cfr_score"] * 20
    )

    ################# TypeError: loop of ufunc does not support argument 0 of type float which has no callable log method
    cols = [
        "Scaled_Riverine_Risk",
        "Scaled_Coastal_Risk",
        "Hazard_Score",
    ]
    Final_Output[cols] = Final_Output[cols].apply(pd.to_numeric, errors="coerce")

    Final_Output["Severity"] = severity_from_hazard_and_risk_scores(Final_Output)
    Final_Output["Alert"] = Final_Output.apply(mofunc_viirs, axis=1)
    Final_Output["Lead_h_VIIRS"] = observed_lead(
        Final_Output, "VIIRSTotal_Score", mofunc_viirs
    )
    Final_Output["Flag"] = Final_Output["Flag"].astype(object)
    Final_Output.loc[Final_Output["Alert"] == "Information", "Flag"] = ""
    Final_Output.loc[Final_Output["Alert"] == "Advisory", "Flag"] = ""
    Final_Output.to_csv(Final_Attributes_csv, encoding="utf-8-sig")
    join1 = pd.merge(
        Attributes,
        PDC_resilience[["ISO", "Resilience_Index", " NormalizedLackofResilience "]],
        on="ISO",
        how="left",
    )
    Attributes_Clean_VIIRS_Updated = pd.merge(
        join1.set_index("pfaf_id"),
        Final_Output[["Alert", "Flag"]],
        on="pfaf_id",
        how="right",
    )
    Attributes_Clean_VIIRS_Updated.to_csv(Attributes_Clean_csv, encoding="utf-8-sig")
    logging.info(f"generated: {Attributes_Clean_csv}")

    os.remove(VIIRS_w_score_csv)

    # remove DFO output
    os.remove(DFO_Final_Attributes_csv)
    os.remove(DFO_Attributes_Clean_csv)

    os.chdir(settings.BASE_DIR)

    return


def find_pair_HWRFoutput(adate):
    """[the MoM+DFO+VIIRS output of a cycle, the final alert of the cycle
    before it]: the alert's Status compares with the Alert the previous cycle
    published (FINAL_MOM_DIR). ["", ""] without this cycle's output,
    [output, ""] without the previous alert
    """
    # first find the hwrf hour output
    hwrfh_list = glob.glob(os.path.join(settings.HWRF_MOM_DIR, "Final*.csv"))
    # Final_Attributes_2021102800HWRF+20211028DFO+20211027VIIRSUpdated.csv
    matching = [s for s in hwrfh_list if adate + settings.HWRF_FILE_TAG + "+" in s]
    if len(matching) < 1:
        return ["", ""]

    MoM_adate = matching[0]

    # the final alert of the previous cycle
    pdate = (datetime.strptime(adate, "%Y%m%d%H") - timedelta(hours=6)).strftime(
        "%Y%m%d%H"
    )
    previous = os.path.join(
        settings.FINAL_MOM_DIR,
        f"Final_Attributes_{pdate}{settings.HWRF_FILE_TAG}+MOM+DFO+VIIRSUpdated_PDC.csv",
    )
    if not os.path.exists(previous):
        return [MoM_adate, ""]

    return [MoM_adate, previous]


def final_alert_pdc(adate):
    """generate final alert"""

    fAlert = (
        f"Final_Attributes_{adate}{settings.HWRF_FILE_TAG}+MOM+DFO+VIIRSUpdated_PDC.csv"
    )
    fAlert = os.path.join(settings.FINAL_MOM_DIR, fAlert)
    # check if final alert is already generatated
    if os.path.exists(fAlert):
        logging.info(f"mathing HWRF output already exists: {adate}")
        return

    [aAlert, pAlert] = find_pair_HWRFoutput(adate)
    if aAlert == "":
        logging.warning(f"mathing HWRF output is not found: {adate}")
        return

    mapping = {"Information": 1, "Advisory": 2, "Watch": 3, "Warning": 4}
    # the previous cycle's final alert: its Alert is Alert_0; without it every
    # watershed is New (Alert_0 = 5)
    if pAlert:
        # written in Windows-1252 (below); only its pfaf_id and Alert are
        # read, one row per watershed (a final alert repeats a watershed's
        # row, with the same Alert, once per admin area), and the join keeps
        # the watersheds of this cycle only
        pa_df = pd.read_csv(
            pAlert, encoding="Windows-1252", usecols=["pfaf_id", "Alert"]
        ).drop_duplicates("pfaf_id")
    else:
        logging.warning(f"no final alert of the previous cycle for {adate}, all watersheds are New")
        pa_df = pd.DataFrame(columns=["pfaf_id", "Alert"], dtype=float)
    # for PA, the only two columns needed
    pa_df = pa_df[["pfaf_id", "Alert"]]
    ca_df = read_data(aAlert)
    pa_df = pa_df.replace({"Alert": mapping})
    ca_df = ca_df.replace({"Alert": mapping})

    # rename Alert in the previous day as Alert_0
    pa_df.rename(columns={"Alert": "Alert_0"}, inplace=True)
    # join two df by pfaf_id
    joined_df = ca_df.set_index("pfaf_id").join(pa_df.set_index("pfaf_id"))

    # check the value of Alert_0
    # expected value: [ 1.  2. nan  3.  4.]
    # print(joined_df["Alert_0"].unique())
    # replace nan value as 5
    joined_df["Alert_0"] = joined_df["Alert_0"].fillna(5)
    # force it int
    joined_df = joined_df.astype({"Alert_0": int})
    # expected value Alert_0: [1 2 3 4 5]
    # print(joined_df["Alert_0"].unique())
    # expected value Alert: [1 2 3 4]
    # print(joined_df["Alert"].unique())

    # a new Status column based on Alert and Alert_0
    # Alert_0 = 5: "New"
    # Alert = Alert_0: "Continued"
    # Alert > Alert_0: "Upgraded"
    # Alert < Alert_0: "Downgraded"
    conditions = [
        (joined_df["Alert_0"] == 5),
        (joined_df["Alert"] == joined_df["Alert_0"]),
        (joined_df["Alert"] > joined_df["Alert_0"]),
        (joined_df["Alert"] < joined_df["Alert_0"]),
    ]
    actions = ["New", "Continued", "Upgraded", "Downgraded"]
    joined_df["Status"] = np.select(conditions, actions, default="Other")
    # print(joined_df["Status"].unique())

    mapping = {1: "Information", 2: "Advisory", 3: "Watch", 4: "Warning"}
    joined_df = joined_df.replace({"Alert": mapping})

    # delete columns
    joined_df = joined_df.drop(
        [
            "Admin0",
            "Admin1",
            "ISO",
            "Resilience_Index",
            " NormalizedLackofResilience ",
            "Alert_0",
        ],
        axis=1,
    )

    # drop FID if it has
    if "FID" in joined_df.columns:
        print("drop FID column")
        joined_df = joined_df.drop(["FID"], axis=1)

    # load admin data
    Union_Attributes = pd.read_csv(
        os.path.join(settings.BASE_DATA_DIR, "Admin0_1_union_centroid.csv"),
        encoding="Windows-1252",
    )

    # reset index
    joined_df = joined_df.reset_index(level=0)
    PDC_Alert = pd.merge(Union_Attributes, joined_df, on="pfaf_id", how="inner")
    # print(PDC_Alert)
    # Dead code: these are numeric columns, so == "" never matches.
    PDC_Alert.drop(
        PDC_Alert.index[
            (PDC_Alert["DFOTotal_Score"] == "")
            & (PDC_Alert["MOM_Score"] == "")
            & (PDC_Alert["CentroidY"] > 50)
        ],
        inplace=True,
    )

    # drop FID if it has
    FIDcolumns = [x for x in PDC_Alert.columns if "FID" in x]
    # print(FIDcolumns)
    if len(FIDcolumns) > 0:
        print("drop FIDs column")
        PDC_Alert = PDC_Alert.drop(FIDcolumns, axis=1)

    # The chain's only cull: a watershed is published when some source scored
    # it, but it keeps every column it collected on the way here.
    PDC_Alert = PDC_Alert[PDC_Alert["Hazard_Score"].fillna(0) != 0]

    # the earliest lead time of all the sources (Lead_h_DFO / Lead_h_VIIRS 0
    # when they observe the flood; -1 and missing ignored), only where a
    # forecast source (FORECAST_LEADS) has one; -1 otherwise
    leads = PDC_Alert[[c for c in PDC_Alert.columns if c.startswith("Lead_h_")]]
    leads = leads.apply(pd.to_numeric, errors="coerce").where(lambda x: x >= 0)
    forecast = leads[[c for c in FORECAST_LEADS if c in leads]].notna().any(axis=1)
    PDC_Alert["Lead_h"] = leads.min(axis=1).where(forecast).fillna(-1).astype(int)

    PDC_Alert.to_csv(fAlert, encoding="Windows-1252")
    logging.info(f"generated final alert: {fAlert}")

    return


def hwrf_workflow(adate):
    """the hwrf workflow on a date
    to simplify the call from the other scripts
    """
    update_HWRF_MoM(adate)
    update_HWRFMoM_DFO_VIIRS(adate)
    final_alert_pdc(adate)


def batchrun_HWRF_MoM():
    """run hwrf in batch mode"""

    # collect all dates
    # first scan hwrf_summary folder
    datelist = []
    now = datetime.now(timezone.utc)
    for entry in get_latestitems(settings.HWRF_SUM_DIR):
        if "rainfall.csv" in entry:
            testdate = entry.split(".")[1].replace("rainfall", "")
            datelist.append(testdate)
            # the next cycle's MoM uses this summary (previous-cycle HWRF), so
            # it can run as soon as that cycle has started
            next_cycle = datetime.strptime(testdate, "%Y%m%d%H").replace(
                tzinfo=timezone.utc
            ) + timedelta(hours=6)
            if next_cycle <= now:
                datelist.append(next_cycle.strftime("%Y%m%d%H"))

    # scan raw data folder
    for entry in get_latestitems(settings.HWRF_PROC_DIR):
        # hwrf.2021092112rainfall.zip
        if ".zip" in entry:
            testdate = entry.split(".")[1].replace("rainfall", "")
            datelist.append(testdate)

    # also scan mom folder
    for entry in get_latestitems(settings.HWRF_MOM_DIR):
        if "Final" in entry and f"{settings.HWRF_FILE_TAG}Updated" in entry:
            testdate = entry.split("_")[2].replace(
                f"{settings.HWRF_FILE_TAG}Updated.csv", ""
            )
            datelist.append(testdate)

    # get current processing hour
    # this code is also in HWRF_cron(), no harm to keep it here
    curdatestr = get_current_processing_datehour(time_delay=settings.HWRF_TIME_DELAY)
    # check if there is the hwrf data for this hour
    if not hwrf_today(adate=curdatestr[:8], ahour=curdatestr[-2:]):
        datelist.append(curdatestr)

    list_set = set(datelist)
    unique_dates = list(list_set)
    unique_dates.sort()
    logging.info(f"HWRF MoM batch, dates found: {unique_dates}")

    for testdate in unique_dates:
        logging.info(f"HWRF MoM batch, running workflow for: {testdate}")
        hwrf_workflow(testdate)

    return


def main():
    # run batch mode
    batchrun_HWRF_MoM()


if __name__ == "__main__":
    main()
