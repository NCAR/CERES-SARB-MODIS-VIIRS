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


def output_path(config: RunConfig, window_start: datetime) -> Path:
    """Contract path for a UTC three-hour window start."""
    from sarb_aod.windows import window_bounds, window_index

    start, _, _ = window_bounds(window_start, window_index(window_start))
    if window_start.tzinfo is not None:
        from datetime import timezone

        window_start = window_start.astimezone(timezone.utc).replace(tzinfo=None)
    if start != window_start:
        raise ValueError("output time must be a UTC three-hour window start")
    return Path(
        config.paths.output.format(
            data_root=str(config.paths.data_root),
            run=config.name,
            stem=config.output.stem,
            year=f"{start:%Y}",
            month=f"{start:%m}",
            day=f"{start:%d}",
            doy=f"{start:%j}",
            yyyymmdd=f"{start:%Y%m%d}",
            sssss=f"{start.hour * 3600:05d}",
        )
    )


def cam_namelist(config: RunConfig) -> dict[str, str]:
    """Derive CAM's literal prefix and date template from the output template.

    A day-of-year-only layout can be rendered by sarb_aod, but cannot be
    represented by the verified CAM year/month/day/second filename contract.
    """
    static = {
        "data_root": str(config.paths.data_root),
        "run": config.name,
        "stem": config.output.stem,
    }
    temporal = {"year": "%y", "month": "%m", "day": "%d", "yyyymmdd": "%y%m%d", "sssss": "%s"}
    pieces = []
    for literal, field, spec, conversion in string.Formatter().parse(config.paths.output):
        if "%" in literal or spec or conversion:
            raise ValueError(
                "CAM output templates cannot contain percent literals or format specifications"
            )
        pieces.append(literal)
        if field is not None:
            if field not in static and field not in temporal:
                raise ValueError(f"CAM filename template does not support {{{field}}}")
            pieces.append(static.get(field, temporal.get(field, "")))
    rendered = "".join(pieces)
    split = rendered.rfind("/", 0, rendered.index("%")) + 1
    prefix, template = rendered[:split], rendered[split:]
    if not prefix.endswith("/"):
        raise ValueError("CAM aod_obs_path requires an output directory and trailing slash")
    expanded_length = len(str(output_path(config, datetime(2000, 12, 31, 21))))
    if len(template) > 80 or expanded_length >= 256:
        raise ValueError("CAM output template exceeds 80 characters or full path reaches 256")
    return {"aod_obs_path": prefix, "aod_obs_template": template}
