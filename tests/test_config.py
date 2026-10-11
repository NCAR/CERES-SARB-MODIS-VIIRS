import hashlib
import json
import pickle
from datetime import date
from pathlib import Path

import pytest

from sarb_aod.config.loader import (
    PROFILE_DIR_ENV,
    PROFILE_ENV,
    configuration_metadata,
    expand_string,
    load_config,
)
from sarb_aod.config.schema import load_catalog
from sarb_aod.errors import ConfigurationError

RUN = """\
period: {start: 2008-07-01, end: 2008-07-31}
stage:
  products:
    MOD04_L2: ["6.1", "7"]
    MYD04_L2: ["6.1"]
    AERDB_L2_VIIRS_NOAA20: ["2", "2.1"]
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(PROFILE_ENV, raising=False)
    monkeypatch.delenv(PROFILE_DIR_ENV, raising=False)


@pytest.fixture
def layout(tmp_path):
    """config/profiles/{dev,prod}.yaml and config/runs/ like the repo."""
    write(tmp_path / "config/profiles/dev.yaml", "paths:\n  data_root: /dev/data\n")
    write(
        tmp_path / "config/profiles/prod.yaml",
        "paths:\n  data_root: ${PROD_ROOT}\n  granule_dir: '{data_root}/{short_name}/{year}/{doy}'\n",
    )
    return tmp_path


def test_profile_supplies_paths(layout):
    run = write(layout / "config/runs/r.yaml", "profile: dev\n" + RUN)
    cfg = load_config(run)
    assert cfg.profile == "dev"
    assert cfg.paths.data_root == Path("/dev/data")
    assert cfg.period.start == date(2008, 7, 1)
    assert len(list(cfg.period.days())) == 31


def test_run_config_overrides_profile(layout):
    run = write(
        layout / "config/runs/r.yaml", "profile: dev\npaths: {data_root: /elsewhere}\n" + RUN
    )
    assert load_config(run).paths.data_root == Path("/elsewhere")


def test_profile_precedence_argument_then_env(layout, monkeypatch):
    monkeypatch.setenv("PROD_ROOT", "/prod")
    run = write(layout / "config/runs/r.yaml", "profile: dev\n" + RUN)
    monkeypatch.setenv(PROFILE_ENV, "prod")
    assert load_config(run).paths.data_root == Path("/prod")
    assert load_config(run, profile="dev").paths.data_root == Path("/dev/data")


def test_config_provenance_snapshots_selected_files_and_effective_overrides(layout, monkeypatch):
    from sarb_aod.pipeline import config_sha256

    monkeypatch.setenv("PROD_ROOT", "/production/data")
    run = write(layout / "config/runs/r.yaml", "# Original Run\nprofile: dev\n" + RUN)
    profile = layout / "config/profiles/prod.yaml"
    originals = {str(path.resolve()): path.read_bytes() for path in (run, profile)}
    config = load_config(run, profile="prod")
    # Later filesystem changes cannot rewrite the configuration history.
    run.write_text("# Changed after loading\n")
    profile.unlink()
    changed = pickle.loads(pickle.dumps(config.model_copy(update={"name": "runtime_override"})))
    metadata = configuration_metadata(changed)
    effective = json.loads(metadata["config"])
    assert effective["name"] == "runtime_override"
    assert effective["profile"] == "prod"
    assert effective["paths"]["data_root"] == "/production/data"
    sources = json.loads(metadata["config_files"])
    assert [source["role"] for source in sources] == ["run", "profile"]
    for source in sources:
        original = originals[source["path"]]
        assert source["content"].encode("utf-8") == original
        assert source["sha256"] == hashlib.sha256(original).hexdigest()
    assert hashlib.sha256(metadata["config"].encode()).hexdigest() == config_sha256(changed)
    assert config_sha256(type(changed).model_validate(effective)) == config_sha256(changed)


def test_development_agent_is_recorded_without_changing_processing(layout):
    from sarb_aod.config.schema import DevelopmentAgent, ProvenanceConfig
    from sarb_aod.pipeline import processing_sha256

    run = write(
        layout / "config/runs/r.yaml",
        "profile: dev\n"
        + RUN
        + 'grid:\n  streams:\n    terra_dt: {product: MOD04_L2, collection: "6.1"}\n',
    )
    original = load_config(run)
    agent = DevelopmentAgent(
        tool="Codex",
        provider="OpenAI",
        model_name="example-model",
        model_version="example-snapshot",
        reasoning_effort="high",
    )
    declared = original.model_copy(
        update={"provenance": ProvenanceConfig(development_agents=[agent])}
    )
    attrs = configuration_metadata(declared)
    assert json.loads(attrs["development_agents"])[0] == agent.model_dump(mode="json")
    assert processing_sha256(original) == processing_sha256(declared)


def test_profile_dir_env_and_explicit_path(tmp_path, monkeypatch):
    elsewhere = write(tmp_path / "site/ami.yaml", "paths: {data_root: /ami}\n")
    run = write(tmp_path / "r.yaml", RUN)
    monkeypatch.setenv(PROFILE_DIR_ENV, str(elsewhere.parent))
    assert load_config(run, profile="ami").paths.data_root == Path("/ami")
    assert load_config(run, profile=str(elsewhere)).paths.data_root == Path("/ami")


def test_missing_profile_is_an_error(layout):
    run = write(layout / "config/runs/r.yaml", "profile: nowhere\n" + RUN)
    with pytest.raises(ConfigurationError, match="Profile 'nowhere' not found"):
        load_config(run)


def test_unset_environment_variable_is_an_error(layout):
    run = write(layout / "config/runs/r.yaml", "profile: prod\n" + RUN)
    with pytest.raises(ConfigurationError, match="PROD_ROOT"):
        load_config(run)


def test_expand_string():
    import os

    os.environ["SARB_TEST_ROOT"] = "/x"
    assert expand_string("${SARB_TEST_ROOT}/a") == "/x/a"
    assert expand_string("$SARB_TEST_ROOT/a") == "/x/a"
    assert expand_string("cost$5") == "cost$5"
    assert expand_string("~/Data") == str(Path.home() / "Data")


def test_duplicate_keys_rejected(tmp_path):
    run = write(tmp_path / "r.yaml", "paths: {data_root: /a}\npaths: {data_root: /b}\n" + RUN)
    with pytest.raises(ConfigurationError, match="duplicate key"):
        load_config(run)


def test_unknown_key_rejected(tmp_path):
    run = write(tmp_path / "r.yaml", "paths: {data_root: /a, data_rot: /b}\n" + RUN)
    with pytest.raises(ConfigurationError, match="data_rot"):
        load_config(run)


def test_unknown_template_placeholder_rejected(tmp_path):
    run = write(
        tmp_path / "r.yaml", "paths: {data_root: /a, granule_dir: '{data_root}/{yr}'}\n" + RUN
    )
    with pytest.raises(ConfigurationError, match="yr"):
        load_config(run)


def test_unknown_collection_rejected(tmp_path):
    text = RUN.replace('["6.1", "7"]', '["6.1", "8"]')  # MOD04_L2
    run = write(tmp_path / "r.yaml", "paths: {data_root: /a}\n" + text)
    with pytest.raises(ConfigurationError, match="no collection '8'"):
        load_config(run)


def test_reversed_period_rejected(tmp_path):
    text = RUN.replace("end: 2008-07-31", "end: 2008-06-30")
    run = write(tmp_path / "r.yaml", "paths: {data_root: /a}\n" + text)
    with pytest.raises(ConfigurationError, match="before period.start"):
        load_config(run)


def test_packaged_catalog():
    catalog = load_catalog()
    c61 = catalog.collection("MOD04_L2", "6.1")
    assert (c61.cmr_version, c61.file_token, c61.file_format) == ("6.1", "061", "hdf4")
    c7 = catalog.collection("MYD04_L2", "7")
    assert (c7.cmr_version, c7.file_token, c7.file_format) == ("7", "007", "netcdf4")
    assert catalog.product("MYD04_L2").platform == "Aqua"


def test_repo_run_config_loads():
    repo = Path(__file__).resolve().parents[1]
    cfg = load_config(repo / "config/runs/modis_l2_200807.yaml")
    assert cfg.profile == "mac"
    assert cfg.paths.data_root == Path.home() / "Data"


def test_selections_follow_config_order(tmp_path):
    run = write(tmp_path / "r.yaml", "paths: {data_root: /a}\n" + RUN)
    assert load_config(run).stage.selections() == [
        ("MOD04_L2", "6.1"),
        ("MOD04_L2", "7"),
        ("MYD04_L2", "6.1"),
        ("AERDB_L2_VIIRS_NOAA20", "2"),
        ("AERDB_L2_VIIRS_NOAA20", "2.1"),
    ]


def test_product_without_collections_rejected(tmp_path):
    text = RUN.replace('MYD04_L2: ["6.1"]', "MYD04_L2: []")
    run = write(tmp_path / "r.yaml", "paths: {data_root: /a}\n" + text)
    with pytest.raises(ConfigurationError, match="no collections"):
        load_config(run)


def test_viirs_catalog_entries():
    catalog = load_catalog()
    assert catalog.collection("AERDB_L2_VIIRS_SNPP", "2").file_token == "002"
    assert catalog.collection("AERDB_L2_VIIRS_NOAA20", "2.1").file_token == "021"
    assert catalog.collection("AERDT_L2_VIIRS_SNPP", "2.1").file_format == "netcdf4"
    assert catalog.product("AERDT_L2_VIIRS_NOAA20").platform == "NOAA-20"
    with pytest.raises(KeyError):
        catalog.collection("AERDT_L2_VIIRS_SNPP", "2")


def test_repo_september_2020_config_loads():
    repo = Path(__file__).resolve().parents[1]
    cfg = load_config(repo / "config/runs/modis_viirs_l2_202009.yaml")
    assert len(cfg.stage.selections()) == 9
