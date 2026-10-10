"""Parse AERONET Web Service v3 direct-sun CSV by named columns.

This implements the small direct-sun subset needed here, independently of the
MonetIO acquisition used by DAVINCI's AERONETReader. AERONET source names are
retained for traceability; ``time`` and ``aeronet_level`` are added for pairing.
"""

from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

AERONET_PREAMBLE = "AERONET Data Download (Version 3 Direct Sun)"
DATE_COLUMN = "Date(dd:mm:yyyy)"
TIME_COLUMN = "Time(hh:mm:ss)"
AOD_COLUMN = re.compile(r"AOD_(\d+(?:\.\d+)?)nm\Z")

_REQUIRED = (
    "AERONET_Site",
    DATE_COLUMN,
    TIME_COLUMN,
    "Site_Latitude(Degrees)",
    "Site_Longitude(Degrees)",
    "Site_Elevation(m)",
    "Data_Quality_Level",
)
_OPTIONAL = (
    "440-675_Angstrom_Exponent",
    "440-870_Angstrom_Exponent",
    "Solar_Zenith_Angle(Degrees)",
)
_TEXT_COLUMNS = {"AERONET_Site", "Data_Quality_Level", DATE_COLUMN, TIME_COLUMN}
_LEVELS = {
    "lev10": "AOD10",
    "lev15": "AOD15",
    "lev20": "AOD20",
    "1.0": "AOD10",
    "1.5": "AOD15",
    "2.0": "AOD20",
}


def _empty_frame() -> pd.DataFrame:
    frame = pd.DataFrame(
        {name: pd.Series(dtype="str" if name in _TEXT_COLUMNS else "float64") for name in _REQUIRED}
    )
    frame["time"] = pd.Series(dtype="datetime64[ns, UTC]")
    frame["aeronet_level"] = pd.Series(dtype="str")
    return frame


def parse_aeronet(text: str, *, day: date | None = None) -> pd.DataFrame:
    """Parse all-points or daily text, optionally keeping only the requested UTC day.

    Every AOD wavelength, the two evaluation Angstrom exponents, solar zenith
    (when supplied), and daily ``N[...]`` counts are retained. Duplicate
    ``*_Empty`` placeholders, the duplicate site-name column, and unrelated
    instrument diagnostics are omitted. Numeric -999 fills become NaN.

    Header and row structure errors raise ``ValueError``. The service's
    one-line preamble-only response represents a valid empty day. Transport
    integrity and agreement with the requested product are checked by the
    fetcher, before caching. Daily responses can contain adjacent dates, so
    callers loading a daily cache should always pass ``day``.
    """
    lines = text.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != AERONET_PREAMBLE:
        raise ValueError("Not an AERONET v3 direct-sun response")
    if len(lines) == 1:
        return _empty_frame()

    reader = csv.reader(lines)
    header: list[str] | None = None
    for row in reader:
        names = [value.strip() for value in row]
        if DATE_COLUMN in names and TIME_COLUMN in names:
            header = names
            break
    if header is None:
        raise ValueError("AERONET response has no Date/Time column header")
    missing = set(_REQUIRED).difference(header)
    if missing:
        raise ValueError(f"AERONET header is missing required columns: {sorted(missing)}")
    kept = [
        (index, name)
        for index, name in enumerate(header)
        if name in _REQUIRED
        or name in _OPTIONAL
        or AOD_COLUMN.fullmatch(name)
        or (name.startswith("N[") and name.endswith("]") and "_Empty" not in name)
    ]
    kept_names = [name for _, name in kept]
    if len(set(kept_names)) != len(kept_names):
        raise ValueError("AERONET header duplicates a data column")
    records = []
    for row in reader:
        if len(row) != len(header):
            raise ValueError(
                f"AERONET row {reader.line_num} has {len(row)} fields; expected {len(header)}"
            )
        records.append([row[index].strip() for index, _ in kept])
    frame = pd.DataFrame(records, columns=kept_names)
    for name in kept_names:
        if name not in _TEXT_COLUMNS:
            try:
                numeric = pd.to_numeric(frame[name], errors="raise").astype("float64")
            except (TypeError, ValueError) as exc:
                raise ValueError(f"AERONET column {name!r} contains nonnumeric data") from exc
            frame[name] = numeric.mask(numeric == -999, np.nan)
    try:
        frame["time"] = pd.to_datetime(
            frame[DATE_COLUMN] + " " + frame[TIME_COLUMN],
            format="%d:%m:%Y %H:%M:%S",
            utc=True,
            errors="raise",
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("AERONET Date/Time columns contain invalid timestamps") from exc
    if frame["AERONET_Site"].eq("").any():
        raise ValueError("AERONET data contains an empty site identifier")
    frame["aeronet_level"] = frame["Data_Quality_Level"].str.lower().map(_LEVELS)
    if frame["aeronet_level"].isna().any():
        raise ValueError("AERONET data contains an unknown Data_Quality_Level")
    if day is not None:
        frame = frame.loc[frame["time"].dt.date == day]
    return frame.reset_index(drop=True)


def read_aeronet(path: str | Path, *, day: date | None = None) -> pd.DataFrame:
    """Read a cached CSV using the same parser and defensive day filter."""
    return parse_aeronet(Path(path).read_text(encoding="utf-8"), day=day)
