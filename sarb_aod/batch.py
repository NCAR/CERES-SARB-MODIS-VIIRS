"""Portable calendar-month orchestration over the existing daily grid pipeline."""

from __future__ import annotations

import calendar
import json
import os
import tempfile
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Optional

import typer

from sarb_aod.config.loader import load_config
from sarb_aod.config.schema import Period, RunConfig
from sarb_aod.errors import SarbAodError
from sarb_aod.paths import output_path, summary_path
from sarb_aod.pipeline import DayResult, _run_day, config_sha256, processing_sha256, stream_recipes
from sarb_aod.summarize import summarize
from sarb_aod.windows import window_bounds


@dataclass
class MonthResult:
    period: Period
    days: list[DayResult]
    elapsed_seconds: float
    gridding_seconds: float
    output_files: int
    output_bytes: int
    summary_path: Path | None
    metrics_path: Path
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures

    @property
    def resumed_days(self) -> int:
        return sum(day.skipped and day.error is None for day in self.days)

    @property
    def processed_days(self) -> int:
        return sum(not day.skipped and day.error is None for day in self.days)

    @property
    def source_granules(self) -> int:
        return sum(day.source_granule_count for day in self.days if day.error is None)

    @property
    def new_output_files(self) -> int:
        return sum(len(day.files) for day in self.days if day.error is None and not day.skipped)

    def report(
        self, config: RunConfig, *, workers: int, overwrite: bool, started: str
    ) -> dict[str, Any]:
        return {
            "run": config.name,
            "start": self.period.start.isoformat(),
            "end": self.period.end.isoformat(),
            "started_utc": started,
            "config_sha256": config_sha256(config),
            "processing_sha256": processing_sha256(config),
            "passed": self.passed,
            "workers": workers,
            "overwrite": overwrite,
            "elapsed_seconds": self.elapsed_seconds,
            "gridding_seconds": self.gridding_seconds,
            "processed_days": self.processed_days,
            "resumed_days": self.resumed_days,
            "failed_days": sum(day.error is not None for day in self.days),
            "days_with_missing_products": sum(bool(day.missing_products) for day in self.days),
            "source_granules_processed": self.source_granules,
            "granules_per_second": (
                self.source_granules / self.gridding_seconds
                if self.source_granules and self.gridding_seconds > 0
                else None
            ),
            "new_files_per_second": (
                self.new_output_files / self.gridding_seconds
                if self.processed_days and self.gridding_seconds > 0
                else None
            ),
            "output_files": self.output_files,
            "output_bytes": self.output_bytes,
            "summary_path": str(self.summary_path) if self.summary_path is not None else None,
            "failures": self.failures,
            "days": [
                dict(
                    day=day.day.isoformat(),
                    skipped=day.skipped,
                    files=len(day.files),
                    source_granules=day.source_granule_count,
                    error=day.error,
                    missing_products=day.missing_products,
                )
                for day in self.days
            ],
        }


def month_periods(period: Period) -> list[Period]:
    """Split inclusive dates at calendar boundaries, retaining partial end months."""
    periods = []
    current = period.start
    while current <= period.end:
        last = current.replace(day=calendar.monthrange(current.year, current.month)[1])
        end = min(last, period.end)
        periods.append(Period(start=current, end=end))
        if end == period.end:
            break
        current = end + timedelta(days=1)
    return periods


def _summary_path(config: RunConfig, period: Period) -> Path:
    return summary_path(config.model_copy(update={"period": period}))


def _destinations(config: RunConfig, periods: list[Period]) -> list[tuple[Path, Path]]:
    destinations = [
        (_summary_path(config, period), _summary_path(config, period).with_suffix(".metrics.json"))
        for period in periods
    ]
    resolved = [path.resolve() for pair in destinations for path in pair]
    if len(set(resolved)) != len(resolved):
        raise ValueError(
            "paths.summary must produce distinct monthly summary and metrics paths; include {start} and {end}"
        )
    reserved = set(resolved)
    for day in config.period.days():
        for index in range(8):
            if output_path(config, window_bounds(day, index)[0]).resolve() in reserved:
                raise ValueError("monthly summary or metrics path collides with a CAM7 output file")
    return destinations


def _write_metrics(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(report, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _days(config: RunConfig, period: Period, workers: int, overwrite: bool) -> list[DayResult]:
    if workers == 1:
        return [_run_day(config, day, overwrite) for day in period.days()]
    results = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_day, config, day, overwrite): day for day in period.days()}
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                results.append(DayResult(day=futures[future], error=f"{type(exc).__name__}: {exc}"))
    return sorted(results, key=lambda result: result.day)


def grid_months(
    config: RunConfig, *, workers: int = 1, overwrite: bool = False
) -> Iterator[MonthResult]:
    """Yield persisted monthly results, retaining successes after a failed day.

    Day workers receive the original configuration unchanged. A period-only
    copy is used exclusively by the monthly summarizer. Resume therefore uses
    the same output paths and provenance as ``grid_period``.
    """
    if workers < 1:
        raise ValueError("workers must be positive")
    stream_recipes(config)
    periods = month_periods(config.period)
    destinations = _destinations(config, periods)
    for period, (summary_target, metrics_target) in zip(periods, destinations, strict=True):
        started = datetime.now(timezone.utc).isoformat()
        clock_start = perf_counter()
        results = _days(config, period, workers, overwrite)
        gridding_seconds = perf_counter() - clock_start
        failures = [
            f"{result.day}: {result.error}" for result in results if result.error is not None
        ]
        summary_path = None
        if not failures:
            try:
                summary_path, _ = summarize(config.model_copy(update={"period": period}))
                if summary_path != summary_target:
                    raise ValueError("summarizer returned an unexpected monthly path")
            except Exception as exc:
                failures.append(f"summary: {type(exc).__name__}: {exc}")
                summary_path = None
        files = [path for result in results if result.error is None for path in result.files]
        output_bytes = 0
        for path in files:
            try:
                output_bytes += path.stat().st_size
            except OSError as exc:
                failures.append(f"output size {path}: {exc}")
        result = MonthResult(
            period=period,
            days=results,
            elapsed_seconds=perf_counter() - clock_start,
            gridding_seconds=gridding_seconds,
            output_files=len(files),
            output_bytes=output_bytes,
            summary_path=summary_path,
            metrics_path=metrics_target,
            failures=failures,
        )
        try:
            _write_metrics(
                metrics_target,
                result.report(config, workers=workers, overwrite=overwrite, started=started),
            )
        except (OSError, ValueError) as exc:
            result.failures.append(f"metrics: {type(exc).__name__}: {exc}")
        yield result


def grid_months_command(
    config: Path = typer.Option(
        ..., "--config", "-c", exists=True, dir_okay=False, help="Run config YAML"
    ),
    profile: Optional[str] = typer.Option(
        None, "--profile", "-p", help="Machine profile name or YAML path"
    ),
    workers: int = typer.Option(1, "--workers", min=1),
    overwrite: bool = typer.Option(False, "--overwrite"),
) -> None:
    """Grid a multi-year period in resumable calendar months with metrics."""
    failed = False
    try:
        cfg = load_config(config, profile)
        for result in grid_months(cfg, workers=workers, overwrite=overwrite):
            failed |= not result.passed
            state = "complete" if result.passed else "FAILED"
            typer.echo(
                f"{result.period.start}/{result.period.end}: {state}; {result.processed_days} processed, {result.resumed_days} resumed; {result.output_files} files, {result.output_bytes} bytes; {result.elapsed_seconds:.2f} s"
            )
            typer.echo(f"metrics: {result.metrics_path}")
            for failure in result.failures:
                typer.echo(failure, err=True)
    except (SarbAodError, ValueError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    raise typer.Exit(1 if failed else 0)
