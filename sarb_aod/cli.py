"""Command-line interface: ``sarb-aod``."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
import yaml

from sarb_aod.config.loader import load_config
from sarb_aod.config.schema import RunConfig
from sarb_aod.errors import ConfigurationError
from sarb_aod.stage.runner import audit as audit_manifests
from sarb_aod.stage.runner import stage as stage_granules

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)

aeronet_app = typer.Typer(no_args_is_help=True, help="Acquire and audit AERONET direct-sun caches.")
app.add_typer(aeronet_app, name="aeronet")

ConfigOpt = typer.Option(..., "--config", "-c", exists=True, dir_okay=False, help="Run config YAML")
ProfileOpt = typer.Option(None, "--profile", "-p", help="Machine profile name or YAML path")


def _load(config: Path, profile: Optional[str]) -> RunConfig:
    try:
        return load_config(config, profile)
    except ConfigurationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


@app.command("show-config")
def show_config(config: Path = ConfigOpt, profile: Optional[str] = ProfileOpt) -> None:
    """Print the fully resolved configuration (profile merged, env expanded)."""
    cfg = _load(config, profile)
    typer.echo(yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False))
    from sarb_aod.paths import cam_namelist

    try:
        for name, value in cam_namelist(cfg).items():
            typer.echo(f"# {name} = '{value}'")
    except ValueError as exc:
        typer.echo(f"# CAM namelist unavailable: {exc}")


@app.command()
def stage(
    config: Path = ConfigOpt,
    profile: Optional[str] = ProfileOpt,
    dry_run: bool = typer.Option(False, "--dry-run", help="Search and report only"),
    product: Optional[list[str]] = typer.Option(None, "--product", help="Limit to product(s)"),
    collection: Optional[list[str]] = typer.Option(
        None, "--collection", help="Limit to collection(s)"
    ),
) -> None:
    """Download L2 granules into the configured product/YYYY/MM/DD layout."""
    cfg = _load(config, profile)
    summaries = stage_granules(
        cfg, dry_run=dry_run, products=product or None, collections=collection or None
    )
    failed = False
    for s in summaries:
        status = "unavailable" if not s.available else f"{len(s.failed)} failed"
        typer.echo(
            f"{s.short_name} v{s.collection}: granules={s.granules} "
            f"present={s.already_present} downloaded={s.downloaded} {status}"
        )
        for problem in s.failed[:20]:
            typer.echo(f"  {problem}", err=True)
        failed = failed or bool(s.failed)
    raise typer.Exit(1 if failed else 0)


@app.command()
def audit(config: Path = ConfigOpt, profile: Optional[str] = ProfileOpt) -> None:
    """Re-verify staged granules against their manifests (size and format)."""
    cfg = _load(config, profile)
    results = audit_manifests(cfg)
    if not results:
        typer.echo("no manifests found for this configuration")
    bad = False
    for r in results:
        typer.echo(f"{r.manifest}: {r.ok}/{r.rows} ok")
        for problem in r.problems[:20]:
            typer.echo(f"  {problem}", err=True)
        bad = bad or bool(r.problems)
    raise typer.Exit(1 if bad else 0)


@app.command("grid")
def grid_command(
    config: Path = ConfigOpt,
    profile: Optional[str] = ProfileOpt,
    day: Optional[str] = typer.Option(None, "--day", help="Single UTC day YYYY-MM-DD"),
    overwrite: bool = typer.Option(False, "--overwrite"),
    workers: int = typer.Option(1, "--workers", min=1),
) -> None:
    """Grid configured streams into eight validated files per UTC day."""
    from datetime import date

    from sarb_aod.pipeline import grid_period

    cfg = _load(config, profile)
    try:
        results = grid_period(
            cfg, day=date.fromisoformat(day) if day else None, overwrite=overwrite, workers=workers
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    for result in results:
        if result.error:
            typer.echo(f"{result.day}: FAILED: {result.error}", err=True)
        else:
            status = (
                "already valid"
                if result.skipped
                else f"written ({result.source_granule_count} granules)"
            )
            typer.echo(f"{result.day}: {len(result.files)} files {status}")
    raise typer.Exit(1 if any(result.error for result in results) else 0)


@app.command("validate")
def validate_command(
    inputs: list[Path] = typer.Argument(..., help="NetCDF files or directories")
) -> None:
    """Check the CAM7 contract for every selected NetCDF file."""
    from sarb_aod.contract import validate_contract_file

    files = sorted(
        {file for path in inputs for file in (path.rglob("*.nc") if path.is_dir() else [path])}
    )
    if not files:
        typer.echo("No NetCDF files found", err=True)
        raise typer.Exit(1)
    failures = 0
    for path in files:
        valid, errors = validate_contract_file(path)
        if not valid:
            failures += 1
            typer.echo(f"{path}: {'; '.join(errors)}", err=True)
    typer.echo(f"{len(files)-failures}/{len(files)} files valid")
    raise typer.Exit(1 if failures else 0)


@app.command("summarize")
def summarize_command(config: Path = ConfigOpt, profile: Optional[str] = ProfileOpt) -> None:
    """Write coverage, AOD distribution, and stream-count summaries as TSV."""
    from sarb_aod.summarize import summarize

    try:
        path, rows = summarize(_load(config, profile))
    except (ValueError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"{path}: period AOD mean={rows[-1]['area_mean']:.6f}, p99.9={rows[-1]['p999']:.6f}")


@aeronet_app.command("stage")
def aeronet_stage(
    config: Path = ConfigOpt,
    profile: Optional[str] = ProfileOpt,
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Inspect caches and report requests only"
    ),
) -> None:
    """Download complete UTC days, with resume checks and service pacing."""
    from sarb_aod.aeronet.fetch import AeronetFetchError
    from sarb_aod.aeronet.fetch import stage as stage_aeronet

    cfg = _load(config, profile)
    try:
        summary = stage_aeronet(cfg, dry_run=dry_run)
    except AeronetFetchError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"AERONET {cfg.aeronet.product}: days={summary.days} present={summary.already_present} "
        f"downloaded={summary.downloaded} empty={summary.empty} rows={summary.rows} "
        f"failed={len(summary.failed)}"
    )
    for problem in summary.failed[:20]:
        typer.echo(f"  {problem}", err=True)
    raise typer.Exit(1 if summary.failed else 0)


@aeronet_app.command("audit")
def aeronet_audit(config: Path = ConfigOpt, profile: Optional[str] = ProfileOpt) -> None:
    """Check every configured day's cache and checksum manifest without network."""
    from sarb_aod.aeronet.fetch import audit as audit_aeronet

    results = audit_aeronet(_load(config, profile))
    for result in results:
        status = f"{result.rows} rows OK" if result.problem is None else result.problem
        typer.echo(f"AERONET {result.day}: {status}")
    raise typer.Exit(1 if any(result.problem is not None for result in results) else 0)


if __name__ == "__main__":
    app()
