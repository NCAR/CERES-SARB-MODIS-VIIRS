"""Resolve platform/algorithm streams into catalog-defined reader recipes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sarb_aod.config.schema import (
    Algorithm,
    AlgorithmFamily,
    AlgorithmSpec,
    Catalog,
    Geolocation,
    Surface,
    SurfaceVars,
    VarRef,
    load_catalog,
)
from sarb_aod.errors import ConfigurationError


@dataclass(frozen=True)
class StreamRecipe:
    """One collection of a stream and its resolved variables for the selected preset."""

    stream_id: str
    product: str
    collection: str
    algorithm: Algorithm
    platform: str
    instrument: str
    file_format: Literal["hdf4", "netcdf4"]
    geolocation: Geolocation
    algorithm_spec: AlgorithmSpec

    @property
    def family(self) -> AlgorithmFamily:
        return self.algorithm_spec.family

    @property
    def surfaces(self) -> dict[Surface, SurfaceVars]:
        return self.algorithm_spec.surfaces


def resolve_stream(
    product: str,
    collection: str,
    algorithm: Algorithm,
    *,
    catalog: Catalog | None = None,
    preset: str = "retrieval_team",
) -> StreamRecipe:
    """Resolve a stream; an unavailable reader fails before a granule is opened.

    Catalog alternates replace only their listed surfaces. A preset with no
    alternate keeps the base variables, including VIIRS DT's Average Ocean.
    """
    catalog = catalog if catalog is not None else load_catalog()
    source = catalog.product(product)
    selected = catalog.collection(product, collection)
    if not selected.algorithms:
        raise ConfigurationError(f"no reader defined for {product} {collection}")
    if algorithm not in selected.algorithms:
        raise ConfigurationError(
            f"no {algorithm} reader defined for {product} {collection}; "
            f"available algorithms: {', '.join(selected.algorithms)}"
        )
    if selected.geolocation is None:
        raise ConfigurationError(f"no geolocation defined for {product} {collection}")
    spec = selected.algorithms[algorithm]
    surfaces = {**spec.surfaces, **spec.alternates.get(preset, {})}
    return StreamRecipe(
        stream_id=f"{source.stream_prefix}_{algorithm}",
        product=product,
        collection=collection,
        algorithm=algorithm,
        platform=source.platform,
        instrument=source.instrument,
        file_format=selected.file_format,
        geolocation=selected.geolocation,
        algorithm_spec=spec.model_copy(update={"surfaces": surfaces}),
    )


def available_streams(
    *, catalog: Catalog | None = None, preset: str = "retrieval_team"
) -> list[StreamRecipe]:
    """Return each available collection's recipes, in catalog order.

    Multiple collections of a platform/algorithm have the same stream id; the
    run configuration chooses exactly one collection when gridding that stream.
    """
    catalog = catalog if catalog is not None else load_catalog()
    return [
        resolve_stream(product, collection, algorithm, catalog=catalog, preset=preset)
        for product, source in catalog.products.items()
        for collection, selected in source.collections.items()
        for algorithm in selected.algorithms
    ]


def dimension_indexer(dimensions: Sequence[str], reference: VarRef) -> tuple[int | slice, ...]:
    """Build a NumPy/netCDF index by dimension name, independent of axis order."""
    selection = reference.select or {}
    missing = selection.keys() - set(dimensions)
    if missing:
        raise ValueError(
            f"{reference.name}: selected dimension(s) {sorted(missing)} absent from "
            f"{tuple(dimensions)}"
        )
    return tuple(selection.get(dimension, slice(None)) for dimension in dimensions)
