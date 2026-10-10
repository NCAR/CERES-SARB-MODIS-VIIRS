from pathlib import Path

import pytest
from pydantic import ValidationError

from sarb_aod.config.loader import load_config
from sarb_aod.config.schema import GridConfig, TimeConfig


def test_existing_runs_get_f09_and_correct_utc():
    cfg = load_config(Path("config/runs/modis_l2_200807.yaml"))
    assert cfg.grid.source == "f09"
    assert cfg.grid.times_per_day == 8
    assert not cfg.grid.edge_granules
    assert cfg.time.leap_seconds == "apply"


def test_grid_and_time_modes():
    assert GridConfig(source="file:/a/grid.nc").source == "file:/a/grid.nc"
    assert TimeConfig(leap_seconds="ignore").leap_seconds == "ignore"
    for invalid in ["fv1", "file:", "file:   "]:
        with pytest.raises(ValidationError):
            GridConfig(source=invalid)
    with pytest.raises(ValidationError):
        GridConfig(times_per_day=24)
    with pytest.raises(ValidationError):
        TimeConfig(leap_seconds="naive")
