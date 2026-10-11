"""Annual submission boundaries and serial wrapper dispatch."""

import os
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

from sarb_aod.config.loader import load_config
from sarb_aod.paths import output_path
from sarb_aod.pipeline import processing_sha256
from sarb_aod.windows import window_bounds

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config/campaigns/modis_c7_200004_202203.txt"


def test_annual_configs_cover_requested_campaign_without_overlap():
    configs = [load_config(ROOT / p) for p in MANIFEST.read_text().splitlines()]
    assert len(configs) == 22
    previous = date(2000, 3, 31)
    output_paths = set()
    for cfg in configs:
        assert cfg.period.start == previous + timedelta(days=1)
        assert cfg.period.start.month == 4 and cfg.period.start.day == 1
        assert cfg.period.end == date(cfg.period.start.year + 1, 3, 31)
        previous = cfg.period.end
        assert set(cfg.grid.streams) == {"terra_dt", "terra_db", "aqua_dt", "aqua_db"}
        assert {stream.collection for stream in cfg.grid.streams.values()} == {"7"}
        assert not cfg.despeckle.enabled and not cfg.aod_correction
        assert cfg.combined_product.enabled and cfg.grid.missing_products == "fill"
        assert cfg.paths.archive_read_only
        for day in cfg.period.days():
            for index in range(8):
                path = output_path(cfg, window_bounds(day, index)[0])
                assert path not in output_paths
                output_paths.add(path)
    assert previous == date(2022, 3, 31)
    assert len(output_paths) == 64280
    assert len({cfg.name for cfg in configs}) == 1
    assert len({processing_sha256(cfg) for cfg in configs}) == 1


@pytest.mark.parametrize("task", ["1", "22", "0", "23", "bad", ""])
def test_array_wrapper_dispatches_one_year_serially(tmp_path, task):
    binary = tmp_path / "bin/sarb-aod"
    binary.parent.mkdir()
    binary.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
    binary.chmod(0o755)
    env = {
        **os.environ,
        "SGE_TASK_ID": task,
        "SARB_AOD_ENV": str(tmp_path),
        "SARB_AOD_REPO": str(ROOT),
        "NSLOTS": "1",
    }
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/ami_grid_years.sh")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if task in {"1", "22"}:
        assert result.returncode == 0, result.stderr
        config = MANIFEST.read_text().splitlines()[int(task) - 1]
        assert f"grid-months\n-c\n{config}\n--workers\n1" in result.stdout
    else:
        assert result.returncode == 2
        assert "grid-months" not in result.stdout
