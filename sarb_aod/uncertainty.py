"""Per-pixel one-sigma retrieval uncertainty and Dark Target EE approximations.

MODIS C6 envelopes follow Levy et al. (2013), AMT 6:2989; VIIRS DT uses
Sawyer et al. (2025), doi:10.3389/fenvs.2025.1602145. These sigma envelopes
are independent of the evaluation's expected-error acceptance envelopes.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray

from sarb_aod.config.schema import AlgorithmFamily, Surface, UncertaintyConfig


def pixel_uncertainty(
    aod: ArrayLike,
    family: AlgorithmFamily,
    surface: Surface,
    *,
    retrieval_sigma: ArrayLike | None = None,
    config: UncertaintyConfig | None = None,
) -> NDArray[np.float64]:
    """Return sigma for scaled, masked AOD and optional scaled retrieval sigma.

    A supplied sigma always takes priority (including its missing values).
    Missing DB sigma stays NaN: count weighting retains those observations,
    while GLS skips their precision sums. Negative retrieval sigma is treated
    as an envelope magnitude. Nonfinite AOD always has missing sigma.
    """
    config = config if config is not None else UncertaintyConfig()
    values = np.ma.asarray(aod, dtype=np.float64).filled(np.nan)
    out = np.full(values.shape, np.nan)
    if retrieval_sigma is not None:
        sigma = np.broadcast_to(
            np.ma.asarray(retrieval_sigma, dtype=np.float64).filled(np.nan), values.shape
        )
        valid = np.isfinite(values) & np.isfinite(sigma)
        out[valid] = np.maximum(np.abs(sigma[valid]), config.sigma_floor)
    elif family in config.envelopes:
        if surface not in config.envelopes[family]:
            raise ValueError(f"No uncertainty envelope for {family}/{surface}")
        a, b = config.envelopes[family][surface]
        valid = np.isfinite(values)
        out[valid] = np.maximum(a + b * np.abs(values[valid]), config.sigma_floor)
    elif family not in ("modis_db", "viirs_db"):
        raise ValueError(f"No uncertainty envelope for {family}/{surface}")
    return out
