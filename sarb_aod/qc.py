"""Retrieval-team QA rules and packed-value decoding for aerosol readers."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

from sarb_aod.config.schema import AlgorithmFamily, QCConfig, Surface

_RETRIEVAL_QA_MIN: dict[AlgorithmFamily, dict[Surface, int]] = {
    "modis_dt": {"land": 3, "ocean": 1},
    "modis_db": {"land": 2, "ocean": 2},
    "viirs_dt": {"land": 3, "ocean": 1},
    "viirs_db": {"land": 2, "ocean": 2},
}


@dataclass(frozen=True)
class QCRule:
    qa_min: int
    aod_min: float
    aod_max: float
    round_physical: bool


@dataclass(frozen=True)
class QCMasks:
    """Masks for usable retrievals and the separate high-AOD diagnostic."""

    accepted: NDArray[np.bool_]
    rejected_high: NDArray[np.bool_]
    qa_passed: NDArray[np.bool_]


def resolve_rule(
    family: AlgorithmFamily, surface: Surface, config: QCConfig | None = None
) -> QCRule:
    """Return the preset's range and threshold with family/surface overrides."""
    config = config if config is not None else QCConfig()
    try:
        qa_min = _RETRIEVAL_QA_MIN[family][surface]
    except KeyError:
        raise ValueError(f"no QC rule defined for {family} {surface}") from None
    if config.preset == "cam7_v2":
        qa_min = 1
    override = config.overrides.get(family, {}).get(surface)
    if override is not None:
        qa_min = override.qa_min
    return QCRule(
        qa_min=qa_min,
        aod_min=config.aod_min,
        aod_max=config.aod_max,
        round_physical=config.preset != "cam7_v2",
    )


def _float_values(values: ArrayLike) -> NDArray[np.float64]:
    """Preserve an input mask while promoting packed integers to floating point."""
    return np.asarray(np.ma.asarray(values, dtype=np.float64).filled(np.nan))


def decode_packed(
    raw: ArrayLike,
    *,
    scale_factor: float = 1.0,
    add_offset: float = 0.0,
    fill_value: float | None = None,
    valid_range: ArrayLike | None = None,
    config: QCConfig | None = None,
    round_values: bool = True,
) -> NDArray[np.float64]:
    """Mask packed fill/range, scale, and optionally round physical values.

    HDF4's approximate 0.001 scale makes inclusive AOD endpoints drift. Six
    decimal places restores them for the retrieval-team preset. ``cam7_v2``
    preserves its historical unrounded scaling. For flags omit ``valid_range``;
    for timestamps and coordinates readers can disable rounding explicitly.
    """
    values = _float_values(raw)
    valid = np.isfinite(values)
    if fill_value is not None:
        valid &= values != fill_value
    if valid_range is not None:
        limits = np.asarray(valid_range, dtype=np.float64)
        if limits.shape != (2,) or not np.all(np.isfinite(limits)) or limits[0] > limits[1]:
            raise ValueError("valid_range must contain two ordered finite packed limits")
        valid &= (values >= limits[0]) & (values <= limits[1])
    scaled = values * scale_factor + add_offset
    if round_values and (config is None or config.preset != "cam7_v2"):
        scaled = np.round(scaled, 6)
    return np.where(valid & np.isfinite(scaled), scaled, np.nan)


def qa_mask(
    qa: ArrayLike,
    family: AlgorithmFamily,
    surface: Surface,
    *,
    config: QCConfig | None = None,
    fill_value: float | None = None,
) -> NDArray[np.bool_]:
    """Accept only finite integral QA flags 0–3 that pass the configured rule."""
    rule = resolve_rule(family, surface, config)
    flags = _float_values(qa)
    accepted = (
        np.isfinite(flags) & (flags == np.floor(flags)) & (flags >= rule.qa_min) & (flags <= 3)
    )
    if fill_value is not None:
        accepted &= flags != fill_value
    return accepted


def quality_masks(
    aod: ArrayLike,
    qa: ArrayLike,
    family: AlgorithmFamily,
    surface: Surface,
    *,
    config: QCConfig | None = None,
    qa_fill_value: float | None = None,
) -> QCMasks:
    """Apply QA and inclusive AOD bounds to already decoded physical values.

    Packed validity masking precedes this operation, so values outside a
    product's documented packed range do not enter ``rejected_high`` either.
    """
    values = _float_values(aod)
    qa_passed = qa_mask(qa, family, surface, config=config, fill_value=qa_fill_value)
    if values.shape != qa_passed.shape:
        raise ValueError("AOD and QA arrays must have identical shapes")
    rule = resolve_rule(family, surface, config)
    finite = np.isfinite(values)
    return QCMasks(
        accepted=qa_passed & finite & (values >= rule.aod_min) & (values <= rule.aod_max),
        rejected_high=qa_passed & finite & (values > rule.aod_max),
        qa_passed=qa_passed,
    )
