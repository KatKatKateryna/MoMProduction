# Do river floodplains coincide with observed floods?

Accumulation: 242 daily rasters (202601..202608), dfo_accumulater.tiff.
Floodplains: GFPLAIN250m (Nardi & Annis), binary mask (https://doi.org/10.6084/m9.figshare.6665165.v1).

Domain: 8,382,631,118 pixels, latitude -54.7 to 64.0. pixels inside the GFPLAIN continental footprints and inside the rows every source raster observed.
Floodplain is 3.61% of that domain (302,505,027 pixels).

## Band 1: 3 - flood (unusual)

- flooded at least once: 27,892,509 pixels (0.333% of the domain)
- of those, 28.2% are on floodplain
- frequency on floodplain: 2.597%, off floodplain: 0.248%
- enrichment 10.5x, odds ratio 10.7
- mean flood count 0.1485 on vs 0.0085 off (17.4x)
- mean flooded fraction of observed days 0.0621% on vs 0.0036% off (17.4x)
- Jaccard overlap 0.0244
- 0.1 degree cell correlation: Pearson 0.068, Spearman -0.250

Flooded fraction of a 0.1 degree cell, by how much of the cell is floodplain:

| floodplain of cell | cells | mean flooded fraction | cells with any flood |
| --- | --- | --- | --- |
| 0% | 3,261,891 | 0.2072% | 324,988 |
| >0-1% | 66,695 | 0.6033% | 24,841 |
| >1-5% | 55,003 | 0.7356% | 24,474 |
| >5-10% | 86,942 | 1.0487% | 45,886 |
| >10-25% | 62,920 | 1.5588% | 38,087 |
| >25-50% | 34,540 | 2.0618% | 22,712 |
| >50-75% | 70,563 | 2.7445% | 45,764 |
| >75-100% | 0 | 0.0000% | 0 |

## Band 2: 2 - recurring flood (regular, seasonal inundation)

- flooded at least once: 11,202,131 pixels (0.134% of the domain)
- of those, 45.7% are on floodplain
- frequency on floodplain: 1.691%, off floodplain: 0.075%
- enrichment 22.4x, odds ratio 22.8
- mean flood count 0.1720 on vs 0.0072 off (24.0x)
- mean flooded fraction of observed days 0.0722% on vs 0.0030% off (23.9x)
- Jaccard overlap 0.0166
- 0.1 degree cell correlation: Pearson 0.101, Spearman -0.317

Flooded fraction of a 0.1 degree cell, by how much of the cell is floodplain:

| floodplain of cell | cells | mean flooded fraction | cells with any flood |
| --- | --- | --- | --- |
| 0% | 3,261,891 | 0.0654% | 158,134 |
| >0-1% | 66,695 | 0.1672% | 11,517 |
| >1-5% | 55,003 | 0.2234% | 11,855 |
| >5-10% | 86,942 | 0.3558% | 23,701 |
| >10-25% | 62,920 | 0.6913% | 21,831 |
| >25-50% | 34,540 | 1.1770% | 14,263 |
| >50-75% | 70,563 | 1.9063% | 29,767 |
| >75-100% | 0 | 0.0000% | 0 |

## Band 1 (3) by latitude

| latitude | floodplain of band | share of all flood here | on floodplain | freq on | freq off | enrichment |
| --- | --- | --- | --- | --- | --- | --- |
| 60..70 | 0.00% | 16.8% | 0.0% | 9.677% | 1.594% | 6.1x |
| 50..60 | 8.22% | 34.7% | 32.7% | 5.239% | 0.965% | 5.4x |
| 40..50 | 4.70% | 11.6% | 34.2% | 3.207% | 0.304% | 10.6x |
| 30..40 | 4.26% | 4.2% | 41.2% | 1.538% | 0.098% | 15.7x |
| 20..30 | 2.66% | 2.5% | 37.4% | 1.326% | 0.061% | 21.9x |
| 10..20 | 5.52% | 1.8% | 55.7% | 0.696% | 0.032% | 21.5x |
| 0..10 | 4.03% | 1.7% | 66.3% | 1.062% | 0.023% | 46.8x |
| -10..0 | 3.54% | 2.3% | 52.1% | 1.302% | 0.044% | 29.7x |
| -20..-10 | 3.15% | 3.8% | 55.5% | 2.547% | 0.066% | 38.4x |
| -30..-20 | 2.65% | 8.4% | 25.2% | 3.042% | 0.246% | 12.4x |
| -40..-30 | 2.12% | 8.9% | 22.9% | 3.653% | 0.267% | 13.7x |
| -50..-40 | 0.30% | 3.2% | 17.4% | 7.015% | 0.101% | 69.6x |

## Band 2 (2) by latitude

| latitude | floodplain of band | share of all flood here | on floodplain | freq on | freq off | enrichment |
| --- | --- | --- | --- | --- | --- | --- |
| 60..70 | 0.00% | 13.5% | 0.0% | 3.226% | 0.515% | 6.3x |
| 50..60 | 8.22% | 35.0% | 51.1% | 3.319% | 0.284% | 11.7x |
| 40..50 | 4.70% | 16.0% | 51.8% | 2.689% | 0.123% | 21.8x |
| 30..40 | 4.26% | 9.3% | 46.8% | 1.551% | 0.079% | 19.7x |
| 20..30 | 2.66% | 5.9% | 50.1% | 1.684% | 0.046% | 36.8x |
| 10..20 | 5.52% | 3.1% | 52.2% | 0.443% | 0.024% | 18.7x |
| 0..10 | 4.03% | 2.2% | 67.3% | 0.553% | 0.011% | 49.2x |
| -10..0 | 3.54% | 2.9% | 69.1% | 0.849% | 0.014% | 60.9x |
| -20..-10 | 3.15% | 6.2% | 76.2% | 2.298% | 0.023% | 98.3x |
| -30..-20 | 2.65% | 2.6% | 50.1% | 0.739% | 0.020% | 36.9x |
| -40..-30 | 2.12% | 2.3% | 41.1% | 0.689% | 0.021% | 32.3x |
| -50..-40 | 0.30% | 1.1% | 14.3% | 0.788% | 0.014% | 55.2x |

