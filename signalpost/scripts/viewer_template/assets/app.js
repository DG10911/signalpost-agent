/* Signalpost viewer. Plain JS, no dependencies, no build step.
   Routes (hash based, so it works on any static host incl. GitHub Pages):
     #/?q=&has=&m=&sort=      find (search + filters + sort)
     #/company/<org>?fact=<id> company page, optional evidence deep link
     #/compare?ids=a,b,c      compare 2-4 companies
     #/about                  where the data comes from and how to verify it */
(() => {
  'use strict';

  const MAX_CMP = 4;
  const PAGE = 48;
  const SECTIONS_DEFAULT = ['Overview', 'Finances', 'People', 'Website & social', 'Hiring', 'News'];
  const FLAGS = [
    { k: 'a', label: 'Accounts', stat: 'with_accounts' },
    { k: 'w', label: 'Website', stat: 'with_website' },
    { k: 's', label: 'Social', stat: 'with_social' },
    { k: 'n', label: 'News', stat: 'with_news' },
    { k: 'h', label: 'Hiring', stat: 'with_hiring' },
  ];
  const METRICS = ['Revenue', 'Operating result', 'Annual result', 'Equity', 'Total assets', 'Debt'];
  const METRIC_FIELD = {
    'financials.revenue': 'Revenue', 'financials.operating_result': 'Operating result',
    'financials.annual_result': 'Annual result', 'financials.equity': 'Equity',
    'financials.assets': 'Total assets', 'financials.debt': 'Debt',
  };
  const TAGS = ['A', 'B', 'C', 'D'];
  const TAG_COLORS = ['var(--ca)', 'var(--cb)', 'var(--cc)', 'var(--cd)'];

  const S = {
    index: [], byOrg: new Map(), stats: {}, cache: new Map(), facts: new Map(),
    compare: [], home: { q: '', has: new Set(), m: '', sort: 'facts', shown: PAGE }, homeScroll: 0,
    route: '', drawer: { list: [], i: -1, opener: null, open: false },
  };

  // ---------- helpers ----------
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const main = () => $('#main');
  const SP = ' ';
  const fmtInt = (v) => Math.round(Math.abs(v)).toString().replace(/\B(?=(\d{3})+(?!\d))/g, SP);
  const fmtFull = (v) => (v < 0 ? '−' : '') + fmtInt(v);
  function fmtCompact(v) {
    const a = Math.abs(v), sign = v < 0 ? '−' : '';
    if (a >= 1e9) return sign + (a / 1e9).toFixed(1) + ' bn';
    if (a >= 1e6) return sign + (a / 1e6).toFixed(1) + ' M';
    if (a >= 1e4) return sign + Math.round(a / 1e3) + ' k';
    return sign + fmtInt(a);
  }
  const fmtOrg = (o) => String(o).replace(/^(\d{3})(\d{3})(\d{3})$/, `$1${SP}$2${SP}$3`);
  const n = (x) => Number(x).toLocaleString('en-GB');
  const SMALL = new Set(['og', 'i', 'på', 'and']);
  function tc(s) {
    if (!s || s !== s.toUpperCase()) return s || '';
    return s.toLowerCase().split(/(\s+|-|\/)/).map((w, i) => (i > 0 && SMALL.has(w)) ? w : w.charAt(0).toUpperCase() + w.slice(1)).join('');
  }
  function fold(s, alt) {
    let t = String(s || '').toLowerCase();
    if (alt) t = t.replace(/å/g, 'aa').replace(/ø/g, 'oe').replace(/æ/g, 'ae');
    return t.replace(/æ/g, 'ae').replace(/ø/g, 'o').replace(/ß/g, 'ss').normalize('NFD').replace(/[̀-ͯ]/g, '');
  }
  function fmtDate(s) {
    if (!s) return '';
    const m = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?/.exec(s);
    if (!m) return s;
    const mon = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][+m[2] - 1];
    return `${+m[3]} ${mon} ${m[1]}` + (m[4] ? `, ${m[4]}:${m[5]} UTC` : '');
  }
  function toast(msg) {
    const t = $('#toast');
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(toast.t);
    toast.t = setTimeout(() => t.classList.remove('show'), 1800);
  }
  async function copy(text, what) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (_) {
      const ta = document.createElement('textarea');
      ta.value = text; ta.setAttribute('readonly', ''); ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); } catch (_) { /* ignore */ }
      ta.remove();
    }
    toast(`${what} copied`);
  }
  const ICON = {
    src: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M6.5 9.5l3-3M7 4.5l1-1a2.5 2.5 0 013.5 3.5l-1 1M9 11.5l-1 1A2.5 2.5 0 014.5 9l1-1" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    search: '<svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true"><circle cx="9" cy="9" r="6" fill="none" stroke="currentColor" stroke-width="2"/><path d="M13.5 13.5L18 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    x: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
    copy: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><rect x="5" y="5" width="8" height="9" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M3 11V3.5A1.5 1.5 0 014.5 2H10" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>',
    ext: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M9 3h4v4M13 3L7 9M11 10v3H3V5h3" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };
  const srcBtn = (id, label, small) => small
    ? `<button type="button" class="src-ico" data-fact="${esc(id)}" aria-label="Show source: ${esc(label)}" title="Show source">${ICON.src}</button>`
    : `<button type="button" class="src-btn" data-fact="${esc(id)}" aria-label="Show source for ${esc(label)}">${ICON.src}Source</button>`;

  function sourceKind(type, name, url) {
    const t = `${type} ${name} ${url}`.toLowerCase();
    if (/registry|accounts|roles|brønnøysund|brreg/.test(t)) return ['registry', 'Official registry'];
    if (/job|nav\.no|arbeidsplassen/.test(t)) return ['jobs', 'Job board'];
    if (/facebook|instagram|linkedin|twitter|x\.com|social/.test(t)) return ['social', 'Social profile'];
    if (/news|rss|article/.test(t)) return ['news', 'News source'];
    if (/website|company_site|company_owned/.test(t)) return ['website', 'Company website'];
    return ['other', 'Web source'];
  }

  // ---------- data ----------
  async function getJSON(url) {
    const r = await fetch(url, { cache: 'default' });
    if (!r.ok) throw new Error(`${r.status} ${url}`);
    return r.json();
  }
  function prepIndex(rows) {
    for (const c of rows) {
      c.hay = [fold(c.n), fold(c.n, true), c.o, fold(c.m), fold(c.m, true), fold(c.i)].join(' ');
      c.nf = fold(c.n);
      S.byOrg.set(c.o, c);
    }
    return rows;
  }
  async function company(org) {
    if (S.cache.has(org)) return S.cache.get(org);
    const raw = await getJSON(`data/c/${encodeURIComponent(org)}.json`);
    const src = (i) => {
      const r = i == null ? null : raw.sources[i];
      return r ? { url: r[0], name: r[1], type: r[2], retrieved: r[3], sha: r[4] } : null;
    };
    const co = { ...raw, facts: [] };
    for (const f of raw.facts) {
      const fact = { id: f.id, section: f.sec, label: f.label, field: f.field, value: f.v, link: f.link,
        avail: f.a, availText: f.at, period: f.p, quote: f.q, src: src(f.s), more: [], co };
      co.facts.push(fact);
      S.facts.set(fact.id, fact);
    }
    const extra = (items, kind) => (items || []).map((it, k) => {
      const srcs = (it.s || []).map((i, j) => ({ src: src(i), quote: (it.q || [])[j] || '' })).filter((x) => x.src);
      const fact = { id: it.id || `${org}-${kind}${k}`, section: kind, field: it.field || kind, raw: it, co,
        label: kind === 'Summary' ? humanField(it.field) : 'Change', value: '', link: '', avail: it.availability || 'available',
        availText: '', period: '', quote: srcs[0] ? srcs[0].quote : '', src: srcs[0] ? srcs[0].src : null, more: srcs.slice(1) };
      S.facts.set(fact.id, fact);
      return fact;
    });
    co.synth = extra(raw.synthesis, 'Summary');
    co.changeList = extra(raw.changes, 'Changes');
    S.cache.set(org, co);
    return co;
  }
  function humanField(f) {
    const s = String(f || '').replace(/^synthesis\.?/, '').replace(/[._]/g, ' ').trim();
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : 'Summary';
  }

  // ---------- compare selection (persisted) ----------
  function loadCompare() {
    try { S.compare = JSON.parse(localStorage.getItem('sp-compare') || '[]').filter((o) => S.byOrg.has(o)).slice(0, MAX_CMP); } catch (_) { S.compare = []; }
  }
  function saveCompare() {
    try { localStorage.setItem('sp-compare', JSON.stringify(S.compare)); } catch (_) { /* private mode */ }
    renderTray();
    $$('[data-cmp]').forEach(syncCmpButton);
  }
  function toggleCompare(org) {
    const i = S.compare.indexOf(org);
    if (i >= 0) S.compare.splice(i, 1);
    else if (S.compare.length >= MAX_CMP) { toast(`You can compare up to ${MAX_CMP} companies`); return; } else S.compare.push(org);
    saveCompare();
    const c = S.byOrg.get(org);
    toast(i >= 0 ? `Removed ${c ? c.n : org} from compare` : `Added ${c ? c.n : org} to compare (${S.compare.length}/${MAX_CMP})`);
  }
  function syncCmpButton(b) {
    const on = S.compare.includes(b.dataset.cmp);
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
    b.disabled = !on && S.compare.length >= MAX_CMP;
    const big = b.classList.contains('btn');
    b.innerHTML = on ? (big ? '✓ In compare' : '✓ Compare') : (big ? '+ Add to compare' : '+ Compare');
    b.title = b.disabled ? `Up to ${MAX_CMP} companies` : '';
    const card = b.closest('.card');
    if (card) card.classList.toggle('sel-on', on);
  }
  const compareHref = (ids) => `#/compare?ids=${ids.join(',')}`;
  function renderTray() {
    const tray = $('#tray'), cnt = $('#nav-count');
    cnt.hidden = !S.compare.length;
    cnt.textContent = S.compare.length;
    const onCompare = S.route.startsWith('/compare');
    tray.hidden = !S.compare.length || onCompare || S.drawer.open;
    if (tray.hidden) return;
    $('#tray-list').innerHTML = S.compare.map((o, i) => {
      const c = S.byOrg.get(o);
      return `<span class="tray-item"><span>${esc(c ? c.n : o)}</span><button type="button" data-untray="${o}" aria-label="Remove ${esc(c ? c.n : o)} from compare">${ICON.x}</button></span>`;
    }).join('') + (S.compare.length < 2 ? '<span class="tray-hint">Pick one more company to compare</span>' : '');
    const go = $('#tray-go');
    go.href = compareHref(S.compare);
    go.textContent = `Compare ${S.compare.length}`;
    go.setAttribute('aria-disabled', S.compare.length < 2 ? 'true' : 'false');
  }

  // ---------- search ----------
  function search(q) {
    const raw = q.trim();
    const digits = raw.replace(/[\s.\-]/g, '');
    const isNum = /^\d{2,}$/.test(digits);
    const toks = fold(raw).split(/\s+/).filter(Boolean);
    const out = [];
    for (const c of S.index) {
      let score = 0;
      if (isNum) {
        if (c.o === digits) score = 100; else if (c.o.startsWith(digits)) score = 80; else if (c.o.includes(digits)) score = 60;
      }
      if (!score && toks.length && toks.every((t) => c.hay.includes(t))) {
        const ql = toks.join(' ');
        score = c.nf === ql ? 90 : c.nf.startsWith(ql) ? 70 : (' ' + c.nf).includes(' ' + toks[0]) ? 50 : 20;
      }
      if (!toks.length) score = 1;
      if (score) out.push([c, score]);
    }
    return out;
  }
  const SORTS = {
    facts: (a, b) => b.fc - a.fc || a.n.localeCompare(b.n),
    revenue: (a, b) => ((b.r ? b.r[0] : -Infinity) - (a.r ? a.r[0] : -Infinity)) || a.n.localeCompare(b.n),
    name: (a, b) => a.n.localeCompare(b.n, 'nb'),
  };
  function filtered() {
    const h = S.home;
    let rows = search(h.q).filter(([c]) => [...h.has].every((k) => c.fl.includes(k)) && (!h.m || c.m === h.m));
    const sort = SORTS[h.sort] || SORTS.facts;
    rows.sort((x, y) => (h.q.trim() ? (y[1] >= 50) - (x[1] >= 50) || y[1] - x[1] : 0) || sort(x[0], y[0]));
    return rows.map((r) => r[0]);
  }

  // ---------- views: home ----------
  function homeParams(qs) {
    const p = new URLSearchParams(qs);
    S.home.q = p.get('q') || '';
    S.home.has = new Set((p.get('has') || '').split(',').filter((k) => FLAGS.some((f) => f.k === k)));
    S.home.m = p.get('m') || '';
    S.home.sort = SORTS[p.get('sort')] ? p.get('sort') : 'facts';
  }
  function homeHash() {
    const p = new URLSearchParams();
    const h = S.home;
    if (h.q) p.set('q', h.q);
    if (h.has.size) p.set('has', [...h.has].join(','));
    if (h.m) p.set('m', h.m);
    if (h.sort !== 'facts') p.set('sort', h.sort);
    const s = p.toString();
    return '#/' + (s ? '?' + s : '');
  }
  function viewHome(qs) {
    homeParams(qs);
    const st = S.stats;
    const munis = new Map();
    for (const c of S.index) if (c.m) munis.set(c.m, (munis.get(c.m) || 0) + 1);
    const muniOpts = [...munis.entries()].sort((a, b) => a[0].localeCompare(b[0], 'nb'))
      .map(([m, k]) => `<option value="${esc(m)}"${m === S.home.m ? ' selected' : ''}>${esc(tc(m))} (${k})</option>`).join('');
    main().innerHTML = `
      <section class="hero" aria-labelledby="h-home">
        <h1 id="h-home" tabindex="-1">Norwegian company profiles you can check</h1>
        <p>Researched by the Signalpost agent from official registers, company websites, job boards and news. Every fact has a <b>Source</b> button showing where it came from, when it was read, the quoted text and a SHA-256 fingerprint.</p>
        <ul class="stats" aria-label="Dataset summary">
          <li><b>${n(st.companies)}</b> companies</li>
          <li><b>${n(st.cited_facts_found)}</b> cited facts</li>
          <li><b>${n(st.municipalities)}</b> municipalities</li>
          <li>Retrieved <b>${esc(fmtDate(st.retrieved_to))}</b></li>
        </ul>
      </section>
      <div class="searchbar">
        <form class="search" role="search" onsubmit="return false">
          <label class="sr" for="q">Search companies by name or organisation number</label>
          ${ICON.search}
          <input id="q" type="search" autocomplete="off" spellcheck="false" enterkeyhint="search"
            placeholder="Search ${n(st.companies)} companies by name or org. number" value="${esc(S.home.q)}" aria-controls="results">
          <button type="button" class="icon-btn clear" id="q-clear" aria-label="Clear search"${S.home.q ? '' : ' hidden'}>${ICON.x}</button>
        </form>
      </div>
      <div class="filters" role="group" aria-label="Filters">
          ${FLAGS.map((f) => `<button type="button" class="chip" data-flag="${f.k}" aria-pressed="${S.home.has.has(f.k)}">Has ${f.label.toLowerCase()} <span class="n">${n(st[f.stat] || 0)}</span></button>`).join('')}
          <label class="sel">Municipality
            <select id="muni"><option value="">All</option>${muniOpts}</select></label>
          <label class="sel">Sort
            <select id="sort">
              <option value="facts"${S.home.sort === 'facts' ? ' selected' : ''}>Most facts</option>
              <option value="revenue"${S.home.sort === 'revenue' ? ' selected' : ''}>Revenue</option>
              <option value="name"${S.home.sort === 'name' ? ' selected' : ''}>Name A–Z</option>
            </select></label>
      </div>
      <div class="resultbar">
        <span id="count" role="status" aria-live="polite"></span>
        <span class="right">
          <button type="button" class="btn ghost sm" id="reset" hidden>Reset filters</button>
          <button type="button" class="btn ghost sm" id="csv">Download CSV</button>
        </span>
      </div>
      <div class="grid" id="results"></div>
      <div class="more" id="more"></div>`;
    const q = $('#q');
    let t;
    q.addEventListener('input', () => {
      clearTimeout(t);
      t = setTimeout(() => { S.home.q = q.value; S.home.shown = PAGE; $('#q-clear').hidden = !q.value; updateHome(); }, 60);
    });
    q.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { const first = $('#results .card h3 a'); if (first) location.hash = first.getAttribute('href'); }
      if (e.key === 'Escape' && q.value) { q.value = ''; q.dispatchEvent(new Event('input')); }
    });
    $('#q-clear').addEventListener('click', () => { q.value = ''; q.dispatchEvent(new Event('input')); q.focus(); });
    $$('[data-flag]').forEach((b) => b.addEventListener('click', () => {
      const k = b.dataset.flag;
      if (S.home.has.has(k)) S.home.has.delete(k); else S.home.has.add(k);
      b.setAttribute('aria-pressed', S.home.has.has(k));
      S.home.shown = PAGE; updateHome();
    }));
    $('#muni').addEventListener('change', (e) => { S.home.m = e.target.value; S.home.shown = PAGE; updateHome(); });
    $('#sort').addEventListener('change', (e) => { S.home.sort = e.target.value; S.home.shown = PAGE; updateHome(); });
    $('#reset').addEventListener('click', () => { S.home = { q: '', has: new Set(), m: '', sort: 'facts', shown: PAGE }; history.replaceState(null, '', '#/'); viewHome(''); $('#q').focus(); });
    $('#csv').addEventListener('click', downloadCSV);
    updateHome();
  }
  function card(c) {
    const badges = FLAGS.filter((f) => c.fl.includes(f.k)).map((f) => `<span class="badge on">✓ ${f.label}</span>`).join('');
    const rev = c.r ? `Revenue ${esc(c.r[1])} <b>NOK ${fmtCompact(c.r[0])}</b>` : '<span>No revenue filed</span>';
    return `<article class="card" aria-labelledby="c-${c.o}">
      <h3 id="c-${c.o}"><a href="#/company/${c.o}">${esc(c.n)}</a></h3>
      <div class="meta"><span class="mono">${fmtOrg(c.o)}</span> · ${esc(tc(c.m) || 'Municipality not registered')} · ${esc(c.i)}</div>
      <div class="badges">${badges}<span class="badge blue">${c.fc} cited facts</span></div>
      <div class="row"><span class="fig">${rev}</span>
        <button type="button" class="cmp-toggle" data-cmp="${c.o}" aria-label="Compare ${esc(c.n)}" aria-pressed="false">+ Compare</button></div>
    </article>`;
  }
  function updateHome() {
    const rows = filtered();
    S.home.rows = rows;
    const shown = rows.slice(0, S.home.shown);
    $('#results').innerHTML = shown.length ? shown.map(card).join('')
      : `<div class="empty"><p><b>No companies match.</b></p><p>Try fewer filters, another spelling, or the 9-digit organisation number.</p></div>`;
    $('#results').style.display = shown.length ? '' : 'block';
    const active = S.home.q || S.home.has.size || S.home.m;
    $('#count').textContent = rows.length === S.index.length && !active
      ? `Showing ${shown.length} of ${n(rows.length)} companies`
      : `${n(rows.length)} ${rows.length === 1 ? 'company matches' : 'companies match'}` + (rows.length > shown.length ? ` · showing ${shown.length}` : '');
    $('#reset').hidden = !(active || S.home.sort !== 'facts');
    $('#more').innerHTML = rows.length > shown.length
      ? `<button type="button" class="btn" id="more-btn">Show ${Math.min(PAGE, rows.length - shown.length)} more</button>` : '';
    const mb = $('#more-btn');
    if (mb) mb.addEventListener('click', () => {
      const firstNew = S.home.shown;
      S.home.shown += PAGE; updateHome();
      const a = $$('#results .card h3 a')[firstNew];
      if (a) a.focus();
    });
    $$('[data-cmp]').forEach(syncCmpButton);
    history.replaceState(null, '', homeHash());
  }
  function downloadCSV() {
    const rows = S.home.rows || [];
    const cols = ['organisation_number', 'name', 'municipality', 'legal_form', 'industry', 'employees', 'website', 'latest_revenue_nok', 'revenue_year', 'cited_facts', 'sources', 'flags'];
    const q = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
    const lines = [cols.join(',')].concat(rows.map((c) => [c.o, c.n, c.m, c.f, c.i, c.e, c.w, c.r ? c.r[0] : '', c.r ? c.r[1] : '', c.fc, c.sc,
      FLAGS.filter((f) => c.fl.includes(f.k)).map((f) => f.label).join(' ')].map(q).join(',')));
    const url = URL.createObjectURL(new Blob(['﻿' + lines.join('\n')], { type: 'text/csv;charset=utf-8' }));
    const a = Object.assign(document.createElement('a'), { href: url, download: 'signalpost-companies.csv' });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // ---------- views: company ----------
  const secId = (s) => 'sec-' + s.toLowerCase().replace(/[^a-z]+/g, '-').replace(/-$/, '');
  function factValue(f) {
    if (f.field === 'roles' && f.value.includes('; ')) return `<ul>${cleanRoles(f.value).map((r) => `<li>${esc(r)}</li>`).join('')}</ul>`;
    const v = esc(f.value || '');
    if (f.link && /^https?:\/\//.test(f.link)) return `<a href="${esc(f.link)}" target="_blank" rel="noopener noreferrer">${v || esc(f.link)}</a>`;
    return v;
  }
  const cleanRoles = (v) => String(v).replace(/\['([^'\]]*)'\]/g, '$1').split('; ');
  function factRow(f) {
    const ok = f.avail === 'available';
    const label = f.label + (f.period ? ` (${f.period})` : '');
    return `<li class="fact${ok ? '' : ' miss'}">
      <span class="lab">${esc(f.label)}</span>
      <span class="val">${ok ? factValue(f) : esc(f.availText || 'Not found')}${f.period ? `<span class="per">Period ${esc(f.period)}</span>` : ''}</span>
      <span>${f.src ? srcBtn(f.id, label) : ''}</span></li>`;
  }
  function finTable(facts) {
    const cells = {};
    const years = new Set();
    for (const f of facts) {
      const m = METRIC_FIELD[f.field];
      if (!m || f.avail !== 'available' || !f.period) continue;
      const y = f.period.slice(0, 4);
      const v = Number(String(f.value).replace(/[^\d\-.]/g, ''));
      if (!Number.isFinite(v)) continue;
      years.add(y);
      (cells[m] = cells[m] || {})[y] = { v, f };
    }
    const ys = [...years].sort().reverse().slice(0, 4);
    if (!ys.length) return '';
    const rows = METRICS.filter((m) => cells[m]).map((m) => `<tr><th scope="row">${esc(m)}</th>${ys.map((y) => {
      const c = cells[m][y];
      return c ? `<td><span class="cell"><span class="${c.v < 0 ? 'neg' : ''}" title="NOK ${fmtFull(c.v)}">${fmtCompact(c.v)}</span>${srcBtn(c.f.id, `${m} ${y}`, true)}</span></td>` : '<td class="muted">—</td>';
    }).join('')}</tr>`).join('');
    return `<div class="fin-wrap"><table class="fin"><caption class="sr">Filed annual accounts, NOK</caption>
      <thead><tr><th scope="col">NOK</th>${ys.map((y) => `<th scope="col">${y}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div>`;
  }
  function deltaHtml(ch) {
    if (ch == null) return '<span class="delta flat">No comparable prior year</span>';
    const cls = ch > 0 ? 'up' : ch < 0 ? 'down' : 'flat';
    const arrow = ch > 0 ? '▲' : ch < 0 ? '▼' : '■';
    return `<span class="delta ${cls}">${arrow} ${ch > 0 ? '+' : ''}${ch.toFixed(1)}% vs prior year</span>`;
  }
  function synthHtml(co) {
    if (!co.synth.length) return '';
    const val = (v) => {
      if (v == null || v === '') return '';
      if (typeof v === 'string' || typeof v === 'number') return `<p>${esc(v)}</p>`;
      if (Array.isArray(v)) {
        return `<ul>${v.map((x) => `<li>${esc(typeof x === 'object' && x ? (x.text || x.statement || x.sentence || x.summary || x.value || JSON.stringify(x)) : x)}</li>`).join('')}</ul>`;
      }
      if (typeof v === 'object') return `<ul>${Object.entries(v).map(([k, x]) => `<li><b>${esc(humanField(k))}:</b> ${esc(typeof x === 'object' ? JSON.stringify(x) : x)}</li>`).join('')}</ul>`;
      return `<p>${esc(v)}</p>`;
    };
    const items = co.synth.slice().sort((a, b) => (b.field === 'synthesis.summary') - (a.field === 'synthesis.summary'));
    return `<section class="panel synth" id="sec-summary" aria-labelledby="h-summary">
      <h2 id="h-summary">Summary <span class="badge blue">Generated from the cited facts</span></h2>
      ${items.map((f) => `<div>${f.field !== 'synthesis.summary' ? `<div class="lbl">${esc(f.label)}</div>` : ''}${val(f.raw.value)}
        ${f.src ? `<p>${srcBtn(f.id, f.label)}${f.more.length ? ` <span class="muted">+${f.more.length} more source${f.more.length > 1 ? 's' : ''}</span>` : ''}</p>` : ''}</div>`).join('')}
    </section>`;
  }
  function changesHtml(co) {
    if (!co.changeList.length) return '';
    const pick = (o, ks) => { for (const k of ks) if (o[k] != null && o[k] !== '') return [k, o[k]]; return [null, null]; };
    const show = (v) => esc(typeof v === 'object' ? JSON.stringify(v) : v);
    return `<section class="panel" id="sec-changes" aria-labelledby="h-changes">
      <h2 id="h-changes">Changes since the last check <span class="badge">${co.changeList.length}</span></h2>
      <ul class="changes">${co.changeList.map((f) => {
        const ch = f.raw.change || {};
        const [kf, field] = pick(ch, ['label', 'field', 'type', 'kind']);
        const [kb, before] = pick(ch, ['previous', 'previous_value', 'old', 'old_value', 'from', 'before']);
        const [ka, after] = pick(ch, ['current', 'current_value', 'new', 'new_value', 'to', 'after', 'value']);
        const [kd, when] = pick(ch, ['detected_at', 'changed_at', 'observed_at', 'date', 'at']);
        const rest = Object.entries(ch).filter(([k]) => ![kf, kb, ka, kd].includes(k));
        f.label = field ? `Change: ${humanField(field)}` : 'Change';
        return `<li><div class="what"><b>${esc(field ? humanField(field) : 'Change')}</b>
          ${before != null ? `<div><del>${show(before)}</del> → <ins>${show(after)}</ins></div>` : after != null ? `<div>${show(after)}</div>` : ''}
          ${when ? `<div class="muted">${esc(fmtDate(String(when)))}</div>` : ''}
          ${rest.length ? `<div class="muted">${rest.map(([k, v]) => `${esc(humanField(k))}: ${show(v)}`).join(' · ')}</div>` : ''}</div>
          <div>${f.src ? srcBtn(f.id, f.label) : ''}</div></li>`;
      }).join('')}</ul></section>`;
  }
  async function viewCompany(org, qs) {
    const ix = S.byOrg.get(org);
    if (!ix) { notFound(`No company with organisation number ${org} in this dataset.`); return; }
    main().innerHTML = `<div class="loading" role="status">Loading ${esc(ix.n)}…</div>`;
    let co;
    try { co = await company(org); } catch (e) { notFound(`Could not load ${esc(ix.n)} (${esc(e.message)}).`); return; }
    if (S.route !== `/company/${org}`) return; // user navigated away while loading
    document.title = `${co.name} — Signalpost`;
    const sections = (S.stats.sections || SECTIONS_DEFAULT).slice();
    for (const f of co.facts) if (!sections.includes(f.section)) sections.push(f.section);
    const bySec = {};
    for (const f of co.facts) (bySec[f.section] = bySec[f.section] || []).push(f);
    const found = (s) => (bySec[s] || []).filter((f) => f.avail === 'available').length;
    const kpis = METRICS.filter((m) => co.latest[m]).map((m) => {
      const k = co.latest[m];
      return `<div class="kpi"><span class="k">${esc(m)} ${esc(k.year)}${srcBtn(k.fact_id, `${m} ${k.year}`, true)}</span>
        <span class="v ${k.value < 0 ? 'neg' : ''}">NOK ${fmtCompact(k.value)}</span>
        <span class="full">${fmtFull(k.value)} NOK</span>${deltaHtml(k.change_pct_vs_previous_year)}</div>`;
    }).join('');
    const toc = [];
    if (co.synth.length) toc.push(['sec-summary', 'Summary', '']);
    toc.push(['sec-key', 'Key figures', ''], ['sec-found', 'What we found', '']);
    if (co.changeList.length) toc.push(['sec-changes', 'Changes', co.changeList.length]);
    for (const s of sections) if (bySec[s]) toc.push([secId(s), s, found(s)]);
    const secHtml = sections.filter((s) => bySec[s]).map((s) => {
      const fs = bySec[s];
      const ok = fs.filter((f) => f.avail === 'available');
      const miss = fs.filter((f) => f.avail !== 'available');
      let body = '';
      let listed = ok;
      if (s === 'Finances') {
        body = finTable(ok);
        listed = body ? ok.filter((f) => !METRIC_FIELD[f.field] || !f.period) : ok;
      }
      return `<section class="panel" id="${secId(s)}" aria-labelledby="h-${secId(s)}">
        <h2 id="h-${secId(s)}">${esc(s)} <span class="badge ${ok.length ? 'on' : 'off'}">${ok.length ? `${ok.length} found` : 'Nothing found'}</span></h2>
        ${s === 'Finances' && body ? '<p class="note">Filed annual accounts (NOK). Tap a link icon to see the filing it came from.</p>' : ''}
        ${body}
        ${listed.length ? `<ul class="facts">${listed.map(factRow).join('')}</ul>` : ''}
        ${miss.length ? `<details class="missing"${ok.length ? '' : ' open'}><summary>Checked but not found (${miss.length})</summary><ul class="facts">${miss.map(factRow).join('')}</ul></details>` : ''}
      </section>`;
    }).join('');
    const covHtml = sections.map((s) => {
      const k = found(s);
      const has = !!bySec[s];
      return `<a class="ci ${k ? 'yes' : 'no'}" href="#/company/${org}" data-target="${has ? secId(s) : ''}"><span class="dot" aria-hidden="true">${k ? '✓' : '–'}</span>
        <span>${esc(s)}<small>${k ? `${k} fact${k > 1 ? 's' : ''}` : 'None found'}</small></span></a>`;
    }).join('');
    const web = co.website && /^https?:/.test(co.website) ? co.website : '';
    main().innerHTML = `
      <nav class="crumbs" aria-label="Breadcrumb"><a href="${homeHash()}">← All companies</a></nav>
      <section class="co-head" aria-labelledby="h-co">
        <h1 id="h-co" tabindex="-1">${esc(co.name)}</h1>
        <div class="co-ids">
          <span>Org. no. <span class="mono">${fmtOrg(co.org)}</span>
            <button type="button" class="icon-btn" style="width:30px;height:30px" data-copy="${co.org}" data-what="Organisation number" aria-label="Copy organisation number">${ICON.copy}</button></span>
          ${co.legal_form ? `<span>${esc(co.legal_form)}</span>` : ''}
          ${co.municipality ? `<span>${esc(tc(co.municipality))}</span>` : ''}
          ${co.industry ? `<span>${esc(co.industry)}</span>` : ''}
          ${co.employees ? `<span>${esc(co.employees)} employees (registry)</span>` : ''}
          ${web ? `<span><a href="${esc(web)}" target="_blank" rel="noopener noreferrer">${esc(web.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, ''))}</a></span>` : ''}
        </div>
        ${co.summary ? `<p class="co-sum">${esc(co.summary)}</p>` : ''}
        <div class="co-actions">
          <button type="button" class="btn" data-cmp="${co.org}" aria-pressed="false">+ Add to compare</button>
          <button type="button" class="btn ghost" data-copy="${esc(location.href.split('?')[0])}" data-what="Link">Copy link</button>
          <span class="badge blue" style="align-self:center">${co.fact_count} cited facts from ${co.source_count} sources</span>
        </div>
      </section>
      <div class="layout">
        <nav class="toc" aria-label="Sections">${toc.map(([id, label, k]) => `<a href="#/company/${org}" data-target="${id}"><span>${esc(label)}</span>${k !== '' ? `<span class="n">${k}</span>` : ''}</a>`).join('')}</nav>
        <div>
          ${synthHtml(co)}
          <section class="panel" id="sec-key" aria-labelledby="h-key">
            <h2 id="h-key">Key figures</h2>
            ${kpis ? `<div class="kpis">${kpis}</div>` : '<p class="muted">No filed annual accounts were found for this company.</p>'}
          </section>
          <section class="panel" id="sec-found" aria-labelledby="h-found">
            <h2 id="h-found">What we found</h2>
            <p class="note">What the agent looked for, and whether it found it. Nothing here is guessed: unfound items are listed as not found.</p>
            <div class="cov">${covHtml}</div>
          </section>
          ${changesHtml(co)}
          ${secHtml}
        </div>
      </div>`;
    $$('[data-cmp]').forEach(syncCmpButton);
    spy();
    const fact = new URLSearchParams(qs).get('fact');
    if (fact && S.facts.has(fact)) {
      const b = $(`main [data-fact="${CSS.escape(fact)}"]`);
      if (b) b.scrollIntoView({ block: 'center' });
      openDrawer(fact, b);
    } else focusHeading();
  }
  function spy() {
    if (!('IntersectionObserver' in window)) return;
    const links = $$('.toc a');
    const io = new IntersectionObserver((es) => {
      for (const e of es) if (e.isIntersecting) {
        links.forEach((a) => a.classList.toggle('on', a.dataset.target === e.target.id));
        const on = $('.toc a.on');
        if (on && on.parentElement.scrollWidth > on.parentElement.clientWidth) on.parentElement.scrollLeft = on.offsetLeft - 20;
      }
    }, { rootMargin: '-120px 0px -65% 0px' });
    $$('main .panel[id]').forEach((p) => io.observe(p));
  }

  // ---------- views: compare ----------
  const ROWS = [
    ['Identity'],
    ['Legal form', 'legal_form'], ['Municipality', 'municipality'], ['Industry', 'industry'], ['Founded', 'founding_date'],
    ['Employees (registry)', 'employees_registered'], ['Share capital (NOK)', 'share_capital'], ['VAT registered', 'vat_registered'],
    ['Part of a group', 'in_group'],
    ['Key figures (latest filed year)'],
    ...METRICS.map((m) => [m, null, m]),
    ['Years with filed accounts', 'financial_history.years'],
    ['Online presence'],
    ['Website', 'official_website'], ['Social profiles', 'social.'], ['Hiring', '@Hiring'], ['News', '@News'],
    ['People'],
    ['Board and management', 'roles'],
  ];
  function cmpCell(co, row) {
    const [, field, metric] = row;
    if (metric) {
      const k = co.latest[metric];
      if (!k) return { html: '<span class="muted">No filed figure</span>', key: '', num: null };
      return { html: `<div class="cv"><span class="t"><b class="${k.value < 0 ? 'neg' : ''}">NOK ${fmtCompact(k.value)}</b><small>${esc(k.year)} · ${k.change_pct_vs_previous_year == null ? 'no prior-year %' : `${k.change_pct_vs_previous_year > 0 ? '▲ +' : k.change_pct_vs_previous_year < 0 ? '▼ ' : ''}${k.change_pct_vs_previous_year.toFixed(1)}%`}</small></span>${srcBtn(k.fact_id, `${co.name}: ${metric} ${k.year}`, true)}</div>`,
        key: `${k.value}|${k.year}`, num: k.value };
    }
    let fs;
    if (field.startsWith('@')) fs = co.facts.filter((f) => f.section === field.slice(1));
    else if (field.endsWith('.')) fs = co.facts.filter((f) => f.field.startsWith(field));
    else fs = co.facts.filter((f) => f.field === field);
    const ok = fs.filter((f) => f.avail === 'available');
    if (!ok.length) {
      const m = fs.find((f) => f.src);
      return { html: `<div class="cv"><span class="t muted">${esc(m ? (m.availText || 'Not found') : 'Not found')}</span>${m ? srcBtn(m.id, `${co.name}: ${row[0]}`, true) : ''}</div>`, key: '', num: null };
    }
    let text;
    if (field === 'roles') {
      const people = cleanRoles(ok[0].value);
      text = esc(people.slice(0, 3).join('; ')) + (people.length > 3 ? ` <small>+${people.length - 3} more</small>` : '');
      return { html: `<div class="cv"><span class="t">${text}</span>${srcBtn(ok[0].id, `${co.name}: ${row[0]}`, true)}</div>`, key: ok[0].value, num: null };
    }
    if (field.startsWith('@') || field.endsWith('.')) {
      return {
        html: ok.slice(0, 3).map((f) => `<div class="cv"><span class="t">${f.link ? `<a href="${esc(f.link)}" target="_blank" rel="noopener noreferrer">${esc(field === 'social.' ? f.label : f.value || f.label)}</a>` : esc(f.value)}</span>${srcBtn(f.id, `${co.name}: ${f.label}`, true)}</div>`).join('')
          + (ok.length > 3 ? `<small class="muted">+${ok.length - 3} more on the company page</small>` : ''),
        key: ok.map((f) => f.label).sort().join('|'), num: null,
      };
    }
    const f = ok[0];
    const val = field === 'municipality' ? tc(f.value) : f.value;
    const shown = f.link ? `<a href="${esc(f.link)}" target="_blank" rel="noopener noreferrer">${esc(val.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, ''))}</a>` : esc(val);
    return { html: `<div class="cv"><span class="t">${shown}</span>${srcBtn(f.id, `${co.name}: ${row[0]}`, true)}</div>`, key: String(f.value).toLowerCase(), num: null };
  }
  function adderHtml(ids) {
    if (ids.length >= MAX_CMP) return `<p class="muted">Comparing the maximum of ${MAX_CMP} companies. Remove one to add another.</p>`;
    return `<div class="adder"><label class="sr" for="add-q">Add a company to the comparison</label>
      <input id="add-q" type="search" autocomplete="off" placeholder="Add a company: name or org. number" aria-controls="add-list" aria-autocomplete="list">
      <ul id="add-list" hidden></ul></div>`;
  }
  function wireAdder(ids) {
    const inp = $('#add-q'), list = $('#add-list');
    if (!inp) return;
    const go = (org) => { location.hash = compareHref(ids.concat(org)); };
    inp.addEventListener('input', () => {
      const v = inp.value.trim();
      if (!v) { list.hidden = true; return; }
      const rows = search(v).sort((a, b) => b[1] - a[1] || b[0].fc - a[0].fc).map((r) => r[0]).filter((c) => !ids.includes(c.o)).slice(0, 6);
      list.innerHTML = rows.length ? rows.map((c) => `<li><button type="button" data-add="${c.o}"><span>${esc(c.n)}</span><small>${esc(tc(c.m))} · ${fmtOrg(c.o)}</small></button></li>`).join('')
        : '<li class="muted" style="padding:8px 10px">No match</li>';
      list.hidden = false;
    });
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { const b = $('#add-list button'); if (b) { e.preventDefault(); b.focus(); } }
      if (e.key === 'Enter') { const b = $('#add-list button'); if (b) go(b.dataset.add); }
    });
    list.addEventListener('click', (e) => { const b = e.target.closest('[data-add]'); if (b) go(b.dataset.add); });
    list.addEventListener('keydown', (e) => {
      const bs = $$('#add-list button'), i = bs.indexOf(document.activeElement);
      if (e.key === 'ArrowDown' && i < bs.length - 1) { e.preventDefault(); bs[i + 1].focus(); }
      if (e.key === 'ArrowUp') { e.preventDefault(); (i > 0 ? bs[i - 1] : inp).focus(); }
      if (e.key === 'Escape') { list.hidden = true; inp.focus(); }
    });
  }
  async function viewCompare(qs) {
    const p = new URLSearchParams(qs);
    let ids = (p.get('ids') || '').split(',').map((s) => s.replace(/\s/g, '')).filter((o) => S.byOrg.has(o));
    ids = [...new Set(ids)].slice(0, MAX_CMP);
    if (!p.get('ids') && S.compare.length) { history.replaceState(null, '', compareHref(S.compare)); ids = S.compare.slice(); }
    S.compare = ids.slice();
    saveCompare();
    document.title = 'Compare companies — Signalpost';
    const onlyDiff = p.get('diff') === '1';
    const head = `<div class="cmp-head"><div><h1 id="h-cmp" tabindex="-1">Compare companies</h1>
      <p class="muted" style="margin:4px 0 0">Up to ${MAX_CMP} side by side. Rows that differ are highlighted; every value has a source.</p></div>
      <div class="cmp-tools">${ids.length >= 2 ? `<button type="button" class="btn sm" id="only-diff" aria-pressed="${onlyDiff}">Only differences</button>
        <button type="button" class="btn sm" data-copy="${esc(location.href.split('#')[0] + compareHref(ids))}" data-what="Comparison link">Copy link</button>` : ''}</div></div>`;
    if (ids.length < 2) {
      main().innerHTML = head + `<div class="empty" style="text-align:left">
        <p><b>${ids.length ? `${esc(S.byOrg.get(ids[0]).n)} is selected.` : 'No companies selected yet.'}</b> Pick ${ids.length ? 'at least one more' : 'two to four'} companies: use <b>+ Compare</b> on the company list or a company page, or search here.</p>
        ${adderHtml(ids)}</div>`;
      wireAdder(ids);
      focusHeading();
      return;
    }
    main().innerHTML = head + '<div class="loading" role="status">Loading companies…</div>';
    let cos;
    try { cos = await Promise.all(ids.map(company)); } catch (e) { notFound(`Could not load the companies (${esc(e.message)}).`); return; }
    if (!S.route.startsWith('/compare')) return;
    const tagOf = (i) => `style="--c:${TAG_COLORS[i]}"`;
    const coCards = cos.map((co, i) => `<div class="cmp-co" ${tagOf(i)}>
      <button type="button" class="icon-btn x" data-rm="${co.org}" aria-label="Remove ${esc(co.name)} from comparison">${ICON.x}</button>
      <h2><span class="tag" aria-hidden="true">${TAGS[i]}</span><a href="#/company/${co.org}">${esc(co.name)}</a></h2>
      <div class="meta"><span class="mono">${fmtOrg(co.org)}</span> · ${esc(tc(co.municipality))}</div>
      <div class="meta">${co.fact_count} cited facts · ${co.source_count} sources</div></div>`).join('');
    let body = '', group = null, groupRows = [];
    const flush = () => {
      if (group && groupRows.length) body += `<tr class="grp"><th colspan="${cos.length + 1}" scope="colgroup">${esc(group)}</th></tr>` + groupRows.join('');
      groupRows = [];
    };
    for (const row of ROWS) {
      if (row.length === 1) { flush(); group = row[0]; continue; }
      const cells = cos.map((co) => cmpCell(co, row));
      const keys = cells.map((c) => c.key);
      const differs = new Set(keys).size > 1;
      if (onlyDiff && !differs) continue;
      if (!differs && keys.every((k) => !k) && !row[2]) continue; // nobody has it: hide noise
      const freq = new Map();
      keys.forEach((k) => freq.set(k, (freq.get(k) || 0) + 1));
      const [mode, modeN] = [...freq.entries()].sort((a, b) => b[1] - a[1])[0];
      const odd = (k) => differs && modeN > 1 && k !== mode;
      const nums = cells.map((c) => c.num).filter((v) => v != null);
      const max = nums.length ? Math.max(...nums.map(Math.abs)) : 0;
      groupRows.push(`<tr class="${differs ? 'differs' : ''}"><th scope="row">${esc(row[0])}${differs ? '<span class="dflag">Differs</span>' : ''}</th>${cells.map((c, i) => {
        const bar = c.num != null && max > 0 && nums.length > 1 ? `<div class="bar${c.num < 0 ? ' neg' : ''}" aria-hidden="true"><i style="width:${Math.max(2, Math.round(Math.abs(c.num) / max * 100))}%"></i></div>` : '';
        return `<td ${tagOf(i)} class="${odd(c.key) ? 'odd' : ''}" data-co="${TAGS[i]} · ${esc(cos[i].name)}">${c.html}${bar}</td>`;
      }).join('')}</tr>`);
    }
    flush();
    main().innerHTML = head + `
      <div class="cmp-cos" style="--n:${cos.length}">${coCards}</div>
      ${adderHtml(ids)}
      <p class="cmp-legend"><span class="sw" aria-hidden="true"></span>Rows marked “Differs” are not the same for all companies; shaded cells are the odd ones out. Bars show relative size within a row.</p>
      <table class="cmp" aria-labelledby="h-cmp">
        <thead><tr><th scope="col"><span class="sr">Field</span></th>${cos.map((co, i) => `<th scope="col" ${tagOf(i)}><span class="tag" aria-hidden="true">${TAGS[i]}</span>${esc(co.name)}</th>`).join('')}</tr></thead>
        <tbody>${body || `<tr><td colspan="${cos.length + 1}">These companies do not differ on any compared field.</td></tr>`}</tbody>
      </table>`;
    wireAdder(ids);
    const od = $('#only-diff');
    if (od) od.addEventListener('click', () => { location.hash = compareHref(ids) + (onlyDiff ? '' : '&diff=1'); });
    focusHeading();
  }

  // ---------- views: about ----------
  function viewAbout() {
    const st = S.stats;
    document.title = 'About the data — Signalpost';
    main().innerHTML = `<article class="prose">
      <h1 id="h-about" tabindex="-1">About the data</h1>
      <p>This viewer shows the output of the <b>Signalpost</b> research agent for <b>${n(st.companies)}</b> Norwegian companies: <b>${n(st.cited_facts_found)}</b> facts it found and cited, plus the items it looked for and did not find (${n(st.facts - st.cited_facts_found)}). Data retrieved ${esc(fmtDate(st.retrieved_from))}${st.retrieved_to !== st.retrieved_from ? ` – ${esc(fmtDate(st.retrieved_to))}` : ''}; site built ${esc(st.built_at)} from <code>${esc(st.origin)}</code>.</p>
      <h2>How to check a fact</h2>
      <ol>
        <li>Open a company and press <b>Source</b> next to any fact (or the link icon next to a number).</li>
        <li>The evidence panel shows the source and its type, a link to the original, when it was read, the period the figure covers, and the exact text the agent quoted.</li>
        <li>The <b>SHA-256</b> is a fingerprint of the content the agent read. Download the same source and run <code>shasum -a 256</code>: the same value means the source is unchanged; a different one means it changed after retrieval.</li>
      </ol>
      <h2>What is covered</h2>
      <ul>
        <li>Registry facts (name, form, address, industry, roles) from the Brønnøysund Register Centre — all ${n(st.companies)} companies.</li>
        <li>Filed annual accounts — ${n(st.with_accounts)} companies.</li>
        <li>Official websites confirmed to belong to the exact legal entity — ${n(st.with_website)}; social profiles — ${n(st.with_social)}; dated news — ${n(st.with_news)}; hiring — ${n(st.with_hiring)}.</li>
      </ul>
      <p>The agent publishes only facts it can tie to the exact organisation number; anything it could not confirm is shown as not found rather than guessed.</p>
      <h2>Data files</h2>
      <p><a href="data/index.json">data/index.json</a> (search index) · <code>data/c/&lt;org&gt;.json</code> (one file per company) · <a href="data/stats.json">data/stats.json</a>. Source code: <a href="https://github.com/DG10911/signalpost-agent">github.com/DG10911/signalpost-agent</a>.</p>
      <h2>Other viewer</h2>
      <p>The same data is also published on a JBox-built site: <a href="https://oxd0l7ewxjleaulg541cm79t.preview.jboxai.com/" target="_blank" rel="noopener noreferrer">oxd0l7ewxjleaulg541cm79t.preview.jboxai.com</a>.</p>
    </article>`;
    focusHeading();
  }
  function notFound(msg) {
    main().innerHTML = `<div class="empty"><h1 id="h-nf" tabindex="-1" style="font-size:20px">Not found</h1><p>${msg}</p><p><a href="#/">Back to all companies</a></p></div>`;
    focusHeading();
  }
  function focusHeading() {
    if (!S.focusOnRender) return;
    const h = $('main h1');
    if (h) h.focus({ preventScroll: true });
  }

  // ---------- evidence drawer ----------
  function drawerHtml(f) {
    const [kind, kindLabel] = f.src ? sourceKind(f.src.type, f.src.name, f.src.url) : ['other', 'No source'];
    const ok = f.avail === 'available';
    const val = f.section === 'Summary' ? (typeof f.raw.value === 'string' ? f.raw.value : JSON.stringify(f.raw.value))
      : f.section === 'Changes' ? JSON.stringify(f.raw.change) : (ok ? f.value : (f.availText || 'Not found'));
    const regLive = kind === 'registry' ? `https://virksomhet.brreg.no/nb/oppslag/enheter/${f.co.org}` : '';
    const bulk = f.src && /lastned/.test(f.src.url);
    const srcBlock = (s, quote, primary) => `
      <div class="ev-src"><span class="stype ${sourceKind(s.type, s.name, s.url)[0]}">${esc(sourceKind(s.type, s.name, s.url)[1])}</span><span class="ev-name">${esc(s.name)}</span></div>
      ${s.url ? `<a class="btn ${primary ? 'primary' : ''} ev-open" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${ICON.ext} Open original source${bulk && primary ? ' (bulk registry CSV, large)' : ''}</a>` : ''}
      <dl class="ev">
        <dt>Retrieved</dt><dd>${esc(fmtDate(s.retrieved) || 'Not recorded')}</dd>
        ${primary && f.period ? `<dt>Period</dt><dd>${esc(f.period)}</dd>` : ''}
        <dt>Source URL</dt><dd class="mono" style="font-size:12.5px">${esc(s.url || '—')}</dd>
        <dt>Source type</dt><dd><code class="mono">${esc(s.type || '—')}</code></dd>
      </dl>
      ${quote ? `<p class="muted" style="margin:14px 0 0;font-size:13px;font-weight:600">Quoted from the source</p><blockquote class="quote">${esc(quote)}</blockquote>` : ''}
      ${s.sha ? `<p class="muted" style="margin:10px 0 0;font-size:13px;font-weight:600">SHA-256 of the retrieved content</p>
        <div class="hash"><code>${esc(s.sha)}</code><button type="button" class="icon-btn" data-copy="${esc(s.sha)}" data-what="SHA-256" aria-label="Copy SHA-256 hash">${ICON.copy}</button></div>` : ''}`;
    return `
      <p class="ev-value">${f.link && ok ? `<a href="${esc(f.link)}" target="_blank" rel="noopener noreferrer">${esc(val)}</a>` : esc(val)}</p>
      <p style="margin:0"><span class="status ${ok ? 'ok' : 'no'}">${ok ? 'Found' : esc(f.availText || 'Not found')}</span>
        <span class="muted" style="font-size:13px">· ${esc(f.co.name)}</span></p>
      ${f.src ? srcBlock(f.src, f.quote, true) : `<p class="muted">No source was recorded for this item.</p>`}
      ${regLive ? `<p style="margin:10px 0 0"><a href="${regLive}" target="_blank" rel="noopener noreferrer">${ICON.ext} See this company's live entry in the Brønnøysund register</a></p>` : ''}
      ${f.more.length ? `<div class="ev-more"><h3>Also cited (${f.more.length})</h3>${f.more.map((m) => srcBlock(m.src, m.quote, false)).join('<hr style="border:0;border-top:1px solid var(--line);margin:14px 0">')}</div>` : ''}
      ${f.src && f.src.sha ? `<p class="ev-help">The hash fingerprints the exact content the agent read. Download the source and run <code class="mono">shasum -a 256</code>: a match proves it is unchanged.</p>` : ''}
      <p class="ev-help">Fact ID <span class="mono">${esc(f.id)}</span></p>`;
  }
  function factList() {
    return [...new Set($$('main [data-fact]').map((b) => b.dataset.fact))].filter((id) => S.facts.has(id));
  }
  function showFact(i) {
    const d = S.drawer;
    d.i = i;
    const f = S.facts.get(d.list[i]);
    $('#dr-kicker').textContent = `Evidence · ${f.co.name}`;
    $('#dr-title').textContent = f.label + (f.period && f.section === 'Finances' ? ` ${f.period.slice(0, 4)}` : '');
    $('#dr-body').innerHTML = drawerHtml(f);
    $('#dr-body').scrollTop = 0;
    $('#dr-pos').textContent = `Fact ${i + 1} of ${d.list.length}`;
    $('#dr-prev').disabled = i <= 0;
    $('#dr-next').disabled = i >= d.list.length - 1;
    if (S.route.startsWith('/company/')) history.replaceState(null, '', `#/company/${f.co.org}?fact=${encodeURIComponent(f.id)}`);
  }
  function openDrawer(id, opener) {
    const d = S.drawer;
    d.list = factList();
    if (!d.list.includes(id)) d.list.unshift(id);
    d.opener = opener || null;
    d.open = true;
    showFact(d.list.indexOf(id));
    $('#scrim').hidden = false;
    $('#drawer').hidden = false;
    for (const el of [main(), $('.top'), $('#tray')]) el.inert = true;
    renderTray();
    document.body.style.overflow = 'hidden';
    $('#dr-title').focus();
  }
  function closeDrawer() {
    const d = S.drawer;
    if (!d.open) return;
    d.open = false;
    $('#scrim').hidden = true;
    $('#drawer').hidden = true;
    for (const el of [main(), $('.top'), $('#tray')]) el.inert = false;
    document.body.style.overflow = '';
    renderTray();
    const id = d.list[d.i];
    if (S.route.startsWith('/company/')) history.replaceState(null, '', `#${S.route}`);
    const back = (d.opener && d.opener.isConnected) ? d.opener : $(`main [data-fact="${CSS.escape(id || '')}"]`);
    if (back) back.focus();
  }
  function trapTab(e) {
    const f = $$('#drawer button:not([disabled]), #drawer a[href], #drawer [tabindex="0"]').filter((el) => el.offsetParent !== null);
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === $('#dr-title'))) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  // ---------- router ----------
  function setNav(name) {
    $$('[data-nav]').forEach((a) => (a.dataset.nav === name ? a.setAttribute('aria-current', 'page') : a.removeAttribute('aria-current')));
  }
  async function render() {
    const h = location.hash.replace(/^#/, '') || '/';
    const [path, qs = ''] = h.split('?');
    const prev = S.route;
    if (S.drawer.open) closeDrawer();
    if (prev === '/' || prev === '') S.homeScroll = window.scrollY;
    S.route = path;
    renderTray();
    let m;
    if ((m = /^\/company\/(\d{9})$/.exec(path))) {
      setNav('');
      window.scrollTo(0, 0);
      await viewCompany(m[1], qs);
    } else if (path === '/compare') {
      setNav('compare');
      window.scrollTo(0, 0);
      await viewCompare(qs);
    } else if (path === '/about') {
      setNav('about');
      window.scrollTo(0, 0);
      viewAbout();
    } else {
      setNav('home');
      document.title = 'Signalpost — verified profiles of Norwegian companies';
      viewHome(qs);
      if (prev.startsWith('/company/') || prev === '/compare' || prev === '/about') {
        const shownNeeded = S.home.shown;
        if (shownNeeded) window.scrollTo(0, S.homeScroll);
        const a = prev.startsWith('/company/') && $(`#results a[href="#${prev}"]`);
        if (a) a.focus({ preventScroll: true }); else focusHeading();
      }
    }
    renderTray();
    S.focusOnRender = true;
  }

  // ---------- global events ----------
  function wire() {
    window.addEventListener('hashchange', render);
    document.addEventListener('click', (e) => {
      const t = e.target;
      let b;
      if ((b = t.closest('[data-fact]'))) { e.preventDefault(); openDrawer(b.dataset.fact, b); return; }
      if ((b = t.closest('[data-cmp]'))) { e.preventDefault(); toggleCompare(b.dataset.cmp); return; }
      if ((b = t.closest('[data-untray]'))) { const o = b.dataset.untray; S.compare = S.compare.filter((x) => x !== o); saveCompare(); const nx = $('#tray-list button') || $('#tray-go'); if (nx && !$('#tray').hidden) nx.focus(); return; }
      if ((b = t.closest('[data-rm]'))) { const ids = S.compare.filter((x) => x !== b.dataset.rm); location.hash = compareHref(ids); return; }
      if ((b = t.closest('[data-copy]'))) { copy(b.dataset.copy, b.dataset.what || 'Text'); return; }
      if ((b = t.closest('a[data-target]'))) {
        e.preventDefault();
        const el = b.dataset.target && document.getElementById(b.dataset.target);
        if (el) {
          el.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' });
          const h = el.querySelector('h2');
          if (h) { h.tabIndex = -1; h.focus({ preventScroll: true }); }
        }
        return;
      }
      if (t.closest('#scrim')) closeDrawer();
      if (!t.closest('.adder')) { const l = $('#add-list'); if (l) l.hidden = true; }
    });
    $('#tray-clear').addEventListener('click', () => { S.compare = []; saveCompare(); });
    $('#dr-close').addEventListener('click', closeDrawer);
    $('#dr-prev').addEventListener('click', () => { if (S.drawer.i > 0) showFact(S.drawer.i - 1); });
    $('#dr-next').addEventListener('click', () => { if (S.drawer.i < S.drawer.list.length - 1) showFact(S.drawer.i + 1); });
    document.addEventListener('keydown', (e) => {
      if (!S.drawer.open) {
        if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
          const q = $('#q') || $('#add-q');
          if (q) { e.preventDefault(); q.focus(); }
        }
        return;
      }
      if (e.key === 'Escape') { e.preventDefault(); closeDrawer(); }
      else if (e.key === 'Tab') trapTab(e);
      else if (e.key === 'ArrowRight' && !e.altKey) { e.preventDefault(); $('#dr-next').click(); }
      else if (e.key === 'ArrowLeft' && !e.altKey) { e.preventDefault(); $('#dr-prev').click(); }
    });
  }

  async function boot() {
    wire();
    try {
      const [idx, stats] = await Promise.all([getJSON('data/index.json'), getJSON('data/stats.json')]);
      S.index = prepIndex(idx);
      S.stats = stats;
    } catch (e) {
      main().innerHTML = `<div class="empty"><p><b>Could not load the data.</b> ${esc(e.message)}</p><p>If you opened this file directly from disk, serve the folder instead: <code>python3 -m http.server</code>.</p></div>`;
      return;
    }
    loadCompare();
    S.focusOnRender = false;
    await render();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
