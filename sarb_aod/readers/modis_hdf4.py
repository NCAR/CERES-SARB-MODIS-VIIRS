"""Selective MODIS HDF4 SDS reads with packed validity and named dimensions."""

from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Any

from sarb_aod.config.schema import QCConfig, VarRef
from sarb_aod.qc import decode_packed
from sarb_aod.readers.base import FloatArray, variable_key
from sarb_aod.streams import dimension_indexer


class HDF4Source:
    """Cache only selected, decoded SDS arrays for the lifetime of a granule."""

    def __init__(self, path: str | Path, qc: QCConfig | None = None):
        self.path = Path(path)
        self.qc = qc if qc is not None else QCConfig()
        self._handle: Any = None
        self._cache: dict[tuple[object, ...], FloatArray] = {}

    def __enter__(self) -> HDF4Source:
        from pyhdf.SD import SD, SDC

        self._handle = SD(str(self.path), SDC.READ)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._cache.clear()
        if self._handle is not None:
            self._handle.end()
            self._handle = None

    def read(
        self, reference: VarRef, *, flag: bool = False, round_values: bool = True
    ) -> FloatArray:
        key = variable_key(reference, flag, round_values)
        if key not in self._cache:
            if self._handle is None:
                raise RuntimeError("HDF4Source must be opened with a context manager")
            variable = self._handle.select(reference.name)
            try:
                attributes = variable.attributes()
                indexer = dimension_indexer(tuple(variable.dimensions()), reference)
                raw = variable[indexer]
                self._cache[key] = decode_packed(
                    raw,
                    scale_factor=attributes.get("scale_factor", 1.0),
                    add_offset=attributes.get("add_offset", 0.0),
                    fill_value=attributes.get("_FillValue"),
                    valid_range=None if flag else attributes.get("valid_range"),
                    config=self.qc,
                    round_values=round_values,
                )
            finally:
                variable.endaccess()
        return self._cache[key]

    def attribute(self, name: str) -> object:
        if self._handle is None:
            raise RuntimeError("HDF4Source must be opened with a context manager")
        return self._handle.attributes()[name]
