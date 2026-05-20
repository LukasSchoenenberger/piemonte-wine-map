"""
prep_soils.py
-------------
Retrieves soil / geological data for the Langhe wine area from the Piemonte
Regional Geoportal and writes:
  ../data/soils.geojson

The Piemonte Geoportal (geoportale.piemonte.it) publishes pedological and
geological maps as OGC WFS services. This script attempts a WFS GetFeature
request for the soil map layer, clipping to the Barolo/Barbaresco bounding box.

DATA SOURCING — if the WFS request fails:
  1. Visit: https://www.geoportale.piemonte.it/geonetwork/
     Search for "carta dei suoli" or "carta geologica" to find the layer name
     and endpoint. This script uses known layer names from 2024; they may change.

  2. Alternatively, download the layer as a shapefile from the Geoportal's
     data download interface and pass it with --shapefile.

  3. The ISPRA (Istituto Superiore per la Protezione e la Ricerca Ambientale)
     also publishes national pedological data at:
     https://www.isprambiente.gov.it/it/banche-dati/suoli-e-territorio
     Search for "carta dei suoli d'Italia 1:250.000".

Dependencies:
  pip install geopandas requests shapely

Usage:
  python prep_soils.py                           # attempt WFS download
  python prep_soils.py --shapefile /path/to/soils.shp
  python prep_soils.py --geojson /path/to/soils.geojson
"""

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import requests
from shapely.geometry import box

OUTPUT = Path(__file__).parent.parent / 'data' / 'soils.geojson'

# Bounding box covering Barolo + Barbaresco + a small buffer (WGS84)
BBOX = (7.85, 44.55, 8.15, 44.80)  # (min_lon, min_lat, max_lon, max_lat)

# Piemonte Geoportal WFS endpoint and layer (verify at geoportale.piemonte.it)
WFS_URL   = 'https://www.geoportale.piemonte.it/ogc/wfs'
WFS_LAYER = 'Pedologia:CARTA_DEI_SUOLI'  # May need to be updated
WFS_CRS   = 'EPSG:4326'

# Soil type color mapping for the MapLibre fill-expression.
# Adjust keys to match whatever the 'tipo_suolo' or equivalent column contains.
SOIL_COLORS = {
    'Tortoniano':    '#c8b090',   # older Tortonian marine sediments (Serralunga side)
    'Elveziano':     '#b0c0a0',   # Helvetian (La Morra side)
    'Messiniano':    '#d0b888',
    'Langhiano':     '#b8a878',
    'Alluvionale':   '#c8d0b8',
    'Flysch':        '#a8b898',
}


def fetch_wfs() -> gpd.GeoDataFrame | None:
    """Attempt to download soil data via WFS. Returns None on failure."""
    bbox_str = ','.join(str(v) for v in BBOX)
    params = {
        'SERVICE':      'WFS',
        'VERSION':      '2.0.0',
        'REQUEST':      'GetFeature',
        'TYPENAMES':    WFS_LAYER,
        'SRSNAME':      WFS_CRS,
        'BBOX':         f'{bbox_str},{WFS_CRS}',
        'OUTPUTFORMAT': 'application/json',
        'COUNT':        10000,
    }
    print(f'Requesting WFS: {WFS_URL} layer={WFS_LAYER}')
    try:
        resp = requests.get(WFS_URL, params=params, timeout=60)
        resp.raise_for_status()
        import io, json
        data = resp.json()
        if data.get('features'):
            gdf = gpd.GeoDataFrame.from_features(data['features'], crs=4326)
            print(f'WFS returned {len(gdf)} features.')
            return gdf
        print('WFS returned zero features — layer name may be incorrect.')
    except Exception as e:
        print(f'WFS request failed: {e}')
    return None


def load_local(path: str) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    return gdf.to_crs(epsg=4326)


def clip_to_bbox(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    bbox_poly = box(*BBOX)
    return gdf[gdf.intersects(bbox_poly)].clip(bbox_poly)


def simplify(gdf: gpd.GeoDataFrame, tolerance_m: float = 50) -> gpd.GeoDataFrame:
    """Simplify geometry to reduce file size (tolerance in metres)."""
    utm = gdf.to_crs(epsg=32632)
    utm.geometry = utm.geometry.simplify(tolerance_m, preserve_topology=True)
    return utm.to_crs(epsg=4326)


def save(gdf: gpd.GeoDataFrame):
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(OUTPUT, driver='GeoJSON')
    print(f'Saved {len(gdf)} soil features -> {OUTPUT}')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--shapefile', metavar='PATH',
                        help='Local soil shapefile')
    parser.add_argument('--geojson', metavar='PATH',
                        help='Local soil GeoJSON')
    parser.add_argument('--simplify-tolerance', type=float, default=50,
                        help='Simplification tolerance in metres (default 50)')
    args = parser.parse_args()

    if args.shapefile:
        gdf = load_local(args.shapefile)
    elif args.geojson:
        gdf = load_local(args.geojson)
    else:
        gdf = fetch_wfs()
        if gdf is None:
            print('\nCould not retrieve soils data automatically.')
            print('Download manually from the Piemonte Geoportal and re-run with --shapefile or --geojson.')
            sys.exit(1)

    gdf = clip_to_bbox(gdf)
    print(f'{len(gdf)} features after bbox clip.')

    gdf = simplify(gdf, tolerance_m=args.simplify_tolerance)
    save(gdf)


if __name__ == '__main__':
    main()
