# Do river floodplains coincide with observed floods?

Accumulation: 236 daily rasters (202601..202608), viirs_accumulater.tiff.
Floodplains: GFPLAIN250m (Nardi & Annis), binary mask (https://doi.org/10.6084/m9.figshare.6665165.v1).

Domain: 3,330,051,622 pixels, latitude -54.6467 to 63.9938. pixels inside the GFPLAIN continental footprints and inside the rows every source raster observed.
Floodplain is 3.47% of that domain (115,558,446 pixels).

## Band 1: 141-200 - flood (MoM production's VIIRS flood threshold, value > 140)

- flooded at least once: 105,064,794 pixels (3.155% of the domain)
- of those, 27.0% are on floodplain
- frequency on floodplain: 24.503%, off floodplain: 2.388%
- enrichment 10.3x, odds ratio 13.3
- mean flood count 2.2546 on vs 0.1270 off (17.8x)
- mean flooded fraction of observed days 1.0124% on vs 0.0566% off (17.9x)
- Jaccard overlap 0.1472
- 0.1 degree cell correlation: Pearson 0.289, Spearman 0.214

Flooded fraction of a 0.1 degree cell, by how much of the cell is floodplain:

| floodplain of cell | cells | mean flooded fraction | cells with any flood |
| --- | --- | --- | --- |
| 0% | 1,263,297 | 1.6691% | 247,024 |
| >0-1% | 43,443 | 7.2781% | 39,659 |
| >1-5% | 31,571 | 8.9789% | 29,537 |
| >5-10% | 43,186 | 12.2559% | 40,488 |
| >10-25% | 27,673 | 17.2930% | 25,776 |
| >25-50% | 13,983 | 21.0142% | 12,992 |
| >50-75% | 22,397 | 24.5757% | 20,208 |
| >75-100% | 0 | 0.0000% | 0 |

## Band 2: 130-140 - lower floodwater-fraction codes below that threshold

- flooded at least once: 48,582,734 pixels (1.459% of the domain)
- of those, 29.0% are on floodplain
- frequency on floodplain: 12.210%, off floodplain: 1.072%
- enrichment 11.4x, odds ratio 12.8
- mean flood count 0.2876 on vs 0.0227 off (12.7x)
- mean flooded fraction of observed days 0.1276% on vs 0.0099% off (12.8x)
- Jaccard overlap 0.0940
- 0.1 degree cell correlation: Pearson 0.227, Spearman 0.176

Flooded fraction of a 0.1 degree cell, by how much of the cell is floodplain:

| floodplain of cell | cells | mean flooded fraction | cells with any flood |
| --- | --- | --- | --- |
| 0% | 1,263,297 | 0.7035% | 207,308 |
| >0-1% | 43,443 | 3.4796% | 35,846 |
| >1-5% | 31,571 | 4.4751% | 27,909 |
| >5-10% | 43,186 | 6.1005% | 39,076 |
| >10-25% | 27,673 | 8.8940% | 25,049 |
| >25-50% | 13,983 | 10.5431% | 12,654 |
| >50-75% | 22,397 | 12.0803% | 19,765 |
| >75-100% | 0 | 0.0000% | 0 |

## Band 1 (141-200) by latitude

| latitude | floodplain of band | share of all flood here | on floodplain | freq on | freq off | enrichment |
| --- | --- | --- | --- | --- | --- | --- |
| 60..70 | 0.00% | 11.6% | 0.0% | 0.000% | 11.152% | 0.0x |
| 50..60 | 8.13% | 32.1% | 22.6% | 33.293% | 10.079% | 3.3x |
| 40..50 | 4.73% | 13.8% | 24.2% | 26.366% | 4.109% | 6.4x |
| 30..40 | 4.26% | 7.0% | 32.0% | 19.526% | 1.843% | 10.6x |
| 20..30 | 2.65% | 5.5% | 38.4% | 29.567% | 1.293% | 22.9x |
| 10..20 | 5.53% | 3.5% | 47.6% | 11.326% | 0.730% | 15.5x |
| 0..10 | 4.02% | 5.0% | 48.8% | 22.810% | 1.004% | 22.7x |
| -10..0 | 3.50% | 6.8% | 40.6% | 29.305% | 1.554% | 18.9x |
| -20..-10 | 3.17% | 4.7% | 51.6% | 29.320% | 0.900% | 32.6x |
| -30..-20 | 2.65% | 3.6% | 40.5% | 20.442% | 0.818% | 25.0x |
| -40..-30 | 2.15% | 2.6% | 37.8% | 17.349% | 0.626% | 27.7x |
| -50..-40 | 0.30% | 2.6% | 8.9% | 27.975% | 0.871% | 32.1x |
| -60..-50 | 0.07% | 1.3% | 3.2% | 49.596% | 0.993% | 49.9x |

## Band 2 (130-140) by latitude

| latitude | floodplain of band | share of all flood here | on floodplain | freq on | freq off | enrichment |
| --- | --- | --- | --- | --- | --- | --- |
| 60..70 | 0.00% | 9.7% | 0.0% | 0.000% | 4.336% | 0.0x |
| 50..60 | 8.13% | 37.8% | 21.0% | 16.867% | 5.601% | 3.0x |
| 40..50 | 4.73% | 14.9% | 24.2% | 13.109% | 2.041% | 6.4x |
| 30..40 | 4.26% | 6.8% | 37.1% | 10.206% | 0.768% | 13.3x |
| 20..30 | 2.65% | 5.4% | 40.8% | 14.299% | 0.565% | 25.3x |
| 10..20 | 5.53% | 3.9% | 47.7% | 5.799% | 0.373% | 15.6x |
| 0..10 | 4.02% | 4.2% | 60.0% | 10.784% | 0.301% | 35.8x |
| -10..0 | 3.50% | 5.7% | 47.7% | 13.339% | 0.531% | 25.1x |
| -20..-10 | 3.17% | 5.1% | 56.5% | 16.096% | 0.406% | 39.7x |
| -30..-20 | 2.65% | 3.2% | 49.9% | 10.554% | 0.288% | 36.6x |
| -40..-30 | 2.15% | 2.4% | 46.8% | 8.895% | 0.222% | 40.0x |
| -50..-40 | 0.30% | 0.7% | 13.8% | 5.161% | 0.099% | 52.3x |
| -60..-50 | 0.07% | 0.3% | 5.0% | 9.561% | 0.121% | 78.9x |

