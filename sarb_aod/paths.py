"""Render configured path templates for granules and manifests."""

from __future__ import annotations

import string
from datetime import date, datetime
from pathlib import Path

from sarb_aod.config.schema import Catalog, PathsConfig, RunConfig


def _product_fields(
    paths: PathsConfig, catalog: Catalog, short_name: str, collection: str
) -> dict[str, str]:
    product = catalog.product(short_name)
    spec = catalog.collection(short_name, collection)
    return {
        "data_root": str(paths.data_root),
        "platform": product.platform,
        "instrument": product.instrument,
        "short_name": short_name,
        "collection": collection,
        "cmr_version": spec.cmr_version,
        "file_token": spec.file_token,
    }


def granule_dir(
    paths: PathsConfig, catalog: Catalog, short_name: str, collection: str, day: date
) -> Path:
    """Directory holding one day of granules for a product collection."""
    fields = _product_fields(paths, catalog, short_name, collection)
    fields.update(
        year=f"{day:%Y}",
        month=f"{day:%m}",
        day=f"{day:%d}",
        doy=f"{day:%j}",
        yyyymmdd=f"{day:%Y%m%d}",
    )
    return Path(paths.granule_dir.format(**fields))


def manifest_path(
    paths: PathsConfig, catalog: Catalog, short_name: str, collection: str, start: date, end: date
) -> Path:
    """Manifest TSV for a product collection over a period."""
    fields = _product_fields(paths, catalog, short_name, collection)
    fields.update(start=f"{start:%Y%m%d}", end=f"{end:%Y%m%d}")
    return Path(paths.manifest.format(**fields))
