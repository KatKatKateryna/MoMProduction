"""
Test upload: reads the first file from gfms_csv_files, adds a timestamp column
from the filename, and inserts all rows into the gfms_summary table in one request.
"""

import os
import csv
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

DOWNLOAD_DIR = "downloads/gfms_csv_files"
DB_PARAMS = {
    "host":     "aws-1-eu-central-1.pooler.supabase.com",
    "port":     5432,
    "dbname":   "postgres",
    "user":     "postgres.xoffzjdacnmwhhaaeokm",
    "password": os.getenv("DB_PASSWORD"),
    "sslmode":         "require",
    "connect_timeout": 10,
}
TABLE = "gfms_summary"

INSERT_SQL = f"""
INSERT INTO {TABLE} (pfaf_id, "GFMS_TotalArea_km", "GFMS_perc_Area", "GFMS_MeanDepth", "GFMS_MaxDepth", "GFMS_Duration", timestamp)
VALUES %s
"""


def main():
    # Pick the first (alphabetically sorted) file
    files = sorted(os.listdir(DOWNLOAD_DIR))
    filename = files[0]
    filepath = os.path.join(DOWNLOAD_DIR, filename)

    # Extract timestamp from filename: part after last '_' and before '.'
    timestamp = filename.rsplit("_", 1)[-1].split(".")[0]

    print(f"File: {filename}")
    print(f"Timestamp: {timestamp}")

    # Read rows
    rows = []
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append((
                int(row["pfaf_id"]),
                float(row["GFMS_TotalArea_km"]),
                float(row["GFMS_perc_Area"]),
                float(row["GFMS_MeanDepth"]),
                float(row["GFMS_MaxDepth"]),
                int(row["GFMS_Duration"]),
                timestamp,
            ))

    print(f"Rows to insert: {len(rows):,}")

    with psycopg2.connect(**DB_PARAMS) as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, INSERT_SQL, rows, page_size=len(rows))
        conn.commit()

    print("Done.")


if __name__ == "__main__":
    main()
