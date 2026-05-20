// -------------------------------------------------------
// Wine features UI: add wine, rate wine, glossary, and the
// producer -> wines -> ratings views inside the info panel.
// -------------------------------------------------------
import {
  listProducers, addProducer,
  listWines, listWinesByProducer, addWine,
  listRatingsForWine, myRatingForWine, upsertRating,
} from './db.js';

const layerSection = document.getElementById('layer-section');
const infoPanel    = document.getElementById('info-panel');
const infoTitle    = document.getElementById('info-title');
const infoContent  = document.getElementById('info-content');

const modalOverlay = document.getElementById('modal-overlay');
const modalContent = document.getElementById('modal-content');

let _onProducersChanged = null;

export function initWineUI({ onProducersChanged } = {}) {
  _onProducersChanged = onProducersChanged;
  document.getElementById('btn-add-wine').addEventListener('click', showAddWineModal);
  document.getElementById('btn-rate-wine').addEventListener('click', showRateWineModal);
  document.getElementById('btn-glossary').addEventListener('click', showGlossaryModal);
  document.getElementById('modal-close').addEventListener('click', closeModal);
  modalOverlay.addEventListener('click', (e) => {
    if (e.target === modalOverlay) closeModal();
  });
}

// -------------------------------------------------------
// Modal helpers
// -------------------------------------------------------
function openModal(html) {
  modalContent.innerHTML = html;
  modalOverlay.hidden = false;
}
function closeModal() {
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
  try {
    wines = await listWines();
  } catch (e) {
    openModal(`<h2>Wine glossary</h2><p class="modal-error">Could not load: ${esc(e.message)}</p>`);
    return;
  }
  if (!wines.length) {
    openModal(`<h2>Wine glossary</h2><p class="modal-empty">No wines yet.</p>`);
    return;
  }
  const rows = wines.map((w) => {
    const scores = (w.ratings ?? []).map((r) => r.score);
    const avg = scores.length ? (scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(1) : '—';
    return `<tr>
      <td>${esc(w.producers?.name ?? '')}</td>
      <td>${esc(w.name)}</td>
      <td>${w.year ?? '—'}</td>
      <td class="num">${avg}</td>
      <td class="num">${scores.length}</td>
    </tr>`;
  }).join('');

  openModal(`
    <h2>Wine glossary</h2>
    <table class="glossary-table">
      <thead><tr><th>Producer</th><th>Wine</th><th>Year</th><th class="num">Avg</th><th class="num">#</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `);
}

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
  try {
    wines = await listWinesByProducer(props.id);
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

  infoContent.innerHTML = `
    ${meta.length ? `<table class="info-table">${meta.join('')}</table>` : ''}
    <h3 class="panel-subhead">Wines</h3>
    <ul class="wine-list">${wineList}</ul>
  `;

  infoContent.querySelectorAll('.wine-item').forEach((li) => {
    li.addEventListener('click', () => {
      const wine = JSON.parse(li.dataset.wine);
      showWineDetail(wine, props);
    });
  });
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
