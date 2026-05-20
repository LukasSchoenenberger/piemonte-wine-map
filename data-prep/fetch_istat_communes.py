"""
fetch_istat_communes.py
-----------------------
Downloads Italian commune administrative boundaries from ISTAT, filters to the
communes that make up the Barolo and Barbaresco DOCGs, and writes:
  ../data/communes.geojson

Dependencies:
  pip install geopandas requests

Usage:
  python fetch_istat_communes.py            # auto-downloads the ISTAT zip
  python fetch_istat_communes.py --year 2023
  python fetch_istat_communes.py --shapefile /path/to/Com01012024_g_WGS84.shp
"""

import argparse
import sys
import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import requests

# ISTAT publishes generalised admin boundaries annually as a zip.
# URL pattern confirmed from istat.it/it/archivio/222527
ISTAT_ZIP_URL = (
    'https://www.istat.it/storage/cartografia/confini_amministrativi/'
    'generalizzati/{year}/Limiti0101{year}_g.zip'
)
# Path inside the zip to the comuni shapefile (WGS84 version)
SHP_INNER_PATTERN = 'Com0101{year}_g/Com0101{year}_g_WGS84.shp'
DEFAULT_YEAR = 2024

OUTPUT = Path(__file__).parent.parent / 'data' / 'communes.geojson'

# All communes that fall within the Barolo DOCG, per the disciplinare.
BAROLO_COMMUNES = {
    'La Morra',
    'Barolo',
    'Castiglione Falletto',
    'Serralunga d\'Alba',
    'Monforte d\'Alba',
    'Novello',
    'Verduno',
    'Grinzane Cavour',
    'Diano d\'Alba',
    'Roddi',
    'Cherasco',
}

# Barbaresco DOCG communes.
# NOTE: Barbaresco DOCG also includes the frazione San Rocco Seno d\'Elvio,
# which is part of the comune of Alba. That sub-area cannot be extracted from
# full-comune boundaries — we include the full Alba comune here as an
# approximation and flag it. See build_docg.py for handling.
BARBARESCO_COMMUNES = {
    'Barbaresco',
    'Neive',
    'Treiso',
    'Alba',  # partial — only San Rocco Seno d\'Elvio fraction is in DOCG
}

TARGET_COMMUNES = BAROLO_COMMUNES | BARBARESCO_COMMUNES

# Province of Cuneo (CN) — narrows the search so we don't accidentally match
# a same-named comune in another province.
PROVINCE_CODE = 'CN'  # ISTAT code for Cuneo


def load_shapefile(path: str) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    print(f'Loaded {len(gdf)} communes from shapefile.')
    print(f'CRS: {gdf.crs}')
    print(f'Columns: {list(gdf.columns)}')
    return gdf


def filter_communes(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    # ISTAT shapefiles use 'COMUNE' or 'DENOMINAZI' for the commune name
    # and 'SIGLA' or 'COD_PROV' for the province.
    # Column names vary by year — try common variants.
    name_col  = _find_col(gdf, ['COMUNE', 'DENOMINAZI', 'DEN_CM', 'NAME'])
    prov_col  = _find_col(gdf, ['SIGLA', 'COD_PROV', 'PROVINCE', 'PRO_COM'])

    if name_col is None:
        sys.exit('Could not find a commune-name column. '
                 f'Available columns: {list(gdf.columns)}')

    # Filter by commune name (case-insensitive) and optionally province
    mask = gdf[name_col].str.strip().isin(TARGET_COMMUNES)
    if prov_col and PROVINCE_CODE in gdf[prov_col].values:
        mask &= gdf[prov_col].str.strip() == PROVINCE_CODE
        print(f'Filtering by province code {PROVINCE_CODE!r} ({prov_col})')

    filtered = gdf[mask].copy()
    print(f'Matched {len(filtered)} communes out of {len(TARGET_COMMUNES)} expected.')

    missing = TARGET_COMMUNES - set(filtered[name_col].str.strip())
    if missing:
        print(f'WARNING: missing communes: {missing}')
        print('Check that column name matching is correct.')

    return filtered


def add_metadata(gdf: gpd.GeoDataFrame, name_col: str) -> gpd.GeoDataFrame:
    """Annotate each commune with which DOCG(s) it belongs to."""
    def docg_for(name):
        parts = []
        if name in BAROLO_COMMUNES:
            parts.append('Barolo')
        if name in BARBARESCO_COMMUNES:
            parts.append('Barbaresco')
            if name == 'Alba':
                parts.append('(partial — San Rocco Seno d\'Elvio only)')
        return ', '.join(parts)

    gdf = gdf.copy()
    gdf['name']     = gdf[name_col].str.strip()
    gdf['docg']     = gdf['name'].apply(docg_for)
    gdf['region']   = 'Piemonte'
    return gdf[['name', 'docg', 'region', 'geometry']]


def save(gdf: gpd.GeoDataFrame):
    gdf = gdf.to_crs(epsg=4326)  # WGS84 for MapLibre / GeoJSON
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(OUTPUT, driver='GeoJSON')
    print(f'Saved {len(gdf)} features -> {OUTPUT}')


def _find_col(gdf, candidates):
    for c in candidates:
        if c in gdf.columns:
            return c
    # Case-insensitive fallback
    lower = {col.upper(): col for col in gdf.columns}
    for c in candidates:
        if c.upper() in lower:
            return lower[c.upper()]
    return None


def download_istat(year: int) -> str:
    """Download the ISTAT zip, extract it to a temp dir, return path to the .shp."""
    url = ISTAT_ZIP_URL.format(year=year)
    inner = SHP_INNER_PATTERN.format(year=year)

    print(f'Downloading ISTAT boundaries ({year}) ...')
    resp = requests.get(url, stream=True, timeout=120)
    if not resp.ok:
        sys.exit(
            f'Download failed (HTTP {resp.status_code}).\n'
            f'URL tried: {url}\n'
            f'Check https://www.istat.it/it/archivio/222527 for the current file.'
        )

    tmp = tempfile.mkdtemp(prefix='istat_')
    zip_path = Path(tmp) / f'Limiti{year}.zip'
    with open(zip_path, 'wb') as f:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            f.write(chunk)
    print(f'Downloaded {zip_path.stat().st_size / 1e6:.1f} MB.')

    with zipfile.ZipFile(zip_path) as z:
        z.extractall(tmp)

    shp = Path(tmp) / inner
    if not shp.exists():
        # If exact inner path changed, find it
        candidates = list(Path(tmp).rglob('*_WGS84.shp'))
        comuni = [p for p in candidates if 'Com' in p.name]
        if not comuni:
            sys.exit(f'Could not locate comuni shapefile inside zip. '
                     f'Contents: {list(Path(tmp).rglob("*.shp"))}')
        shp = comuni[0]
        print(f'Using shapefile: {shp}')

    return str(shp)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--shapefile', metavar='PATH',
                        help='Path to a locally downloaded ISTAT comuni .shp file '
                             '(skips automatic download)')
    parser.add_argument('--year', type=int, default=DEFAULT_YEAR,
                        help=f'ISTAT boundary year to download (default: {DEFAULT_YEAR})')
    args = parser.parse_args()

    if args.shapefile:
        shp_path = args.shapefile
    else:
        shp_path = download_istat(args.year)

    gdf = load_shapefile(shp_path)

    name_col = _find_col(gdf, ['COMUNE', 'DENOMINAZI', 'DEN_CM', 'NAME'])
    filtered = filter_communes(gdf)
    enriched = add_metadata(filtered, name_col)
    save(enriched)


if __name__ == '__main__':
    main()
