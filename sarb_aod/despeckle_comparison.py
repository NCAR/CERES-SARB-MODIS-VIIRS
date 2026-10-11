"""Paired diagnostics with fixed temporal weights and explicit sample support."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import netCDF4
import numpy as np
import pandas as pd

from sarb_aod.config.schema import RunConfig
from sarb_aod.contract import validate_contract_file
from sarb_aod.despeckle import cell_areas
from sarb_aod.grid.spec import GridSpec, load_grid
from sarb_aod.paths import output_path
from sarb_aod.pipeline import require_processing_provenance
from sarb_aod.windows import window_bounds


def ratio(total: np.ndarray, weight: np.ndarray) -> np.ndarray:
    return np.divide(total, weight, out=np.full(total.shape, np.nan), where=weight > 0)


@dataclass
class PairedAggregate:
    """Accumulate both own-sample and identical-window monthly means."""

    grid: GridSpec
    sums: dict[str, np.ndarray] = field(init=False)
    weights: dict[str, np.ndarray] = field(init=False)
    occurrences: dict[str, np.ndarray] = field(init=False)
    windows: int = 0
    rows: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        names = ("unfiltered", "filtered", "unfiltered_common", "filtered_common")
        self.sums = {name: np.zeros(self.grid.shape) for name in names}
        self.weights = {name: np.zeros(self.grid.shape) for name in names}
        self.occurrences = {
            name: np.zeros(self.grid.shape)
            for name in ("unfiltered", "filtered", "removed", "replaced", "changed")
        }

    def add(
        self,
        unfiltered: np.ndarray,
        filtered: np.ndarray,
        count: np.ndarray,
        flags: np.ndarray,
        *,
        time: Any,
        bands: tuple[tuple[float, float], ...],
    ) -> None:
        valid = lambda x: np.isfinite(x) & (x > -900) & (count > 0)
        before, after = valid(unfiltered), valid(filtered)
        common = before & after
        selections = {
            "unfiltered": before,
            "filtered": after,
            "unfiltered_common": common,
            "filtered_common": common,
        }
        actions = {
            "unfiltered": before,
            "filtered": after,
            "removed": before & ~after,
            "replaced": before & ((flags.astype(int) & 2) != 0),
            "changed": before & (flags > 0),
        }
        for name, mask in selections.items():
            data = filtered if name.startswith("filtered") else unfiltered
            self.sums[name] += np.where(mask, data * count, 0)
            self.weights[name] += np.where(mask, count, 0)
        for name, mask in actions.items():
            self.occurrences[name] += mask
        areas = cell_areas(self.grid)
        for region, lo, hi in [
            ("global", -90.0, 90.0),
            *[(f"band_{i+1}", *band) for i, band in enumerate(bands)],
        ]:
            inside = np.broadcast_to(
                ((self.grid.lat >= lo) & (self.grid.lat <= hi))[:, None], self.grid.shape
            )
            row: dict[str, Any] = {"time": time, "region": region, "south": lo, "north": hi}
            for name, mask in selections.items():
                usable = mask & inside
                data = filtered if name.startswith("filtered") else unfiltered
                row[name] = (
                    float(np.average(data[usable], weights=areas[usable]))
                    if usable.any()
                    else np.nan
                )
            for name, mask in actions.items():
                row[name + "_fraction"] = float(areas[mask & inside].sum() / areas[inside].sum())
                row[name + "_cells"] = int((mask & inside).sum())
            self.rows.append(row)
        self.windows += 1

    def fields(self) -> dict[str, np.ndarray]:
        output = {name: ratio(total, self.weights[name]) for name, total in self.sums.items()}
        output["difference_common"] = output["filtered_common"] - output["unfiltered_common"]
        for name in ("removed", "replaced", "changed"):
            output[name + "_fraction"] = ratio(
                self.occurrences[name], self.occurrences["unfiltered"]
            )
        for name in ("unfiltered", "filtered"):
            output[name + "_coverage"] = self.occurrences[name] / self.windows
        return output


def aggregate_comparison(config: RunConfig) -> PairedAggregate:
    if not config.despeckle.enabled:
        raise ValueError("Despeckle comparisons require enabled paired output")
    result = PairedAggregate(load_grid(config.grid.source))
    for day in config.period.days():
        for index in range(8):
            start = window_bounds(day, index)[0]
            path = output_path(config, start)
            valid, errors = validate_contract_file(path)
            if not valid:
                raise ValueError(f"{path}: {errors}")
            with netCDF4.Dataset(path) as dataset:
                require_processing_provenance(dataset, config, start)
                dataset.set_auto_mask(False)
                values = [
                    np.asarray(dataset[name][:])
                    for name in (
                        "aod_weighted_mean_unfiltered",
                        "aod_weighted_mean_filtered",
                        "aod_count",
                        "aod_despeckle_flags",
                    )
                ]
            result.add(*values, time=start.isoformat(), bands=config.despeckle.latitude_bands)
    return result


def comparison_table(result: PairedAggregate) -> pd.DataFrame:
    return pd.DataFrame(result.rows)
