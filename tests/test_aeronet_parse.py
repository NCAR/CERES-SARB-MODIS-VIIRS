"""Recorded all-points responses and synthetic daily header variations."""

import csv
import io
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sarb_aod.aeronet.parse import AERONET_PREAMBLE, parse_aeronet, read_aeronet

FIXTURES = Path(__file__).parent / "data" / "aeronet"
GSFC = FIXTURES / "AERONET_AOD20_ALLPOINTS_20080701_GSFC.csv"


def _rewrite(header: list[str], rows: list[list[str]]) -> str:
    output = io.StringIO()
    output.write("\n".join(GSFC.read_text().splitlines()[:5]) + "\n")
    csv.writer(output, lineterminator="\n").writerows([header, *rows])
    return output.getvalue()


def _raw() -> tuple[list[str], list[list[str]]]:
    rows = list(csv.reader(GSFC.read_text().splitlines()[5:]))
    return rows[0], rows[1:]


def test_gsfc_full_day_and_required_metadata() -> None:
    frame = read_aeronet(GSFC, day=date(2008, 7, 1))
    assert len(frame) == 34
    assert frame["AERONET_Site"].unique().tolist() == ["GSFC"]
    assert frame["time"].min().strftime("%H:%M") == "10:38"
    assert frame["time"].max().strftime("%H:%M") == "23:45"
    assert str(frame["time"].dt.tz) == "UTC"
    assert frame["time"].dt.date.eq(date(2008, 7, 1)).all()
    assert frame["aeronet_level"].eq("AOD20").all()
    assert frame["Data_Quality_Level"].eq("lev20").all()
    for column in (
        "Site_Latitude(Degrees)",
        "Site_Longitude(Degrees)",
        "Site_Elevation(m)",
        "440-675_Angstrom_Exponent",
        "440-870_Angstrom_Exponent",
        "Solar_Zenith_Angle(Degrees)",
        "AOD_1640nm",
    ):
        assert column in frame
        assert frame[column].dtype == np.float64
    assert not any("_Empty" in name for name in frame.columns)
    assert "AERONET_Site_Name" not in frame


def test_parse_is_by_name_and_fill_values_are_numeric() -> None:
    header, rows = _raw()
    rows[0][header.index("AOD_500nm")] = "-999."
    rows[1][header.index("AOD_500nm")] = "-999.000000"
    normal = parse_aeronet(_rewrite(header, rows))
    reordered = parse_aeronet(_rewrite(header[::-1], [row[::-1] for row in rows]))
    pd.testing.assert_frame_equal(normal, reordered[normal.columns])
    assert normal["AOD_500nm"].iloc[:2].isna().all()
    assert not (normal.select_dtypes("number") == -999).any().any()


def test_daily_layout_and_defensive_day_filter() -> None:
    original, rows = _raw()
    keep = [
        i
        for i, name in enumerate(original)
        if not name.startswith(("Triplet_", "Exact_")) and name != "Solar_Zenith_Angle(Degrees)"
    ]
    header = [original[i] for i in keep]
    # A daily response has N[...] counts in place of triplet / wavelength data.
    counted = [name for name in header if name.startswith("AOD_") and "Empty" not in name]
    counted += ["Precipitable_Water(cm)"]
    counted += [name for name in header if "Angstrom_Exponent" in name][:4]
    header += [f"N[{name}]" for name in counted]
    assert len(header) == 82
    daily_rows = []
    for day in ("30:06:2008", "01:07:2008", "02:07:2008"):
        row = [rows[0][i] for i in keep] + ["34"] * len(counted)
        row[header.index("Date(dd:mm:yyyy)")] = day
        row[header.index("Time(hh:mm:ss)")] = "12:00:00"
        daily_rows.append(row)
    frame = parse_aeronet(_rewrite(header, daily_rows), day=date(2008, 7, 1))
    assert len(frame) == 1
    assert frame["time"].iloc[0] == pd.Timestamp("2008-07-01T12:00:00Z")
    assert "Solar_Zenith_Angle(Degrees)" not in frame
    assert frame["N[AOD_500nm]"].iloc[0] == 34
    assert read_aeronet(GSFC, day=date(2008, 7, 2)).empty


def test_empty_response_and_header_only_table() -> None:
    empty = parse_aeronet(AERONET_PREAMBLE + "\n")
    assert empty.empty
    assert str(empty["time"].dt.tz) == "UTC"
    header, _ = _raw()
    empty_with_bands = parse_aeronet(_rewrite(header, []))
    assert empty_with_bands.empty
    assert "AOD_500nm" in empty_with_bands


@pytest.mark.parametrize("text", ["", "<html>Error</html>\n", AERONET_PREAMBLE + "\nbroken\n"])
def test_bad_response_is_not_an_empty_day(text: str) -> None:
    with pytest.raises(ValueError):
        parse_aeronet(text)


def test_truncated_rows_and_duplicate_named_fields_fail() -> None:
    header, rows = _raw()
    with pytest.raises(ValueError, match="fields; expected"):
        parse_aeronet(_rewrite(header, [rows[0][:-1]]))
    with pytest.raises(ValueError, match="duplicates a data column"):
        parse_aeronet(_rewrite([*header, "AOD_500nm"], [[*rows[0], "0.1"]]))
    index = header.index("Site_Latitude(Degrees)")
    with pytest.raises(ValueError, match="missing required columns"):
        parse_aeronet(_rewrite(header[:index] + header[index + 1 :], []))


@pytest.mark.parametrize(
    "column,value,match",
    [
        ("AOD_500nm", "bad", "nonnumeric"),
        ("Date(dd:mm:yyyy)", "32:07:2008", "invalid timestamps"),
        ("Data_Quality_Level", "unknown", "unknown Data_Quality_Level"),
        ("AERONET_Site", "", "empty site"),
    ],
)
def test_invalid_observation_fields(column: str, value: str, match: str) -> None:
    header, rows = _raw()
    rows[0][header.index(column)] = value
    with pytest.raises(ValueError, match=match):
        parse_aeronet(_rewrite(header, rows))
