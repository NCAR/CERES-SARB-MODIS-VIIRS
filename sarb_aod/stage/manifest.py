"""Granule manifests (TSV) and on-disk integrity checks."""

from __future__ import annotations

import csv
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

COLUMNS = ("granule", "date", "short_name", "collection", "size_bytes", "url", "local_path")

# Leading bytes of each on-disk format; anything else (e.g. an HTML login page
# saved under a granule name) fails verification.
_SIGNATURES = {
    "hdf4": (b"\x0e\x03\x13\x01",),
    "netcdf4": (b"\x89HDF\r\n\x1a\n", b"CDF\x01", b"CDF\x02"),
}


@dataclass(frozen=True)
class ManifestRow:
    granule: str
    date: date
    short_name: str
    collection: str
    size_bytes: int
    url: str
    local_path: Path


def write_manifest(path: Path, rows: Iterable[ManifestRow]) -> None:
    """Atomically write a manifest TSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", newline="") as stream:
            writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
            writer.writerow(COLUMNS)
            for row in rows:
                writer.writerow(
                    (
                        row.granule,
                        row.date.isoformat(),
                        row.short_name,
                        row.collection,
                        row.size_bytes,
                        row.url,
                        str(row.local_path),
                    )
                )
        os.replace(tmp_name, path)
    finally:
        Path(tmp_name).unlink(missing_ok=True)


def read_manifest(path: Path) -> list[ManifestRow]:
    with path.open(newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise ValueError(f"Unexpected manifest columns in {path}: {reader.fieldnames}")
        return [
            ManifestRow(
                granule=row["granule"],
                date=date.fromisoformat(row["date"]),
                short_name=row["short_name"],
                collection=row["collection"],
                size_bytes=int(row["size_bytes"]),
                url=row["url"],
                local_path=Path(row["local_path"]),
            )
            for row in reader
        ]


def verify_file(path: Path, expected_bytes: int, file_format: str, rtol: float) -> str | None:
    """Return ``None`` if ``path`` looks like a complete granule, else a reason."""
    if not path.is_file():
        return "missing"
    actual = path.stat().st_size
    if actual == 0:
        return "empty"
    if expected_bytes > 0 and abs(actual - expected_bytes) > max(1.0, rtol * expected_bytes):
        return f"size {actual} != expected {expected_bytes}"
    with path.open("rb") as stream:
        head = stream.read(8)
    if not head.startswith(_SIGNATURES[file_format]):
        return f"not a {file_format} file (leading bytes {head!r})"
    return None
