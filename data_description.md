# Final_Alert field reference

Covers the columns produced in the `Final_Attributes_*HWRF+MOM+DFO+VIIRSUpdated_PDC.csv` product (the `Final_Alert` output of `hwrf_workflow()` in [HWRF_MoM.py](HWRF_MoM.py)). Grouped by pipeline stage, in the order each stage contributes to the file.

---

# Watershed identity (static reference data)

All fields in this group come from static lookup tables (`data/Attributes.csv`, `data/Admin0_1_union_centroid.csv`) that the pipeline only ever reads, never regenerates — **constant per watershed**, unaffected by any run.

## pfaf_id
The Pfafstetter watershed code — the primary key every data source is aggregated to. Comes from the watershed shapefile.

## name, name_1
Watershed / sub-watershed name from `data/Admin0_1_union_centroid.csv` (joined in at the final PDC step, [HWRF_MoM.py:1276-1283](HWRF_MoM.py#L1276-L1283)).

## CentroidX, CentroidY
Longitude/latitude (decimal degrees, WGS84) of the watershed's centroid — used for mapping and, incidentally, to filter out high-latitude artifacts in the final PDC merge ([HWRF_MoM.py:1289](HWRF_MoM.py#L1289)).

## Admin1_count, Admin1_names
How many admin-1 (state/province)-level regions the watershed spans, and their names — from the same union/centroid reference table.

## area_km2
The watershed's surface area (Pfafstetter unit), in square kilometers. Comes straight from the watershed shapefile / `data/Attributes.csv` — used as the denominator when converting flood extent to `perc_Area` for every source.

## rfr_score / Scaled_Riverine_Risk
`rfr_score` is the underlying riverine flood risk score (0–5 scale, from `data/Attributes.csv`, WRI Aqueduct-style risk score). `Scaled_Riverine_Risk = rfr_score × 20`, a 0–100 unitless index ([GFMS_MoM.py:381-382](GFMS_MoM.py#L381-L382)), directly comparable to `Hazard_Score`.
**Cap:** 0–5 holds for every row currently in `data/Attributes.csv` (verified), so `Scaled_Riverine_Risk` tops out at 100 in practice — but nothing in the code enforces or clips that bound; it's purely a property of the source dataset. A future update to `Attributes.csv` with a value above 5 would flow straight through unclipped.

## cfr_score / Scaled_Coastal_Risk
The coastal counterpart to `rfr_score` — coastal flood risk score, 0–5 scale, from `data/Attributes.csv` (mostly 0 for inland watersheds). `Scaled_Coastal_Risk = cfr_score × 20`, a 0–100 unitless index ([GFMS_MoM.py:384-385](GFMS_MoM.py#L384-L385)), used alongside `Scaled_Riverine_Risk` in the Severity calculation.
**Cap:** same as `rfr_score` above — 0–5 holds in the current data but is not enforced by code.

---

# GloFAS forecast inputs and scores

GloFAS (ECMWF's Global Flood Awareness System) supplies a river-discharge forecast per gauge station, matched to a watershed. All values below are **recalculated each run** — pulled fresh from the latest `threspoints_*.csv` forecast file and re-scored every time `flood_severity()` / `update_HWRF_MoM()` runs.

## Alert_level
GloFAS's own alert level for the station, an integer **0–3** (0 = no alert, up to 3 = highest; values outside this range are logged as a data error and the row is dropped, [GFMS_MoM.py:198-201](GFMS_MoM.py#L198-L201)).
**Cap:** enforced upstream of scoring — a row with `Alert_level < 0` or `> 3` is written to a `GloFas_Error_*.csv` and excluded from that run's scoring entirely, rather than clipped to the valid range.

## Days_until_peak
Forecast lead time, in **days**, until the predicted peak discharge arrives at the station (capped at 30; larger values are flagged as an error).
**Cap:** same pattern — `> 30` is treated as a data error and the row is dropped from scoring, not clamped to 30.

## GloFAS_2yr, GloFAS_5yr, GloFAS_20yr
Ensemble Prediction System (EPS) probability, as a **percentage (0–100)**, that forecast discharge will exceed the 2-year / 5-year / 20-year return-period threshold at that station — i.e. how likely a flood of that rarity is to occur.
**Cap:** same pattern again — any value `> 100` is a data error, and the whole row (not just that field) is excluded from scoring for that run rather than clipped to 100.

## Alert_Score, TwoYScore, FiveYScore, TwtyYScore
Unitless sub-scores (0–10 each) derived from the raw GloFAS values above via `data/GFMS_Weightage.csv` weights: `Alert_Score = round(Alert_level × 3.33)`; each `*YScore = (GloFAS_Xyr %) / 10`.
**Cap:** `TwoYScore`/`FiveYScore`/`TwtyYScore` are hard-capped at 10 by the earlier error check that rejects any `GloFAS_Xyr` value over 100 ([GFMS_MoM.py:202-213](GFMS_MoM.py#L202-L213)). `Alert_Score` is bounded by `Alert_level`'s own 0–3 range × the weight in `data/GFMS_Weightage.csv` (currently 3.33, giving max 10) — change that CSV value and the cap moves with it; nothing in the code clips `Alert_Score` directly.

## PeakArrivalScore
Unitless sub-score (0–10) from a lookup table on `Days_until_peak`: sooner peak → higher score (peak tomorrow = 10, peak in ≥10 days = 1, 0 if there's no flood signal at all) ([GFMS_MoM.py:230-251](GFMS_MoM.py#L230-L251)).
**Cap:** hard 0–10 by construction — every branch of the lookup table assigns a literal value in that range ([GFMS_MoM.py:225-251](GFMS_MoM.py#L225-L251)).

## Sum_Score_x
The GloFAS composite: `Alert_Score + PeakArrivalScore + TwoYScore + FiveYScore + TwtyYScore`. Unitless, normally 0–50, but doubled when the paired GFMS score (`Sum_Score_y`) is exactly 0, so a single active source isn't diluted on the combined 0–100 scale ([GFMS_MoM.py:362-367](GFMS_MoM.py#L362-L367)). Feeds directly into `MOM_Score`.
**Cap:** not clipped directly — its 0–50 (or 0–100 when doubled) range is an emergent sum of the five capped sub-scores above, not an explicit `min()`/clamp in the code.

---

# GFMS raw values and scores

GFMS (Global Flood Monitoring System, UMD) provides a daily flood-extent/depth raster. **Recalculated each run**, from the current `Flood_byStor_*` raster.

## GFMS_TotalArea_km
Total flooded area within the watershed, in **km²**, summed from raster pixels flagged as flooded ([GFMS_tool.py:510](GFMS_tool.py#L510)).

## GFMS_perc_Area
`GFMS_TotalArea_km / area_km2 × 100` — flooded fraction of the watershed, as a **percentage**.
**Cap:** not enforced anywhere — a straight ratio, so it could in principle exceed 100% if pixel-area summation (based on lat/lon grid cell size, [GFMS_tool.py:438](GFMS_tool.py#L438)) overshoots the watershed's polygon-based `area_km2` near boundaries. Same caveat applies to the DFO and VIIRS `perc_Area` fields below.

## GFMS_MeanDepth
Mean flood depth/storage value across flooded pixels in the watershed, in **meters** (raw raster values, no unit conversion — [GFMS_tool.py:516](GFMS_tool.py#L516)).

## max_flood_depth_m (→ GFMS_MaxDepth)
Maximum flood depth pixel value in the watershed, in **meters** ([GFMS_tool.py:517](GFMS_tool.py#L517)).

## GFMS_Duration
Set to `3` if `GFMS_TotalArea_km > 100`, else `0` — a coarse flag for "large, sustained flood event" rather than a true elapsed-time duration ([GFMS_tool.py:511-512](GFMS_tool.py#L511-L512)).

## GFMS_area_score, GFMS_perc_area_score, MeanD_Score, MaxD_Score, Duration_Score
Unitless sub-scores (0–10 each), each the corresponding raw value scaled by `data/GFMS_Weightage.csv` and capped at 10.
**Cap:** explicitly clipped in code — each is computed via `if raw/weight > max_pt: score = max_pt` ([GFMS_MoM.py:90-139](GFMS_MoM.py#L90-L139)), where `max_pt` is read from `data/GFMS_Weightage.csv` (currently 10 for all five). A real code-level cap, but its value is config-driven, not a literal `10` in the script.

## Sum_Score_y
The GFMS composite: sum of the five sub-scores above. Unitless, normally 0–50, doubled when `Sum_Score_x` (GloFAS) is exactly 0, same reasoning as above. Feeds into `MOM_Score`.
**Cap:** not clipped directly — same as `Sum_Score_x`, its range is an emergent sum of already-capped sub-scores.

---

# Combined baseline: MOM_Score, Hazard_Score, Flag

## MOM_Score
The "Model of Models" baseline score: `Sum_Score_x + Sum_Score_y` (GloFAS composite + GFMS composite, [HWRF_MoM.py:566-570](HWRF_MoM.py#L566-L570)). Unitless, roughly 0–100. Kept as its own column so the GFMS+GloFAS-only signal stays visible even after HWRF/DFO/VIIRS push `Hazard_Score` higher.
**Recalculated each run.** **Cap:** no explicit clamp — bounded to ~100 only because `Sum_Score_x` and `Sum_Score_y` each individually max at 50 (or one maxes at 100 while the other is exactly 0, never both inflated at once). Depends entirely on `data/GFMS_Weightage.csv` staying at its current max-point values.

## hazard_score (→ Hazard_Score)
A unitless composite score, not a physical measurement. Starts as `MOM_Score` and is then ratcheted upward — via `max(Hazard_Score, source_score)` — first against HWRF's rain-based score, then DFO's satellite score, then VIIRS's satellite score ([HWRF_MoM.py:570-583, 887-892, 1097-1100](HWRF_MoM.py#L570-L583)). The final value is whichever of the four sources currently shows the worst flood signal for that watershed. Typically 0–100+, thresholded for severity: >80 highest, 60–80, 35–60, 0–35 lowest ([GFMS_MoM.py:14-20](GFMS_MoM.py#L14-L20)).
**Recalculated each run.** **Cap:** not enforced by any `min()`/clip in code. `Hazard_Score` is `max()` of `MOM_Score`, `HWRFTot_Score`, `DFOTotal_Score`, `VIIRSTotal_Score` — and each of those four independently maxes out around 100 given the current weightage tables, so `Hazard_Score` effectively never exceeds ~100 today. (`HWRFTot_Score` itself isn't a Final_Alert column — it's `(HWRF_area_score + HWRF_percarea_score + MeanRain_Score + MaxRain_Score) × 2.5`, with each of those four sub-scores capped at 10 by `data/HWRF_Weightage.csv`, giving `40 × 2.5 = 100` max — same "sums of capped sub-scores" pattern as `DFOTotal_Score`/`VIIRSTotal_Score` below.) Edit any `*_Weightage.csv` max-point column and the corresponding ceiling moves with it. The severity rule (`> 80` → Warning) is open-ended above 80, so nothing would break if `Hazard_Score` did exceed 100.

## Flag
Marks which data source most recently raised `Hazard_Score` above the `MOM_Score` baseline: `1` = HWRF (rain forecast), `2` = DFO (satellite), `3` = VIIRS (satellite), blank = `MOM_Score` (GFMS+GloFAS) is still dominant, or the alert severity is "Information"/"Advisory" — in which case Flag is always blanked regardless of source ([HWRF_MoM.py:576-580, 887-889, 1097-1100](HWRF_MoM.py#L576-L580); blanking e.g. [DFO_MoM.py:286-287](DFO_MoM.py#L286-L287)). An audit trail of which model is currently driving the alert, not a measurement.
**Recalculated each run.**

---

# DFO raw values and scores

DFO (Dartmouth Flood Observatory, via NASA MODAPS MCDWD product) supplies 1/2/3-day flood-extent rasters, plus a "CS" (cloud/shadow-filtered) variant of the 1-day layer. **Recalculated each run**.

## 1-Day_TotalArea_km2, 2-Day_TotalArea_km2, 3-Day_TotalArea_km2
Flooded area within the watershed from the 1-day / 2-day / 3-day MODIS flood-detection composite, in **km²** ([DFO_tool.py:222](DFO_tool.py#L222)).

## 1-Day_perc_Area, 2-Day_perc_Area, 3-Day_perc_Area
Corresponding flooded fraction of the watershed, as a **percentage** (`TotalArea_km2 / area_km2 × 100`).

## 1-Day_CS_TotalArea_km2, 1-Day_CS_perc_Area
Same as the 1-Day fields, but from the "Flood 1-Day CS 250m" layer — a cloud/shadow-filtered variant of the 1-day MODIS flood detection, meant to reduce false positives from cloud shadow ([DFO_tool.py:190-196, 260-268](DFO_tool.py#L190-L196)).

## DFO_area_1day_score, DFO_area_2day_score, DFO_area_3day_score, DFO_percarea_1day_score, DFO_percarea_2day_score, DFO_percarea_3day_score
Unitless sub-scores, each raw area/percent value scaled per `data/DFO_Weightage.csv`, then multiplied by a day-recency weight (`1× / 1.5× / 2.5×` for 1/2/3-day) so a fresher flood signal counts more.
**Cap:** explicitly clipped in code — `if raw/weight > max_pt: score = max_pt × day_multiplier` ([DFO_MoM.py:104-187](DFO_MoM.py#L104-L187)). With the current `data/DFO_Weightage.csv` (`max_pt = 10` for both area and %-area), that puts a hard ceiling of `10 × 1 = 10` on the 1-day pair, `10 × 1.5 = 15` on the 2-day pair, `10 × 2.5 = 25` on the 3-day pair.

## DFOTotal_Score
Sum of the six sub-scores above. Unitless composite specific to DFO; this is what gets compared against the running `Hazard_Score` (Flag = 2 if it wins).
**Recalculated each run.** **Cap:** not clipped directly, but by design the six sub-score ceilings above sum to exactly 100 (10+10+15+15+25+25), so `DFOTotal_Score` maxes at 100 given the current weightage table — an emergent property, not an enforced clamp.

---

# VIIRS raw values and scores

VIIRS floodlight data (SSEC, 1-day and 5-day flood composites). **Recalculated each run**.

## onedayFlood_Area_km, fivedayFlood_Area_km
Flooded area within the watershed from the 1-day and 5-day VIIRS flood composites, in **km²**. The 5-day composite fills in cloud/orbit gaps using the last 5 days of clear observations, trading recency for more complete coverage.

## onedayperc_Area, fivedayperc_Area
Corresponding flooded fraction of the watershed, as a **percentage**.

## VIIRS_area_1day_score, VIIRS_area_5day_score, VIIRS_percarea_1day_score, VIIRS_percarea_5day_score
Unitless sub-scores, scaled per `data/VIIRS_Weightage.csv`, each multiplied by a day-recency weight (`1.5×` for 1-day, `3.5×` for 5-day).
**Cap:** explicitly clipped in code the same way as DFO's sub-scores ([VIIRS_MoM.py:92-147](VIIRS_MoM.py#L92-L147)). With the current `data/VIIRS_Weightage.csv` (`max_pt = 10`), that's `10 × 1.5 = 15` ceiling for each 1-day sub-score and `10 × 3.5 = 35` for each 5-day sub-score.

## VIIRSTotal_Score
Sum of the four sub-scores above. Unitless composite specific to VIIRS; compared against the running `Hazard_Score` (Flag = 3 if it wins).
**Recalculated each run.** **Cap:** not clipped directly, but by design the four ceilings above sum to exactly 100 (15+15+35+35), so `VIIRSTotal_Score` maxes at 100 given the current weightage table.

---

# Final composite outputs

## Severity
A 0–1 probability-like value from a normal-CDF comparison of `log(Hazard_Score)` against `log(100 − max(Scaled_Riverine_Risk, Scaled_Coastal_Risk))` ([GFMS_MoM.py:401-411](GFMS_MoM.py#L401-L411)). Effectively: how extreme the current hazard signal is relative to what the watershed's baseline flood-risk profile would "expect." Used alongside the raw `Hazard_Score` thresholds to assign `Alert`.
**Recalculated each run.** **Cap:** genuinely bounded to [0, 1] — it's the output of a normal distribution's CDF (`scipy.stats.norm(...).cdf(...)`), which is mathematically constrained to that range regardless of input. **Known edge case:** if `max(Scaled_Riverine_Risk, Scaled_Coastal_Risk)` reaches exactly 100 (`rfr_score` or `cfr_score` = 5), `log(100 − 100) = log(0)` is mathematically undefined (`-inf` in floating point, feeding an invalid `loc=-inf` into `scipy.stats.norm`), so `Severity` likely comes out `NaN`/undefined for that row rather than a valid probability. This isn't hypothetical — `data/Attributes.csv` currently has 2 watersheds with `rfr_score = 5` and 1 with `cfr_score = 5`, so it's worth checking whether their `Severity`/`Alert` values in the output look sane.

## Alert
Categorical severity label — `Information` / `Advisory` / `Watch` / `Warning` — assigned from `Severity` and `Hazard_Score` via the threshold rules in `mofunc_*()` (>0.8 or >80 → Warning, down to 0–0.35 or 0–35 → Information; [GFMS_MoM.py:13-21](GFMS_MoM.py#L13-L21)).
**Recalculated each run.**

## Status
Compares this run's `Alert` to the same watershed's `Alert` from the equivalent run 24 hours earlier: `New` (no prior record), `Continued` (same level), `Upgraded` (more severe), `Downgraded` (less severe) ([HWRF_MoM.py:1239-1251](HWRF_MoM.py#L1239-L1251)).
**Recalculated each run** — by definition, since it's a day-over-day comparison.
