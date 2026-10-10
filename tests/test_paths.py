from datetime import date
from pathlib import Path

from sarb_aod.config.schema import PathsConfig, load_catalog
from sarb_aod.paths import granule_dir, manifest_path


def test_default_layout_is_product_year_month_day():
    paths = PathsConfig(data_root=Path("/data"))
    catalog = load_catalog()
    assert granule_dir(paths, catalog, "MOD04_L2", "6.1", date(2008, 7, 1)) == Path(
        "/data/Terra/MODIS/MOD04_L2.061/2008/07/01"
    )
    assert granule_dir(paths, catalog, "MYD04_L2", "7", date(2008, 7, 31)) == Path(
        "/data/Aqua/MODIS/MYD04_L2.007/2008/07/31"
    )


def test_alternate_layout_template():
    paths = PathsConfig(
        data_root=Path("/archive"), granule_dir="{data_root}/{file_token}/{short_name}/{year}/{doy}"
    )
    assert granule_dir(paths, load_catalog(), "MOD04_L2", "6.1", date(2008, 7, 1)) == Path(
        "/archive/061/MOD04_L2/2008/183"
    )


def test_manifest_path():
    paths = PathsConfig(data_root=Path("/data"))
    assert manifest_path(
        paths, load_catalog(), "MOD04_L2", "6.1", date(2008, 7, 1), date(2008, 7, 31)
    ) == Path("/data/Terra/MODIS/MOD04_L2.061/manifests/manifest_20080701_20080731.tsv")


def test_viirs_layout():
    paths = PathsConfig(data_root=Path("/data"))
    assert granule_dir(
        paths, load_catalog(), "AERDB_L2_VIIRS_NOAA20", "2.1", date(2020, 9, 8)
    ) == Path("/data/NOAA-20/VIIRS/AERDB_L2_VIIRS_NOAA20.021/2020/09/08")
