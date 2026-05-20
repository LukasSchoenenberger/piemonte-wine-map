"""
svg_to_mga_geojson.py
---------------------
Programmatically extracts MGA polygon boundaries from the official
Barolo/Barbaresco PDF maps (converted to SVG) and georeferences them
using the known commune boundaries as control points.

Pipeline:
  1. Parse SVG paths → polygon vertices in SVG coordinates
  2. Filter for large filled polygons (MGA zone fills)
  3. Group polygons by fill colour (each unique colour = one commune)
  4. Rough affine transform: SVG bbox → geographic bbox
  5. For each colour group: use rough transform to find which commune it is
  6. Solve a refined affine transform using commune centroids as control points
  7. Apply refined transform to all polygon vertices → WGS84
  8. Export GeoJSON (with commune attribution; MGA names added by prep_mga.py)

Usage:
  python svg_to_mga_geojson.py --svg data-prep/reference-maps/barolo.svg \\
                               --communes data/communes.geojson \\
                               --docg Barolo \\
                               --out data/mga-barolo.geojson

  python svg_to_mga_geojson.py --svg data-prep/reference-maps/barbaresco.svg \\
                               --communes data/communes.geojson \\
                               --docg Barbaresco \\
                               --out data/mga-barbaresco.geojson

Dependencies: geopandas, shapely, numpy
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import MultiPolygon, Point, Polygon, shape
from shapely.ops import unary_union
import xml.etree.ElementTree as ET


# -----------------------------------------------------------------------
# SVG path parser
# -----------------------------------------------------------------------

def _tokenize_path(d: str):
    """Split path d-string into (command, [args]) tuples."""
    tokens = re.findall(r'[MmLlHhVvCcSsQqTtAaZz]|[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?', d)
    cmd = None
    args = []
    for t in tokens:
        if t.isalpha():
            if cmd is not None:
                yield cmd, args
            cmd = t
            args = []
        else:
            args.append(float(t))
    if cmd is not None:
        yield cmd, args


def parse_path(d: str, bezier_steps: int = 4) -> list[tuple[float, float]]:
    """
    Parse an SVG path d-string into a list of (x, y) vertices.
    Bezier curves are sampled at bezier_steps intermediate points.
    Returns an empty list for degenerate paths.
    """
    pts = []
    cx, cy = 0.0, 0.0   # current position
    sx, sy = 0.0, 0.0   # subpath start

    for cmd, args in _tokenize_path(d):
        n = len(args)
        rel = cmd.islower()
        c = cmd.upper()

        if c == 'M':
            pairs = list(zip(args[0::2], args[1::2]))
            for i, (x, y) in enumerate(pairs):
                if rel and not (i == 0 and len(pts) == 0):
                    x += cx; y += cy
                elif rel:
                    x += cx; y += cy
                cx, cy = x, y
                if i == 0:
                    sx, sy = cx, cy
                pts.append((cx, cy))

        elif c == 'L':
            for x, y in zip(args[0::2], args[1::2]):
                if rel:
                    x += cx; y += cy
                cx, cy = x, y
                pts.append((cx, cy))

        elif c == 'H':
            for x in args:
                if rel:
                    x += cx
                cx = x
                pts.append((cx, cy))

        elif c == 'V':
            for y in args:
                if rel:
                    y += cy
                cy = y
                pts.append((cx, cy))

        elif c in ('C', 'S'):
            # Cubic bezier — sample intermediate points
            i = 0
            while i < n:
                if c == 'C' and i + 5 < n + 1:
                    x1, y1, x2, y2, x, y = args[i:i+6]
                    i += 6
                elif c == 'S' and i + 3 < n + 1:
                    # Reflected control point
                    x1, y1 = cx, cy  # simplified — use current pos
                    x2, y2, x, y = args[i], args[i+1], args[i+2], args[i+3]
                    i += 4
                else:
                    break
                if rel:
                    x1+=cx; y1+=cy; x2+=cx; y2+=cy; x+=cx; y+=cy
                p0 = np.array([cx, cy])
                p1 = np.array([x1, y1])
                p2 = np.array([x2, y2])
                p3 = np.array([x, y])
                for t in np.linspace(0, 1, bezier_steps + 1)[1:]:
                    pt = ((1-t)**3*p0 + 3*(1-t)**2*t*p1
                          + 3*(1-t)*t**2*p2 + t**3*p3)
                    pts.append((float(pt[0]), float(pt[1])))
                cx, cy = x, y

        elif c == 'Q':
            i = 0
            while i + 3 < n + 1:
                x1, y1, x, y = args[i:i+4]
                i += 4
                if rel:
                    x1+=cx; y1+=cy; x+=cx; y+=cy
                p0 = np.array([cx, cy])
                p1 = np.array([x1, y1])
                p2 = np.array([x, y])
                for t in np.linspace(0, 1, bezier_steps + 1)[1:]:
                    pt = (1-t)**2*p0 + 2*(1-t)*t*p1 + t**2*p2
                    pts.append((float(pt[0]), float(pt[1])))
                cx, cy = x, y

        elif c == 'Z':
            if pts:
                pts.append((sx, sy))
            cx, cy = sx, sy

    return pts


# -----------------------------------------------------------------------
# Extract filled polygons from SVG
# -----------------------------------------------------------------------

_COLOUR_RE = re.compile(
    r'fill:\s*(#[0-9a-fA-F]{3,6}|rgb\([^)]+\))',
    re.IGNORECASE,
)
_SKIP_COLOURS = {
    'none', 'transparent', '#ffffff', 'rgb(100%,100%,100%)',
    'white',
}


def _normalise_colour(raw: str) -> str:
    """Normalise a fill colour string to lowercase #rrggbb for clean grouping
    and downstream use (matplotlib + MapLibre + CSS all accept hex)."""
    raw = raw.strip().lower()
    m = re.match(r'rgb\((.+)\)', raw)
    if m:
        parts = [p.strip() for p in m.group(1).split(',')]
        chans = []
        for p in parts:
            if p.endswith('%'):
                chans.append(round(float(p.rstrip('%')) * 2.55))
            else:
                # Bare numbers — could be 0-255 or 0-100. Heuristic: SVGs
                # produced by pdftocairo emit percentages without the '%'
                # in some locales, so treat values <= 100 as percentages.
                v = float(p)
                chans.append(round(v * 2.55) if v <= 100.0 else round(v))
        chans = [max(0, min(255, c)) for c in chans[:3]]
        return '#{:02x}{:02x}{:02x}'.format(*chans)
    if raw.startswith('#') and len(raw) == 4:
        # #abc → #aabbcc
        return '#' + ''.join(c * 2 for c in raw[1:])
    return raw


def _hex_to_rgb_pct(colour: str) -> tuple[float, float, float] | None:
    """Convert a #rrggbb colour to (r%, g%, b%) tuple, or None if not hex."""
    if not colour.startswith('#') or len(colour) != 7:
        return None
    try:
        r = int(colour[1:3], 16)
        g = int(colour[3:5], 16)
        b = int(colour[5:7], 16)
        return (r / 2.55, g / 2.55, b / 2.55)
    except ValueError:
        return None


def _is_near_white(colour: str, threshold: float = 95.0) -> bool:
    rgb = _hex_to_rgb_pct(colour)
    if rgb is None:
        return colour in ('white',)
    return all(v >= threshold for v in rgb)


def _is_near_black(colour: str, threshold: float = 20.0) -> bool:
    rgb = _hex_to_rgb_pct(colour)
    if rgb is None:
        return colour in ('black',)
    return all(v <= threshold for v in rgb)


def _is_near_grey(colour: str, tol: float = 5.0) -> bool:
    rgb = _hex_to_rgb_pct(colour)
    if rgb is None:
        return False
    return max(rgb) - min(rgb) < tol


def extract_polygons(svg_path: str,
                     min_bbox_area: float = 15_000,
                     ) -> list[dict]:
    """
    Parse SVG and return a list of dicts:
      { 'colour': str, 'pts': [(x,y),...], 'bbox_area': float }
    Only large filled polygons (MGA zone fills) are kept.
    """
    tree = ET.parse(svg_path)
    root = tree.getroot()
    ns_prefix = '{http://www.w3.org/2000/svg}'

    results = []
    for el in root.iter(f'{ns_prefix}path'):
        d = el.get('d', '')
        if not d:
            continue

        # Extract fill colour
        style = el.get('style', '')
        fill = el.get('fill', 'none')
        m = _COLOUR_RE.search(style)
        if m:
            fill = m.group(1)
        fill = fill.strip().lower()
        if fill in ('none', '', 'transparent'):
            continue

        colour = _normalise_colour(fill)
        if _is_near_white(colour) or _is_near_black(colour) or _is_near_grey(colour):
            continue

        # Rough bbox check before full parse
        nums = re.findall(r'[-+]?\d*\.?\d+', d)
        if len(nums) < 6:
            continue
        fns = [float(x) for x in nums]
        xs = fns[0::2]; ys = fns[1::2]
        bbox_w = max(xs) - min(xs)
        bbox_h = max(ys) - min(ys)
        if bbox_w * bbox_h < min_bbox_area:
            continue

        # Skip nearly-rectangular shapes (legend swatches) using aspect ratio + vertex count
        aspect = max(bbox_w, bbox_h) / max(min(bbox_w, bbox_h), 1)
        if aspect < 4:   # legends are usually wide rectangles; skip very square small shapes
            pass  # geographic polygons can be any aspect — don't filter here

        pts = parse_path(d)
        if len(pts) < 4:
            continue

        # Filter out shapes with very few vertices relative to size — those are
        # likely decorative rectangles (legend boxes have exactly 4-5 vertices)
        shapely_poly = pts_to_polygon(pts)
        if shapely_poly is None:
            continue
        area_svg = abs(shapely_poly.area)
        if area_svg < min_bbox_area * 0.05:    # true area much smaller than bbox → thin/decorative
            continue

        results.append({
            'colour':    colour,
            'pts':       pts,
            'bbox_area': bbox_w * bbox_h,
            'svg_area':  area_svg,
            'n_pts':     len(pts),
        })

    print(f'Extracted {len(results)} large coloured polygons from SVG.')
    return results


# -----------------------------------------------------------------------
# Shapely polygon helpers
# -----------------------------------------------------------------------

def pts_to_polygon(pts: list[tuple]) -> Polygon | None:
    if len(pts) < 3:
        return None
    try:
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
        return poly if not poly.is_empty else None
    except Exception:
        return None


# -----------------------------------------------------------------------
# Affine transform estimation
# -----------------------------------------------------------------------

def solve_affine(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """
    Least-squares affine transform from src → dst.
    src, dst: (N, 2) arrays of control point coordinates.
    Returns 2×3 matrix A such that dst ≈ (A[:, :2] @ src.T + A[:, 2:]).T
    """
    N = len(src)
    # Build system: [x_src, y_src, 1] @ [a b; c d; e f]^T = [x_dst, y_dst]
    X = np.column_stack([src, np.ones(N)])   # (N, 3)
    A, _, _, _ = np.linalg.lstsq(X, dst, rcond=None)  # (3, 2)
    return A  # apply as: dst = X @ A


def apply_affine(A: np.ndarray, pts: np.ndarray) -> np.ndarray:
    """Apply affine transform A (3×2) to (N,2) points."""
    X = np.column_stack([pts, np.ones(len(pts))])
    return X @ A  # (N, 2)


# -----------------------------------------------------------------------
# Main pipeline
# -----------------------------------------------------------------------

def run(svg_path: str, communes_path: str, docg_name: str, out_path: str,
        min_bbox_area: float = 3_000, docg_geojson: str | None = None,
        scale_x: float = 1.0, scale_y: float = 1.0,
        offset_lon: float = 0.0, offset_lat: float = 0.0):

    # --- Load DOCG boundary for clipping ---
    docg_clip_geom = None
    if docg_geojson:
        docg_gdf = gpd.read_file(docg_geojson).to_crs(epsg=4326)
        match = docg_gdf[docg_gdf['name'].str.contains(docg_name, case=False, na=False)]
        if not match.empty:
            docg_clip_geom = match.geometry.iloc[0].buffer(0.01)  # small buffer for edge tolerance
            print(f'Loaded DOCG clip boundary for {docg_name}.')

    # --- Load communes ---
    communes = gpd.read_file(communes_path).to_crs(epsg=4326)
    docg_col = 'docg'
    name_col = 'name'
    target_communes = communes[
        communes[docg_col].str.contains(docg_name, case=False, na=False)
    ]
    if target_communes.empty:
        sys.exit(f'No communes found for DOCG "{docg_name}" in {communes_path}.')
    print(f'Communes for {docg_name}: {list(target_communes[name_col])}')

    # Geographic bbox + centroid for each commune
    commune_geo = {}
    for _, row in target_communes.iterrows():
        commune_geo[row[name_col]] = {
            'centroid':   (row.geometry.centroid.x, row.geometry.centroid.y),
            'geometry':   row.geometry,
        }

    # --- Extract SVG polygons ---
    polys = extract_polygons(svg_path, min_bbox_area=min_bbox_area)

    # Group by colour (for later attribution)
    by_colour = defaultdict(list)
    for p in polys:
        by_colour[p['colour']].append(p)
    print(f'Unique colours: {len(by_colour)}  |  Total polygons: {len(polys)}')

    # --- Transform: SVG bbox of all polygon vertices → DOCG geographic bbox ---
    # This avoids legend-item contamination that biases colour-centroid matching.
    # SVG Y increases downward; geo lat increases upward → Y is flipped.
    all_svg_pts = np.array([pt for p in polys for pt in p['pts']])
    svg_min = np.percentile(all_svg_pts, 2, axis=0)   # 2nd percentile avoids outliers
    svg_max = np.percentile(all_svg_pts, 98, axis=0)

    if docg_clip_geom is not None:
        geo_bounds = docg_clip_geom.bounds  # (minx, miny, maxx, maxy) = (min_lon, min_lat, max_lon, max_lat)
    else:
        all_geo = np.array([v['centroid'] for v in commune_geo.values()])
        geo_bounds = (all_geo[:,0].min(), all_geo[:,1].min(),
                      all_geo[:,0].max(), all_geo[:,1].max())

    min_lon, min_lat, max_lon, max_lat = geo_bounds

    # Centre to scale around (so scaling alone doesn't shift the whole map).
    cx_geo = 0.5 * (min_lon + max_lon)
    cy_geo = 0.5 * (min_lat + max_lat)

    def rough_transform_pts(pts_arr: np.ndarray) -> np.ndarray:
        """Map SVG (x, y) → (lon, lat) via bbox scaling (no user adjust)."""
        lon = min_lon + (pts_arr[:,0] - svg_min[0]) / (svg_max[0] - svg_min[0]) * (max_lon - min_lon)
        lat = max_lat - (pts_arr[:,1] - svg_min[1]) / (svg_max[1] - svg_min[1]) * (max_lat - min_lat)
        return np.column_stack([lon, lat])

    def apply_user_adjust(coords: list[list[float]]) -> list[list[float]]:
        """Apply user scale/offset around the DOCG bbox centre."""
        out = []
        for lon, lat in coords:
            lon = cx_geo + (lon - cx_geo) * scale_x + offset_lon
            lat = cy_geo + (lat - cy_geo) * scale_y + offset_lat
            out.append([float(lon), float(lat)])
        return out

    if (scale_x, scale_y, offset_lon, offset_lat) != (1.0, 1.0, 0.0, 0.0):
        print(f'User adjust: scale_x={scale_x} scale_y={scale_y} '
              f'offset_lon={offset_lon} offset_lat={offset_lat}')

    # --- Apply rough transform → GeoJSON; clip to DOCG; assign commune by containment ---
    features = []
    for p in polys:
        pts_arr = np.array(p['pts'])
        if len(pts_arr) < 3:
            continue
        geo_pts = rough_transform_pts(pts_arr)
        coords = [[float(lon), float(lat)] for lon, lat in geo_pts]
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        try:
            poly = Polygon(coords)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty or poly.area < 1e-10:
                continue
        except Exception:
            continue

        # Clip to DOCG using UNADJUSTED coords (so user scale/offset can't
        # drop polygons; this filter exists only to strip legend items).
        if docg_clip_geom is not None and not docg_clip_geom.intersects(poly):
            continue

        # Assign commune by spatial containment of polygon centroid
        # (also using unadjusted coords).
        centroid_pt = poly.centroid
        commune = None
        best_dist = float('inf')
        for cname, cdata in commune_geo.items():
            if cdata['geometry'].contains(centroid_pt):
                commune = cname
                break
            d = centroid_pt.distance(cdata['geometry'])
            if d < best_dist:
                best_dist = d
                commune = cname

        # Apply user scale/offset only to the output coords.
        adj_coords = apply_user_adjust(coords)

        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Polygon', 'coordinates': [adj_coords]},
            'properties': {
                'name':       '',
                'comune':     commune,
                'docg':       docg_name,
                'svg_colour': p['colour'],
                'area_ha':    round(poly.area * 111_320**2 * 0.0001, 1),
            },
        })

    print(f'After DOCG clip: {len(features)} polygons.')

    print(f'\nTotal MGA polygon features: {len(features)}')

    # Sort by commune then area (largest first — helps manual name assignment)
    features.sort(key=lambda f: (f['properties']['comune'],
                                  -f['properties']['area_ha']))

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'w') as f:
        json.dump({'type': 'FeatureCollection', 'features': features}, f, indent=2)
    print(f'Saved → {out}')

    # Summary by commune
    from collections import Counter
    commune_counts = Counter(f['properties']['comune'] for f in features)
    print('\nPolygons per commune:')
    for commune, count in sorted(commune_counts.items()):
        print(f'  {commune:<28} {count}')


# -----------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--svg',      required=True, help='SVG converted from the PDF map')
    p.add_argument('--communes', required=True, help='communes.geojson')
    p.add_argument('--docg',     required=True, choices=['Barolo', 'Barbaresco'],
                   help='Which DOCG to process')
    p.add_argument('--out',      required=True, help='Output GeoJSON path')
    p.add_argument('--min-area', type=float, default=3_000,
                   help='Min SVG bounding-box area to consider a polygon (default 3000)')
    p.add_argument('--docg-geojson', metavar='PATH', default=None,
                   help='docg.geojson to clip output and strip legend polygons')
    p.add_argument('--scale-x', type=float, default=1.0,
                   help='Multiplicative scale on longitude (around DOCG bbox centre). Default 1.0')
    p.add_argument('--scale-y', type=float, default=1.0,
                   help='Multiplicative scale on latitude (around DOCG bbox centre). Default 1.0')
    p.add_argument('--offset-lon', type=float, default=0.0,
                   help='Additive longitude shift in degrees. Default 0.0')
    p.add_argument('--offset-lat', type=float, default=0.0,
                   help='Additive latitude shift in degrees. Default 0.0')
    args = p.parse_args()

    run(args.svg, args.communes, args.docg, args.out, args.min_area, args.docg_geojson,
        scale_x=args.scale_x, scale_y=args.scale_y,
        offset_lon=args.offset_lon, offset_lat=args.offset_lat)


if __name__ == '__main__':
    main()
