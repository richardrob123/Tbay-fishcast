"""Satellite basemap tiles for a projected bounding box — validation overlay.

The isotherm map draws a NONNA-derived shoreline and a modelled 12 °C line. The
only way to know those are georeferenced correctly is to lay them over real
imagery: if the NONNA shoreline hugs the actual land/water edge and the reachable
zones sit on visible shoals, the map is trustworthy; if they slide off, it isn't.

Esri World Imagery (ArcGIS REST `export`) serves an image for an arbitrary bbox in
any SR, no API key. We request the NONNA patch's EXACT EPSG:3857 bounds so the
returned image co-registers pixel-for-pixel with the depth grid (alignment is by
extent, not pixel count). Network — offline/build use only.

  Imagery © Esri, Maxar, Earthstar Geographics, and the GIS User Community —
  attribute in any derivative (added to the map footer).
"""
from __future__ import annotations

ESRI_EXPORT = ("https://services.arcgisonline.com/ArcGIS/rest/services/"
               "World_Imagery/MapServer/export")
ATTRIBUTION = "Imagery © Esri, Maxar, Earthstar Geographics"


def fetch_imagery_3857(bounds_3857, size_px: int = 700, timeout: float = 40.0):
    """RGB uint8 array (H, W, 3) of satellite imagery for a 3857 bbox.

    bounds_3857 = (left, bottom, right, top). Requires numpy + an image decoder
    (matplotlib). Raises on a non-image response.
    """
    import io
    import subprocess

    import numpy as np

    left, bottom, right, top = bounds_3857
    url = (f"{ESRI_EXPORT}?bbox={left:.2f},{bottom:.2f},{right:.2f},{top:.2f}"
           f"&bboxSR=3857&imageSR=3857&size={size_px},{size_px}"
           f"&format=png24&transparent=false&f=image")
    raw = subprocess.run(["curl", "-s", "-m", str(int(timeout)), url],
                         capture_output=True).stdout
    if not raw or raw[:8] != b"\x89PNG\r\n\x1a\n":
        head = raw[:160].decode("latin-1", "replace")
        raise RuntimeError(f"Esri imagery export returned non-PNG for {bounds_3857}: {head!r}")
    import matplotlib.image as mpimg
    arr = mpimg.imread(io.BytesIO(raw))            # float 0..1, (H,W,3|4)
    rgb = (arr[..., :3] * 255).astype(np.uint8)
    return rgb
