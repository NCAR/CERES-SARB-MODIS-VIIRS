"""Grid the producer's DT/DB combined product independently of the CAM estimate.

Each native retrieval contributes once, even when both DT and DB streams are
selected. Counts preserve the three source algorithm categories. Output flag 3
means multiple source categories in a grid cell, distinct from native flag 2.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np

from sarb_aod.config.schema import RunConfig
from sarb_aod.grid.kernels import Accumulators, accumulate
from sarb_aod.grid.spec import load_grid, normalize_longitude
from sarb_aod.readers.base import _geolocation
from sarb_aod.readers.netcdf import NetCDFSource
from sarb_aod.streams import StreamRecipe
from sarb_aod.windows import day_time_edges_seconds

AOD_NAME = "AOD_550_Dark_Target_Deep_Blue_Combined_Land_Ocean"
FLAG_NAME = "AOD_550_Dark_Target_Deep_Blue_Combined_Algorithm_Flag_Land_Ocean"
COUNT_NAMES = tuple(f"aod_count_modis_combined_{s}" for s in ("dt", "db", "mixed"))
TOTAL_NAME = "aod_count_modis_combined"


@dataclass(frozen=True)
class CombinedWindow:
    mean: np.ndarray
    category_counts: np.ndarray


def accumulate_product(
    acc: Accumulators, path: Path, recipe: StreamRecipe, config: RunConfig, day: date
) -> None:
    spec = config.load_catalog().collection(recipe.product, recipe.collection).combined_product
    if spec is None:
        raise ValueError(f"no combined product for {recipe.product} {recipe.collection}")
    settings = config.combined_product
    with NetCDFSource(path, config.qc) as source:
        t, lat, lon = _geolocation(source, recipe, config.time)
        aod = source.read(spec.aod, round_values=False)
        # Preserve missing flags as NaN: QA's generic fill-to-zero conversion
        # would turn a missing algorithm flag into a valid Dark Target category.
        flag = source.read(spec.algorithm_flag, round_values=False)
        qa = source.read(spec.qa, round_values=False)
    if any(v.shape != lat.shape for v in (aod, flag, qa)):
        raise ValueError("combined product and geolocation shapes differ")
    # C7 combined AOD is float32. Match configured endpoints in that storage
    # precision: promotion otherwise makes a valid native -0.1 fall below -0.1.
    lower, upper = np.asarray([settings.aod_min, settings.aod_max], dtype=np.float32).astype(float)
    valid = (
        np.isfinite(aod)
        & np.isfinite(qa)
        & (qa == np.floor(qa))
        & (qa >= settings.qa_min)
        & (qa <= 3)
        & (aod >= lower)
        & (aod <= upper)
    )
    if np.any(valid & ~np.isin(flag, [0, 1, 2])):
        raise ValueError("valid combined AOD has missing or unsupported algorithm flag")
    grid = load_grid(config.grid.source)
    for category in range(3):
        selected = valid & (flag == category)
        values = aod[selected]
        accumulate(
            day_time_edges_seconds(day),
            grid.lat_edges,
            grid.lon_edges,
            t[selected],
            lat[selected],
            normalize_longitude(lon[selected]),
            values,
            np.full(values.shape, np.nan),
            category,
            *acc.arrays(),
        )


def product_window(acc: Accumulators, window: int) -> CombinedWindow:
    counts = acc.n[window]
    total = counts.sum(axis=0)
    mean = np.full(total.shape, -999.0)
    np.divide(acc.sum_x[window].sum(axis=0), total, out=mean, where=total > 0)
    return CombinedWindow(mean=mean, category_counts=counts)
