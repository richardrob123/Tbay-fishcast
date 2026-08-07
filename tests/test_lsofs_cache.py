"""LSOFS byte-cache — one network fetch per distinct file, bounded (hermetic, no real network)."""
import netCDF4 as nc

from tbay_fishcast.ingest import backfill as bf


class _FakeFS:
    def __init__(self, data, counter):
        self._data, self._c = data, counter

    def cat_file(self, url):
        self._c["n"] += 1
        return self._data


def _patch(monkeypatch, data, counter):
    monkeypatch.setattr(bf.fsspec, "filesystem", lambda *a, **k: _FakeFS(data, counter))
    bf._BYTES_CACHE.clear()


def test_repeated_urls_fetch_once(monkeypatch, lsofs_fixture):
    data = lsofs_fixture.read_bytes()
    c = {"n": 0}
    _patch(monkeypatch, data, c)
    for _ in range(7):          # e.g. 7 stretches opening the same lead file
        bf._cat_bytes("s3://bucket/lead_f024.nc")
    assert c["n"] == 1, "the same LSOFS file was fetched more than once across stretches"


def test_cache_is_bounded(monkeypatch, lsofs_fixture):
    data = lsofs_fixture.read_bytes()
    c = {"n": 0}
    _patch(monkeypatch, data, c)
    for i in range(bf._BYTES_CACHE_MAX + 12):
        bf._cat_bytes(f"s3://bucket/f{i}.nc")
    assert len(bf._BYTES_CACHE) == bf._BYTES_CACHE_MAX, "byte cache grew past its cap (OOM risk)"


def test_reopen_from_cached_bytes_reads_correctly(monkeypatch, lsofs_fixture):
    data = lsofs_fixture.read_bytes()
    c = {"n": 0}
    _patch(monkeypatch, data, c)
    d1 = bf._open_bytes("s3://bucket/x.nc")
    v1 = set(d1.variables)
    d1.close()
    d2 = bf._open_bytes("s3://bucket/x.nc")   # served from cache, must still open cleanly
    v2 = set(d2.variables)
    d2.close()
    assert v1 == v2 and "temp" in v1
    assert c["n"] == 1
