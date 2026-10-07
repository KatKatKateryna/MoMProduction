# VIIRS floods and river floodplains

**Question:** how much does VIIRS flood water (values 141–200, the range the MoM pipeline counts as VIIRS flood) overlap with river floodplains (GFPLAIN250m)? And inside a region that floods, does the floodplain tell you where the water will be?

**Data:** 236 daily VIIRS 1-day flood composites (NOAA/CIMSS-GMU, as staged by MoM), 2026-01-07 to 2026-08-31.
- **Band 1** of the accumulation counts, per pixel, the rasters in which the value was 141–200.
- In this product, codes 101–200 encode the floodwater fraction of the pixel (code − 100 = percent), so 141–200 means **more than about 40% of the pixel is floodwater**. The MoM pipeline counts exactly this range per watershed (`VIIRS_tool.py:309`: `(data > 140) & (data < 201)`), in both the 1-day and the 5-day composites. The main sections analyse the **1-day** composite. The same analysis for the **5-day** composite is in [VIIRS 5-day composite](#viirs-5-day-composite).
- The lower codes, 130–140 (band 2), are not analysed here.

"Flooded" below means flagged in at least one raster.

## Summary

1. **VIIRS flags about 10x more land than DFO.** 9.4% of the land domain was flagged at least once in eight months, against 0.97% for DFO sudden flood.
2. **Floodplains are enriched to the same degree as for DFO sudden flood.**
   - Floodplain is 10.5% of land and holds 27% of the VIIRS-flagged pixels.
   - A floodplain pixel is 3.2x more likely to be flagged than other land.
   - Weighted by days, the floodplain holds 40% of flood-days, and its mean daily flagged share is 5.7x that of other land.
3. **Inside a region that floods, the floodplain helps, but only coarsely.**
   - Within flooded 1° regions, the floodplain is 13.5% of land and holds 33% of VIIRS flood pixels (risk ratio 3.2x).
   - Floodplain floods more than other land in 87% of those regions.
   - Still, 75% of floodplain pixels in these regions were never flagged, and 18% of flood pixels are in flooded 1° regions with no mapped floodplain.
4. **The floodplain link is weakest where VIIRS flags the most.** 45% of all VIIRS flood pixels are north of 50°N, and at 50–60°N the risk ratio is only 2.0x. At 40–50°S (Patagonia) VIIRS flagged 27% of all land at least once, with no floodplain preference (1.1x).
5. **The 5-day composite gives the same picture, slightly weaker.**
   - It flags more land: 12.4% ever flagged, against 9.4% for the 1-day composite.
   - It is slightly less floodplain-bound: risk ratio 2.9x against 3.2x, both overall and within flooded 1° regions.
   - The extra area the 5-day composite flags is less floodplain-bound than the 1-day area, which dilutes the floodplain contrast a little. The conclusions don't change.
6. **The grid makes no difference.** The DFO grid, with VIIRS resampled by nearest neighbour, gives the same figures to within rounding.

## Analysis domain, and why the earlier river-plain reports overstated the effect

A pixel is in the domain if all of the following hold:
- it is **land** (inside a MoM watershed polygon);
- it is inside the GFPLAIN250m continental footprints, which end at 64.1°N;
- it was observed by at least 80% of the DFO rasters **and** at least 80% of the VIIRS rasters. South of 50°S only 183 of the 242 DFO rasters have coverage, so the domain is effectively **50°S to 64.1°N**.

Every layer and both products use this same domain, so their numbers can be compared directly.

**Why the land mask matters.** GFPLAIN maps floodplains only on land. But the source raster still stores a value everywhere: `0` is floodplain and `255` is "everything else". That second value covers non-floodplain land, the ocean and areas outside the tiles alike. The earlier reports (`dfo_accumulation/flood_vs_river_plains_report.md`, `viirs_accumulation/viirs_vs_river_plains_report.md`) used the continental bounding boxes as their domain. Those boxes are mostly sea; the Oceania box, for example, spans 139°W–180°E and 55°S–19°N. Ocean can never be flagged as flood, yet it was counted as "off floodplain". That pushed the off-floodplain flood rate down and every enrichment figure up.

| | old box domain | land-only domain (this report) |
|---|---|---|
| DFO grid pixels | 8,382,631,118 | 2,871,713,525 (34%) |
| VIIRS grid pixels | 3,330,051,622 | 1,096,128,286 (33%) |
| floodplain share of domain | 3.6% | 10.5% |
| risk ratio, DFO sudden (class 3) | 10.5x | **3.3x** |
| risk ratio, DFO recurrent (class 2) | 22.4x | **7.2x** |
| risk ratio, VIIRS 141-200 | 10.3x | **3.2x** |

The share of flooded pixels lying on floodplain doesn't change (28.2%, 45.9% and ~27%), because the numerator is the same. Only the denominator changes. Restricting the domain to land roughly divides the enrichment by three.

**Bin bug in the earlier reports.** The "floodplain share of a 0.1° cell" tables in the old reports are shifted by one bin. `np.digitize(..., right=True)` assigned cells with a (0, 1%] floodplain share to the "0%" row, and every later row likewise holds the next bin's cells. That is why ">75-100%" always showed 0 cells while ">50-75%" held the largest count. The tables below use correct bins. For example, the old ">50-75%" row's 70,563 cells correspond to the 70,691 cells in the ">75-100%" row here; the small difference comes from the land-only domain.

## Overlap with floodplains, whole domain

"P(flood | floodplain)" is the share of floodplain pixels flagged at least once. The risk ratio is that share divided by the same share for the rest of the land. "Mean daily share flooded" averages, over pixels, the share of the observing rasters in which the pixel was flagged. The DFO-grid row uses `viirs_accumulation/viirs_on_dfo_grid.tiff`.

| layer | grid | land pixels | flooded pixels | flooded share of land | flood on floodplain | P(flood \| floodplain) | P(flood \| other land) | risk ratio | odds ratio | flood-days on floodplain | mean daily share flooded: floodplain / other land |
|---|---|---|---|---|---|---|---|---|---|---|---|
| VIIRS 141-200 | DFO | 2,871,713,525 | 270,651,332 | 9.425% | 27.3% | 24.46% | 7.655% | 3.2x | 3.9 | 40.1% | 1.011% / 0.1779% |
| VIIRS 141-200 | VIIRS | 1,096,128,286 | 103,322,537 | 9.426% | 27.4% | 24.49% | 7.653% | 3.2x | 3.9 | 40.1% | 1.012% / 0.1779% |

A floodplain pixel had a 24.5% chance of being flagged at least once, against 7.7% for other land. On an average observation day, 1.0% of floodplain was flagged, against 0.18% of other land.

## Inside flooded regions: does the floodplain tell you where the water is?

The land is cut into regions (0.1°, 0.5°, 1° cells) and only regions that flooded at least once are kept. Within the regions that also contain both floodplain and other land, the floodplain is compared with the rest. This separates "floodplains lie in wet regions" from "within a wet region, water goes to the floodplain". The second is what predicts where in a flooded region the water will be.

| layer | region size | flooded regions | flood pixels in flooded regions with no floodplain | floodplain share of land (regions with floodplain) | share of flood on floodplain | P(flood \| floodplain) | P(flood \| other land) | risk ratio | regions where floodplain floods more |
|---|---|---|---|---|---|---|---|---|---|
| VIIRS 141-200 | 0.1° | 947,808 | 50.3% | 26.0% | 46.2% | 26.99% | 11.02% | 2.4x | 66% |
| VIIRS 141-200 | 0.5° | 51,696 | 22.5% | 14.2% | 34.2% | 25.62% | 8.15% | 3.1x | 79% |
| VIIRS 141-200 | 1° | 14,303 | 18.3% | 13.5% | 33.3% | 25.29% | 7.94% | 3.2x | 87% |

- **The within-region effect is real, and consistent at coarser scales.** At 0.5–1° the risk ratio is 3.1–3.2x, essentially the global value, and floodplain floods more than other land in 79–87% of flooded regions. At 0.1° it is 2.4x, and in a third of flooded cells the floodplain is not preferred.
- **As a predictor the floodplain has moderate recall but low precision.**
  - Predicting "water will be on the floodplain" in a flooded 1° region captures 33% of VIIRS flood pixels from 13.5% of the land.
  - But 75% of the floodplain pixels in those regions stayed dry for the whole period.
- **Half of the flood pixels counted in flooded 0.1° cells sit in cells with no mapped floodplain** (18% at 1°), so there the floodplain can't help at all.

The same analysis on the DFO grid:

| layer | region size | flooded regions | flood pixels in flooded regions with no floodplain | floodplain share of land (regions with floodplain) | share of flood on floodplain | P(flood \| floodplain) | P(flood \| other land) | risk ratio | regions where floodplain floods more |
|---|---|---|---|---|---|---|---|---|---|
| VIIRS 141-200 | 0.1° | 951,903 | 50.0% | 25.8% | 46.0% | 26.91% | 10.98% | 2.5x | 66% |
| VIIRS 141-200 | 0.5° | 51,714 | 22.4% | 14.2% | 34.1% | 25.61% | 8.15% | 3.1x | 79% |
| VIIRS 141-200 | 1° | 14,313 | 18.2% | 13.5% | 33.2% | 25.27% | 7.95% | 3.2x | 87% |

## By latitude (VIIRS grid)

The northern band ends at 64.1°N, the edge of GFPLAIN's footprints, and the domain ends at 50°S (see above). GFPLAIN maps essentially no floodplain at 60–64°N, so the floodplain rates there are shown as "—".

| latitude | floodplain share of land | flooded share of land | share of all flood pixels | flood on floodplain | P(flood \| floodplain) | P(flood \| other land) | risk ratio |
|---|---|---|---|---|---|---|---|
| 60 to 64 | 0.0% | 15.76% | 12.2% | 0.0% | — | 15.76% | — |
| 50 to 60 | 12.9% | 18.75% | 32.4% | 22.9% | 33.26% | 16.60% | 2.0x |
| 40 to 50 | 8.0% | 8.73% | 13.8% | 24.2% | 26.34% | 7.19% | 3.7x |
| 30 to 40 | 8.9% | 5.36% | 7.0% | 32.3% | 19.53% | 3.98% | 4.9x |
| 20 to 30 | 6.2% | 4.75% | 5.5% | 38.8% | 29.58% | 3.10% | 9.5x |
| 10 to 20 | 18.6% | 4.34% | 3.5% | 48.5% | 11.33% | 2.75% | 4.1x |
| 0 to 10 | 15.6% | 7.23% | 5.1% | 49.2% | 22.74% | 4.35% | 5.2x |
| -10 to 0 | 13.2% | 9.39% | 6.8% | 41.2% | 29.18% | 6.37% | 4.6x |
| -20 to -10 | 12.7% | 7.13% | 4.8% | 51.7% | 29.13% | 3.94% | 7.4x |
| -30 to -20 | 10.2% | 5.15% | 3.6% | 40.9% | 20.71% | 3.39% | 6.1x |
| -40 to -30 | 16.7% | 7.56% | 2.6% | 37.6% | 17.04% | 5.66% | 3.0x |
| -50 to -40 | 8.6% | 27.07% | 2.6% | 9.0% | 28.31% | 26.96% | 1.1x |

VIIRS flags 16–19% of all land north of 50°N at least once, and 27% at 40–50°S. That is far more than at any other latitude, and in those bands the floodplain barely matters (1.1–2.0x). This pattern is consistent with seasonal snow and ice melt, lakes and wetlands, or misclassification being counted as floodwater there. That is a hypothesis: these accumulations can't tell the causes apart. Between 40°S and 40°N, 32–52% of VIIRS flood pixels are on floodplain, with risk ratios of 3.0–9.5x.

## Dose response: floodplain share of a 0.1° cell against flooding

Only cells whose domain covers at least half of the cell are included.

| floodplain share of 0.1° cell | cells | VIIRS 141-200: flooded share of land / cells with any flood |
|---|---|---|
| 0% | 839,293 | 6.85% / 67% |
| >0-1% | 32,030 | 7.37% / 80% |
| >1-5% | 66,186 | 7.74% / 83% |
| >5-10% | 54,914 | 8.96% / 86% |
| >10-25% | 86,103 | 11.97% / 89% |
| >25-50% | 62,925 | 16.94% / 90% |
| >50-75% | 34,563 | 21.35% / 90% |
| >75-100% | 70,723 | 24.79% / 87% |

The flagged share of a cell's land rises steadily, from 7.4% in cells with 0–1% floodplain to 24.8% in cells that are more than 75% floodplain (3.4x). Most cells flag something at some point: 67% even of cells with no floodplain, and about 90% of cells that are more than a quarter floodplain.

## VIIRS 5-day composite

**What it is.** Each daily 5-day composite fills cloud and orbit gaps with the most recent of the last 5 days of clear observations (see `data_description.md`). The accumulation `viirs5day_merged/viirs5day_janaug.tiff` counts, per pixel, the composites in which the value was 141–200 (the same range the MoM pipeline counts in the 5-day composites). It covers 236 composites from 2026-01-07 to 2026-08-31, without 2026-05-05.

Because consecutive composites share up to 4 days of observations, one flood day can be counted in up to 5 files. 5-day counts and "mean daily share" therefore measure how often a pixel *appears* flooded in the composites, not distinct flood days. They are larger than the 1-day values by construction.

The domain is identical to the 1-day run (every composite covers the full grid).

### Overlap with floodplains, whole domain

| layer | grid | land pixels | flooded pixels | flooded share of land | flood on floodplain | P(flood \| floodplain) | P(flood \| other land) | risk ratio | odds ratio | flood-days on floodplain | mean daily share flooded: floodplain / other land |
|---|---|---|---|---|---|---|---|---|---|---|---|
| VIIRS 5-day 141-200 | DFO | 2,871,713,525 | 354,721,788 | 12.352% | 25.7% | 30.18% | 10.253% | 2.9x | 3.8 | 37.2% | 3.939% / 0.7817% |
| VIIRS 5-day 141-200 | VIIRS | 1,096,128,286 | 135,411,055 | 12.354% | 25.8% | 30.21% | 10.251% | 2.9x | 3.8 | 37.3% | 3.945% / 0.7816% |

### Inside flooded regions (VIIRS grid)

| layer | region size | flooded regions | flood pixels in flooded regions with no floodplain | floodplain share of land (regions with floodplain) | share of flood on floodplain | P(flood \| floodplain) | P(flood \| other land) | risk ratio | regions where floodplain floods more |
|---|---|---|---|---|---|---|---|---|---|
| VIIRS 5-day 141-200 | 0.1° | 1,034,012 | 51.7% | 25.5% | 44.5% | 32.45% | 13.81% | 2.3x | 67% |
| VIIRS 5-day 141-200 | 0.5° | 52,093 | 22.6% | 14.2% | 32.2% | 31.46% | 10.96% | 2.9x | 81% |
| VIIRS 5-day 141-200 | 1° | 14,377 | 18.1% | 13.5% | 31.2% | 31.08% | 10.71% | 2.9x | 87% |

### By latitude (VIIRS grid)

| latitude | floodplain share of land | flooded share of land | share of all flood pixels | flood on floodplain | P(flood \| floodplain) | P(flood \| other land) | risk ratio |
|---|---|---|---|---|---|---|---|
| 60 to 64 | 0.0% | 19.46% | 11.5% | 0.0% | — | 19.46% | — |
| 50 to 60 | 12.9% | 23.76% | 31.3% | 22.8% | 41.89% | 21.07% | 2.0x |
| 40 to 50 | 8.0% | 11.53% | 14.0% | 22.3% | 32.01% | 9.74% | 3.3x |
| 30 to 40 | 8.9% | 8.40% | 8.4% | 26.0% | 24.66% | 6.82% | 3.6x |
| 20 to 30 | 6.2% | 6.64% | 5.9% | 34.4% | 36.58% | 4.65% | 7.9x |
| 10 to 20 | 18.6% | 5.89% | 3.6% | 45.7% | 14.48% | 3.93% | 3.7x |
| 0 to 10 | 15.6% | 9.80% | 5.2% | 46.1% | 28.87% | 6.26% | 4.6x |
| -10 to 0 | 13.2% | 12.33% | 6.8% | 38.4% | 35.78% | 8.76% | 4.1x |
| -20 to -10 | 12.7% | 9.12% | 4.7% | 47.5% | 34.21% | 5.48% | 6.2x |
| -30 to -20 | 10.2% | 6.66% | 3.6% | 36.8% | 24.15% | 4.68% | 5.2x |
| -40 to -30 | 16.7% | 10.17% | 2.7% | 32.9% | 20.05% | 8.19% | 2.4x |
| -50 to -40 | 8.6% | 31.73% | 2.3% | 8.1% | 30.12% | 31.88% | 0.9x |

### Dose response: floodplain share of a 0.1° cell (VIIRS grid)

| floodplain share of 0.1° cell | cells | VIIRS 5-day 141-200: flooded share of land / cells with any flood |
|---|---|---|
| 0% | 839,293 | 9.26% / 76% |
| >0-1% | 32,030 | 10.04% / 88% |
| >1-5% | 66,186 | 10.56% / 89% |
| >5-10% | 54,914 | 11.89% / 92% |
| >10-25% | 86,103 | 15.46% / 93% |
| >25-50% | 62,925 | 21.44% / 92% |
| >50-75% | 34,563 | 26.56% / 92% |
| >75-100% | 70,723 | 30.49% / 89% |

### 1-day against 5-day

| | VIIRS 1-day | VIIRS 5-day |
|---|---|---|
| land ever flagged | 9.43% | 12.35% |
| mean daily flagged share of land | 0.266% | 1.115% (4.2x, partly double counting across overlapping composites) |
| flood on floodplain (floodplain = 10.5% of land) | 27.4% | 25.8% |
| risk ratio, all land | 3.2x | 2.9x |
| flood-days on floodplain | 40.1% | 37.3% |
| risk ratio inside flooded 1° regions | 3.2x | 2.9x |
| flooded 1° regions where floodplain floods more | 87% | 87% |
| floodplain pixels in flooded 1° regions never flagged | 75% | 69% |
| flood pixels in flooded 1° regions with no floodplain | 18% | 18% |
| share of flood pixels north of 50°N | 45% | 43% |
| land ever flagged at 40–50°S | 27% (1.1x) | 32% (0.9x) |

- The 5-day composite adds about a third more ever-flagged land. The added area is slightly less floodplain-bound, so all floodplain contrasts weaken a little.
- The spatial pattern is the same: the weakest floodplain link and most flagging at high latitudes and in Patagonia, and the strongest links between 30°S and 30°N (risk ratios 3.7–7.9x).
- Whether the extra area is real water that clouds hid in the 1-day product, or water carried forward after it receded, can't be told from the accumulations.

## Caveats

- **Totals over the whole period.** The accumulation holds totals for January–August only, so it doesn't say *when* a pixel flooded, or whether the floodplain predicts water on a given day.
- **Lake and permanent water.** The land domain may include large inland lakes, because they fall inside watershed polygons. Whether the product reliably separates normal open water from floodwater at lake edges wasn't checked here.
- **Floodplain is binary.** GFPLAIN gives only a 0/1 mask, with no return period or depth.
- **Statistics.** Pixel counts are in degree space, not equal-area. Within-band shares and ratios are unaffected; band weights in global totals are slightly biased toward high latitudes.

## How to reproduce

Every VIIRS 1-day number in this report comes from `report/data/flood_overlap_metrics.json`, and every VIIRS 5-day number from `report/data/flood_overlap_metrics_viirs5day.json`. All scripts are in `validation_scripts/`; run them from that directory with the `myenv` conda environment (`/root/miniconda3/envs/myenv/bin/python`).

1. **Build the extra inputs** with [`build_report_inputs.sh`](../build_report_inputs.sh). It's idempotent and skips files that already exist.
   - `dfo_accumulation/dfo_on_viirs_grid.tiff`: DFO counts resampled onto the VIIRS grid (`gdalwarp -r near`).
   - `viirs5day_merged/viirs5day_janaug_on_dfo_grid.tiff`: VIIRS 5-day counts resampled onto the DFO grid (`gdalwarp -r near`).
   - `dfo_accumulation/dfo_land_mask.tiff` and `viirs_accumulation/viirs_land_mask.tiff`: land masks on the DFO and VIIRS grids, rasterised from `../data/watershed_shp`.
   - `report/data/watersheds_0p1deg.tiff`: MoM `pfaf_id` per 0.1° cell.
   ```bash
   bash build_report_inputs.sh
   ```
2. **One pass per grid** with [`flood_overlap_pass.py`](../flood_overlap_pass.py). Each pass reduces every pixel to 0.1° cell sums. Each pass takes ~10–30 min on an idle host, and up to ~1.5 h when other jobs compete for the 2 cores.
   ```bash
   # VIIRS 1-day
   python flood_overlap_pass.py --grid-name dfo_grid \
     --dfo dfo_accumulation/dfo_accumulater.tiff --viirs viirs_accumulation/viirs_on_dfo_grid.tiff \
     --dfo-meta dfo_accumulation/dfo_accumulater_metadata.json \
     --viirs-meta viirs_accumulation/viirs_accumulater_metadata.json \
     --plain dfo_accumulation/river_plains_mask.tiff \
     --plain-meta viirs_accumulation/river_plains_mask_metadata.json \
     --land dfo_accumulation/dfo_land_mask.tiff --window-rows 48 --out report/data/pass_dfo_grid.npz

   python flood_overlap_pass.py --grid-name viirs_grid \
     --dfo dfo_accumulation/dfo_on_viirs_grid.tiff --viirs viirs_accumulation/viirs_accumulater.tiff \
     --dfo-meta dfo_accumulation/dfo_accumulater_metadata.json \
     --viirs-meta viirs_accumulation/viirs_accumulater_metadata.json \
     --plain viirs_accumulation/river_plains_mask.tiff \
     --plain-meta viirs_accumulation/river_plains_mask_metadata.json \
     --land viirs_accumulation/viirs_land_mask.tiff --window-rows 40 --out report/data/pass_viirs_grid.npz

   # VIIRS 5-day
   python flood_overlap_pass.py --grid-name dfo_grid \
     --dfo dfo_accumulation/dfo_accumulater.tiff --viirs viirs5day_merged/viirs5day_janaug_on_dfo_grid.tiff \
     --dfo-meta dfo_accumulation/dfo_accumulater_metadata.json \
     --viirs-meta viirs5day_merged/viirs5day_janaug_metadata.json \
     --plain dfo_accumulation/river_plains_mask.tiff \
     --plain-meta viirs_accumulation/river_plains_mask_metadata.json \
     --land dfo_accumulation/dfo_land_mask.tiff --window-rows 48 --out report/data/pass_viirs5day_dfo_grid.npz

   python flood_overlap_pass.py --grid-name viirs_grid \
     --dfo dfo_accumulation/dfo_on_viirs_grid.tiff --viirs viirs5day_merged/viirs5day_janaug.tiff \
     --dfo-meta dfo_accumulation/dfo_accumulater_metadata.json \
     --viirs-meta viirs5day_merged/viirs5day_janaug_metadata.json \
     --plain viirs_accumulation/river_plains_mask.tiff \
     --plain-meta viirs_accumulation/river_plains_mask_metadata.json \
     --land viirs_accumulation/viirs_land_mask.tiff --window-rows 40 --out report/data/pass_viirs5day_viirs_grid.npz
   ```
   The DFO-grid passes read the GFPLAIN continental footprints from the VIIRS mask's metadata. The footprints are geographic boxes, identical in both `river_plains_mask_metadata.json` files, so it makes no difference which one is used.
3. **Compute every statistic** with [`flood_overlap_metrics.py`](../flood_overlap_metrics.py). Takes ~25 s and needs ~1.4 GB of RAM.
   ```bash
   python flood_overlap_metrics.py --dfo-grid report/data/pass_dfo_grid.npz \
     --viirs-grid report/data/pass_viirs_grid.npz --pfaf report/data/watersheds_0p1deg.tiff \
     --out-json report/data/flood_overlap_metrics.json

   python flood_overlap_metrics.py --dfo-grid report/data/pass_viirs5day_dfo_grid.npz \
     --viirs-grid report/data/pass_viirs5day_viirs_grid.npz --pfaf report/data/watersheds_0p1deg.tiff \
     --out-json report/data/flood_overlap_metrics_viirs5day.json
   ```

Inputs:
- `dfo_accumulation/dfo_accumulater.tiff`: 242 daily MCDWD "Flood 3-Day 250m" rasters, 2026-01-01 to 2026-08-31.
- `viirs_accumulation/viirs_accumulater.tiff`: 236 daily VIIRS 1-day composites, 2026-01-07 to 2026-08-31.
- `viirs5day_merged/viirs5day_janaug.tiff`: 236 daily VIIRS 5-day composites, 2026-01-07 to 2026-08-31 without 2026-05-05, merged from parts A, B, R1–R4 and E.
- The GFPLAIN250m `river_plains_mask.tiff` on each grid.
