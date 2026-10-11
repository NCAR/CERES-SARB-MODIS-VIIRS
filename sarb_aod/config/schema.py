"""Pydantic schema for sarb_aod run configurations and the product catalog."""

from __future__ import annotations

import string
from datetime import date, timedelta
from importlib import resources
from pathlib import Path
from typing import Iterator, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Placeholders available to path templates. Granule templates are rendered per
# (product, collection, day); manifest templates per (product, collection, period).
GRANULE_FIELDS = frozenset(
    {
        "data_root",
        "platform",
        "instrument",
        "short_name",
        "collection",
        "cmr_version",
        "file_token",
        "year",
        "month",
        "day",
        "doy",
        "yyyymmdd",
    }
)
MANIFEST_FIELDS = (GRANULE_FIELDS - {"year", "month", "day", "doy", "yyyymmdd"}) | {
    "start",
    "end",
}
OUTPUT_FIELDS = frozenset(
    {"data_root", "run", "stem", "year", "month", "day", "doy", "yyyymmdd", "sssss"}
)
AERONET_FIELDS = frozenset(
    {"data_root", "product", "avg", "year", "month", "day", "doy", "yyyymmdd"}
)
PLOT_FIELDS = frozenset({"data_root", "run", "suite", "name", "start", "end"})


def template_fields(template: str) -> set[str]:
    """Return the placeholder names used in a ``str.format`` template."""
    return {field for _, field, _, _ in string.Formatter().parse(template) if field}


def _check_template(template: str, allowed: frozenset[str], label: str) -> str:
    unknown = template_fields(template) - allowed
    if unknown:
        raise ValueError(
            f"{label} uses unknown placeholder(s) {sorted(unknown)}; allowed: {sorted(allowed)}"
        )
    return template


class StrictModel(BaseModel):
    """Base model: unknown keys are errors, instances are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# --- product catalog -------------------------------------------------------


Algorithm = Literal["dt", "db"]
Surface = Literal["land", "ocean"]
AlgorithmFamily = Literal["modis_dt", "modis_db", "viirs_dt", "viirs_db"]


class VarRef(StrictModel):
    """An SDS or NetCDF group/variable, with optional named-dimension selection."""

    name: str = Field(min_length=1)
    select: dict[str, int] | None = None

    @field_validator("select")
    @classmethod
    def _named_nonnegative_indices(cls, value: dict[str, int] | None) -> dict[str, int] | None:
        if value is not None and any(not name or index < 0 for name, index in value.items()):
            raise ValueError("select requires nonempty dimension names and nonnegative indices")
        return value


class SurfaceVars(StrictModel):
    aod: VarRef
    qa: VarRef
    sigma: VarRef | None = None


class AlgorithmSpec(StrictModel):
    family: AlgorithmFamily
    surfaces: dict[Surface, SurfaceVars] = Field(min_length=1)
    alternates: dict[str, dict[Surface, SurfaceVars]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _alternate_surfaces(self) -> "AlgorithmSpec":
        for preset, surfaces in self.alternates.items():
            unknown = surfaces.keys() - self.surfaces.keys()
            if unknown:
                raise ValueError(f"alternate {preset!r} has undefined surfaces: {sorted(unknown)}")
        return self


class Geolocation(StrictModel):
    lat: str = Field(min_length=1)
    lon: str = Field(min_length=1)
    time: str | None
    time_kind: Literal["tai93", "coverage_attrs"]

    @model_validator(mode="after")
    def _time_source(self) -> "Geolocation":
        if self.time_kind == "tai93" and not self.time:
            raise ValueError("tai93 geolocation requires a time variable")
        if self.time_kind == "coverage_attrs" and self.time is not None:
            raise ValueError("coverage_attrs geolocation must use time: null")
        return self


class CombinedProductSpec(StrictModel):
    """Producer's combined AOD, independent of the DT/DB merge."""

    aod: VarRef
    qa: VarRef
    algorithm_flag: VarRef


class CollectionSpec(StrictModel):
    cmr_version: str
    file_token: str
    file_format: Literal["hdf4", "netcdf4"]
    geolocation: Geolocation | None = None
    algorithms: dict[Algorithm, AlgorithmSpec] = Field(default_factory=dict)
    combined_product: CombinedProductSpec | None = None

    @model_validator(mode="after")
    def _reader_metadata(self) -> "CollectionSpec":
        if self.algorithms and self.geolocation is None:
            raise ValueError("collections with algorithms require geolocation")
        for algorithm, spec in self.algorithms.items():
            if spec.family.rsplit("_", 1)[-1] != algorithm:
                raise ValueError(f"algorithm {algorithm!r} conflicts with family {spec.family!r}")
        return self


class ProductSpec(StrictModel):
    platform: str
    instrument: str
    provider: str = "LAADS"
    stream_prefix: str = Field(pattern=r"^[a-z][a-z0-9]*$")
    collections: dict[str, CollectionSpec]


class Catalog(StrictModel):
    products: dict[str, ProductSpec]

    def product(self, short_name: str) -> ProductSpec:
        try:
            return self.products[short_name]
        except KeyError:
            known = ", ".join(sorted(self.products))
            raise KeyError(f"Unknown product {short_name!r}; catalog has: {known}") from None

    def collection(self, short_name: str, collection: str) -> CollectionSpec:
        spec = self.product(short_name)
        try:
            return spec.collections[collection]
        except KeyError:
            known = ", ".join(sorted(spec.collections))
            raise KeyError(
                f"Product {short_name} has no collection {collection!r}; known: {known}"
            ) from None


def load_catalog(path: Path | None = None) -> Catalog:
    """Load the product catalog (packaged default unless ``path`` is given)."""
    if path is None:
        text = resources.files("sarb_aod").joinpath("data/catalog.yaml").read_text()
    else:
        text = Path(path).read_text()
    return Catalog.model_validate(yaml.safe_load(text))


# --- run configuration -----------------------------------------------------


DEFAULT_GRANULE_DIR = (
    "{data_root}/{platform}/{instrument}/{short_name}.{file_token}/{year}/{month}/{day}"
)
DEFAULT_MANIFEST = (
    "{data_root}/{platform}/{instrument}/{short_name}.{file_token}/manifests/"
    "manifest_{start}_{end}.tsv"
)
DEFAULT_OUTPUT = "{data_root}/CAM7/{stem}/{run}/{year}/{month}/{day}/{stem}.{yyyymmdd}.{sssss}.nc"
DEFAULT_AERONET = (
    "{data_root}/AERONET/{product}/{year}/{month}/{day}/" "AERONET_{product}_{avg}_{yyyymmdd}.csv"
)


class PathsConfig(StrictModel):
    """Machine-specific locations. Templates are Python ``str.format`` strings."""

    data_root: Path
    granule_dir: str = DEFAULT_GRANULE_DIR
    granule_pattern: str = "*"
    archive_read_only: bool = False
    manifest: str = DEFAULT_MANIFEST
    output: str = DEFAULT_OUTPUT
    evaluation_dir: str = "{data_root}/evaluation/{run}"
    plot: str = "{data_root}/plots/{run}/{suite}/{name}.{start}-{end}.pdf"

    @field_validator("granule_pattern")
    @classmethod
    def _granule_pattern(cls, value: str) -> str:
        _check_template(value, GRANULE_FIELDS - {"data_root"}, "paths.granule_pattern")
        if not value or "/" in value or "\\" in value or "**" in value:
            raise ValueError("granule_pattern must be a nonempty, nonrecursive basename glob")
        return value

    @field_validator("plot")
    @classmethod
    def _plot_fields(cls, value: str) -> str:
        _check_template(value, PLOT_FIELDS, "paths.plot")
        if not {"run", "suite", "name", "start", "end"} <= template_fields(value):
            raise ValueError("paths.plot must include {run}, {suite}, {name}, {start}, and {end}")
        if not value.endswith(".pdf"):
            raise ValueError("paths.plot must end in .pdf")
        return value

    @field_validator("evaluation_dir")
    @classmethod
    def _evaluation_fields(cls, value: str) -> str:
        return _check_template(value, frozenset({"data_root", "run"}), "paths.evaluation_dir")

    cartopy_data_dir: str = "{data_root}/cartopy"

    @field_validator("cartopy_data_dir")
    @classmethod
    def _cartopy_fields(cls, value: str) -> str:
        return _check_template(value, frozenset({"data_root"}), "paths.cartopy_data_dir")

    summary: str = "{data_root}/CAM7/{stem}/{run}/summary_{start}_{end}.tsv"

    @field_validator("summary")
    @classmethod
    def _summary_fields(cls, value: str) -> str:
        return _check_template(
            value, frozenset({"data_root", "run", "stem", "start", "end"}), "paths.summary"
        )

    aeronet: str = DEFAULT_AERONET

    @field_validator("aeronet")
    @classmethod
    def _aeronet_fields(cls, value: str) -> str:
        _check_template(value, AERONET_FIELDS, "paths.aeronet")
        fields = template_fields(value)
        dated = (
            "yyyymmdd" in fields or {"year", "month", "day"} <= fields or {"year", "doy"} <= fields
        )
        if not {"product", "avg"} <= fields or not dated:
            raise ValueError("paths.aeronet must include {product}, {avg}, and a complete date")
        return value

    @field_validator("output")
    @classmethod
    def _output_fields(cls, value: str) -> str:
        _check_template(value, OUTPUT_FIELDS, "paths.output")
        fields = template_fields(value)
        dated = (
            "yyyymmdd" in fields or {"year", "month", "day"} <= fields or {"year", "doy"} <= fields
        )
        if "sssss" not in fields or not dated:
            raise ValueError("paths.output must include {sssss} and a complete date")
        return value

    @field_validator("granule_dir")
    @classmethod
    def _granule_dir_fields(cls, value: str) -> str:
        return _check_template(value, GRANULE_FIELDS, "paths.granule_dir")

    @field_validator("manifest")
    @classmethod
    def _manifest_fields(cls, value: str) -> str:
        return _check_template(value, MANIFEST_FIELDS, "paths.manifest")


class Period(StrictModel):
    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self) -> "Period":
        if self.end < self.start:
            raise ValueError(f"period.end {self.end} is before period.start {self.start}")
        return self

    def days(self) -> Iterator[date]:
        day = self.start
        while day <= self.end:
            yield day
            day += timedelta(days=1)


class StageConfig(StrictModel):
    """Which granules to acquire and how.

    ``products`` maps each product short name to the collections to stage, e.g.
    ``{MOD04_L2: ["6.1", "7"], AERDB_L2_VIIRS_SNPP: ["2"]}``.
    """

    products: dict[str, list[str]] = Field(min_length=1)
    auth_strategy: Literal["netrc", "environment", "interactive", "all"] = "netrc"
    threads: int = Field(default=8, ge=1, le=32)
    # Relative tolerance between the CMR-listed size and the bytes on disk.
    size_rtol: float = Field(default=1e-4, ge=0.0, lt=0.1)

    @field_validator("products")
    @classmethod
    def _nonempty_collections(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        empty = [name for name, collections in value.items() if not collections]
        if empty:
            raise ValueError(f"stage.products lists no collections for {empty}")
        return value

    def selections(self) -> list[tuple[str, str]]:
        """``(short_name, collection)`` pairs in configuration order."""
        return [(name, c) for name, collections in self.products.items() for c in collections]


class StreamSelection(StrictModel):
    product: str
    collection: str


class GridConfig(StrictModel):
    """CAM f09 spatial grid and fixed three-hour windows."""

    source: str = "f09"
    times_per_day: Literal[8] = 8
    edge_granules: bool = False
    streams: dict[str, StreamSelection] = Field(default_factory=dict)

    @field_validator("source")
    @classmethod
    def _source(cls, value: str) -> str:
        if value != "f09" and not (value.startswith("file:") and value[5:].strip()):
            raise ValueError("grid.source must be 'f09' or 'file:<path>'")
        return value


class TimeConfig(StrictModel):
    leap_seconds: Literal["apply", "ignore"] = "apply"


class QAOverride(StrictModel):
    qa_min: int = Field(ge=1, le=3, strict=True)


class QCConfig(StrictModel):
    """QA preset, inclusive physical AOD range, and per-surface QA overrides."""

    preset: Literal["retrieval_team", "cam7_v2"] = "retrieval_team"
    aod_min: float = Field(default=-0.05, allow_inf_nan=False)
    aod_max: float = Field(default=5.0, allow_inf_nan=False)
    overrides: dict[AlgorithmFamily, dict[Surface, QAOverride]] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _preset_range(cls, value: object) -> object:
        if isinstance(value, dict) and value.get("preset") == "cam7_v2":
            return {"aod_min": 0.0, **value}
        return value

    @model_validator(mode="after")
    def _valid_rules(self) -> "QCConfig":
        if self.aod_min > self.aod_max:
            raise ValueError("qc.aod_min must not exceed qc.aod_max")
        if self.preset == "cam7_v2" and self.aod_min < 0:
            raise ValueError("cam7_v2 requires nonnegative qc.aod_min")
        return self


def _default_envelopes() -> (
    dict[Literal["modis_dt", "viirs_dt"], dict[Surface, tuple[float, float]]]
):
    return {
        "modis_dt": {"land": (0.05, 0.15), "ocean": (0.03, 0.10)},
        "viirs_dt": {"land": (0.05, 0.15), "ocean": (0.05, 0.15)},
    }


class UncertaintyConfig(StrictModel):
    sigma_floor: float = Field(default=0.01, gt=0, allow_inf_nan=False)
    envelopes: dict[Literal["modis_dt", "viirs_dt"], dict[Surface, tuple[float, float]]] = Field(
        default_factory=_default_envelopes
    )

    @field_validator("envelopes")
    @classmethod
    def _coefficients(
        cls, value: dict[Literal["modis_dt", "viirs_dt"], dict[Surface, tuple[float, float]]]
    ) -> dict[Literal["modis_dt", "viirs_dt"], dict[Surface, tuple[float, float]]]:
        import math

        for surfaces in value.values():
            for coefficients in surfaces.values():
                if any(not math.isfinite(c) or c < 0 for c in coefficients):
                    raise ValueError(
                        "uncertainty envelope coefficients must be finite and nonnegative"
                    )
        return value


class WeightingConfig(StrictModel):
    method: Literal["gls", "count"] = "gls"
    rho: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    birge: bool = True
    min_pixels: int = Field(default=1, ge=1, strict=True)
    single_pixel_sigma: Literal["floor", "zero"] = "floor"


class OutputConfig(StrictModel):
    stem: str = Field(default="MODIS_VIIRS_AOD_3hr_f09", pattern=r"^[A-Za-z0-9_.-]+$")
    clip_negative_mean: bool = True


class AeronetConfig(StrictModel):
    """Direct-sun acquisition, spectral conversion, and reference uncertainty."""

    product: Literal["AOD10", "AOD15", "AOD20"] = "AOD20"
    avg: Literal["all_points", "daily"] = "all_points"
    min_interval_s: float = Field(default=6.0, ge=6.0, allow_inf_nan=False)
    retries: int = Field(default=3, ge=0, strict=True)
    sites: list[str] | None = None
    spectral: Literal["quadratic_loglog", "angstrom_500"] = "quadratic_loglog"
    include_extrapolated: bool = True
    instrument_sigma: float = Field(default=0.01, gt=0, allow_inf_nan=False)

    @field_validator("sites")
    @classmethod
    def _site_names(cls, value: list[str] | None) -> list[str] | None:
        if value is not None:
            if not value or any(not name.strip() or name != name.strip() for name in value):
                raise ValueError("aeronet.sites must contain nonempty, trimmed site identifiers")
            if len(value) != len(set(value)):
                raise ValueError("aeronet.sites contains duplicate identifiers")
        return value


class ExpectedErrorEnvelopeConfig(StrictModel):
    """Positive lower/upper EE magnitudes, independent of retrieval sigma."""

    lower_intercept: float = Field(ge=0, allow_inf_nan=False)
    lower_slope: float = Field(ge=0, allow_inf_nan=False)
    upper_intercept: float = Field(ge=0, allow_inf_nan=False)
    upper_slope: float = Field(ge=0, allow_inf_nan=False)


def _evaluation_envelopes() -> dict[str, ExpectedErrorEnvelopeConfig]:
    return {
        name: ExpectedErrorEnvelopeConfig(
            lower_intercept=lower[0],
            lower_slope=lower[1],
            upper_intercept=upper[0],
            upper_slope=upper[1],
        )
        for name, lower, upper in (
            ("dt_land", (0.05, 0.15), (0.05, 0.15)),
            ("dt_ocean", (0.02, 0.10), (0.04, 0.10)),
            ("db_land", (0.05, 0.20), (0.05, 0.20)),
            ("viirs_db_water", (0.03, 0.10), (0.03, 0.10)),
        )
    }


class EvaluationConfig(StrictModel):
    """Matchup selection, grouped statistics, and published expected errors."""

    min_samples: int = Field(default=3, ge=1, strict=True)
    round_precision: int = Field(default=4, ge=0, le=15, strict=True)
    ee: dict[str, ExpectedErrorEnvelopeConfig] = Field(default_factory=_evaluation_envelopes)
    radius_km: float = Field(default=25, gt=0, allow_inf_nan=False)
    time_minutes: float = Field(default=30, ge=0, allow_inf_nan=False)
    min_pixels: int = Field(default=3, ge=1, strict=True)
    min_aeronet: int = Field(default=2, ge=1, strict=True)
    min_aeronet_grid: int = Field(default=2, ge=1, strict=True)
    high_elevation_m: float = Field(default=1000, allow_inf_nan=False)

    @field_validator("ee")
    @classmethod
    def _ee_classes(
        cls, value: dict[str, ExpectedErrorEnvelopeConfig]
    ) -> dict[str, ExpectedErrorEnvelopeConfig]:
        required = {"dt_land", "dt_ocean", "db_land", "viirs_db_water"}
        if set(value) != required:
            raise ValueError(f"evaluation.ee must define exactly {sorted(required)}")
        return value


PlotSuite = Literal[
    "aod_maps",
    "aod_differences",
    "aeronet_site_bias",
    "aeronet_site_timeseries",
    "aeronet_regional_timeseries",
    "aeronet_scatter",
    "evaluation_summary",
]


class PlotSiteSelection(StrictModel):
    select: Literal["top_n"] = "top_n"
    n: int = Field(default=20, ge=1, strict=True)


class PlotsConfig(StrictModel):
    """Publication rendering; global maps unless regional subsets are requested."""

    context: Literal["default", "presentation", "publication"] = "publication"
    aod_fill: Literal["pcolormesh", "contourf"] = "pcolormesh"
    coastline_resolution: Literal["110m", "50m"] = "110m"
    suites: list[PlotSuite] = ["aod_maps", "aod_differences"]
    regions: Literal["all"] | list[str] | None = None
    sites: PlotSiteSelection | list[str] = Field(default_factory=PlotSiteSelection)
    site_sources: Literal["product", "sensor", "stream"] = "product"

    @field_validator("regions")
    @classmethod
    def _known_regions(
        cls, value: Literal["all"] | list[str] | None
    ) -> Literal["all"] | list[str] | None:
        if isinstance(value, list):
            from sarb_aod.evaluation.regions import get_region

            try:
                value = [get_region(key).key for key in value]
            except KeyError as exc:
                raise ValueError(str(exc)) from exc
            if len(set(value)) != len(value):
                raise ValueError("plots.regions must not contain duplicate regions")
        return value

    @field_validator("suites")
    @classmethod
    def _unique_suites(cls, value: list[PlotSuite]) -> list[PlotSuite]:
        if not value or len(set(value)) != len(value):
            raise ValueError("plots.suites must contain unique suite names")
        return value

    @field_validator("sites")
    @classmethod
    def _unique_sites(cls, value: PlotSiteSelection | list[str]) -> PlotSiteSelection | list[str]:
        if isinstance(value, list) and (
            any(not site.strip() for site in value) or len(set(value)) != len(value)
        ):
            raise ValueError("plots.sites must contain nonempty unique site names")
        return value


class CombinedProductConfig(StrictModel):
    """Grid the supplied product separately, preserving native valid retrievals."""

    enabled: bool = False
    qa_min: int = Field(default=0, ge=0, le=3)
    aod_min: float = Field(default=-0.1, allow_inf_nan=False)
    aod_max: float = Field(default=5.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _range(self) -> "CombinedProductConfig":
        if self.aod_min >= self.aod_max:
            raise ValueError("combined_product requires aod_min < aod_max")
        return self


class RunConfig(StrictModel):
    name: str = Field(default="run", pattern=r"^[A-Za-z0-9_.-]+$")
    profile: str | None = None
    catalog: Path | None = None
    paths: PathsConfig
    period: Period
    stage: StageConfig
    grid: GridConfig = Field(default_factory=GridConfig)
    time: TimeConfig = Field(default_factory=TimeConfig)
    uncertainty: UncertaintyConfig = Field(default_factory=UncertaintyConfig)
    qc: QCConfig = Field(default_factory=QCConfig)
    weighting: WeightingConfig = Field(default_factory=WeightingConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    combined_product: CombinedProductConfig = Field(default_factory=CombinedProductConfig)
    aeronet: AeronetConfig = Field(default_factory=AeronetConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    plots: PlotsConfig = Field(default_factory=PlotsConfig)

    @model_validator(mode="after")
    def _products_in_catalog(self) -> "RunConfig":
        catalog = load_catalog(self.catalog)
        for short_name, collection in self.stage.selections():
            catalog.collection(short_name, collection)
        selected_collections: dict[str, str] = {}
        for stream_id, selection in self.grid.streams.items():
            product = catalog.product(selection.product)
            collection_spec = catalog.collection(selection.product, selection.collection)
            if self.combined_product.enabled and collection_spec.combined_product is None:
                raise ValueError(
                    f"no combined product defined for {selection.product} {selection.collection}"
                )
            prefix, _, algorithm = stream_id.rpartition("_")
            if prefix != product.stream_prefix or algorithm not in collection_spec.algorithms:
                raise ValueError(
                    f"invalid or unavailable stream {stream_id!r} for {selection.product} {selection.collection}"
                )
            if selection.collection not in self.stage.products.get(selection.product, []):
                raise ValueError(f"grid stream {stream_id} must appear in stage.products")
            previous = selected_collections.setdefault(selection.product, selection.collection)
            if previous != selection.collection:
                raise ValueError(f"all streams of {selection.product} must use the same collection")
        return self

    def load_catalog(self) -> Catalog:
        return load_catalog(self.catalog)
