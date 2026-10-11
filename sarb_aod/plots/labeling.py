# Ported from NCAR/DAVINCI origin/develop, davinci_monet/plots/labeling.py (Apache-2.0).
"""Central, publication-quality text composition for all DAVINCI plots.

Pure functions only — no matplotlib, no I/O. Renderers call these instead of
building label/title strings themselves.
"""

from __future__ import annotations

import math
import posixpath
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from sarb_aod.plots.labels import (
    _QUANTITY_LOWERCASE_KEEP,
    AOD_DISPLAY_NAMES,
    DISPLAY_NAMES,
    SPECIES_WORD_TO_FORMULA,
    VARIABLE_DISPLAY_NAMES,
    format_plot_title,
    get_variable_label,
    get_variable_units,
)
from sarb_aod.plots.titles import title_for_labeled_subset, title_violations, validate_title

if TYPE_CHECKING:
    import xarray as xr

_DIMENSIONLESS = {"", "1", "none", "dimensionless", "unitless", "fraction"}


def davinci_tagline() -> str:
    """Attribution above the shared colorbar in period-mean spatial figures."""
    return "DAVINCI - Data Analysis and Visual Intelligence for Climate & Chemistry"


def format_units(units: str | None) -> str:
    """Rewrite a raw unit string to negative-exponent SI LaTeX (D3).

    Strings that already contain non-ASCII characters or LaTeX (``$``) are
    returned unchanged — they are assumed to be pre-formatted.
    """
    if units is None:
        return ""
    s = str(units).strip()
    if s.lower() in _DIMENSIONLESS:
        return ""
    # Pass through pre-formatted strings (Unicode or LaTeX already present).
    if "$" in s or not s.isascii():
        return s
    s = re.sub(r"(?<![A-Za-z])ug(?![A-Za-z])", r"$\\mu$g", s)

    # division: "/unitN" -> " unit^{-N}" (N defaults to 1)
    def _div(m: re.Match[str]) -> str:
        unit, power = m.group(1), m.group(2) or "1"
        return f" {unit}$^{{-{power}}}$"

    s = re.sub(r"/([A-Za-z]+)(\d+)?", _div, s)
    # space/dash negative exponents: "unit-N" -> "unit^{-N}"
    s = re.sub(r"([A-Za-z])-(\d+)", lambda m: f"{m.group(1)}$^{{-{m.group(2)}}}$", s)
    # remaining positive exponents: "unitN" -> "unit^{N}"
    s = re.sub(r"([A-Za-z])(\d+)", lambda m: f"{m.group(1)}$^{{{m.group(2)}}}$", s)
    return s.strip()


_SOURCE_ACRONYMS = {
    "cesm": "CESM",
    "cam": "CAM",
    "wrf": "WRF",
    "wrfchem": "WRF-Chem",
    "merra2": "MERRA-2",
    "geoschem": "GEOS-Chem",
    "geos": "GEOS",
    "airnow": "AirNow",
    "aeronet": "AERONET",
    "pandora": "Pandora",
    "ceres": "CERES",
    "modis": "MODIS",
    "tropomi": "TROPOMI",
    "tempo": "TEMPO",
    "ufs": "UFS",
    "cmaq": "CMAQ",
    "ebaf": "EBAF",
    "ssf": "SSF",
    "us": "US",
    "usa": "USA",
    "viirs": "VIIRS",
    "terra": "Terra",
    "aqua": "Aqua",
    "snpp": "SNPP",
    "noaa20": "NOAA-20",
    "noaa-20": "NOAA-20",
    "cam7": "CAM7",
    "aod": "AOD",
    "dt": "Dark Target",
    "db": "Deep Blue",
    "c61": "C6.1",
    "c7": "C7",
}


def source_display_name(source_label: str | None, *, rho: float | None = None) -> str:
    """Friendly source name from a raw config key (D5); never ALL-CAPS."""
    if not source_label:
        return ""
    key = str(source_label).strip().lower()
    if key == "gls" and rho is not None:
        if not math.isfinite(rho) or not 0 <= rho <= 1:
            raise ValueError("rho must be finite and between 0 and 1")
        return f"GLS (ρ = {rho:g})"
    if key in DISPLAY_NAMES:
        return DISPLAY_NAMES[key]
    # Composed display labels (including experiment labels with rho) have
    # already passed through the labeling layer; preserve their typography.
    if "_" not in str(source_label) and (
        " " in str(source_label) or not str(source_label).isascii()
    ):
        return str(source_label).strip()
    out = []
    for tok in str(source_label).split("_"):
        if not tok:
            continue
        low = tok.lower()
        if low in _SOURCE_ACRONYMS:
            out.append(_SOURCE_ACRONYMS[low])
        elif low in VARIABLE_DISPLAY_NAMES:
            out.append(VARIABLE_DISPLAY_NAMES[low])
        else:
            out.append(tok.capitalize())
    return " ".join(out)


def collection_source_name(source: str, collections: Sequence[str]) -> str:
    """Identify MODIS collections without changing the underlying panel keys."""
    name = source_display_name(source)
    if source != "modis":
        return name
    return f"{name} {' / '.join('C' + value for value in sorted(set(collections)))}"


def _provenance_path_lines(path: str, width: int) -> list[str]:
    """Indent values and wrap only at slashes, never inside a path component."""
    lines = []
    line = "  "
    for part in re.findall(r"[^/]*/|[^/]+$", path):
        if line.strip() and len(line + part) > width:
            lines.append(line)
            line = "    "
        line += part
    if line.strip():
        lines.append(line)
    return lines


def provenance_repository_url(provenance: Mapping[str, Any]) -> str:
    """Keep the repository's literal URL intact above the provenance heading."""
    return str(provenance["repository_url"])


def _provenance_paths(paths: Sequence[str], width: int) -> list[str]:
    """Group multiple absolute inputs beneath their shared directory root."""
    if len(paths) > 1 and all(path.startswith("/") for path in paths):
        root = posixpath.commonpath(paths).rstrip("/") + "/"
        if root != "/":
            return _provenance_path_lines(root, width) + [
                "  " + line
                for path in paths
                for line in _provenance_path_lines(path.removeprefix(root), width - 2)
            ]
    return [line for path in paths for line in _provenance_path_lines(path, width)]


def provenance_panel_sections(
    provenance: Mapping[str, Any], *, width: int = 66
) -> list[tuple[str, str]]:
    """Heading/body pairs, with indented values and intact path components."""
    dates = sorted({value[:10] for value in provenance["grid_created_utc"]})
    processed = " – ".join(dict.fromkeys([dates[0], dates[-1]])) if dates else "Unknown"
    method = source_display_name(provenance["weighting"], rho=provenance["rho"])
    if provenance["weighting"] == "gls":
        method += f" | Birge: {'On' if provenance['birge'] else 'Off'}"
    qa = source_display_name(provenance["qc"])
    if provenance["qc_config"]["overrides"]:
        qa += " + Overrides"
    sections = [
        ("Provenance", ""),
        (
            "Created (UTC):",
            f"  Grids: {processed}\n  Plot: {provenance['plot_created_utc']}",
        ),
        (
            "Method:",
            f"  {method} | {qa}\n"
            f"  {provenance['grid']} | Count-Weighted Mean\n"
            f"  Time Windows: {provenance['windows']} × "
            f"{provenance['window_duration_seconds'] / 3600:g} Hours",
        ),
        (
            "Code (Git):",
            "  Grids: "
            + ", ".join(value[:12] for value in provenance["processing_commits"])
            + f"\n  Plot: {provenance['plot_git_commit'][:12]}",
        ),
    ]
    for label, paths in (
        ("L2 Inputs (Configured)", provenance["l2_input_templates"]),
        ("Grid Outputs (Read)", [provenance["grid_output_template"]]),
        ("PDF Output", [provenance["pdf_output"]]),
    ):
        sections.append(
            (
                label + ":",
                "\n".join(_provenance_paths(paths, width)),
            )
        )
    return sections


def provenance_panel_text(provenance: Mapping[str, Any], *, width: int = 66) -> str:
    """Plain-text equivalent of the styled panel (also useful outside plots)."""
    return "\n".join(
        text
        for section in provenance_panel_sections(provenance, width=width)
        for text in section
        if text
    )


def _normalize_quantity_label(raw: str) -> str:
    """Normalize an auto-derived quantity label for consistent display.

    Applied only to labels that originate from raw data attributes
    (``long_name``, ``standard_name``) — NOT to user-supplied titles or
    lookup-table names.  Three passes, in order:

    1. Species word/phrase → LaTeX formula (case-insensitive, whole-word,
       longest-first, skips tokens already inside ``$…$``).
    2. Smart title-case: capitalise each word EXCEPT tokens that contain
       ``$`` (already LaTeX), tokens containing a digit, all-uppercase
       tokens, and a keep-set of unit abbreviations and particles.
    3. Formula subscript via ``format_plot_title`` (idempotent).
    """
    text = raw

    # ---- pass 1: species word → formula ------------------------------------
    # We must not touch substrings that are already inside $…$.  Strategy:
    # split on dollar-sign delimited segments and only process plain-text
    # segments.
    def _apply_species_map(s: str) -> str:
        """Replace species words in a plain-text segment."""
        for phrase, formula in SPECIES_WORD_TO_FORMULA:
            # Whole-word / whole-phrase match, case-insensitive.
            pattern = r"(?<![A-Za-z])" + re.escape(phrase) + r"(?![A-Za-z])"
            s = re.sub(pattern, formula, s, flags=re.IGNORECASE)
        return s

    # Split into (plain, latex, plain, latex, …) alternating segments.
    # Dollar-sign blocks: $...$ (non-nested, greedy).
    parts = re.split(r"(\$[^$]*\$)", text)
    text = "".join(
        part if (i % 2 == 1) else _apply_species_map(part) for i, part in enumerate(parts)
    )

    # ---- pass 2: smart title-case ------------------------------------------
    def _title_case_token(tok: str) -> str:
        # Tokens that are already LaTeX (contain $): leave untouched.
        if "$" in tok:
            return tok
        if tok.lower() in _SOURCE_ACRONYMS:
            return _SOURCE_ACRONYMS[tok.lower()]
        # Tokens containing a digit: leave untouched (NO2, 500, 2.5).
        if any(c.isdigit() for c in tok):
            return tok
        # All-uppercase tokens (TOA, LW, AOD, OLR, CO, UV, …): leave as-is.
        # A token is "all-uppercase" if stripping punctuation leaves only
        # uppercase ASCII letters (length ≥ 2, or a known 1-char acronym).
        alpha = re.sub(r"[^A-Za-z]", "", tok)
        if alpha and alpha == alpha.upper() and (len(alpha) >= 2 or alpha in {"K", "W"}):
            return tok
        # Keep-set (units + particles): strip surrounding punctuation before
        # comparing.
        stripped = tok.strip("()[].,;:!?").lower()
        if stripped in _QUANTITY_LOWERCASE_KEEP:
            return tok
        # Default: capitalise first letter, leave rest.
        return tok[0].upper() + tok[1:] if tok else tok

    # Tokenise preserving spaces and punctuation attached to words.
    # We split on spaces to handle multi-char tokens like "(500".
    text = " ".join(_title_case_token(t) for t in text.split(" "))

    # ---- pass 3: formula subscript (idempotent) ----------------------------
    return format_plot_title(text)


def quantity_label(dataset: "xr.Dataset", var_name: str) -> str:
    """Quantity name only (no source, no units), normalised and chem-formatted.

    When the label comes from the lookup table or a ``display_name`` attr it
    is returned as-is after a formula-subscript pass (idempotent for
    already-formatted strings).

    When the label comes from a raw ``long_name`` / ``standard_name`` attr it
    is additionally normalised:
      1. Species words/phrases are replaced with LaTeX formulas
         (e.g. "Ozone" → "O$_3$", "nitrogen dioxide" → "NO$_2$").
      2. Smart title-case is applied (preserving existing LaTeX tokens,
         digit-containing tokens, all-uppercase acronyms, and a keep-set of
         unit abbreviations and particles).
      3. Chemical formula subscripts are applied via ``format_plot_title``.

    ``title_text`` is deliberately NOT changed — user-supplied titles must
    pass through unchanged.
    """
    if var_name in AOD_DISPLAY_NAMES and not (
        var_name in dataset and dataset[var_name].attrs.get("display_name")
    ):
        return AOD_DISPLAY_NAMES[var_name]
    raw = get_variable_label(dataset, var_name, include_prefix=False)

    # Detect whether the label came from a raw attr or the lookup / display_name
    # path. Raw attrs produce labels that differ from what the lookup would give
    # for the same var. A simple heuristic: check the variable's own attrs.
    var_from_attr = False
    if var_name in dataset:
        attrs = dataset[var_name].attrs
        if attrs.get("display_name"):
            # display_name is already publication-quality: only do subscripts.
            return format_plot_title(raw)
        if attrs.get("long_name") or attrs.get("standard_name"):
            var_from_attr = True

    if var_from_attr:
        return _normalize_quantity_label(raw)
    # Lookup-table / format_variable_display_name path: subscripts only.
    return format_plot_title(raw)


def _distinctive_source_tokens(source: str, quantity: str) -> list[str]:
    """Source-name tokens that are NOT already part of the quantity.

    Lets a source key that redundantly embeds the quantity (e.g.
    ``cesm_no2_column``) contribute only its distinctive identifier (``CESM``)
    when placed next to that quantity, avoiding repetition.
    """
    q_tokens = set((quantity or "").split())
    return [t for t in source_display_name(source).split() if t not in q_tokens]


def distinctive_source_name(source_label: str, quantity: str) -> str:
    """Source display name for use NEXT TO a quantity (e.g. a single-source map
    title ``Quantity (Source)``), with quantity-overlapping tokens removed so a
    key like ``cesm_no2_column`` beside ``NO2 Column`` yields ``CESM`` (not
    ``CESM NO2 Column``). Falls back to the full display name if nothing is left.
    """
    toks = _distinctive_source_tokens(source_label, quantity)
    return " ".join(toks) or source_display_name(source_label)


def axis_label(quantity: str, units: str | None, source: str | None = None) -> str:
    """Compose an axis label (D2 context-aware, with de-dup).

    When the source name overlaps the quantity, only the source's distinctive
    tokens are kept, so e.g. ``cesm_no2_column`` + ``Tropospheric NO2 Column``
    renders ``CESM Tropospheric NO2 Column`` (not ``CESM NO2 Column
    Tropospheric NO2 Column``).

    The quantity is normalised here (species words → formulas, smart title-case,
    formula subscripts) so axes/colorbars are consistent regardless of whether
    the caller passed a lookup name or a raw ``long_name`` — normalisation is
    idempotent on already-formatted strings.
    """
    q = _normalize_quantity_label(quantity or "")
    if source:
        src = source_display_name(source)
        if q and q in src:
            # The source name already contains the quantity (in good order),
            # e.g. quantity "NO2" with source "CESM NO2 Column" -> use the source
            # name as-is rather than reordering tokens ("CESM Column NO2").
            label = src
        else:
            distinctive = _distinctive_source_tokens(source, q)
            if distinctive and q:
                label = f"{' '.join(distinctive)} {q}"
            elif distinctive:
                label = " ".join(distinctive)
            else:
                label = q
    else:
        label = q
    u = format_units(units)
    return f"{label} ({u})" if u else label


def legend_label(
    source_label: str, uncertainty: str | None = None, *, band_drawn: bool = False
) -> str:
    """Compose a legend entry: friendly name, optionally with uncertainty note."""
    name = source_display_name(source_label)
    return f"{name} ({uncertainty})" if uncertainty and band_drawn else name


def bias_label(
    y_source: str,
    x_source: str,
    units: str | None,
    quantity: str | None = None,
) -> str:
    """Bias colorbar label '<Ysrc> − <Xsrc> (units)' — viewer-facing, never x/y.

    The word "Bias" lives in the plot TITLE (``title_text(..., operation="Bias")``),
    so the colorbar carries only the direction and units to avoid repetition.
    When ``quantity`` is given (it already appears in the title), tokens each
    source shares with it are stripped so the label stays terse, e.g.
    ``cesm_no2_column`` vs ``pandora`` with quantity ``NO2 Column`` →
    ``CESM − Pandora``. Any remaining shared trailing tokens are factored.
    """
    if quantity:
        yw = _distinctive_source_tokens(y_source, quantity)
        xw = _distinctive_source_tokens(x_source, quantity)
    else:
        yw = source_display_name(y_source).split()
        xw = source_display_name(x_source).split()
    while yw and xw and yw[-1] == xw[-1]:
        yw.pop()
        xw.pop()
    y = " ".join(yw) or source_display_name(y_source)
    x = " ".join(xw) or source_display_name(x_source)
    core = f"{y} − {x}"
    u = format_units(units)
    return f"{core} ({u})" if u else core


def title_text(quantity: str, operation: str | None = None) -> str:
    """Terse plot title: chem-format the quantity, optionally append operation."""
    q = _normalize_quantity_label(AOD_DISPLAY_NAMES.get(quantity, quantity) or "")
    return f"{q} {_normalize_quantity_label(operation)}".strip() if operation else q


def sourced_title(source_label: str | None, quantity: str) -> str:
    """Single-source plot title with the SOURCE NAME FIRST: ``<Source> <Quantity>``.

    The source's distinctive tokens (vs the quantity) lead the title, so a
    single-source map/timeseries reads ``AERONET AOD (500 nm)`` or ``AirNow O3``
    rather than ``AOD (500 nm) (AERONET)``. Mirrors :func:`axis_label`'s
    source-first, de-duplicated ordering. Falls back to the bare quantity when
    no source is given.
    """
    if not source_label:
        return quantity
    src = distinctive_source_name(source_label, quantity)
    return f"{src} {quantity}" if src else quantity


def subtitle_text(start: Any, end: Any) -> str:
    """Date-range subtitle: 'YYYY-MM-DD – YYYY-MM-DD' or single date or ''."""
    if not start:
        return ""
    s = str(start).split(" ")[0].split("T")[0]
    e = str(end).split(" ")[0].split("T")[0] if end else s
    return s if s == e else f"{s} – {e}"


def map_subtitle(
    start: Any,
    end: Any,
    *,
    mean: float | None = None,
    sites: int | None = None,
    decimals: int | None = None,
) -> str:
    """Dates with an optional finite-cell area mean and matched site count."""
    parts = [subtitle_text(start, end)]
    if mean is not None:
        if decimals is not None and decimals < 0:
            raise ValueError("mean decimals must be nonnegative")
        value = f"{mean:.6g}" if decimals is None else f"{mean:.{decimals}f}"
        parts.append(f"Mean: {value}" if math.isfinite(mean) else "Mean: no finite values")
    if sites is not None:
        if sites < 0:
            raise ValueError("site count must be nonnegative")
        parts.append(f"Sites: {sites}")
    return " | ".join(part for part in parts if part)


def active_sites_subtitle(
    start: Any,
    end: Any,
    minimum: int,
    maximum: int,
    *,
    extent: tuple[float, float, float, float] | None = None,
) -> str:
    """Region latitude/longitude bounds, dates and active matched sites."""
    if minimum < 0 or maximum < minimum:
        raise ValueError("active site counts must be nonnegative and ordered")
    counts = str(minimum) if minimum == maximum else f"{minimum}–{maximum}"
    detail = " | ".join(
        part for part in (subtitle_text(start, end), f"Active Sites: {counts}") if part
    )
    if extent is None:
        return detail

    def coordinate(value: float, negative: str, positive: str) -> str:
        hemisphere = negative if value < 0 else positive if value > 0 else ""
        return f"{abs(value):g}°{hemisphere}"

    west, east, south, north = extent
    latitude = f"{coordinate(south, 'S', 'N')}–{coordinate(north, 'S', 'N')}"
    longitude = f"{coordinate(west, 'W', 'E')}–{coordinate(east, 'W', 'E')}"
    return f"{latitude} {longitude} | {detail}"


def regression_label(method: str, slope: float, intercept: float, *, decimals: int = 2) -> str:
    """Publication legend for a fit, naming coefficients without x/y equations."""
    method = method.upper()
    if method not in {"OLS", "RMA"}:
        raise ValueError("regression method must be OLS or RMA")
    if decimals < 0:
        raise ValueError("regression decimals must be nonnegative")
    return f"{method}: slope {slope:.{decimals}f}, intercept {intercept:.{decimals}f}"


def reference_label(kind: str) -> str:
    """Labels for standard plot reference lines and sampling roles."""
    return {
        "one_to_one": "1:1",
        "expected_error": "Expected Error",
        "unit_normal": "Unit Normal",
        "all_points": "All Points",
        "window_means": "Window Means",
        "grid": "Grid",
        "overpasses": "Overpass Means",
        "no_data": "No Finite Pairs",
        "matched_sites": "Matched Sites",
        "full_region": "Full Region",
    }[kind]


def series_legend_label(source: str, role: str, *, band_drawn: bool = False) -> str:
    label = f"{source_display_name(source)} {reference_label(role)}"
    return f"{label} (mean ± σ)" if band_drawn else label


def metric_label(name: str) -> str:
    return {
        "N": "N",
        "N_z": "N",
        "MB": "MB",
        "RMSE": "RMSE",
        "R": "R",
        "pct_ee": "%EE",
        "z_mean": "Mean Residual",
        "z_std": "Residual Standard Deviation",
        "STDX": "Reference Standard Deviation",
        "STDY": "Satellite Standard Deviation",
    }.get(name, source_display_name(name))


def number_label(value: float, *, count: bool = False, decimals: int = 3) -> str:
    if not math.isfinite(value):
        return "—"
    return f"{int(value):,}" if count else f"{value:.{decimals}f}"


def statistics_text(values: dict[str, float]) -> str:
    """Format computed values only at display time; callers retain original metrics."""
    return "\n".join(
        f"{metric_label(name)} = {number_label(value, count=name in {'N', 'N_z'}, decimals=1 if name == 'pct_ee' else 3)}"
        for name, value in values.items()
    )


def regional_statistics_text(values: dict[str, float]) -> str:
    """Statistics for the matched daily regional means actually plotted."""
    return " | ".join(
        (
            f"Daily Means: {number_label(values['N'], count=True)} Days",
            f"Bias: {number_label(values['MB'])}",
            f"RMSE: {number_label(values['RMSE'])}",
            f"R: {number_label(values['R'])}",
            f"Absolute Error: {number_label(values['NME'], decimals=1)}%",
        )
    )


def regional_spread_note() -> str:
    """Define site variability separately from uncertainty or error bars."""
    return (
        "Lines: Equal-Site Daily Means | Bands: Middle 50% of Sites | "
        "Absolute Error: Mean Absolute Difference / Mean AERONET × 100"
    )


def region_label(region: str) -> str:
    from sarb_aod.evaluation.regions import get_region

    try:
        return get_region(region).display_name
    except KeyError:
        return source_display_name(region)


__all__ = [
    "format_units",
    "source_display_name",
    "quantity_label",
    "axis_label",
    "distinctive_source_name",
    "legend_label",
    "bias_label",
    "title_text",
    "sourced_title",
    "subtitle_text",
    "map_subtitle",
    "regression_label",
    "title_for_labeled_subset",
    "title_violations",
    "validate_title",
    "reference_label",
    "series_legend_label",
    "metric_label",
    "number_label",
    "statistics_text",
    "regional_statistics_text",
    "regional_spread_note",
    "region_label",
]
