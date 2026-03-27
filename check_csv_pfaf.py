import os
import csv
import json
from collections import Counter

ROOT = "sample_data"
OUTFILE = "csv_pfaf_unique.json"
TARGET = "pfaf_id"

def add_nested(dct, parts, value):
    key = parts[0]
    if len(parts) == 1:
        dct[key] = value
        return
    dct.setdefault(key, {})
    add_nested(dct[key], parts[1:], value)

result = {}

for dirpath, _, filenames in os.walk(ROOT):
    for fname in filenames:
        if not fname.lower().endswith(".csv"):
            continue
        full = os.path.join(dirpath, fname)
        rel = os.path.relpath(full, ROOT)
        parts = rel.split(os.sep)
        duplicates = []
        try:
            with open(full, newline='', encoding='utf-8', errors="replace") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                if header is None:
                    # empty file -> no duplicates
                    duplicates = []
                else:
                    # case-insensitive header lookup
                    lower = [h.strip().lower() for h in header]
                    if TARGET in lower:
                        idx = lower.index(TARGET)
                        values = [row[idx].strip() for row in reader if len(row) > idx]
                    else:
                        # fallback: assume first column is pfaf_id
                        values = [row[0].strip() for row in reader if row]
                        # include first row as data if no header matched
                        # (some CSVs may be single-column without header)
                        # but we've already consumed header; include it if it looks like data
                        if header and len(header) == 1 and header[0].strip() and header[0].strip().lower() != TARGET:
                            values.insert(0, header[0].strip())
                    counts = Counter(values)
                    duplicates = [val for val, cnt in counts.items() if cnt > 1]
        except Exception as e:
            # On any read/parsing error, record as an error marker
            duplicates = [f"{e}"]
        add_nested(result, parts, duplicates)

with open(OUTFILE, "w", encoding="utf-8") as outf:
    json.dump(result, outf, indent=2, ensure_ascii=False)