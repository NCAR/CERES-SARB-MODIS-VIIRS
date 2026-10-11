"""Source-specific AOD adjustments after retrieval QC and before aggregation."""

from collections.abc import Mapping

import numpy as np
from numpy.typing import NDArray

from sarb_aod.config.schema import AODCorrectionConfig


def active_corrections(
    settings: Mapping[str, AODCorrectionConfig],
) -> dict[str, dict[str, float]]:
    """Normalize omitted and explicit identity corrections for cache compatibility."""
    return {
        stream: correction.model_dump()
        for stream, correction in settings.items()
        if not correction.is_identity
    }


def correct_aod(values: NDArray[np.float64], settings: AODCorrectionConfig) -> NDArray[np.float64]:
    """Keep identity runs exact; do not alter retrieval uncertainty or clip pixels."""
    if settings.is_identity:
        return values
    try:
        with np.errstate(over="raise", invalid="raise"):
            corrected = settings.scale * values + settings.offset
    except FloatingPointError as exc:
        raise ValueError("aod_correction produced nonfinite AOD") from exc
    # CAM treats <= -900 as missing; all gridded AOD fields are stored as float32.
    # Reject these values before accumulation rather than silently publishing fill.
    with np.errstate(over="ignore", invalid="ignore"):
        encoded = corrected.astype(np.float32)
    if not np.all(
        (corrected <= np.finfo(np.float32).max) & np.isfinite(encoded) & (encoded > -900)
    ):
        raise ValueError("aod_correction produced AOD outside the CAM/float32 output range")
    return corrected
