"""Execution-platform and Python/native-environment provenance for NetCDF.

Only known package fields and an explicit environment-variable allowlist are
recorded. Credentials, package download URLs and the full process environment
are never inspected. Software inventory is captured once per Python process.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from datetime import datetime, timezone
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Any

import netCDF4
import numpy as np

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


@lru_cache(maxsize=1)
def python_environment() -> str:
    """Exact package versions and Conda builds, including native dependencies."""
    conda_dir = Path(sys.prefix) / "conda-meta"
    conda_packages = []
    unreadable = []
    for path in sorted(conda_dir.glob("*.json")):
        try:
            record = json.loads(path.read_text())
            conda_packages.append(
                {
                    key: record[key]
                    for key in (
                        "name",
                        "version",
                        "build",
                        "build_number",
                        "subdir",
                        "md5",
                        "sha256",
                    )
                    if key in record
                }
            )
        except (OSError, ValueError, TypeError):
            unreadable.append(path.name)
    packages = sorted(
        (
            {"name": str(dist.metadata.get("Name", "unknown")), "version": dist.version}
            for dist in metadata.distributions()
        ),
        key=lambda item: (item["name"].lower(), item["version"]),
    )
    numerical_build = getattr(np.__config__, "CONFIG", {}).get("Build Dependencies", {})
    return json.dumps(
        {
            "python_version": platform.python_version(),
            "python_build": list(platform.python_build()),
            "python_compiler": platform.python_compiler(),
            "python_implementation": platform.python_implementation(),
            "executable": sys.executable,
            "prefix": sys.prefix,
            "base_prefix": sys.base_prefix,
            "packages": packages,
            "conda_metadata_available": conda_dir.is_dir(),
            "conda_packages": conda_packages,
            "conda_records_unreadable": unreadable,
            "native_libraries": {
                "netcdf": netCDF4.__netcdf4libversion__,
                "hdf5": netCDF4.__hdf5libversion__,
                "numpy_blas_lapack": numerical_build,
            },
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
        "python_environment": python_environment(),
        "execution_environment": json.dumps(
            {name: os.environ[name] for name in _EXECUTION_VARIABLES if name in os.environ},
            sort_keys=True,
        ),
    }
