"""AERONET transport, complete-day requests, cache integrity, and pacing."""

import json
import threading
from dataclasses import dataclass, field
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import yaml
from typer.testing import CliRunner

from sarb_aod.aeronet import fetch
from sarb_aod.aeronet.fetch import (
    AeronetFetchError,
    AeronetHooks,
    AeronetManifest,
    audit,
    build_url,
    fetch_url,
    manifest_path,
    parse_locations,
    read_cached_day,
    stage,
    validate_response,
)
from sarb_aod.aeronet.parse import AERONET_PREAMBLE, parse_aeronet
from sarb_aod.cli import app
from sarb_aod.config.schema import AeronetConfig, PathsConfig, RunConfig
from sarb_aod.paths import aeronet_path

FIXTURES = Path(__file__).parent / "data" / "aeronet"
GSFC = (FIXTURES / "AERONET_AOD20_ALLPOINTS_20080701_GSFC.csv").read_bytes()
DAY = date(2008, 7, 1)
LOCATIONS = (
    b"AERONET_Database_Site_List,Num=2,Date_Generated=10:10:2026\n"
    b"Site_Name,Longitude(decimal_degrees),Latitude(decimal_degrees),Elevation(meters)\n"
    b"GSFC,-76.84,38.99,87\nAvignon,4.88,43.93,32\n"
)


def config(tmp_path: Path, **aeronet: object) -> RunConfig:
    return RunConfig.model_validate(
        {
            "paths": {"data_root": str(tmp_path)},
            "period": {"start": DAY, "end": DAY},
            "stage": {"products": {"MOD04_L2": ["6.1"]}},
            "aeronet": {"retries": 0, **aeronet},
        }
    )


@dataclass
class Clock:
    elapsed: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def now(self) -> float:
        return self.elapsed

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.elapsed += seconds


@pytest.mark.parametrize(
    "day,end",
    [
        (date(2008, 7, 1), (2008, 7, 2)),
        (date(2008, 7, 31), (2008, 8, 1)),
        (date(2020, 12, 31), (2021, 1, 1)),
    ],
)
def test_request_uses_exclusive_next_midnight(day: date, end: tuple[int, int, int]) -> None:
    query = parse_qs(urlparse(build_url(day)).query)
    assert query["hour"] == ["0"]
    assert query["hour2"] == ["0"]
    assert tuple(int(query[key][0]) for key in ("year2", "month2", "day2")) == end
    assert query["AVG"] == ["10"]
    assert query["AOD20"] == ["1"]
    assert "site" not in query
    daily = parse_qs(urlparse(build_url(day, product="AOD15", avg="daily", site="GSFC")).query)
    assert daily["AOD15"] == ["1"] and "AOD20" not in daily
    assert daily["AVG"] == ["20"] and daily["site"] == ["GSFC"]


def test_response_counts_full_day_and_empty() -> None:
    info = validate_response(GSFC, product="AOD20", day=DAY)
    assert (info.rows, info.sites, info.nbytes) == (34, 1, len(GSFC))
    assert not info.empty
    assert len(info.sha256) == 64
    # Daily responses may contain neighbors: counts always refer to requested D.
    assert validate_response(GSFC, product="AOD20", day=date(2008, 7, 2)).empty
    empty = validate_response((AERONET_PREAMBLE + "\n").encode(), product="AOD20", day=DAY)
    assert empty.empty and empty.rows == empty.sites == 0


@pytest.mark.parametrize(
    "body,match",
    [
        (GSFC[:-1], "terminal newline"),
        (b"<html>server error</html>\n", "preamble"),
        (GSFC.replace(b"Version 3: AOD Level 2.0", b"Version 3: AOD Level 1.5"), "requested AOD20"),
        (GSFC.replace(b",lev20,", b",lev15,"), "other than requested AOD20"),
        (GSFC.rsplit(b",", 1)[0] + b"\n", "fields; expected"),
    ],
)
def test_bad_responses_fail_before_cache_write(body: bytes, match: str, tmp_path: Path) -> None:
    with pytest.raises(AeronetFetchError, match=match):
        validate_response(body, product="AOD20", day=DAY)
    cfg = config(tmp_path)
    result = stage(cfg, hooks=AeronetHooks(fetch=lambda _: body), log=lambda _: None)
    assert len(result.failed) == 1 and result.downloaded == 0
    assert not aeronet_path(cfg, DAY).exists()
    assert not manifest_path(aeronet_path(cfg, DAY)).exists()


@pytest.fixture
def server():
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(GSFC)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/", requests
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()


def test_stdlib_http_server_stage_resume_and_audit(server, tmp_path: Path) -> None:
    endpoint, requests = server
    hooks = AeronetHooks(fetch=lambda url: fetch_url(endpoint + "?" + urlparse(url).query))
    cfg = config(tmp_path)
    first = stage(cfg, hooks=hooks, log=lambda _: None)
    assert first.downloaded == 1 and not first.failed
    cached = aeronet_path(cfg, DAY)
    assert cached.read_bytes() == GSFC
    record = AeronetManifest.model_validate_json(manifest_path(cached).read_text())
    assert (record.rows, record.sites, record.empty) == (34, 1, False)
    second = stage(cfg, hooks=hooks, log=lambda _: None)
    assert second.already_present == 1 and second.downloaded == 0
    assert len(requests) == 1
    assert audit(cfg)[0].problem is None
    assert len(read_cached_day(cfg, DAY)) == 34
    assert not list(tmp_path.rglob("*.tmp"))


def test_empty_day_is_recorded_and_never_refetched(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    calls = []

    def get(url: str) -> bytes:
        calls.append(url)
        return (AERONET_PREAMBLE + "\n").encode()

    for _ in range(2):
        summary = stage(cfg, hooks=AeronetHooks(fetch=get), log=lambda _: None)
        assert summary.empty == 1
    assert len(calls) == 1
    assert audit(cfg)[0].problem is None
    assert read_cached_day(cfg, DAY).empty


def test_checksum_corruption_is_detected_and_repaired(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    hooks = AeronetHooks(fetch=lambda _: GSFC)
    stage(cfg, hooks=hooks, log=lambda _: None)
    path = aeronet_path(cfg, DAY)
    # Same byte count and parseable data: a checksum must catch this corruption.
    altered = GSFC.replace(b"GSFC,", b"GSFD,")
    assert len(altered) == len(GSFC)
    path.write_bytes(altered)
    assert "sha256" in audit(cfg)[0].problem
    summary = stage(cfg, hooks=hooks, log=lambda _: None)
    assert summary.downloaded == 1 and summary.already_present == 0
    assert path.read_bytes() == GSFC


def test_missing_or_tampered_manifests_are_audit_failures(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    assert audit(cfg)[0].problem is not None
    stage(cfg, hooks=AeronetHooks(fetch=lambda _: GSFC), log=lambda _: None)
    sidecar = manifest_path(aeronet_path(cfg, DAY))
    document = json.loads(sidecar.read_text())
    document["avg"] = "daily"
    sidecar.write_text(json.dumps(document))
    assert "mismatch" in audit(cfg)[0].problem
    sidecar.write_text("not json")
    assert audit(cfg)[0].problem is not None


def test_failed_refetch_preserves_existing_bytes(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    path = aeronet_path(cfg, DAY)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"old incomplete file")
    result = stage(
        cfg, hooks=AeronetHooks(fetch=lambda _: b"<html>error</html>\n"), log=lambda _: None
    )
    assert result.failed
    assert path.read_bytes() == b"old incomplete file"
    assert not list(path.parent.glob("*.tmp"))


def test_pacing_spans_location_lookup_retry_and_days(tmp_path: Path) -> None:
    cfg = config(tmp_path, sites=["GSFC"], retries=1)
    cfg = cfg.model_copy(update={"period": cfg.period.model_copy(update={"end": date(2008, 7, 2)})})
    clock = Clock()
    starts: list[float] = []

    def get(url: str) -> bytes:
        starts.append(clock.now())
        if url == fetch.LOCATIONS_URL:
            return LOCATIONS
        assert "site=" not in url  # cached responses always contain all sites
        if len(starts) == 2:
            raise OSError("temporary failure")
        if parse_qs(urlparse(url).query)["day"] == ["2"]:
            return GSFC.replace(b"01:07:2008", b"02:07:2008")
        return GSFC

    summary = stage(cfg, hooks=AeronetHooks(get, clock.sleep, clock.now), log=lambda _: None)
    assert not summary.failed and summary.downloaded == 2
    assert starts == [0, 6, 12, 18]
    assert clock.sleeps == [6, 6, 6]


def test_elapsed_request_time_counts_toward_pacing(tmp_path: Path) -> None:
    cfg = config(tmp_path, sites=["GSFC"])
    clock = Clock()

    def get(url: str) -> bytes:
        clock.elapsed += 9
        return LOCATIONS if url == fetch.LOCATIONS_URL else GSFC

    summary = stage(cfg, hooks=AeronetHooks(get, clock.sleep, clock.now), log=lambda _: None)
    assert not summary.failed
    assert clock.sleeps == []


def test_unknown_site_rejected_and_site_selection_does_not_change_cache(tmp_path: Path) -> None:
    cfg = config(tmp_path, sites=["Does_Not_Exist"])
    with pytest.raises(AeronetFetchError, match="Unknown AERONET sites"):
        stage(cfg, hooks=AeronetHooks(fetch=lambda _: LOCATIONS), log=lambda _: None)
    assert not aeronet_path(cfg, DAY).exists()
    cfg = config(tmp_path)
    stage(cfg, hooks=AeronetHooks(fetch=lambda _: GSFC), log=lambda _: None)
    selected = config(tmp_path, sites=["Avignon"])
    assert aeronet_path(selected, DAY) == aeronet_path(cfg, DAY)
    assert read_cached_day(selected, DAY).empty
    assert len(read_cached_day(cfg, DAY)) == 34


def test_location_list_validation() -> None:
    assert parse_locations(LOCATIONS) == {"GSFC", "Avignon"}
    for bad in (b"<html>Error</html>\n", LOCATIONS[:-1], LOCATIONS.rsplit(b",", 1)[0] + b"\n"):
        with pytest.raises(AeronetFetchError):
            parse_locations(bad)


def test_dry_run_has_no_writes_or_data_requests(tmp_path: Path) -> None:
    def forbidden(url: str) -> bytes:
        raise AssertionError(f"unexpected fetch {url}")

    cfg = config(tmp_path)
    messages: list[str] = []
    result = stage(cfg, dry_run=True, hooks=AeronetHooks(fetch=forbidden), log=messages.append)
    assert result.days == 1 and result.downloaded == 0
    assert "would fetch" in messages[0]
    assert not list(tmp_path.iterdir())


def test_cache_templates_and_config_validation(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    assert (
        aeronet_path(cfg, DAY)
        == tmp_path / "AERONET/AOD20/2008/07/01/AERONET_AOD20_ALLPOINTS_20080701.csv"
    )
    custom = cfg.model_copy(
        update={
            "paths": PathsConfig(
                data_root=tmp_path, aeronet="{data_root}/{product}/{avg}/{year}-{doy}.csv"
            ),
            "aeronet": AeronetConfig(product="AOD15", avg="daily"),
        }
    )
    assert aeronet_path(custom, DAY) == tmp_path / "AOD15/DAILY/2008-183.csv"
    for template in (
        "{data_root}/{unknown}",
        "{data_root}/{product}/{yyyymmdd}.csv",
        "{data_root}/{product}/{avg}.csv",
    ):
        with pytest.raises(ValueError):
            PathsConfig(data_root=tmp_path, aeronet=template)
    for kwargs in (
        {"sites": []},
        {"sites": [" "]},
        {"sites": ["GSFC", "GSFC"]},
        {"retries": -1},
        {"min_interval_s": 5},
    ):
        with pytest.raises(ValueError):
            AeronetConfig.model_validate(kwargs)


def test_cli_stage_audit_and_missing_day_failure(tmp_path: Path, monkeypatch) -> None:
    cfg = config(tmp_path)
    run = tmp_path / "run.yaml"
    run.write_text(yaml.safe_dump(cfg.model_dump(mode="json")))
    monkeypatch.setattr(
        fetch,
        "stage",
        lambda config, **kwargs: stage(config, hooks=AeronetHooks(fetch=lambda _: GSFC), **kwargs),
    )
    runner = CliRunner()
    missing = runner.invoke(app, ["aeronet", "audit", "-c", str(run)])
    assert missing.exit_code == 1
    staged = runner.invoke(app, ["aeronet", "stage", "-c", str(run)])
    assert staged.exit_code == 0, staged.output
    assert "downloaded=1" in staged.output
    checked = runner.invoke(app, ["aeronet", "audit", "-c", str(run)])
    assert checked.exit_code == 0 and "34 rows OK" in checked.output


@pytest.mark.network
def test_live_gsfc_full_utc_day() -> None:
    body = fetch_url(build_url(DAY, site="GSFC"))
    info = validate_response(body, product="AOD20", day=DAY)
    assert info.rows > 0
    frame = parse_aeronet(body.decode("utf-8"))
    assert frame["time"].dt.hour.ge(23).any()
    assert frame["time"].dt.date.eq(DAY).all()
    assert frame["AERONET_Site"].eq("GSFC").all()
