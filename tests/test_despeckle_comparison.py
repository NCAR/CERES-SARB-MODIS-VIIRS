"""Sampling-aware differences, daily collocation and cache identity."""

import numpy as np
import pandas as pd
import pytest
from test_collocate import aero, field
from test_pipeline import pipeline_config
from test_readers import hdf_granule
from test_report import config as make_config

from sarb_aod.config.schema import DespeckleConfig
from sarb_aod.despeckle_comparison import PairedAggregate
from sarb_aod.evaluation.collocate import collocate_grid_daily
from sarb_aod.evaluation.report import matchup_signature
from sarb_aod.grid.spec import load_grid
from sarb_aod.variants import comparison_tag, evaluation_directory, select_grid_variant


def config(tmp_path, variant="unfiltered", support="available"):
    cfg = make_config(tmp_path, aod_variant=variant, sample_support=support)
    return cfg.model_copy(
        update={"despeckle": DespeckleConfig(enabled=True, latitude_bands=((65, 90),))}
    )


def paired(value, after, count):
    ds = field(value, count=count)
    ds["aod_weighted_mean_unfiltered"] = ds.aod_weighted_mean
    ds["aod_weighted_mean_filtered"] = field(after).aod_weighted_mean
    ds["aod_uncertainty_filtered"] = field(after).aod_uncertainty
    return ds


def test_available_sampling_change_is_separate_from_common_value_change():
    grid = load_grid()
    result = PairedAggregate(grid)
    for a, b, count, flag in [(4.0, -999.0, 9, 1), (1.0, 0.5, 1, 2)]:
        result.add(
            *[np.full(grid.shape, v) for v in (a, b, count, flag)],
            time="2008-07-01",
            bands=((65, 90),),
        )
    fields = result.fields()
    assert np.all(fields["unfiltered"] == 3.7)
    assert np.all(fields["filtered"] == 0.5)
    assert np.all(fields["difference_common"] == -0.5)
    assert np.all(fields["removed_fraction"] == 0.5)
    assert np.all(fields["replaced_fraction"] == 0.5)
    assert np.all(fields["filtered_coverage"] == 0.5)
    assert len(result.rows) == 4


def test_daily_common_windows_keep_identical_weights_and_site_days(tmp_path):
    observations = aero(["2008-07-01T00:00Z", "2008-07-01T20:00Z"], [0.2, 0.4])
    inputs = [paired(4.0, -999.0, 9), paired(1.0, 0.5, 1)] + [paired(-999.0, -999.0, 0)] * 6
    outputs = {}
    for variant in ("unfiltered", "filtered"):
        for support in ("available", "common"):
            cfg = config(tmp_path, variant, support)
            outputs[variant, support] = collocate_grid_daily(
                [select_grid_variant(ds, cfg) for ds in inputs],
                observations,
                start=pd.Timestamp("2008-07-01"),
            )
    before = outputs["unfiltered", "available"].iloc[0]
    assert before.y == 3.7 and before.n_satellite_windows == 2
    unfiltered = outputs["unfiltered", "common"].iloc[0]
    filtered = outputs["filtered", "common"].iloc[0]
    assert unfiltered.y == 1 and filtered.y == 0.5
    assert unfiltered.n_satellite == filtered.n_satellite == 1
    assert unfiltered.n_satellite_windows == filtered.n_satellite_windows == 1
    assert unfiltered.site == filtered.site and unfiltered.time == filtered.time
    assert unfiltered.x == pytest.approx(0.3)
    assert np.isnan(filtered.sigma_y)
    assert inputs[0].aod_weighted_mean.values[0, 0] == 4


def test_grid_cache_separation_and_l2_compatibility(tmp_path):
    variants = [
        config(tmp_path, v, s) for v in ("unfiltered", "filtered") for s in ("available", "common")
    ]
    assert len({comparison_tag(cfg) for cfg in variants}) == 4
    assert len({evaluation_directory(cfg) for cfg in variants}) == 4
    assert len({str(matchup_signature(cfg)) for cfg in variants}) == 4
    disabled = variants[0].model_copy(update={"despeckle": DespeckleConfig()})
    assert matchup_signature(disabled) != matchup_signature(variants[0])
    for cfg in variants:
        assert matchup_signature(cfg, "l2") == matchup_signature(disabled, "l2")
        assert evaluation_directory(cfg, "l2") == evaluation_directory(disabled, "l2")
        assert comparison_tag(cfg, "l2") == ""


def test_filtered_selection_rejects_disabled_config(tmp_path):
    cfg = make_config(tmp_path, aod_variant="filtered")
    with pytest.raises(ValueError, match="requires enabled"):
        select_grid_variant(field(), cfg)


@pytest.mark.parametrize("high", [0.8, 1.2, 1.1])
def test_comparison_suite_protocol_and_custom_latitude_extent(tmp_path, monkeypatch, high):
    from test_aeronet_suites import config as maps_config

    from sarb_aod.plots.despeckle import render_comparison

    # The fixture creates a local coastline and explicitly forbids downloads.
    cfg = maps_config.__wrapped__(tmp_path, monkeypatch)
    cfg = cfg.model_copy(
        update={"despeckle": DespeckleConfig(enabled=True, latitude_bands=((65, 90),))}
    )
    result = PairedAggregate(load_grid())
    for day, before, after in [(1, 0.2, 0.2), (2, high, 0.3)]:
        result.add(
            *[np.full(result.grid.shape, v) for v in (before, after, 2, 0 if day == 1 else 2)],
            time=f"2008-07-{day:02d}",
            bands=((65, 90),),
        )
    artifacts = render_comparison(cfg, result)
    assert len(artifacts) == 10
    assert all(a.report["passed"] and a.pdf.is_file() and a.png.is_file() for a in artifacts)


def test_existing_l2_archive_reusable_after_enabling_filter(tmp_path):
    from test_report import frame

    from sarb_aod.config.loader import configuration_metadata
    from sarb_aod.evaluation.collocate import write_matchups
    from sarb_aod.evaluation.report import load_matchups
    from sarb_aod.pipeline import processing_sha256

    original = make_config(tmp_path)
    path = evaluation_directory(original, "l2") / "matchups_l2_default_20080701_20080731.nc"
    write_matchups(
        path,
        frame(),
        attributes={
            "level": "l2",
            "pairing": "default",
            "processing_sha256": processing_sha256(original),
            **configuration_metadata(original),
        },
    )
    filtered = config(tmp_path, "filtered", "common")
    assert len(load_matchups(filtered, "l2")) == 3
    with pytest.raises(FileNotFoundError):
        load_matchups(filtered, "grid", pairing="daily")


def test_filtered_month_batch_and_campaign_share_summary_path(pipeline_config):
    from datetime import date

    from sarb_aod import batch
    from sarb_aod.config.schema import EvaluationConfig, Period
    from sarb_aod.evaluation.campaign import coverage
    from sarb_aod.paths import summary_path

    cfg, _ = pipeline_config
    cfg = cfg.model_copy(
        update={
            "period": Period(start=date(2008, 7, 1), end=date(2008, 7, 1)),
            "despeckle": DespeckleConfig(enabled=True, latitude_bands=((0, 15),)),
            "evaluation": EvaluationConfig(aod_variant="filtered"),
        }
    )
    result = list(batch.grid_months(cfg))[0]
    assert result.passed and result.summary_path == summary_path(cfg)
    assert "filtered_available" in result.summary_path.name
    assert coverage(cfg)["status"] == "verified"


def test_sparse_action_flags_keep_five_distinct_categories(tmp_path, monkeypatch):
    import matplotlib.pyplot as plt
    from test_aeronet_suites import config as maps_config

    from sarb_aod.paths import cartopy_data_dir
    from sarb_aod.plots.despeckle import action_scale
    from sarb_aod.plots.maps import map_panels
    from sarb_aod.plots.protocol import validate_rendered_figures

    cfg = maps_config.__wrapped__(tmp_path, monkeypatch)
    data = np.zeros(load_grid().shape)
    data[100, :5] = np.arange(5)
    scale = action_scale()
    assert len(np.unique(scale.cmap(scale.norm(np.arange(5))), axis=0)) == 5
    figure = map_panels(
        [data],
        ["terra_dt"],
        kind="count",
        quantity="Action Flag",
        scale=scale,
        cartopy_data_dir=cartopy_data_dir(cfg),
    )
    try:
        assert validate_rendered_figures({"flags": figure})["passed"]
        np.testing.assert_array_equal(
            getattr(figure, "_sarb_plot_metadata")["colorbars"][0].get_ticks(), np.arange(5)
        )
    finally:
        plt.close(figure)
