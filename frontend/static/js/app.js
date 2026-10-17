/* UsedCarDossier frontend. Plain JS, no framework, no build step. */

const state = {
  bodyStyle: null,
  brands: new Set(),
  priorities: new Set(),
  options: null,
};

const $  = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const money = (n) =>
  n == null ? 'n/a' : '$' + Math.round(n).toLocaleString('en-US');

const esc = (s) =>
  String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* ---------- navigation ---------- */

function goto(view) {
  $$('.view').forEach((v) => v.classList.remove('active'));
  $('#view-' + view).classList.add('active');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function showStep(n) {
  $$('.panel').forEach((p) =>
    p.classList.toggle('active', p.dataset.panel === String(n)));
  $$('.step').forEach((s) => {
    const i = Number(s.dataset.step);
    s.classList.toggle('active', i === n);
    s.classList.toggle('done', i < n);
  });
}

/* ---------- loading overlay with agent trace ---------- */

const AGENTS = {
  discover: [
    ['profiler', 'Profiler: reading your priorities'],
    ['filter',   'Filtering catalog (Python, no LLM)'],
    ['enrich',   'Pulling EPA + NHTSA data per car'],
    ['score',    'Scoring (deterministic)'],
    ['scout',    'Scout: writing the rationale'],
  ],
  dossier: [
    ['data',     'Fetching trims, recalls, complaints'],
    ['tco',      'Computing 5-year cost (Python)'],
    ['dossier',  'Dossier: explaining the trims'],
    ['analyst',  'Analyst: reading the cost table'],
    ['critic',   'Critic: fact-checking every claim'],
  ],
};

let traceTimer = null;
let elapsedTimer = null;

function startLoading(kind, msg) {
  const steps = AGENTS[kind];
  const ul = $('#agent-trace');
  ul.innerHTML = steps
    .map(([k, label]) => `<li class="wait" data-k="${k}">${esc(label)}</li>`)
    .join('');
  $('#loading').classList.add('on');

  // Hold the last step as "running" rather than marking everything done and
  // then sitting silently -- the trace is indicative, not a real progress bar,
  // and pretending otherwise is how a slow run starts to look like a hung one.
  let i = 0;
  const tick = () => {
    const items = Array.from(ul.children);
    if (i > 0) items[i - 1].className = 'done';
    if (i < items.length) items[i].className = 'run';
    i += 1;
    if (i < items.length) traceTimer = setTimeout(tick, 2200);
  };
  tick();

  // A live counter, so the user can always tell the thing is still working.
  const started = Date.now();
  const render = () => {
    const s = Math.round((Date.now() - started) / 1000);
    const slow = s > 45
      ? ' &mdash; first lookup for these cars, pulling live EPA and NHTSA data'
      : '';
    $('#loading-msg').innerHTML = `${esc(msg)} &middot; ${s}s${slow}`;
  };
  render();
  elapsedTimer = setInterval(render, 1000);
}

function stopLoading() {
  clearTimeout(traceTimer);
  clearInterval(elapsedTimer);
  $('#loading').classList.remove('on');
}

/* ---------- API ---------- */

/* A cold discovery can legitimately take a while: it pulls live EPA and NHTSA
   data for every candidate. But "slow" must never be indistinguishable from
   "broken", so every call carries an explicit timeout and the overlay keeps
   reporting elapsed time rather than sitting on one frozen message. */
const API_TIMEOUT_MS = 180000;

async function api(path, body, timeoutMs = API_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const opts = body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body), signal: controller.signal }
    : { signal: controller.signal };
  try {
    const res = await fetch(path, opts);
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    return await res.json();
  } catch (err) {
    if (err.name === 'AbortError') {
      throw new Error(
        `Timed out after ${Math.round(timeoutMs / 1000)}s. The government APIs ` +
        `can be slow on a first lookup; the data is now cached, so trying again ` +
        `is usually much faster.`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

/* ---------- boot ---------- */

async function boot() {
  try {
    state.options = await api('/api/options');
  } catch (err) {
    $('#body-choices').innerHTML =
      `<div class="note bad"><b>Cannot load the catalog.</b><br>${esc(err.message)}
       <br><br>Run <code>python -m scripts.seed_catalog</code> to build it.</div>`;
    return;
  }

  const descriptions = {
    sedan: 'Compact to full size, plus hatchbacks',
    suv:   'Crossovers and sport utilities',
    truck: 'Small and full-size pickups',
    wagon: 'Station wagons',
    van:   'Minivans and passenger vans',
  };

  $('#body-choices').innerHTML = state.options.body_styles
    .map((b) => `<button class="choice" data-body="${b.key}">
        <b>${esc(b.label)}</b><span>${esc(descriptions[b.key] || '')}</span>
      </button>`)
    .join('');

  $$('[data-body]').forEach((el) =>
    el.addEventListener('click', () => {
      state.bodyStyle = el.dataset.body;
      renderBrands();
      showStep(2);
    }));

  $('#priority-chips').innerHTML = state.options.priorities
    .map((p) => `<button class="chip" data-pri="${p.key}">${esc(p.label)}</button>`)
    .join('');

  $$('[data-pri]').forEach((el) =>
    el.addEventListener('click', () => {
      const k = el.dataset.pri;
      if (state.priorities.has(k)) state.priorities.delete(k);
      else if (state.priorities.size < 3) state.priorities.add(k);
      el.classList.toggle('on', state.priorities.has(k));
    }));

  const makeSel = $('#lk-make');
  makeSel.innerHTML = '<option value="">choose</option>' +
    state.options.brands
      .map((b) => `<option value="${esc(b.name)}">${esc(b.name)}</option>`)
      .join('');
}

function renderBrands() {
  const brands = state.options.brands;
  $('#brand-chips').innerHTML = brands
    .map((b) => `<button class="chip ${b.tier === 'luxury' ? 'lux' : ''}"
         data-brand="${b.slug}">${esc(b.name)}</button>`)
    .join('');
  $$('[data-brand]').forEach((el) =>
    el.addEventListener('click', () => {
      const s = el.dataset.brand;
      if (state.brands.has(s)) state.brands.delete(s);
      else state.brands.add(s);
      el.classList.toggle('on', state.brands.has(s));
    }));
}

/* ---------- discovery ---------- */

async function runDiscovery() {
  const body = {
    body_style: state.bodyStyle,
    brand_slugs: Array.from(state.brands),
    priorities: Array.from(state.priorities),
    budget_min: Number($('#budget-min').value) || 0,
    budget_max: Number($('#budget-max').value) || 50000,
    annual_miles: Number($('#annual-miles').value) || 12000,
    hold_years: Number($('#hold-years').value) || 5,
    year_min: Number($('#year-min').value) || null,
  };

  startLoading('discover', 'Analysing your options');
  try {
    const data = await api('/api/discover', body);
    renderResults(data, body);
    goto('results');
  } catch (err) {
    alert('Something went wrong: ' + err.message);
  } finally {
    stopLoading();
  }
}

function flagChips(risk) {
  if (!risk || !risk.flags || !risk.flags.length) {
    return '<span class="flag neutral">No complaint data</span>';
  }
  const bad = risk.flags.filter((f) => f.severity !== 'normal');
  if (!bad.length) {
    return '<span class="flag ok">No system above segment norm</span>';
  }
  return bad.slice(0, 3)
    .map((f) => `<span class="flag ${f.severity}">
        ${esc(f.system)} ${f.ratio.toFixed(1)}x segment</span>`)
    .join('');
}

function renderResults(data, req) {
  const head = $('#results-head');
  const w = data.weights || {};
  const weightText = Object.entries(w)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `${k.replace('_', ' ')} ${Math.round(v * 100)}%`)
    .join(' &middot; ');

  head.innerHTML = `
    <div class="hero" style="padding:26px 0 18px">
      <h1 style="font-size:27px">${data.results.length} cars worth your attention</h1>
      <p class="lede" style="font-size:15px;margin-bottom:14px">
        Filtered ${data.filtered_count.toLocaleString()} catalog entries down to
        ${data.enriched_count} that fit your budget, then ranked them.
      </p>
      <div class="note"><b>Ranking weights:</b> ${weightText}
        ${data.profiler_notes ? '<br>' + esc(data.profiler_notes) : ''}</div>
      ${(data.tensions || []).length
        ? `<div class="note warn"><b>Worth knowing:</b><ul style="margin:6px 0 0;padding-left:18px">
            ${data.tensions.map((t) => `<li>${esc(t)}</li>`).join('')}</ul></div>`
        : ''}
      ${(data.notes || []).length
        ? `<div class="note">${data.notes.map(esc).join('<br>')}</div>` : ''}
    </div>`;

  $('#results-list').innerHTML = data.results.map((r, i) => {
    const dep = r.tco.lines.find((l) => l.key === 'depreciation');
    return `
    <div class="rescard">
      <div class="rescard-top">
        <div>
          <h3><span class="rank">${i + 1}</span>${esc(r.year)} ${esc(r.make)} ${esc(r.model)}</h3>
          <div class="sub">${esc(r.vehicle_class)} &middot; est. price
            ${money(r.value_low)}&ndash;${money(r.value_high)}
            ${r.msrp_confidence === 'low'
              ? `<span class="pill warn" title="No model-level original MSRP was
                  available, so this band is derived from the median for this body
                  type. Treat it as indicative only and check a real listing.">rough</span>`
              : ''}</div>
        </div>
        <div class="bignum">
          <span class="n">${money(r.tco.total)}</span>
          <span class="l">${req.hold_years}-year cost of ownership</span>
        </div>
      </div>
      <div class="metrics">
        <div class="metric"><div class="mv">${money(r.tco.per_year)}</div><div class="ml">per year</div></div>
        <div class="metric"><div class="mv">$${r.tco.per_mile.toFixed(2)}</div><div class="ml">per mile</div></div>
        <div class="metric"><div class="mv">${money(r.annual_fuel_cost)}</div><div class="ml">fuel / yr</div></div>
        <div class="metric"><div class="mv">${money(dep ? dep.total : null)}</div><div class="ml">depreciation</div></div>
        <div class="metric"><div class="mv">${r.ncap_overall ? r.ncap_overall + '/5' : 'n/r'}</div><div class="ml">NCAP stars</div></div>
      </div>
      ${r.why ? `<div class="why">${esc(r.why)}</div>` : ''}
      <div class="flags">${flagChips(r.risk)}</div>
      <div class="rescard-actions">
        <button class="btn" data-dossier='${esc(JSON.stringify(
          { year: r.year, make: r.make, model: r.model }))}'>
          Full dossier &rarr;</button>
      </div>
    </div>`;
  }).join('');

  $$('[data-dossier]').forEach((el) =>
    el.addEventListener('click', () => {
      const v = JSON.parse(el.dataset.dossier);
      $('#lk-year').value = v.year;
      $('#lk-make').value = v.make;
      loadModels().then(() => {
        $('#lk-model').value = v.model;
        goto('lookup');
      });
    }));

  $('#usage-box').innerHTML = usageHtml(data.usage, 'this search');
}

function usageHtml(u, label) {
  if (!u || !u.call_count) return '';
  return `
  <div class="usage">
    <h4>What this ${esc(label)} cost to run</h4>
    <div class="usage-grid">
      <div><span class="uv">${u.call_count}</span><span class="ul">LLM calls</span></div>
      <div><span class="uv">${u.prompt_tokens.toLocaleString()}</span><span class="ul">prompt tokens</span></div>
      <div><span class="uv">${u.completion_tokens.toLocaleString()}</span><span class="ul">completion tokens</span></div>
      <div><span class="uv">$${u.total_cost_usd.toFixed(5)}</span><span class="ul">total cost</span></div>
    </div>
  </div>`;
}

/* ---------- dossier ---------- */

async function loadModels() {
  const year = Number($('#lk-year').value);
  const make = $('#lk-make').value;
  const sel = $('#lk-model');
  if (!make) { sel.innerHTML = '<option value="">pick a make first</option>'; return; }
  sel.innerHTML = '<option>loading...</option>';
  try {
    const d = await api(`/api/models?year=${year}&make=${encodeURIComponent(make)}`);
    sel.innerHTML = d.models.length
      ? d.models.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join('')
      : '<option value="">no models that year</option>';
  } catch (err) {
    sel.innerHTML = '<option value="">could not load</option>';
  }
}

async function runDossier() {
  const body = {
    year: Number($('#lk-year').value),
    make: $('#lk-make').value,
    model: $('#lk-model').value,
    mileage: Number($('#lk-mileage').value) || null,
    asking_price: Number($('#lk-price').value) || null,
    vin: $('#lk-vin').value.trim() || null,
    annual_miles: Number($('#lk-annual').value) || 12000,
    hold_years: Number($('#lk-hold').value) || 5,
  };
  if (!body.make || !body.model) { alert('Pick a make and model first.'); return; }

  startLoading('dossier', `Building the dossier for ${body.year} ${body.make} ${body.model}`);
  try {
    const data = await api('/api/dossier', body);
    renderDossier(data);
  } catch (err) {
    alert('Something went wrong: ' + err.message);
  } finally {
    stopLoading();
  }
}

function renderDossier(d) {
  const t = d.tco;
  const dj = d.dossier || {};
  const an = d.analysis || {};
  const cr = d.critic || {};

  const trimRows = d.trims.map((tr) => {
    const cheap = dj.cheapest_to_run && tr.label === dj.cheapest_to_run;
    const dear  = dj.most_expensive_to_run && tr.label === dj.most_expensive_to_run;
    return `<tr class="${cheap ? 'best' : dear ? 'worst' : ''}">
      <td>${esc(tr.label)}</td>
      <td>${esc(tr.drive || '')}</td>
      <td class="num">${tr.mpg_combined || '-'}</td>
      <td class="num">${money(tr.annual_fuel_cost)}</td>
    </tr>`;
  }).join('');

  const costRows = t.lines.map((l) => `
    <tr>
      <td><b>${esc(l.label)}</b>${l.is_modelled ? '<span class="asm">MODELLED</span>' : ''}
        <div class="basis">${esc(l.basis)}</div></td>
      <td class="num">${money(l.per_year)}</td>
      <td class="num">${money(l.total)}</td>
    </tr>`).join('');

  const problems = (dj.known_problems || []).map((p) => `
    <div class="note ${/recall|NHTSA/i.test(p.citation || '') ? 'warn' : ''}">
      ${esc(p.claim)}<br>
      <span class="source-chip">${esc(p.citation || 'uncited')}</span>
    </div>`).join('');

  const recalls = d.recalls.map((r) => `
    <div class="recall">
      <div class="recall-id">${esc(r.campaign)}${r.park_it ? ' &middot; DO NOT DRIVE' : ''}</div>
      <div class="recall-comp">${esc(r.component)}</div>
      <div class="recall-sum">${esc((r.summary || '').slice(0, 300))}</div>
      <a href="${esc(r.url)}" target="_blank" rel="noopener">View on NHTSA &rarr;</a>
    </div>`).join('');

  const criticPill = cr.verdict === 'pass'
    ? `<span class="pill good">passed &middot; ${cr.checked_count} claims checked</span>`
    : `<span class="pill warn">${cr.issue_count} issue(s) found</span>`;

  const criticIssues = (cr.issues || []).map((i) => `
    <div class="note ${i.severity === 'high' ? 'bad' : ''}">
      <b>${esc(i.severity.toUpperCase())}</b> &mdash; ${esc(i.problem)}<br>
      <span style="color:var(--ink-3);font-size:12.5px">claim: &ldquo;${esc(i.claim)}&rdquo;</span>
    </div>`).join('');

  $('#dossier-out').innerHTML = `
  <div class="card" style="margin-top:18px">
    <div class="rescard-top" style="padding:0">
      <div>
        <h2>${esc(d.label)}</h2>
        <div class="sub">Estimated value ${money(d.value.low)}&ndash;${money(d.value.high)}
          &middot; MSRP ${money(d.msrp.value)}
          <span class="pill ${d.msrp.confidence === 'exact' ? 'good' : 'warn'}">
            ${esc(d.msrp.confidence)}</span></div>
      </div>
      <div class="bignum">
        <span class="n">${money(t.total)}</span>
        <span class="l">${t.hold_years}-year cost</span>
      </div>
    </div>

    ${an.headline ? `<div class="note good" style="margin-top:16px"><b>${esc(an.headline)}</b>
       ${an.biggest_driver ? '<br>' + esc(an.biggest_driver) : ''}</div>` : ''}

    <div class="note" style="font-size:12.5px">${esc(d.msrp.note)}</div>

    <div class="section">
      <h3>Where the money goes</h3>
      <table class="costtable">
        <thead><tr><th>Cost</th><th style="text-align:right">Per year</th>
          <th style="text-align:right">${t.hold_years} years</th></tr></thead>
        <tbody>${costRows}
          <tr class="total"><td>Total cost of ownership</td>
            <td class="num">${money(t.per_year)}</td>
            <td class="num">${money(t.total)}</td></tr>
        </tbody>
      </table>
      <p class="hint" style="margin-top:10px">
        Bought at ${money(t.purchase_price)}, projected resale ${money(t.projected_resale)}
        after ${t.hold_years} years at ${t.annual_miles.toLocaleString()} mi/yr.
        Lines marked MODELLED rest on a stated assumption &mdash;
        <button class="linkbtn inline" id="open-method">see how it works</button>.
      </p>
      ${(an.surprises || []).map((s) => `<div class="note">${esc(s)}</div>`).join('')}
      ${(t.warnings || []).map((s) => `<div class="note warn">${esc(s)}</div>`).join('')}
    </div>

    <div class="section">
      <h3>Trims that year <span class="pill">${d.trims.length} tested by EPA</span></h3>
      ${dj.trim_summary ? `<p style="font-size:14px;color:var(--ink-2)">${esc(dj.trim_summary)}</p>` : ''}
      <table class="trimtable">
        <thead><tr><th>Trim</th><th>Drive</th><th style="text-align:right">MPG</th>
          <th style="text-align:right">Fuel / yr</th></tr></thead>
        <tbody>${trimRows}</tbody>
      </table>
    </div>

    <div class="section">
      <h3>Known problems
        <span class="pill ${d.risk.flags.some((f) => f.severity === 'high') ? 'bad' : 'good'}">
          ${d.risk.total_complaints.toLocaleString()} NHTSA complaints</span></h3>
      <p style="font-size:13.5px;color:var(--ink-2)">${esc(d.risk.explanation)}</p>
      ${problems || '<div class="note good">No defect claim met the citation bar.</div>'}
    </div>

    ${recalls ? `<div class="section"><h3>Recall campaigns
       <span class="pill bad">${d.recalls.length}</span></h3>${recalls}</div>` : ''}

    <div class="section">
      <h3>Fact check ${criticPill}</h3>
      <p class="hint">
        A separate agent re-checked every claim above against the raw NHTSA and EPA
        records. Defect claims need a campaign number or a complaint count; dollar
        figures must match the computed table exactly.
      </p>
      ${criticIssues || '<div class="note good">Every claim traced back to source data.</div>'}
      ${(cr.applied || []).length
        ? `<div class="note warn"><b>Corrections applied automatically:</b><br>
           ${cr.applied.map(esc).join('<br>')}</div>` : ''}
    </div>

    ${(an.caveats || []).length ? `<div class="section"><h3>Caveats</h3>
      ${an.caveats.map((c) => `<div class="note">${esc(c)}</div>`).join('')}</div>` : ''}

    ${dj.bottom_line ? `<div class="note good"><b>Bottom line.</b> ${esc(dj.bottom_line)}</div>` : ''}
  </div>
  ${usageHtml(d.usage, 'dossier')}`;

  const ob = $('#open-method');
  if (ob) ob.addEventListener('click', openMethodology);
  $('#dossier-out').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ---------- methodology drawer ---------- */

async function openMethodology() {
  $('#method-drawer').classList.add('on');
  const box = $('#method-body');
  box.innerHTML = '<p>Loading...</p>';
  try {
    const m = await api('/api/methodology');
    box.innerHTML = `
      <h3>Where the data comes from</h3>
      ${m.data_sources.map((s) => `<div class="srcrow">
        <b>${esc(s.name)}</b><span>${esc(s.use)}${s.key_required ? '' : ' &middot; no API key'}</span>
      </div>`).join('')}

      <h3>What a car is worth</h3>
      <p>${esc(m.value_model.approach)}</p>
      <p><b>Fit quality:</b> R&sup2; ${m.value_model.r_squared},
         median absolute error ${Math.round(m.value_model.median_abs_pct_error * 100)}%.
         ${esc(m.value_model.note)}</p>

      <h3>How defect risk is measured</h3>
      <p>${esc(m.risk_model.approach)}</p>
      <p><b>Limit:</b> ${esc(m.risk_model.limit)}</p>

      <h3>Assumptions we make</h3>
      <p class="hint">These are judgement calls, not measurements. Listed so you can
         disagree with a specific one.</p>
      ${m.assumptions.map((a) => `<div class="note">
        <b>${esc(a.key)} = ${a.value}</b><br>${esc(a.rationale)}</div>`).join('')}

      <h3>Maintenance basis</h3>
      <p><b>${m.maintenance_basis.value} ${esc(m.maintenance_basis.unit)}</b><br>
         ${esc(m.maintenance_basis.source)}</p>
      <p class="hint">${esc(m.maintenance_basis.note)}</p>`;
  } catch (err) {
    box.innerHTML = `<p>Could not load: ${esc(err.message)}</p>`;
  }
}

/* ---------- wiring ---------- */

document.addEventListener('DOMContentLoaded', () => {
  boot();

  $$('[data-goto]').forEach((el) =>
    el.addEventListener('click', () => goto(el.dataset.goto)));
  $$('[data-back]').forEach((el) =>
    el.addEventListener('click', () => showStep(Number(el.dataset.back))));

  $('#brands-next').addEventListener('click', () => showStep(3));
  $('#priorities-next').addEventListener('click', () => showStep(4));
  $('#run-discovery').addEventListener('click', runDiscovery);
  $('#run-dossier').addEventListener('click', runDossier);
  $('#lk-make').addEventListener('change', loadModels);
  $('#lk-year').addEventListener('change', loadModels);

  $('#nav-method').addEventListener('click', openMethodology);
  $('#method-close').addEventListener('click', () =>
    $('#method-drawer').classList.remove('on'));
  $('#method-drawer').addEventListener('click', (e) => {
    if (e.target.id === 'method-drawer') e.target.classList.remove('on');
  });
});
