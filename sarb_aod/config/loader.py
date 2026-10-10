"""Load run configurations: YAML, machine profiles, and environment expansion.

A run config may name a machine ``profile`` (e.g. ``mac``, ``derecho``, ``ami``).
The profile YAML supplies machine-specific values (data roots, path and filename
templates); the run config is deep-merged over it, so either can set any key.

Profile selection precedence: explicit argument > ``SARB_AOD_PROFILE`` > the run
config's ``profile:`` key. Profile files are searched in ``SARB_AOD_PROFILE_DIR``,
then ``<run config dir>/profiles``, then ``<run config dir>/../profiles``.

String values support ``${VAR}``, bare ``$VAR`` path segments, and a leading ``~``.
An unset variable is an error rather than an empty string.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from sarb_aod.config.schema import RunConfig
from sarb_aod.errors import ConfigurationError

PROFILE_ENV = "SARB_AOD_PROFILE"
PROFILE_DIR_ENV = "SARB_AOD_PROFILE_DIR"

_BRACED_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_BARE_ENV_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader, node: yaml.nodes.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def read_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML mapping, rejecting duplicate keys."""
    try:
        with path.open() as handle:
            data = yaml.load(handle, Loader=_UniqueKeySafeLoader)
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Configuration file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML in {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigurationError(f"Top level of {path} must be a mapping")
    return data


def _env_value(name: str, original: str) -> str:
    if name not in os.environ:
        raise ConfigurationError(
            f"Unresolved environment variable {name!r} in config value: {original!r}"
        )
    return os.environ[name]


def _is_bare_env_segment(value: str, start: int, end: int) -> bool:
    """Treat ``$VAR`` as a variable only when it is a whole path-like segment."""
    previous = value[start - 1 : start]
    next_char = value[end : end + 1]
    if previous == "$" or next_char == "$":
        return False
    if previous and previous not in "/\\:":
        return False
    if next_char and next_char not in "/\\.-":
        return False
    return True


def expand_string(value: str) -> str:
    """Expand ``${VAR}``, bare ``$VAR`` segments, and a leading ``~``."""
    expanded = _BRACED_ENV_RE.sub(lambda m: _env_value(m.group(1), value), value)
    source = expanded

    def replace_bare(match: re.Match[str]) -> str:
        if not _is_bare_env_segment(source, match.start(), match.end()):
            return match.group(0)
        return _env_value(match.group(1), source)

    expanded = _BARE_ENV_RE.sub(replace_bare, source)
    if expanded == "~" or expanded.startswith(("~/", "~\\")):
        expanded = os.path.expanduser(expanded)
    return expanded


def expand_env(data: Any) -> Any:
    """Recursively expand environment references in every string value."""
    if isinstance(data, str):
        return expand_string(data)
    if isinstance(data, dict):
        return {key: expand_env(value) for key, value in data.items()}
    if isinstance(data, list):
        return [expand_env(value) for value in data]
    return data


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge ``override`` into ``base``; nested mappings merge, other values replace."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def find_profile(name: str, config_path: Path | None) -> Path:
    """Resolve a profile name (or explicit ``.yaml`` path) to a file."""
    candidate = Path(name).expanduser()
    if candidate.suffix in (".yaml", ".yml"):
        if candidate.is_file():
            return candidate
        raise ConfigurationError(f"Profile file not found: {candidate}")

    search_dirs: list[Path] = []
    if os.environ.get(PROFILE_DIR_ENV):
        search_dirs.append(Path(os.environ[PROFILE_DIR_ENV]).expanduser())
    if config_path is not None:
        search_dirs.append(config_path.parent / "profiles")
        search_dirs.append(config_path.parent.parent / "profiles")
    for directory in search_dirs:
        for suffix in (".yaml", ".yml"):
            path = directory / f"{name}{suffix}"
            if path.is_file():
                return path
    searched = ", ".join(str(d) for d in search_dirs) or "(no search directories)"
    raise ConfigurationError(f"Profile {name!r} not found; searched {searched}")


def resolve_raw(config_path: Path, profile: str | None = None) -> dict[str, Any]:
    """Return the merged, env-expanded mapping before schema validation."""
    run_data = read_yaml(config_path)
    profile_name = profile or os.environ.get(PROFILE_ENV) or run_data.get("profile")
    merged: dict[str, Any] = {}
    if profile_name:
        profile_data = read_yaml(find_profile(str(profile_name), config_path))
        profile_data.pop("profile", None)
        merged = deep_merge(profile_data, run_data)
        merged["profile"] = str(profile_name)
    else:
        merged = run_data
    return expand_env(merged)


def load_config(config_path: str | Path, profile: str | None = None) -> RunConfig:
    """Load and validate a run configuration."""
    path = Path(config_path).expanduser()
    raw = resolve_raw(path, profile)
    raw.setdefault("name", path.stem)
    try:
        return RunConfig.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError, re-raised with context
        raise ConfigurationError(f"Invalid configuration {path}:\n{exc}") from exc
