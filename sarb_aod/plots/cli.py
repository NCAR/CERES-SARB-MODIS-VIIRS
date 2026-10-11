"""Plot subcommand, registered by the top-level sarb-aod Typer application."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from sarb_aod.config.loader import load_config
from sarb_aod.errors import SarbAodError
from sarb_aod.paths import cartopy_data_dir
from sarb_aod.plots.aeronet_suites import render_aeronet_suites
from sarb_aod.plots.style import prefetch_cities, prefetch_coastlines
from sarb_aod.plots.suites import render_map_suites


def plot_command(
    config: Path = typer.Option(
        ..., "--config", "-c", exists=True, dir_okay=False, help="Run config YAML"
    ),
    profile: Optional[str] = typer.Option(
        None, "--profile", "-p", help="Machine profile name or YAML path"
    ),
    compare: Optional[list[Path]] = typer.Option(
        None,
        "--compare",
        exists=True,
        dir_okay=False,
        help="Comparison run config (repeatable); plots current minus comparison",
    ),
    prefetch_maps: bool = typer.Option(
        False, "--prefetch-maps", help="Download configured map assets, then exit"
    ),
) -> None:
    """Render protocol-checked CAM7 maps and AERONET evaluation figures."""
    try:
        cfg = load_config(config, profile)
        if prefetch_maps:
            prefetch_coastlines(cartopy_data_dir(cfg))
            if cfg.plots.regional_layout == "summary" and cfg.plots.regional_city_limit:
                prefetch_cities(cartopy_data_dir(cfg))
                typer.echo(f"Cities cached in {cartopy_data_dir(cfg)}")
            typer.echo(f"Coastlines cached in {cartopy_data_dir(cfg)}")
            return
        comparisons = [load_config(path, profile) for path in (compare or [])]
        artifacts = render_map_suites(cfg, compare_configs=comparisons)
        if "aod_despeckle_comparison" in cfg.plots.suites:
            from sarb_aod.plots.despeckle import render_comparison

            artifacts.extend(render_comparison(cfg))
        if set(cfg.plots.suites) - {"aod_maps", "aod_differences", "aod_despeckle_comparison"}:
            artifacts.extend(render_aeronet_suites(cfg, compare_configs=comparisons))
        for artifact in artifacts:
            typer.echo(str(artifact.pdf))
        if not artifacts:
            typer.echo("No map differences selected; use --compare or a mixed MODIS/VIIRS run.")
    except (SarbAodError, ValueError, OSError, KeyError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
