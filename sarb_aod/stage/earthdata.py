"""NASA Earthdata access via earthaccess: login, granule search, download.

Network calls are isolated here so the staging runner can be tested offline with
injected hooks. ``earthaccess`` is imported lazily.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

from sarb_aod.config.schema import CollectionSpec

_ACQUISITION_RE = re.compile(r"\.A(\d{4})(\d{3})\.")
_EXTENSIONS = {"hdf4": (".hdf",), "netcdf4": (".nc", ".nc4", ".h5")}
_SIZE_UNIT_BYTES = {
    "B": 1,
    "KB": 1024,
    "MB": 1024**2,
    "GB": 1024**3,
    "TB": 1024**4,
}


@dataclass(frozen=True)
class GranuleRecord:
    """One remote granule, normalized from CMR metadata."""

    name: str
    short_name: str
    collection: str
    day: date
    size_bytes: int
    url: str
    handle: Any = field(default=None, compare=False, repr=False)


def acquisition_day(name: str) -> date:
    """Acquisition date from a LAADS filename token ``.AYYYYDDD.``."""
    match = _ACQUISITION_RE.search(name)
    if match is None:
        raise ValueError(f"No .AYYYYDDD. acquisition token in granule name {name!r}")
    year, doy = int(match.group(1)), int(match.group(2))
    return date(year, 1, 1) + timedelta(days=doy - 1)


def _size_bytes(granule: Any, name: str) -> int:
    """Archive size of ``name`` from UMM metadata (CMR units are explicit)."""
    try:
        infos = granule["umm"]["DataGranule"]["ArchiveAndDistributionInformation"]
    except (KeyError, TypeError):
        return 0
    candidates = [info for info in infos if isinstance(info, dict)]
    named = [info for info in candidates if info.get("Name") == name]
    for info in named or candidates[:1]:
        factor = _SIZE_UNIT_BYTES.get(str(info.get("SizeUnit")).upper())
        size = info.get("Size")
        if factor is not None and isinstance(size, (int, float)):
            return int(round(float(size) * factor))
    return 0


def to_record(
    granule: Any, short_name: str, collection: str, spec: CollectionSpec
) -> GranuleRecord:
    """Normalize an earthaccess ``DataGranule`` into a ``GranuleRecord``."""
    links = list(granule.data_links())
    extensions = _EXTENSIONS[spec.file_format]
    data_links = [link for link in links if link.lower().endswith(extensions)]
    if not data_links:
        raise ValueError(f"No {spec.file_format} data link among {links}")
    url = data_links[0]
    name = url.rsplit("/", 1)[-1]
    return GranuleRecord(
        name=name,
        short_name=short_name,
        collection=collection,
        day=acquisition_day(name),
        size_bytes=_size_bytes(granule, name),
        url=url,
        handle=granule,
    )


def login(strategy: str) -> None:
    """Authenticate to NASA Earthdata Login."""
    import earthaccess

    auth = earthaccess.login(strategy=strategy)
    if not getattr(auth, "authenticated", False):
        raise PermissionError(f"Earthdata login failed (strategy={strategy!r})")


def search(
    short_name: str,
    collection: str,
    spec: CollectionSpec,
    provider: str,
    start: date,
    end: date,
) -> list[GranuleRecord]:
    """All granules overlapping ``[start, end]`` (whole days, UTC)."""
    import earthaccess

    temporal = (
        datetime.combine(start, datetime.min.time()).isoformat(),
        datetime.combine(end, datetime.max.time()).replace(microsecond=0).isoformat(),
    )
    results = earthaccess.search_data(
        short_name=short_name,
        version=spec.cmr_version,
        provider=provider,
        temporal=temporal,
        count=-1,
    )
    return [to_record(granule, short_name, collection, spec) for granule in results]


def download(records: Sequence[GranuleRecord], dest: Path, threads: int) -> None:
    """Download ``records`` into ``dest``; callers verify what landed."""
    import earthaccess

    handles = [record.handle for record in records]
    earthaccess.download(handles, local_path=str(dest), threads=threads, show_progress=False)
