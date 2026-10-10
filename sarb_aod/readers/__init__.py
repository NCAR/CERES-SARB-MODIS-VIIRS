"""Read one staged granule into independent, QC-filtered stream batches."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

from sarb_aod.config.schema import QCConfig, TimeConfig, UncertaintyConfig
from sarb_aod.readers.base import LAND, OCEAN, PixelBatch, PixelCoordinates, assemble_batch
from sarb_aod.readers.modis_hdf4 import HDF4Source
from sarb_aod.readers.netcdf import NetCDFSource
from sarb_aod.streams import StreamRecipe

__all__ = ["LAND", "OCEAN", "PixelBatch", "PixelCoordinates", "read_granule"]


def read_granule(
    path: str | Path,
    recipes: Sequence[StreamRecipe],
    *,
    qc: QCConfig | None = None,
    uncertainty: UncertaintyConfig | None = None,
    time: TimeConfig | None = None,
    day: date | None = None,
) -> dict[str, PixelBatch]:
    """Read shared geolocation/QA once and return a batch for each selected stream.

    All recipes must name the same product and collection. Resolve them with
    the same QC preset passed here so alternate AOD fields match the QA rules.
    Pixels outside ``day`` are dropped when a UTC day is supplied.
    """
    if not recipes:
        return {}
    products = {(recipe.product, recipe.collection, recipe.file_format) for recipe in recipes}
    if len(products) != 1:
        raise ValueError("one granule can only serve recipes of one product and collection")
    if len({recipe.stream_id for recipe in recipes}) != len(recipes):
        raise ValueError("each stream can appear only once per granule")
    qc = qc if qc is not None else QCConfig()
    uncertainty = uncertainty if uncertainty is not None else UncertaintyConfig()
    time = time if time is not None else TimeConfig()
    source_type = HDF4Source if recipes[0].file_format == "hdf4" else NetCDFSource
    with source_type(path, qc) as source:
        return {
            recipe.stream_id: assemble_batch(
                source, recipe, qc=qc, uncertainty=uncertainty, time=time, day=day
            )
            for recipe in recipes
        }
