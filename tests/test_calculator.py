"""Group sample gates and pooled versus equal-site regional statistics."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from sarb_aod.evaluation.calculator import (
    StatisticsCalculator,
    StatisticsConfig,
    binned_bias,
    calculate_statistics,
    quick_stats,
    regional_daily_series,
    regional_statistics,
    rounded_table,
    write_statistics_csv,
)
from sarb_aod.evaluation.metrics import ALL_METRICS


def test_unrounded_core_and_four_decimal_exports(tmp_path: Path) -> None:
    data = pd.DataFrame({"x": [1, 2, 3], "y": [1, 2, 4]})
    result = calculate_statistics(data, metrics=["MB"])
    assert result.loc[0, "MB"] == pytest.approx(1 / 3, abs=1e-16)
    assert result.loc[0, "N"] == 3
    assert result.loc[0, "MB"] > 0.33331  # A gate that rounding would change.
    table = rounded_table(result)
    assert table.loc[0, "MB"] == 0.3333
    path = write_statistics_csv(result, tmp_path / "stats.csv")
    assert "0.3333" in path.read_text()
    assert result.loc[0, "MB"] != table.loc[0, "MB"]


def test_min_samples_counts_only_joint_finite_pairs() -> None:
    result = quick_stats([1, 2, np.nan, 100], [2, 3, 100, np.inf], ALL_METRICS)
    assert result["N"] == 2
    assert all(np.isnan(value) for key, value in result.items() if key != "N")
    assert quick_stats([1, 2], [2, 3], ["MB"], min_samples=2)["MB"] == 1
    with pytest.raises(ValueError, match="identical shapes"):
        quick_stats([1, 2], [1])


def test_rounding_preserves_datetime_and_non_numeric_columns(tmp_path: Path) -> None:
    statistics = pd.DataFrame(
        {
            "day": pd.date_range("2008-07-01", periods=2, tz="UTC"),
            "region": ["global", "southern_ocean"],
            "MB": [1 / 3, np.nan],
            "N": [3, 0],
        }
    )
    rounded = rounded_table(statistics)
    pd.testing.assert_series_equal(rounded["day"], statistics["day"])
    pd.testing.assert_series_equal(rounded["region"], statistics["region"])
    pd.testing.assert_series_equal(rounded["N"], statistics["N"])
    assert rounded["MB"].iloc[0] == 0.3333
    assert statistics["MB"].iloc[0] == 1 / 3
    path = write_statistics_csv(statistics, tmp_path / "regional.csv")
    assert "2008-07-01 00:00:00+00:00,global,0.3333,3" in path.read_text()


def test_group_columns_context_and_missing_labels() -> None:
    data = pd.DataFrame(
        {
            "x": [1, 2, 3, 1, 2, 3, 1],
            "y": [2, 3, 4, 0, 1, 2, 3],
            "configuration": ["A"] * 3 + ["B"] * 3 + [None],
            "stream": ["terra_dt"] * 7,
            "surface": ["land"] * 7,
        }
    )
    result = calculate_statistics(
        data, groupby=["configuration", "stream", "surface"], metrics=["MB"]
    )
    assert len(result) == 3
    assert result.loc[result.configuration == "A", "MB"].item() == 1
    assert result.loc[result.configuration == "B", "MB"].item() == -1
    missing = result[result.configuration.isna()].iloc[0]
    assert missing.N == 1 and np.isnan(missing.MB)


def test_utc_day_and_meteorological_season_grouping() -> None:
    data = pd.DataFrame(
        {
            "x": [1, 1, 1, 1],
            "y": [2, 2, 3, 4],
            "time": [
                "2008-12-31T23:30:00-04:00",
                "2009-01-01T04:00:00Z",
                "2009-03-01T00:00:00Z",
                "2009-09-01T00:00:00Z",
            ],
        }
    )
    result = calculate_statistics(
        data, groupby=["day", "season"], config={"min_samples": 1}, metrics=["MB"]
    )
    assert list(result["season"]) == ["DJF", "MAM", "SON"]
    assert list(result["N"]) == [2, 1, 1]
    assert result.loc[0, "day"] == pd.Timestamp("2009-01-01", tz="UTC")
    assert result.loc[0, "MB"] == 1


def test_xarray_grouping_and_summary() -> None:
    dataset = xr.Dataset(
        {"reference": ("pair", [1, 2, 3]), "product": ("pair", [2, 3, 4])},
        coords={"site": ("pair", ["A", "A", "A"])},
    )
    calculator = StatisticsCalculator(StatisticsConfig(metrics=["MB"]))
    result = calculator.compute(dataset, "reference", "product", groupby="site")
    assert result.iloc[0].to_dict() == {"site": "A", "N": 3, "MB": 1}
    assert calculator.compute_summary(dataset, "reference", "product") == {"N": 3, "MB": 1}


def test_grouped_aerosol_metrics_align_sigma_with_each_group() -> None:
    data = pd.DataFrame(
        {
            "x": [0.0] * 6,
            "y": [0.0, 1.0, -1.0, 0.0, 2.0, -2.0],
            "site": ["A"] * 3 + ["B"] * 3,
            "sigma": [1.0] * 3 + [2.0] * 3,
        }
    )
    result = calculate_statistics(
        data,
        groupby="site",
        ee="dt_ocean",
        sigma_sat="sigma",
        sigma_aer=0.0,
        include_regression=True,
    )
    np.testing.assert_allclose(result["z_std"], [1, 1])
    np.testing.assert_allclose(result["pct_ee"], [100 / 3, 100 / 3])
    assert list(result["N_z"]) == [3, 3]
    assert list(result["fraction_abs_z_lt_1"]) == [1 / 3, 1 / 3]
    assert result["ols_slope"].isna().all()


def test_sigma_sample_gate_uses_calibration_subset() -> None:
    data = pd.DataFrame({"x": [0, 1, 2], "y": [0, 1, 2], "sigma": [0.1, np.nan, np.nan]})
    result = calculate_statistics(data, sigma_sat="sigma", sigma_aer=0.01)
    assert result.loc[0, "N"] == 3 and result.loc[0, "N_z"] == 1
    assert np.isnan(result.loc[0, "fraction_abs_z_lt_1"])


def test_binned_bias_reference_edges_and_empty_bins() -> None:
    x = np.array([0, 0.1, 0.2, 0.3, 0.5, np.nan])
    y = np.array([0.1, 0.3, 0.5, 0.7, 1.0, 10])
    result = binned_bias(x, y, [0, 0.2, 0.4, 0.5, 1.0, 2.0], min_samples=1)
    assert list(result.N) == [2, 2, 0, 1, 0]
    np.testing.assert_allclose(result.MB[[0, 1, 3]], [0.15, 0.35, 0.5])
    assert np.isnan(result.loc[2, "MB"])
    # The final upper boundary is included, and bins always use x rather than y.
    final = binned_bias([1.0], [5.0], [0, 1], min_samples=1)
    assert final.loc[0, "N"] == 1 and final.loc[0, "MB"] == 4
    grouped = calculate_statistics(
        pd.DataFrame({"x": x, "y": y}),
        groupby="aod_bin",
        aod_bins=[0, 0.2, 0.4, 1],
        config={"min_samples": 1},
    )
    assert list(grouped.N) == [2, 2, 1, 0]


def _regional_pairs() -> pd.DataFrame:
    # A contributes nine times as many pairs as B on day 1 but must have the
    # same regional weight. Day 2 has one active site; day 3 has two.
    return pd.DataFrame(
        {
            "region": ["western_us"] * 13,
            "site": ["A"] * 9 + ["B", "A", "A", "B"],
            "time": ["2008-07-01T12:00:00Z"] * 10
            + ["2008-07-02T12:00:00Z"]
            + ["2008-07-03T12:00:00Z"] * 2,
            "x": [0.0] * 9 + [2, 1, 2, 4],
            "y": [1.0] * 9 + [2, 1, 2, 2],
        }
    )


def test_equal_site_region_days_and_report_counts() -> None:
    pairs = _regional_pairs()
    site_days, regional = regional_daily_series(pairs)
    assert len(site_days) == 5
    assert list(regional.active_sites) == [2, 1, 2]
    assert list(regional.N_pairs) == [10, 1, 2]
    np.testing.assert_allclose(regional.x, [1, 1, 3])
    np.testing.assert_allclose(regional.y, [1.5, 1, 2])
    result = regional_statistics(pairs).iloc[0]
    assert result.N == 3
    assert result.N_site_days == 5
    assert result.N_pairs == 13
    assert result.mean_active_sites == pytest.approx(5 / 3)
    assert result.MB == pytest.approx(-1 / 6)
    pooled = calculate_statistics(pairs).iloc[0]
    assert pooled.N == 13 and pooled.MB == pytest.approx(7 / 13)


def test_regional_contexts_are_kept_separate() -> None:
    first, second = _regional_pairs(), _regional_pairs()
    first["configuration"], second["configuration"] = "A", "B"
    second["y"] = second["x"]
    result = regional_statistics(pd.concat([first, second]), groupby="configuration")
    assert list(result.N) == [3, 3]
    assert list(result.N_pairs) == [13, 13]
    assert result.loc[result.configuration == "A", "MB"].item() == pytest.approx(-1 / 6)
    assert result.loc[result.configuration == "B", "MB"].item() == 0


def test_regional_utc_days_joint_filtering_and_empty_inputs() -> None:
    data = pd.DataFrame(
        {
            "region": ["global"] * 4,
            "site": ["A", "A", "B", "C"],
            "time": [
                "2008-07-01T23:00:00-04:00",
                "2008-07-02T04:00:00Z",
                "2008-07-02T12:00:00Z",
                None,
            ],
            "x": [1, 3, np.nan, 1],
            "y": [2, 4, 9, 2],
        }
    )
    site_days, regional = regional_daily_series(data)
    assert len(site_days) == len(regional) == 1
    assert site_days.day.iloc[0] == pd.Timestamp("2008-07-02", tz="UTC")
    assert site_days.x.iloc[0] == 2 and site_days.y.iloc[0] == 3
    assert regional.N_pairs.iloc[0] == 2
    empty = data.iloc[:0]
    assert regional_statistics(empty).empty
    grouped = calculate_statistics(empty, groupby="site", ee="dt_land", include_regression=True)
    assert grouped.empty and {"N", "site", "pct_ee", "ols_slope"} <= set(grouped.columns)


@pytest.mark.parametrize(
    "config",
    [{"min_samples": 0}, {"min_samples": True}, {"round_precision": -1}, {"metrics": ["bad"]}],
)
def test_invalid_configuration(config: dict) -> None:
    with pytest.raises((ValueError, KeyError)):
        StatisticsConfig.from_dict(config)


def test_bad_group_and_uncertainty_columns_raise() -> None:
    data = pd.DataFrame({"x": [1, 2, 3], "y": [1, 2, 3]})
    with pytest.raises(KeyError, match="No grouping column"):
        calculate_statistics(data, groupby="site")
    with pytest.raises(ValueError, match="both sigma"):
        calculate_statistics(data, sigma_sat=0.1)
    with pytest.raises(KeyError, match="uncertainty column"):
        calculate_statistics(data, sigma_sat="missing", sigma_aer=0.01)
    with pytest.raises(ValueError, match="strictly increasing"):
        binned_bias([1], [1], [1, 1])
