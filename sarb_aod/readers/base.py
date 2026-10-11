"""Shared pixel batches and per-surface QC for catalog-defined granule readers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from sarb_aod.config.schema import (
    AODCorrectionConfig,
    QCConfig,
    TimeConfig,
    UncertaintyConfig,
    VarRef,
)
from sarb_aod.correction import correct_aod
from sarb_aod.qc import quality_masks
from sarb_aod.streams import StreamRecipe
from sarb_aod.timeconv import tai93_to_unix_seconds
from sarb_aod.uncertainty import pixel_uncertainty
from sarb_aod.windows import day_time_edges_seconds

LAND = np.uint8(0)
OCEAN = np.uint8(1)
FloatArray = NDArray[np.float64]


@dataclass(frozen=True)
class PixelCoordinates:
    t: FloatArray
    lat: FloatArray
    lon: FloatArray

    def __len__(self) -> int:
        return self.t.size


@dataclass(frozen=True)
class PixelBatch:
    """Flattened accepted pixels of one stream; times are UTC Unix seconds.

    Surface codes are ``LAND = 0`` and ``OCEAN = 1``. Missing sigma is retained
    as NaN for count weighting. The separate high-AOD coordinates count only
    QA-passing retrievals with usable geolocation and times in the requested day.
    """

    stream_id: str
    t: FloatArray
    lat: FloatArray
    lon: FloatArray
    aod: FloatArray
    sigma: FloatArray
    surface: NDArray[np.uint8]
    rejected_high: PixelCoordinates
    counts: dict[str, int]

    def __len__(self) -> int:
        return self.aod.size


class VariableSource(Protocol):
    def read(
        self, reference: VarRef, *, flag: bool = False, round_values: bool = True
    ) -> FloatArray: ...

    def attribute(self, name: str) -> object: ...


def variable_key(reference: VarRef, flag: bool, round_values: bool) -> tuple[object, ...]:
    return (reference.name, tuple(sorted((reference.select or {}).items())), flag, round_values)


def coverage_times(source: VariableSource, shape: tuple[int, ...]) -> FloatArray:
    """Interpolate row centers within a VIIRS DT six-minute UTC interval."""

    def parse(name: str) -> datetime:
        value = datetime.fromisoformat(str(source.attribute(name)).replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    start, end = parse("time_coverage_start"), parse("time_coverage_end")
    if (
        start.minute % 6
        or start.second
        or start.microsecond
        or (end - start).total_seconds() != 360
    ):
        raise ValueError("VIIRS DT coverage must start on a six-minute boundary and last 360 s")
    if len(shape) != 2 or shape[0] == 0:
        raise ValueError("VIIRS DT geolocation must have nonempty row and column dimensions")
    rows = start.timestamp() + (np.arange(shape[0], dtype=np.float64) + 0.5) / shape[0] * 360
    return np.broadcast_to(rows[:, None], shape)


def _geolocation(
    source: VariableSource, recipe: StreamRecipe, time: TimeConfig
) -> tuple[FloatArray, FloatArray, FloatArray]:
    geo = recipe.geolocation
    lat = source.read(VarRef(name=geo.lat), round_values=False)
    lon = source.read(VarRef(name=geo.lon), round_values=False)
    if lat.shape != lon.shape:
        raise ValueError(f"{recipe.stream_id}: latitude and longitude shapes differ")
    if geo.time_kind == "coverage_attrs":
        t = coverage_times(source, lat.shape)
    else:
        assert geo.time is not None
        raw_time = source.read(VarRef(name=geo.time), round_values=False)
        t = tai93_to_unix_seconds(raw_time, leap_seconds=time.leap_seconds)
        if t.ndim == 1 and lat.ndim == 2 and t.shape[0] == lat.shape[0]:
            t = t[:, None]
        t = np.broadcast_to(t, lat.shape)
    return t, lat, lon


def assemble_batch(
    source: VariableSource,
    recipe: StreamRecipe,
    *,
    qc: QCConfig,
    uncertainty: UncertaintyConfig,
    time: TimeConfig,
    correction: AODCorrectionConfig | None = None,
    day: date | None = None,
) -> PixelBatch:
    """QC surfaces, prefer accepted land, then correct AOD with original sigma."""
    correction = correction if correction is not None else AODCorrectionConfig()
    t, lat, lon = _geolocation(source, recipe, time)
    located = np.isfinite(t) & np.isfinite(lat) & np.isfinite(lon)
    if day is not None:
        edges = day_time_edges_seconds(day)
        located &= (t >= edges[0]) & (t < edges[-1])
    selected = np.zeros(lat.shape, dtype=bool)
    high = np.zeros(lat.shape, dtype=bool)
    qa_passed = np.zeros(lat.shape, dtype=bool)
    output_aod = np.full(lat.shape, np.nan)
    output_sigma = np.full(lat.shape, np.nan)
    output_surface = np.full(lat.shape, LAND, dtype=np.uint8)
    counts: dict[str, int] = {
        "n_input": lat.size,
        "n_located": int(np.count_nonzero(located)),
    }
    # This order is independent of YAML mapping order, including VIIRS DB's
    # overlapping coastal retrievals. Rejected land never suppresses good ocean.
    for surface, code in (("land", LAND), ("ocean", OCEAN)):
        if surface not in recipe.surfaces:
            continue
        variables = recipe.surfaces[surface]
        aod = source.read(variables.aod)
        qa = source.read(variables.qa, flag=True, round_values=False)
        if aod.shape != lat.shape:
            raise ValueError(f"{recipe.stream_id}/{surface}: AOD and geolocation shapes differ")
        masks = quality_masks(aod, qa, recipe.family, surface, config=qc)
        usable = masks.accepted & located & ~selected
        qa_passed |= masks.qa_passed & np.isfinite(aod) & located
        high |= masks.rejected_high & located
        raw_sigma = source.read(variables.sigma)[usable] if variables.sigma is not None else None
        sigma = pixel_uncertainty(
            aod[usable], recipe.family, surface, retrieval_sigma=raw_sigma, config=uncertainty
        )
        selected |= usable
        output_aod[usable] = aod[usable]
        output_sigma[usable] = sigma
        output_surface[usable] = code
        counts[f"n_{surface}"] = int(np.count_nonzero(usable))
    output_aod[selected] = correct_aod(output_aod[selected], correction)
    counts.update(
        n_qa_passed=int(np.count_nonzero(qa_passed)),
        n_accepted=int(np.count_nonzero(selected)),
        n_negative=int(np.count_nonzero(output_aod[selected] < 0)),
        n_sigma_missing=int(np.count_nonzero(~np.isfinite(output_sigma[selected]))),
        n_rejected_high=int(np.count_nonzero(high)),
    )
    return PixelBatch(
        stream_id=recipe.stream_id,
        t=t[selected],
        lat=lat[selected],
        lon=lon[selected],
        aod=output_aod[selected],
        sigma=output_sigma[selected],
        surface=output_surface[selected],
        rejected_high=PixelCoordinates(t=t[high], lat=lat[high], lon=lon[high]),
        counts=counts,
    )
