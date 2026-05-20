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
  initWineUI({ onProducersChanged: reloadProducers, pickCoordinate: pickCoordinateOnMap });
  initProducerSearch();

  // Reveal the authed UI
  loginOverlay.hidden = true;
  document.getElementById('wine-actions').hidden = false;
  document.getElementById('producer-actions').hidden = false;

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
// in the glossary / dropdowns. Caches the rows for the producer search.
let _producers = [];
async function producersGeojson() {
  _producers = await listProducers();
  const features = _producers
    .filter((p) => p.lat != null && p.lon != null)
    .map((p) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [p.lon, p.lat] },
      properties: {
        id: p.id, name: p.name, commune: p.commune, docg: p.docg,
        website: p.website, created_by: p.created_by,
      },
    }));
  return { type: 'FeatureCollection', features };
}

// Refresh the producers source after add/delete, plus the search list.
async function reloadProducers() {
  const src = map.getSource('producers');
  if (src) src.setData(await producersGeojson());
  refreshProducerSearchList();
}

// -------------------------------------------------------
// Producer search: type a name -> fly to + highlight on the map
// -------------------------------------------------------
let _highlightMarker = null;

function initProducerSearch() {
  refreshProducerSearchList();
  const input = document.getElementById('producer-search');
  input.addEventListener('change', () => {
    const term = input.value.trim().toLowerCase();
    if (!term) return;
    const match = _producers.find(
      (p) => p.lat != null && p.lon != null && p.name.toLowerCase() === term
    ) || _producers.find(
      (p) => p.lat != null && p.lon != null && p.name.toLowerCase().includes(term)
    );
    if (match) highlightProducer(match);
  });
}

function refreshProducerSearchList() {
  const list = document.getElementById('producer-search-list');
  if (!list) return;
  list.innerHTML = _producers
    .filter((p) => p.lat != null && p.lon != null)
    .map((p) => `<option value="${p.name.replace(/"/g, '&quot;')}"></option>`)
    .join('');
}

function highlightProducer(p) {
  if (_highlightMarker) _highlightMarker.remove();
  const el = document.createElement('div');
  el.className = 'producer-highlight-marker';
  _highlightMarker = new maplibregl.Marker({ element: el })
    .setLngLat([p.lon, p.lat])
    .addTo(map);
  map.flyTo({ center: [p.lon, p.lat], zoom: 13.5, speed: 1.2 });
}

// -------------------------------------------------------
// Coordinate picker: hide UI chrome, let the user click the map once
// -------------------------------------------------------
function pickCoordinateOnMap() {
  return new Promise((resolve) => {
    const banner = document.getElementById('map-banner');
    banner.textContent = 'Click the producer’s location on the map  (Esc to cancel)';
    banner.hidden = false;
    map.getCanvas().style.cursor = 'crosshair';

    function cleanup() {
      map.off('click', onClick);
      window.removeEventListener('keydown', onKey);
      banner.hidden = true;
      map.getCanvas().style.cursor = '';
    }
    function onClick(e) {
      cleanup();
      resolve({ lat: e.lngLat.lat, lon: e.lngLat.lng });
    }
    function onKey(e) {
      if (e.key === 'Escape') { cleanup(); resolve({ lat: null, lon: null }); }
    }
    map.on('click', onClick);
    window.addEventListener('keydown', onKey);
  });
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
