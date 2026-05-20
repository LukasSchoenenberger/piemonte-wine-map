"""
mga_calibrator.py
-----------------
PySide6 GUI for calibrating extracted MGA polygons against real-world coordinates.

Workflow:
  1. Open the GUI; it loads data/mga-{docg}.geojson plus communes / DOCG for context.
  2. Click "Add point", click a recognisable spot on the MGA map, enter the real
     lon/lat from Google Maps. Repeat for >= 3 well-distributed points.
  3. Click "Apply calibration" — solves a least-squares affine transform from
     the control points and overwrites data/mga-{docg}.geojson (with a backup).

Control points are persisted to data-prep/calibration-points-{docg}.json so you
can quit and resume.

Run:
  python data-prep/mga_calibrator.py --docg Barolo
  python data-prep/mga_calibrator.py --docg Barbaresco
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

# Force matplotlib's Qt binding to PySide6, and import PySide6 before the
# matplotlib Qt backend (otherwise mpl auto-detects PyQt and a later PySide6
# import fails with a Qt symbol-version error).
os.environ.setdefault('QT_API', 'pyside6')

# Multiple Qt installs are present in the env (PySide6, PyQt5, opencv, system
# qt6). Pin the platform-plugin path to PySide6's bundle to avoid the
# "Could not find the Qt platform plugin xcb" error.
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


ROOT = Path(__file__).resolve().parent.parent


def affine_lstsq(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Solve dst = A @ src.T + b via least squares. Returns (A, b)."""
    n = len(src)
    if n < 3:
        # Fall back to scale + offset (4 params): dst_x = sx*src_x + tx etc.
        sx = (dst[:, 0].max() - dst[:, 0].min()) / max(src[:, 0].max() - src[:, 0].min(), 1e-12)
        sy = (dst[:, 1].max() - dst[:, 1].min()) / max(src[:, 1].max() - src[:, 1].min(), 1e-12)
        tx = dst[:, 0].mean() - sx * src[:, 0].mean()
        ty = dst[:, 1].mean() - sy * src[:, 1].mean()
        A = np.array([[sx, 0], [0, sy]])
        b = np.array([tx, ty])
        return A, b

    X = np.column_stack([src, np.ones(n)])  # (n, 3)
    sol, *_ = np.linalg.lstsq(X, dst, rcond=None)  # (3, 2)
    A = sol[:2, :].T  # (2, 2)
    b = sol[2, :]     # (2,)
    return A, b


def apply_affine_coords(coords, A, b):
    """coords: list of [lon, lat]; returns transformed list."""
    pts = np.array(coords)
    out = pts @ A.T + b
    return out.tolist()


def transform_geojson(gj: dict, A: np.ndarray, b: np.ndarray) -> dict:
    """Apply affine to every coordinate in a Polygon/MultiPolygon FeatureCollection."""
    for feat in gj['features']:
        geom = feat['geometry']
        if geom['type'] == 'Polygon':
            geom['coordinates'] = [apply_affine_coords(ring, A, b)
                                   for ring in geom['coordinates']]
        elif geom['type'] == 'MultiPolygon':
            geom['coordinates'] = [
                [apply_affine_coords(ring, A, b) for ring in poly]
                for poly in geom['coordinates']
            ]
    return gj


# ---------------------------------------------------------------- dialog

class TargetDialog(QtWidgets.QDialog):
    def __init__(self, src_x: float, src_y: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Enter real coordinates')
        self.target = None

        layout = QtWidgets.QFormLayout(self)
        layout.addRow('Clicked (lon, lat):',
                      QtWidgets.QLabel(f'{src_x:.6f},  {src_y:.6f}'))

        self.lon_edit = QtWidgets.QLineEdit()
        self.lat_edit = QtWidgets.QLineEdit()
        self.lon_edit.setPlaceholderText('e.g. 7.94532')
        self.lat_edit.setPlaceholderText('e.g. 44.61234')
        layout.addRow('Real longitude:', self.lon_edit)
        layout.addRow('Real latitude:',  self.lat_edit)

        hint = QtWidgets.QLabel(
            'Tip: in Google Maps, right-click → click the coords to copy as "lat, lon".\n'
            'Paste either into either field — the parser handles "lat, lon" too.'
        )
        hint.setStyleSheet('color: #777; font-size: 11px;')
        layout.addRow(hint)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

        self.lon_edit.setFocus()

    def _on_ok(self):
        lon_raw = self.lon_edit.text().strip()
        lat_raw = self.lat_edit.text().strip()

        # Allow pasting "44.612, 7.945" into either field
        for raw in (lon_raw, lat_raw):
            if ',' in raw:
                parts = [p.strip() for p in raw.split(',')]
                if len(parts) == 2:
                    a, b = float(parts[0]), float(parts[1])
                    # Heuristic: in Piemonte, lat ~44, lon ~7-8
                    lat, lon = (a, b) if a > b else (b, a)
                    self.target = [lon, lat]
                    self.accept()
                    return
        try:
            self.target = [float(lon_raw), float(lat_raw)]
            self.accept()
        except ValueError:
            QtWidgets.QMessageBox.warning(self, 'Invalid input',
                                          'Could not parse coordinates.')


# ---------------------------------------------------------------- main window

class Calibrator(QtWidgets.QMainWindow):
    def __init__(self, docg: str):
        super().__init__()
        self.docg = docg
        self.mga_path     = ROOT / 'data' / f'mga-{docg.lower()}.geojson'
        self.communes_path = ROOT / 'data' / 'communes.geojson'
        self.docg_path     = ROOT / 'data' / 'docg.geojson'
        self.points_path   = ROOT / 'data-prep' / f'calibration-points-{docg.lower()}.json'

        self.setWindowTitle(f'MGA Calibrator — {docg}')
        self.resize(1300, 850)

        self.points = self._load_points()
        self.capture_mode = False

        self._build_ui()
        self._load_geo()
        self._redraw()

    # -------- persistence

    def _load_points(self) -> list[dict]:
        if self.points_path.exists():
            return json.load(open(self.points_path))
        return []

    def _save_points(self):
        self.points_path.parent.mkdir(parents=True, exist_ok=True)
        json.dump(self.points, open(self.points_path, 'w'), indent=2)

    # -------- ui

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QHBoxLayout(central)

        # left: matplotlib
        left = QtWidgets.QVBoxLayout()
        self.fig = Figure(figsize=(10, 8), tight_layout=True)
        self.ax  = self.fig.add_subplot(111)
        self.canvas = FigureCanvas(self.fig)
        self.toolbar = NavigationToolbar(self.canvas, self)
        self.canvas.mpl_connect('button_press_event', self._on_click)
        left.addWidget(self.toolbar)
        left.addWidget(self.canvas, 1)
        outer.addLayout(left, 3)

        # right: controls
        right = QtWidgets.QVBoxLayout()

        self.add_btn = QtWidgets.QPushButton('Add point  (off)')
        self.add_btn.setCheckable(True)
        self.add_btn.toggled.connect(self._toggle_capture)
        right.addWidget(self.add_btn)

        self.delete_btn = QtWidgets.QPushButton('Delete selected')
        self.delete_btn.clicked.connect(self._delete_selected)
        right.addWidget(self.delete_btn)

        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ['src lon', 'src lat', 'real lon', 'real lat'])
        self.table.horizontalHeader().setStretchLastSection(True)
        right.addWidget(self.table, 1)

        self.apply_btn = QtWidgets.QPushButton('Apply calibration')
        self.apply_btn.clicked.connect(self._apply_calibration)
        right.addWidget(self.apply_btn)

        self.status = QtWidgets.QLabel('Ready.')
        self.status.setWordWrap(True)
        right.addWidget(self.status)

        outer.addLayout(right, 1)

    # -------- data

    def _load_geo(self):
        self.mga = gpd.read_file(self.mga_path) if self.mga_path.exists() else None
        self.communes = gpd.read_file(self.communes_path)
        # restrict communes to those of this DOCG, if column present
        if 'docg' in self.communes.columns:
            self.communes = self.communes[
                self.communes['docg'].fillna('').str.contains(self.docg, case=False)
            ]
        try:
            self.docg_gdf = gpd.read_file(self.docg_path)
            self.docg_gdf = self.docg_gdf[
                self.docg_gdf['name'].fillna('').str.contains(self.docg, case=False)
            ]
        except Exception:
            self.docg_gdf = None

    # -------- drawing

    def _redraw(self):
        self.ax.clear()

        if self.communes is not None and len(self.communes):
            self.communes.boundary.plot(ax=self.ax, color='#888', linewidth=0.8)
            for _, row in self.communes.iterrows():
                c = row.geometry.centroid
                self.ax.text(c.x, c.y, row['name'], fontsize=8,
                             color='#555', ha='center', va='center', alpha=0.7)

        if self.mga is not None and len(self.mga):
            self.mga.plot(ax=self.ax, facecolor='#c9b88a', edgecolor='#5a3e2c',
                          linewidth=0.4, alpha=0.55)

        if self.docg_gdf is not None and len(self.docg_gdf):
            self.docg_gdf.boundary.plot(ax=self.ax, color='#7a5c38', linewidth=1.6)

        # Control points
        for i, p in enumerate(self.points):
            sx, sy = p['src']
            tx, ty = p['tgt']
            self.ax.plot(sx, sy, 'o', color='#c0392b', markersize=8,
                         markeredgecolor='white', markeredgewidth=1.2)
            self.ax.plot(tx, ty, 'x', color='#27ae60', markersize=10, mew=2)
            self.ax.plot([sx, tx], [sy, ty], color='#c0392b',
                         linewidth=0.8, alpha=0.5)
            self.ax.annotate(str(i + 1), (sx, sy), fontsize=9,
                             color='white', ha='center', va='center')

        self.ax.set_aspect('equal')
        self.ax.set_title(
            f'{self.docg} — red dot: clicked point, green ×: real target')
        self.canvas.draw_idle()

        # refresh table
        self.table.setRowCount(len(self.points))
        for i, p in enumerate(self.points):
            for j, v in enumerate([*p['src'], *p['tgt']]):
                self.table.setItem(i, j, QtWidgets.QTableWidgetItem(f'{v:.6f}'))

    # -------- interactions

    def _toggle_capture(self, on: bool):
        # While the matplotlib toolbar is in pan/zoom mode, clicks belong to it.
        if on and self.toolbar.mode != '':
            QtWidgets.QMessageBox.information(
                self, 'Pan/Zoom active',
                'Disable Pan or Zoom in the toolbar before adding points.')
            self.add_btn.setChecked(False)
            return
        self.capture_mode = on
        self.add_btn.setText(f'Add point  ({"ON" if on else "off"})')
        self.canvas.setCursor(
            QtCore.Qt.CrossCursor if on else QtCore.Qt.ArrowCursor)

    def _on_click(self, event):
        if not self.capture_mode:
            return
        if event.inaxes is not self.ax:
            return
        if self.toolbar.mode != '':
            return
        if event.xdata is None or event.ydata is None:
            return

        dlg = TargetDialog(event.xdata, event.ydata, self)
        if dlg.exec() == QtWidgets.QDialog.Accepted and dlg.target:
            self.points.append({
                'src': [float(event.xdata), float(event.ydata)],
                'tgt': dlg.target,
            })
            self._save_points()
            self._redraw()
            self.status.setText(f'Saved {len(self.points)} control point(s).')

    def _delete_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()},
                      reverse=True)
        for r in rows:
            del self.points[r]
        self._save_points()
        self._redraw()

    # -------- calibration

    def _apply_calibration(self):
        if len(self.points) < 2:
            QtWidgets.QMessageBox.warning(
                self, 'Not enough points',
                'Need at least 2 points (3+ recommended for full affine).')
            return
        if not self.mga_path.exists():
            QtWidgets.QMessageBox.warning(self, 'No MGA file',
                                          f'{self.mga_path} not found.')
            return

        src = np.array([p['src'] for p in self.points])
        dst = np.array([p['tgt'] for p in self.points])
        A, b = affine_lstsq(src, dst)

        # Residuals
        pred = src @ A.T + b
        resid = np.linalg.norm(pred - dst, axis=1)
        mean_err_m = resid.mean() * 111_320
        max_err_m  = resid.max()  * 111_320

        # Backup + overwrite the geojson file
        backup = self.mga_path.with_suffix('.geojson.bak')
        shutil.copy2(self.mga_path, backup)

        gj = json.load(open(self.mga_path))
        gj = transform_geojson(gj, A, b)
        json.dump(gj, open(self.mga_path, 'w'), indent=2)

        # Save (cumulative) affine matrix so downstream tools can re-apply it
        # when they pull in newly extracted raw polygons.
        matrix_path = ROOT / 'data-prep' / f'calibration-{self.docg.lower()}.json'
        if matrix_path.exists():
            try:
                prev = json.load(open(matrix_path))
                M_prev_A = np.array(prev['A'])
                M_prev_b = np.array(prev['b'])
            except Exception:
                M_prev_A = np.eye(2); M_prev_b = np.zeros(2)
        else:
            M_prev_A = np.eye(2); M_prev_b = np.zeros(2)
        # cumulative = (A, b) ∘ (M_prev_A, M_prev_b)
        M_new_A = A @ M_prev_A
        M_new_b = A @ M_prev_b + b
        json.dump({
            'docg': self.docg,
            'A': M_new_A.tolist(),
            'b': M_new_b.tolist(),
            'note': 'calibrated_lonlat = raw_lonlat @ A.T + b. Cumulative across runs.',
        }, open(matrix_path, 'w'), indent=2)

        # Translate the existing src coords too — so further calibration is
        # additive on the new state.
        for p in self.points:
            sx, sy = p['src']
            new = np.array([[sx, sy]]) @ A.T + b
            p['src'] = [float(new[0, 0]), float(new[0, 1])]
        self._save_points()

        self._load_geo()
        self._redraw()

        QtWidgets.QMessageBox.information(
            self, 'Calibration applied',
            f'Affine matrix:\n'
            f'  A = [[{A[0,0]:+.5f}, {A[0,1]:+.5f}], [{A[1,0]:+.5f}, {A[1,1]:+.5f}]]\n'
            f'  b = [{b[0]:+.5f}, {b[1]:+.5f}]\n\n'
            f'Mean residual: {mean_err_m:.1f} m\n'
            f'Max  residual: {max_err_m:.1f} m\n\n'
            f'Backup saved to {backup.name}.\n'
            f'Refresh the browser to see the result.')


# ---------------------------------------------------------------- entry

def main():
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--docg', choices=['Barolo', 'Barbaresco'], default='Barolo')
    args = p.parse_args()

    app = QtWidgets.QApplication(sys.argv)
    win = Calibrator(args.docg)
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
