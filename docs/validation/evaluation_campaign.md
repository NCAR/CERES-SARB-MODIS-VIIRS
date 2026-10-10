# July 2008 and September 2020 evaluation evidence

The campaign evidence is **complete: all 32 expected reports and all 23 grid
summaries are verified**. Completion establishes artifact integrity, not scientific
acceptance: every global RMSE gate fails, and no tested rho calibrates global grid
uncertainty in either month. The accompanying JSON records the exact collection
time, verification status and unrounded results. The recommendations below retain
working defaults with rho=0.5 explicitly provisional; no configuration defaults
have changed. MODIS Collection 7 remains unavailable.

Reproduce the snapshot from the repository root:

```bash
MPLCONFIGDIR=$PWD/.cache/matplotlib \
  ~/miniforge3/envs/sarb-aod/bin/python scripts/collect_evaluation_campaign.py
```

`evaluation_campaign.tsv` contains full-precision global, Southern Ocean and
September 8–14 smoke-box matchup statistics. `evaluation_campaign_coverage.tsv`
contains global gridded coverage and counts. `evaluation_campaign.json` adds report
and matchup SHA256 values, exact QC/weighting settings, per-stream/surface/sensor
statistics, scientific gate outcomes, and explicit missing/invalid artifacts.
The collector expects 32 primary reports. It verifies the scientific configuration,
resolved processing fingerprint and matchup checksum before accepting a report.
For older reports with rounded tables it recalculates from the verified matchup
archive and checks the original unrounded MB, RMSE and correlation gate values.
Neither log `START` lines nor standalone matchup files establish completion.
Grid summaries must cover every configured window, match output provenance, and
agree with the output arrays' counts, coverage and period statistics.
The original long-running L2 batch exited nonzero after exporting its artifacts:
workers retained an older in-memory report module, then imported updated plotting
code requiring `load_matchups`. That historical `ImportError` is recorded in
`.cache/evaluate_l2_campaign.log`; it is not counted as a successful batch exit.
The independent collector verifies the exported artifacts under the current code,
and fresh plotting processes perform their own protocol and visual acceptance.
`evaluation_campaign_sensitivity.tsv` adds full-precision inclusion/exclusion of
spectrally extrapolated AERONET data and instrument sigma 0.01/0.02 for every scope.
These calculations reuse the complete archived candidate cohort, including rows
not eligible under the original primary selection; no raw pairing is rerun.

## July global grid experiments

Values below are rounded for reading; the TSV/JSON preserve the unrounded numbers
used for every gate. Coverage is the mean fraction of f09 cells observed in a
three-hour window, not area-weighted coverage. All rho experiments have the same
4,341 pairs and coverage. Direct archive comparison confirms identical UTC time,
site and reference AOD for every rho and count experiment in each month (4,341
July and 11,380 September pairs). Filters alter the cohort, so their apparent improvements
are descriptive and do not establish improvement on a fixed set of observations.

| Experiment | Pairs | Bias | RMSE | Correlation | std(z) | Window coverage |
|---|---:|---:|---:|---:|---:|---:|
| GLS rho 0 | 4,341 | 0.01038 | 0.15222 | 0.8571 | 2.949 | 10.57% |
| GLS rho 0.25 | 4,341 | 0.00910 | 0.15161 | 0.8582 | 1.995 | 10.57% |
| GLS rho 0.5 | 4,341 | 0.00888 | 0.15143 | 0.8586 | 1.717 | 10.57% |
| GLS rho 0.75 | 4,341 | 0.00881 | 0.15135 | 0.8588 | 1.554 | 10.57% |
| GLS rho 1 | 4,341 | 0.00879 | 0.15131 | 0.8589 | 1.443 | 10.57% |
| Count weighting | 4,341 | 0.03097 | 0.14987 | 0.8726 | 4.395 | 10.57% |
| GLS rho 0.5, minimum 3 pixels | 3,927 | 0.00158 | 0.14549 | 0.8572 | 1.709 | 9.42% |
| GLS rho 0.5, minimum 5 pixels | 3,699 | -0.00215 | 0.14151 | 0.8589 | 1.704 | 8.73% |
| GLS rho 0.5, reject negative AOD | 4,339 | 0.01042 | 0.15122 | 0.8591 | 1.704 | 10.57% |
| GLS rho 0.5, ocean QA 3 | 4,200 | 0.00956 | 0.15333 | 0.8582 | 1.734 | 7.96% |

Every July grid experiment fails the RMSE <= 0.10 gate. Count weighting also fails
the absolute bias <= 0.03 gate; every global grid correlation passes 0.8. Count
weighting's reported sigma is a sample standard error (with the configured floor
for a singleton), so its z statistic does not calibrate the retrieval-error model
used by GLS.

Increasing rho improves global std(z) with almost unchanged RMSE, but **no tested
rho reaches global std(z) approximately 1**. Even the upper physical limit rho=1
leaves std(z)=1.44308. Raising rho beyond 1 would violate the configured correlation
model. The July L2 baseline has std(z)=1.07812, much nearer one
(N=4,088; RMSE=0.11048). The difference suggests investigating
grid-to-point representativeness and uncertainty specification; the different
pairing cohorts prevent attributing the entire difference to gridding alone.

Minimum counts 3 and 5 reduce global RMSE by 3.92% and 6.55%, while losing 9.54%
and 14.79% of matched pairs and 10.88% and 17.39% of occupied cell-windows. These
filters are useful sensitivity cases, but do not yet justify reducing the default
coverage. Rejecting negative retrievals changes global bias from 0.00888 to 0.01042,
with only a 0.13% RMSE reduction and nearly unchanged grid coverage. Keeping valid
negative DT retrievals in the average remains the supported working choice.
The [September negative-AOD scan](sept2020_negative_aod.tsv) also confirms valid
VIIRS DT land negatives down to -0.05: 91,531 SNPP and 69,330 NOAA-20 pixels on
September 8. The nonnegative cutoff experiment therefore changes both MODIS and
VIIRS DT land in September; it is not a MODIS-only sensitivity. No DT ocean or DB
negatives were found in that one-day scan.

## Ocean quality and regional tradeoffs

Here “Southern Ocean” is the configured **latitude band south of 40° S, without
an ocean mask**. It is not an open-ocean validation sample. The July L2 cohort has
31 land and four ocean matchups; September has 170 land, 140 coastal and 27 ocean
matchups. July's 53 grid pairs are from Trelew (33), CEILAP-RG (16) and Crozet
Island (4). The band statistics primarily describe southern land/coastal stations,
so their response to ocean QA cannot be generalized to the open Southern Ocean.

Ocean QA 3 reduces global gridded pixel contributions from 56,032,127 to 38,884,661
(-30.60%) and occupied cell-windows by 24.75%. Global grid RMSE increases from
0.15143 to 0.15333. Global L2 RMSE also increases (0.11048 to 0.11309); the ocean-only
L2 cohort falls from 336 to 152, with RMSE increasing from 0.10233 to 0.13956.
These July results support retaining the retrieval-team ocean QA >= 1 default.
They do not establish that every individual low-QA ocean retrieval is preferable.

The Southern Ocean has only 53 default grid pairs, with small AOD variability:
rho=0.5 gives bias=-0.01155, RMSE=0.03988, correlation=0.4892 and std(z)=0.99621.
Near-unit spread alone does not establish calibrated uncertainty: mean(z)=-0.28092,
with fractions |z|<1 and |z|<2 of 0.75472 and 0.92453.
Changing rho to 1 lowers std(z) to 0.84327 with essentially unchanged RMSE. Thus
raising rho to reduce global std(z) makes this regional uncertainty wider
than its observed residual spread. Ocean QA 3 leaves only 37 grid pairs, raises
RMSE to 0.04978 and lowers correlation to 0.1011. The separate Southern Ocean L2
cohort does show smaller RMSE with QA 3 (0.04387 to 0.03286), but has just 35/32
pairs and lower correlation. The regional results therefore do not justify a
blanket stricter ocean QA rule. Southern Ocean correlation fails the 0.8 gate in
all tested variants; low dynamic range and small N limit its interpretation.

The CAM7-QC L2 experiment increases N from 4,088 to 6,677, while bias rises from
0.01609 to 0.04969, RMSE from 0.11048 to 0.13014, and expected-error coverage falls
from 68.45% to 57.97%. It fails both bias and RMSE gates. This experiment changes
several QC choices together (including land QA, MODIS Best Ocean, scaling and
negative filtering); it is not the archived SP1 product and does not isolate Best
versus Average Ocean. The dedicated Best Ocean experiment keeps the same 4,088
pairs and gives global RMSE=0.11024 versus 0.11048 for Average, with nearly
unchanged bias, correlation and std(z). Its ocean-only RMSE is 0.09942 versus
0.10233 (336 pairs), with correlation slightly lower (0.9157 versus 0.9171).
Southern Ocean RMSE changes only from 0.04387 to 0.04376 (35 pairs). The completed
September MODIS Best Ocean control keeps 31,173 mixed-sensor pairs and gives global RMSE=0.238103
versus 0.238089 for Average, with effectively unchanged bias, correlation and
std(z). Smoke statistics are exactly unchanged. Restricting the archived September
L2 pairs to `sensor == MODIS` and `surface == ocean` gives the same 469 pairs:
Best raises RMSE from 0.06426 to 0.06683 and lowers correlation from 0.9631 to
0.9576. Thus the small global change is not concealing an ocean-only MODIS gain.
The small July benefit does not
persist in September, so these controls support retaining the retrieval-team
Average Ocean choice.

September's CAM7-QC L2 preset gives 38,829 pairs versus 31,173 with retrieval-team
QC, but raises bias from 0.00637 to 0.03265 and RMSE from 0.23809 to 0.28128.
Correlation falls from 0.8550 to 0.8078, expected-error coverage falls from 72.77%
to 67.32%, and both bias and RMSE gates fail. Its std(z)=1.12652 is nearer one
than 1.21332, illustrating why spread alone cannot select a better QC preset.
Smoke N rises from 580 to 651 while RMSE rises from 1.13611 to 1.34560 and bias
becomes more negative (-0.36773 versus -0.29051). After excluding extrapolation,
CAM7-QC smoke RMSE is still higher (0.58750 versus 0.53751). These bundled-preset
comparisons support keeping retrieval-team QC; the cohorts differ and do not
isolate a single quality flag as the cause.

## September smoke and spectral sensitivity

The September mixed baseline's global L2 result has 31,173
pairs, bias=0.00637, RMSE=0.23809, correlation=0.8550 and std(z)=1.21332. At grid
level, 11,380 pairs give bias=-0.04980, RMSE=0.36522, correlation=0.7783 and
std(z)=2.17357: **all three global grid acceptance gates fail**. July's zero
smoke-box count remains insufficient data because the smoke region is date-limited.

Some extreme September reference values use spectral extrapolation. A
[checksum-verified NASA Ames case](sept2020_extrapolation_case.json) has valid
870 nm AOD 5.648606/5.275340 and 1020 nm AOD 4.223785/3.908705, with no valid
bands bracketing 550 nm. The resulting two 550 nm extrapolations are
13.058187/12.521624, averaging 12.789906. The underlying longwave AOD is already
extreme; this is not evidence of a parsing error. Physical smoke severity and
uncertainty from extending the spectrum to 550 nm must be considered separately.
The satellite retrievals are capped at 5, and point versus grid averaging is an
additional representativeness difference. None of these facts warrants silently
discarding the high-reference cases or changing the evaluation default.

| September mixed baseline | Spectral selection | Pairs | Bias | RMSE | Correlation | std(z) |
|---|---|---:|---:|---:|---:|---:|
| Global L2 | Include extrapolation | 31,173 | 0.00637 | 0.23809 | 0.8550 | 1.213 |
| Global L2 | Exclude extrapolation | 31,127 | 0.01280 | 0.16601 | 0.8939 | 1.206 |
| Global grid | Include extrapolation | 11,380 | -0.04980 | 0.36522 | 0.7783 | 2.174 |
| Global grid | Exclude extrapolation | 11,354 | -0.03659 | 0.24457 | 0.7896 | 2.105 |
| Smoke L2 | Include extrapolation | 580 | -0.29051 | 1.13611 | 0.8767 | 1.668 |
| Smoke L2 | Exclude extrapolation | 544 | -0.04218 | 0.53751 | 0.8974 | 1.477 |
| Smoke grid | Include extrapolation | 164 | -1.21137 | 2.17503 | 0.8254 | 2.905 |
| Smoke grid | Exclude extrapolation | 146 | -0.61133 | 1.13422 | 0.7457 | 3.271 |

The table uses instrument sigma=0.01. Exclusion can change a retained matchup's
AERONET mean as well as remove a matchup that lacks enough direct observations;
it also recomputes reference sigma from the retained within-window spread. It is
not merely deletion of a fixed outlier list, and lower RMSE need not imply lower
std(z) when both the cohort and uncertainty denominator change. Exclusion materially reduces
RMSE, but **the global grid still fails every gate and smoke-grid uncertainty
calibration becomes worse**. Its lower correlation also fails 0.8. Thus spectral
extrapolation explains part of the large residuals, not the full grid discrepancy.
The 164 primary smoke-box pairs are only 1.44% of the global sample but contribute
51.11% of its squared error, computed from N times RMSE squared. Outside this
date-limited box, RMSE is still 0.25722; after excluding extrapolation it remains
0.20937. Those other locations can also contain smoke, so this is a geographic
decomposition rather than a clean-air control. The global error gate therefore
cannot be rescued simply by removing the designated smoke-box cohort.
Increasing instrument sigma to 0.02 reduces baseline global grid std(z) to
1.97204 and smoke grid std(z) to 2.87159, still far from one. It changes no bias,
RMSE or correlation and is not a remedy for the model's scientific shortcomings.
The implemented AERONET sigma combines within-window sampling spread and the
instrument term; it does not add a distinct uncertainty for the spectral
extrapolation model. Excluding extrapolation is therefore an explicit sensitivity
to reference construction, not proof that the excluded large AOD values are false.
Southern Ocean selection is unchanged by extrapolation: L2/grid N=337/146,
RMSE=0.03697/0.03310 and std(z)=0.67180/1.14838 at instrument sigma=0.01.

## September gridding comparisons

All 23 campaign grid reports are verified. September's global results use the
configured primary AERONET selection, including extrapolation:

| September experiment | Pairs | Bias | RMSE | Correlation | std(z) |
|---|---:|---:|---:|---:|---:|
| Mixed GLS rho 0 | 11,380 | -0.04985 | 0.36933 | 0.7723 | 3.141 |
| Mixed GLS rho 0.25 | 11,380 | -0.04994 | 0.36602 | 0.7772 | 2.409 |
| Mixed GLS rho 0.5 | 11,380 | -0.04980 | 0.36522 | 0.7783 | 2.174 |
| Mixed GLS rho 0.75 | 11,380 | -0.04968 | 0.36480 | 0.7789 | 2.014 |
| Mixed GLS rho 1 | 11,380 | -0.04956 | 0.36452 | 0.7792 | 1.900 |
| Mixed count weighting | 11,380 | -0.00767 | 0.32444 | 0.8192 | 4.434 |
| Mixed GLS rho 0.5, minimum 3 | 11,114 | -0.05328 | 0.36812 | 0.7809 | 2.176 |
| Mixed GLS rho 0.5, minimum 5 | 10,883 | -0.05566 | 0.37049 | 0.7825 | 2.175 |
| Mixed GLS rho 0.5, nonnegative | 11,379 | -0.04866 | 0.36511 | 0.7786 | 2.163 |
| Mixed GLS rho 0.5, ocean QA 3 | 11,291 | -0.05090 | 0.37387 | 0.7665 | 2.169 |
| MODIS GLS rho 0.5 | 8,933 | -0.04815 | 0.35942 | 0.7755 | 1.954 |
| VIIRS GLS rho 0.5 | 8,884 | -0.05198 | 0.40350 | 0.7801 | 2.013 |
| Mixed DB v2.1 GLS rho 0.5 | 11,379 | -0.04984 | 0.36219 | 0.7821 | 2.175 |

All thirteen September grid experiments fail RMSE <= 0.10. Only count weighting
passes global bias and correlation; every GLS experiment fails all three global
gates. Count reduces global RMSE by 11.17% and smoke RMSE from 2.17503 to 1.90552,
but its global/smoke std(z)=4.434/4.639 demonstrate poor uncertainty calibration.
This is a material accuracy-versus-uncertainty tradeoff, not evidence that GLS
uniformly improves the AOD mean. No tested rho calibrates global or smoke-grid
uncertainty: even rho=1 gives std(z)=1.900 globally and 2.678 in smoke. The Southern
Ocean alone reaches std(z)=1.044 at rho=0.75 or 0.970 at rho=1, with almost
unchanged RMSE, again showing the regional dependence.

The weak RMSE response to rho is consistent with the implemented estimator:
within-stream inverse-variance means do not depend on rho; rho changes their
variance and therefore their relative weights when streams are combined. Raising
rho can widen uncertainties but cannot directly repair a within-stream spatial
mean that differs from the point reference. Count and GLS use different pixel
weights, and their exact common-cohort comparison confirms that this choice
materially affects smoke AOD. The experiments do not isolate how much of the
remaining error comes from retrieval bias versus spatial/time representativeness.

Unlike July, minimum counts 3/5 worsen September RMSE and bias while reducing
sampling. Minimum 3 leaves smoke statistics exactly unchanged; minimum 5 removes
two smoke pairs and slightly worsens smoke RMSE. The data do not support promoting
either minimum-count filter. Removing valid negative retrievals changes little
in September and is not a solution to extreme smoke residuals.

Ocean QA 3 raises global and smoke grid RMSE to 0.37387 and 2.25712, respectively,
while reducing coverage. The separate September L2 comparison also gives no
global gain (RMSE=0.23838 versus 0.23809). Southern Ocean grid RMSE does improve
slightly (0.03310 to 0.03247), but this small regional benefit does not offset the
global sampling loss or establish a universally stricter QA rule.

Mixed sensor coverage and matchup count exceed either sensor alone. MODIS-only,
VIIRS-only and mixed RMSE are 0.35942, 0.40350 and 0.36522, respectively; their
different sampled cohorts preclude treating that ranking as a controlled sensor
accuracy comparison. The DB v2.1 grid comparison has slightly lower global and
smoke RMSE (0.36219 and 2.13815), with nearly unchanged global calibration.

The completed NOAA-20 DB v2.1 L2 control has 31,144 mixed-sensor pairs versus
31,173 for v2. Global RMSE improves slightly (0.23809 to 0.23784), while correlation
falls (0.8550 to 0.8538) and std(z) rises (1.21332 to 1.21601). Smoke RMSE decreases
from 1.13611 to 1.13246 with 578 versus 580 pairs; smoke correlation falls from
0.8767 to 0.8744 while std(z) improves from 1.66761 to 1.64408. Southern latitude-band
RMSE increases from 0.03697 to 0.03717, with 337 pairs in each version but a
different cohort. The affected
NOAA-20 DB stream has 5,380 versus 5,409 pairs: RMSE improves from 0.29513 to
0.29426, but bias becomes more negative (-0.02338 versus -0.02114), correlation
falls from 0.8816 to 0.8756, std(z) increases from 1.15013 to 1.16212, and
expected-error coverage falls from 81.76% to 81.50%. All seven unchanged streams
have identical reported metrics. These small, mixed changes and differing cohorts
do not establish a consistently better version. Retain v2 as the working baseline
and keep v2.1 available as an explicit version comparison.

## Coverage and working defaults

All 23 configured grid summaries are complete and verified, including
September's 240 windows per run. September mean window coverage is 9.90% for
MODIS, 10.89% for VIIRS and 14.58% for mixed sensors. The mixed product contains
407,897,909 retrieval contributions, compared with 50,565,187 MODIS and
357,332,722 VIIRS contributions. Mixing therefore improves sampled coverage;
these totals alone do not demonstrate improved AERONET accuracy. Minimum counts
3/5 reduce mixed window coverage to 13.61%/13.03%; ocean QA 3 reduces it to 13.19%
and removes 19.63% of contributions. DB v2.1 gives 14.57% coverage and 405,959,039
contributions, a small global change despite the larger smoke-box sampling change.
The independent [September retrieval/grid verification](sept2020_verification.md)
is complete: Deep Blue p90 exceeds Dark Target p90 for every platform, mixed means
remain between sensor companions, and stream disagreement exceeds one in the
smoke box. Its strict rejected-high-AOD gate remains unmet because native validity
already caps every selected retrieval at 5. The gate was not tuned to pass.
NOAA-20 DB v2.1 has 6.70% fewer accepted smoke-box retrievals than v2 in that scan;
this retrieval count is distinct from the smaller loss of L2 AERONET matchups.

The current evidence supports retaining retrieval-team QC, Average Ocean, valid
negative AOD and minimum count 1 as working defaults. Keep rho=0.5 explicitly
provisional: its std(z) is near one in the small July southern latitude-band
sample, but its global uncertainty spread is inadequate; rho=1 improves global
spread without solving it. Keep GLS as the uncertainty-aware working estimator,
with count weighting as a documented accuracy sensitivity: count improves
September smoke RMSE but fails July bias and has much larger global std(z) in
both months. The completed controls do not justify promoting a different rho,
the bundled CAM7-QC preset, Best Ocean or DB v2.1. These are working choices for
continued evaluation, not a scientifically accepted production configuration:
every one of the 32 primary reports fails its global RMSE gate, and global grid
uncertainty remains uncalibrated.

This campaign does not resolve three external limits: MODIS Collection 7 aerosol
products remain unavailable; CAM7's July 2008 SP1 archive is absent locally, so
there is no completed archive-to-archive baseline comparison; and AMI deployment
still requires verified production paths, archive layout, scheduler/allocation,
environment and processing dates. The portable monthly driver is implemented,
but these local validation results do not establish AMI production readiness.
