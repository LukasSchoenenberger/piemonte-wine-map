"""
seed_producers.py
-----------------
Converts producers.csv (hand-curated seed list) into a GeoJSON point file
at ../data/producers.geojson.

If a row already has lat/lon values, those are used directly.
Rows with missing coordinates are geocoded via the Nominatim API (free, OSM).

Dependencies:
  pip install requests

Usage:
  python seed_producers.py
  python seed_producers.py --no-geocode   # skip geocoding; drop rows with no coords
"""

import argparse
import csv
import json
import time
from pathlib import Path

import requests

CSV_FILE = Path(__file__).parent / 'producers.csv'
OUTPUT   = Path(__file__).parent.parent / 'data' / 'producers.geojson'

NOMINATIM_URL    = 'https://nominatim.openstreetmap.org/search'
NOMINATIM_AGENT  = 'piemonte-wine-map/1.0 (personal project)'
NOMINATIM_DELAY  = 1.1  # seconds between requests (Nominatim rate limit: 1/s)


def load_csv() -> list[dict]:
    with open(CSV_FILE, newline='', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    print(f'Loaded {len(rows)} producers from {CSV_FILE.name}.')
    return rows


def geocode(name: str, commune: str) -> tuple[float, float] | None:
    """Query Nominatim for the winery. Returns (lat, lon) or None."""
    query = f'{name}, {commune}, Piemonte, Italy'
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={'q': query, 'format': 'json', 'limit': 1},
            headers={'User-Agent': NOMINATIM_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json()
        if results:
            return float(results[0]['lat']), float(results[0]['lon'])
    except Exception as e:
        print(f'  Geocoding failed for {name!r}: {e}')
    return None


def build_features(rows: list[dict], geocode_missing: bool) -> list[dict]:
    features = []
    for row in rows:
        lat_str = row.get('lat', '').strip()
        lon_str = row.get('lon', '').strip()

        lat = float(lat_str) if lat_str else None
        lon = float(lon_str) if lon_str else None

        if (lat is None or lon is None) and geocode_missing:
            print(f'Geocoding: {row["name"]!r} ...')
            result = geocode(row['name'], row.get('commune', ''))
            time.sleep(NOMINATIM_DELAY)
            if result:
                lat, lon = result
                print(f'  -> ({lat:.4f}, {lon:.4f})')
            else:
                print(f'  -> skipped (no result).')

        if lat is None or lon is None:
            print(f'Skipping {row["name"]!r}: no coordinates.')
            continue

        props = {k: v for k, v in row.items() if k not in ('lat', 'lon') and v and v != '—'}

        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
            'properties': props,
        })

    return features


def save(features: list[dict]):
    geojson = {'type': 'FeatureCollection', 'features': features}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(geojson, f, indent=2, ensure_ascii=False)
    print(f'Saved {len(features)} producer features -> {OUTPUT}')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--no-geocode', action='store_true',
                        help='Skip Nominatim geocoding; drop rows with missing coords')
    args = parser.parse_args()

    rows     = load_csv()
    features = build_features(rows, geocode_missing=not args.no_geocode)
    save(features)


if __name__ == '__main__':
    main()
