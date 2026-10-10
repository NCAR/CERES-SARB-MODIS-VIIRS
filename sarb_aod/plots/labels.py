# Ported from NCAR/DAVINCI origin/develop, davinci_monet/plots/labels.py (Apache-2.0).
"""Text formatting functions and lookup tables for DAVINCI plots.

Provides:
- Lookup tables for variable display names, title formula replacements, and unit strings
- format_plot_title: apply chemical formula subscripts to a title string
- format_variable_display_name: friendly name from a raw variable identifier
- get_variable_label: best display label from a dataset variable
- canonical_variable_name: strip source-label / x/y prefix from a variable name
- get_variable_units: read units attr from a dataset variable
- format_units: rewrite raw unit strings to LaTeX form
- format_label_with_units: combine a label and its units
- calculate_symmetric_limits / calculate_data_limits: percentile-based axis scaling
- merge_config_dicts: shallow-merge two config dicts
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    import xarray as xr


def canonical_variable_name(dataset: "xr.Dataset", var_name: str) -> str:
    """Strip paired source/axis prefixes (ported from DAVINCI core/base.py)."""
    if var_name in dataset.data_vars:
        canonical = dataset[var_name].attrs.get("canonical_name")
        if canonical:
            return str(canonical)
        source_label = dataset[var_name].attrs.get("source_label")
        if source_label and var_name.startswith(f"{source_label}_"):
            return var_name[len(source_label) + 1 :]
    for prefix in ("x_", "y_"):
        if str(var_name).lower().startswith(prefix):
            return var_name[len(prefix) :]
    return var_name


# =============================================================================
# Lookup Tables
# =============================================================================

# Lookup table for common atmospheric variable display names
# Maps lowercase variable names (or patterns) to display names with proper formatting
# Whole words kept uppercase in auto-formatted display names (see
# format_variable_display_name): title-casing otherwise mangles them.
_DISPLAY_ACRONYMS = {
    "toa",
    "sfc",
    "lw",
    "sw",
    "wn",
    "olr",
    "uv",
    "tc",
    "adm",
    "aod",
    "modis",
    "viirs",
    "snpp",
    "noaa-20",
    "aeronet",
    "cam",
    "qc",
    "gls",
    "ols",
    "rma",
    "rmse",
    "us",
}

# Check complete keys before token-wise formatting; splitting e.g. noaa20_db
# or retrieval_team would otherwise expose implementation vocabulary.
DISPLAY_NAMES: dict[str, str] = {
    "count": "Count-Weighted",
    "gls": "GLS",
    "retrieval_team": "Retrieval-Team QC",
    "cam7_v2": "CAM7 QC",
    "c61": "C6.1",
    "6.1": "C6.1",
    "061": "C6.1",
    "c7": "C7",
    "7": "C7",
    "007": "C7",
    "deep_blue_v2": "Deep Blue v2",
    "deep_blue_v21": "Deep Blue v2.1",
    "deep_blue_v2.1": "Deep Blue v2.1",
    "dark_target_v21": "Dark Target v2.1",
    "dark_target_v2.1": "Dark Target v2.1",
    "modis": "MODIS",
    "viirs": "VIIRS",
    "mixed": "MODIS + VIIRS",
    "terra_dt": "Terra Dark Target",
    "terra_db": "Terra Deep Blue",
    "aqua_dt": "Aqua Dark Target",
    "aqua_db": "Aqua Deep Blue",
    "snpp_dt": "SNPP Dark Target",
    "snpp_db": "SNPP Deep Blue",
    "noaa20_dt": "NOAA-20 Dark Target",
    "noaa20_db": "NOAA-20 Deep Blue",
    "modis_dt": "MODIS Dark Target",
    "modis_db": "MODIS Deep Blue",
    "viirs_dt": "VIIRS Dark Target",
    "viirs_db": "VIIRS Deep Blue",
    "mod04_l2": "Terra MODIS",
    "myd04_l2": "Aqua MODIS",
    "aerdb_l2_viirs_snpp": "SNPP Deep Blue",
    "aerdb_l2_viirs_noaa20": "NOAA-20 Deep Blue",
    "aerdt_l2_viirs_snpp": "SNPP Dark Target",
    "aerdt_l2_viirs_noaa20": "NOAA-20 Dark Target",
    "dt": "Dark Target",
    "db": "Deep Blue",
    "dark_target": "Dark Target",
    "deep_blue": "Deep Blue",
    "rho": "Error Correlation",
    "min_pixels": "Minimum Pixel Count",
    "sigma_floor": "Uncertainty Floor",
    "aod_min": "Minimum AOD",
    "aod_max": "Maximum AOD",
}

AOD_DISPLAY_NAMES: dict[str, str] = {
    "aod_weighted_mean": "AOD",
    "aod_weighted_mean_modis": "AOD",
    "aod_weighted_mean_viirs": "AOD",
    "aod_uncertainty": "AOD Uncertainty",
    "aod_count": "Pixel Count",
    "aod_count_negative": "Negative AOD Count",
    "aod_count_sigma_missing": "Missing Uncertainty Count",
    "aod_count_rejected_high": "High-AOD Rejection Count",
    "aod_count_clipped_negative": "Clipped Negative AOD Count",
    "aod_negative_fraction": "Negative AOD Fraction",
    "negative_aod_fraction": "Negative AOD Fraction",
    "coverage": "AOD Coverage",
    "aod_coverage": "AOD Coverage",
    "aod_chi2_dof": "Reduced χ²",
    "chi2_dof": "Reduced χ²",
}

VARIABLE_DISPLAY_NAMES: dict[str, str] = {
    **AOD_DISPLAY_NAMES,
    # Surface pollutants (using LaTeX math mode for subscripts)
    "pm25": r"PM$_{2.5}$",
    "pm2.5": r"PM$_{2.5}$",
    "pm10": r"PM$_{10}$",
    "o3": r"O$_3$",
    "ozone": r"O$_3$",
    "no2": r"NO$_2$",
    "no": "NO",
    "nox": r"NO$_x$",
    "co": "CO",
    "co2": r"CO$_2$",
    "so2": r"SO$_2$",
    "hcho": "HCHO",
    "ch2o": "CH$_2$O",
    "nh3": r"NH$_3$",
    "hno3": r"HNO$_3$",
    "n2o": r"N$_2$O",
    "n2o5": r"N$_2$O$_5$",
    "ch4": r"CH$_4$",
    # AOD variables
    "aod": "AOD",
    "aod_500nm": "AOD (500 nm)",
    "aod_550nm": "AOD (550 nm)",
    "aod_440nm": "AOD (440 nm)",
    "aodvisdn": "AOD",
    # Column variables
    "no2_trop_column": r"Tropospheric NO$_2$ Column",
    "no2_column": r"NO$_2$ Column",
    "o3_column": r"O$_3$ Column",
    "trop_no2": r"Tropospheric NO$_2$",
    # Dataset variables (uppercase)
    "PM25": r"PM$_{2.5}$",
    "O3": r"O$_3$",
    "NO2": r"NO$_2$",
    "CO": "CO",
    "SO2": r"SO$_2$",
    "AODVISdn": "AOD",
    "NO2_column": r"NO$_2$ Column",
    # ASIA-AQ DC-8 aircraft variables (ICARTT naming convention)
    "O3_ROZE_STCLAIR": r"O$_3$",
    "NO2_CANOE_STCLAIR": r"NO$_2$",
    "CO_DACOM_DISKIN": "CO",
}

# ---------------------------------------------------------------------------
# Species word/phrase → formula map (used by quantity_label normalization).
# Longer phrases come first so "nitrogen dioxide" is matched before "nitrogen".
# Values are publication-ready LaTeX formula strings.
# ---------------------------------------------------------------------------
SPECIES_WORD_TO_FORMULA: list[tuple[str, str]] = [
    # multi-word phrases first (longest first within each chemical)
    ("nitrogen dioxide", r"NO$_2$"),
    ("nitrogen oxides", r"NO$_x$"),
    ("nitrous oxide", r"N$_2$O"),
    ("nitric oxide", "NO"),
    ("carbon monoxide", "CO"),
    ("carbon dioxide", r"CO$_2$"),
    ("sulfur dioxide", r"SO$_2$"),
    ("formaldehyde", "HCHO"),
    ("methane", r"CH$_4$"),
    ("ammonia", r"NH$_3$"),
    ("ozone", r"O$_3$"),
]

# Tokens that must stay lowercase in smart title-case (strip surrounding
# punctuation before checking).  Two categories:
#   1. unit abbreviations that would be wrong capitalised (nm → Nm, ppb → Ppb)
#   2. short particles / prepositions (of, at, …)
_QUANTITY_LOWERCASE_KEEP: frozenset[str] = frozenset(
    {
        # unit abbreviations
        "nm",
        "um",
        "µm",
        "mm",
        "cm",
        "m",
        "km",
        "s",
        "ms",
        "hr",
        "h",
        "ppb",
        "ppbv",
        "ppm",
        "ppmv",
        "ppt",
        "pptv",
        "mol",
        "kg",
        "g",
        "mg",
        "ug",
        "w",
        "pa",
        "hpa",
        "k",
        # particles
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "at",
        "by",
        "in",
        "on",
        "to",
        "per",
        "vs",
    }
)

# Patterns for title formatting (case-insensitive replacements)
# Order matters - longer patterns first to avoid partial matches
# Uses LaTeX math mode subscripts for matplotlib rendering
TITLE_FORMULA_REPLACEMENTS: list[tuple[str, str]] = [
    # Longer patterns first
    ("PM2.5", r"PM$_{2.5}$"),
    ("PM25", r"PM$_{2.5}$"),
    ("PM10", r"PM$_{10}$"),
    ("N2O5", r"N$_2$O$_5$"),
    ("HNO3", r"HNO$_3$"),
    ("N2O", r"N$_2$O"),
    ("NO2", r"NO$_2$"),
    ("SO2", r"SO$_2$"),
    ("CO2", r"CO$_2$"),
    ("NH3", r"NH$_3$"),
    ("CH4", r"CH$_4$"),
    ("CH2O", r"CH$_2$O"),
    ("NOx", r"NO$_x$"),
    ("NOX", r"NO$_x$"),
    ("O3", r"O$_3$"),
]


# Unit string replacements for plot labels. Longer/more-specific patterns
# first so e.g. "ug/m3" doesn't get partially rewritten by a bare "m3" rule.
UNIT_REPLACEMENTS: list[tuple[str, str]] = [
    ("ug/m3", r"$\mu$g/m$^3$"),
    ("ug m-3", r"$\mu$g m$^{-3}$"),
    ("ug m^-3", r"$\mu$g m$^{-3}$"),
    ("mg/m3", r"mg/m$^3$"),
    ("mg m-3", r"mg m$^{-3}$"),
    ("kg/m3", r"kg/m$^3$"),
    ("kg m-3", r"kg m$^{-3}$"),
    ("g/m3", r"g/m$^3$"),
    ("m/s2", r"m/s$^2$"),
    ("m s-2", r"m s$^{-2}$"),
    ("m/s", "m/s"),
    ("W/m2", r"W/m$^2$"),
    ("W m-2", r"W m$^{-2}$"),
]


# =============================================================================
# Formatting Functions
# =============================================================================


def format_plot_title(title: str) -> str:
    """Format a plot title with proper chemical formula subscripts.

    Replaces common chemical formulas (NO2, O3, PM2.5, etc.) with
    LaTeX subscript versions for matplotlib rendering.

    Parameters
    ----------
    title
        Raw title string.

    Returns
    -------
    str
        Title with chemical formulas properly formatted with LaTeX.

    Examples
    --------
    >>> format_plot_title("PM2.5 Y vs X")
    'PM$_{2.5}$ Y vs X'
    >>> format_plot_title("NO2 Time Series")
    'NO$_2$ Time Series'
    """
    import re

    # Species words/phrases -> formula first (e.g. "Ozone" -> "O$_3$"), so titles
    # and any other text routed through here are consistent with the lookup
    # table. Only plain-text segments are touched (never inside an existing
    # ``$...$`` LaTeX block); whole-word, longest-phrase-first, case-insensitive.
    segments = re.split(r"(\$[^$]*\$)", title)
    for i in range(0, len(segments), 2):
        seg = segments[i]
        for phrase, formula in SPECIES_WORD_TO_FORMULA:
            seg = re.sub(
                r"(?<![A-Za-z])" + re.escape(phrase) + r"(?![A-Za-z])",
                formula,
                seg,
                flags=re.IGNORECASE,
            )
        segments[i] = seg
    result = "".join(segments)

    for pattern, replacement in TITLE_FORMULA_REPLACEMENTS:
        # Case-insensitive replacement while preserving surrounding text
        result = re.sub(re.escape(pattern), replacement, result, flags=re.IGNORECASE)
    return result


def format_variable_display_name(var_name: str, include_prefix: bool = True) -> str:
    """Format a variable name for display.

    Uses lookup table for known variables, otherwise applies
    basic formatting (replace underscores, title case).

    Parameters
    ----------
    var_name
        Raw variable name.
    include_prefix
        Prefixes are not rendered in labels.

    Returns
    -------
    str
        Formatted display name.
    """
    # Strip x/y prefixes for lookup.
    base_name = var_name
    if var_name.startswith("x_"):
        base_name = var_name[len("x_") :]
    elif var_name.startswith("y_"):
        base_name = var_name[len("y_") :]

    # Check lookup table (try exact match first, then lowercase)
    if base_name in VARIABLE_DISPLAY_NAMES:
        return VARIABLE_DISPLAY_NAMES[base_name]
    if base_name.lower() in VARIABLE_DISPLAY_NAMES:
        return VARIABLE_DISPLAY_NAMES[base_name.lower()]

    # Basic formatting: replace underscores, apply title case
    formatted = base_name.replace("_", " ")
    if formatted.islower() or formatted.isupper():
        formatted = formatted.title()
    # Restore acronyms that title-casing mangles ("Toa Lw Up" -> "TOA LW Up").
    formatted = " ".join(
        word.upper() if word.lower() in _DISPLAY_ACRONYMS else word for word in formatted.split(" ")
    )

    return formatted


def get_variable_label(
    dataset: xr.Dataset,
    var_name: str,
    custom_label: str | None = None,
    include_prefix: bool = True,
) -> str:
    """Get a display label for a variable.

    Uses custom label if provided, then checks dataset attributes
    (display_name, long_name, standard_name), then falls back to
    automatic formatting via lookup table.

    Parameters
    ----------
    dataset
        Dataset containing the variable.
    var_name
        Variable name.
    custom_label
        Custom label to use (overrides all other sources).
    include_prefix
        Prefixes are not rendered in labels.

    Returns
    -------
    str
        Display label for the variable.
    """
    if custom_label:
        return custom_label

    if var_name in dataset:
        attrs = dataset[var_name].attrs
        # Check for display_name first (our custom attribute)
        if attrs.get("display_name"):
            return str(attrs["display_name"])
        if attrs.get("long_name"):
            return str(attrs["long_name"])
        if attrs.get("standard_name"):
            return str(attrs["standard_name"])
        # Pair-axis metadata drives pairing and styling only; labels should name
        # the quantity.
        axis = attrs.get("axis")
        if axis in ("x", "y"):
            var_name = canonical_variable_name(dataset, var_name)

    # Fall back to automatic formatting
    return format_variable_display_name(var_name, include_prefix=include_prefix)


def get_variable_units(
    dataset: xr.Dataset,
    var_name: str,
) -> str | None:
    """Get units for a variable.

    Parameters
    ----------
    dataset
        Dataset containing the variable.
    var_name
        Variable name.

    Returns
    -------
    str | None
        Units string, or None if not found.
    """
    if var_name in dataset:
        return dataset[var_name].attrs.get("units")
    return None


def format_units(units: str) -> str:
    """Rewrite raw unit strings to negative-exponent SI LaTeX form.

    Delegates to :func:`sarb_aod.plots.labeling.format_units` so that
    all callers get the canonical SI rendering (e.g. ``"mol/m2"`` →
    ``"mol m$^{-2}$"``).
    """
    from sarb_aod.plots.labeling import format_units as _si

    return _si(units)


def format_label_with_units(label: str, units: str | None) -> str:
    """Format a label with units.

    Parameters
    ----------
    label
        Base label.
    units
        Units string (can be None). Dimensionless units ("1") are omitted.

    Returns
    -------
    str
        Formatted label with units in parentheses if provided. The units
        string is passed through :func:`format_units` so common bare-ASCII
        forms (``ug/m3``, ``W m-2``, ...) render with proper LaTeX symbols.
    """
    formatted = format_units(units) if units else ""
    return f"{label} ({formatted})" if formatted else label


def calculate_symmetric_limits(
    data: np.ndarray,
    percentile: float = 98,
) -> tuple[float, float]:
    """Calculate symmetric limits around zero for bias plots.

    Parameters
    ----------
    data
        Data array.
    percentile
        Percentile to use for limit calculation.

    Returns
    -------
    tuple[float, float]
        Symmetric (vmin, vmax) limits.
    """
    data = np.asarray(data).flatten()
    data = data[np.isfinite(data)]
    if len(data) == 0:
        return (-1.0, 1.0)

    abs_max = np.percentile(np.abs(data), percentile)
    if not np.isfinite(abs_max) or abs_max == 0:
        return (-1.0, 1.0)
    return (-abs_max, abs_max)


def calculate_data_limits(
    data: np.ndarray,
    percentile: float = 98,
    symmetric: bool = False,
) -> tuple[float, float]:
    """Calculate data limits for colorbar/axis scaling.

    Parameters
    ----------
    data
        Data array.
    percentile
        Percentile to use for limit calculation.
    symmetric
        If True, make limits symmetric around zero.

    Returns
    -------
    tuple[float, float]
        (vmin, vmax) limits.
    """
    if symmetric:
        return calculate_symmetric_limits(data, percentile)

    data = np.asarray(data).flatten()
    data = data[np.isfinite(data)]
    if len(data) == 0:
        return (0.0, 1.0)

    vmin = np.percentile(data, 100 - percentile)
    vmax = np.percentile(data, percentile)
    return (vmin, vmax)


def merge_config_dicts(
    defaults: dict[str, Any],
    overrides: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge configuration dictionaries with defaults.

    Parameters
    ----------
    defaults
        Default configuration values.
    overrides
        Override values (can be None).

    Returns
    -------
    dict[str, Any]
        Merged configuration.
    """
    if overrides is None:
        return defaults.copy()
    return {**defaults, **overrides}


__all__ = [
    "DISPLAY_NAMES",
    "AOD_DISPLAY_NAMES",
    "VARIABLE_DISPLAY_NAMES",
    "TITLE_FORMULA_REPLACEMENTS",
    "SPECIES_WORD_TO_FORMULA",
    "_QUANTITY_LOWERCASE_KEEP",
    "UNIT_REPLACEMENTS",
    "format_plot_title",
    "format_variable_display_name",
    "canonical_variable_name",
    "get_variable_label",
    "get_variable_units",
    "format_units",
    "format_label_with_units",
    "calculate_symmetric_limits",
    "calculate_data_limits",
    "merge_config_dicts",
]
