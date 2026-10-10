# Ported from NCAR/DAVINCI origin/develop, davinci_monet/plots/_stats.py (Apache-2.0).
"""Shared helper for renderer on-plot statistics annotations.

Renderers display small "stats boxes" (e.g. ``N``, ``MB``, ``RMSE``, ``R``,
``NMB``, ``NME``) on top of their figures. Earlier renderer implementations
hand-rolled these computations inline with raw NumPy, duplicating the formulas
and risking inconsistent NaN handling. This module centralizes the computation
by delegating to the canonical metric registry in
the sarb_aod.evaluation.metrics module, so the annotation values stay consistent
with the statistics tables the pipeline writes.

The single entry point is :func:`annotation_metrics`, which performs a pairwise
NaN drop (matching the ``~isnan(x) & ~isnan(y)`` behavior the renderers
already used) and then resolves each requested metric via ``get_metric`` and
calls its ``.compute(x, y)``.

This module imports from ``sarb_aod.evaluation`` (allowed direction); the stats
package never imports from ``sarb_aod.plots``, so no circular import is
introduced.
"""

from __future__ import annotations

import numpy as np

from sarb_aod.evaluation.metrics import get_metric

__all__ = ["annotation_metrics"]


def annotation_metrics(
    x: np.ndarray,
    y: np.ndarray,
    metrics: list[str],
) -> dict[str, float]:
    """Compute on-plot annotation metrics via the central metric registry.

    The inputs are flattened and pairwise NaN-dropped (an index is kept only
    where *both* ``x`` and ``y`` are non-NaN), reproducing the masking the
    renderers performed before computing their inline stats. Each requested
    metric is then resolved from the registry and computed on the cleaned
    arrays.

    Parameters
    ----------
    x
        The x series values.
    y
        The y series values. Must broadcast to the same flattened length
        as ``x``.
    metrics
        Registry metric names to compute (e.g. ``["N", "MB", "RMSE", "R"]``).

    Returns
    -------
    dict[str, float]
        Mapping of each requested metric name to its computed value. ``"N"`` is
        returned as a Python ``int``; all other metrics as ``float`` (possibly
        ``nan``).

    Notes
    -----
    The registry metrics re-apply a finite mask internally, so passing arrays
    that are already finite (as the renderers do) leaves the values unchanged.
    """
    x_arr = np.asarray(x).flatten()
    y_arr = np.asarray(y).flatten()

    if x_arr.shape != y_arr.shape:
        raise ValueError("annotation arrays must have the same flattened length")
    valid_both = np.isfinite(x_arr) & np.isfinite(y_arr)
    x_valid = x_arr[valid_both]
    y_valid = y_arr[valid_both]

    results: dict[str, float] = {}
    for name in metrics:
        value = get_metric(name).compute(x_valid, y_valid)
        results[name] = int(value) if name == "N" else value
    return results
