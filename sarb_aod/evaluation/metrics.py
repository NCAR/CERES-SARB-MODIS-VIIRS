"""Statistical metrics for paired source comparison.

Ported from NCAR/DAVINCI ``origin/develop:davinci_monet/stats/metrics.py``
(Apache-2.0). The 27 metric definitions are unchanged; registration is local.
Aerosol-specific metrics below the port are new.

This module provides individual metric implementations for comparing a
``y`` source against an ``x`` source.

All metrics follow the convention:
- x: reference values
- y: comparison values
- Positive bias means y > x
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

import numpy as np
from numpy.typing import ArrayLike

if TYPE_CHECKING:
    import xarray as xr

METRIC_REGISTRY: dict[str, type[BaseMetric]] = {}


def _register(name: str) -> Callable[[type[BaseMetric]], type[BaseMetric]]:
    def register(metric_class: type[BaseMetric]) -> type[BaseMetric]:
        METRIC_REGISTRY[name] = metric_class
        return metric_class

    return register


# =============================================================================
# Base Classes
# =============================================================================


@dataclass
class MetricResult:
    """Result container for a calculated metric.

    Attributes
    ----------
    name : str
        Metric abbreviation (e.g., 'MB', 'RMSE').
    long_name : str
        Full metric name.
    value : float
        Calculated value.
    units : str | None
        Units of the metric (if applicable).
    """

    name: str
    long_name: str
    value: float
    units: str | None = None


class BaseMetric(ABC):
    """Abstract base class for statistical metrics.

    All metrics must implement the compute method which takes
    x (reference) and y (comparison) arrays and returns a single value.
    """

    # Override in subclasses
    name: str = "base"
    long_name: str = "Base Metric"
    units: str | None = None

    @abstractmethod
    def compute(
        self,
        x: np.ndarray,
        y: np.ndarray,
        **kwargs: Any,
    ) -> float:
        """Compute the metric.

        Parameters
        ----------
        x
            Reference values (1D array).
        y
            Comparison values (1D array, same shape as x).
        **kwargs
            Metric-specific options.

        Returns
        -------
        float
            Computed metric value.
        """
        ...

    def __call__(
        self,
        x: np.ndarray,
        y: np.ndarray,
        **kwargs: Any,
    ) -> MetricResult:
        """Compute metric and return result object.

        Parameters
        ----------
        x
            Reference values.
        y
            Comparison values.
        **kwargs
            Additional options.

        Returns
        -------
        MetricResult
            Result container with value and metadata.
        """
        value = self.compute(x, y, **kwargs)
        return MetricResult(
            name=self.name,
            long_name=self.long_name,
            value=value,
            units=self.units,
        )


def _prepare_arrays(
    x: np.ndarray,
    y: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Prepare arrays for computation by removing NaN values.

    Parameters
    ----------
    x, y
        Input arrays.

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        Cleaned arrays with matching valid indices.
    """
    x = np.asarray(x).flatten()
    y = np.asarray(y).flatten()

    # Remove NaN from both arrays at matching indices
    mask = np.isfinite(x) & np.isfinite(y)
    return x[mask], y[mask]


# =============================================================================
# Basic Statistical Measures
# =============================================================================


@_register("N")
class CountMetric(BaseMetric):
    """Number of valid x-y pairs."""

    name = "N"
    long_name = "Sample Size"
    units = None

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, y = _prepare_arrays(x, y)
        return float(len(x))


@_register("MX")
class MeanXMetric(BaseMetric):
    """Mean of x values."""

    name = "MX"
    long_name = "Mean X"
    units = None  # Inherits from data

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, _ = _prepare_arrays(x, y)
        if len(x) == 0:
            return np.nan
        return float(np.mean(x))


@_register("MY")
class MeanYMetric(BaseMetric):
    """Mean of y values."""

    name = "MY"
    long_name = "Mean Y"
    units = None

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        _, y = _prepare_arrays(x, y)
        if len(y) == 0:
            return np.nan
        return float(np.mean(y))


@_register("STDX")
class StdXMetric(BaseMetric):
    """Standard deviation of x values."""

    name = "STDX"
    long_name = "X Standard Deviation"
    units = None

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, _ = _prepare_arrays(x, y)
        if len(x) < 2:
            return np.nan
        return float(np.std(x, ddof=1))


@_register("STDY")
class StdYMetric(BaseMetric):
    """Standard deviation of y values."""

    name = "STDY"
    long_name = "Y Standard Deviation"
    units = None

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        _, y = _prepare_arrays(x, y)
        if len(y) < 2:
            return np.nan
        return float(np.std(y, ddof=1))


@_register("MdnX")
class MedianXMetric(BaseMetric):
    """Median of x values."""

    name = "MdnX"
    long_name = "Median X"
    units = None

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, _ = _prepare_arrays(x, y)
        if len(x) == 0:
            return np.nan
        return float(np.median(x))


@_register("MdnY")
class MedianYMetric(BaseMetric):
    """Median of y values."""

    name = "MdnY"
    long_name = "Median Y"
    units = None

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        _, y = _prepare_arrays(x, y)
        if len(y) == 0:
            return np.nan
        return float(np.median(y))


# =============================================================================
# Bias Metrics
# =============================================================================


@_register("MB")
class MeanBiasMetric(BaseMetric):
    """Mean Bias: average difference (y - x).

    MB = (1/N) * Σ(y - x)

    Positive values indicate y values are higher than x values.
    """

    name = "MB"
    long_name = "Mean Bias"
    units = None

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, y = _prepare_arrays(x, y)
        if len(x) == 0:
            return np.nan
        return float(np.mean(y - x))


@_register("MdnB")
class MedianBiasMetric(BaseMetric):
    """Median Bias: median difference (y - x)."""

    name = "MdnB"
    long_name = "Median Bias"
    units = None

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, y = _prepare_arrays(x, y)
        if len(x) == 0:
            return np.nan
        return float(np.median(y - x))


@_register("NMB")
class NormalizedMeanBiasMetric(BaseMetric):
    """Normalized Mean Bias (%).

    NMB = 100 * Σ(y - x) / Σ(x)
    """

    name = "NMB"
    long_name = "Normalized Mean Bias"
    units = "%"

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, y = _prepare_arrays(x, y)
        if len(x) == 0:
            return np.nan
        sum_x = np.sum(x)
        if sum_x == 0:
            return np.nan
        return float(100.0 * np.sum(y - x) / sum_x)


@_register("NMdnB")
class NormalizedMedianBiasMetric(BaseMetric):
    """Normalized Median Bias (%)."""

    name = "NMdnB"
    long_name = "Normalized Median Bias"
    units = "%"

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, y = _prepare_arrays(x, y)
        if len(x) == 0:
            return np.nan
        mdn_x = np.median(x)
        if mdn_x == 0:
            return np.nan
        return float(100.0 * np.median(y - x) / mdn_x)


@_register("FB")
class FractionalBiasMetric(BaseMetric):
    """Fractional Bias (%).

    FB = 200 * (mean(y) - mean(x)) / (mean(y) + mean(x))

    Range: -200% to +200%
    """

    name = "FB"
    long_name = "Fractional Bias"
    units = "%"

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, y = _prepare_arrays(x, y)
        if len(x) == 0:
            return np.nan
        mean_x = np.mean(x)
        mean_y = np.mean(y)
        denom = mean_x + mean_y
        if denom == 0:
            return np.nan
        return float(200.0 * (mean_y - mean_x) / denom)


@_register("MNB")
class MeanNormalizedBiasMetric(BaseMetric):
    """Mean Normalized Bias (%).

    MNB = 100 * (1/N) * Σ((y - x) / x)
    """

    name = "MNB"
    long_name = "Mean Normalized Bias"
    units = "%"

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, y = _prepare_arrays(x, y)
        # Exclude zero x values.
        mask = x != 0
        x = x[mask]
        y = y[mask]
        if len(x) == 0:
            return np.nan
        return float(100.0 * np.mean((y - x) / x))


# =============================================================================
# Error Metrics
# =============================================================================


@_register("ME")
class MeanErrorMetric(BaseMetric):
    """Mean (Gross) Error: average absolute difference.

    ME = (1/N) * Σ|y - x|
    """

    name = "ME"
    long_name = "Mean Error"
    units = None

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, y = _prepare_arrays(x, y)
        if len(x) == 0:
            return np.nan
        return float(np.mean(np.abs(y - x)))


@_register("MdnE")
class MedianErrorMetric(BaseMetric):
    """Median (Gross) Error."""

    name = "MdnE"
    long_name = "Median Error"
    units = None

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, y = _prepare_arrays(x, y)
        if len(x) == 0:
            return np.nan
        return float(np.median(np.abs(y - x)))


@_register("RMSE")
class RMSEMetric(BaseMetric):
    """Root Mean Square Error.

    RMSE = sqrt((1/N) * Σ(y - x)²)
    """

    name = "RMSE"
    long_name = "Root Mean Square Error"
    units = None

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, y = _prepare_arrays(x, y)
        if len(x) == 0:
            return np.nan
        return float(np.sqrt(np.mean((y - x) ** 2)))


@_register("NME")
class NormalizedMeanErrorMetric(BaseMetric):
    """Normalized Mean Error (%).

    NME = 100 * Σ|y - x| / Σ(x)
    """

    name = "NME"
    long_name = "Normalized Mean Error"
    units = "%"

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, y = _prepare_arrays(x, y)
        if len(x) == 0:
            return np.nan
        sum_x = np.sum(x)
        if sum_x == 0:
            return np.nan
        return float(100.0 * np.sum(np.abs(y - x)) / sum_x)


@_register("FE")
class FractionalErrorMetric(BaseMetric):
    """Fractional Error (%).

    FE = 200 * (1/N) * Σ|y - x| / (y + x)

    Range: 0% to 200%
    """

    name = "FE"
    long_name = "Fractional Error"
    units = "%"

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, y = _prepare_arrays(x, y)
        if len(x) == 0:
            return np.nan
        denom = y + x
        # Exclude where denominator is zero
        mask = denom != 0
        if not np.any(mask):
            return np.nan
        return float(200.0 * np.mean(np.abs(y[mask] - x[mask]) / denom[mask]))


@_register("MNE")
class MeanNormalizedErrorMetric(BaseMetric):
    """Mean Normalized (Gross) Error (%).

    MNE = 100 * (1/N) * Σ(|y - x| / x)
    """

    name = "MNE"
    long_name = "Mean Normalized Error"
    units = "%"

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, y = _prepare_arrays(x, y)
        # Exclude zero x
        mask = x != 0
        x = x[mask]
        y = y[mask]
        if len(x) == 0:
            return np.nan
        return float(100.0 * np.mean(np.abs(y - x) / x))


# =============================================================================
# Correlation and Agreement Metrics
# =============================================================================


@_register("R")
class CorrelationMetric(BaseMetric):
    """Pearson Correlation Coefficient.

    R = cov(x, y) / (std(x) * std(y))

    Range: -1 to +1
    """

    name = "R"
    long_name = "Correlation Coefficient"
    units = None

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, y = _prepare_arrays(x, y)
        if len(x) < 2:
            return np.nan
        # Check for zero variance
        if np.std(x) == 0 or np.std(y) == 0:
            return np.nan
        return float(np.corrcoef(x, y)[0, 1])


@_register("R2")
class R2Metric(BaseMetric):
    """Coefficient of Determination (R²).

    R² = R^2 (square of correlation coefficient)

    Range: 0 to 1
    """

    name = "R2"
    long_name = "Coefficient of Determination"
    units = None

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, y = _prepare_arrays(x, y)
        if len(x) < 2:
            return np.nan
        if np.std(x) == 0 or np.std(y) == 0:
            return np.nan
        r = np.corrcoef(x, y)[0, 1]
        return float(r**2)


@_register("IOA")
class IndexOfAgreementMetric(BaseMetric):
    """Index of Agreement (Willmott, 1981).

    IOA = 1 - Σ(y - x)² / Σ(|y - mean(x)| + |x - mean(x)|)²

    Range: 0 to 1, where 1 indicates perfect agreement.
    """

    name = "IOA"
    long_name = "Index of Agreement"
    units = None

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, y = _prepare_arrays(x, y)
        if len(x) == 0:
            return np.nan

        mean_x = np.mean(x)
        numerator = np.sum((y - x) ** 2)
        denominator = np.sum((np.abs(y - mean_x) + np.abs(x - mean_x)) ** 2)

        if denominator == 0:
            return np.nan
        return float(1.0 - numerator / denominator)


@_register("d1")
class ModifiedIndexOfAgreementMetric(BaseMetric):
    """Modified Index of Agreement (d1).

    d1 = 1 - Σ|y - x| / Σ(|y - mean(x)| + |x - mean(x)|)

    Range: 0 to 1
    """

    name = "d1"
    long_name = "Modified Index of Agreement"
    units = None

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, y = _prepare_arrays(x, y)
        if len(x) == 0:
            return np.nan

        mean_x = np.mean(x)
        numerator = np.sum(np.abs(y - x))
        denominator = np.sum(np.abs(y - mean_x) + np.abs(x - mean_x))

        if denominator == 0:
            return np.nan
        return float(1.0 - numerator / denominator)


@_register("E1")
class ModifiedCoefficientOfEfficiencyMetric(BaseMetric):
    """Modified Coefficient of Efficiency (E1).

    E1 = 1 - Σ|y - x| / Σ|x - mean(x)|

    Range: -∞ to 1
    """

    name = "E1"
    long_name = "Modified Coefficient of Efficiency"
    units = None

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, y = _prepare_arrays(x, y)
        if len(x) == 0:
            return np.nan

        mean_x = np.mean(x)
        numerator = np.sum(np.abs(y - x))
        denominator = np.sum(np.abs(x - mean_x))

        if denominator == 0:
            return np.nan
        return float(1.0 - numerator / denominator)


@_register("AC")
class AnomalyCorrelationMetric(BaseMetric):
    """Anomaly Correlation.

    AC = Σ((x - mean(x)) * (y - mean(y))) /
         sqrt(Σ(x - mean(x))² * Σ(y - mean(y))²)

    Range: -1 to +1
    """

    name = "AC"
    long_name = "Anomaly Correlation"
    units = None

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, y = _prepare_arrays(x, y)
        if len(x) < 2:
            return np.nan

        x_anom = x - np.mean(x)
        y_anom = y - np.mean(y)

        numerator = np.sum(x_anom * y_anom)
        denominator = np.sqrt(np.sum(x_anom**2) * np.sum(y_anom**2))

        if denominator == 0:
            return np.nan
        return float(numerator / denominator)


# =============================================================================
# Ratio Metrics
# =============================================================================


@_register("RM")
class MeanRatioMetric(BaseMetric):
    """Mean Ratio (x/y)."""

    name = "RM"
    long_name = "Mean Ratio"
    units = None

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, y = _prepare_arrays(x, y)
        # Exclude zero y values
        mask = y != 0
        x = x[mask]
        y = y[mask]
        if len(x) == 0:
            return np.nan
        return float(np.mean(x / y))


@_register("RMdn")
class MedianRatioMetric(BaseMetric):
    """Median Ratio (x/y)."""

    name = "RMdn"
    long_name = "Median Ratio"
    units = None

    def compute(self, x: np.ndarray, y: np.ndarray, **kwargs: Any) -> float:
        x, y = _prepare_arrays(x, y)
        # Exclude zero y values
        mask = y != 0
        x = x[mask]
        y = y[mask]
        if len(x) == 0:
            return np.nan
        return float(np.median(x / y))


# =============================================================================
# Default Metric Sets
# =============================================================================

#: Standard set of metrics for general evaluation
STANDARD_METRICS = ["N", "MX", "MY", "MB", "RMSE", "R", "R2", "NMB", "NME", "IOA"]

#: Full set of all implemented metrics
ALL_METRICS = [
    # Basic
    "N",
    "MX",
    "MY",
    "STDX",
    "STDY",
    "MdnX",
    "MdnY",
    # Bias
    "MB",
    "MdnB",
    "NMB",
    "NMdnB",
    "FB",
    "MNB",
    # Error
    "ME",
    "MdnE",
    "RMSE",
    "NME",
    "FE",
    "MNE",
    # Correlation/Agreement
    "R",
    "R2",
    "IOA",
    "d1",
    "E1",
    "AC",
    # Ratio
    "RM",
    "RMdn",
]

#: Metrics that are expressed as percentages
PERCENTAGE_METRICS = {"NMB", "NMdnB", "FB", "MNB", "NME", "FE", "MNE"}


def get_metric(name: str) -> BaseMetric:
    """Get a metric instance by name.

    Parameters
    ----------
    name
        Metric name (e.g., 'MB', 'RMSE').

    Returns
    -------
    BaseMetric
        Metric instance.

    Raises
    ------
    KeyError
        If metric is not registered.
    """
    metric_cls = METRIC_REGISTRY[name]
    return metric_cls()


def list_metrics() -> list[str]:
    """List all registered metric names.

    Returns
    -------
    list[str]
        Sorted list of metric names.
    """
    return sorted(METRIC_REGISTRY)


def compute_metric(
    name: str,
    x: np.ndarray,
    y: np.ndarray,
    **kwargs: Any,
) -> float:
    """Compute a single metric.

    Parameters
    ----------
    name
        Metric name.
    x
        Reference values.
    y
        Comparison values.
    **kwargs
        Additional options.

    Returns
    -------
    float
        Computed metric value.
    """
    metric = get_metric(name)
    return metric.compute(x, y, **kwargs)


# Aerosol diagnostics. These functions use x=AERONET and y=product, just as
# the 27 definitions above, and return unrounded values for scientific gates.


@dataclass(frozen=True)
class EEEnvelope:
    """Positive lower/upper error magnitudes, each intercept + slope * AOD.

    An asymmetric ocean envelope is represented without changing signs in
    configuration: e.g. lower=(0.02, 0.10), upper=(0.04, 0.10).
    """

    lower_intercept: float
    lower_slope: float
    upper_intercept: float
    upper_slope: float

    def __post_init__(self) -> None:
        coefficients = (
            self.lower_intercept,
            self.lower_slope,
            self.upper_intercept,
            self.upper_slope,
        )
        if not np.isfinite(coefficients).all() or min(coefficients) < 0:
            raise ValueError("EE coefficients must be finite and nonnegative")


DEFAULT_EE: dict[str, EEEnvelope] = {
    "dt_land": EEEnvelope(0.05, 0.15, 0.05, 0.15),  # Levy et al. (2013)
    "dt_ocean": EEEnvelope(0.02, 0.10, 0.04, 0.10),  # Levy et al. (2013)
    "db_land": EEEnvelope(0.05, 0.20, 0.05, 0.20),  # Sayer et al. (2019)
    "viirs_db_water": EEEnvelope(0.03, 0.10, 0.03, 0.10),  # Sayer et al. (2018)
}


def _paired_values(x: ArrayLike, y: ArrayLike) -> tuple[np.ndarray, np.ndarray]:
    left, right = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if left.shape != right.shape:
        raise ValueError("reference x and product y must have identical shapes")
    return left.ravel(), right.ravel()


def expected_error(
    x: ArrayLike, y: ArrayLike, envelope: str | EEEnvelope = "dt_land"
) -> dict[str, float]:
    """Percent within inclusive EE limits; above/below are fractions [0, 1].

    Bounds use the reference AOD, never product AOD. ``N_EE`` counts finite
    pairs with nonnegative envelope widths. Boundaries are compared as product
    values against x+error to avoid roundoff from subtracting x at an exact edge.
    """
    spec = DEFAULT_EE[envelope] if isinstance(envelope, str) else envelope
    left, right = _paired_values(x, y)
    finite = np.isfinite(left) & np.isfinite(right)
    left, right = left[finite], right[finite]
    lower = spec.lower_intercept + spec.lower_slope * left
    upper = spec.upper_intercept + spec.upper_slope * left
    valid = (lower >= 0) & (upper >= 0)
    left, right, lower, upper = left[valid], right[valid], lower[valid], upper[valid]
    result = {
        "N_EE": float(left.size),
        "pct_ee": np.nan,
        "fraction_above_ee": np.nan,
        "fraction_below_ee": np.nan,
    }
    if left.size:
        above = right > left + upper
        below = right < left - lower
        result.update(
            pct_ee=float(100 * np.mean(~above & ~below)),
            fraction_above_ee=float(np.mean(above)),
            fraction_below_ee=float(np.mean(below)),
        )
    return result


def regression(x: ArrayLike, y: ArrayLike) -> dict[str, float]:
    """OLS y-on-x and signed reduced-major-axis fits, without rounding.

    Two finite pairs and nonzero x variance are required. RMA also requires
    nonzero y variance and covariance (otherwise its direction is undefined).
    Constant y has the valid OLS fit slope=0 and intercept=mean(y).
    """
    left, right = _paired_values(x, y)
    left, right = _prepare_arrays(left, right)
    result = {name: np.nan for name in ("ols_slope", "ols_intercept", "rma_slope", "rma_intercept")}
    if left.size < 2:
        return result
    mean_x, mean_y = left.mean(), right.mean()
    dx, dy = left - mean_x, right - mean_y
    xx, yy, xy = np.dot(dx, dx), np.dot(dy, dy), np.dot(dx, dy)
    if xx == 0:
        return result
    slope = xy / xx
    result.update(ols_slope=float(slope), ols_intercept=float(mean_y - slope * mean_x))
    if yy > 0 and xy != 0:
        rma = np.sign(xy) * np.sqrt(yy / xx)
        result.update(rma_slope=float(rma), rma_intercept=float(mean_y - rma * mean_x))
    return result


def uncertainty_calibration(
    x: ArrayLike, y: ArrayLike, sigma_sat: ArrayLike, sigma_aer: ArrayLike
) -> dict[str, float]:
    """Sample std(z), mean(z), and strict |z|<1/<2 coverage fractions.

    sigma inputs may be scalars or arrays matching x/y. Missing or negative
    uncertainties, nonfinite pairs and zero combined uncertainty are excluded.
    """
    original_x = np.asarray(x, dtype=float)
    left, right = _paired_values(original_x, y)
    try:
        satellite = np.broadcast_to(np.asarray(sigma_sat, dtype=float), original_x.shape).ravel()
        aeronet = np.broadcast_to(np.asarray(sigma_aer, dtype=float), original_x.shape).ravel()
    except ValueError:
        raise ValueError("uncertainties must be scalars or broadcast to the pair shape") from None
    total_sigma = np.hypot(satellite, aeronet)
    valid = (
        np.isfinite(left)
        & np.isfinite(right)
        & np.isfinite(total_sigma)
        & (satellite >= 0)
        & (aeronet >= 0)
        & (total_sigma > 0)
    )
    z = (right[valid] - left[valid]) / total_sigma[valid]
    return {
        "N_z": float(z.size),
        "z_mean": float(z.mean()) if z.size else np.nan,
        "z_std": float(z.std(ddof=1)) if z.size >= 2 else np.nan,
        "fraction_abs_z_lt_1": float(np.mean(np.abs(z) < 1)) if z.size else np.nan,
        "fraction_abs_z_lt_2": float(np.mean(np.abs(z) < 2)) if z.size else np.nan,
    }
