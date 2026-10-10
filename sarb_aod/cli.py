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


if __name__ == "__main__":
    app()
