"""LSOFS S3 key/URL construction — both verified bucket layouts."""
from datetime import date

import pytest

from tbay_fishcast.ingest.lsofs_paths import (
    LsofsFile, archive_url, candidate_urls, recent_url,
)


def test_recent_key_layout():
    f = LsofsFile(day=date(2026, 8, 4), cycle="t00z", kind="n", hour=0)
    assert f.recent_key() == "lsofs.20260804/lsofs.t00z.20260804.fields.n000.nc"


def test_archive_key_layout():
    f = LsofsFile(day=date(2026, 1, 1), cycle="t12z", kind="f", hour=24)
    assert f.archive_key() == "lsofs/netcdf/2026/01/01/lsofs.t12z.20260101.fields.f024.nc"


def test_recent_url_has_byterange_suffix():
    f = LsofsFile(day=date(2026, 8, 4), cycle="t06z", kind="n", hour=3)
    u = recent_url(f, "noaa-ofs-pds")
    assert u.startswith("https://noaa-ofs-pds.s3.amazonaws.com/")
    assert u.endswith("#mode=bytes")


def test_byterange_can_be_disabled():
    f = LsofsFile(day=date(2026, 8, 4), cycle="t06z", kind="n", hour=3)
    assert not archive_url(f, "noaa-nos-ofs-pds", byterange=False).endswith("#mode=bytes")


def test_candidate_urls_order_recent_then_archive():
    f = LsofsFile(day=date(2026, 8, 4), cycle="t18z", kind="n", hour=6)
    urls = candidate_urls(f, "noaa-ofs-pds", "noaa-nos-ofs-pds")
    assert "noaa-ofs-pds" in urls[0] and "lsofs.20260804/" in urls[0]
    assert "noaa-nos-ofs-pds" in urls[1] and "/netcdf/2026/08/04/" in urls[1]


@pytest.mark.parametrize("bad", [
    dict(cycle="t01z", kind="n", hour=0),
    dict(cycle="t00z", kind="x", hour=0),
    dict(cycle="t00z", kind="n", hour=-1),
])
def test_invalid_identity_rejected(bad):
    with pytest.raises(ValueError):
        LsofsFile(day=date(2026, 8, 4), **bad)
