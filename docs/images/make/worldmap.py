"""The website's world map, site/worldmap.json: each country's outline as one SVG path, drawn in a 1000-wide viewBox, for
the census of who runs Sedjem (site/build.py colours the countries that have visitors). It is made from Natural Earth's
1:110m admin 0 countries, which is in the public domain (https://www.naturalearthdata.com/), pinned to v5.1.2:
    https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_110m_admin_0_countries.geojson
Download that file anywhere outside the repository and run, from anywhere, with the standard library only:
    python docs/images/make/worldmap.py path/to/ne_110m_admin_0_countries.geojson
The GeoJSON is read as data and nothing is fetched. The projection is the equirectangular one (a degree is as wide
as it is tall), from 180 degrees west to 180 east and from 84 north to 58 south, which leaves out Antarctica. A country
is keyed by its ISO_A2_EH code (ISO_A2 is -99 for France and Norway); a place with no code is left out.  Points are
rounded to a tenth of a pixel, the ones that fall on the one before are dropped, and a piece of land smaller than
MIN_AREA square pixels is left out (the small islands the 110m data holds), so the file stays small. Paths are drawn
with relative lines and filled with the even-odd rule. site/build.py reads only the file this writes, and never fetches."""
import json
import re
import sys
from pathlib import Path

R = Path(__file__).resolve().parent.parent.parent.parent
OUT = R / "site" / "worldmap.json"
WIDTH = 1000
NORTH, SOUTH = 84, -58  # the latitudes at the top and the bottom of the map
SCALE = WIDTH / 360  # pixels a degree
HEIGHT = round((NORTH - SOUTH) * SCALE)
MIN_AREA = 1.0  # square pixels; a smaller piece of land isn't drawn


def project(lon, lat):
    """A point's place on the map, rounded to a tenth of a pixel."""
    return round((lon + 180) * SCALE, 1), round((NORTH - lat) * SCALE, 1)


def number(value):
    """A number as short as it can be written: 12.5, 3, -0.4 (no leading zero)."""
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else re.sub(r"^(-?)0\.", r"\1.", text)


def ring(points):
    """A ring of (lon, lat) points as relative path commands and its area in square pixels, or None when it is too
    small to draw."""
    xy = []
    for lon, lat in points:
        p = project(lon, lat)
        if not xy or p != xy[-1]:
            xy.append(p)
    if len(xy) > 1 and xy[0] == xy[-1]:
        xy.pop()
    if len(xy) < 3:
        return None
    area = abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1]))) / 2
    if area < MIN_AREA:
        return None
    steps = [f"{number(x2 - x1)},{number(y2 - y1)}" for (x1, y1), (x2, y2) in zip(xy, xy[1:])]
    return f"M{number(xy[0][0])},{number(xy[0][1])}l" + " ".join(steps).replace(" -", "-") + "z", area


def outline(geometry):
    """A country's land as one path: the rings of its polygons, holes too, which the page draws with the even-odd rule."""
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    paths = []
    for polygon in polygons:
        for points in polygon:
            made = ring(points)
            if made:
                paths.append(made[0])
    return "".join(paths)


def make(source):
    """{"viewBox", "countries": {code: path}} from Natural Earth's GeoJSON (a path to it, or what it holds)."""
    data = source if isinstance(source, dict) else json.loads(Path(source).read_text(encoding="utf-8"))
    countries = {}
    for feature in data["features"]:
        code = str(feature["properties"].get("ISO_A2_EH", ""))
        if not re.fullmatch(r"[A-Z]{2}", code) or code == "AQ":
            continue
        path = outline(feature["geometry"])
        if path:
            countries[code] = path
    return {"viewBox": f"0 0 {WIDTH} {HEIGHT}", "countries": dict(sorted(countries.items()))}


def main(argv):
    if len(argv) != 1:
        sys.exit("worldmap: give the path of ne_110m_admin_0_countries.geojson (see this file's description)")
    made = make(argv[0])
    OUT.write_text(json.dumps(made, separators=(",", ":"), ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT}: {len(made['countries'])} countries, {OUT.stat().st_size:,} bytes")


if __name__ == "__main__":
    main(sys.argv[1:])
