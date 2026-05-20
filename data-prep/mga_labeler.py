"""
mga_labeler.py
--------------
PySide6 GUI for assigning official MGA names to extracted polygons.

Workflow:
  1. Loads data/mga-{docg}.geojson (calibrated polygons with svg_colour).
  2. Loads data-prep/reference-maps/mga-names-{docg}.txt for autocomplete.
  3. Click any polygon → autocomplete name picker. Bulk-by-colour is on by
     default (one MGA in the PDF usually has one colour, so all fragments
     get the same name in one shot).
  4. Sidebar shows progress, unused names, and labeled MGAs.
  5. "Save" writes the `name` property into each feature.

Run:
  python data-prep/mga_labeler.py --docg Barolo
"""

import argparse
import json
import os
import shutil
import sys
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

import geopandas as gpd  # noqa: E402
import numpy as np  # noqa: E402
from shapely.geometry import Point as ShPoint, Polygon as ShPoly, shape  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'data-prep'))
from svg_to_mga_geojson import extract_polygons  # noqa: E402


def affine_lstsq(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Solve dst_row = src_row @ A.T + b via least squares. Returns (A, b)."""
    n = len(src)
    X = np.column_stack([src, np.ones(n)])
    sol, *_ = np.linalg.lstsq(X, dst, rcond=None)
    return sol[:2, :].T, sol[2, :]


def load_official_names(docg: str) -> list[str]:
    path = ROOT / 'data-prep' / 'reference-maps' / f'mga-names-{docg.lower()}.txt'
    out = []
    seen = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line not in seen:
            seen.add(line)
            out.append(line)
    return out


# ---------------------------------------------------------------- dialog

class LabelDialog(QtWidgets.QDialog):
    def __init__(self, available: list[str], current_color: str,
                 group_size: int, current_name: str | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Label polygon')
        self.selected_name: str | None = None
        self.apply_to_color = group_size > 1

        layout = QtWidgets.QFormLayout(self)

        swatch = QtWidgets.QLabel()
        swatch.setFixedSize(50, 22)
        swatch.setStyleSheet(f'background: {current_color}; '
                             f'border: 1px solid #888;')
        layout.addRow('SVG colour:', swatch)
        layout.addRow('Group size:',
                      QtWidgets.QLabel(f'{group_size} polygon(s) share this colour'))
        if current_name:
            layout.addRow('Current name:', QtWidgets.QLabel(current_name))

        self.combo = QtWidgets.QComboBox()
        self.combo.setEditable(True)
        self.combo.addItem('')
        self.combo.addItems(available)
        completer = QtWidgets.QCompleter(available, self)
        completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        completer.setFilterMode(QtCore.Qt.MatchContains)
        self.combo.setCompleter(completer)
        layout.addRow('Name:', self.combo)

        self.bulk_check = QtWidgets.QCheckBox(
            f'Apply to all {group_size} polygons of this colour')
        self.bulk_check.setChecked(group_size > 1)
        if group_size <= 1:
            self.bulk_check.setEnabled(False)
        layout.addRow('', self.bulk_check)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        discard_btn = QtWidgets.QPushButton('Discard (not an MGA)')
        btns.addButton(discard_btn, QtWidgets.QDialogButtonBox.DestructiveRole)
        discard_btn.clicked.connect(self._discard)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

        self.combo.setFocus()
        if self.combo.lineEdit():
            self.combo.lineEdit().selectAll()

    def _accept(self):
        name = self.combo.currentText().strip()
        if not name:
            QtWidgets.QMessageBox.warning(self, 'No name',
                                          'Pick a name or click Discard.')
            return
        self.selected_name = name
        self.apply_to_color = self.bulk_check.isChecked()
        self.accept()

    def _discard(self):
        self.selected_name = '__DISCARD__'
        self.apply_to_color = self.bulk_check.isChecked()
        self.accept()


# ---------------------------------------------------------------- main

class Labeler(QtWidgets.QMainWindow):
    def __init__(self, docg: str):
        super().__init__()
        self.docg = docg
        self.geojson_path  = ROOT / 'data' / f'mga-{docg.lower()}.geojson'
        self.working_path  = ROOT / 'data-prep' / f'mga-{docg.lower()}.working.geojson'
        self.labels_path   = ROOT / 'data-prep' / f'mga-labels-{docg.lower()}.json'
        self.svg_path      = ROOT / 'data-prep' / 'reference-maps' / f'{docg.lower()}.svg'
        self.docg_path     = ROOT / 'data' / 'docg.geojson'
        self.communes_path = ROOT / 'data' / 'communes.geojson'

        self.setWindowTitle(f'MGA Labeler — {docg}')
        self.resize(1500, 900)

        self.official_names = load_official_names(docg)
        # Prefer the working geojson if it exists — it carries scan-found
        # candidate polygons across sessions.
        load_path = self.working_path if self.working_path.exists() else self.geojson_path
        self.geojson = json.load(open(load_path))
        # Track which file we loaded from — labels.json indices are only
        # valid against the working.geojson that produced them. If we fell
        # back to the main geojson (e.g. after the finder appended new
        # polygons), labels.json is stale and must be ignored in favour of
        # names baked into the main geojson.
        self._loaded_from_working = (load_path == self.working_path)

        # int feature_idx -> str name
        self.feature_labels: dict[int, str] = {}
        self.discarded: set[int] = set()
        self._load_labels()

        self._build_ui()
        self._redraw()

    # -------- persistence

    def _load_labels(self):
        # Seed from any 'name' baked into the geojson features. These were
        # written by the last Save and are authoritative for the main geojson.
        for i, feat in enumerate(self.geojson['features']):
            existing = feat['properties'].get('name')
            if existing:
                self.feature_labels[i] = existing
        # Only overlay labels.json if we loaded the working geojson it was
        # written against — otherwise its indices may be stale (e.g. after
        # the finder appended new polygons to the main geojson).
        if self._loaded_from_working and self.labels_path.exists():
            try:
                data = json.load(open(self.labels_path))
                self.feature_labels = {int(k): v
                                       for k, v in data.get('labels', {}).items()}
                self.discarded = set(int(x) for x in data.get('discarded', []))
            except Exception as e:
                print(f'Could not load labels file: {e}')
        elif not self._loaded_from_working and self.labels_path.exists():
            print(f'Skipping labels.json (loaded main geojson directly; '
                  f'using names baked into features instead).')

    def _save_labels(self):
        self.labels_path.parent.mkdir(parents=True, exist_ok=True)
        json.dump({
            'docg': self.docg,
            'labels':    {str(k): v for k, v in self.feature_labels.items()},
            'discarded': sorted(self.discarded),
        }, open(self.labels_path, 'w'), indent=2)
        # Keep working.geojson in lock-step with labels.json so its indices
        # remain valid on next launch — otherwise the labeler will fall back
        # to the main geojson and ignore labels.json as stale.
        self._persist_working()

    # -------- ui

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
        outer.addLayout(left, 3)

        right = QtWidgets.QVBoxLayout()
        self.status = QtWidgets.QLabel()
        self.status.setStyleSheet('font-weight: bold; padding: 4px;')
        right.addWidget(self.status)

        self.show_labels = QtWidgets.QCheckBox('Show name labels on map')
        self.show_labels.toggled.connect(self._redraw)
        right.addWidget(self.show_labels)

        self.save_btn = QtWidgets.QPushButton('Save → write geojson')
        self.save_btn.clicked.connect(self._save_and_write)
        right.addWidget(self.save_btn)

        self.scan_btn = QtWidgets.QPushButton('Scan SVG for missing MGAs')
        self.scan_btn.clicked.connect(self._scan_for_missing)
        right.addWidget(self.scan_btn)

        tabs = QtWidgets.QTabWidget()

        # Unused names tab
        unused_box = QtWidgets.QWidget()
        ul = QtWidgets.QVBoxLayout(unused_box); ul.setContentsMargins(0, 0, 0, 0)
        self.unused_filter = QtWidgets.QLineEdit()
        self.unused_filter.setPlaceholderText('Filter unused names...')
        self.unused_filter.textChanged.connect(self._filter_unused)
        self.unused_list = QtWidgets.QListWidget()
        ul.addWidget(self.unused_filter)
        ul.addWidget(self.unused_list)
        tabs.addTab(unused_box, 'Unused')

        # Labeled tab
        labeled_box = QtWidgets.QWidget()
        ll = QtWidgets.QVBoxLayout(labeled_box); ll.setContentsMargins(0, 0, 0, 0)
        self.labeled_filter = QtWidgets.QLineEdit()
        self.labeled_filter.setPlaceholderText('Filter labeled...')
        self.labeled_filter.textChanged.connect(self._filter_labeled)
        self.labeled_list = QtWidgets.QListWidget()
        self.labeled_list.itemDoubleClicked.connect(self._jump_to_labeled)
        ll.addWidget(self.labeled_filter)
        ll.addWidget(self.labeled_list)
        tabs.addTab(labeled_box, 'Labeled')

        right.addWidget(tabs, 1)

        hint = QtWidgets.QLabel(
            'Click a polygon to label it. Pan/Zoom in the toolbar must be off '
            'while labeling. Double-click a labeled name to jump to it.'
        )
        hint.setWordWrap(True)
        hint.setStyleSheet('color: #666; font-size: 11px;')
        right.addWidget(hint)

        outer.addLayout(right, 1)

    # -------- filtering

    def _filter_unused(self, text: str):
        text = text.strip().lower()
        for i in range(self.unused_list.count()):
            it = self.unused_list.item(i)
            it.setHidden(text not in it.text().lower())

    def _filter_labeled(self, text: str):
        text = text.strip().lower()
        for i in range(self.labeled_list.count()):
            it = self.labeled_list.item(i)
            it.setHidden(text not in it.text().lower())

    # -------- drawing

    def _used_names(self) -> set[str]:
        return set(self.feature_labels.values())

    def _redraw(self):
        # Preserve current axis limits across redraws (so labeling doesn't reset zoom).
        prev_xlim = self.ax.get_xlim() if self.ax.has_data() else None
        prev_ylim = self.ax.get_ylim() if self.ax.has_data() else None

        self.ax.clear()
        for i, feat in enumerate(self.geojson['features']):
            geom = feat['geometry']
            if geom['type'] != 'Polygon':
                continue
            ring = geom['coordinates'][0]
            xs = [c[0] for c in ring]
            ys = [c[1] for c in ring]
            color = feat['properties'].get('svg_colour') or '#cccccc'

            is_candidate = bool(feat['properties'].get('_candidate'))

            edge = '#5a3e2c'; lw = 0.4; alpha = 0.6
            if i in self.discarded:
                color = '#bdbdbd'; alpha = 0.18
            elif i in self.feature_labels:
                edge = '#1a1a1a'; lw = 1.0; alpha = 0.75
            elif is_candidate:
                edge = '#cc8800'; lw = 0.4; alpha = 0.6

            self.ax.fill(xs, ys, color=color, edgecolor=edge,
                         linewidth=lw, alpha=alpha)
            if (is_candidate and i not in self.feature_labels
                    and i not in self.discarded):
                # Dashed gold outline so missing-MGA candidates stand out
                self.ax.plot(xs, ys, color='#cc8800', linewidth=1.4,
                             linestyle='--', alpha=0.9)

            if self.show_labels.isChecked() and i in self.feature_labels:
                cx = float(np.mean(xs)); cy = float(np.mean(ys))
                self.ax.text(cx, cy, self.feature_labels[i], fontsize=6,
                             ha='center', va='center', color='#222')

        self.ax.set_aspect('equal')
        self.ax.set_title(f'{self.docg} — click a polygon to label')
        if prev_xlim and prev_xlim != (0.0, 1.0):
            self.ax.set_xlim(prev_xlim)
            self.ax.set_ylim(prev_ylim)
        self.canvas.draw_idle()
        self._refresh_lists()

    def _refresh_lists(self):
        used = self._used_names()
        self.unused_list.clear()
        for n in self.official_names:
            if n not in used:
                self.unused_list.addItem(n)
        self.labeled_list.clear()
        for n in sorted(used):
            self.labeled_list.addItem(n)
        total_official = len(self.official_names)
        unique = len(used)
        n_polys = len(self.geojson['features'])
        n_labeled_polys = len(self.feature_labels)
        self.status.setText(
            f'MGAs labeled: {unique} / {total_official}\n'
            f'Polygons:     {n_labeled_polys} labeled, '
            f'{len(self.discarded)} discarded, of {n_polys} total'
        )
        # re-apply filters
        self._filter_unused(self.unused_filter.text())
        self._filter_labeled(self.labeled_filter.text())

    def _jump_to_labeled(self, item):
        name = item.text()
        for i, lbl in self.feature_labels.items():
            if lbl != name:
                continue
            ring = self.geojson['features'][i]['geometry']['coordinates'][0]
            xs = [c[0] for c in ring]; ys = [c[1] for c in ring]
            pad = 0.005
            self.ax.set_xlim(min(xs) - pad, max(xs) + pad)
            self.ax.set_ylim(min(ys) - pad, max(ys) + pad)
            self.canvas.draw_idle()
            return

    # -------- click → label

    def _on_click(self, event):
        if event.inaxes is not self.ax: return
        if self.toolbar.mode != '': return
        if event.xdata is None: return
        click = ShPoint(event.xdata, event.ydata)
        hit = None
        for i, feat in enumerate(self.geojson['features']):
            geom = feat['geometry']
            if geom['type'] != 'Polygon':
                continue
            try:
                if shape(geom).contains(click):
                    hit = i
                    break
            except Exception:
                continue
        if hit is None:
            self.status.setText('No polygon under that click. Try again.')
            return
        self._open_label_dialog(hit)

    def _open_label_dialog(self, idx: int):
        feat = self.geojson['features'][idx]
        color = feat['properties'].get('svg_colour') or '#cccccc'
        same_color = [
            i for i, f in enumerate(self.geojson['features'])
            if (f['properties'].get('svg_colour') or '#cccccc') == color
            and i not in self.discarded
        ]
        used = self._used_names()
        avail = [n for n in self.official_names if n not in used]
        current = self.feature_labels.get(idx)
        if current and current not in avail:
            avail = [current] + avail

        dlg = LabelDialog(avail, color, len(same_color), current, self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        targets = same_color if dlg.apply_to_color else [idx]
        if dlg.selected_name == '__DISCARD__':
            for i in targets:
                self.discarded.add(i)
                self.feature_labels.pop(i, None)
        else:
            for i in targets:
                self.feature_labels[i] = dlg.selected_name
                self.discarded.discard(i)
        self._save_labels()
        self._redraw()

    # -------- scan SVG for missing MGAs

    def _persist_working(self):
        """Write the in-memory feature list (originals + candidates) so they
        survive a quit. The labeler prefers this file on next launch."""
        self.working_path.parent.mkdir(parents=True, exist_ok=True)
        json.dump(self.geojson, open(self.working_path, 'w'), indent=2)

    def _scan_for_missing(self):
        if not self.svg_path.exists():
            QtWidgets.QMessageBox.warning(
                self, 'No SVG', f'{self.svg_path} not found.')
            return

        # 1. Re-extract from SVG with relaxed thresholds.
        polys_raw = extract_polygons(str(self.svg_path), min_bbox_area=500)
        if not polys_raw:
            QtWidgets.QMessageBox.warning(self, 'Scan failed',
                                          'No polygons extracted from SVG.')
            return

        # 2. Build the same SVG bbox → DOCG bbox rough transform that the
        # original extraction used, so colours align with the calibrated set.
        all_svg = np.array([pt for p in polys_raw for pt in p['pts']])
        svg_min = np.percentile(all_svg, 2, axis=0)
        svg_max = np.percentile(all_svg, 98, axis=0)

        docg_gdf = gpd.read_file(self.docg_path).to_crs(epsg=4326)
        match = docg_gdf[docg_gdf['name'].fillna('').str.contains(self.docg, case=False)]
        if match.empty:
            QtWidgets.QMessageBox.warning(self, 'Missing DOCG', 'No DOCG geometry.')
            return
        docg_clip = match.geometry.iloc[0].buffer(0.01)
        min_lon, min_lat, max_lon, max_lat = docg_clip.bounds

        rough = []
        for p in polys_raw:
            pts = np.array(p['pts'])
            if len(pts) < 3:
                continue
            lon = min_lon + (pts[:, 0] - svg_min[0]) / (svg_max[0] - svg_min[0]) * (max_lon - min_lon)
            lat = max_lat - (pts[:, 1] - svg_min[1]) / (svg_max[1] - svg_min[1]) * (max_lat - min_lat)
            coords = list(zip(lon.tolist(), lat.tolist()))
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            try:
                poly = ShPoly(coords)
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if poly.is_empty or poly.area < 1e-12:
                    continue
            except Exception:
                continue
            if not docg_clip.intersects(poly):
                continue
            rough.append({
                'coords': [[float(c[0]), float(c[1])] for c in coords],
                'colour': p['colour'],
                'centroid': (poly.centroid.x, poly.centroid.y),
                'area': poly.area,
            })

        # 3. For each colour, take the LARGEST polygon in the calibrated
        # geojson and the LARGEST in the rough re-extraction. Their centroids
        # form a colour-paired control point.
        cal_largest: dict[str, dict] = {}
        for feat in self.geojson['features']:
            if feat['properties'].get('_candidate'):
                continue  # don't bootstrap from prior scan candidates
            c = feat['properties'].get('svg_colour')
            if not c:
                continue
            try:
                geom = shape(feat['geometry'])
            except Exception:
                continue
            entry = cal_largest.get(c)
            if entry is None or entry['area'] < geom.area:
                cal_largest[c] = {
                    'centroid': (geom.centroid.x, geom.centroid.y),
                    'area':     geom.area,
                }

        rough_largest: dict[str, dict] = {}
        for rp in rough:
            entry = rough_largest.get(rp['colour'])
            if entry is None or entry['area'] < rp['area']:
                rough_largest[rp['colour']] = rp

        pairs_src, pairs_dst = [], []
        for c, cal in cal_largest.items():
            rp = rough_largest.get(c)
            if rp is None:
                continue
            pairs_src.append(rp['centroid'])
            pairs_dst.append(cal['centroid'])

        if len(pairs_src) < 3:
            QtWidgets.QMessageBox.warning(
                self, 'Not enough colour matches',
                f'Only {len(pairs_src)} colour-pairs found. Need 3+.')
            return

        src = np.array(pairs_src)
        dst = np.array(pairs_dst)
        A, b = affine_lstsq(src, dst)

        # Save the matrix for any other tooling.
        json.dump({
            'docg': self.docg,
            'A': A.tolist(),
            'b': b.tolist(),
            'note': 'Derived from largest-polygon-per-colour pairs during scan.',
            'n_pairs': len(pairs_src),
        }, open(ROOT / 'data-prep' / f'calibration-{self.docg.lower()}.json',
                'w'), indent=2)

        pred = src @ A.T + b
        mean_err_m = np.linalg.norm(pred - dst, axis=1).mean() * 111_320

        # 4. Apply affine to every rough polygon, dedupe vs existing, append
        # new ones as candidates.
        existing_shapes = []
        for feat in self.geojson['features']:
            try:
                existing_shapes.append(shape(feat['geometry']))
            except Exception:
                existing_shapes.append(None)

        communes = gpd.read_file(self.communes_path).to_crs(epsg=4326)
        if 'docg' in communes.columns:
            communes = communes[
                communes['docg'].fillna('').str.contains(self.docg, case=False)
            ]

        n_new = n_dup = 0
        for rp in rough:
            arr = np.array(rp['coords'])
            cal_xy = (arr @ A.T + b).tolist()
            try:
                poly = ShPoly(cal_xy)
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if poly.is_empty:
                    continue
            except Exception:
                continue

            duplicate = False
            for ex in existing_shapes:
                if ex is None or ex.is_empty:
                    continue
                try:
                    inter = poly.intersection(ex).area
                    if inter / max(poly.area, ex.area) > 0.5:
                        duplicate = True
                        break
                except Exception:
                    continue
            if duplicate:
                n_dup += 1
                continue

            commune = None
            best = float('inf')
            cpt = poly.centroid
            for _, row in communes.iterrows():
                if row.geometry.contains(cpt):
                    commune = row['name']
                    break
                d = cpt.distance(row.geometry)
                if d < best:
                    best = d
                    commune = row['name']

            self.geojson['features'].append({
                'type': 'Feature',
                'geometry': {'type': 'Polygon', 'coordinates': [cal_xy]},
                'properties': {
                    'name':       '',
                    'comune':     commune,
                    'docg':       self.docg,
                    'svg_colour': rp['colour'],
                    'area_ha':    round(poly.area * 111_320**2 * 0.0001, 1),
                    '_candidate': True,
                },
            })
            existing_shapes.append(poly)
            n_new += 1

        self._persist_working()
        self._redraw()
        QtWidgets.QMessageBox.information(
            self, 'Scan complete',
            f'Found {n_new} new candidate polygons.\n'
            f'Skipped {n_dup} as duplicates of existing polygons.\n\n'
            f'Affine derived from {len(pairs_src)} colour pairs.\n'
            f'Mean residual: {mean_err_m:.1f} m.\n\n'
            f'Candidates show with a dashed gold outline. Click each to '
            f'label or discard.')

    # -------- write geojson

    def _save_and_write(self):
        backup = self.geojson_path.with_suffix('.geojson.bak2')
        if self.geojson_path.exists():
            shutil.copy2(self.geojson_path, backup)
        out_features = []
        n_skipped_candidates = 0
        for i, feat in enumerate(self.geojson['features']):
            if i in self.discarded:
                continue
            is_candidate = bool(feat['properties'].get('_candidate'))
            is_labeled   = i in self.feature_labels
            # Drop unlabeled candidates from the website output — they're
            # provisional until the user names them.
            if is_candidate and not is_labeled:
                n_skipped_candidates += 1
                continue
            new = json.loads(json.dumps(feat))
            new['properties']['name'] = self.feature_labels.get(i, '')
            new['properties'].pop('_candidate', None)
            out_features.append(new)
        json.dump({'type': 'FeatureCollection', 'features': out_features},
                  open(self.geojson_path, 'w'), indent=2)
        # Keep the working file alive so unlabeled candidates persist.
        self._persist_working()
        QtWidgets.QMessageBox.information(
            self, 'Saved',
            f'Wrote {len(out_features)} features to {self.geojson_path.name}.\n'
            f'Discarded: {len(self.discarded)}.\n'
            f'Unlabeled candidates kept for next session: {n_skipped_candidates}.\n'
            f'Backup: {backup.name}.\n\n'
            'Hard-refresh the browser to see updated colours / names.')


def main():
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--docg', choices=['Barolo', 'Barbaresco'], default='Barolo')
    args = p.parse_args()
    app = QtWidgets.QApplication(sys.argv)
    win = Labeler(args.docg)
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
