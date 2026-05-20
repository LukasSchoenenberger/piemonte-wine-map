// -------------------------------------------------------
// Sidebar UI: layer toggles + feature info panel
// -------------------------------------------------------

const layerSection  = document.getElementById('layer-section');
const layerToggles  = document.getElementById('layer-toggles');
const infoPanel     = document.getElementById('info-panel');
const infoTitle     = document.getElementById('info-title');
const infoContent   = document.getElementById('info-content');
const infoClose     = document.getElementById('info-close');

let _map, _setVisibility;

export function initUI(map, layerResults, setVisibility) {
  _map = map;
  _setVisibility = setVisibility;
  buildToggles(layerResults);
}

// -------------------------------------------------------
// Build the layer toggle list
// -------------------------------------------------------
function buildToggles(layerResults) {
  layerToggles.innerHTML = '';

  for (const { def, available } of layerResults) {
    const label = document.createElement('label');
    label.className = 'layer-toggle' + (available ? '' : ' unavailable');

    const checkbox = document.createElement('input');
    checkbox.type    = 'checkbox';
    checkbox.checked = available && def.defaultVisible;
    checkbox.disabled = !available;
    if (available) {
      checkbox.addEventListener('change', () => {
        _setVisibility(_map, def, checkbox.checked);
      });
    }

    const swatch = document.createElement('span');
    swatch.className       = 'swatch';
    swatch.style.background = def.swatchColor ?? '#cccccc';

    const labelSpan = document.createElement('span');
    labelSpan.className   = 'layer-label';
    labelSpan.textContent = def.label;

    label.appendChild(checkbox);
    label.appendChild(swatch);
    label.appendChild(labelSpan);

    if (!available) {
      const badge = document.createElement('span');
      badge.className   = 'badge-nodata';
      badge.textContent = 'no data';
      label.appendChild(badge);
    }

    layerToggles.appendChild(label);
  }
}

// -------------------------------------------------------
// Feature info panel
// -------------------------------------------------------
export function showInfo(def, props) {
  // Best-effort heading: prefer a 'name' or 'MGA' property, fall back to layer label
  const heading = props.name ?? props.MGA ?? props.mga ?? props.denominazione ?? def.label;
  infoTitle.textContent = heading;

  // Render all non-internal properties as a table. URLs render as links;
  // everything else is escaped to prevent HTML injection from CSV/geojson
  // data.
  const rows = Object.entries(props)
    .filter(([k]) => !k.startsWith('_'))
    .map(([k, v]) => {
      if (v === null || v === undefined || v === '') {
        return `<tr><th>${formatKey(k)}</th><td>—</td></tr>`;
      }
      const s = String(v);
      const isUrl = /^https?:\/\//i.test(s);
      const cell = isUrl
        ? `<a href="${escapeAttr(s)}" target="_blank" rel="noopener noreferrer">${escapeText(displayUrl(s))}</a>`
        : escapeText(s);
      return `<tr><th>${formatKey(k)}</th><td>${cell}</td></tr>`;
    })
    .join('');

  infoContent.innerHTML = rows
    ? `<table class="info-table">${rows}</table>`
    : `<p class="info-empty">No metadata available.</p>`;

  layerSection.hidden = true;
  infoPanel.hidden    = false;
}

function hideInfo() {
  infoPanel.hidden    = true;
  layerSection.hidden = false;
}

infoClose.addEventListener('click', hideInfo);

// -------------------------------------------------------
// Helpers
// -------------------------------------------------------
function formatKey(k) {
  return k
    .replace(/_/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function escapeText(s) {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function escapeAttr(s) {
  return escapeText(s).replace(/"/g, '&quot;');
}

function displayUrl(s) {
  // Drop the scheme and any trailing slash for a cleaner-looking link label.
  return s.replace(/^https?:\/\//i, '').replace(/\/$/, '');
}
