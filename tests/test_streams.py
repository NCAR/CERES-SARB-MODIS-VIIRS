"""Catalog recipes and named spectral selection, independent of file readers."""

from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from sarb_aod.config.loader import load_config
from sarb_aod.config.schema import (
    AlgorithmSpec,
    Catalog,
    CollectionSpec,
    Geolocation,
    VarRef,
    load_catalog,
)
from sarb_aod.errors import ConfigurationError
from sarb_aod.paths import granule_dir
from sarb_aod.streams import available_streams, dimension_indexer, resolve_stream


def test_every_catalog_collection_has_resolvable_or_explicitly_unsupported_readers():
    catalog = load_catalog()
    for product, source in catalog.products.items():
        for collection, selected in source.collections.items():
            if not selected.algorithms:
                with pytest.raises(
                    ConfigurationError, match=f"no reader defined for {product} {collection}"
                ):
                    resolve_stream(product, collection, "dt", catalog=catalog)
                continue
            for algorithm in selected.algorithms:
                recipe = resolve_stream(product, collection, algorithm, catalog=catalog)
                assert recipe.stream_id == f"{source.stream_prefix}_{algorithm}"
                assert recipe.geolocation == selected.geolocation
                assert recipe.file_format == selected.file_format
                assert recipe.family == selected.algorithms[algorithm].family
    assert [recipe.stream_id for recipe in available_streams(catalog=catalog)] == [
        "terra_dt",
        "terra_db",
        "aqua_dt",
        "aqua_db",
        "snpp_db",
        "snpp_db",
        "noaa20_db",
        "noaa20_db",
    ]


def test_stream_id_uses_catalog_prefix():
    raw = load_catalog().model_dump()
    raw["products"]["MOD04_L2"]["stream_prefix"] = "example"
    recipe = resolve_stream("MOD04_L2", "6.1", "dt", catalog=Catalog.model_validate(raw))
    assert recipe.stream_id == "example_dt"


def test_cam7_swap_preserves_land_and_catalog():
    catalog = load_catalog()
    baseline = resolve_stream("MOD04_L2", "6.1", "dt", catalog=catalog)
    cam7 = resolve_stream("MOD04_L2", "6.1", "dt", catalog=catalog, preset="cam7_v2")
    assert cam7.surfaces["ocean"].aod.name == "Effective_Optical_Depth_Best_Ocean"
    assert baseline.surfaces["ocean"].aod.name == "Effective_Optical_Depth_Average_Ocean"
    assert cam7.surfaces["land"] == baseline.surfaces["land"]
    assert catalog.collection("MOD04_L2", "6.1").algorithms["dt"].surfaces == baseline.surfaces
    assert resolve_stream("MYD04_L2", "6.1", "db", preset="cam7_v2").surfaces == {
        "land": catalog.collection("MYD04_L2", "6.1").algorithms["db"].surfaces["land"]
    }


def test_deep_blue_has_land_uncertainty_only():
    recipe = resolve_stream("MYD04_L2", "6.1", "db")
    assert set(recipe.surfaces) == {"land"}
    assert recipe.surfaces["land"].sigma is not None
    assert recipe.surfaces["land"].sigma.name.endswith("Estimated_Uncertainty")


def test_unknown_algorithm_for_supported_collection():
    raw = load_catalog().model_dump()
    del raw["products"]["MOD04_L2"]["collections"]["6.1"]["algorithms"]["db"]
    with pytest.raises(ConfigurationError, match="no db reader defined"):
        resolve_stream("MOD04_L2", "6.1", "db", catalog=Catalog.model_validate(raw))


@pytest.mark.parametrize(
    "data",
    [
        {"name": "aod", "select": {"": 1}},
        {"name": "aod", "select": {"wavelength": -1}},
        {"name": "aod", "axis": 0},
        {"name": ""},
    ],
)
def test_invalid_variable_reference_rejected(data):
    with pytest.raises(ValidationError):
        VarRef.model_validate(data)


@pytest.mark.parametrize(
    "time,time_kind", [(None, "tai93"), ("", "tai93"), ("Scan_Start_Time", "coverage_attrs")]
)
def test_inconsistent_time_source_rejected(time, time_kind):
    with pytest.raises(ValidationError):
        Geolocation.model_validate(
            {"lat": "lat", "lon": "lon", "time": time, "time_kind": time_kind}
        )


def test_reader_recipe_requires_geolocation_and_consistent_family():
    raw = load_catalog().collection("MOD04_L2", "6.1").model_dump()
    raw["geolocation"] = None
    with pytest.raises(ValidationError, match="require geolocation"):
        CollectionSpec.model_validate(raw)
    raw = load_catalog().collection("MOD04_L2", "6.1").model_dump()
    raw["algorithms"]["db"]["family"] = "modis_dt"
    with pytest.raises(ValidationError, match="conflicts with family"):
        CollectionSpec.model_validate(raw)


def test_alternates_cannot_add_an_undefined_surface():
    spec = load_catalog().collection("MOD04_L2", "6.1").algorithms["db"].model_dump()
    spec["alternates"] = {"cam7_v2": {"ocean": spec["surfaces"]["land"]}}
    with pytest.raises(ValidationError, match="undefined surfaces"):
        AlgorithmSpec.model_validate(spec)


def test_named_dimension_selection_follows_axis_order():
    values = np.arange(3 * 4 * 5).reshape(3, 4, 5)
    reference = VarRef(name="aod", select={"wavelength": 1})
    leading = dimension_indexer(("wavelength", "row", "column"), reference)
    trailing = dimension_indexer(("row", "column", "wavelength"), reference)
    np.testing.assert_array_equal(values[leading], np.moveaxis(values, 0, -1)[trailing])
    np.testing.assert_array_equal(
        values[dimension_indexer(("wavelength", "row", "column"), VarRef(name="qa"))],
        values,
    )
    with pytest.raises(ValueError, match="selected dimension.*wavelength.*absent"):
        dimension_indexer(("row", "column", "band"), reference)


@pytest.mark.real_data
@pytest.mark.parametrize("product", ["MOD04_L2", "MYD04_L2"])
def test_staged_modis_dimension_names_and_spectral_selection(product):
    from pyhdf.SD import SD, SDC

    repo = Path(__file__).resolve().parents[1]
    config = load_config(repo / "config/runs/modis_l2_200807.yaml")
    catalog = config.load_catalog()
    directory = granule_dir(config.paths, catalog, product, "6.1", config.period.start)
    paths = sorted(directory.glob("*.hdf"))
    if not paths:
        pytest.skip(f"no staged {product} granules under the configured data root")
    handle = SD(str(paths[0]), SDC.READ)
    try:
        for preset in ("retrieval_team", "cam7_v2"):
            recipe = resolve_stream(product, "6.1", "dt", catalog=catalog, preset=preset)
            for surface in recipe.surfaces.values():
                variable = handle.select(surface.aod.name)
                try:
                    dimensions = tuple(variable.dimensions())
                    values = variable.get()
                    np.testing.assert_array_equal(
                        values[dimension_indexer(dimensions, surface.aod)], values[1, :, :]
                    )
                    assert "0.55" in variable.attributes()["long_name"]
                finally:
                    variable.endaccess()
    finally:
        handle.end()
