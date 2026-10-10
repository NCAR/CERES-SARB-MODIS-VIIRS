# AERONET test fixtures

Raw AERONET Web Service v3 text (Version 3 Direct Sun, AOD Level 2.0, all points `AVG=10`):
the service's 5 information lines and header line, followed by a subset of its data rows
(114 columns, fill −999). Rows were extracted unchanged from full-UTC-day, all-sites
responses retrieved 2026-10-10. A full UTC day D is requested with `hour=0` on D and
`day2 = D+1, hour2=0` (exclusive end); `hour2=23` on D loses late-UTC data (see
docs/IMPLEMENTATION_PLAN.md Task 3.1).

| File | Source request | Rows | Purpose |
|---|---|---|---|
| `AERONET_AOD20_ALLPOINTS_20080701_GSFC.csv` | 2008-07-01 → 2008-07-02 00 UTC, all sites; GSFC rows | 34 (10:38–23:45 UTC) | single-site parse, full-day boundary |
| `AERONET_AOD20_ALLPOINTS_20080701_EDGECASES.csv` | same response | 4 | Avignon (no 500 nm), Helsinki_Lighthouse (AERONET-OC: 443/490/667/865 nm, no 440/500) |
| `AERONET_AOD20_ALLPOINTS_20200908_EDGECASES.csv` | 2020-09-08 → 2020-09-09 00 UTC, all sites | 11 | NASA_Ames smoke (440 and 500 nm missing at very high AOD), Galata_Platform (OC), Birdsville (eastern site, late UTC), Missoula (23 UTC), GSFC |

Request URLs:

    https://aeronet.gsfc.nasa.gov/cgi-bin/print_web_data_v3?year=2008&month=7&day=1&hour=0&year2=2008&month2=7&day2=2&hour2=0&AOD20=1&AVG=10&if_no_html=1
    https://aeronet.gsfc.nasa.gov/cgi-bin/print_web_data_v3?year=2020&month=9&day=8&hour=0&year2=2020&month2=9&day2=9&hour2=0&AOD20=1&AVG=10&if_no_html=1

AERONET data are public; acknowledge the AERONET project and the site PIs in any publication.
