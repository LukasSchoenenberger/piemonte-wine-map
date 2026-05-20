"""
mga_polygon_finder.py
---------------------
PySide6 GUI for manually identifying SVG polygons the automated extraction
missed. Operates entirely in SVG coordinates; the affine to calibrated
geographic coordinates is applied only at save time.

Workflow:
  1. Parses every fill-coloured path in the SVG (no size/colour thresholds
     beyond white/black background filtering).
  2. Polygons already in data/mga-{docg}.geojson (i.e. those that passed the
     strict extraction) are drawn in solid colour. The rest — candidates for
     missing MGAs — are drawn with a faded fill and a dashed outline.
  3. Click anywhere: the smallest candidate polygon containing the click is
     highlighted in red. Hit "Confirm" to add it to the found list.
  4. Repeat for every missing MGA you can identify.
  5. "Apply transform & save" derives a SVG → calibrated affine from
     colour-rank-paired strict polygons (~150 control points) and appends
     every found polygon to data/mga-{docg}.geojson with svg_colour, area_ha
     and commune.

Run:
  python data-prep/mga_polygon_finder.py --docg Barolo
"""

import argparse
import json
import os
import shutil
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

# Qt setup must happen before matplotlib's Qt backend is imported.
os.environ.setdefault('QT_API', 'pyside6')
import PySide6 as _PySide6  # noqa: E402
_pyside_plugins = (Path(_PySide6.__file__).parent / 'Qt' / 'plugins').as_posix()
os.environ['QT_PLUGIN_PATH'] = _pyside_plugins
os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = _pyside_plugins + '/platforms'

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use('QtAgg')
from matplotlib.backends.backend_qtagg import (  # noqa: E402
    FigureCanvasQTAgg as FigureCanvas,
    NavigationToolbar2QT as NavigationToolbar,
)
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.collections import PatchCollection  # noqa: E402
from matplotlib.patches import Polygon as MplPolygon  # noqa: E402

import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
from shapely.geometry import Point as ShPoint, Polygon as ShPoly, shape  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'data-prep'))
from svg_to_mga_geojson import (  # noqa: E402
    _normalise_colour, _is_near_white, _is_near_black, _is_near_grey,
    parse_path, _COLOUR_RE,
)

STRICT_BBOX_AREA = 3_000  # matches the original extract_polygons default


def affine_lstsq(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = len(src)
    X = np.column_stack([src, np.ones(n)])
    sol, *_ = np.linalg.lstsq(X, dst, rcond=None)
    return sol[:2, :].T, sol[2, :]


# ---------------------------------------------------------------- SVG parsing

def parse_all_paths(svg_path: str) -> list[dict]:
    """Parse every fill-coloured path. Permissive: only excludes near-white
    and near-black (background, text outlines)."""
    tree = ET.parse(svg_path)
    root = tree.getroot()
    ns = '{http://www.w3.org/2000/svg}'

    out = []
    for el in root.iter(f'{ns}path'):
        d = el.get('d', '')
        if not d:
            continue
        style = el.get('style', '')
        fill = el.get('fill', 'none')
        m = _COLOUR_RE.search(style)
        if m:
            fill = m.group(1)
        fill = fill.strip().lower()
        if fill in ('none', '', 'transparent'):
            continue

        colour = _normalise_colour(fill)
        if _is_near_white(colour) or _is_near_black(colour):
            continue

        pts = parse_path(d)
        if len(pts) < 3:
            continue

        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        bbox_w = max(xs) - min(xs)
        bbox_h = max(ys) - min(ys)
        bbox_area = bbox_w * bbox_h
        if bbox_area < 50:
            continue

        try:
            poly = ShPoly(pts)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty or poly.area < 1:
                continue
        except Exception:
            continue

        passes_strict = (
            bbox_area >= STRICT_BBOX_AREA
            and not _is_near_grey(colour)
            and abs(poly.area) >= STRICT_BBOX_AREA * 0.05
        )

        out.append({
            'pts': pts,
            'xs': xs, 'ys': ys,
            'colour': colour,
            'poly': poly,
            'area': float(poly.area),
            'bbox_area': bbox_area,
            'in_strict': passes_strict,
            'centroid': (float(poly.centroid.x), float(poly.centroid.y)),
        })
    return out


# ---------------------------------------------------------------- main window

class PolygonFinder(QtWidgets.QMainWindow):
    def __init__(self, docg: str):
        super().__init__()
        self.docg = docg
        self.svg_path     = ROOT / 'data-prep' / 'reference-maps' / f'{docg.lower()}.svg'
        self.geojson_path = ROOT / 'data' / f'mga-{docg.lower()}.geojson'
        self.docg_path    = ROOT / 'data' / 'docg.geojson'
        self.communes_path = ROOT / 'data' / 'communes.geojson'
        self.found_path   = ROOT / 'data-prep' / f'found-polygons-{docg.lower()}.json'
        self.svg2cal_path = ROOT / 'data-prep' / f'svg-to-calibrated-{docg.lower()}.json'

        self.setWindowTitle(f'MGA Polygon Finder — {docg}')
        self.resize(1500, 900)

        # Parse SVG (slow — 9 MB / 27k paths for Barolo)
        self.statusBar().showMessage('Parsing SVG…')
        QtWidgets.QApplication.processEvents()
        self.all_polys = parse_all_paths(str(self.svg_path))

        # Load current geojson (truth set; calibrated coords)
        self.geojson = json.load(open(self.geojson_path))

        # Load DOCG outline (drawn for context, derived from current geojson)
        self.docg_outline_svg = self._derive_docg_outline_svg()

        # Derive SVG → calibrated affine
        self.A, self.b, self.n_pairs, self.affine_residual_svg = self._derive_affine()
        # Save it
        json.dump({
            'docg': self.docg,
            'A': self.A.tolist(), 'b': self.b.tolist(),
            'note': 'svg_xy → calibrated_lonlat. From colour-rank-paired centroids.',
            'n_pairs': self.n_pairs,
        }, open(self.svg2cal_path, 'w'), indent=2)

        # Demote 'strict' polygons that don't actually match any geojson feature
        # (e.g. dropped by the DOCG clip at build time) so they show up as
        # clickable candidates instead of being hidden as already-extracted.
        self.n_demoted = self._reclassify_strict()
        print(f'Demoted {self.n_demoted} threshold-strict polygons not in '
              f'geojson → now selectable as candidates.')

        # Found polygons (manually confirmed missing)
        self.found_indices: list[int] = []   # indices into self.all_polys
        self._load_found()
        self.highlighted_idx: int | None = None

        self._build_ui()
        self._render_base()
        self._update_status()

    # ---- derived geometry

    def _derive_docg_outline_svg(self):
        """Find the largest non-strict polygon with the most vertices —
        usually the DOCG outline drawn underneath. Just for visual reference."""
        candidates = sorted(
            (p for p in self.all_polys if len(p['pts']) > 200),
            key=lambda p: -p['bbox_area']
        )
        return candidates[0] if candidates else None

    def _derive_affine(self):
        """Bootstrap a SVG → calibrated affine.

        Step 1: coarse affine from largest-strict-polygon-per-colour vs
                largest-geojson-feature-per-colour (~83 pairs).
        Step 2: project every strict polygon's SVG centroid through the
                coarse affine, then for each geojson feature pick the
                nearest-predicted strict polygon of the same colour. This
                naturally excludes legend swatches (their predicted geo
                position is far from any real MGA).
        Step 3: refit the affine on the refined pair set (~150 pairs).
        """
        strict_by_colour: dict[str, list[dict]] = defaultdict(list)
        for p in self.all_polys:
            if p['in_strict']:
                strict_by_colour[p['colour']].append(p)

        geo_by_colour: dict[str, list[dict]] = defaultdict(list)
        for feat in self.geojson['features']:
            c = feat['properties'].get('svg_colour')
            if not c:
                continue
            try:
                geom = shape(feat['geometry'])
            except Exception:
                continue
            geo_by_colour[c].append({
                'centroid': (geom.centroid.x, geom.centroid.y),
                'area':     geom.area,
            })

        # --- Step 1: coarse affine
        src_c, dst_c = [], []
        for c, sl in strict_by_colour.items():
            gl = geo_by_colour.get(c)
            if not gl:
                continue
            src_c.append(max(sl, key=lambda p: p['area'])['centroid'])
            dst_c.append(max(gl, key=lambda f: f['area'])['centroid'])
        if len(src_c) < 3:
            raise RuntimeError(f'Only {len(src_c)} colour matches; need 3+.')
        A_c, b_c = affine_lstsq(np.array(src_c), np.array(dst_c))

        # --- Step 2: nearest-predicted matching within colour
        src, dst = [], []
        for c, sl in strict_by_colour.items():
            gl = geo_by_colour.get(c)
            if not gl:
                continue
            preds = np.array([p['centroid'] for p in sl]) @ A_c.T + b_c
            used: set[int] = set()
            for gf in gl:
                gf_c = np.array(gf['centroid'])
                best_j = -1
                best_d = float('inf')
                for j in range(len(sl)):
                    if j in used:
                        continue
                    d = np.linalg.norm(preds[j] - gf_c)
                    if d < best_d:
                        best_d, best_j = d, j
                if best_j >= 0:
                    used.add(best_j)
                    src.append(sl[best_j]['centroid'])
                    dst.append(gf['centroid'])

        if len(src) < 3:
            raise RuntimeError(f'Only {len(src)} refined pairs; need 3+.')
        src = np.array(src); dst = np.array(dst)
        A, b = affine_lstsq(src, dst)
        pred = src @ A.T + b
        resid = np.linalg.norm(pred - dst, axis=1).mean()
        return A, b, len(src), resid

    def _reclassify_strict(self):
        """Use the refined affine to verify each 'in_strict' polygon actually
        corresponds to a geojson feature. Polygons that pass the size/colour
        thresholds but don't match any geojson centroid of the same colour
        get demoted to candidates so the user can click them.

        Matching is one-to-one per colour: each geojson feature claims its
        nearest unused predicted strict polygon. Anything left unclaimed
        (including all polygons whose colour has zero geojson features) is
        demoted. Returns the number demoted.
        """
        gj_by_colour: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for feat in self.geojson['features']:
            c = feat['properties'].get('svg_colour')
            if not c:
                continue
            try:
                geom = shape(feat['geometry'])
                gj_by_colour[c].append((geom.centroid.x, geom.centroid.y))
            except Exception:
                continue

        matched: set[int] = set()
        for c, gl in gj_by_colour.items():
            sl_idx = [i for i, p in enumerate(self.all_polys)
                      if p['in_strict'] and p['colour'] == c]
            if not sl_idx:
                continue
            preds = (np.array([self.all_polys[i]['centroid'] for i in sl_idx])
                     @ self.A.T + self.b)
            used_local: set[int] = set()
            for gx, gy in gl:
                best_l, best_d = -1, float('inf')
                for l in range(len(sl_idx)):
                    if l in used_local:
                        continue
                    d = np.hypot(preds[l][0] - gx, preds[l][1] - gy)
                    if d < best_d:
                        best_d, best_l = d, l
                if best_l >= 0:
                    used_local.add(best_l)
                    matched.add(sl_idx[best_l])

        demoted = 0
        for i, p in enumerate(self.all_polys):
            if p['in_strict'] and i not in matched:
                p['in_strict'] = False
                demoted += 1
        return demoted

    # ---- persistence of found polygons (by SVG path centroid+area key)

    def _poly_key(self, p: dict) -> str:
        return f"{p['colour']}|{round(p['centroid'][0], 3)}|" \
               f"{round(p['centroid'][1], 3)}|{round(p['area'], 2)}"

    def _load_found(self):
        if not self.found_path.exists():
            return
        try:
            saved = json.load(open(self.found_path))
            saved_keys = set(saved.get('keys', []))
            for i, p in enumerate(self.all_polys):
                if self._poly_key(p) in saved_keys:
                    self.found_indices.append(i)
        except Exception as e:
            print(f'Could not load found-polys file: {e}')

    def _save_found(self):
        keys = [self._poly_key(self.all_polys[i]) for i in self.found_indices]
        self.found_path.parent.mkdir(parents=True, exist_ok=True)
        json.dump({'docg': self.docg, 'keys': keys},
                  open(self.found_path, 'w'), indent=2)

    # ---- UI

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QHBoxLayout(central)

        left = QtWidgets.QVBoxLayout()
        self.fig = Figure(figsize=(11, 9), tight_layout=True)
        self.ax  = self.fig.add_subplot(111)
        self.canvas  = FigureCanvas(self.fig)
        self.toolbar = NavigationToolbar(self.canvas, self)
        self.canvas.mpl_connect('button_press_event', self._on_click)
        left.addWidget(self.toolbar)
        left.addWidget(self.canvas, 1)
        outer.addLayout(left, 4)

        right = QtWidgets.QVBoxLayout()
        self.status_lbl = QtWidgets.QLabel()
        self.status_lbl.setStyleSheet('font-weight:bold; padding:4px;')
        right.addWidget(self.status_lbl)

        self.affine_lbl = QtWidgets.QLabel()
        self.affine_lbl.setWordWrap(True)
        self.affine_lbl.setStyleSheet('color:#666; font-size:11px;')
        right.addWidget(self.affine_lbl)

        self.confirm_btn = QtWidgets.QPushButton('Confirm highlighted polygon')
        self.confirm_btn.setEnabled(False)
        self.confirm_btn.clicked.connect(self._confirm)
        right.addWidget(self.confirm_btn)

        self.clear_hl_btn = QtWidgets.QPushButton('Cancel highlight')
        self.clear_hl_btn.setEnabled(False)
        self.clear_hl_btn.clicked.connect(self._clear_highlight)
        right.addWidget(self.clear_hl_btn)

        right.addWidget(QtWidgets.QLabel('<b>Found polygons</b>'))
        self.found_list = QtWidgets.QListWidget()
        self.found_list.itemDoubleClicked.connect(self._jump_to_found)
        right.addWidget(self.found_list, 1)

        del_btn = QtWidgets.QPushButton('Remove selected from list')
        del_btn.clicked.connect(self._delete_selected_found)
        right.addWidget(del_btn)

        self.save_btn = QtWidgets.QPushButton('Apply transform & append to geojson')
        self.save_btn.clicked.connect(self._apply_and_save)
        right.addWidget(self.save_btn)

        hint = QtWidgets.QLabel(
            'Click on a missing MGA in the canvas. Faded dashed polygons are '
            'candidates; solid ones are already extracted. The smallest '
            'candidate under your click gets highlighted in red.\n\n'
            'Pan/Zoom in the toolbar steals clicks — turn them off when adding.'
        )
        hint.setWordWrap(True)
        hint.setStyleSheet('color:#666; font-size:11px;')
        right.addWidget(hint)

        outer.addLayout(right, 1)

    # ---- rendering

    def _render_base(self):
        self.ax.clear()

        # Build patch collections for fast bulk rendering.
        strict_patches = []
        strict_colors  = []
        cand_patches = []
        cand_colors  = []

        for i, p in enumerate(self.all_polys):
            if i in self.found_indices:
                continue   # drawn separately
            patch = MplPolygon(list(zip(p['xs'], p['ys'])), closed=True)
            if p['in_strict']:
                strict_patches.append(patch)
                strict_colors.append(p['colour'])
            else:
                cand_patches.append(patch)
                cand_colors.append(p['colour'])

        # Faded candidate fills behind, then strict fills on top
        if cand_patches:
            pc = PatchCollection(cand_patches, facecolors=cand_colors,
                                 edgecolors='#cc8800', linewidths=0.8,
                                 linestyles='--', alpha=0.20)
            self.ax.add_collection(pc)
        if strict_patches:
            ps = PatchCollection(strict_patches, facecolors=strict_colors,
                                 edgecolors='#5a3e2c', linewidths=0.4,
                                 alpha=0.65)
            self.ax.add_collection(ps)

        # Confirmed-found polygons in green
        for i in self.found_indices:
            p = self.all_polys[i]
            self.ax.fill(p['xs'], p['ys'], color=p['colour'],
                         edgecolor='#1c6b1c', linewidth=1.6, alpha=0.85)

        # Highlight on top
        self._draw_highlight()

        # Set sensible limits from SVG content
        all_xs = [p['xs'] for p in self.all_polys]
        all_ys = [p['ys'] for p in self.all_polys]
        if all_xs:
            xmin = min(min(xs) for xs in all_xs)
            xmax = max(max(xs) for xs in all_xs)
            ymin = min(min(ys) for ys in all_ys)
            ymax = max(max(ys) for ys in all_ys)
            pad = 0.02 * max(xmax - xmin, ymax - ymin)
            self.ax.set_xlim(xmin - pad, xmax + pad)
            self.ax.set_ylim(ymax + pad, ymin - pad)  # SVG Y down

        self.ax.set_aspect('equal')
        self.ax.set_title(
            f'{self.docg} — solid = already extracted, dashed = candidates'
        )
        self.canvas.draw_idle()
        self._refresh_found_list()

    def _draw_highlight(self):
        if self.highlighted_idx is None:
            return
        p = self.all_polys[self.highlighted_idx]
        # Hatched red overlay
        self.ax.fill(p['xs'], p['ys'], facecolor='none',
                     edgecolor='#d32f2f', linewidth=2.4)

    # ---- click

    def _on_click(self, event):
        if event.inaxes is not self.ax: return
        if self.toolbar.mode != '': return
        if event.xdata is None: return

        click = ShPoint(event.xdata, event.ydata)
        candidates = []
        for i, p in enumerate(self.all_polys):
            if p['in_strict'] or i in self.found_indices:
                continue
            try:
                if p['poly'].contains(click):
                    candidates.append((i, p['area']))
            except Exception:
                continue
        if not candidates:
            self.statusBar().showMessage('No candidate polygon under that click.')
            return
        candidates.sort(key=lambda t: t[1])  # smallest first
        self.highlighted_idx = candidates[0][0]
        self.confirm_btn.setEnabled(True)
        self.clear_hl_btn.setEnabled(True)
        self._render_base()
        self._update_status()

    def _confirm(self):
        if self.highlighted_idx is None:
            return
        if self.highlighted_idx not in self.found_indices:
            self.found_indices.append(self.highlighted_idx)
            self._save_found()
        self.highlighted_idx = None
        self.confirm_btn.setEnabled(False)
        self.clear_hl_btn.setEnabled(False)
        self._render_base()
        self._update_status()

    def _clear_highlight(self):
        self.highlighted_idx = None
        self.confirm_btn.setEnabled(False)
        self.clear_hl_btn.setEnabled(False)
        self._render_base()

    # ---- found list

    def _refresh_found_list(self):
        self.found_list.clear()
        for i in self.found_indices:
            p = self.all_polys[i]
            cx, cy = p['centroid']
            self.found_list.addItem(
                f'#{i}  {p["colour"]}  area={p["area"]:.0f}  ({cx:.0f},{cy:.0f})')

    def _jump_to_found(self, item):
        idx_text = item.text().split()[0].lstrip('#')
        try:
            i = int(idx_text)
        except ValueError:
            return
        if i not in self.found_indices:
            return
        p = self.all_polys[i]
        pad = max(p['xs']) - min(p['xs'])
        pad = max(pad, max(p['ys']) - min(p['ys']))
        cx, cy = p['centroid']
        self.ax.set_xlim(cx - 2 * pad, cx + 2 * pad)
        self.ax.set_ylim(cy + 2 * pad, cy - 2 * pad)
        self.canvas.draw_idle()

    def _delete_selected_found(self):
        for it in self.found_list.selectedItems():
            idx_text = it.text().split()[0].lstrip('#')
            try:
                i = int(idx_text)
                self.found_indices.remove(i)
            except (ValueError, ValueError):
                pass
        self._save_found()
        self._render_base()
        self._update_status()

    # ---- status

    def _update_status(self):
        n_strict = sum(1 for p in self.all_polys if p['in_strict'])
        n_cand   = sum(1 for p in self.all_polys if not p['in_strict']) - len(self.found_indices)
        n_found  = len(self.found_indices)
        hl_info = ''
        if self.highlighted_idx is not None:
            p = self.all_polys[self.highlighted_idx]
            hl_info = (f'Highlighted: {p["colour"]}, area={p["area"]:.0f}, '
                       f'{len(p["pts"])} verts')
        self.status_lbl.setText(
            f'Strict polygons: {n_strict}\n'
            f'Candidates remaining: {n_cand}\n'
            f'Found: {n_found}\n'
            f'Demoted (size-OK but not in geojson): {self.n_demoted}\n'
            f'{hl_info}'
        )
        # affine residual in geo degrees → convert to metres
        self.affine_lbl.setText(
            f'SVG → calibrated affine derived from {self.n_pairs} colour-rank pairs.\n'
            f'Mean residual: {self.affine_residual_svg * 111_320:.0f} m'
        )

    # ---- save

    def _apply_affine(self, pts: list[tuple[float, float]]) -> list[list[float]]:
        arr = np.array(pts)
        out = arr @ self.A.T + self.b
        return [[float(x), float(y)] for x, y in out]

    def _apply_and_save(self):
        if not self.found_indices:
            QtWidgets.QMessageBox.information(
                self, 'Nothing to save', 'No polygons confirmed yet.')
            return

        # Backup geojson
        backup = self.geojson_path.with_suffix('.geojson.bakfinder')
        shutil.copy2(self.geojson_path, backup)

        # Communes for spatial assignment
        communes = gpd.read_file(self.communes_path).to_crs(epsg=4326)
        if 'docg' in communes.columns:
            communes = communes[
                communes['docg'].fillna('').str.contains(self.docg, case=False)
            ]

        gj = json.load(open(self.geojson_path))
        existing_shapes = []
        for feat in gj['features']:
            try:
                existing_shapes.append(shape(feat['geometry']))
            except Exception:
                existing_shapes.append(None)

        n_added = n_dup = 0
        for i in self.found_indices:
            p = self.all_polys[i]
            cal_coords = self._apply_affine(p['pts'])
            if cal_coords[0] != cal_coords[-1]:
                cal_coords.append(cal_coords[0])
            try:
                cal_poly = ShPoly(cal_coords)
                if not cal_poly.is_valid:
                    cal_poly = cal_poly.buffer(0)
                if cal_poly.is_empty:
                    continue
            except Exception:
                continue

            # Light dedup vs whatever's in the geojson (50% IoU-ish)
            duplicate = False
            for ex in existing_shapes:
                if ex is None or ex.is_empty:
                    continue
                try:
                    inter = cal_poly.intersection(ex).area
                    if inter / max(cal_poly.area, ex.area) > 0.5:
                        duplicate = True
                        break
                except Exception:
                    continue
            if duplicate:
                n_dup += 1
                continue

            cpt = cal_poly.centroid
            commune = None; best = float('inf')
            for _, row in communes.iterrows():
                if row.geometry.contains(cpt):
                    commune = row['name']
                    break
                d = cpt.distance(row.geometry)
                if d < best:
                    best = d; commune = row['name']

            gj['features'].append({
                'type': 'Feature',
                'geometry': {'type': 'Polygon', 'coordinates': [cal_coords]},
                'properties': {
                    'name':       '',
                    'comune':     commune,
                    'docg':       self.docg,
                    'svg_colour': p['colour'],
                    'area_ha':    round(cal_poly.area * 111_320**2 * 0.0001, 1),
                },
            })
            existing_shapes.append(cal_poly)
            n_added += 1

        json.dump(gj, open(self.geojson_path, 'w'), indent=2)
        QtWidgets.QMessageBox.information(
            self, 'Saved',
            f'Appended {n_added} new polygons to {self.geojson_path.name}.\n'
            f'Skipped {n_dup} as duplicates of existing.\n'
            f'Backup at {backup.name}.\n\n'
            'Hard-refresh the browser to see them.\n'
            'Run mga_labeler.py to label the new polygons.'
        )


def main():
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--docg', choices=['Barolo', 'Barbaresco'], default='Barolo')
    args = p.parse_args()
    app = QtWidgets.QApplication(sys.argv)
    win = PolygonFinder(args.docg)
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
