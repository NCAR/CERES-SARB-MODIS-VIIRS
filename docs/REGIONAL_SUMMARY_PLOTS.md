# Regional comparison figures

The July 2008 C6.1 iteration uses
`config/plots/modis_c61_200807_regional_summary.yaml`. Its explicit `name`
reuses the existing gridded run and independently paired daily matchups;
the scientific processing hash is unchanged. Run it on the laptop with:

```bash
export MPLCONFIGDIR="$PWD/.cache/matplotlib" MPLBACKEND=Agg
E="$HOME/miniforge3/envs/sarb-aod/bin"
"$E/sarb-aod" plot -c config/plots/modis_c61_200807_regional_summary.yaml --prefetch-maps
"$E/sarb-aod" plot -c config/plots/modis_c61_200807_regional_summary.yaml
```

The explicit prefetch retrieves coastlines and Natural Earth city locations.
Rendering subsequently uses cached assets only. This plotting command reads
verified daily matchups; it does not recompute gridded data or evaluation.

## Layout and interpretation

- Top: AERONET and MODIS C6.1 daily means across the full figure width, with
  the same overlapping middle-50%-of-sites bands as the existing time series.
- Bottom left: repository link, bold provenance entries, plot date/revision,
  processing method, actual matchup input, tracked plot configuration and
  actual PDF destination. Paths break only between directory components.
- Bottom center: surrounding geography, the analysis rectangle in orange,
  active paired AERONET sites in blue, and labeled major cities in black.
  City markers have area 20 pt² and site markers 12 pt²: approximately a
  one-point increase in marker diameter. Only sites contributing finite
  pairs during the displayed period appear.
- Bottom right: one point per matched regional day and a 1:1 line, with day
  count, bias, RMSE, correlation and normalized absolute error. These are
  the exact same daily means/statistics as the top panel. The scatter does
  not pool individual site-days. Empty regions remain explicit empty plots.

Global cannot show geography beyond the globe. Other map extents use the
configured padding, at least five degrees on each side, and extra latitude
context for broad regions. Analysis boundaries and which observations are
included remain unchanged. Dateline-crossing regions use a compact map
centered on 180°.

## Configurable choices

`plots.regional_layout: summary` selects this composition for the existing
`aeronet_regional_timeseries` suite. Output directories use
`aeronet_regional_summary` so previous series remain available. The default
`timeseries` layout preserves the established inset plots. Only the dedicated
C6.1 plot configuration opts in during the laptop iteration.

`plots.regional_map_padding` defaults to 0.25 and accepts 0.05–1.0.
`plots.regional_city_limit` defaults to six and accepts 0–20. Labels are placed
inside the map without overlapping one another; fewer cities may appear
when space is limited. Cities inside the analysis region rank first, then
population and name provide deterministic ordering. Dataset population
estimates select cities of at least one million; they describe map context,
not a reconstruction of July 2008 city populations.

Locations and names come from
[Natural Earth 110m Populated Places](https://www.naturalearthdata.com/downloads/110m-cultural-vectors/110m-populated-places/),
version 5.1.2 in the initial laptop cache. Natural Earth describes this layer
as country capitals plus selected major cities. The code reads the configured
cache's `.shp`, `.shx` and `.dbf` components and records their SHA-256 values.

PDF Keywords retain matchup pathname/checksum and its recorded creation date
and revision when available, selected city/site coordinates, analysis/view
extents, sampling basis, configuration/processing hashes and producing code
metadata. Legacy matchup files lacking dates or revisions are recorded as
`unknown`; the current plot revision does not replace input history.

## Verification

Regression checks compare daily values and statistics with the existing
renderer, verify scatter inputs and map/site selection, exercise empty and
wide regions and dateline extents, reject missing city caches, and require
protocol-checked PDF/PNG output. Plot protocol now rejects nonfinite text
positions; this catches a Cartopy automatic-title placement failure that
would otherwise silently hide a map title. Explicit title positioning fixes
the new map renderer.
