// -------------------------------------------------------
// Layer definitions and MapLibre helpers
// -------------------------------------------------------

// Muted, commune-keyed palette for Barolo MGA fills.
// Property expected in data: feature.properties.comune (or .commune)
const BAROLO_COMMUNE_COLORS = {
  'La Morra':             '#b8c9a5',
  'Barolo':               '#c9b88a',
  'Castiglione Falletto': '#c9a86a',
  'Serralunga d\'Alba':   '#bfb095',
  'Monforte d\'Alba':     '#a8bb95',
  'Novello':              '#98b282',
  'Verduno':              '#cccaa8',
  'Grinzane Cavour':      '#d5ccb5',
  'Diano d\'Alba':        '#c5d3ae',
  'Roddi':                '#dcd5c0',
  'Cherasco':             '#e5ddd0',
};

const BARBARESCO_COMMUNE_COLORS = {
  'Barbaresco': '#b5c5cc',
  'Neive':      '#a8bac8',
  'Treiso':     '#9aadc2',
  'Alba':       '#ccd5dd',
};

// Build a MapLibre fill-color expression. If the feature carries an
// `svg_colour` property (sampled from the original PDF map during extraction),
// use it directly so each MGA gets its authentic Consorzio-map hue. Fall back
// to a commune-keyed palette for features that have no svg_colour.
function mgaFillExpression(palette) {
  const pairs = Object.entries(palette).flatMap(([k, v]) => [k, v]);
  return [
    'case',
    ['all', ['has', 'svg_colour'], ['!=', ['get', 'svg_colour'], '']],
    ['get', 'svg_colour'],
    ['match',
      ['coalesce', ['get', 'comune'], ['get', 'commune'], ''],
      ...pairs,
      '#cccccc',
    ],
  ];
}

// -------------------------------------------------------
// Layer definitions
// -------------------------------------------------------
// Each entry describes one toggleable data layer.
// - id:              unique string, also used as MapLibre source id
// - label:           display name in the sidebar
// - dataFile:        path relative to project root (served by http.server)
// - type:            'fill' | 'line' | 'fill+line' | 'circle'
// - defaultVisible:  whether the layer starts visible
// - clickable:       whether clicking opens the info panel
// - swatchColor:     representative color for the sidebar swatch
// -------------------------------------------------------
export const LAYER_DEFS = [
  {
    id: 'docg',
    label: 'DOCG / DOC Zones',
    dataFile: 'data/docg.geojson',
    type: 'fill+line',
    fillColor: '#c4a882',
    fillOpacity: 0.15,
    lineColor: '#7a5c38',
    lineWidth: 2.5,
    defaultVisible: true,
    clickable: true,
    swatchColor: '#c4a882',
  },
  {
    id: 'mga-barolo',
    label: 'Barolo MGAs (crus)',
    dataFile: 'data/mga-barolo.geojson',
    type: 'fill+line',
    fillExpression: mgaFillExpression(BAROLO_COMMUNE_COLORS),
    fillOpacity: 0.55,
    lineColor: '#5a3e2c',
    lineWidth: 0.6,
    defaultVisible: false,
    clickable: true,
    swatchColor: '#c9b88a',
  },
  {
    id: 'mga-barbaresco',
    label: 'Barbaresco MGAs (crus)',
    dataFile: 'data/mga-barbaresco.geojson',
    type: 'fill+line',
    fillExpression: mgaFillExpression(BARBARESCO_COMMUNE_COLORS),
    fillOpacity: 0.55,
    lineColor: '#2c3e5a',
    lineWidth: 0.6,
    defaultVisible: false,
    clickable: true,
    swatchColor: '#b5c5cc',
  },
  {
    id: 'producers',
    label: 'Producers',
    // NOTE: producers load from the Supabase `producers` table (see main.js
    // loadAllLayers), not this file — so user-added wineries appear too.
    // dataFile is kept only for reference and is ignored at load time.
    dataFile: 'data/producers.geojson',
    type: 'circle',
    circleColor: '#3d2b1a',
    // Radius scales with zoom: small at overview, larger when zoomed in
    circleRadius: ['interpolate', ['linear'], ['zoom'], 9, 3, 14, 7],
    circleStrokeColor: '#faf7f2',
    circleStrokeWidth: 1.5,
    defaultVisible: false,
    clickable: true,
    swatchColor: '#3d2b1a',
  },
];

// -------------------------------------------------------
// Add a layer (source + MapLibre layers) to the map
// -------------------------------------------------------
export function addLayerToMap(map, def, geojson) {
  map.addSource(def.id, { type: 'geojson', data: geojson });

  // Fill layer
  if (def.type === 'fill' || def.type === 'fill+line') {
    map.addLayer({
      id: `${def.id}-fill`,
      type: 'fill',
      source: def.id,
      layout: { visibility: def.defaultVisible ? 'visible' : 'none' },
      paint: {
        'fill-color': def.fillExpression ?? def.fillColor ?? '#cccccc',
        'fill-opacity': def.fillOpacity ?? 0.3,
      },
    });
  }

  // Line / outline layer
  if (def.type === 'line' || def.type === 'fill+line') {
    const linePaint = {
      'line-color': def.lineColor ?? '#555555',
      'line-width': def.lineWidth ?? 1,
    };
    if (def.lineDasharray) {
      linePaint['line-dasharray'] = def.lineDasharray;
    }
    map.addLayer({
      id: `${def.id}-line`,
      type: 'line',
      source: def.id,
      layout: { visibility: def.defaultVisible ? 'visible' : 'none' },
      paint: linePaint,
    });
  }

  // Circle (point) layer
  if (def.type === 'circle') {
    map.addLayer({
      id: `${def.id}-circle`,
      type: 'circle',
      source: def.id,
      layout: { visibility: def.defaultVisible ? 'visible' : 'none' },
      paint: {
        'circle-color': def.circleColor ?? '#555555',
        'circle-radius': def.circleRadius ?? 5,
        'circle-stroke-color': def.circleStrokeColor ?? '#ffffff',
        'circle-stroke-width': def.circleStrokeWidth ?? 1,
      },
    });
  }
}

// -------------------------------------------------------
// Toggle layer visibility
// -------------------------------------------------------
export function setLayerVisibility(map, def, visible) {
  for (const suffix of _suffixes(def.type)) {
    const id = `${def.id}-${suffix}`;
    if (map.getLayer(id)) {
      map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
    }
  }
}

// Returns the MapLibre layer id to attach click events to
export function clickLayerId(def) {
  switch (def.type) {
    case 'circle':    return `${def.id}-circle`;
    case 'line':      return null;  // lines are not clickable
    default:          return `${def.id}-fill`;
  }
}

function _suffixes(type) {
  switch (type) {
    case 'fill':      return ['fill'];
    case 'line':      return ['line'];
    case 'fill+line': return ['fill', 'line'];
    case 'circle':    return ['circle'];
    default:          return [];
  }
}
