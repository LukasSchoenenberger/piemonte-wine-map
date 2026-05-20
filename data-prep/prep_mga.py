"""
prep_mga.py
-----------
Processes MGA (Menzione Geografica Aggiuntiva) sub-zone boundaries for Barolo
and Barbaresco. Writes:
  ../data/mga-barolo.geojson
  ../data/mga-barbaresco.geojson

DATA SOURCING — try in order (time-box each attempt to ~1 hour):

1. OpenStreetMap / Overpass:
   Search the Langhe area for boundary features tagged as wine sub-zones.
   Use the Overpass Turbo query builder at overpass-turbo.eu:
     area["name"="Piemonte"]->.searchArea;
     (
       relation["boundary"="protected_area"]["protect_class"="24"](area.searchArea);
       way["boundary"="protected_area"]["protect_class"="24"](area.searchArea);
     );
     out body; >; out skel qt;
   Export as GeoJSON and pass with --osm-geojson.

2. GitHub / community projects:
   Search for "barolo mga geojson", "barbaresco crus geojson",
   "langhe vineyard polygons". Some community wine-mapping projects exist.

3. Consorzio di Tutela Barolo Barbaresco Alba Langhe e Roero:
   Contact via consolorzio-tutelabarolo.it — the Consorzio publishes the
   official MGA map and may distribute the shapefile on request.

4. QGIS manual digitization (fallback):
   Download the official Consorzio PDF map.
   In QGIS: Layer > Georeferencer — register the PDF to real-world coordinates
   using known points (commune centroids, road intersections).
   Then digitize each MGA polygon as a new vector layer.
   Export as GeoJSON and pass with --barolo-geojson and/or --barbaresco-geojson.

Dependencies:
  pip install geopandas shapely

Usage:
  python prep_mga.py --barolo-geojson /path/to/barolo_mga.geojson
  python prep_mga.py --barolo-geojson /path/to/source.geojson \\
                     --barbaresco-geojson /path/to/other.geojson
  python prep_mga.py --osm-geojson /path/to/overpass_export.geojson
"""

import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd

OUT_BAROLO      = Path(__file__).parent.parent / 'data' / 'mga-barolo.geojson'
OUT_BARBARESCO  = Path(__file__).parent.parent / 'data' / 'mga-barbaresco.geojson'

# Expected property names in normalized output.
# Adjust the mappings below to match your source data's column names.
PROPERTY_MAP = {
    # source column   : output column
    'MGA':              'name',
    'DENOMINAZIONE':    'name',
    'Name':             'name',
    'name':             'name',
    'COMUNE':           'comune',
    'COMMUNE':          'comune',
    'AREA_HA':          'area_ha',
    'AREA':             'area_ha',
    'DOCG':             'docg',
}


def normalize(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Rename columns to a canonical schema and reproject to WGS84."""
    gdf = gdf.to_crs(epsg=4326)

    rename = {}
    for src, dst in PROPERTY_MAP.items():
        if src in gdf.columns:
            rename[src] = dst
    gdf = gdf.rename(columns=rename)

    # Compute area in hectares from geometry if not already present
    if 'area_ha' not in gdf.columns:
        area_gdf = gdf.to_crs(epsg=32632)   # UTM zone 32N for metric areas
        gdf['area_ha'] = (area_gdf.geometry.area / 10_000).round(1)

    # Keep only recognized columns + geometry
    keep = [c for c in ['name', 'comune', 'docg', 'area_ha'] if c in gdf.columns]
    return gdf[keep + ['geometry']]


def split_by_docg(gdf: gpd.GeoDataFrame):
    """Split a combined GeoJSON into Barolo vs Barbaresco subsets."""
    if 'docg' not in gdf.columns:
        print('WARNING: no "docg" column — cannot split by DOCG automatically.')
        print('Assuming all features are Barolo. Pass separate files to avoid this.')
        return gdf, gpd.GeoDataFrame()

    barolo      = gdf[gdf['docg'].str.contains('Barolo',      case=False, na=False)]
    barbaresco  = gdf[gdf['docg'].str.contains('Barbaresco',  case=False, na=False)]
    return barolo, barbaresco


def save(gdf: gpd.GeoDataFrame, path: Path, label: str):
    if gdf.empty:
        print(f'No {label} features to save.')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(path, driver='GeoJSON')
    print(f'Saved {len(gdf)} {label} MGA features -> {path}')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--barolo-geojson',      metavar='PATH',
                        help='GeoJSON source for Barolo MGAs')
    parser.add_argument('--barbaresco-geojson',  metavar='PATH',
                        help='GeoJSON source for Barbaresco MGAs')
    parser.add_argument('--osm-geojson',         metavar='PATH',
                        help='Combined GeoJSON from Overpass (split by docg attribute)')
    args = parser.parse_args()

    if args.osm_geojson:
        combined   = normalize(gpd.read_file(args.osm_geojson))
        barolo, barbaresco = split_by_docg(combined)
        save(barolo,     OUT_BAROLO,     'Barolo')
        save(barbaresco, OUT_BARBARESCO, 'Barbaresco')
        return

    if args.barolo_geojson:
        barolo = normalize(gpd.read_file(args.barolo_geojson))
        save(barolo, OUT_BAROLO, 'Barolo')

    if args.barbaresco_geojson:
        barbaresco = normalize(gpd.read_file(args.barbaresco_geojson))
        save(barbaresco, OUT_BARBARESCO, 'Barbaresco')

    if not any([args.osm_geojson, args.barolo_geojson, args.barbaresco_geojson]):
        print('No source file provided. See module docstring for data-sourcing instructions.')
        print('Usage: python prep_mga.py --barolo-geojson /path/to/source.geojson')
        sys.exit(1)


if __name__ == '__main__':
    main()
