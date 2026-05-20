"""
build_docg.py
-------------
Derives DOCG boundary polygons from the commune-level GeoJSON produced by
fetch_istat_communes.py. Unions the relevant communes into two features:
  - Barolo DOCG
  - Barbaresco DOCG (approximate — see note on Alba below)

Writes:
  ../data/docg.geojson

Dependencies:
  pip install geopandas shapely

IMPORTANT — Barbaresco / Alba approximation:
  The Barbaresco DOCG includes only the frazione "San Rocco Seno d'Elvio",
  a sub-part of the comune of Alba. Since ISTAT does not publish frazione
  boundaries, the script has two options (controlled by --alba-handling):

    approximate (default):
      Includes the full Alba comune in the Barbaresco DOCG union. The resulting
      polygon is larger than the actual DOCG. A 'boundary_note' attribute flags
      this in the output so the map legend can show a disclaimer.

    exclude:
      Omits Alba entirely. The Barbaresco DOCG polygon will be slightly smaller
      than the actual DOCG (missing the San Rocco Seno d'Elvio pocket).

  A better solution (if you can source it) is to supply the exact frazione
  geometry via --alba-frazione-geojson. That file should contain a single
  polygon covering San Rocco Seno d'Elvio only.

Usage:
  # After running fetch_istat_communes.py:
  python build_docg.py
  python build_docg.py --alba-handling exclude
  python build_docg.py --alba-frazione-geojson /path/to/san_rocco.geojson
"""

import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
from shapely.ops import unary_union

COMMUNES_FILE = Path(__file__).parent.parent / 'data' / 'communes.geojson'
OUTPUT        = Path(__file__).parent.parent / 'data' / 'docg.geojson'

BAROLO_COMMUNES = {
    'La Morra', 'Barolo', 'Castiglione Falletto', 'Serralunga d\'Alba',
    'Monforte d\'Alba', 'Novello', 'Verduno', 'Grinzane Cavour',
    'Diano d\'Alba', 'Roddi', 'Cherasco',
}
BARBARESCO_COMMUNES_NO_ALBA = {'Barbaresco', 'Neive', 'Treiso'}


def load_communes() -> gpd.GeoDataFrame:
    if not COMMUNES_FILE.exists():
        sys.exit(f'communes.geojson not found at {COMMUNES_FILE}. '
                 'Run fetch_istat_communes.py first.')
    gdf = gpd.read_file(COMMUNES_FILE)
    print(f'Loaded {len(gdf)} communes.')
    return gdf


def union_communes(gdf: gpd.GeoDataFrame, names: set) -> object:
    subset = gdf[gdf['name'].isin(names)]
    if subset.empty:
        return None
    return unary_union(subset.geometry)


def build_docg_features(gdf, alba_handling, alba_frazione_path):
    features = []

    # --- Barolo DOCG ---
    barolo_geom = union_communes(gdf, BAROLO_COMMUNES)
    if barolo_geom is None:
        print('WARNING: No Barolo communes found. Check communes.geojson.')
    else:
        features.append({
            'type': 'Feature',
            'properties': {
                'name':   'Barolo DOCG',
                'docg':   'Barolo',
                'grapes': 'Nebbiolo 100%',
                'area_ha': 2010,
                'boundary_note': '',
            },
            'geometry': barolo_geom.__geo_interface__,
        })
        print('Built Barolo DOCG polygon.')

    # --- Barbaresco DOCG ---
    base_names = set(BARBARESCO_COMMUNES_NO_ALBA)
    note = ''

    if alba_frazione_path:
        # Best case: use the exact San Rocco Seno d'Elvio geometry
        fraz = gpd.read_file(alba_frazione_path).to_crs(gdf.crs)
        base_geom  = union_communes(gdf, base_names)
        barb_geom  = unary_union([base_geom, *fraz.geometry])
        note = 'Alba fraction: San Rocco Seno d\'Elvio from supplied geojson'
        print('Built Barbaresco DOCG with exact Alba frazione.')
    elif alba_handling == 'approximate':
        base_names.add('Alba')
        barb_geom = union_communes(gdf, base_names)
        note = ('APPROXIMATE: includes full comune of Alba. '
                'Actual DOCG covers only the San Rocco Seno d\'Elvio fraction.')
        print('WARNING: Barbaresco polygon includes full Alba comune (approximation).')
    else:  # exclude
        barb_geom = union_communes(gdf, base_names)
        note = ('APPROXIMATE: Alba/San Rocco Seno d\'Elvio pocket omitted. '
                'Actual DOCG is slightly larger.')
        print('Building Barbaresco DOCG without Alba fraction (exclusion mode).')

    if barb_geom is None:
        print('WARNING: No Barbaresco communes found.')
    else:
        features.append({
            'type': 'Feature',
            'properties': {
                'name':   'Barbaresco DOCG',
                'docg':   'Barbaresco',
                'grapes': 'Nebbiolo 100%',
                'area_ha': 760,
                'boundary_note': note,
            },
            'geometry': barb_geom.__geo_interface__,
        })

    return features


def save(features: list):
    geojson = {
        'type':     'FeatureCollection',
        'features': features,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, 'w') as f:
        json.dump(geojson, f, indent=2)
    print(f'Saved {len(features)} DOCG features -> {OUTPUT}')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--alba-handling', choices=['approximate', 'exclude'],
                        default='approximate',
                        help='How to handle the Alba/San Rocco Seno d\'Elvio '
                             'fraction of Barbaresco DOCG (default: approximate)')
    parser.add_argument('--alba-frazione-geojson', metavar='PATH',
                        help='GeoJSON of the San Rocco Seno d\'Elvio frazione '
                             'only (overrides --alba-handling)')
    args = parser.parse_args()

    gdf = load_communes()
    features = build_docg_features(gdf, args.alba_handling,
                                   args.alba_frazione_geojson)
    save(features)


if __name__ == '__main__':
    main()
