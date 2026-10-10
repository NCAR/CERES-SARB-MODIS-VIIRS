"""NetCDF4 group paths and named spectral selections without full-swath loading."""

from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Any

import numpy as np

from sarb_aod.config.schema import QCConfig, VarRef
from sarb_aod.qc import decode_packed
from sarb_aod.readers.base import FloatArray, variable_key
from sarb_aod.streams import dimension_indexer


class NetCDFSource:
    """Use CF auto-mask/scale for physical fields; QA ignores range metadata."""

    def __init__(self, path: str | Path, qc: QCConfig | None = None):
        self.path = Path(path)
        self.qc = qc if qc is not None else QCConfig()
        self._handle: Any = None
        self._cache: dict[tuple[object, ...], FloatArray] = {}

    def __enter__(self) -> NetCDFSource:
        from netCDF4 import Dataset

        self._handle = Dataset(self.path, "r")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._cache.clear()
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def read(
        self, reference: VarRef, *, flag: bool = False, round_values: bool = True
    ) -> FloatArray:
        key = variable_key(reference, flag, round_values)
        if key not in self._cache:
            if self._handle is None:
                raise RuntimeError("NetCDFSource must be opened with a context manager")
            variable = self._handle[reference.name]
            indexer = dimension_indexer(variable.dimensions, reference)
            if flag:
                # Auto-mask would honor valid_range on flags, which is wrong
                # for some products. Mask only fill, then normalize it to QA 0.
                variable.set_auto_maskandscale(False)
                values = decode_packed(
                    variable[indexer],
                    scale_factor=getattr(variable, "scale_factor", 1.0),
                    add_offset=getattr(variable, "add_offset", 0.0),
                    fill_value=getattr(variable, "_FillValue", None),
                    round_values=False,
                )
                values = np.where(np.isfinite(values), values, 0.0)
            else:
                variable.set_auto_maskandscale(True)
                values = np.asarray(
                    np.ma.asarray(variable[indexer], dtype=np.float64).filled(np.nan)
                )
                if (
                    round_values
                    and self.qc.preset != "cam7_v2"
                    and hasattr(variable, "scale_factor")
                ):
                    values = np.round(values, 6)
            self._cache[key] = values
        return self._cache[key]

    def attribute(self, name: str) -> object:
        if self._handle is None:
            raise RuntimeError("NetCDFSource must be opened with a context manager")
        return self._handle.getncattr(name)
