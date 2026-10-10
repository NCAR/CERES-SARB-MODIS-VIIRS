"""Unrounded paired statistics, grouped tables and equal-site regional series.

Adapted from NCAR/DAVINCI ``origin/develop:davinci_monet/stats/calculator.py``
(Apache-2.0), retaining its metric convention and minimum-sample behavior.
The dataframe grouping also accepts xarray datasets. Rounding is explicitly
deferred to exports so scientific gates never use rounded results.

The two-stage regional aggregation follows NCAR/CERES-SARB-CAM7
``scripts/summarize_2008_regional_aeronet_band_comparison.py``: matched pairs
within site-days, then equally weighted active sites within each region-day.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr
from numpy.typing import ArrayLike

from .metrics import (
    STANDARD_METRICS,
    EEEnvelope,
    _paired_values,
    expected_error,
    get_metric,
    regression,
    uncertainty_calibration,
)

MONTH_TO_METEOROLOGICAL_SEASON = {
    12: "DJF",
    1: "DJF",
    2: "DJF",
    3: "MAM",
    4: "MAM",
    5: "MAM",
    6: "JJA",
    7: "JJA",
    8: "JJA",
    9: "SON",
    10: "SON",
    11: "SON",
}
DEFAULT_AOD_BINS = (0.0, 0.1, 0.2, 0.5, 1.0, 2.0, np.inf)


@dataclass
class StatisticsConfig:
    metrics: list[str] = field(default_factory=lambda: list(STANDARD_METRICS))
    round_precision: int = 4
    include_counts: bool = True
    min_samples: int = 3

    def __post_init__(self) -> None:
        if (
            isinstance(self.min_samples, bool)
            or not isinstance(self.min_samples, int)
            or self.min_samples < 1
        ):
            raise ValueError("min_samples must be a positive integer")
        if (
            isinstance(self.round_precision, bool)
            or not isinstance(self.round_precision, int)
            or self.round_precision < 0
        ):
            raise ValueError("round_precision must be a nonnegative integer")
        for name in self.metrics:
            get_metric(name)

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> StatisticsConfig:
        return cls(**values)


def _frame(data: pd.DataFrame | xr.Dataset) -> pd.DataFrame:
    if isinstance(data, pd.DataFrame):
        return data.copy()
    if isinstance(data, xr.Dataset):
        return data.to_dataframe().reset_index()
    raise TypeError("paired data must be a pandas DataFrame or xarray Dataset")


def _group_names(groupby: str | Sequence[str] | None) -> list[str]:
    names = [] if groupby is None else [groupby] if isinstance(groupby, str) else list(groupby)
    if len(set(names)) != len(names):
        raise ValueError("grouping columns must be unique")
    return names


def _bin_edges(edges: ArrayLike) -> np.ndarray:
    values = np.asarray(edges, dtype=float)
    if (
        values.ndim != 1
        or values.size < 2
        or np.isnan(values).any()
        or np.any(values[1:] <= values[:-1])
    ):
        raise ValueError("AOD bin edges must be a strictly increasing one-dimensional array")
    return values


def _group_columns(frame: pd.DataFrame, names: list[str], x_var: str, aod_bins: ArrayLike) -> None:
    for name in names:
        if name in frame:
            continue
        if name == "aod_bin":
            edges = _bin_edges(aod_bins)
            reference = frame[x_var].to_numpy(dtype=float)
            index = np.searchsorted(edges, reference, side="right") - 1
            index[reference == edges[-1]] = len(edges) - 2
            valid = np.isfinite(reference) & (index >= 0) & (index < len(edges) - 1)
            frame[name] = np.where(valid, index, np.nan)
            continue
        accessor = name.removeprefix("time.")
        if accessor not in ("day", "season", "month", "hour", "dayofweek") or "time" not in frame:
            raise KeyError(f"No grouping column {name!r} in paired data")
        times = pd.to_datetime(frame["time"], utc=True, errors="coerce")
        if accessor == "day":
            frame[name] = times.dt.floor("D")
        elif accessor == "season":
            frame[name] = times.dt.month.map(MONTH_TO_METEOROLOGICAL_SEASON)
        else:
            frame[name] = getattr(times.dt, accessor)


class StatisticsCalculator:
    """Core methods always return unrounded metrics; export methods round."""

    def __init__(self, config: StatisticsConfig | None = None) -> None:
        self.config = config or StatisticsConfig()

    def _compute_metrics(
        self, x: ArrayLike, y: ArrayLike, metrics: Sequence[str]
    ) -> dict[str, float]:
        left, right = _paired_values(x, y)
        valid = np.isfinite(left) & np.isfinite(right)
        left, right = left[valid], right[valid]
        names = list(metrics)
        if self.config.include_counts and "N" not in names:
            names.insert(0, "N")
        result = {}
        for name in names:
            metric = get_metric(name)
            result[name] = (
                float(left.size)
                if name == "N"
                else np.nan if left.size < self.config.min_samples else metric.compute(left, right)
            )
        return result

    def compute(
        self,
        paired_data: pd.DataFrame | xr.Dataset,
        x_var: str = "x",
        y_var: str = "y",
        *,
        metrics: Sequence[str] | None = None,
        groupby: str | Sequence[str] | None = None,
        aod_bins: ArrayLike = DEFAULT_AOD_BINS,
        ee: str | EEEnvelope | None = None,
        sigma_sat: str | float | None = None,
        sigma_aer: str | float | None = None,
        include_regression: bool = False,
    ) -> pd.DataFrame:
        """Pooled, equally weighted matchup metrics within arbitrary columns.

        Group configuration/sensor/stream/collection/surface/region/site columns
        directly. UTC day/season and ``time.month/hour/dayofweek`` are derived
        from ``time`` when absent; ``aod_bin`` uses integer bin IDs based on x.
        Sigma arguments are column names or constants; both must be supplied
        together. Missing group labels remain explicit groups instead of being
        silently discarded. EE is opt-in because mixed retrieval families need
        an explicitly chosen envelope.
        """
        frame = _frame(paired_data)
        if x_var not in frame or y_var not in frame:
            raise KeyError(f"paired data must contain {x_var!r} and {y_var!r}")
        names = list(self.config.metrics if metrics is None else metrics)
        for name in names:
            get_metric(name)
        groups = _group_names(groupby)
        _group_columns(frame, groups, x_var, aod_bins)
        if (sigma_sat is None) != (sigma_aer is None):
            raise ValueError("both sigma_sat and sigma_aer must be supplied")
        for value in (sigma_sat, sigma_aer):
            if isinstance(value, str) and value not in frame:
                raise KeyError(f"No uncertainty column {value!r}")
        grouped = (
            frame.groupby(groups, dropna=False, observed=True, sort=True)
            if groups
            else [((), frame)]
        )
        rows: list[dict[str, Any]] = []
        for key, subset in grouped:
            keys = key if isinstance(key, tuple) else (key,)
            row = dict(zip(groups, keys, strict=True))
            left, right = subset[x_var].to_numpy(dtype=float), subset[y_var].to_numpy(dtype=float)
            valid_count = np.count_nonzero(np.isfinite(left) & np.isfinite(right))
            row.update(self._compute_metrics(left, right, names))
            if include_regression:
                fit = regression(left, right)
                row.update(
                    fit
                    if valid_count >= self.config.min_samples
                    else {name: np.nan for name in fit}
                )
            if ee is not None:
                errors = expected_error(left, right, ee)
                row.update(self._minimum_for_diagnostic(errors, "N_EE"))
            if sigma_sat is not None and sigma_aer is not None:
                satellite = (
                    subset[sigma_sat].to_numpy(dtype=float)
                    if isinstance(sigma_sat, str)
                    else sigma_sat
                )
                aeronet = (
                    subset[sigma_aer].to_numpy(dtype=float)
                    if isinstance(sigma_aer, str)
                    else sigma_aer
                )
                calibration = uncertainty_calibration(left, right, satellite, aeronet)
                row.update(self._minimum_for_diagnostic(calibration, "N_z"))
            rows.append(row)
        if rows:
            return pd.DataFrame(rows)
        # Preserve a useful schema for empty grouped inputs, including optional
        # diagnostics, using a single empty overall calculation.
        columns = self.compute(
            frame,
            x_var,
            y_var,
            metrics=names,
            ee=ee,
            sigma_sat=sigma_sat,
            sigma_aer=sigma_aer,
            include_regression=include_regression,
        ).columns
        return pd.DataFrame(columns=[*groups, *columns])

    def _minimum_for_diagnostic(
        self, values: dict[str, float], count_name: str
    ) -> dict[str, float]:
        if values[count_name] >= self.config.min_samples:
            return values
        return {key: value if key == count_name else np.nan for key, value in values.items()}

    def compute_summary(
        self,
        paired_data: pd.DataFrame | xr.Dataset,
        x_var: str = "x",
        y_var: str = "y",
        **kwargs: Any,
    ) -> dict[str, float]:
        return self.compute(paired_data, x_var, y_var, **kwargs).iloc[0].to_dict()

    def rounded_table(self, statistics: pd.DataFrame) -> pd.DataFrame:
        return rounded_table(statistics, self.config.round_precision)


def calculate_statistics(
    paired_data: pd.DataFrame | xr.Dataset,
    x_var: str = "x",
    y_var: str = "y",
    *,
    config: StatisticsConfig | dict[str, Any] | None = None,
    **kwargs: Any,
) -> pd.DataFrame:
    if isinstance(config, dict):
        config = StatisticsConfig.from_dict(config)
    return StatisticsCalculator(config).compute(paired_data, x_var, y_var, **kwargs)


def quick_stats(
    x: ArrayLike, y: ArrayLike, metrics: Sequence[str] | None = None, min_samples: int = 3
) -> dict[str, float]:
    config = StatisticsConfig(
        metrics=list(STANDARD_METRICS if metrics is None else metrics), min_samples=min_samples
    )
    return StatisticsCalculator(config)._compute_metrics(x, y, config.metrics)


def rounded_table(statistics: pd.DataFrame, precision: int = 4) -> pd.DataFrame:
    """Return a rounded presentation copy, leaving the gate values untouched."""
    rounded = statistics.copy()
    numeric = rounded.select_dtypes(include="number").columns
    rounded[numeric] = rounded[numeric].round(precision)
    return rounded


def write_statistics_csv(statistics: pd.DataFrame, path: str | Path, precision: int = 4) -> Path:
    """Write rounded tabular values; in-memory statistics remain unrounded."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rounded_table(statistics, precision).to_csv(
        target, index=False, float_format=f"%.{precision}f", na_rep="NA"
    )
    return target


def binned_bias(
    x: ArrayLike, y: ArrayLike, edges: ArrayLike = DEFAULT_AOD_BINS, *, min_samples: int = 3
) -> pd.DataFrame:
    """Bias by reference AOD: half-open bins, with the final upper edge included."""
    left, right = _paired_values(x, y)
    bounds = _bin_edges(edges)
    rows = []
    for index, (lower, upper) in enumerate(zip(bounds[:-1], bounds[1:], strict=True)):
        selected = (left >= lower) & (
            (left <= upper) if index == len(bounds) - 2 else (left < upper)
        )
        rows.append(
            {
                "aod_bin": index,
                "aod_lower": lower,
                "aod_upper": upper,
                **quick_stats(
                    left[selected], right[selected], ["N", "MX", "MY", "MB", "RMSE"], min_samples
                ),
            }
        )
    return pd.DataFrame(rows)


def regional_daily_series(
    paired_data: pd.DataFrame | xr.Dataset,
    *,
    x_var: str = "x",
    y_var: str = "y",
    time_var: str = "time",
    site_var: str = "site",
    region_var: str = "region",
    groupby: str | Sequence[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return site-day means and equal-site region-day means with sample counts.

    Pass configuration/stream keys in groupby so independent products remain
    separate. Only paired-finite rows with a UTC date, site and region enter the
    averages. Overlapping regions may have duplicate matchups in the input;
    pooled global statistics must use the original unduplicated matchups.
    """
    frame = _frame(paired_data)
    contexts = _group_names(groupby)
    if region_var in contexts:
        contexts.remove(region_var)
    if site_var in contexts or "day" in contexts:
        raise ValueError("regional context grouping must not include site or day")
    required = [x_var, y_var, time_var, site_var, region_var, *contexts]
    missing = [name for name in required if name not in frame]
    if missing:
        raise KeyError(f"Missing regional columns: {missing}")
    frame["day"] = pd.to_datetime(frame[time_var], utc=True, errors="coerce").dt.floor("D")
    finite = np.isfinite(frame[x_var].to_numpy(dtype=float)) & np.isfinite(
        frame[y_var].to_numpy(dtype=float)
    )
    frame = frame.loc[finite & frame[["day", site_var, region_var]].notna().all(axis=1)]
    site_keys = [*contexts, region_var, "day", site_var]
    daily = (
        frame.groupby(site_keys, dropna=False, observed=True, sort=True)
        .agg(
            **{
                x_var: (x_var, "mean"),
                y_var: (y_var, "mean"),
                "N_pairs": (x_var, "size"),
            }
        )
        .reset_index()
    )
    region_keys = [*contexts, region_var, "day"]
    regional = (
        daily.groupby(region_keys, dropna=False, observed=True, sort=True)
        .agg(
            **{
                x_var: (x_var, "mean"),
                y_var: (y_var, "mean"),
                "active_sites": (site_var, "size"),
                "N_pairs": ("N_pairs", "sum"),
            }
        )
        .reset_index()
    )
    return daily, regional


def regional_statistics(
    paired_data: pd.DataFrame | xr.Dataset,
    *,
    x_var: str = "x",
    y_var: str = "y",
    time_var: str = "time",
    site_var: str = "site",
    region_var: str = "region",
    groupby: str | Sequence[str] | None = None,
    config: StatisticsConfig | None = None,
) -> pd.DataFrame:
    """Metrics over equally weighted region-days, plus site-day/pair counts."""
    contexts = _group_names(groupby)
    if region_var not in contexts:
        contexts.append(region_var)
    _, regional = regional_daily_series(
        paired_data,
        x_var=x_var,
        y_var=y_var,
        time_var=time_var,
        site_var=site_var,
        region_var=region_var,
        groupby=contexts,
    )
    statistics = StatisticsCalculator(config).compute(regional, x_var, y_var, groupby=contexts)
    counts = (
        regional.groupby(contexts, dropna=False, observed=True, sort=True)
        .agg(
            N_region_days=("day", "size"),
            N_site_days=("active_sites", "sum"),
            N_pairs=("N_pairs", "sum"),
            mean_active_sites=("active_sites", "mean"),
        )
        .reset_index()
    )
    result = statistics.merge(counts, on=contexts, how="left", validate="one_to_one")
    if "N" not in result:
        result["N"] = result["N_region_days"]
    return result
