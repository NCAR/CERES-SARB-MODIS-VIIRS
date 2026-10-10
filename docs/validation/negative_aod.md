# Negative retrievals in the September campaign

A complete scan of September 8, 2020 under the mixed baseline configuration
finds negative AOD in **both MODIS and VIIRS Dark Target land**. The original
handoff statement that only MODIS DT land was negative was too broad. The
reader already preserves these valid retrievals, so this corrects the
interpretation and documentation without changing processing.

| Stream | Accepted land pixels | Negative pixels | Negative fraction | Pixels at −0.05 |
|---|---:|---:|---:|---:|
| Terra DT | 145,130 | 15,633 | 10.772% | 6,481 |
| Aqua DT | 112,919 | 13,675 | 12.110% | 3,715 |
| SNPP DT | 710,967 | 91,531 | 12.874% | 30,405 |
| NOAA-20 DT | 699,312 | 69,330 | 9.914% | 21,693 |

Every negative value in this scan belongs to a DT land retrieval, and each
stream's minimum is −0.05. No accepted negative DT ocean or DB land/water AOD
was found in this day. These are pixel distributions after configured QA,
native validity, geolocation and UTC-day checks, rather than independent
footprints or global area fractions. They do not establish a universal
statement for unexamined days or collections.

The grid campaign's `aod_min: 0` sensitivity therefore removes both MODIS and
VIIRS DT land negatives. A VIIRS negative-fraction map is expected to be
nonzero. Monthly gridded fractions use the map suite's documented aggregation
and need not equal these one-day pixel fractions. Final CAM means remain
clipped at zero under the baseline configuration.

The [exact table](sept2020_negative_aod.tsv) names each source field and the
first file containing a negative retrieval. The [JSON evidence](sept2020_negative_aod.json)
records the resolved configuration, processing hash, all input paths, counts
and minima. Reproduce with:

```bash
~/miniforge3/envs/sarb-aod/bin/python scripts/investigate_negative_aod.py \
  --config config/runs/mixed_l2_202009_gls_rho05.yaml --day 2020-09-08 \
  --output docs/validation/sept2020_negative_aod.json
```
