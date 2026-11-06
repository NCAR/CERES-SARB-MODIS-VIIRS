"""Compact software/platform provenance, without an environment inventory."""

from __future__ import annotations

import json
import os
import platform
from collections.abc import Mapping
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import netCDF4
import numpy as np

from sarb_aod import __version__

_EXECUTION_VARIABLES = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMBA_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "BLIS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "JOB_ID",
    "SGE_TASK_ID",
    "NSLOTS",
    "QUEUE",
    "SLURM_JOB_ID",
    "SLURM_CPUS_PER_TASK",
)


def compact_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Omit retired inventories from new metadata, including copied reuse history."""
    result = dict(attributes)
    if "python_environment" in result:
        result.pop("python_environment")
        result["omitted_metadata_attributes"] = json.dumps(["python_environment"])
    history = result.get("reused_l2_provenance")
    serialized = isinstance(history, str)
    if isinstance(history, str):
        try:
            history = json.loads(history)
        except (ValueError, TypeError):
            history = None
    if isinstance(history, dict):
        clean = compact_attributes(history)
        result["reused_l2_provenance"] = json.dumps(clean, sort_keys=True) if serialized else clean
    return result


@lru_cache(maxsize=1)
def software_versions() -> str:
    """Record numerical software versions without paths, builds or packages."""
    return json.dumps(
        {
            "sarb_aod": __version__,
            "python": platform.python_version(),
            "numpy": np.__version__,
            "netCDF4": netCDF4.__version__,
            "netcdf_c": netCDF4.__netcdf4libversion__,
            "hdf5": netCDF4.__hdf5libversion__,
        },
        sort_keys=True,
    )


def runtime_global_attrs() -> dict[str, str]:
    """Capture the execution host, OS/kernel, architecture and selected settings."""
    distribution: dict[str, str] = {}
    if platform.system() == "Linux":
        try:
            release = platform.freedesktop_os_release()
            distribution = {
                key: release[key] for key in ("ID", "PRETTY_NAME", "VERSION_ID") if key in release
            }
        except OSError:
            distribution = {"PRETTY_NAME": "unknown"}
    elif platform.system() == "Darwin":
        distribution = {"ID": "macOS", "VERSION_ID": platform.mac_ver()[0]}
    machine: dict[str, Any] = {
        "hostname": platform.node(),
        "os_name": platform.system(),
        "os_release": platform.release(),
        "os_version": platform.version(),
        "os_distribution": distribution,
        "architecture": platform.machine(),
        "processor": platform.processor() or "unknown",
        "logical_cpu_count": os.cpu_count(),
        "libc": list(platform.libc_ver()),
    }
    affinity = getattr(os, "sched_getaffinity", None)
    if affinity is not None:
        machine["cpu_affinity"] = sorted(affinity(0))
    return {
        "date_created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "compute_platform": json.dumps(machine, sort_keys=True),
        "software_versions": software_versions(),
        "execution_environment": json.dumps(
            {name: os.environ[name] for name in _EXECUTION_VARIABLES if name in os.environ},
            sort_keys=True,
        ),
    }
