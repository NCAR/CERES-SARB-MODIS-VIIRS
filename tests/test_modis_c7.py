"""C7 archive discovery, native-product provenance, and real sample checks."""

from datetime import date, datetime, timezone
from pathlib import Path

import netCDF4 as nc
import numpy as np
import pytest

from sarb_aod.combined_product import AOD_NAME, COUNT_NAMES, FLAG_NAME, TOTAL_NAME
from sarb_aod.config.schema import AODCorrectionConfig, QCConfig, RunConfig
from sarb_aod.contract import validate_contract_file
from sarb_aod.paths import granule_dir
from sarb_aod.pipeline import discover_granules, grid_day, processing_sha256, stream_recipes
from sarb_aod.readers import read_granule
from sarb_aod.stage.runner import stage
from sarb_aod.streams import resolve_stream

DAY = date(2008, 7, 1)


def config_for(root):
    return RunConfig.model_validate(
        {
            "name": "c7_test",
            "paths": {
                "data_root": str(root),
                "granule_pattern": "{short_name}.A{year}{doy}.*.{file_token}.*.nc",
                "archive_read_only": True,
            },
            "period": {"start": DAY, "end": DAY},
            "stage": {"products": {"MOD04_L2": ["7"]}},
            "grid": {
                "streams": {
                    f"terra_{a}": {"product": "MOD04_L2", "collection": "7"} for a in ["dt", "db"]
                }
            },
            "combined_product": {"enabled": True},
        }
    )


@pytest.fixture
def c7_config(tmp_path):
    cfg = config_for(tmp_path)
    target = granule_dir(cfg.paths, cfg.load_catalog(), "MOD04_L2", "7", DAY)
    target.mkdir(parents=True)
    filename = target / "MOD04_L2.A2008183.0000.007.synthetic.nc"
    with nc.Dataset(filename, "w") as ds:
        ds.createDimension("number_of_lines_10x10", 1)
        ds.createDimension("number_of_pixels_10x10", 6)
        ds.createDimension("Wavelength_Used_Land_2", 4)
        ds.createDimension("Wavelength_Used_ALL", 7)
        dims = ("number_of_lines_10x10", "number_of_pixels_10x10")

        def put(name, values, spectral=None):
            group, name = name.rsplit("/", 1)
            g = ds.groups[group] if group in ds.groups else ds.createGroup(group)
            v = g.createVariable(
                name, "f8", dims + ((spectral,) if spectral else ()), fill_value=-999.0
            )
            a = np.array(values, dtype=float).reshape(1, 6)
            if spectral:
                a = np.broadcast_to(a[..., None], (1, 6, len(ds.dimensions[spectral])))
            v[:] = a

        spec = cfg.load_catalog().collection("MOD04_L2", "7")
        put(spec.geolocation.lat, [0] * 6)
        put(spec.geolocation.lon, [0, 0, 0, 5, 10, 15])
        tai = (
            datetime(2008, 7, 1, tzinfo=timezone.utc) - datetime(1993, 1, 1, tzinfo=timezone.utc)
        ).total_seconds() + 6
        put(spec.geolocation.time, [tai + 1] * 5 + [tai + 86400])
        for algorithm, recipe in spec.algorithms.items():
            for surface, fields in recipe.surfaces.items():
                put(
                    fields.aod.name,
                    [0.1] * 6 if surface == "land" else [-999] * 6,
                    next(iter(fields.aod.select)) if fields.aod.select else None,
                )
                if (
                    fields.qa.name.rsplit("/", 1)[-1]
                    not in ds[fields.qa.name.rsplit("/", 1)[0]].variables
                ):
                    put(fields.qa.name, [3] * 6)
                if fields.sigma:
                    put(fields.sigma.name, [0.05] * 6)
        native = spec.combined_product
        put(native.aod.name, [0.1, 0.2, 0.6, -0.05, -999, 0.8])
        put(native.qa.name, [3, 2, 1, 0, -999, 3])
        put(native.algorithm_flag.name, [0, 1, 2, 2, -999, 0])
    # Same-extension non-aerosol files must never reach the reader.
    (target / "MOD0203_SS.A2008183.0000.007.synthetic.nc").write_bytes(b"not aerosol")
    return cfg, filename


def test_combined_product_is_independent_and_counts_flags(c7_config):
    cfg, _ = c7_config
    inputs, _, count = discover_granules(cfg, DAY, stream_recipes(cfg))
    assert count == 1 and len(inputs) == 1
    result = grid_day(cfg, DAY)
    assert len(result.files) == 8
    assert all(validate_contract_file(p) == (True, []) for p in result.files)
    with nc.Dataset(result.files[0]) as ds:
        ds.set_auto_mask(False)
        total, mean, flag = (ds[n][:] for n in [TOTAL_NAME, AOD_NAME, FLAG_NAME])
        assert total.sum() == 4  # Not doubled by the separate DT and DB streams.
        assert mean[total == 3] == pytest.approx([0.3])
        assert mean[total == 1] == pytest.approx([-0.05])  # Native diagnostic is unclipped.
        assert flag[total == 3].tolist() == [3]
        assert flag[total == 1].tolist() == [2]  # Native mixed, not mixed grid categories.
        assert [ds[n][:].sum() for n in COUNT_NAMES] == [1, 1, 2]
        assert ds["aod_count"][:].sum() == 10
    assert grid_day(cfg, DAY).skipped
    with nc.Dataset(result.files[0], "a") as ds:
        ds[FLAG_NAME][0, 0] = 1
    assert not validate_contract_file(result.files[0])[0]
    assert not grid_day(cfg, DAY).skipped


def test_missing_algorithm_flags_fail_and_archive_is_read_only(c7_config):
    cfg, path = c7_config
    with pytest.raises(ValueError, match="read-only archive"):
        stage(cfg)
    with nc.Dataset(path, "a") as ds:
        ds[f"geophysical_data_Dark_Target_Deep_Blue_Combined/{FLAG_NAME}"][0, 0] = -999
    with pytest.raises(ValueError, match="algorithm flag"):
        grid_day(cfg, DAY)


def test_source_corrections_preserve_native_combined_diagnostic(c7_config):
    cfg, _ = c7_config
    cfg = cfg.model_copy(
        update={
            "aod_correction": {
                "terra_dt": AODCorrectionConfig(scale=2, offset=0.2),
                "terra_db": AODCorrectionConfig(scale=0.5, offset=0.05),
            }
        }
    )
    result = grid_day(cfg, DAY)
    with nc.Dataset(result.files[0]) as ds:
        ds.set_auto_mask(False)
        total, mean = ds[TOTAL_NAME][:], ds[AOD_NAME][:]
        assert total.sum() == 4
        assert mean[total == 3] == pytest.approx([0.3])
        assert mean[total == 1] == pytest.approx([-0.05])  # Native and unclipped.
        assert [ds[n][:].sum() for n in COUNT_NAMES] == [1, 1, 2]
        for stream, expected in (("terra_dt", 0.4), ("terra_db", 0.1)):
            values = ds[f"aod_mean_{stream}"][:]
            assert values[values > -900] == pytest.approx([expected] * 3)


def test_float32_native_lower_endpoint_is_retained(c7_config):
    cfg, path = c7_config
    with nc.Dataset(path, "a") as ds:
        ds[f"geophysical_data_Dark_Target_Deep_Blue_Combined/{AOD_NAME}"][0, 3] = np.float32(-0.1)
    result = grid_day(cfg, DAY)
    with nc.Dataset(result.files[0]) as ds:
        assert ds[TOTAL_NAME][:].sum() == 4
        assert ds[AOD_NAME][:].min() == np.float32(-0.1)


def test_config_changes_invalidate_native_output(c7_config):
    cfg, _ = c7_config
    changed = cfg.model_copy(
        update={"combined_product": cfg.combined_product.model_copy(update={"qa_min": 2})}
    )
    assert processing_sha256(changed) != processing_sha256(cfg)
    raw = cfg.model_dump()
    raw["grid"]["streams"]["aqua_dt"] = {"product": "MYD04_L2", "collection": "7"}
    raw["stage"]["products"]["MYD04_L2"] = ["7"]
    with pytest.raises(ValueError, match="no primary granules for MYD04_L2"):
        cfg = RunConfig.model_validate(raw)
        grid_day(cfg, DAY)


def test_c7_deep_blue_qa_one_remains_configurable(c7_config):
    cfg, path = c7_config
    recipe = resolve_stream("MOD04_L2", "7", "db")
    with nc.Dataset(path, "a") as ds:
        ds[recipe.surfaces["land"].qa.name][0, 0] = 1
    default = read_granule(path, [recipe], day=DAY)["terra_db"]
    permissive = read_granule(
        path,
        [recipe],
        day=DAY,
        qc=QCConfig.model_validate(
            {"overrides": {"modis_db": {"land": {"qa_min": 1}, "ocean": {"qa_min": 1}}}}
        ),
    )["terra_db"]
    assert len(default) == 4
    assert len(permissive) == 5


@pytest.mark.real_data
@pytest.mark.parametrize("product", ["MOD04_L2", "MYD04_L2"])
def test_real_c7_matches_supplied_best_estimates(product):
    samples = list(Path(".cache/c7_samples").glob(f"{product}*.nc"))
    if not samples:
        pytest.skip("AMI C7 sample not cached")
    path = samples[0]
    recipes = [resolve_stream(product, "7", a) for a in ["dt", "db"]]
    batches = read_granule(path, recipes, day=DAY)
    with nc.Dataset(path) as ds:
        for recipe, field in zip(
            recipes,
            [
                "geophysical_data_Dark_Target/Optical_Depth_Land_And_Ocean",
                "geophysical_data_Deep_Blue/Aerosol_Optical_Thickness_550_Land_Ocean_Best_Estimate",
            ],
            strict=True,
        ):
            expected = np.ma.asarray(ds[field][:], dtype=float).filled(np.nan)
            expected = np.round(expected, 6)
            expected = expected[np.isfinite(expected) & (expected >= -0.05) & (expected <= 5)]
            np.testing.assert_allclose(
                np.sort(batches[recipe.stream_id].aod), np.sort(expected), atol=1e-6
            )
            batch = batches[recipe.stream_id]
            if recipe.algorithm == "dt":
                assert np.isfinite(batch.sigma).all()
            else:
                # A real Terra DB retrieval has missing sigma. Preserve it for
                # count weighting and report it; do not invent precision.
                assert (
                    np.count_nonzero(~np.isfinite(batch.sigma)) == batch.counts["n_sigma_missing"]
                )
                assert np.isfinite(batch.sigma).any()
