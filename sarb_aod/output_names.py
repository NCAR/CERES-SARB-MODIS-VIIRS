"""Publication names and read-only access to both grid naming conventions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sarb_aod.config.schema import RunConfig

LEGACY_STEM = "MODIS_VIIRS_AOD_3hr_f09"
_TOKENS = {
    "terra": "Terra",
    "aqua": "Aqua",
    "snpp": "SNPP",
    "noaa20": "NOAA20",
    "modis": "MODIS",
    "viirs": "VIIRS",
    "dt": "DT",
    "db": "DB",
    "combined": "COMBINED",
    "mixed": "MIXED",
}
_LOWER_TOKENS = {value: key for key, value in _TOKENS.items()}
_SPECIAL = {
    "AOD_550_Dark_Target_Deep_Blue_Combined_Land_Ocean": "AOD_mean_MODIS_COMBINED",
    "AOD_550_Dark_Target_Deep_Blue_Combined_Algorithm_Flag_Land_Ocean": (
        "AOD_algorithm_flag_MODIS_COMBINED"
    ),
}
_REVERSE_SPECIAL = {value: key for key, value in _SPECIAL.items()}


def public_name(name: str) -> str:
    """AOD and algorithm acronyms uppercase; platforms title case; statistics lowercase."""
    if name in _SPECIAL:
        return _SPECIAL[name]
    if name.startswith("aod_weighted_mean"):
        name = "AOD_mean" + name.removeprefix("aod_weighted_mean")
    elif name.startswith("aod_"):
        name = "AOD_" + name.removeprefix("aod_")
    else:
        return name
    return "_".join(_TOKENS.get(token, token) for token in name.split("_"))


def legacy_name(name: str) -> str:
    """Map a published field to its internal name; coordinates retain CF spelling."""
    if name in _REVERSE_SPECIAL:
        return _REVERSE_SPECIAL[name]
    if name in _SPECIAL or not name.startswith("AOD_"):
        return name
    parts = [_LOWER_TOKENS.get(token, token) for token in name.split("_")[1:]]
    if parts[0] == "mean":
        rest = parts[1:]
        if not rest or rest[0] in {"modis", "viirs", "filtered", "unfiltered"}:
            return "aod_weighted_mean" + ("_" + "_".join(rest) if rest else "")
    return "aod_" + "_".join(parts)


def source_label(config: RunConfig) -> str:
    """Deterministic configured source identity, independent of clouds and data gaps."""
    catalog = config.load_catalog()
    sources: dict[str, set[str]] = {}
    for selected in config.grid.streams.values():
        product = catalog.product(selected.product)
        platform = product.platform.replace("-", "")
        sources.setdefault(product.instrument.upper(), set()).add(platform)
    if not sources:
        raise ValueError("automatic output naming requires configured grid.streams")
    order = {name: i for i, name in enumerate(("Terra", "Aqua", "SNPP", "NOAA20"))}
    return "_".join(
        sensor + "-" + "-".join(sorted(platforms, key=lambda p: (order.get(p, 99), p)))
        for sensor, platforms in sorted(sources.items())
    )


def output_stem(config: RunConfig) -> str:
    """Explicit custom stems are respected; new-format defaults reflect selected inputs."""
    stem = config.output.stem
    if stem == "auto" or (stem == LEGACY_STEM and config.output.naming == "ceres"):
        return f"CER_AOD-3hr-f09_{source_label(config)}"
    return stem


def rename_output_variables(dataset: Any) -> None:
    """Rename the completed file in place, including variable-reference attributes."""
    names = {name: public_name(name) for name in dataset.variables}
    if len(set(names.values())) != len(names):
        raise ValueError("publication variable names collide")
    for old, new in names.items():
        if old != new:
            dataset.renameVariable(old, new)
    for variable in dataset.variables.values():
        for attribute in ("ancillary_variables", "coordinates", "bounds"):
            if attribute in variable.ncattrs():
                value = variable.getncattr(attribute)
                variable.setncattr(attribute, " ".join(names.get(v, v) for v in value.split()))


class _GridView:
    """Canonical names for internal readers without changing the file or its metadata."""

    def __init__(self, dataset: Any):
        self._dataset = dataset
        self.variables = {legacy_name(name): value for name, value in dataset.variables.items()}
        if len(self.variables) != len(dataset.variables):
            raise ValueError("ambiguous old and new AOD variable names in one file")

    def __getitem__(self, name: str) -> Any:
        return self.variables[legacy_name(name)]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._dataset, name)


def grid_view(dataset: Any) -> Any:
    """Expose either on-disk convention under canonical internal names."""
    return dataset if isinstance(dataset, _GridView) else _GridView(dataset)


def canonical_xarray(dataset: Any) -> Any:
    """Return a shallow xarray view; values and recorded processing history are preserved."""
    names = {name: legacy_name(str(name)) for name in dataset.variables}
    if len(set(names.values())) != len(names):
        raise ValueError("ambiguous old and new AOD variable names in one file")
    return dataset.rename({old: new for old, new in names.items() if old != new})
