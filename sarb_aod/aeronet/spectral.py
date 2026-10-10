"""Per-observation AERONET 550 nm AOD and matchup-window uncertainty.

The default is the quadratic log-log interpolation of Eck et al. (1999),
used by Levy et al. (2010), as specified in the project implementation plan.
The optional 500 nm Angstrom formula follows CERES-SARB-CAM7's
``sp1_aod_pipeline/validation/aeronet.py``; no reference package is imported.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike, NDArray

from sarb_aod.aeronet.parse import AOD_COLUMN

SpectralMethod = Literal["quadratic_loglog", "angstrom_500"]


@dataclass(frozen=True)
class SpectralResult:
    """550 nm values and ``interpolated`` / ``extrapolated`` / ``missing`` flags."""

    aod: NDArray[np.float64]
    flag: NDArray[np.str_]


def quadratic_loglog(wavelengths_nm: ArrayLike, aod: ArrayLike) -> SpectralResult:
    """Fit each spectrum's positive AOD at nominal wavelengths from 340 to 1020 nm.

    ``aod`` has shape ``(..., wavelength)``; returned arrays have its leading
    shape. Quadratic fits require at least three valid wavelengths straddling
    550 nm. Otherwise the two shortest valid wavelengths at or above 550 nm
    (including bands beyond 1020 nm) provide a log-linear extrapolation.
    Rows lacking either basis are missing.
    Identical availability patterns are fitted together for efficiency.
    """
    wavelength = np.asarray(wavelengths_nm, dtype=np.float64)
    values = np.asarray(aod, dtype=np.float64)
    if wavelength.ndim != 1 or not np.isfinite(wavelength).all() or (wavelength <= 0).any():
        raise ValueError("Wavelengths must be a finite positive one-dimensional array")
    if np.unique(wavelength).size != wavelength.size:
        raise ValueError("Wavelengths must be unique")
    if values.ndim < 1 or values.shape[-1] != wavelength.size:
        raise ValueError("The final AOD dimension must match the wavelengths")
    shape = values.shape[:-1]
    output = np.full(shape, np.nan, dtype=np.float64)
    flags = np.full(shape, "missing", dtype="U12")
    if not wavelength.size or not values.size:
        return SpectralResult(output, flags)
    order = np.argsort(wavelength)
    wavelength = wavelength[order]
    rows = values.reshape(-1, wavelength.size)[:, order]
    output_flat = output.reshape(-1)
    flags_flat = flags.reshape(-1)
    valid = np.isfinite(rows) & (rows > 0)
    patterns, groups = np.unique(valid, axis=0, return_inverse=True)
    for group, pattern in enumerate(patterns):
        available = np.flatnonzero(pattern)
        bands = available[(wavelength[available] >= 340) & (wavelength[available] <= 1020)]
        selected = wavelength[bands]
        row_index = np.flatnonzero(groups == group)
        if selected.size >= 3 and selected[0] < 550 < selected[-1]:
            # Centering ln(lambda) at 550 is algebraically the same quadratic
            # and improves conditioning; its intercept is ln(tau_550).
            x = np.log(selected / 550.0)
            design = np.column_stack([np.ones(x.size), x, x**2])
            log_tau = np.log(rows[np.ix_(row_index, bands)])
            coefficients = np.linalg.lstsq(design, log_tau.T, rcond=None)[0]
            log_550 = coefficients[0]
            flag = "interpolated"
        else:
            long_bands = available[wavelength[available] >= 550]
            if long_bands.size < 2:
                continue
            first, second = long_bands[:2]
            x1, x2 = np.log(wavelength[[first, second]] / 550.0)
            y1, y2 = np.log(rows[row_index, first]), np.log(rows[row_index, second])
            log_550 = y1 - x1 * (y2 - y1) / (x2 - x1)
            flag = "extrapolated"
        with np.errstate(over="ignore", under="ignore", invalid="ignore"):
            converted = np.exp(log_550)
        usable = np.isfinite(converted) & (converted > 0)
        output_flat[row_index[usable]] = converted[usable]
        flags_flat[row_index[usable]] = flag
    return SpectralResult(output, flags)


def angstrom_500(aod500: ArrayLike, exponent: ArrayLike) -> SpectralResult:
    """Apply CAM7's tau_500 * (550/500)**(-alpha_440_675) to valid inputs."""
    aod, alpha = np.broadcast_arrays(
        np.asarray(aod500, dtype=np.float64), np.asarray(exponent, dtype=np.float64)
    )
    valid = np.isfinite(aod) & (aod > 0) & np.isfinite(alpha) & (alpha != -999)
    output = np.full(aod.shape, np.nan, dtype=np.float64)
    flags = np.full(aod.shape, "missing", dtype="U12")
    with np.errstate(over="ignore", under="ignore", invalid="ignore"):
        converted = aod[valid] * np.power(550.0 / 500.0, -alpha[valid])
    output[valid] = converted
    usable = np.isfinite(output) & (output > 0)
    output[~usable] = np.nan
    flags[usable] = "interpolated"
    return SpectralResult(output, flags)


def convert_550nm(
    frame: pd.DataFrame,
    *,
    method: SpectralMethod = "quadratic_loglog",
    include_extrapolated: bool = True,
) -> pd.DataFrame:
    """Return a copy with ``aod_550nm`` and ``spectral_flag``, before averaging.

    If extrapolations are excluded their AOD becomes NaN while the flag remains
    ``extrapolated``, preserving their identity for sensitivity reports.
    """
    if method == "quadratic_loglog":
        bands = [(name, AOD_COLUMN.fullmatch(name)) for name in frame.columns]
        columns = [name for name, match in bands if match is not None]
        wavelengths = [float(match.group(1)) for _, match in bands if match is not None]
        result = quadratic_loglog(wavelengths, frame[columns].to_numpy(dtype=np.float64))
    elif method == "angstrom_500":
        missing = np.full(len(frame), np.nan)
        result = angstrom_500(
            frame.get("AOD_500nm", missing), frame.get("440-675_Angstrom_Exponent", missing)
        )
    else:
        raise ValueError(f"Unknown AERONET spectral method {method!r}")
    output = frame.copy()
    output["aod_550nm"] = result.aod
    output["spectral_flag"] = result.flag
    if not include_extrapolated:
        output.loc[output["spectral_flag"] == "extrapolated", "aod_550nm"] = np.nan
    return output


def window_uncertainty(aod550: ArrayLike, *, instrument_sigma: float = 0.01) -> float:
    """sqrt(instrument_sigma**2 + sample_std**2), after spectral conversion.

    Missing values are omitted; one observation has zero sample spread and an
    empty window has NaN uncertainty. The spread is not divided by sqrt(N).
    Set ``instrument_sigma=0.02`` for the specified sensitivity calculation.
    """
    if not np.isfinite(instrument_sigma) or instrument_sigma <= 0:
        raise ValueError("AERONET instrument_sigma must be finite and positive")
    values = np.asarray(aod550, dtype=np.float64)
    valid = values[np.isfinite(values)]
    if not valid.size:
        return float("nan")
    spread = float(np.std(valid, ddof=1)) if valid.size > 1 else 0.0
    return float(np.hypot(instrument_sigma, spread))
