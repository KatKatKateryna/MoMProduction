# AI4G SAR observations, modelled flood extents and river floodplains

**Questions**
1. How do river floodplains (GFPLAIN250m) relate to the merged "flood extent, any model" layer
   (Aqueduct riverine RP1000 OR Aqueduct coastal RP1000 OR GloFAS RP500)?
2. Do the DFO and VIIRS observations (1-day and 5-day composites, full range) lie inside the
   AI4G Sentinel-1 flood layer (2014-2024)?

Everything is put on one common 30 arcsec (~1 km) grid, the grid the Aqueduct and AI4G
rasters already use. DFO (1/480°) and VIIRS (0.003372°) are reduced onto it by assigning
every native pixel to the cell containing its centre, so neither is interpolated; the layers
that only exist as PMTiles in `MoM-map/data/persistent/` are decoded back from the archives.
Companion reports: [DFO_report.md](DFO_report.md), [VIIRS_report.md](VIIRS_report.md),
[DFO_vs_VIIRS_report.md](DFO_vs_VIIRS_report.md).

## Summary

**Q1. Floodplains and the merged model extent are not the same map, and the model extent is twice as large.**
- Floodplain covers 10.9% of the land domain, the merged model extent 20.6%.
- 73% of the floodplain lies inside the model extent, but only 39% of the model extent is
  floodplain. Jaccard 0.34, risk ratio 5.2x.
- The agreement is strongly depth-dependent: of model cells less than 0.1 m deep only 11% are
  floodplain, rising monotonically to 43% at 8-16 m. Where the models put **deep** water they
  are mostly on a geomorphic floodplain; their shallow fringe is what makes them twice as big.
- In the tropics 80-88% of the floodplain is inside the model extent. North of 60°N GFPLAIN
  maps essentially **no** floodplain while the models flood 26% of the land, so there the
  floodplain carries no information at all.
- At watershed level the two rank watersheds only loosely: Spearman 0.40 between floodplain
  share and model-extent share, and only 50 of each one's 100 most-flooded watersheds are shared.

**Q2. Yes — where AI4G has a verdict, the optical observations are almost all inside it. But it has a verdict for only a small part of them.**
- Of land AI4G both tiles and assesses, **88% of the DFO sudden-flood area, 98% of DFO
  recurrent, 80% of VIIRS 1-day and 77% of VIIRS 5-day falls inside AI4G-flooded**. Restricted
  to pixels flagged on 5+ days it is 92% (DFO) and 91% (VIIRS 1-day).
- That is the answer to "are DFO/VIIRS similar to SAR": as a *subset* test, yes, strikingly so.
- The reverse fails, as expected for a 10-year union against 8-10 months: DFO sudden covers only
  6.6% of the AI4G flooded area, VIIRS 1-day 48%, VIIRS 5-day 56%. Jaccard runs 0.06 (DFO) to
  0.28 (VIIRS), so they are still not interchangeable.
- **The big caveat:** the committed `ai4g_30s.tif` has no tile over 68% of the land domain, and
  79% of what it does cover is AI4G's own exclusion mask (rough terrain, arid, urban). So
  74-89% of each product's flooded area cannot be tested at all. The build script
  (`build_ai4g_30s.py`) is resumable and marks missing tiles 255, which fits a mosaic made
  before the download finished. Completing it would make this test far stronger.

**Is the floodplain a reliable way to say "the watershed flood will be here"?**
- As a *prior*, weakly. A floodplain cell is 2.0-3.5x more likely to be flagged than other land
  (2.5x DFO sudden, 3.5x DFO recurrent, 2.2x VIIRS 1-day), and the merged model extent, despite
  being twice as large, does no better (2.4x / 3.1x / 1.9x). Neither is a flood map.
- 20-23% of the watersheds where a satellite saw flood have **no** floodplain mapped at all,
  while only 0.3-0.7% are outside the model extent: the model extent almost never says "nothing
  here", which is also why it discriminates so little.
- Within AI4G-assessed land every lift collapses to ~1.5x, because that land is already
  floodplain-like (floodplain is 33.6% of it against 10.9% globally) - AI4G's exclusion mask
  removes the rough, arid and urban land where nothing floods anyway. On genuinely flood-capable
  land, none of these maps tells you where the water goes.

## Areas, honestly: two of the PMTiles layers are inflated by their own tiling

Summing cover fractions instead of thresholding cells (`frac_area.py`) gives the true area of
each layer on the land domain:

| layer | area on land | note |
|---|---|---|
| GFPLAIN floodplain, from the 250 m raster | 13,491,065 km2 (10.93%) | reference |
| `river_plains_30s.pmtiles`, decoded | 21,250,280 km2 (17.22%) | **1.58x too large** |
| AI4G flooded, from `ai4g_30s.tif` | 4,565,619 km2 (3.70%) | reference |
| `ai4g_flood_1km.pmtiles`, decoded | 6,957,833 km2 (5.64%) | **1.52x too large** |
| `flood_extent_1km.pmtiles`, decoded | 25,686,182 km2 (20.81%) | matches its 50% threshold area, 20.6% |

Both inflated layers are the ones tiled with `resample="max"` (`build_ai4g_pmtiles.py`,
`build_river_plains_30s.py`), which is deliberate - it keeps them opaque when zoomed out - but
it means the map draws them about 1.5x larger than they are at zoom 7 and below. The depth
layers use `average` and are unbiased. Every number in this report therefore uses the **raster**
for floodplains and AI4G, and the 50% threshold for the model extent.

# Floodplains, modelled flood extents and SAR observations

All numbers are km2 of land on one common 30 arcsec (~1 km) grid, inside the
domain used by the earlier reports: MoM watershed land between 64.07N and
54.73S, where DFO, VIIRS and GFPLAIN all have coverage. A 30" cell counts as
land when at least half of it is land. Cell areas are true areas (cos lat), not cell counts.

| layer | what it is | km2 | share of land |
|---|---|---|---|
| (domain) | land in the domain | 123,425,922 | 100% |
| plain_any | GFPLAIN250m floodplain, any 250 m pixel in the cell | 16,701,625 | 13.5% |
| plain_maj | GFPLAIN250m floodplain over at least half the cell | 13,629,128 | 11.0% |
| plain_dec | floodplain from the PMTiles archive, >=50% of the cell (decode check) | 21,135,142 | 17.1% |
| extent_any | flooded in Aqueduct riverine RP1000, Aqueduct coastal RP1000 or GloFAS RP500 (>=1% of cell) | 30,960,853 | 25.1% |
| extent_maj | the same merged model extent over at least half the cell | 25,464,092 | 20.6% |
| ai4g_flood | AI4G: flooded at least once in Sentinel-1, 2014-2024 | 4,565,619 | 3.7% |
| ai4g_dec | AI4G flooded from the PMTiles archive, >=50% of the cell (decode check) | 6,870,961 | 5.6% |
| ai4g_mask | AI4G exclusion mask (rough terrain, arid, urban): not assessed | 31,786,159 | 25.8% |
| ai4g_cov | AI4G has a tile at all (0, 1 or 2, not 255) | 40,019,198 | 32.4% |
| dfo_sudden | DFO class 3 sudden flood, flagged on >=1 day (Jan-Aug 2026) | 4,318,877 | 3.5% |
| dfo_sud5 | DFO class 3 flagged on >=5 days | 741,304 | 0.6% |
| dfo_recur | DFO class 2 recurrent flood, >=1 day | 1,884,091 | 1.5% |
| viirs1d | VIIRS 1-day composite 141-200, >=1 day (full range) | 24,908,747 | 20.2% |
| viirs1d5 | VIIRS 1-day, >=5 days | 6,622,026 | 5.4% |
| viirs5d | VIIRS 5-day composite 141-200, >=1 day (full range) | 32,489,737 | 26.3% |
| viirs5d5 | VIIRS 5-day, >=5 days | 27,794,750 | 22.5% |

## Decode check

The floodplain and AI4G layers exist both as a raster and as PMTiles, so decoding
the archives can be checked against the real thing. Area ratio decoded/exact:

- floodplain: 21,135,142 vs 13,629,128 km2 (1.55x), 100.0% of the exact floodplain recovered
- AI4G flooded: 6,870,961 vs 4,565,619 km2 (1.50x), 100.0% of the exact flooded area recovered

The merged model extent only exists as PMTiles, so its area carries the same bias.

## AI4G coverage first, because it limits everything else

- AI4G has a tile over 32.4% of the land domain (40,019,198 km2).
- Of the covered land, 79.4% is the exclusion mask (rough terrain, arid or urban: Sentinel-1 was not trusted there), and
  11.4% was seen flooded at least once in 2014-2024.
- 67.6% of the land domain has no AI4G tile at all, so nothing can be said there.

## Q1. River floodplains against the merged model extent

Merged model extent = Aqueduct riverine RP1000 OR Aqueduct coastal RP1000 OR GloFAS RP500,
i.e. your "flood extent, any model" layer. Both are static maps, so this is a pure
map-to-map comparison: no time, no observation.

| floodplain / model extent | floodplain km2 | extent km2 | both | of floodplain in extent | of extent on floodplain | Jaccard | risk ratio |
|---|---|---|---|---|---|---|---|
| plain_any / extent_any | 16,701,625 | 30,960,853 | 12,995,759 | 77.8% | 42.0% | 0.375 | 4.6x |
| plain_any / extent_maj | 16,701,625 | 25,464,092 | 11,963,002 | 71.6% | 47.0% | 0.396 | 5.7x |
| plain_maj / extent_any | 13,629,128 | 30,960,853 | 10,685,039 | 78.4% | 34.5% | 0.315 | 4.2x |
| plain_maj / extent_maj | 13,629,128 | 25,464,092 | 9,963,380 | 73.1% | 39.1% | 0.342 | 5.2x |

### By latitude (floodplain over half the cell, model extent >=1%)

| latitude | land km2-ish (cells) | floodplain | model extent | both | of floodplain in extent | of extent on floodplain |
|---|---|---|---|---|---|---|
| -60..-50 | 382,307 | 3.8% | 27.9% | 2.4% | 62.6% | 8.5% |
| -50..-40 | 1,618,323 | 8.7% | 19.3% | 4.9% | 56.0% | 25.1% |
| -40..-30 | 5,824,303 | 16.9% | 27.5% | 12.2% | 72.2% | 44.2% |
| -30..-20 | 11,995,012 | 10.2% | 27.4% | 8.3% | 80.8% | 30.2% |
| -20..-10 | 11,454,213 | 12.8% | 25.6% | 10.8% | 84.3% | 42.0% |
| -10..0 | 12,269,535 | 13.4% | 27.9% | 11.8% | 88.2% | 42.3% |
| 0..10 | 11,836,197 | 15.8% | 28.5% | 13.3% | 84.5% | 46.9% |
| 10..20 | 13,669,600 | 18.8% | 27.0% | 12.9% | 68.9% | 47.9% |
| 20..30 | 19,570,417 | 6.3% | 23.7% | 5.3% | 84.0% | 22.3% |
| 30..40 | 22,083,990 | 8.9% | 24.3% | 7.0% | 78.3% | 28.8% |
| 40..50 | 26,851,366 | 8.1% | 21.8% | 6.4% | 78.7% | 29.3% |
| 50..60 | 29,626,625 | 13.0% | 22.8% | 9.3% | 72.0% | 40.9% |
| 60..70 | 14,554,385 | 0.0% | 25.9% | 0.0% | n/a | 0.0% |

### Does the model put its deep water on the floodplain?

Cells of the merged model extent, by the deepest of the three models (flood_max).

| model depth | cells | on floodplain (half-cell) | on floodplain (any) |
|---|---|---|---|
| 0-0.1 m | 3,599,908 | 11.0% | 12.4% |
| 0.1-0.5 m | 7,609,775 | 25.8% | 29.1% |
| 0.5-1 m | 7,118,746 | 32.7% | 37.8% |
| 1-2 m | 9,127,147 | 34.6% | 41.7% |
| 2-4 m | 9,373,674 | 37.9% | 47.5% |
| 4-8 m | 5,963,179 | 42.0% | 53.7% |
| 8-16 m | 1,864,930 | 43.3% | 56.1% |
| 16-655.34 m | 440,082 | 35.1% | 49.8% |

## Q2. Do the DFO and VIIRS observations fall inside AI4G?

Only cells where AI4G has a tile are counted, and the AI4G classes are kept apart:
flooded (2), exclusion mask (1, not assessed) and observed-but-never-flooded (0).

| observed layer | km2 in AI4G-covered land | in AI4G flooded | in AI4G exclusion mask | in AI4G never flooded | outside AI4G coverage |
|---|---|---|---|---|---|
| dfo_sudden | 491,546 | 61.3% | 30.1% | 8.6% | 88.6% |
| dfo_sud5 | 119,564 | 70.8% | 22.8% | 6.5% | 83.9% |
| dfo_recur | 343,323 | 71.4% | 26.8% | 1.8% | 81.8% |
| viirs1d | 5,417,974 | 40.6% | 49.5% | 10.0% | 78.2% |
| viirs1d5 | 1,738,083 | 66.4% | 26.9% | 6.7% | 73.8% |
| viirs5d | 7,376,249 | 34.5% | 55.3% | 10.1% | 77.3% |
| viirs5d5 | 6,491,618 | 37.1% | 52.8% | 10.0% | 76.6% |

The exclusion-mask column is land AI4G did not assess, so the honest containment
test is the one that drops it:

| observed layer | km2 on land AI4G assessed (tile, not masked) | also AI4G flooded | AI4G never flooded |
|---|---|---|---|
| dfo_sudden | 343,818 | 87.6% | 12.4% |
| dfo_sud5 | 92,361 | 91.6% | 8.4% |
| dfo_recur | 251,220 | 97.6% | 2.4% |
| viirs1d | 2,737,348 | 80.3% | 19.7% |
| viirs1d5 | 1,270,161 | 90.8% | 9.2% |
| viirs5d | 3,293,536 | 77.3% | 22.7% |
| viirs5d5 | 3,061,580 | 78.7% | 21.3% |

Reverse direction, inside AI4G-covered land only:

| observed layer | of AI4G flooded also flagged | Jaccard with AI4G flooded | risk ratio |
|---|---|---|---|
| dfo_sudden | 6.6% | 0.063 | 5.7x |
| dfo_sud5 | 1.9% | 0.018 | 6.3x |
| dfo_recur | 5.4% | 0.053 | 6.6x |
| viirs1d | 48.1% | 0.282 | 5.9x |
| viirs1d5 | 25.3% | 0.224 | 7.4x |
| viirs5d | 55.8% | 0.271 | 5.6x |
| viirs5d5 | 52.8% | 0.279 | 5.8x |

## Which map best says where the water will be?

Lift = P(observed flood | predictor) / P(observed flood | all land in the domain).

| predictor | share of land | lift, dfo_sudden | lift, dfo_recur | lift, viirs1d | lift, viirs5d |
|---|---|---|---|---|---|
| plain_maj | 11.0% | 2.5x | 3.5x | 2.2x | 2.0x |
| plain_any | 13.5% | 2.4x | 3.2x | 2.1x | 1.9x |
| extent_any | 25.1% | 2.2x | 2.8x | 1.8x | 1.6x |
| extent_maj | 20.6% | 2.4x | 3.1x | 1.9x | 1.7x |
| ai4g_flood | 3.7% | 1.9x | 3.5x | 2.4x | 2.1x |

AI4G only covers a third of the land, which flatters the other rows. The same table
on the land AI4G actually assessed, where all three maps can be compared fairly:

| predictor | share of that land | lift, dfo_sudden | lift, dfo_recur | lift, viirs1d | lift, viirs5d |
|---|---|---|---|---|---|
| plain_maj | 33.6% | 1.5x | 1.5x | 1.5x | 1.4x |
| extent_any | 48.0% | 1.7x | 1.8x | 1.5x | 1.5x |
| extent_maj | 43.7% | 1.8x | 1.9x | 1.6x | 1.5x |
| ai4g_flood | 55.5% | 1.6x | 1.8x | 1.4x | 1.4x |

## Watershed level: does the floodplain rank the watersheds that flood?

14,607 MoM watersheds with more than 100 km2 inside the domain. For each one,
the share of its land that is floodplain, that the models flood, and that each
satellite product flagged.

| layer | watersheds with any | median share of watershed | mean share |
|---|---|---|---|
| plain_maj | 10,973 (75%) | 4.96% | 13.29% |
| extent_any | 14,446 (99%) | 21.44% | 28.20% |
| ai4g_flood | 4,145 (28%) | 0.00% | 4.33% |
| dfo_sudden | 12,627 (86%) | 1.57% | 4.03% |
| viirs1d | 13,941 (95%) | 14.21% | 22.04% |
| viirs5d | 14,023 (96%) | 21.36% | 28.18% |

Rank agreement between watershed shares (Spearman, all watersheds):

| | plain_maj | extent_any | ai4g_flood | dfo_sudden | viirs1d | viirs5d |
|---|---|---|---|---|---|---|
| plain_maj | 1.00 | 0.40 | 0.17 | 0.23 | 0.35 | 0.35 |
| extent_any | 0.40 | 1.00 | 0.14 | 0.30 | 0.38 | 0.36 |
| ai4g_flood | 0.17 | 0.14 | 1.00 | -0.24 | -0.05 | -0.07 |
| dfo_sudden | 0.23 | 0.30 | -0.24 | 1.00 | 0.67 | 0.67 |
| viirs1d | 0.35 | 0.38 | -0.05 | 0.67 | 1.00 | 0.97 |
| viirs5d | 0.35 | 0.36 | -0.07 | 0.67 | 0.97 | 1.00 |

Of the 100 watersheds with the largest flooded area in each product, how many are
also in the other's top 100:

| | plain_maj | extent_any | ai4g_flood | dfo_sudden | viirs1d | viirs5d |
|---|---|---|---|---|---|---|
| plain_maj | 100 | 50 | 12 | 10 | 25 | 22 |
| extent_any | 50 | 100 | 19 | 8 | 30 | 33 |
| ai4g_flood | 12 | 19 | 100 | 1 | 5 | 7 |
| dfo_sudden | 10 | 8 | 1 | 100 | 38 | 34 |
| viirs1d | 25 | 30 | 5 | 38 | 100 | 74 |
| viirs5d | 22 | 33 | 7 | 34 | 74 | 100 |

Watersheds where a satellite saw flood but the map says nothing:

| observed layer | watersheds with observed flood | of those, no floodplain mapped | of those, outside the model extent |
|---|---|---|---|
| dfo_sudden | 12,627 | 20.4% | 0.3% |
| viirs1d | 13,941 | 22.7% | 0.6% |
| viirs5d | 14,023 | 22.9% | 0.7% |

*AI4G rows in the watershed tables are depressed by missing tiles: a watershed with no AI4G
tile scores 0, which is why AI4G's rank agreement with the satellites is near zero or
negative. Treat them as "not comparable yet" rather than as disagreement.*

## How this was built

| script | what it does |
|---|---|
| `decode_pmtiles.py` | decodes a MoM-map PMTiles value raster (2% log index, Terrarium WebP, or a colour PNG) back to a global 30" GeoTIFF; `--mode fraction` keeps the percent of each cell covered, which is area preserving |
| `reduce_to_30s.py` | reduces a native-grid accumulation or mask onto the 30" grid by pixel centre: pixels per cell, pixels ever flagged, and the largest day count in the cell |
| `joint_30s.py` | one pass over every 30" layer, packing one bit per layer per cell and counting the codes with true cell areas, so any combination of layers is a sum over one table; also per 10° latitude band, per 0.1° cell, and by model depth |
| `frac_area.py` | unbiased area of a percent-cover layer |
| `report_q1q2.py`, `watershed_stats.py` | the tables above |

Inputs: `dfo_accumulation/dfo_accumulater.tiff` (242 rasters, Jan-Aug 2026),
`viirs1day_full_accumulation/viirs_accumulater.tiff` (326 rasters),
`viirs5day_merged/viirs5day_full.tiff` (327 rasters), `dfo_accumulation/river_plains_mask.tiff`
and `dfo_land_mask.tiff`, and from `MoM-map/data/persistent/`: `ai4g/ai4g_30s.tif`,
`ai4g/ai4g_flood_1km.pmtiles`, `flood_max/flood_extent_1km.pmtiles`,
`flood_max/flood_max_1km.pmtiles`, `river_plains/river_plains_30s.pmtiles`.
