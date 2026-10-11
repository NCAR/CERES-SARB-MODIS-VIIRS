# Independent daily AERONET validation

User direction on 2026-10-11 replaces three-hour product acceptance with daily
comparisons. Terra and Aqua provide samples during the day; their merged product
does not represent a single overpass or a continuously observed three-hour interval.
The [historical campaign](evaluation_campaign.md) remains available as diagnostic
evidence under its original sampling definition.

## Comparison and acceptance definition

For each UTC day and AERONET site:

1. Average all eligible AERONET observations after converting each to 550 nm,
   applying the configured spectral selection and minimum observation count.
2. At the site's nearest CAM cell, count-weight the valid satellite AOD fields
   from the day's eight output windows. Include valid satellite windows even
   when AERONET has no observations in that window.
3. Pair the independent daily means by site and day. Give each paired site
   equal weight in its regional daily mean, separately for AERONET and satellite.
4. Calculate bias, RMSE and correlation across those paired regional days.
   Each day contributes once, regardless of its number of active sites.

Acceptance requires absolute bias ≤ 0.03, RMSE ≤ 0.10 and correlation ≥ 0.8,
using unrounded metrics and at least `evaluation.min_samples` paired days
(default 3). Undefined metrics are insufficient data. The run-level gate uses
the global set of available AERONET sites; each geographic region also has its
own results. Overlapping regions are never pooled into the global calculation.

The regional time-series plots use these exact daily values and report the same
metrics. Their middle-50% envelopes describe the spread among paired sites'
daily means. Pooled site-day errors are retained separately because averaging
across sites can cancel local errors. Global denotes available sites, not an
area-weighted whole-Earth sample.

Daily satellite uncertainty remains unavailable because no temporal covariance
model has been specified. The original three-hour and CAM7 hourly comparisons
remain diagnostics, with separate archives and reports. Individual L2 overpass
comparisons retain AERONET observations within ±30 minutes of the local overpass.
CAM7 output files retain their required three-hour cadence.

## Baseline results

| Configuration | Days | Bias | RMSE | Correlation | Global daily acceptance |
|---|---:|---:|---:|---:|---|
| July 2008 MODIS, GLS ρ=0.5 | 31 | +0.01512 | 0.02069 | 0.88018 | Pass |
| September 2020 MODIS + VIIRS, GLS ρ=0.5 | 30 | −0.02618 | 0.05062 | 0.91340 | Pass |

July's normalized mean absolute error is 7.86%, defined as
100 × mean absolute daily regional difference / mean daily regional AERONET.
The plot's rounded RMSE is 0.021. It is distinct from the historical 0.15143
RMSE of individual three-hour site matchups.

Global acceptance does not imply every region passes. The baseline regional daily
comparisons retain these failures:

| Comparison | Days | Bias | RMSE | Correlation | Failed daily criterion |
|---|---:|---:|---:|---:|---|
| July Western US | 31 | −0.01444 | 0.05185 | 0.75528 | Correlation |
| September Western US | 30 | −0.19039 | 0.31639 | 0.89338 | Bias, RMSE |
| September 8–14 smoke region | 7 | −1.10387 | 1.15415 | 0.94412 | Bias, RMSE |

## Reproduction and evidence

Grid CLI evaluation defaults to daily pairing. These commands explicitly state
the sampling basis and preserve the historical archives:

```bash
sarb-aod evaluate -c config/runs/modis_l2_200807_gls_rho05.yaml --level grid --pairing daily
sarb-aod evaluate -c config/runs/mixed_l2_202009_gls_rho05.yaml --level grid --pairing daily
MPLCONFIGDIR=$PWD/.cache/matplotlib \
  ~/miniforge3/envs/sarb-aod/bin/python scripts/collect_daily_evaluation_campaign.py
```

The daily campaign covers the original 23 grid experiments plus the separately
plotted July CAM7-v2 control. Its JSON records report/matchup hashes, processing
definitions and verification status. The main TSV contains global daily criteria,
the regional TSV contains regional daily criteria, and the site-day diagnostic
TSV preserves the pooled local errors separately.

All **24 reports are verified**; **18 pass** the global daily criteria. All 24
global daily RMSE values satisfy 0.10. The six remaining global failures are:

| Configuration | Failed criterion | Value |
|---|---|---:|
| July count weighting | Absolute bias | 0.03974 |
| July minimum five pixels | Correlation | 0.78632 |
| July CAM7-v2 control | Absolute bias | 0.08234 |
| September mixed, minimum three pixels | Absolute bias | 0.03166 |
| September mixed, minimum five pixels | Absolute bias | 0.03493 |
| September MODIS-only | Absolute bias | 0.03770 |

The 1,200 pre-existing evaluation files retain their original bytes. Full-precision
results and hashes are in [daily_acceptance_campaign.json](daily_acceptance_campaign.json),
[global results](daily_acceptance_campaign.tsv),
[regional results](daily_acceptance_campaign_regional.tsv), and
[pooled site-day diagnostics](daily_acceptance_campaign_site_day_diagnostics.tsv).
