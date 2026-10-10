"""Calendar boundaries, real output resume, failure isolation and provenance."""

import json
from datetime import date

import netCDF4
import pytest
import typer
from test_pipeline import hdf_granule, pipeline_config
from typer.testing import CliRunner

from sarb_aod import batch
from sarb_aod.config.schema import Period
from sarb_aod.pipeline import DayResult, config_sha256, processing_sha256


@pytest.mark.parametrize(
    "start,end,expected",
    [
        (
            "2020-01-30",
            "2020-03-02",
            [
                ("2020-01-30", "2020-01-31"),
                ("2020-02-01", "2020-02-29"),
                ("2020-03-01", "2020-03-02"),
            ],
        ),
        ("2019-12-31", "2020-01-01", [("2019-12-31", "2019-12-31"), ("2020-01-01", "2020-01-01")]),
        ("2019-02-01", "2019-02-28", [("2019-02-01", "2019-02-28")]),
    ],
)
def test_calendar_months(start, end, expected):
    periods = batch.month_periods(
        Period(start=date.fromisoformat(start), end=date.fromisoformat(end))
    )
    assert [(str(item.start), str(item.end)) for item in periods] == expected


def test_resume_preserves_real_contract_files_and_provenance(pipeline_config):
    config, _ = pipeline_config
    config = config.model_copy(
        update={"period": Period(start=date(2008, 7, 1), end=date(2008, 7, 1))}
    )
    first = list(batch.grid_months(config))[0]
    assert first.passed and first.processed_days == 1 and first.resumed_days == 0
    assert first.summary_path.is_file()
    assert first.output_files == 8
    paths = first.days[0].files
    assert first.output_bytes == sum(path.stat().st_size for path in paths)
    before = [path.stat().st_mtime_ns for path in paths]
    second = list(batch.grid_months(config, workers=2))[0]
    assert second.passed and second.resumed_days == 1 and second.processed_days == 0
    assert [path.stat().st_mtime_ns for path in paths] == before
    report = json.loads(second.metrics_path.read_text())
    assert report["config_sha256"] == config_sha256(config)
    assert report["processing_sha256"] == processing_sha256(config)
    assert report["granules_per_second"] is None
    assert report["resumed_days"] == 1
    with netCDF4.Dataset(paths[0]) as dataset:
        assert dataset.config_sha256 == config_sha256(config)
        assert dataset.processing_sha256 == processing_sha256(config)


def test_failed_day_preserves_successful_outputs_and_failed_month_metrics(pipeline_config):
    config, _ = pipeline_config
    result = list(batch.grid_months(config))[0]
    assert not result.passed
    assert result.processed_days == 1 and result.output_files == 8
    assert result.summary_path is None
    assert all(path.is_file() for path in result.days[0].files)
    report = json.loads(result.metrics_path.read_text())
    assert report["failed_days"] == 1 and report["days"][1]["error"]
    again = list(batch.grid_months(config))[0]
    assert again.resumed_days == 1
    assert not again.passed


def test_later_month_runs_after_failure_without_rewriting_original_config(
    pipeline_config, monkeypatch, tmp_path
):
    config, _ = pipeline_config
    original = Period(start=date(2020, 1, 31), end=date(2020, 2, 1))
    config = config.model_copy(update={"period": original})
    observed, summarized = [], []

    def run(cfg, day, overwrite):
        observed.append((cfg, day, overwrite))
        if day.month == 1:
            return DayResult(day=day, error="deliberate January failure")
        path = tmp_path / "retained.nc"
        path.write_bytes(b"synthetic output")
        return DayResult(day=day, files=[path], source_granule_count=7)

    def summarize(cfg):
        summarized.append(cfg)
        target = batch._summary_path(cfg, cfg.period)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("synthetic summary\n")
        return target, []

    monkeypatch.setattr(batch, "_run_day", run)
    monkeypatch.setattr(batch, "summarize", summarize)
    results = list(batch.grid_months(config, overwrite=True))
    assert [result.passed for result in results] == [False, True]
    assert all(cfg is config and overwrite for cfg, _, overwrite in observed)
    assert summarized[0].period == Period(start=date(2020, 2, 1), end=date(2020, 2, 1))
    assert summarized[0].name == config.name and summarized[0].paths == config.paths
    assert processing_sha256(summarized[0]) == processing_sha256(config)
    assert all(
        json.loads(result.metrics_path.read_text())["config_sha256"] == config_sha256(config)
        for result in results
    )


@pytest.mark.parametrize("template", ["{data_root}/summary.tsv", "{data_root}/summary.{start}"])
def test_colliding_monthly_summary_or_metrics_paths_fail_before_writes(
    pipeline_config, monkeypatch, template
):
    config, _ = pipeline_config
    config = config.model_copy(
        update={
            "period": Period(start=date(2020, 1, 31), end=date(2020, 2, 1)),
            "paths": config.paths.model_copy(update={"summary": template}),
        }
    )

    def unexpected(*args, **kwargs):
        pytest.fail("collision validation must precede any grid work")

    monkeypatch.setattr(batch, "_run_day", unexpected)
    with pytest.raises(ValueError, match="distinct monthly"):
        list(batch.grid_months(config))


def test_summary_failure_marks_month_failed_and_keeps_grid_files(pipeline_config, monkeypatch):
    config, _ = pipeline_config
    config = config.model_copy(
        update={"period": Period(start=date(2008, 7, 1), end=date(2008, 7, 1))}
    )

    def fail_summary(_):
        raise OSError("summary disk unavailable")

    monkeypatch.setattr(batch, "summarize", fail_summary)
    result = list(batch.grid_months(config))[0]
    assert not result.passed and result.output_files == 8
    assert "summary disk unavailable" in result.failures[0]
    assert json.loads(result.metrics_path.read_text())["passed"] is False


def test_cli_reports_failed_month_and_returns_failure(pipeline_config):
    _, path = pipeline_config
    app = typer.Typer()
    app.command("grid-months")(batch.grid_months_command)
    result = CliRunner().invoke(app, ["-c", str(path)])
    assert result.exit_code == 1, result.output
    assert "FAILED" in result.output and "8 files" in result.output
    assert "metrics:" in result.output
