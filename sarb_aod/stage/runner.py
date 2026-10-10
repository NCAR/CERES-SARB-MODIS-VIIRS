"""Stage L2 granules into the configured product/YYYY/MM/DD layout.

For each (product, collection): one CMR search over the whole period, granules
bucketed by the acquisition date in their filename, a manifest written, then a
per-day download of whatever is missing or fails verification.

Downloads land in ``<day dir>/.partial/`` and are moved into place only after
size and format checks pass, so an interrupted run never leaves a truncated
granule where a later run would mistake it for a complete one. Re-runs resume.
"""

from __future__ import annotations

import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Sequence

from sarb_aod.config.schema import Catalog, CollectionSpec, RunConfig
from sarb_aod.paths import granule_dir, manifest_path
from sarb_aod.stage import earthdata
from sarb_aod.stage.earthdata import GranuleRecord
from sarb_aod.stage.manifest import ManifestRow, read_manifest, verify_file, write_manifest

PARTIAL_DIRNAME = ".partial"

LoginFn = Callable[[str], None]
SearchFn = Callable[[str, str, CollectionSpec, str, date, date], list[GranuleRecord]]
DownloadFn = Callable[[Sequence[GranuleRecord], Path, int], None]
LogFn = Callable[[str], None]


@dataclass
class Hooks:
    """Network operations; tests substitute offline fakes."""

    login: LoginFn = earthdata.login
    search: SearchFn = earthdata.search
    download: DownloadFn = earthdata.download


@dataclass
class StageSummary:
    short_name: str
    collection: str
    granules: int = 0
    total_bytes: int = 0
    already_present: int = 0
    downloaded: int = 0
    failed: list[str] = field(default_factory=list)
    manifest: Path | None = None

    @property
    def available(self) -> bool:
        return self.granules > 0


def _select(
    records: Sequence[GranuleRecord], start: date, end: date
) -> dict[date, list[GranuleRecord]]:
    """Bucket by acquisition day within ``[start, end]``, dropping duplicates."""
    by_day: dict[date, dict[str, GranuleRecord]] = defaultdict(dict)
    for record in records:
        if start <= record.day <= end:
            by_day[record.day].setdefault(record.name, record)
    return {day: sorted(group.values(), key=lambda r: r.name) for day, group in by_day.items()}


def _fetch_day(
    records: list[GranuleRecord],
    dest: Path,
    spec: CollectionSpec,
    cfg: RunConfig,
    hooks: Hooks,
    summary: StageSummary,
) -> None:
    rtol = cfg.stage.size_rtol
    needed = [
        r for r in records if verify_file(dest / r.name, r.size_bytes, spec.file_format, rtol)
    ]
    summary.already_present += len(records) - len(needed)
    if not needed:
        return

    partial = dest / PARTIAL_DIRNAME
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    try:
        hooks.download(needed, partial, cfg.stage.threads)
    except Exception as exc:  # keep going: report per-granule failures below
        summary.failed.append(f"{dest}: download error: {exc}")
    for record in needed:
        staged = partial / record.name
        problem = verify_file(staged, record.size_bytes, spec.file_format, rtol)
        if problem is None:
            staged.replace(dest / record.name)
            summary.downloaded += 1
        else:
            summary.failed.append(f"{record.name}: {problem}")
    shutil.rmtree(partial, ignore_errors=True)


def stage(
    cfg: RunConfig,
    *,
    dry_run: bool = False,
    hooks: Hooks | None = None,
    products: Sequence[str] | None = None,
    collections: Sequence[str] | None = None,
    log: LogFn = print,
) -> list[StageSummary]:
    """Acquire the configured granules; return one summary per product collection."""
    hooks = hooks or Hooks()
    catalog: Catalog = cfg.load_catalog()
    start, end = cfg.period.start, cfg.period.end
    logged_in = False
    summaries: list[StageSummary] = []

    for short_name, collection in _filtered(cfg, products, collections):
        product = catalog.product(short_name)
        spec = catalog.collection(short_name, collection)
        summary = StageSummary(short_name, collection)
        summaries.append(summary)

        by_day = _select(
            hooks.search(short_name, collection, spec, product.provider, start, end), start, end
        )
        summary.granules = sum(len(group) for group in by_day.values())
        summary.total_bytes = sum(r.size_bytes for group in by_day.values() for r in group)
        label = f"{short_name} v{collection}"
        if not summary.available:
            log(f"{label}: no granules in CMR for {start}..{end} (not yet published?)")
            continue
        log(
            f"{label}: {summary.granules} granules, {summary.total_bytes / 1024**3:.2f} GiB "
            f"over {len(by_day)} days"
        )

        dirs = {day: granule_dir(cfg.paths, catalog, short_name, collection, day) for day in by_day}
        rows = [
            ManifestRow(
                granule=r.name,
                date=day,
                short_name=short_name,
                collection=collection,
                size_bytes=r.size_bytes,
                url=r.url,
                local_path=dirs[day] / r.name,
            )
            for day in sorted(by_day)
            for r in by_day[day]
        ]
        if dry_run:
            missing = sum(
                1
                for row in rows
                if verify_file(row.local_path, row.size_bytes, spec.file_format, cfg.stage.size_rtol)
            )
            summary.already_present = len(rows) - missing
            log(f"{label}: dry run, {missing} to download, e.g. {rows[0].local_path}")
            continue

        summary.manifest = manifest_path(cfg.paths, catalog, short_name, collection, start, end)
        write_manifest(summary.manifest, rows)
        if not logged_in:
            hooks.login(cfg.stage.auth_strategy)
            logged_in = True
        for day in sorted(by_day):
            dirs[day].mkdir(parents=True, exist_ok=True)
            _fetch_day(by_day[day], dirs[day], spec, cfg, hooks, summary)
            log(
                f"{label} {day}: {len(by_day[day])} granules; cumulative "
                f"present={summary.already_present} downloaded={summary.downloaded} "
                f"failed={len(summary.failed)}"
            )
    return summaries


def _filtered(
    cfg: RunConfig, products: Sequence[str] | None, collections: Sequence[str] | None
) -> list[tuple[str, str]]:
    """Configured selections, optionally narrowed by product and/or collection."""
    return [
        (name, collection)
        for name, collection in cfg.stage.selections()
        if (not products or name in products) and (not collections or collection in collections)
    ]


@dataclass
class AuditResult:
    manifest: Path
    rows: int
    ok: int
    problems: list[str]


def audit(cfg: RunConfig) -> list[AuditResult]:
    """Re-verify every granule listed in the configured manifests."""
    catalog = cfg.load_catalog()
    results: list[AuditResult] = []
    for short_name, collection in cfg.stage.selections():
        path = manifest_path(
            cfg.paths, catalog, short_name, collection, cfg.period.start, cfg.period.end
        )
        if not path.is_file():
            continue
        spec = catalog.collection(short_name, collection)
        rows = read_manifest(path)
        problems = []
        for row in rows:
            problem = verify_file(row.local_path, row.size_bytes, spec.file_format, cfg.stage.size_rtol)
            if problem:
                problems.append(f"{row.granule}: {problem}")
        results.append(AuditResult(path, len(rows), len(rows) - len(problems), problems))
    return results
