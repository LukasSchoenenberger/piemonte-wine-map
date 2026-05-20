// Propagate the cache-buster from index.html so local modules reload on every
// page load too. Without this, edits stick in the browser ES-module cache.
const _v = new URL(import.meta.url).searchParams.get('v') ?? Date.now();
const { LAYER_DEFS, addLayerToMap, setLayerVisibility, clickLayerId }
  = await import(`./layers.js?v=${_v}`);
const { initUI, showInfo } = await import(`./ui.js?v=${_v}`);
const { getSession, signIn, signOut, getMyDisplayName, listProducers }
  = await import(`./db.js?v=${_v}`);
const { initWineUI, showProducerPanel } = await import(`./wineui.js?v=${_v}`);

// Map center: Langhe hills, midpoint between Barolo and Barbaresco
const CENTER  = [7.980, 44.630];
const ZOOM    = 11;
const MIN_ZOOM = 8;
const MAX_ZOOM = 18;

const STYLE_URL = 'https://tiles.openfreemap.org/styles/liberty';

const map = new maplibregl.Map({
  container: 'map',
  style: STYLE_URL,
  center: CENTER,
  zoom: ZOOM,
  minZoom: MIN_ZOOM,
  maxZoom: MAX_ZOOM,
  attributionControl: { compact: true },
});
map.addControl(new maplibregl.NavigationControl(), 'top-right');

// -------------------------------------------------------
// Auth gate: the app only initialises once map is loaded AND a session exists
// -------------------------------------------------------
const loginOverlay = document.getElementById('login-overlay');
const loginForm    = document.getElementById('login-form');
const loginError   = document.getElementById('login-error');

let _mapLoaded = false;
let _authed = false;
let _initialized = false;

map.on('load', () => { _mapLoaded = true; tryInit(); });

// Overlay is visible by default (see CSS) so there's no unprotected flash.
// If a session already exists, tryInit() will hide it.
(async () => {
  const session = await getSession();
  if (session) { _authed = true; tryInit(); }
})();

loginForm.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  loginError.hidden = true;
  const submit = document.getElementById('login-submit');
  submit.disabled = true;
  try {
    await signIn(
      document.getElementById('login-email').value.trim(),
      document.getElementById('login-password').value,
    );
    _authed = true;
    tryInit();
  } catch (err) {
    loginError.textContent = err.message || 'Sign in failed.';
    loginError.hidden = false;
  } finally {
    submit.disabled = false;
  }
});

window.addEventListener('auth-change', (e) => {
  if (e.detail.event === 'SIGNED_OUT') location.reload();
});

async function tryInit() {
  if (_initialized || !_mapLoaded || !_authed) return;
  _initialized = true;

  const results = await loadAllLayers();
  initUI(map, results, setLayerVisibility);
  attachClickHandlers(results);
  initWineUI({ onProducersChanged: reloadProducers });

  // Reveal the authed UI
  loginOverlay.hidden = true;
  document.getElementById('wine-actions').hidden = false;

  const name = await getMyDisplayName();
  if (name) {
    document.getElementById('account-name').textContent = name;
    document.getElementById('account-row').hidden = false;
  }
  document.getElementById('btn-signout').addEventListener('click', () => signOut());
}

// -------------------------------------------------------
// Load layers. Static files come from data/*.geojson; producers come from
// Supabase so user-added wineries show up too.
// -------------------------------------------------------
async function loadAllLayers() {
  return Promise.all(
    LAYER_DEFS.map(async (def) => {
      try {
        if (def.id === 'producers') {
          const geojson = await producersGeojson();
          addLayerToMap(map, def, geojson);
          return { def, available: true };
        }
        const url = `${def.dataFile}?t=${Date.now()}`;
        const res = await fetch(url, { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const geojson = await res.json();
        addLayerToMap(map, def, geojson);
        return { def, available: true };
      } catch {
        return { def, available: false };
      }
    })
  );
}

// Build a point FeatureCollection from the Supabase producers table.
// Only producers with coordinates appear on the map; the rest still exist
// in the glossary / dropdowns.
async function producersGeojson() {
  const rows = await listProducers();
  const features = rows
    .filter((p) => p.lat != null && p.lon != null)
    .map((p) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [p.lon, p.lat] },
      properties: {
        id: p.id, name: p.name, commune: p.commune, docg: p.docg, website: p.website,
      },
    }));
  return { type: 'FeatureCollection', features };
}

// Refresh the producers source after a new producer is added.
async function reloadProducers() {
  const src = map.getSource('producers');
  if (src) src.setData(await producersGeojson());
}

// -------------------------------------------------------
// Click handlers
// -------------------------------------------------------
function attachClickHandlers(results) {
  for (const { def, available } of results) {
    if (!available || !def.clickable) continue;
    const layerId = clickLayerId(def);
    if (!layerId) continue;

    map.on('click', layerId, (e) => {
      const feature = e.features[0];
      if (!feature) return;
      if (def.id === 'producers') {
        showProducerPanel(feature.properties);
      } else {
        showInfo(def, feature.properties);
      }
    });
    map.on('mouseenter', layerId, () => { map.getCanvas().style.cursor = 'pointer'; });
    map.on('mouseleave', layerId, () => { map.getCanvas().style.cursor = ''; });
  }
}
