"""NONNA WCS request contract — the GetCoverage URL must match the served coverage.

Origin (ADR-062): on 2026-09-14 CHS re-published the NONNA-10 coverage with its
subset axis labels renamed "x y" -> "X Y". GeoServer matches those labels
case-sensitively and answers a mismatch with an `ows:ExceptionReport` + HTTP 404
instead of a GeoTIFF, so `fetch_patch` burned its four retries and raised. Silver
Harbour is the first station in the heartbeat loop, so every run died there: 28
consecutive heartbeat failures and a frozen coast-site, with no alert (ntfy only
speaks on a window change, never on a crash).

Nothing in the suite compared what we ASK for against what the server SAYS it
serves, so a one-character server change was invisible until a human noticed the
forecast had stopped. This pins the request to the contract: the assertions read
the axis labels out of a recorded DescribeCoverage response, so the next rename
fails here instead of in the field.

Hermetic — the fixture is a recorded response, no network (conftest blocks it).
Refresh with:

    curl -s "https://nonna-geoserver.data.chs-shc.ca/geoserver/wcs?service=WCS\
&version=2.0.1&request=DescribeCoverage&coverageId=nonna__NONNA%2010%20Coverage" \
      -o tests/fixtures/nonna_describecoverage.xml
"""
import re
from pathlib import Path
from xml.etree import ElementTree

import pytest

from tbay_fishcast.ingest.nonna import AXIS_X, AXIS_Y, _wcs_geotiff_url

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "nonna_describecoverage.xml"
GML = "{http://www.opengis.net/gml/3.2}"

LAT, LON = 48.5085, -88.9746   # Silver Harbour — the station the outage died on


@pytest.fixture(scope="module")
def served_axis_labels() -> list[str]:
    """The axis labels the live coverage advertises, as recorded from the server."""
    env = ElementTree.parse(FIXTURE).getroot().find(f".//{GML}Envelope")
    assert env is not None, "fixture is not a DescribeCoverage response"
    labels = env.get("axisLabels", "").split()
    assert labels, "DescribeCoverage carried no axisLabels"
    return labels


def _subset_labels(url: str) -> list[str]:
    """Axis labels this request actually asks to subset on, in request order."""
    return re.findall(r"[?&]subset=([^(]+)\(", url)


def test_requested_axis_labels_are_the_ones_the_server_serves(served_axis_labels):
    # order matters too: subset order follows the coverage's axis order (X then Y)
    url = _wcs_geotiff_url(LAT, LON, half_m=1200, scale_px=300)
    assert _subset_labels(url) == served_axis_labels


def test_axis_constants_track_the_served_coverage(served_axis_labels):
    assert [AXIS_X, AXIS_Y] == served_axis_labels


def test_axis_labels_are_case_exact(served_axis_labels):
    # the 2026-09-14 outage in one assertion: same letters, wrong case, 404.
    url = _wcs_geotiff_url(LAT, LON, half_m=1200, scale_px=300)
    for label, served in zip(_subset_labels(url), served_axis_labels):
        assert label == served and label != served.swapcase()


def test_url_keeps_the_rest_of_the_getcoverage_contract():
    url = _wcs_geotiff_url(LAT, LON, half_m=1200, scale_px=300)
    assert "service=WCS" in url and "version=2.0.1" in url
    assert "request=GetCoverage" in url
    assert "coverageId=nonna__NONNA%2010%20Coverage" in url
    assert "format=image/geotiff" in url
    # grid-axis labels for scalesize are OGC URIs (i/j), NOT the coverage's own
    # axis labels — they are a different namespace and stay lowercase.
    assert "/def/axis/OGC/1/i(300)" in url and "/def/axis/OGC/1/j(300)" in url


def test_subset_window_brackets_the_requested_point():
    """half_m is a half-width: the point sits inside the box on both axes."""
    from tbay_fishcast.ingest.nonna import to_mercator
    x, y = to_mercator(LAT, LON)
    url = _wcs_geotiff_url(LAT, LON, half_m=1200, scale_px=None)
    (xlo, xhi), (ylo, yhi) = (
        tuple(float(v) for v in m.split(","))
        for m in re.findall(r"[?&]subset=[^(]+\(([^)]+)\)", url)
    )
    assert xlo < x < xhi and ylo < y < yhi
    assert xhi - xlo == pytest.approx(2400.0) and yhi - ylo == pytest.approx(2400.0)
    assert "scalesize" not in url   # omitted when scale_px is None
