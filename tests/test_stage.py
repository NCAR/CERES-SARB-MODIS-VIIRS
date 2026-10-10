from datetime import date
from pathlib import Path

import pytest

from sarb_aod.config.schema import PathsConfig, Period, RunConfig, StageConfig, load_catalog
from sarb_aod.stage.earthdata import GranuleRecord, acquisition_day, to_record
from sarb_aod.stage.manifest import read_manifest, verify_file
from sarb_aod.stage.runner import Hooks, audit, stage

HDF4_MAGIC = b"\x0e\x03\x13\x01"


def granule(name: str, size: int = 64) -> GranuleRecord:
    return GranuleRecord(
        name=name,
        short_name=name.split(".")[0],
        collection="6.1",
        day=acquisition_day(name),
        size_bytes=size,
        url=f"https://example.invalid/{name}",
    )


GRANULES = {
    "MOD04_L2": [
        granule("MOD04_L2.A2008182.2355.061.2017292033454.hdf"),  # June 30: outside period
        granule("MOD04_L2.A2008183.0020.061.2017292033454.hdf"),
        granule("MOD04_L2.A2008183.0025.061.2017292032841.hdf", size=128),
        granule("MOD04_L2.A2008184.0020.061.2017292033454.hdf"),
    ],
    "MYD04_L2": [],  # e.g. a collection not yet published
}


class FakeNetwork:
    def __init__(self, payload=None):
        self.logins = 0
        self.downloads: list[str] = []
        self.payload = payload  # bytes to write instead of a valid granule

    def login(self, strategy):
        self.logins += 1

    def search(self, short_name, collection, spec, provider, start, end):
        return list(GRANULES[short_name])

    def download(self, records, dest, threads):
        for record in records:
            self.downloads.append(record.name)
            body = self.payload if self.payload is not None else (
                HDF4_MAGIC + b"\0" * (record.size_bytes - len(HDF4_MAGIC))
            )
            (dest / record.name).write_bytes(body)

    def hooks(self):
        return Hooks(login=self.login, search=self.search, download=self.download)


@pytest.fixture
def cfg(tmp_path):
    return RunConfig(
        paths=PathsConfig(data_root=tmp_path),
        period=Period(start=date(2008, 7, 1), end=date(2008, 7, 2)),
        stage=StageConfig(products={"MOD04_L2": ["6.1"], "MYD04_L2": ["6.1"]}),
    )


def day_dir(root: Path, day: str) -> Path:
    return root / "Terra/MODIS/MOD04_L2.061/2008/07" / day


def test_acquisition_day():
    assert acquisition_day("MOD04_L2.A2008183.0020.061.2017292033454.hdf") == date(2008, 7, 1)
    assert acquisition_day("MYD04_L2.A2008366.2355.007.2025000000000.nc") == date(2008, 12, 31)
    with pytest.raises(ValueError):
        acquisition_day("no_token.hdf")


def test_stage_places_files_and_writes_manifest(cfg, tmp_path):
    net = FakeNetwork()
    summaries = stage(cfg, hooks=net.hooks(), log=lambda _: None)

    mod, myd = summaries
    assert (mod.granules, mod.downloaded, mod.failed) == (3, 3, [])
    assert not myd.available and myd.manifest is None
    assert sorted(p.name for p in day_dir(tmp_path, "01").iterdir()) == [
        "MOD04_L2.A2008183.0020.061.2017292033454.hdf",
        "MOD04_L2.A2008183.0025.061.2017292032841.hdf",
    ]
    assert [p.name for p in day_dir(tmp_path, "02").iterdir()] == [
        "MOD04_L2.A2008184.0020.061.2017292033454.hdf"
    ]
    assert not (day_dir(tmp_path, "01") / ".partial").exists()
    assert not (tmp_path / "Terra/MODIS/MOD04_L2.061/2008/06").exists()

    rows = read_manifest(mod.manifest)
    assert [r.date for r in rows] == [date(2008, 7, 1), date(2008, 7, 1), date(2008, 7, 2)]
    assert rows[1].size_bytes == 128
    assert rows[0].local_path == day_dir(tmp_path, "01") / rows[0].granule
    assert net.logins == 1


def test_rerun_is_idempotent_and_repairs_truncated_files(cfg, tmp_path):
    stage(cfg, hooks=FakeNetwork().hooks(), log=lambda _: None)
    truncated = day_dir(tmp_path, "01") / "MOD04_L2.A2008183.0025.061.2017292032841.hdf"
    truncated.write_bytes(HDF4_MAGIC)

    net = FakeNetwork()
    mod, _ = stage(cfg, hooks=net.hooks(), log=lambda _: None)
    assert net.downloads == [truncated.name]
    assert (mod.already_present, mod.downloaded) == (2, 1)
    assert truncated.stat().st_size == 128


def test_bad_downloads_are_reported_and_never_placed(cfg, tmp_path):
    net = FakeNetwork(payload=b"<html>Earthdata Login</html>")
    mod, _ = stage(cfg, hooks=net.hooks(), log=lambda _: None)
    assert mod.downloaded == 0 and len(mod.failed) == 3
    assert list(day_dir(tmp_path, "01").iterdir()) == []


def test_dry_run_writes_nothing(cfg, tmp_path):
    net = FakeNetwork()
    mod, _ = stage(cfg, dry_run=True, hooks=net.hooks(), log=lambda _: None)
    assert mod.granules == 3 and mod.manifest is None
    assert net.logins == 0 and net.downloads == []
    assert not (tmp_path / "Terra").exists()


def test_audit_flags_corruption(cfg, tmp_path):
    stage(cfg, hooks=FakeNetwork().hooks(), log=lambda _: None)
    (result,) = audit(cfg)
    assert (result.rows, result.ok, result.problems) == (3, 3, [])

    (day_dir(tmp_path, "02") / "MOD04_L2.A2008184.0020.061.2017292033454.hdf").unlink()
    (result,) = audit(cfg)
    assert result.ok == 2 and "missing" in result.problems[0]


def test_verify_file(tmp_path):
    path = tmp_path / "g.hdf"
    assert verify_file(path, 10, "hdf4", 1e-4) == "missing"
    path.write_bytes(HDF4_MAGIC + b"\0" * 6)
    assert verify_file(path, 10, "hdf4", 1e-4) is None
    assert verify_file(path, 0, "hdf4", 1e-4) is None  # size unknown: format check only
    assert "size" in verify_file(path, 20, "hdf4", 1e-4)
    assert "not a netcdf4" in verify_file(path, 10, "netcdf4", 1e-4)


class FakeDataGranule(dict):
    def __init__(self, links, infos):
        super().__init__(umm={"DataGranule": {"ArchiveAndDistributionInformation": infos}})
        self._links = links

    def data_links(self):
        return self._links


def test_to_record_reads_umm_size_with_units():
    spec = load_catalog().collection("MOD04_L2", "6.1")
    name = "MOD04_L2.A2008183.0020.061.2017292033454.hdf"
    fake = FakeDataGranule(
        [f"https://host/prod-lads/MOD04_L2/{name}.xml", f"https://host/prod-lads/MOD04_L2/{name}"],
        [{"Name": name, "Size": 0.653202056884766, "SizeUnit": "MB"}],
    )
    record = to_record(fake, "MOD04_L2", "6.1", spec)
    assert record.name == name
    assert record.day == date(2008, 7, 1)
    assert record.size_bytes == 684932  # matches the LAADS byte count for this granule


def test_product_and_collection_filters(cfg):
    net = FakeNetwork()
    summaries = stage(cfg, dry_run=True, hooks=net.hooks(), products=["MYD04_L2"], log=lambda _: None)
    assert [(s.short_name, s.collection) for s in summaries] == [("MYD04_L2", "6.1")]
    assert stage(cfg, dry_run=True, hooks=net.hooks(), collections=["7"], log=lambda _: None) == []
