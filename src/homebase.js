// -------------------------------------------------------
// Homebase: a private, per-user playground.
//   - Drag glass/bottle tokens onto your avatar to earn colour-tracked stars
//     (glass = 1 pt, bottle = 5 pts; 5 small -> 1 medium -> 1 large, max 5 large).
//   - Drag a bottle onto the fridge to add a wine (from the shared glossary,
//     or a new one) with a price; cellar value earns stars ($50 = 1 small).
//   - Drag a glass onto the fridge for some gentle judgement.
// All icons are inline SVG (no emoji, per project style rules).
// -------------------------------------------------------
import { openModal } from './wineui.js';
import {
  getHomebase, addAvatarPoints,
  listFridge, addFridgeWine, deleteFridgeWine,
  listWines, listProducers, addWine, getLeaderboard,
} from './db.js';

const modalContent = document.getElementById('modal-content');

const WINE_COLORS  = { white: '#d8c87a', rose: '#d99fa6', red: '#7e2b35' };
const COLOR_LABELS = { white: 'White', rose: 'Rosé', red: 'Red' };
const COLORS = ['white', 'rose', 'red'];

export function initHomebase() {
  document.getElementById('btn-homebase').addEventListener('click', openHomebase);
  document.getElementById('btn-podium').addEventListener('click', openPodium);
}

// -------------------------------------------------------
// Podium: 4 rankings (white / rosé / red / fridge), all users
// -------------------------------------------------------
async function openPodium() {
  let rows;
  try {
    rows = await getLeaderboard();
  } catch (e) {
    openModal(`<h2>Podium</h2><p class="modal-error">Could not load: ${esc(e.message)}</p>`);
    return;
  }

  const cols = [
    { key: 'white',  label: 'White',  color: WINE_COLORS.white },
    { key: 'rose',   label: 'Rosé',   color: WINE_COLORS.rose },
    { key: 'red',    label: 'Red',    color: WINE_COLORS.red },
    { key: 'fridge', label: 'Fridge', color: '#9a8d7a' },
  ];

  const grid = cols.map((c) => {
    const ranked = [...rows].sort(
      (a, b) => (b[c.key] - a[c.key]) || a.display_name.localeCompare(b.display_name)
    );
    const items = ranked.length
      ? ranked.map((r, i) => `
          <li>
            <span class="podium-rank">${i + 1}</span>
            <span class="podium-name">${esc(r.display_name)}</span>
            <span class="podium-count">${r[c.key]}${starSvg(11)}</span>
          </li>`).join('')
      : '<li class="modal-empty">No users.</li>';
    return `<div class="podium-col">
      <h3 class="podium-head" style="border-color:${c.color}">${c.label}</h3>
      <ol class="podium-list">${items}</ol>
    </div>`;
  }).join('');

  openModal(`<h2>Podium</h2><div class="podium-grid">${grid}</div>`);
}

async function openHomebase() {
  try {
    const [hb, fridge] = await Promise.all([getHomebase(), listFridge()]);
    renderHomebase(hb, fridge);
  } catch (e) {
    openModal(`<h2>Homebase</h2><p class="modal-error">Could not load: ${esc(e.message)}</p>`);
  }
}

function renderHomebase(hb, fridge) {
  const total = fridge.reduce((s, f) => s + Number(f.price || 0), 0);
  const fridgePts = Math.floor(total / 50);

  openModal(`
    <h2>Homebase</h2>
    <div class="hb-top">
      <div class="hb-target" id="hb-avatar">
        <div class="hb-icon">${avatarSvg()}</div>
        <div class="hb-stars">
          ${COLORS.map((c) => `
            <div class="hb-star-row">
              <span class="hb-star-label" style="color:${WINE_COLORS[c]}">${COLOR_LABELS[c]}</span>
              ${starsHtml(hb['points_' + c] ?? 0)}
            </div>`).join('')}
        </div>
      </div>
      <div class="hb-target" id="hb-fridge">
        <div class="hb-icon">${fridgeSvg()}</div>
        <div class="hb-fridge-info">
          ${starsHtml(fridgePts)}
          <button class="link-btn" id="hb-view-cellar">View cellar ($${total.toFixed(0)})</button>
        </div>
      </div>
    </div>

    <div class="hb-tokens">
      <div class="hb-token-row">
        <span class="hb-token-label">Bottles</span>
        ${COLORS.map((c) => tokenHtml('bottle', c)).join('')}
      </div>
      <div class="hb-token-row">
        <span class="hb-token-label">Glasses</span>
        ${COLORS.map((c) => tokenHtml('glass', c)).join('')}
      </div>
    </div>
    <p class="hb-hint">Drag a glass or bottle onto your avatar or the fridge.</p>
    <div id="hb-subpanel"></div>
  `);

  wireHomebase(fridge);
}

function wireHomebase(fridge) {
  modalContent.querySelectorAll('.hb-token').forEach((t) => {
    t.addEventListener('dragstart', (e) => {
      e.dataTransfer.setData('text/plain',
        JSON.stringify({ kind: t.dataset.kind, color: t.dataset.color }));
    });
  });

  const avatar = document.getElementById('hb-avatar');
  const fridgeEl = document.getElementById('hb-fridge');

  for (const zone of [avatar, fridgeEl]) {
    zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('hb-drop-hover'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('hb-drop-hover'));
  }

  avatar.addEventListener('drop', async (e) => {
    e.preventDefault();
    avatar.classList.remove('hb-drop-hover');
    const { kind, color } = readToken(e);
    const delta = kind === 'bottle' ? 5 : 1;
    try {
      const newVal = await addAvatarPoints(color, delta);
      const [hb, fr] = await Promise.all([getHomebase(), listFridge()]);
      renderHomebase(hb, fr);
      if (newVal >= 125) showSubpanel(`<p class="hb-note">${COLOR_LABELS[color]} maxed out — 5 large stars!</p>`);
    } catch (err) {
      alert('Could not save: ' + (err.message || err));
    }
  });

  fridgeEl.addEventListener('drop', (e) => {
    e.preventDefault();
    fridgeEl.classList.remove('hb-drop-hover');
    const { kind } = readToken(e);
    if (kind === 'glass') {
      showSubpanel('<p class="hb-poor">poor soul</p>');
    } else {
      showFridgeForm();
    }
  });

  document.getElementById('hb-view-cellar').addEventListener('click', () => showCellar(fridge));
}

function readToken(e) {
  try { return JSON.parse(e.dataTransfer.getData('text/plain')); }
  catch { return {}; }
}

function showSubpanel(html) {
  document.getElementById('hb-subpanel').innerHTML = html;
}

// -------------------------------------------------------
// Fridge: add-to-cellar form (pick existing wine or add a new one)
// -------------------------------------------------------
async function showFridgeForm() {
  let wines, producers;
  try {
    [wines, producers] = await Promise.all([listWines(), listProducers()]);
  } catch (e) {
    return showSubpanel(`<p class="modal-error">${esc(e.message)}</p>`);
  }

  const wineOpts = wines
    .map((w) => `<option value="${w.id}">${esc(w.producers?.name ?? '?')} — ${esc(w.name)}${w.year ? ' ' + w.year : ''}</option>`)
    .join('');
  const prodOpts = producers.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join('');

  showSubpanel(`
    <div class="wine-form hb-form">
      <h3 class="panel-subhead">Add to your cellar</h3>
      <label>Wine
        <select id="fr-wine">${wineOpts}<option value="__new__">+ Add new wine…</option></select>
      </label>
      <div id="fr-new" hidden>
        <label>Producer <select id="fr-prod">${prodOpts}</select></label>
        <label>New wine name <input id="fr-name" type="text"></label>
        <label>Year (optional) <input id="fr-year" type="number" min="1900" max="2099"></label>
      </div>
      <label>Price (USD) <input id="fr-price" type="number" min="0" step="any" required></label>
      <button type="button" id="fr-save">Save to cellar</button>
      <p class="modal-error" id="fr-error" hidden></p>
    </div>
  `);

  const sel = document.getElementById('fr-wine');
  sel.addEventListener('change', () => {
    document.getElementById('fr-new').hidden = sel.value !== '__new__';
  });

  document.getElementById('fr-save').addEventListener('click', async () => {
    const err = document.getElementById('fr-error');
    err.hidden = true;
    const priceRaw = document.getElementById('fr-price').value.trim();
    if (!priceRaw) return showErr(err, 'Price is required.');
    const price = parseFloat(priceRaw);
    if (!(price >= 0)) return showErr(err, 'Price must be a positive number.');

    try {
      let wineId = sel.value;
      if (wineId === '__new__') {
        const name = document.getElementById('fr-name').value.trim();
        if (!name) return showErr(err, 'New wine name is required.');
        const yearRaw = document.getElementById('fr-year').value.trim();
        const newWine = await addWine({
          name,
          year: yearRaw ? parseInt(yearRaw, 10) : null,
          producerId: document.getElementById('fr-prod').value,
        });
        wineId = newWine.id;
      }
      await addFridgeWine(wineId, price);
      const [hb, fridge] = await Promise.all([getHomebase(), listFridge()]);
      renderHomebase(hb, fridge);
      showCellar(fridge);
    } catch (e) {
      showErr(err, e.message || 'Could not save.');
    }
  });
}

function showCellar(fridge) {
  if (!fridge.length) {
    return showSubpanel('<p class="modal-empty">Your cellar is empty. Drag a bottle onto the fridge to add a wine.</p>');
  }
  const rows = fridge.map((f) => `
    <tr>
      <td>${esc(f.wines?.producers?.name ?? '')}</td>
      <td>${esc(f.wines?.name ?? '')}</td>
      <td>${f.wines?.year ?? '—'}</td>
      <td class="num">$${Number(f.price).toFixed(0)}</td>
      <td class="del-cell"><button class="wine-del" data-id="${f.id}" title="Remove">&times;</button></td>
    </tr>`).join('');

  showSubpanel(`
    <h3 class="panel-subhead">Your cellar</h3>
    <table class="glossary-table">
      <thead><tr><th>Producer</th><th>Wine</th><th>Year</th><th class="num">Price</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `);

  document.querySelectorAll('#hb-subpanel .wine-del').forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (!confirm('Remove this wine from your cellar?')) return;
      try {
        await deleteFridgeWine(btn.dataset.id);
        const [hb, fr] = await Promise.all([getHomebase(), listFridge()]);
        renderHomebase(hb, fr);
        showCellar(fr);
      } catch (e) {
        alert('Could not remove: ' + (e.message || e));
      }
    });
  });
}

// -------------------------------------------------------
// Stars (base-5 fusion: 5 small -> 1 medium -> 1 large, max 5 large)
// -------------------------------------------------------
function starBreakdown(points) {
  const capped = Math.min(Math.max(points, 0), 125);
  const large = Math.floor(capped / 25);
  const rem = capped % 25;
  return { large, medium: Math.floor(rem / 5), small: rem % 5 };
}

function starsHtml(points) {
  const { large, medium, small } = starBreakdown(points);
  let h = '';
  for (let i = 0; i < large; i++)  h += starSvg(26);
  for (let i = 0; i < medium; i++) h += starSvg(18);
  for (let i = 0; i < small; i++)  h += starSvg(12);
  if (!h) h = '<span class="hb-nostar">no stars yet</span>';
  return `<span class="hb-star-group">${h}</span>`;
}

// -------------------------------------------------------
// Inline SVG icons
// -------------------------------------------------------
function avatarSvg() {
  return `<svg viewBox="0 0 64 64" width="64" height="64" aria-label="avatar">
    <circle cx="32" cy="23" r="13" fill="#9a8d7a"/>
    <path d="M10 58 a22 20 0 0 1 44 0 z" fill="#9a8d7a"/>
  </svg>`;
}
function fridgeSvg() {
  return `<svg viewBox="0 0 64 64" width="64" height="64" aria-label="fridge">
    <rect x="18" y="5" width="28" height="54" rx="3" fill="#cfc6b8" stroke="#9a8d7a" stroke-width="2"/>
    <line x1="18" y1="25" x2="46" y2="25" stroke="#9a8d7a" stroke-width="2"/>
    <rect x="40" y="11" width="3" height="9" rx="1.5" fill="#9a8d7a"/>
    <rect x="40" y="30" width="3" height="9" rx="1.5" fill="#9a8d7a"/>
  </svg>`;
}
function bottleSvg(color) {
  return `<svg viewBox="0 0 24 48" width="22" height="44" aria-hidden="true">
    <rect x="10" y="3" width="4" height="9" fill="#5a4a3a"/>
    <path d="M8 12 q-3 4 -3 10 v18 q0 4 4 4 h6 q4 0 4 -4 v-18 q0 -6 -3 -10 z"
          fill="${color}" stroke="#5a4a3a" stroke-width="1.2"/>
  </svg>`;
}
function glassSvg(color) {
  return `<svg viewBox="0 0 24 48" width="22" height="44" aria-hidden="true">
    <path d="M7 5 h10 v5 q0 6 -5 8 q-5 -2 -5 -8 z" fill="${color}" stroke="#7a7060" stroke-width="0.8"/>
    <path d="M7 5 h10 v3 h-10 z" fill="#cfc6b8" opacity="0.5"/>
    <line x1="12" y1="18" x2="12" y2="40" stroke="#8a8070" stroke-width="2"/>
    <line x1="6" y1="42" x2="18" y2="42" stroke="#8a8070" stroke-width="2"/>
  </svg>`;
}
function starSvg(size) {
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" class="hb-star">
    <path d="M12 2 l2.9 6.3 6.9 .6 -5.2 4.6 1.6 6.8 -6.2 -3.6 -6.2 3.6 1.6 -6.8 -5.2 -4.6 6.9 -.6 z"
          fill="#c9a44a" stroke="#a8842f" stroke-width="0.6"/>
  </svg>`;
}

// -------------------------------------------------------
// Helpers
// -------------------------------------------------------
function tokenHtml(kind, color) {
  const svg = kind === 'bottle' ? bottleSvg(WINE_COLORS[color]) : glassSvg(WINE_COLORS[color]);
  return `<div class="hb-token" draggable="true" data-kind="${kind}" data-color="${color}"
            title="${COLOR_LABELS[color]} ${kind}">${svg}</div>`;
}
function showErr(el, msg) { el.textContent = msg; el.hidden = false; }
function esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
