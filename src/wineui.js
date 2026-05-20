// -------------------------------------------------------
// Wine features UI: add wine, rate wine, glossary, and the
// producer -> wines -> ratings views inside the info panel.
// -------------------------------------------------------
import {
  listProducers, addProducer, deleteProducer,
  listWines, listWinesByProducer, addWine, deleteWine,
  listRatingsForWine, myRatingForWine, upsertRating,
  getMyUserId,
} from './db.js';

const layerSection = document.getElementById('layer-section');
const infoPanel    = document.getElementById('info-panel');
const infoTitle    = document.getElementById('info-title');
const infoContent  = document.getElementById('info-content');

const modalOverlay = document.getElementById('modal-overlay');
const modalContent = document.getElementById('modal-content');

let _onProducersChanged = null;
let _pickCoordinate = null;   // () => Promise<{lat, lon}>, provided by main.js

export function initWineUI({ onProducersChanged, pickCoordinate } = {}) {
  _onProducersChanged = onProducersChanged;
  _pickCoordinate = pickCoordinate;
  document.getElementById('btn-add-wine').addEventListener('click', showAddWineModal);
  document.getElementById('btn-rate-wine').addEventListener('click', showRateWineModal);
  document.getElementById('btn-glossary').addEventListener('click', showGlossaryModal);
  document.getElementById('btn-add-producer').addEventListener('click', () => showAddProducerModal());
  document.getElementById('modal-close').addEventListener('click', closeModal);
  modalOverlay.addEventListener('click', (e) => {
    if (e.target === modalOverlay) closeModal();
  });
}

// -------------------------------------------------------
// Modal helpers
// -------------------------------------------------------
export function openModal(html) {
  modalContent.innerHTML = html;
  modalOverlay.hidden = false;
}
export function closeModal() {
  modalOverlay.hidden = true;
  modalContent.innerHTML = '';
}

// -------------------------------------------------------
// Add wine
// -------------------------------------------------------
async function showAddWineModal() {
  let producers = [];
  try {
    producers = await listProducers();
  } catch (e) {
    openModal(`<h2>Add wine</h2><p class="modal-error">Could not load producers: ${esc(e.message)}</p>`);
    return;
  }
  const options = producers
    .map((p) => `<option value="${p.id}">${esc(p.name)}</option>`)
    .join('');

  openModal(`
    <h2>Add wine</h2>
    <form id="add-wine-form" class="wine-form">
      <label>Producer
        <select id="aw-producer">
          ${options}
          <option value="__new__">+ Add new producer…</option>
        </select>
      </label>
      <div id="aw-new" hidden>
        <label>New producer name <input id="aw-np-name" type="text"></label>
        <label>Commune <input id="aw-np-commune" type="text"></label>
        <div class="coord-row">
          <label>Latitude (optional) <input id="aw-np-lat" type="number" step="any" placeholder="44.61"></label>
          <label>Longitude (optional) <input id="aw-np-lon" type="number" step="any" placeholder="7.94"></label>
        </div>
      </div>
      <label>Wine name <input id="aw-name" type="text" required placeholder="e.g. Barolo Cannubi"></label>
      <label>Year (optional) <input id="aw-year" type="number" min="1900" max="2099" placeholder="2019"></label>
      <button type="submit">Save wine</button>
      <p class="modal-error" id="aw-error" hidden></p>
    </form>
  `);

  const sel    = document.getElementById('aw-producer');
  const newBox = document.getElementById('aw-new');
  sel.addEventListener('change', () => {
    newBox.hidden = sel.value !== '__new__';
  });

  document.getElementById('add-wine-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const err = document.getElementById('aw-error');
    err.hidden = true;
    const name = document.getElementById('aw-name').value.trim();
    const yearRaw = document.getElementById('aw-year').value.trim();
    const year = yearRaw ? parseInt(yearRaw, 10) : null;
    if (!name) { return showErr(err, 'Wine name is required.'); }

    try {
      let producerId = sel.value;
      if (producerId === '__new__') {
        const npName = document.getElementById('aw-np-name').value.trim();
        if (!npName) { return showErr(err, 'New producer name is required.'); }
        const latRaw = document.getElementById('aw-np-lat').value.trim();
        const lonRaw = document.getElementById('aw-np-lon').value.trim();
        const newProd = await addProducer({
          name: npName,
          commune: document.getElementById('aw-np-commune').value.trim(),
          docg: null,
          lat: latRaw ? parseFloat(latRaw) : null,
          lon: lonRaw ? parseFloat(lonRaw) : null,
          website: null,
        });
        producerId = newProd.id;
        if (_onProducersChanged) await _onProducersChanged();
      }
      await addWine({ name, year, producerId });
      closeModal();
    } catch (e) {
      showErr(err, e.message || 'Could not save wine.');
    }
  });
}

// -------------------------------------------------------
// Rate wine (sidebar entry point — pick a wine, then score it)
// -------------------------------------------------------
async function showRateWineModal() {
  let wines = [];
  try {
    wines = await listWines();
  } catch (e) {
    openModal(`<h2>Rate wine</h2><p class="modal-error">Could not load wines: ${esc(e.message)}</p>`);
    return;
  }
  if (!wines.length) {
    openModal(`<h2>Rate wine</h2><p class="modal-empty">No wines yet. Add one first.</p>`);
    return;
  }
  const options = wines
    .map((w) => `<option value="${w.id}">${esc(w.producers?.name ?? '?')} — ${esc(w.name)}${w.year ? ' ' + w.year : ''}</option>`)
    .join('');

  openModal(`
    <h2>Rate wine</h2>
    <form id="rate-form" class="wine-form">
      <label>Wine <select id="rate-wine">${options}</select></label>
      <label>Your score: <output id="rate-out">5</output> / 10
        <input type="range" id="rate-score" min="0" max="10" step="1" value="5">
      </label>
      <button type="submit">Save rating</button>
      <p class="modal-error" id="rate-error" hidden></p>
    </form>
  `);

  const wineSel = document.getElementById('rate-wine');
  const slider  = document.getElementById('rate-score');
  const out     = document.getElementById('rate-out');
  slider.addEventListener('input', () => { out.textContent = slider.value; });

  async function prefill() {
    const existing = await myRatingForWine(wineSel.value);
    slider.value = existing ?? 5;
    out.textContent = slider.value;
  }
  wineSel.addEventListener('change', prefill);
  await prefill();

  document.getElementById('rate-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const err = document.getElementById('rate-error');
    err.hidden = true;
    try {
      await upsertRating(wineSel.value, parseInt(slider.value, 10));
      closeModal();
    } catch (e) {
      showErr(err, e.message || 'Could not save rating.');
    }
  });
}

// -------------------------------------------------------
// Glossary
// -------------------------------------------------------
async function showGlossaryModal() {
  let wines = [];
  let myId = null;
  try {
    [wines, myId] = await Promise.all([listWines(), getMyUserId()]);
  } catch (e) {
    openModal(`<h2>Wine glossary</h2><p class="modal-error">Could not load: ${esc(e.message)}</p>`);
    return;
  }

  let search = '';
  let sort = 'wine';

  openModal(`
    <h2>Wine glossary</h2>
    <div class="glossary-controls">
      <input id="gl-search" class="search-input" type="search" placeholder="Search wines or producers…">
      <select id="gl-sort">
        <option value="wine">Sort: A–Z (wine)</option>
        <option value="producer">Sort: producer</option>
        <option value="year">Sort: year (newest)</option>
        <option value="rating">Sort: rating (highest)</option>
      </select>
    </div>
    <div id="gl-table"></div>
  `);

  const tableEl = document.getElementById('gl-table');

  function render() {
    tableEl.innerHTML = glossaryTableHtml(wines, myId, search, sort);
    tableEl.querySelectorAll('.wine-del').forEach((btn) => {
      btn.addEventListener('click', async () => {
        if (!confirm('Delete this wine and all its ratings? This cannot be undone.')) return;
        try {
          await deleteWine(btn.dataset.id);
          wines = await listWines();
          render();
        } catch (e) {
          alert('Could not delete: ' + (e.message || e));
        }
      });
    });
  }

  document.getElementById('gl-search').addEventListener('input', (e) => {
    search = e.target.value.toLowerCase();
    render();
  });
  document.getElementById('gl-sort').addEventListener('change', (e) => {
    sort = e.target.value;
    render();
  });
  render();
}

function glossaryTableHtml(wines, myId, search, sort) {
  const withAvg = wines.map((w) => {
    const scores = (w.ratings ?? []).map((r) => r.score);
    const avg = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null;
    return { ...w, _scores: scores.length, _avg: avg, _producer: w.producers?.name ?? '' };
  });

  const filtered = search
    ? withAvg.filter((w) =>
        w.name.toLowerCase().includes(search) || w._producer.toLowerCase().includes(search))
    : withAvg;

  filtered.sort((a, b) => {
    switch (sort) {
      case 'producer':
        return a._producer.localeCompare(b._producer) || a.name.localeCompare(b.name);
      case 'year':
        return (b.year ?? -Infinity) - (a.year ?? -Infinity) || a.name.localeCompare(b.name);
      case 'rating':
        return (b._avg ?? -Infinity) - (a._avg ?? -Infinity) || a.name.localeCompare(b.name);
      default: // wine A-Z
        return a.name.localeCompare(b.name);
    }
  });

  if (!filtered.length) {
    return '<p class="modal-empty">No matching wines.</p>';
  }

  const rows = filtered.map((w) => {
    const del = w.created_by && w.created_by === myId
      ? `<button class="wine-del" data-id="${w.id}" title="Delete wine">&times;</button>`
      : '';
    return `<tr>
      <td>${esc(w._producer)}</td>
      <td>${esc(w.name)}</td>
      <td>${w.year ?? '—'}</td>
      <td class="num">${w._avg == null ? '—' : w._avg.toFixed(1)}</td>
      <td class="num">${w._scores}</td>
      <td class="del-cell">${del}</td>
    </tr>`;
  }).join('');

  return `<table class="glossary-table">
    <thead><tr><th>Producer</th><th>Wine</th><th>Year</th><th class="num">Avg</th><th class="num">#</th><th></th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

// -------------------------------------------------------
// Add producer (dedicated form with coordinates)
// -------------------------------------------------------
export function showAddProducerModal(initial = {}) {
  openModal(`
    <h2>Add producer</h2>
    <form id="add-prod-form" class="wine-form">
      <label>Name <input id="ap-name" type="text" required value="${escAttr(initial.name ?? '')}"></label>
      <label>Commune <input id="ap-commune" type="text" value="${escAttr(initial.commune ?? '')}"></label>
      <label>DOCG
        <select id="ap-docg">
          <option value=""${!initial.docg ? ' selected' : ''}>—</option>
          <option value="Barolo"${initial.docg === 'Barolo' ? ' selected' : ''}>Barolo</option>
          <option value="Barbaresco"${initial.docg === 'Barbaresco' ? ' selected' : ''}>Barbaresco</option>
        </select>
      </label>
      <label>Website <input id="ap-website" type="text" placeholder="https://…" value="${escAttr(initial.website ?? '')}"></label>
      <div class="coord-row">
        <label>Latitude <input id="ap-lat" type="number" step="any" required value="${initial.lat ?? ''}"></label>
        <label>Longitude <input id="ap-lon" type="number" step="any" required value="${initial.lon ?? ''}"></label>
      </div>
      <button type="button" id="ap-pick" class="secondary-btn">Pick location on map</button>
      <button type="submit">Save producer</button>
      <p class="modal-error" id="ap-error" hidden></p>
    </form>
  `);

  // Pick on map: stash the current form values, close the modal so the map is
  // clickable, await a click, then reopen pre-filled with the coordinates.
  document.getElementById('ap-pick').addEventListener('click', async () => {
    if (!_pickCoordinate) return;
    const vals = collectProducerForm();
    closeModal();
    const picked = await _pickCoordinate();
    if (picked.lat == null || picked.lon == null) {
      showAddProducerModal(vals);                  // cancelled — reopen unchanged
    } else {
      showAddProducerModal({ ...vals, lat: round6(picked.lat), lon: round6(picked.lon) });
    }
  });

  document.getElementById('add-prod-form').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const err = document.getElementById('ap-error');
    err.hidden = true;
    const v = collectProducerForm();
    if (!v.name) return showErr(err, 'Name is required.');
    if (v.lat === null || v.lon === null) return showErr(err, 'Coordinates are required (type them or use "Pick location on map").');
    try {
      await addProducer({
        name: v.name, commune: v.commune, docg: v.docg || null,
        lat: v.lat, lon: v.lon, website: v.website || null,
      });
      if (_onProducersChanged) await _onProducersChanged();
      closeModal();
    } catch (e) {
      showErr(err, e.message || 'Could not save producer.');
    }
  });
}

function collectProducerForm() {
  const latRaw = document.getElementById('ap-lat').value.trim();
  const lonRaw = document.getElementById('ap-lon').value.trim();
  return {
    name: document.getElementById('ap-name').value.trim(),
    commune: document.getElementById('ap-commune').value.trim(),
    docg: document.getElementById('ap-docg').value,
    website: document.getElementById('ap-website').value.trim(),
    lat: latRaw ? parseFloat(latRaw) : null,
    lon: lonRaw ? parseFloat(lonRaw) : null,
  };
}
function round6(n) { return Math.round(n * 1e6) / 1e6; }

// -------------------------------------------------------
// Producer panel (opened from a producer map click)
// -------------------------------------------------------
export async function showProducerPanel(props) {
  infoTitle.textContent = props.name ?? 'Producer';
  layerSection.hidden = true;
  infoPanel.hidden = false;
  infoContent.innerHTML = '<p class="info-empty">Loading…</p>';

  const meta = [];
  if (props.commune) meta.push(`<tr><th>Commune</th><td>${esc(props.commune)}</td></tr>`);
  if (props.docg)    meta.push(`<tr><th>DOCG</th><td>${esc(props.docg)}</td></tr>`);
  if (props.website) {
    const u = String(props.website);
    meta.push(`<tr><th>Website</th><td><a href="${escAttr(u)}" target="_blank" rel="noopener noreferrer">${esc(u.replace(/^https?:\/\//, ''))}</a></td></tr>`);
  }

  let wines = [];
  let myId = null;
  try {
    [wines, myId] = await Promise.all([listWinesByProducer(props.id), getMyUserId()]);
  } catch (e) {
    infoContent.innerHTML = `<p class="modal-error">Could not load wines: ${esc(e.message)}</p>`;
    return;
  }

  const wineList = wines.length
    ? wines.map((w) => {
        const scores = (w.ratings ?? []).map((r) => r.score);
        const avg = scores.length ? (scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(1) : '—';
        return `<li class="wine-item" data-wine='${escAttr(JSON.stringify({ id: w.id, name: w.name, year: w.year }))}'>
          <span class="wine-item-name">${esc(w.name)}${w.year ? ' <span class="wine-year">' + w.year + '</span>' : ''}</span>
          <span class="wine-item-avg">${avg}</span>
        </li>`;
      }).join('')
    : '<li class="info-empty">No wines yet. Use "Add wine" to add one.</li>';

  // Delete is allowed only for producers the current user created (RLS).
  const canDelete = props.created_by && props.created_by === myId;
  const deleteBtn = canDelete
    ? `<button id="prod-delete" class="danger-btn">Delete this producer</button>`
    : '';

  infoContent.innerHTML = `
    ${meta.length ? `<table class="info-table">${meta.join('')}</table>` : ''}
    <h3 class="panel-subhead">Wines</h3>
    <ul class="wine-list">${wineList}</ul>
    ${deleteBtn}
  `;

  infoContent.querySelectorAll('.wine-item').forEach((li) => {
    li.addEventListener('click', () => {
      const wine = JSON.parse(li.dataset.wine);
      showWineDetail(wine, props);
    });
  });

  if (canDelete) {
    document.getElementById('prod-delete').addEventListener('click', async () => {
      if (!confirm(`Delete "${props.name}" and ALL its wines and ratings? This cannot be undone.`)) return;
      try {
        await deleteProducer(props.id);
        if (_onProducersChanged) await _onProducersChanged();
        infoPanel.hidden = true;
        layerSection.hidden = false;
      } catch (e) {
        alert('Could not delete: ' + (e.message || e));
      }
    });
  }
}

// -------------------------------------------------------
// Wine detail: ratings bar chart + your rating
// -------------------------------------------------------
async function showWineDetail(wine, producerProps) {
  infoTitle.textContent = `${wine.name}${wine.year ? ' ' + wine.year : ''}`;
  infoContent.innerHTML = '<p class="info-empty">Loading…</p>';

  let ratings = [];
  let myScore = null;
  try {
    [ratings, myScore] = await Promise.all([
      listRatingsForWine(wine.id),
      myRatingForWine(wine.id),
    ]);
  } catch (e) {
    infoContent.innerHTML = `<p class="modal-error">Could not load ratings: ${esc(e.message)}</p>`;
    return;
  }

  const chart = ratings.length
    ? ratings
        .sort((a, b) => b.score - a.score)
        .map((r) => `
          <div class="rating-row">
            <span class="rating-name">${esc(r.name)}</span>
            <span class="rating-track"><span class="rating-fill" style="width:${r.score * 10}%"></span></span>
            <span class="rating-score">${r.score}</span>
          </div>`).join('')
    : '<p class="info-empty">No ratings yet.</p>';

  infoContent.innerHTML = `
    <p class="producer-back" id="wine-back">&larr; ${esc(producerProps.name)}</p>
    <h3 class="panel-subhead">Ratings</h3>
    <div class="rating-chart">${chart}</div>
    <h3 class="panel-subhead">Your rating</h3>
    <form id="inline-rate" class="wine-form">
      <label>Score: <output id="ir-out">${myScore ?? 5}</output> / 10
        <input type="range" id="ir-score" min="0" max="10" step="1" value="${myScore ?? 5}">
      </label>
      <button type="submit">${myScore == null ? 'Save rating' : 'Update rating'}</button>
      <p class="modal-error" id="ir-error" hidden></p>
    </form>
  `;

  document.getElementById('wine-back').addEventListener('click', () => showProducerPanel(producerProps));
  const slider = document.getElementById('ir-score');
  const out    = document.getElementById('ir-out');
  slider.addEventListener('input', () => { out.textContent = slider.value; });

  document.getElementById('inline-rate').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const err = document.getElementById('ir-error');
    err.hidden = true;
    try {
      await upsertRating(wine.id, parseInt(slider.value, 10));
      await showWineDetail(wine, producerProps);   // re-render with updated chart
    } catch (e) {
      showErr(err, e.message || 'Could not save rating.');
    }
  });
}

// -------------------------------------------------------
// Helpers
// -------------------------------------------------------
function showErr(el, msg) {
  el.textContent = msg;
  el.hidden = false;
}
function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function escAttr(s) {
  return esc(s).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
