from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from sarb_aod.config.loader import load_config
from sarb_aod.config.schema import OutputConfig, PathsConfig
from sarb_aod.paths import cam_namelist, output_path


def cfg():
    return load_config("config/runs/modis_l2_200807.yaml")


def test_default_output_and_cam_template():
    config = cfg()
    start = datetime(2008, 7, 1, 3)
    path = output_path(config, start)
    assert config.name == "modis_l2_200807"
    expected = (
        config.paths.data_root
        / "CAM7/MODIS_VIIRS_AOD_3hr_f09/modis_l2_200807/2008/07/01/MODIS_VIIRS_AOD_3hr_f09.20080701.10800.nc"
    )
    assert path == expected
    namelist = cam_namelist(config)
    assert namelist["aod_obs_path"].endswith("/modis_l2_200807/")
    assert namelist["aod_obs_template"] == "%y/%m/%d/MODIS_VIIRS_AOD_3hr_f09.%y%m%d.%s.nc"
    assert len(namelist["aod_obs_template"]) <= 80


def test_utc_start_and_distinct_runs():
    config = cfg()
    aware = datetime(2008, 6, 30, 23, tzinfo=timezone(timedelta(hours=-4)))
    assert output_path(config, aware) == output_path(config, datetime(2008, 7, 1, 3))
    with pytest.raises(ValueError, match="window start"):
        output_path(config, datetime(2008, 7, 1, 3, 1))
    another = config.model_copy(update={"name": "comparison"})
    assert output_path(config, aware) != output_path(another, aware)


@pytest.mark.parametrize(
    "template",
    [
        "{data_root}/{year}/{month}/{stem}.{sssss}.nc",
        "{data_root}/{yyyymmdd}/{stem}.nc",
        "{data_root}/{yyyymmdd}/{unknown}.{sssss}.nc",
    ],
)
def test_output_template_rejections(template):
    with pytest.raises(ValidationError):
        PathsConfig(data_root=Path("/data"), output=template)


def test_doy_output_and_cam_limit_diagnostics():
    config = cfg()
    paths = PathsConfig(
        data_root=Path("/data"), output="{data_root}/{year}/{doy}/{stem}.{sssss}.nc"
    )
    config = config.model_copy(update={"paths": paths})
    assert "/2008/183/" in str(output_path(config, datetime(2008, 7, 1)))
    with pytest.raises(ValueError, match="does not support"):
        cam_namelist(config)
    config = cfg().model_copy(update={"output": OutputConfig(stem="x" * 80)})
    with pytest.raises(ValueError, match="80 characters"):
        cam_namelist(config)


def test_output_stem_is_filename_component():
    with pytest.raises(ValidationError):
        OutputConfig(stem="../out")


def test_cam_limit_uses_expanded_date_and_second_widths():
    config = cfg().model_copy(
        update={
            "paths": PathsConfig(
                data_root=Path("/" + "x" * 241),
                output="{data_root}/{yyyymmdd}.{sssss}.nc",
            )
        }
    )
    with pytest.raises(ValueError, match="256"):
        cam_namelist(config)
