#!/usr/bin/env python3
"""Builds app/static/world.json: country outlines as ready-made SVG paths, keyed by ISO country code.

Sources (download once, not part of the repo):
  https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-50m.json   (Natural Earth, public domain)
  https://cdn.jsdelivr.net/npm/i18n-iso-countries@7.11.0/codes.json   (ISO numeric code to country code)

Usage: python3 tools/make_world.py countries-50m.json codes.json app/static/world.json
"""

import json
import math
import sys

WIDTH = 1000          # map width in SVG units
TOLERANCE = 0.35      # simplification in SVG units
MIN_AREA = 0.6        # smaller islands are dropped, the largest part of a country always stays
NAMED = {"Kosovo": "XK", "N. Cyprus": "CY", "Somaliland": "SO"}  # no numeric code in world-atlas
SKIP = {"AQ"}         # Antarctica


def natural_earth(lon, lat):
    """Natural Earth I projection, result roughly in [-2.74, 2.74] x [-1.42, 1.42]."""
    l, p = math.radians(lon), math.radians(lat)
    p2 = p * p
    p4 = p2 * p2
    x = l * (0.870700 - 0.131979 * p2 + p4 * (-0.013791 + p4 * (0.003971 * p2 - 0.001529 * p4)))
    y = p * (1.007226 + p2 * (0.015085 + p4 * (-0.044475 + 0.028874 * p2 - 0.005916 * p4)))
    return x, y


def decode_arcs(topo):
    (sx, sy), (tx, ty) = topo["transform"]["scale"], topo["transform"]["translate"]
    out = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        out.append(pts)
    return out


def ring(arcs, idx):
    pts = []
    for i in idx:
        a = arcs[i] if i >= 0 else arcs[~i][::-1]
        pts.extend(a if not pts else a[1:])
    return pts


def clip(pts, keep, cut):
    """Sutherland-Hodgman at a meridian: keep(lon) tells which side stays, cut is the longitude."""
    out = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        if keep(x0) != keep(x1):
            out.append((cut, y0 + (y1 - y0) * (cut - x0) / (x1 - x0)))
        if keep(x1):
            out.append((x1, y1))
    return out


def split_antimeridian(pts):
    """Rings across the antimeridian (Russia, Fiji): make them continuous first, then split at ±180°."""
    if all(abs(b[0] - a[0]) <= 180 for a, b in zip(pts, pts[1:])):
        return [pts]
    flat, shift = [pts[0]], 0
    for (x0, _), (x1, y1) in zip(pts, pts[1:]):
        if x1 - x0 > 180:
            shift -= 360
        elif x1 - x0 < -180:
            shift += 360
        flat.append((x1 + shift, y1))
    parts = []
    for off in (-360, 0, 360):
        r = [(x + off, y) for x, y in flat]
        r = clip(clip(r, lambda x: x <= 180, 180), lambda x: x >= -180, -180)
        if len(r) >= 3:
            parts.append(r)
    return parts


def simplify(pts, tol):
    """Douglas-Peucker without recursion."""
    if len(pts) < 4:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = pts[a], pts[b]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy) or 1e-12
        best, bi = 0.0, None
        for i in range(a + 1, b):
            px, py = pts[i]
            d = abs(dx * (ay - py) - dy * (ax - px)) / norm if (dx or dy) else math.hypot(px - ax, py - ay)
            if d > best:
                best, bi = d, i
        if bi is not None and best > tol:
            keep[bi] = True
            stack += [(a, bi), (bi, b)]
    return [p for p, k in zip(pts, keep) if k]


def area(pts):
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]))) / 2


def to_path(pts):
    r = [(round(x * 10), round(y * 10)) for x, y in pts]
    out = [f"M{r[0][0] / 10:g} {r[0][1] / 10:g}"]
    for (x0, y0), (x1, y1) in zip(r, r[1:]):
        if (x0, y0) != (x1, y1):
            out.append(f"{(x1 - x0) / 10:g} {(y1 - y0) / 10:g}")
    return out[0] + "l" + " ".join(out[1:]) + "z"


def main(topo_path, codes_path, out_path):
    topo = json.load(open(topo_path))
    numeric = {row[2]: row[0] for row in json.load(open(codes_path))}
    arcs = decode_arcs(topo)
    scale = WIDTH / (2 * natural_earth(180, 0)[0])

    shapes = {}
    for g in topo["objects"]["countries"]["geometries"]:
        cc = numeric.get(g.get("id")) or NAMED.get(g["properties"]["name"])
        if not cc or cc in SKIP or g["type"] not in ("Polygon", "MultiPolygon"):
            continue
        polys = [g["arcs"]] if g["type"] == "Polygon" else g["arcs"]
        for poly in polys:
            for idx in poly:
                for part in split_antimeridian(ring(arcs, idx)):
                    pts = [natural_earth(lon, lat) for lon, lat in part]
                    pts = [(WIDTH / 2 + x * scale, -y * scale) for x, y in pts]
                    small = simplify(pts, TOLERANCE)
                    shapes.setdefault(cc, []).append(small if len(small) >= 4 else pts)  # do not simplify microstates away

    ys = [y for rings in shapes.values() for r in rings for _, y in r]
    top = min(ys)
    world = {}
    for cc, rings in shapes.items():
        rings = [[(x, y - top) for x, y in r] for r in rings if len(r) >= 3]
        biggest = max(rings, key=area)
        world[cc] = "".join(to_path(r) for r in rings if r is biggest or area(r) >= MIN_AREA)
    height = math.ceil(max(ys) - top)
    json.dump({"width": WIDTH, "height": height, "source": "Natural Earth via world-atlas", "shapes": world},
              open(out_path, "w"), separators=(",", ":"), sort_keys=True)
    print(f"{len(world)} countries, {WIDTH}x{height}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
