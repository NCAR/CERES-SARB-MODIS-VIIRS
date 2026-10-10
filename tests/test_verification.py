"""Scientific verification rejects false gates and preserves inclusive endpoints."""

import numpy as np
import pytest

from sarb_aod.evaluation.verification import box_mask, grid_statistics, pixel_statistics


def test_box_handles_native_longitudes_boundaries_and_dateline():
    np.testing.assert_array_equal(
        box_mask([235, 244, 245, np.nan], [36, 49, 40, 40], [-125, -116, 36, 49]),
        [True, True, False, False],
    )
    np.testing.assert_array_equal(
        box_mask([175, -175, 0], 0, [170, -170, -10, 10]), [True, True, False]
    )


def test_statistics_do_not_confuse_equal_cap_with_rejection():
    stats = pixel_statistics([[0, 1, np.nan], [2, 5]], aod_max=5, rejected_high=0)
    assert stats == dict(
        n_qc_passed=4,
        p50=1.5,
        p90=pytest.approx(4.1),
        maximum=5.0,
        n_at_or_above_max=1,
        n_rejected_high=0,
    )
    assert pixel_statistics([[5]], aod_max=5, rejected_high=3)["n_at_or_above_max"] == 4
    with pytest.raises(ValueError):
        pixel_statistics([[6]], aod_max=5)


def test_convexity_is_cellwise_ignores_fills_and_requires_actual_overlap():
    stats = grid_statistics(
        [0.2, 0.41, -999, 0.5],
        [0.1, 0.1, 0.1, 0.4],
        [0.3, 0.4, 0.3, np.nan],
        [2, 3, -999, 0],
        [0, 2, 0, 1],
        [True, True, False, False],
    )
    assert stats["n_companion_cells_global"] == 3
    assert stats["n_outside_companions_global"] == 2
    assert stats["n_missing_mixed_global"] == 1
    assert stats["maximum_excursion"] == pytest.approx(0.01)
    assert stats["n_chi2_gt_one_box"] == 2
    assert stats["n_rejected_high_box"] == 2
    assert stats["n_rejected_high_global"] == 3


@pytest.fixture
def retrieval_config(tmp_path):
    from sarb_aod.config.schema import RunConfig

    return RunConfig.model_validate(
        {
            "paths": {"data_root": str(tmp_path / "data")},
            "period": {"start": "2020-09-08", "end": "2020-09-14"},
            "stage": {"products": {"MOD04_L2": ["6.1"]}},
            "grid": {"streams": {"terra_dt": {"product": "MOD04_L2", "collection": "6.1"}}},
        }
    )


@pytest.mark.parametrize("setting", ["qc", "time", "uncertainty", "edge_granules"])
def test_retrieval_cache_rejects_changed_reader_settings(retrieval_config, tmp_path, setting):
    from sarb_aod.config.schema import QCConfig
    from scripts.verify_september2020 import (
        load_retrieval_cache,
        retrieval_definition,
        save_retrieval_cache,
    )

    config = retrieval_config
    definition = retrieval_definition(
        [config], config.period.start, config.period.end, [-125, -116, 36, 49]
    )
    path = tmp_path / "summary.tsv"
    save_retrieval_cache(path, [{"stream": "terra_dt", "p90": 0.7}], definition)
    assert load_retrieval_cache(path, definition)[0]["p90"] == "0.7"
    if setting == "qc":
        changed = config.model_copy(update={"qc": QCConfig(preset="cam7_v2")})
    elif setting == "time":
        changed = config.model_copy(
            update={"time": config.time.model_copy(update={"leap_seconds": "ignore"})}
        )
    elif setting == "uncertainty":
        changed = config.model_copy(
            update={"uncertainty": config.uncertainty.model_copy(update={"sigma_floor": 0.02})}
        )
    else:
        changed = config.model_copy(
            update={"grid": config.grid.model_copy(update={"edge_granules": True})}
        )
    updated = retrieval_definition(
        [changed], changed.period.start, changed.period.end, [-125, -116, 36, 49]
    )
    with pytest.raises(ValueError, match="fingerprint differs"):
        load_retrieval_cache(path, updated)


def test_retrieval_cache_rejects_external_catalog_map_change(retrieval_config, tmp_path):
    import yaml

    from scripts.verify_september2020 import (
        load_retrieval_cache,
        retrieval_definition,
        save_retrieval_cache,
    )

    raw = retrieval_config.load_catalog().model_dump(mode="json")
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(yaml.safe_dump(raw))
    config = retrieval_config.model_copy(update={"catalog": catalog})
    definition = retrieval_definition(
        [config], config.period.start, config.period.end, [-125, -116, 36, 49]
    )
    path = tmp_path / "summary.tsv"
    save_retrieval_cache(path, [{"stream": "terra_dt", "p90": 0.7}], definition)
    raw["products"]["MOD04_L2"]["collections"]["6.1"]["algorithms"]["dt"]["surfaces"]["ocean"][
        "aod"
    ]["name"] = "Effective_Optical_Depth_Best_Ocean"
    catalog.write_text(yaml.safe_dump(raw))
    updated = retrieval_definition(
        [config], config.period.start, config.period.end, [-125, -116, 36, 49]
    )
    with pytest.raises(ValueError, match="resolved recipes"):
        load_retrieval_cache(path, updated)


def test_retrieval_cache_rejects_missing_fingerprint_and_changed_table(retrieval_config, tmp_path):
    from scripts.verify_september2020 import (
        load_retrieval_cache,
        retrieval_definition,
        save_retrieval_cache,
    )

    config = retrieval_config
    definition = retrieval_definition(
        [config], config.period.start, config.period.end, [-125, -116, 36, 49]
    )
    path = tmp_path / "summary.tsv"
    path.write_text("stream\tp90\nterra_dt\t0.7\n")
    with pytest.raises(ValueError, match="no fingerprint"):
        load_retrieval_cache(path, definition)
    save_retrieval_cache(path, [{"stream": "terra_dt", "p90": 0.7}], definition)
    path.write_text(path.read_text().replace("0.7", "0.8"))
    with pytest.raises(ValueError, match="content differs"):
        load_retrieval_cache(path, definition)
