"""Resumable, paced AERONET Web Service v3 daily acquisition.

Each cache is an unmodified all-site response for one product and UTC day.
Site selections are validated against the official catalog and applied when
reading, so runs selecting different sites safely share the same cache.
Transport, sleeping, and the monotonic clock are injectable for offline tests.

Web-service protocol: https://aeronet.gsfc.nasa.gov/print_web_data_help_v3.html
The daily end bound is the next day at 00 UTC; day2=D/hour2=23 loses late data.
"""

from __future__ import annotations

import csv
import hashlib
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Literal, TypeVar
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd
from pydantic import Field

from sarb_aod.aeronet.parse import AERONET_PREAMBLE, parse_aeronet, read_aeronet
from sarb_aod.config.schema import RunConfig, StrictModel
from sarb_aod.errors import SarbAodError
from sarb_aod.paths import aeronet_path

DATA_URL = "https://aeronet.gsfc.nasa.gov/cgi-bin/print_web_data_v3"
LOCATIONS_URL = "https://aeronet.gsfc.nasa.gov/aeronet_locations_v3.txt"
Product = Literal["AOD10", "AOD15", "AOD20"]
Average = Literal["all_points", "daily"]
_LEVELS = {"AOD10": "1.0", "AOD15": "1.5", "AOD20": "2.0"}
_AVERAGES = {"all_points": 10, "daily": 20}
_T = TypeVar("_T")


class AeronetFetchError(SarbAodError):
    """An AERONET request, response, or requested site is invalid."""


def build_url(
    day: date,
    *,
    product: Product = "AOD20",
    avg: Average = "all_points",
    site: str | None = None,
) -> str:
    """One UTC day, including all 23 UTC measurements; optional direct site query.

    Staging always omits ``site`` because cache paths represent all-site files.
    The optional argument is useful for direct service checks and small probes.
    """
    if product not in _LEVELS or avg not in _AVERAGES:
        raise ValueError("Unsupported AERONET product or averaging mode")
    end = day + timedelta(days=1)
    query: dict[str, str | int] = {
        "year": day.year,
        "month": day.month,
        "day": day.day,
        "hour": 0,
        "year2": end.year,
        "month2": end.month,
        "day2": end.day,
        "hour2": 0,
        product: 1,
        "AVG": _AVERAGES[avg],
        "if_no_html": 1,
    }
    if site is not None:
        if not site.strip():
            raise ValueError("AERONET site cannot be blank")
        query["site"] = site
    return DATA_URL + "?" + urlencode(query)


def fetch_url(url: str) -> bytes:
    """Read one response with a bounded timeout, using only the stdlib."""
    request = Request(url, headers={"User-Agent": "sarb-aod/0.1 (AERONET daily cache)"})
    with urlopen(request, timeout=60) as response:
        return response.read()


@dataclass
class AeronetHooks:
    fetch: Callable[[str], bytes] = fetch_url
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.monotonic


@dataclass(frozen=True)
class ResponseInfo:
    nbytes: int
    rows: int
    sites: int
    sha256: str

    @property
    def empty(self) -> bool:
        return self.rows == 0


def validate_response(body: bytes, *, product: Product, day: date) -> ResponseInfo:
    """Check transport structure and parse rows before a response may be cached."""
    if not body.endswith(b"\n"):
        raise AeronetFetchError("AERONET response is truncated: no terminal newline")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AeronetFetchError("AERONET response is not UTF-8 text") from exc
    lines = text.splitlines()
    if not lines or lines[0] != AERONET_PREAMBLE:
        raise AeronetFetchError("AERONET response has the wrong direct-sun preamble")
    if len(lines) > 1:
        if len(lines) < 6 or lines[2] != f"Version 3: AOD Level {_LEVELS[product]}":
            raise AeronetFetchError(f"AERONET response does not identify requested {product}")
    try:
        # Parse every row before filtering; malformed adjacent-day rows also fail.
        frame = parse_aeronet(text)
    except (ValueError, csv.Error) as exc:
        raise AeronetFetchError(f"Invalid AERONET response: {exc}") from exc
    if not frame.empty and not frame["aeronet_level"].eq(product).all():
        raise AeronetFetchError(f"AERONET rows contain a level other than requested {product}")
    frame = frame.loc[frame["time"].dt.date == day]
    return ResponseInfo(
        nbytes=len(body),
        rows=len(frame),
        sites=int(frame["AERONET_Site"].nunique()),
        sha256=hashlib.sha256(body).hexdigest(),
    )


def parse_locations(body: bytes) -> frozenset[str]:
    """Read valid site identifiers from the official v3 location table."""
    if not body.endswith(b"\n"):
        raise AeronetFetchError("AERONET locations response lacks a terminal newline")
    try:
        lines = body.decode("utf-8").splitlines()
        if not lines or not lines[0].startswith("AERONET_Database_Site_List,"):
            raise ValueError("wrong site-list preamble")
        rows = csv.reader(lines[1:])
        header = next(rows)
        index = header.index("Site_Name")
        if len(header) != 4:
            raise ValueError("wrong site-list header width")
        sites = set()
        for row in rows:
            if len(row) != len(header) or not row[index].strip():
                raise ValueError("malformed site-list row")
            sites.add(row[index].strip())
        if not sites:
            raise ValueError("empty site list")
        return frozenset(sites)
    except (UnicodeDecodeError, ValueError, csv.Error, StopIteration) as exc:
        raise AeronetFetchError(f"Invalid AERONET locations response: {exc}") from exc


class _Requester:
    def __init__(self, hooks: AeronetHooks, *, interval: float, retries: int):
        self.hooks = hooks
        self.interval = interval
        self.retries = retries
        self.last_request: float | None = None

    def get(self, url: str, validate: Callable[[bytes], _T]) -> tuple[bytes, _T]:
        """Rate-limit request starts, including retries and location lookups."""
        failure: Exception | None = None
        for _ in range(self.retries + 1):
            if self.last_request is not None:
                delay = self.interval - (self.hooks.now() - self.last_request)
                if delay > 0:
                    self.hooks.sleep(delay)
            self.last_request = self.hooks.now()
            try:
                body = self.hooks.fetch(url)
                return body, validate(body)
            except Exception as exc:
                failure = exc
        raise AeronetFetchError(
            f"AERONET request failed after {self.retries + 1} attempts: {failure}"
        ) from failure


class AeronetManifest(StrictModel):
    """One validated raw response and its requested-day observation counts."""

    version: Literal[1] = 1
    day: date
    product: Product
    avg: Average
    url: str
    nbytes: int = Field(gt=0)
    rows: int = Field(ge=0)
    sites: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    empty: bool


def manifest_path(cache: Path) -> Path:
    """A per-day sidecar beside the configured cache, independent of run length."""
    return cache.with_name(cache.name + ".manifest.json")


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _verify_cache(cfg: RunConfig, day: date) -> AeronetManifest:
    path = aeronet_path(cfg, day)
    manifest = AeronetManifest.model_validate_json(manifest_path(path).read_bytes())
    expected = (day, cfg.aeronet.product, cfg.aeronet.avg)
    if (manifest.day, manifest.product, manifest.avg) != expected:
        raise AeronetFetchError("AERONET manifest date, product, or averaging mode mismatch")
    if manifest.url != build_url(day, product=cfg.aeronet.product, avg=cfg.aeronet.avg):
        raise AeronetFetchError("AERONET manifest URL does not identify the all-site request")
    info = validate_response(path.read_bytes(), product=cfg.aeronet.product, day=day)
    for key in ("nbytes", "sha256", "rows", "sites", "empty"):
        if getattr(info, key) != getattr(manifest, key):
            raise AeronetFetchError(f"AERONET cache {key} disagrees with its manifest")
    return manifest


@dataclass
class StageSummary:
    days: int = 0
    already_present: int = 0
    downloaded: int = 0
    empty: int = 0
    rows: int = 0
    failed: list[str] = field(default_factory=list)


def stage(
    cfg: RunConfig,
    *,
    dry_run: bool = False,
    hooks: AeronetHooks | None = None,
    log: Callable[[str], None] = print,
) -> StageSummary:
    """Acquire daily all-site caches, resuming only checksum-verified entries.

    ``retries`` counts additional attempts after the initial request. A dry run
    inspects local caches and reports planned URLs, writing nothing. Configured
    site names are checked against the service even in a dry run.
    """
    requester = _Requester(
        hooks or AeronetHooks(), interval=cfg.aeronet.min_interval_s, retries=cfg.aeronet.retries
    )
    if cfg.aeronet.sites is not None:
        _, known = requester.get(LOCATIONS_URL, parse_locations)
        unknown = set(cfg.aeronet.sites) - known
        if unknown:
            raise AeronetFetchError(f"Unknown AERONET sites: {', '.join(sorted(unknown))}")
    summary = StageSummary()
    for day in cfg.period.days():
        summary.days += 1
        path = aeronet_path(cfg, day)
        try:
            existing = _verify_cache(cfg, day)
        except (OSError, ValueError, AeronetFetchError):
            existing = None
        if existing is not None:
            summary.already_present += 1
            summary.rows += existing.rows
            summary.empty += int(existing.empty)
            log(f"AERONET {cfg.aeronet.product} {day}: verified {existing.rows} rows in {path}")
            continue
        url = build_url(day, product=cfg.aeronet.product, avg=cfg.aeronet.avg)
        if dry_run:
            log(f"AERONET {day}: would fetch {url} -> {path}")
            continue
        try:
            body, info = requester.get(
                url, lambda body: validate_response(body, product=cfg.aeronet.product, day=day)
            )
            manifest = AeronetManifest(
                day=day,
                product=cfg.aeronet.product,
                avg=cfg.aeronet.avg,
                url=url,
                nbytes=info.nbytes,
                rows=info.rows,
                sites=info.sites,
                sha256=info.sha256,
                empty=info.empty,
            )
            _atomic_write(path, body)
            _atomic_write(manifest_path(path), (manifest.model_dump_json(indent=2) + "\n").encode())
        except (OSError, ValueError, AeronetFetchError) as exc:
            summary.failed.append(f"{day}: {exc}")
            log(f"AERONET {day}: failed: {exc}")
            continue
        summary.downloaded += 1
        summary.empty += int(info.empty)
        summary.rows += info.rows
        log(f"AERONET {cfg.aeronet.product} {day}: cached {info.rows} rows in {path}")
    return summary


@dataclass(frozen=True)
class AuditResult:
    day: date
    path: Path
    rows: int
    problem: str | None = None


def audit(cfg: RunConfig) -> list[AuditResult]:
    """Verify all configured days offline; missing manifests are audit failures."""
    results = []
    for day in cfg.period.days():
        try:
            manifest = _verify_cache(cfg, day)
        except (OSError, ValueError, AeronetFetchError) as exc:
            results.append(AuditResult(day, aeronet_path(cfg, day), 0, str(exc)))
        else:
            results.append(AuditResult(day, aeronet_path(cfg, day), manifest.rows))
    return results


def read_cached_day(cfg: RunConfig, day: date) -> pd.DataFrame:
    """Load a verified cache, retaining the configured day and optional sites."""
    _verify_cache(cfg, day)
    frame = read_aeronet(aeronet_path(cfg, day), day=day)
    if cfg.aeronet.sites is not None:
        frame = frame.loc[frame["AERONET_Site"].isin(cfg.aeronet.sites)]
    return frame.reset_index(drop=True)
