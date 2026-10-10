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


class CollectionSpec(StrictModel):
    cmr_version: str
    file_token: str
    file_format: Literal["hdf4", "netcdf4"]


class ProductSpec(StrictModel):
    platform: str
    instrument: str
    provider: str = "LAADS"
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


class PathsConfig(StrictModel):
    """Machine-specific locations. Templates are Python ``str.format`` strings."""

    data_root: Path
    granule_dir: str = DEFAULT_GRANULE_DIR
    manifest: str = DEFAULT_MANIFEST

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


class RunConfig(StrictModel):
    profile: str | None = None
    catalog: Path | None = None
    paths: PathsConfig
    period: Period
    stage: StageConfig

    @model_validator(mode="after")
    def _products_in_catalog(self) -> "RunConfig":
        catalog = load_catalog(self.catalog)
        for short_name, collection in self.stage.selections():
            catalog.collection(short_name, collection)
        return self

    def load_catalog(self) -> Catalog:
        return load_catalog(self.catalog)
