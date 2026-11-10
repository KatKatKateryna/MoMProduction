"""
GloFAS_tool.py

Everything GloFAS: download and conversion of the host's files, finding a day's
csv or the latest one before it, the scores and the lead time.
"""

import csv
import glob
import io
import logging
import os
import time
from datetime import datetime

import geopandas
import numpy as np
import pandas as pd
from dotenv import load_dotenv

import settings
from processing.utilities import lead_hours, read_data, watersheds_gdb_reader

load_dotenv()

# a GloFAS file still missing after a download is tried again this many times,
# this far apart
GLOFAS_CSV_COLUMNS = [
    "Point No",
    "Station",
    "Basin",
    "Country",
    "Lat",
    "Lon",
    "Upstream area",
    "Forecast Date",
    "max_EPS",
    "GloFAS_2yr",
    "GloFAS_5yr",
    "GloFAS_20yr",
    "Alert_level",
    "Days_until_peak",
    "pfaf_id",
]
GLOFAS_RETRIES = 3
GLOFAS_WAIT_SECONDS = 600

WATERSHEDS = None


def _glofas_station_lines(txt_file):
    """the number of lines of a GloFAS text file that are stations (18 or
    more fields ending in the alert level); 0 for a missing file"""
    if not os.path.exists(txt_file):
        return 0
    count = 0
    with open(txt_file, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.strip().split(",")
            if len(p) >= 18 and p[-1].strip().isdigit():
                count += 1
    return count


def GloFAS_download():
    """download glofas data from ftp"""
    ftpsite = {}
    ftpsite["host"] = settings.config.get("glofas", "HOST")
    ftpsite["user"] = (
        settings.config.get("glofas", "USER")
        if "???" not in settings.config.get("glofas", "USER")
        else os.getenv("AUTH_GLOFAS_USER")
    )
    ftpsite["passwd"] = (
        settings.config.get("glofas", "PASSWD")
        if "???" not in settings.config.get("glofas", "PASSWD")
        else os.getenv("AUTH_GLOFAS_PASSWD")
    )
    ftpsite["directory"] = settings.config.get("glofas", "DIRECTORY")
    from ftplib import FTP

    for attempt in range(3):
        try:
            ftp = FTP(
                host=ftpsite["host"], user=ftpsite["user"], passwd=ftpsite["passwd"]
            )
            break
        except Exception as e:
            logging.warning(f"FTP connect attempt {attempt + 1} failed: {e}")
            time.sleep(2)
    else:
        logging.warning("GloFAS FTP connection failed, using the local files")
        return []

    try:
        ftp.cwd(ftpsite["directory"])
        file_list = ftp.nlst()
    except Exception as e:
        logging.warning(f"GloFAS file listing failed, using the local files: {e}")
        return []
    job_list = []
    for txt in file_list:
        save_txt = os.path.join(settings.GLOFAS_PROC_DIR, txt)

        # add date to job list. Even if GloFAS file already exists, it's not a guarantee it was processed
        if txt.startswith("threspoints"):
            adate = txt.split(".")[0].split("_")[-1]
            if adate not in job_list:
                job_list.append(adate)

        # downloaded next to it first: a file without station lines (empty,
        # blank, an error page) never replaces a local copy that has some;
        # a file with any station line always replaces it
        tmp_txt = save_txt + ".part"
        try:
            with open(tmp_txt, "wb") as fp:
                ftp.retrbinary("RETR " + txt, fp.write)
        except Exception as e:
            logging.warning(f"GloFAS download failed: {txt}: {e}")
            if os.path.exists(tmp_txt):
                os.remove(tmp_txt)
            continue
        if _glofas_station_lines(tmp_txt) == 0 and _glofas_station_lines(save_txt) > 0:
            logging.warning(
                f"GloFAS host sent {txt} without stations, keeping the local copy"
            )
            os.remove(tmp_txt)
        else:
            os.replace(tmp_txt, save_txt)

    try:
        ftp.quit()
    except Exception as e:
        logging.warning(f"GloFAS FTP quit failed: {e}")
    # a date with at least one of its files here counts as complete
    return [
        d
        for d in job_list
        if os.path.exists(os.path.join(settings.GLOFAS_PROC_DIR, f"threspoints_{d}.txt"))
        or os.path.exists(os.path.join(settings.GLOFAS_PROC_DIR, f"threspointsDyn_{d}.txt"))
    ]


# station text fields, in file order before the country: fixed and dynamic
# files order them differently
GLOFAS_TEXT_FIELDS = {
    False: ["Basin", "Location", "Station"],
    True: ["Station", "Basin", "Location"],
}


def _is_number(value):
    try:
        float(value)
        return True
    except ValueError:
        return False


def GloFAS_read_points(txt_file, dynamic=False):
    """GloFAS stations of a threspoints (dynamic=False) or threspointsDyn
    (dynamic=True) text file

    The files are unquoted csv whose station and country names can hold
    commas ("Moldova, Republic of", "Al Furat, Euphrates, Firat"), and the
    position of lon/lat among the "-" placeholders varies from line to line,
    so a line is read from its end: Alert_level, 20/5/2 yr EPS,
    Days_until_peak, one unused number, then lon and lat as the two numbers
    among the three fields before it, then upstream area, country code and
    continent (a few dynamic lines have the country code one field later,
    without the upstream area). The names are what lies between the station
    ID and the continent. An empty or missing file has no stations."""
    columns = (
        ["Point No", "ID"]
        + GLOFAS_TEXT_FIELDS[dynamic]
        + ["Country", "Continent", "Country_code", "Upstream area", "Lon", "Lat"]
        + ["Days_until_peak", "GloFAS_2yr", "GloFAS_5yr", "GloFAS_20yr", "Alert_level"]
    )
    rows = []
    if os.path.exists(txt_file):
        with open(txt_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                raw = line.rstrip("\r\n").split(",")
                p = [x.strip() for x in raw]
                if len(p) < 18:
                    if line.strip():
                        logging.warning(
                            f"GloFAS line skipped in {txt_file}: {line.strip()}"
                        )
                    continue
                lon, lat = [x for x in p[-9:-6] if _is_number(x)][:2]
                if p[-10] in ("-", "NA") or _is_number(p[-10]):
                    country_code, upstream = p[-11], p[-10]
                else:
                    country_code, upstream = p[-10], "-"
                # a comma inside a name: the piece after it starts with a space
                names = []
                for piece, raw_piece in zip(p[2:-12], raw[2:-12]):
                    if names and raw_piece.startswith(" "):
                        names[-1] = names[-1] + ", " + piece
                    else:
                        names.append(piece)
                country = names[-1]
                text = names[:-1]
                if len(text) > 3:
                    text = [text[0], ", ".join(text[1:-1]), text[-1]]
                text = text + ["-"] * (3 - len(text))
                rows.append(
                    [p[0], p[1]]
                    + text
                    + [country, p[-12], country_code, upstream, lon, lat]
                    + p[-5:]
                )
    df = pd.DataFrame(rows, columns=columns)
    numeric = [
        "Point No",
        "Lon",
        "Lat",
        "Days_until_peak",
        "GloFAS_2yr",
        "GloFAS_5yr",
        "GloFAS_20yr",
        "Alert_level",
    ]
    df[numeric] = df[numeric].apply(pd.to_numeric)
    # "NA" and "-" when not known
    df["Upstream area"] = pd.to_numeric(df["Upstream area"], errors="coerce")
    df = df.astype({"Point No": int, "Days_until_peak": int, "Alert_level": int})
    return df


def GloFAS_stations(fixed_sites, dyn_sites, data_date):
    """the fixed and dynamic GloFAS stations of data_date with the pfaf_id of
    the watershed they are in"""
    global WATERSHEDS
    if WATERSHEDS is None:
        WATERSHEDS = watersheds_gdb_reader()

    fixed_data = GloFAS_read_points(fixed_sites, dynamic=False)
    dyn_data = GloFAS_read_points(dyn_sites, dynamic=True)
    logging.info(
        f"GloFAS {data_date}: {len(fixed_data)} fixed, {len(dyn_data)} dynamic stations"
    )
    total_data = pd.concat([fixed_data, dyn_data], ignore_index=True)

    # create a geopanda dataset
    gdf = geopandas.GeoDataFrame(
        total_data,
        geometry=geopandas.points_from_xy(total_data.Lon, total_data.Lat),
    )
    gdf.crs = "EPSG:4326"
    # generate sindex
    gdf.sindex

    # sjoin
    gdf_watersheds = geopandas.sjoin(gdf, WATERSHEDS, predicate="within")
    gdf_watersheds.rename(columns={"index_right": "pfaf_id"}, inplace=True)

    forcast_time = datetime.strptime(data_date[:8], "%Y%m%d")
    # add column "Forecast Date"
    gdf_watersheds["Forecast Date"] = forcast_time.isoformat()

    # convert "GloFAS_2yr","GloFAS_5yr","GloFAS_20y" to 0~100
    if gdf_watersheds["GloFAS_2yr"].max() <= 1.0:
        gdf_watersheds["GloFAS_2yr"] = gdf_watersheds["GloFAS_2yr"] * 100
        gdf_watersheds["GloFAS_5yr"] = gdf_watersheds["GloFAS_5yr"] * 100
        gdf_watersheds["GloFAS_20yr"] = gdf_watersheds["GloFAS_20yr"] * 100
    gdf_watersheds = gdf_watersheds.astype(
        {"GloFAS_2yr": int, "GloFAS_5yr": int, "GloFAS_20yr": int}
    )

    # fill max_EPS
    gdf_watersheds["max_EPS"] = gdf_watersheds.apply(
        lambda row: str(row["GloFAS_2yr"])
        + "/"
        + str(row["GloFAS_5yr"])
        + "/"
        + str(row["GloFAS_20yr"]),
        axis=1,
    )
    return gdf_watersheds


def GloFAS_write(data_date):
    """the GloFAS csv and geojson of data_date from its downloaded fixed and
    dynamic station files, written over any existing ones"""
    out_csv = os.path.join(settings.GLOFAS_DIR, "threspoints_" + data_date + ".csv")
    out_geojson = os.path.join(
        settings.GLOFAS_DIR, "threspoints_" + data_date + ".geojson"
    )
    logging.info(f"processing GLoFAS: {data_date}")
    fixed_sites = os.path.join(
        settings.GLOFAS_PROC_DIR, "threspoints_" + data_date + ".txt"
    )
    dyn_sites = os.path.join(
        settings.GLOFAS_PROC_DIR, "threspointsDyn_" + data_date + ".txt"
    )
    gdf_watersheds = GloFAS_stations(fixed_sites, dyn_sites, data_date)

    # write out csv file
    out_columns = [
        "Point No",
        "Station",
        "Basin",
        "Country",
        "Lat",
        "Lon",
        "Upstream area",
        "Forecast Date",
        "max_EPS",
        "GloFAS_2yr",
        "GloFAS_5yr",
        "GloFAS_20yr",
        "Alert_level",
        "Days_until_peak",
        "pfaf_id",
    ]
    gdf_watersheds.to_csv(
        out_csv, index=False, columns=out_columns, float_format="%.3f"
    )
    logging.info(f"generated: {out_csv}")

    # write to excel
    # out_excel = glofasdata + "threspoints_" + data_date + ".xlsx"
    # gdf_watersheds.to_excel(out_excel,index=False,columns=out_columns,sheet_name='Sheet_name_1')

    # to geojson
    gdf_watersheds.to_file(out_geojson, driver="GeoJSON")
    logging.info(f"generated: {out_geojson}")


def GloFAS_process():
    """process glofas data"""

    processing_dates = GloFAS_download()

    if len(processing_dates) == 0:
        logging.info("no new glofas file to process!")
        return []

    for data_date in processing_dates:

        # final file names
        out_csv = os.path.join(settings.GLOFAS_DIR, "threspoints_" + data_date + ".csv")
        out_geojson = os.path.join(
            settings.GLOFAS_DIR, "threspoints_" + data_date + ".geojson"
        )
        if os.path.exists(out_csv) and os.path.exists(out_geojson):
            continue

        GloFAS_write(data_date)

    # return a list date to be processed with GFMS
    return processing_dates


def GloFAS_process_until(found):
    """GloFAS_process, retried while found() says the wanted file is missing"""
    for retry in range(1, GLOFAS_RETRIES + 1):
        GloFAS_process()
        if found():
            return
        logging.info(
            f"GloFAS file not there yet, retry {retry} of {GLOFAS_RETRIES} "
            f"in {GLOFAS_WAIT_SECONDS}s"
        )
        time.sleep(GLOFAS_WAIT_SECONDS)
    GloFAS_process()


def glofas_summary(adate):
    """the GloFAS csv of the day of adate (YYYYMMDD[HH])"""
    return os.path.join(settings.GLOFAS_DIR, f"threspoints_{adate[:8]}00.csv")


def find_latest_glofas(apath, adate, atype):
    """return the latest GloFAS file dated BEFORE adate (YYYYMMDD[HH])"""
    target = f"threspoints_{adate[:8]}00.{atype}"
    candidates = [
        os.path.basename(f)
        for f in glob.glob(os.path.join(apath, f"threspoints_*.{atype}"))
        if os.path.basename(f) < target
    ]
    return max(candidates) if candidates else ""


def glofas_scores(glofas_csv, weightage, proc_dir, adate):
    """score the GloFAS stations of glofas_csv with weightage and average the
    Hazard score of each pfaf_id; the csv files are written to proc_dir:
    returns (scores, averaged scores), for the caller to remove"""
    add_field_GloFas = [
        "Alert_Score",
        "PeakArrivalScore",
        "TwoYScore",
        "FiveYScore",
        "TwtyYScore",
        "Sum_Score",
    ]

    # no glofas_csv: no stations, no watershed flooded
    with (
        open(glofas_csv, "r", encoding="UTF-8")
        if glofas_csv
        else io.StringIO(",".join(GLOFAS_CSV_COLUMNS) + "\n")
    ) as GloFas_file:
        GloFas_reader = csv.reader(GloFas_file)
        GloFas_w_score_csv = os.path.join(
            proc_dir, "GloFas_w_score_{}.csv".format(adate)
        )
        GloFas_Error_csv = os.path.join(
            proc_dir, "GloFas_Error_{}.csv".format(adate)
        )
        csvfile = open(GloFas_w_score_csv, "w", newline="\n", encoding="utf-8")
        txtfile = open(GloFas_Error_csv, "w", newline="\n")
        GloFas_w_score = csv.writer(csvfile)
        errorfile = csv.writer(txtfile)
        error_flag = False
        row_count = 1
        for row in GloFas_reader:
            # GloFAS writes -9999 for the 2/5/20 yr EPS when no exceedance is forecast
            if row_count > 1:
                row[9:12] = ["0" if float(x) < 0 else x for x in row[9:12]]
            if row_count == 1:
                for x in add_field_GloFas:
                    row.append(x)
                write = [
                    row[14],
                    row[12],
                    row[13],
                    row[9],
                    row[10],
                    row[11],
                    row[15],
                    row[16],
                    row[17],
                    row[18],
                    row[19],
                    row[20],
                ]
                GloFas_w_score.writerow(write)
                errorfile.writerow([row[0], row[1], row[14], "Error"])
                row_count = row_count + 1
            elif float(row[12]) > 3 or float(row[12]) < 0:
                error = "Alert less than 0 or greater than 3 is encountered"
                errorfile.writerow([row[0], row[1], row[14], error])
                error_flag = True
            elif float(row[9]) > 100:
                error = "2 yr EPS greater than 100 is encountered"
                errorfile.writerow([row[0], row[1], row[14], error])
                error_flag = True
            elif float(row[10]) > 100:
                error = "5 yr EPS greater than 100 is encountered"
                errorfile.writerow([row[0], row[1], row[14], error])
                error_flag = True
            elif float(row[11]) > 100:
                error = "20 yr EPS greater than 100 is encountered"
                errorfile.writerow([row[0], row[1], row[14], error])
                error_flag = True
            elif float(row[13]) > 30:
                error = "Peak arrival days greater than 30 is encountered"
                errorfile.writerow([row[0], row[1], row[14], error])
                error_flag = True
            else:
                Alert_Score = str(
                    round(float(row[12]) * float(weightage.Alert_score[0]))
                )
                TwoYScore = str(float(row[9]) / float(weightage.EPS_Twoyear_wt[0]))
                FiveYScore = str(float(row[10]) / float(weightage.EPS_Fiveyear_wt[0]))
                TwtyYScore = str(float(row[11]) / float(weightage.EPS_Twtyyear_wt[0]))
                if (
                    int(row[9]) == 0
                    and int(row[10]) == 0
                    and int(row[11]) == 0
                    and int(row[12]) == 0
                ):
                    PeakArrival_Score = str(0)
                elif int(row[13]) == 10 or int(row[13]) > 10:
                    PeakArrival_Score = str(1)
                elif int(row[13]) == 9:
                    PeakArrival_Score = str(2)
                elif int(row[13]) == 8:
                    PeakArrival_Score = str(3)
                elif int(row[13]) == 7:
                    PeakArrival_Score = str(4)
                elif int(row[13]) == 6:
                    PeakArrival_Score = str(5)
                elif int(row[13]) == 5:
                    PeakArrival_Score = str(6)
                elif int(row[13]) == 4:
                    PeakArrival_Score = str(7)
                elif int(row[13]) == 3:
                    PeakArrival_Score = str(8)
                elif int(row[13]) == 2:
                    PeakArrival_Score = str(9)
                elif int(row[13]) == 1:
                    PeakArrival_Score = str(10)
                Sum_Score = str(
                    float(Alert_Score)
                    + float(PeakArrival_Score)
                    + float(TwoYScore)
                    + float(FiveYScore)
                    + float(TwtyYScore)
                )
                score_field = [
                    Alert_Score,
                    PeakArrival_Score,
                    TwoYScore,
                    FiveYScore,
                    TwtyYScore,
                    Sum_Score,
                ]
                for x in score_field:
                    row.append(x)
                write = [
                    row[14],
                    row[12],
                    row[13],
                    row[9],
                    row[10],
                    row[11],
                    row[15],
                    row[16],
                    row[17],
                    row[18],
                    row[19],
                    row[20],
                ]
                GloFas_w_score.writerow(write)
    csvfile.close()
    txtfile.close()
    if not error_flag:
        os.remove(GloFas_Error_csv)

    # Taking average of Hazard score from glofas for same pfaf_id
    GloFas = read_data(GloFas_w_score_csv)
    GloFas.sort_values(by="pfaf_id", ascending=True, inplace=True)
    GloFas_w_score_sort_csv = os.path.join(
        proc_dir, "GloFas_w_score_sort_{}.csv".format(adate)
    )
    GloFas.set_index("pfaf_id").to_csv(GloFas_w_score_sort_csv, encoding="utf-8")

    with open(GloFas_w_score_sort_csv, "r") as GloFas_file:
        GloFas_reader = csv.reader(GloFas_file)
        GloFas_w_Avgscore_csv = os.path.join(
            proc_dir, "GloFas_w_Avgscore_{}.csv".format(adate)
        )
        csvfile = open(GloFas_w_Avgscore_csv, "w", newline="\n", encoding="utf-8")
        GloFas_w_score = csv.writer(csvfile)
        Haz_Score = 0
        pfaf_id = -1
        similarity = 0
        i = 1
        To_be_written = "False"
        write = []
        for row in GloFas_reader:
            if i == 1:
                i = i + 1
                GloFas_w_score.writerow(row)
            else:
                if pfaf_id == -1 or pfaf_id == row[0]:
                    pfaf_id = row[0]
                    Haz_Score = Haz_Score + float(row[11])
                    similarity = similarity + 1
                    last_row = row
                    To_be_written = "True"
                else:
                    last_row[11] = str(Haz_Score / similarity)
                    GloFas_w_score.writerow(last_row)
                    last_row = row
                    pfaf_id = row[0]
                    Haz_Score = float(row[11])
                    similarity = 1
                    To_be_written = "True"
    if To_be_written == "True":
        last_row[11] = str(Haz_Score / similarity)
        GloFas_w_score.writerow(last_row)
    csvfile.close()
    # Glofas Done
    os.remove(GloFas_w_score_sort_csv)

    return GloFas_w_score_csv, GloFas_w_Avgscore_csv


def glofas_lead(glofas_csv, glofas_score, gfms_score, risk, target, alert_func):
    """Lead_h_GloFAS per pfaf_id (index of risk): (forecast date of the
    GloFAS file + (Days_until_peak - 1) days - target) in hours, at least 0,
    where the GloFAS hazard as the MoM adds it (GloFAS + GFMS score, twice
    GloFAS without GFMS) gives an alert above Information (alert_func); -1
    otherwise or without GloFAS data. Days_until_peak: the earliest peak of
    the watershed's stations with an alert (Alert_level > 0), else of its
    stations with any exceedance probability.

    glofas_score, gfms_score: the Sum_Scores per pfaf_id before the doubling;
    risk: Scaled_Riverine_Risk, Scaled_Coastal_Risk per pfaf_id."""
    stations = read_data(glofas_csv)
    stamp = datetime.strptime(
        os.path.basename(glofas_csv)[len("threspoints_") : len("threspoints_") + 8],
        "%Y%m%d",
    )
    eps = stations[["GloFAS_2yr", "GloFAS_5yr", "GloFAS_20yr"]].clip(lower=0).sum(axis=1)
    alerted = stations[stations["Alert_level"] > 0].groupby("pfaf_id")["Days_until_peak"].min()
    signal = stations[eps > 0].groupby("pfaf_id")["Days_until_peak"].min()
    days = alerted.combine_first(signal)

    df = pd.DataFrame({"pfaf_id": days.index, "days": days.to_numpy()})
    glofas = df["pfaf_id"].map(glofas_score).fillna(0).to_numpy()
    gfms = df["pfaf_id"].map(gfms_score).fillna(0).to_numpy()
    df["Hazard_Score"] = np.where(
        glofas > 0, np.where(gfms > 0, glofas + gfms, 2 * glofas), 0
    )
    offset = (stamp - datetime.strptime(target, "%Y%m%d%H")).total_seconds() / 3600
    df["lead_h"] = (offset + 24 * (df["days"] - 1)).clip(lower=0).astype(int)
    return lead_hours(df, risk, alert_func)
