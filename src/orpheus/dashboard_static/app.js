/* Cruscotto ORPHEUS (dashboard-v1): one page, no library, nothing from the internet.
   Every text coming from the database is written with textContent (never as HTML). */
(() => {
"use strict";

const TOKEN = document.body.dataset.token;
const viewRoot = document.getElementById("view");
let liveView = viewRoot;          /* a fresh box per route: a slow section never writes into the next one */
const cache = new Map();
const NS = "http://www.w3.org/2000/svg";
let journalColors = new Map();
let journalNames = new Map();


/* ------------------------------------------------------------------ language (IT / EN, en.js) */
const EN = self.ORPHEUS_EN || {};
const EN_TPL = (self.ORPHEUS_EN_TPL || []).map(([it, en]) => [new RegExp("^" + it.split("{}").map((x) => x.replace(/[.*+?^$()|[\]\\{}]/g, "\\$&")).join("(.+?)") + "$", "s"), en]);
let lang = "it";
try { lang = localStorage.getItem("orpheus-lingua") === "en" ? "en" : "it"; } catch (e) { lang = "it"; }
/* texts changed by the user ("Modifica i testi", file testi_cruscotto.json in the data folder): Italian only */
let IT_OVR = {}, IT_REV = new Map();
function setOverrides(d) { IT_OVR = d || {}; IT_REV = new Map(Object.entries(IT_OVR).map(([k, v]) => [v, k])); }
const hasOwn = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
function t(text, inner) {
  if (typeof text !== "string" || !text) return text;
  if (lang !== "en") {
    if (hasOwn(IT_OVR, text)) return IT_OVR[text];
    const c = text.trim();
    if (c !== text && hasOwn(IT_OVR, c)) return text.match(/^\s*/)[0] + IT_OVR[c] + text.match(/\s*$/)[0];
    return text;
  }
  if (Object.prototype.hasOwnProperty.call(EN, text)) return EN[text];
  const lead = text.match(/^\s*/)[0], trail = text.match(/\s*$/)[0], core = text.trim();
  if (core !== text && Object.prototype.hasOwnProperty.call(EN, core)) return lead + EN[core] + trail;
  if (inner) return text;
  for (const [rx, en] of EN_TPL) {
    const m = core.match(rx);
    if (m) { let i = 1; return lead + en.replace(/\{\}/g, () => t(m[i++], true)) + trail; }
  }
  for (const sep of ["; ", ": "]) {
    const i = core.indexOf(sep);
    if (i > 0) {
      const left = t(core.slice(0, i)), right = t(core.slice(i + sep.length));
      if (left !== core.slice(0, i) || (sep === "; " && right !== core.slice(i + sep.length))) return lead + left + sep + right + trail;
    }
  }
  return text;
}

/* ------------------------------------------------------------------ helpers */

function h(tag, props, ...kids) {
  const node = document.createElement(tag);
  setProps(node, props);
  append(node, kids);
  return node;
}
function s(tag, attrs, ...kids) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v !== undefined && v !== null) node.setAttribute(k, k === "aria-label" || k === "title" ? t(String(v)) : v);
  append(node, kids);
  return node;
}
function setProps(node, props) {
  if (!props) return;
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (k === "title" || k === "aria-label" || k === "placeholder") node.setAttribute(k, t(String(v)));
    else if (k === "dataset") Object.assign(node.dataset, v);
    else node.setAttribute(k, v === true ? "" : v);
  }
}
function append(node, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid === undefined || kid === null || kid === false) continue;
    if (kid instanceof Node) { node.append(kid); continue; }
    const tn = document.createTextNode(typeof kid === "string" ? t(kid) : String(kid));
    if (typeof kid === "string") tn.__src = kid;           // the original text, for "Modifica i testi"
    node.append(tn);
  }
}
function put(node, ...kids) { node.replaceChildren(); append(node, kids); return node; }
const fmtN = (n) => (n === null || n === undefined || n === "") ? "—" : (typeof n === "number" ? n.toLocaleString("it-IT") : String(n));
function fmtDate(iso, withTime = true) {
  if (!iso) return null;
  const d = new Date(String(iso).replace(" UTC", "Z").replace(" ", "T"));
  if (isNaN(d)) return String(iso);
  const opts = {day: "2-digit", month: "2-digit", year: "numeric"};
  if (withTime) Object.assign(opts, {hour: "2-digit", minute: "2-digit"});
  return d.toLocaleString("it-IT", opts);
}
function srcLine(o, extra) {
  const parts = [`Fonte: ${o && o.fonte ? o.fonte : "database di ORPHEUS"}.`];
  const when = o && fmtDate(o.data);
  parts.push(when ? `Dati aggiornati al ${when}.` : "Dati letti adesso.");
  if (extra) parts.push(extra);
  return h("p", {class: "src"}, parts.join(" "));
}
async function api(path, opts) {
  const res = await fetch(path, opts);
  let body = null;
  try { body = await res.json(); } catch (e) { body = null; }
  if (!res.ok) throw new Error((body && body.errore) || `errore ${res.status}`);
  return body;
}
async function get(path, fresh = false) {
  if (!fresh && cache.has(path)) return cache.get(path);
  const data = await api(path);
  cache.set(path, data);
  return data;
}
function clearCache() { cache.clear(); }
function colorFor(sigla) { return journalColors.get(sigla) || "var(--s-other)"; }
const PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#d9a400", "#d55181", "#7b5bd6", "#159eb5", "#a0522d", "#5d8a1f", "#c2185b",
  "#3f51b5", "#ef8f00"];
const COLORS_KEY = "orpheus-colori-riviste";
function setJournals(siglas, counts) {
  /* every journal has its own colour and keeps it (saved in this browser): the first time, the journals with more
     articles get the 12 most distinct colours, the others hues spread around the circle */
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(COLORS_KEY) || "{}") || {}; } catch (e) { saved = {}; }
  const n = counts || {};
  const order = [...new Set(siglas.filter(Boolean))].sort((a, b) => (n[b] || 0) - (n[a] || 0) || a.localeCompare(b));
  const used = new Set(Object.values(saved));
  let k = 0;
  const next = () => {
    for (;;) {
      const c = k < PALETTE.length ? PALETTE[k] : `hsl(${Math.round(((k - PALETTE.length) * 137.508 + 20) % 360)} 55% ${k % 2 ? 40 : 52}%)`;
      k++;
      if (!used.has(c)) { used.add(c); return c; }
    }
  };
  for (const sg of order) if (!saved[sg]) saved[sg] = next();
  journalColors = new Map(order.map((sg) => [sg, saved[sg]]));
  try { localStorage.setItem(COLORS_KEY, JSON.stringify(saved)); } catch (e) { /* not available */ }
}
function jname(sigla) { return journalNames.get(sigla) || null; }
/* full name + abbreviation, everywhere a journal is listed */
function jlabel(sigla) {
  if (!sigla) return "—";
  if (sigla === "altre") return t("altre riviste");
  const name = jname(sigla);
  return name && name !== sigla ? `${name} (${sigla})` : sigla;
}
function journalItems(siglas) {
  return [...new Set(siglas.filter(Boolean))].map((sg) => ({value: sg, label: jlabel(sg), color: colorFor(sg)}))
    .sort((a, b) => a.label.localeCompare(b.label));
}
function loadSet(key, all, fallback) {
  try {
    const saved = JSON.parse(localStorage.getItem(key) || "null");
    if (Array.isArray(saved)) return new Set(saved.filter((x) => all.includes(x)));
  } catch (e) { /* storage not available */ }
  return new Set(fallback());
}
function saveSet(key, set) { try { localStorage.setItem(key, JSON.stringify([...set])); } catch (e) { /* not available */ } }
function plural(n, one, many) { return `${fmtN(n)} ${t(n === 1 ? one : many)}`; }

/* drop-down list with multiple choice (riviste): one look in every section */
function multiPick({label, items, selected, onChange}) {
  const box = h("details", {class: "mpick"});
  const sum = h("summary", {class: "mpick-sum"});
  const list = h("div", {class: "mpick-list", role: "group", "aria-label": label});
  let q = "";
  const text = () => {
    const n = selected.size;
    if (!n) return t("Nessuna scelta");
    if (n === items.length) return `${t("Tutte")} (${fmtN(n)})`;
    if (n <= 2) return items.filter((i) => selected.has(i.value)).map((i) => i.value).join(", ");
    return `${fmtN(n)} ${t("scelte su")} ${fmtN(items.length)}`;
  };
  const changed = () => { put(sum, h("span", {class: "sr-only"}, `${label}: `), h("span", {class: "mpick-lab"}, text()), h("span", {class: "mpick-caret", "aria-hidden": "true"})); onChange(selected); };
  const render = () => {
    const qq = q.trim().toLowerCase();
    const shown = items.filter((i) => !qq || `${i.value} ${i.label}`.toLowerCase().includes(qq));
    put(list, shown.length ? shown.map((i) => {
      const cb = h("input", {type: "checkbox", checked: selected.has(i.value) || null,
        onchange: () => { if (cb.checked) selected.add(i.value); else selected.delete(i.value); changed(); }});
      return h("label", {class: "mpick-item"}, cb, i.color ? h("span", {class: "sw", style: `background:${i.color}`, "aria-hidden": "true"}) : null,
        h("span", null, i.label));
    }) : h("p", {class: "empty"}, "Nessuna rivista con questo nome."));
  };
  const all = (on) => { items.forEach((i) => (on ? selected.add(i.value) : selected.delete(i.value))); render(); changed(); };
  box.append(sum, h("div", {class: "mpick-pop"},
    h("div", {class: "mpick-tools"},
      items.length > 8 ? h("input", {type: "search", placeholder: "Cerca per nome o sigla", "aria-label": "Cerca una rivista",
        oninput: (e) => { q = e.target.value; render(); }}) : null,
      h("button", {type: "button", class: "btn small", onclick: () => all(true)}, "Tutte"),
      h("button", {type: "button", class: "btn small", onclick: () => all(false)}, "Nessuna")),
    list));
  put(sum, h("span", {class: "sr-only"}, `${label}: `), h("span", {class: "mpick-lab"}, text()), h("span", {class: "mpick-caret", "aria-hidden": "true"}));
  render();
  return box;
}
document.addEventListener("click", (e) => {
  for (const d of document.querySelectorAll("details.mpick[open]")) if (!d.contains(e.target)) d.open = false;
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  for (const d of document.querySelectorAll("details.mpick[open]")) { d.open = false; d.querySelector("summary").focus(); }
});
/* the abbreviation of a journal, with its full name shown after one second on the pointer (or on keyboard focus) */
function sgl(sigla, strong) {
  const name = jname(sigla);
  return h(strong ? "b" : "span", name ? {"data-jname": name} : null, sigla || "—");
}
function swatch(sigla) { return h("span", {class: "sw", style: `background:${colorFor(sigla)}`, "aria-hidden": "true"}); }
function tag(text, kind) { return h("span", {class: `tag ${kind || ""}`}, text); }
function yesNo(v) { return v === true ? h("span", {class: "yes"}, "sì") : v === false ? h("span", {class: "no"}, "no") : h("span", {class: "no"}, "—"); }
function level(l) {
  if (!l) return h("span", {class: "no"}, "—");
  return h("span", {class: "lvl", "data-l": l, title: `Livello della prova: ${l} (appraisal-v1)`},
    h("i", {"aria-hidden": "true"}, h("b"), h("b"), h("b")), l);
}
const VERDICT = {right: "giusta", wrong: "sbagliata"};
const FIELD = {
  disegno_studio: "Disegno dello studio", partecipanti_numero: "Partecipanti", partecipanti_sesso: "Sesso",
  partecipanti_eta: "Età", partecipanti_livello: "Livello dei partecipanti", partecipanti_sport: "Sport",
  intervento_esposizione: "Intervento o esposizione", confronto: "Confronto", misura_principale: "Misura principale",
  risultato_chiave: "Risultato chiave", limite: "Limite", conclusione_autori: "Conclusione degli autori",
};
function loading(text) { return h("p", {class: "loading"}, text || "Lettura del database…"); }
function errorBox(err) {
  const msg = String(err.message || err).replace(/\.$/, "");
  const disk = /disco esterno|database non trovato/.test(msg);
  return h("div", {class: "error", role: "alert"}, `Non riesco a leggere i dati: ${msg}. ` + (disk
    ? "Controlla che il disco esterno sia collegato, poi aggiorna la pagina."
    : "Aggiorna la pagina; se l'errore resta, chiudi e riapri il cruscotto."));
}
function head(title, lede, actions) {
  return [h("div", {class: "view-head"}, h("h1", {tabindex: "-1"}, title), actions || null),
          lede ? h("p", {class: "lede"}, lede) : null];
}
function refreshButton(onClick) {
  return h("button", {class: "btn", type: "button", onclick: onClick}, "Aggiorna i dati");
}
function seg(options, value, onChange, label) {
  const box = h("div", {class: "seg", role: "group", "aria-label": label});
  for (const [val, text] of options) {
    box.append(h("button", {type: "button", "aria-pressed": String(val === value),
      onclick: () => onChange(val)}, text));
  }
  return box;
}
function chip(text, pressed, onClick, sw) {
  const name = jname(text);
  return h("button", {type: "button", class: "chip", "aria-pressed": String(!!pressed), onclick: onClick,
    "data-jname": name, "aria-label": name ? `${text}, ${name}` : null}, sw || null, text);
}
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
function shortDoi(doi) { return doi ? doi.replace(/^https?:\/\/(dx\.)?doi\.org\//, "") : null; }
function authorLine(r) { return [r.autori ? r.autori.replace(" et al.", " e altri") : null, r.anno].filter(Boolean).join(", "); }

/* tooltip */
const tip = document.getElementById("tooltip");
function showTip(evt, nodes) {
  put(tip, ...nodes);
  tip.hidden = false;
  const pad = 14, w = tip.offsetWidth, hgt = tip.offsetHeight;
  let x = evt.clientX + pad, y = evt.clientY + pad;
  if (x + w > window.innerWidth - 8) x = evt.clientX - w - pad;
  if (y + hgt > window.innerHeight - 8) y = evt.clientY - hgt - pad;
  tip.style.left = `${Math.max(8, x)}px`;
  tip.style.top = `${Math.max(8, y)}px`;
}
function hideTip() { tip.hidden = true; }

/* ------------------------------------------------------------------ charts */

function niceMax(v) {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}
function stackedBars(holder, categories, series, opts) {
  const draw = () => {
    const W = Math.max(320, holder.clientWidth || 640), H = opts.height || 220;
    const m = {l: 34, r: 6, t: 8, b: 24};
    const totals = categories.map((_, i) => series.reduce((a, sr) => a + (sr.values[i] || 0), 0));
    const max = niceMax(Math.max(...totals, 1));
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const band = iw / categories.length, bw = Math.max(2, Math.min(28, band * 0.72));
    const y = (v) => m.t + ih - (v / max) * ih;
    const svg = s("svg", {class: "chart", viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img",
      "aria-label": opts.label});
    const grid = s("g", {class: "grid"}), axis = s("g", {class: "axis"});
    const ticks = 4;
    for (let k = 0; k <= ticks; k++) {
      const v = (max / ticks) * k, yy = y(v);
      grid.append(s("line", {x1: m.l, x2: W - m.r, y1: yy, y2: yy}));
      axis.append(s("text", {x: m.l - 6, y: yy + 4, "text-anchor": "end"}, fmtN(Math.round(v))));
    }
    svg.append(grid);
    const step = Math.max(1, Math.ceil(categories.length / Math.floor(iw / 44)));
    categories.forEach((c, i) => {
      const cx = m.l + band * i + band / 2;
      const lastI = categories.length - 1;        // the last label always, the regular ones not too close to it
      if (i === lastI || (i % step === 0 && lastI - i >= Math.ceil(step * 0.6))) axis.append(s("text", {x: cx, y: H - 6, "text-anchor": "middle"}, String(c)));
      let acc = 0;
      const parts = series.map((sr) => ({sr, v: sr.values[i] || 0})).filter((p) => p.v > 0);
      parts.forEach((p, j) => {
        const y0 = y(acc), y1 = y(acc + p.v);
        acc += p.v;
        const top = j === parts.length - 1;
        const gap = j > 0 ? 2 : 0;                  // 2px surface gap between stacked fills
        const hh = Math.max(1, y0 - y1 - gap);
        const x0 = cx - bw / 2, yy = y1;
        if (top) {
          const r = Math.min(4, bw / 2, hh);
          svg.append(s("path", {fill: p.sr.color, d: `M${x0},${yy + hh}V${yy + r}Q${x0},${yy} ${x0 + r},${yy}H${x0 + bw - r}Q${x0 + bw},${yy} ${x0 + bw},${yy + r}V${yy + hh}Z`}));
        } else {
          svg.append(s("rect", {x: x0, y: yy, width: bw, height: hh, fill: p.sr.color}));
        }
      });
      const hit = s("rect", {class: "hit", x: m.l + band * i, y: m.t, width: band, height: ih});
      hit.addEventListener("mousemove", (e) => showTip(e, [
        h("b", null, `${c}: ${fmtN(totals[i])} ${opts.unit}`),
        ...series.filter((sr) => sr.values[i]).map((sr) => h("div", {class: "row"},
          h("span", null, h("span", {class: "sw", style: `background:${sr.color}`}), sr.name), h("span", {class: "num"}, fmtN(sr.values[i])))),
      ]));
      hit.addEventListener("mouseleave", hideTip);
      svg.append(hit);
    });
    svg.append(axis);
    put(holder, svg);
  };
  draw();
  if (holder._ro) holder._ro.disconnect();
  holder._ro = new ResizeObserver(debounce(() => { if (document.contains(holder)) draw(); else holder._ro.disconnect(); }, 120));
  holder._ro.observe(holder);
}
function hbars(rows, total) {
  const box = h("div", {class: "hbars"});
  const max = Math.max(...rows.map((r) => r[1]), 1);
  for (const [label, n] of rows) {
    box.append(h("span", null, label),
      h("span", {class: "track", "aria-hidden": "true"}, h("i", {style: `width:${(n / max) * 100}%`})),
      h("span", {class: "num"}, fmtN(n) + (total ? ` (${Math.round((n / total) * 100)}%)` : "")));
  }
  return box;
}

/* ------------------------------------------------------------------ table with sorting */

function sortableTable(columns, rows, opts) {
  const state = opts.state;
  const table = h("table");
  const tr = h("tr");
  for (const col of columns) {
    const th = h("th", {class: col.num ? "n" : null, scope: "col"});
    if (col.sort) {
      th.setAttribute("aria-sort", state.sort === col.key ? (state.dir > 0 ? "ascending" : "descending") : "none");
      th.append(h("button", {type: "button", onclick: () => {
        state.dir = state.sort === col.key ? -state.dir : (col.num ? -1 : 1);
        state.sort = col.key;
        opts.rerender();
      }}, col.label));
    } else th.textContent = col.label;
    tr.append(th);
  }
  table.append(h("thead", null, tr));
  const tbody = h("tbody");
  for (const r of rows) {
    const row = h("tr", opts.onRow ? {class: "clickable", tabindex: "0",
      onclick: () => opts.onRow(r), onkeydown: (e) => { if (e.key === "Enter") opts.onRow(r); }} : null);
    for (const col of columns) row.append(h("td", {class: col.num ? "n" : null}, col.cell(r)));
    tbody.append(row);
  }
  table.append(tbody);
  return h("div", {class: "table-wrap"}, table);
}
function sortRows(rows, state, columns) {
  const col = columns.find((c) => c.key === state.sort);
  if (!col) return rows;
  const val = col.value || ((r) => r[col.key]);
  return [...rows].sort((a, b) => {
    const va = val(a), vb = val(b);
    if (va === vb) return 0;
    if (va === null || va === undefined) return 1;
    if (vb === null || vb === undefined) return -1;
    return (va > vb ? 1 : -1) * state.dir;
  });
}

/* ------------------------------------------------------------------ actions (buttons, 2026-10-17)
   The functions grouped in the order of use; each button runs the same command as its .cmd (one at a time). */
let actionsCache = null;
async function actionsInfo(fresh) {
  if (!actionsCache || fresh) actionsCache = await api("/api/azioni");
  return actionsCache;
}
function actionPanel(info, key, opts = {}) {
  const a = info.azioni.find((x) => x.azione === key);
  if (!a) return null;
  const title = opts.title || a.titolo;
  const box = h("section", {class: "block action", id: `act-${key}`, "aria-label": title});
  const stateLine = h("div", {class: "state-line"});
  const consoleBox = h("pre", {class: "console", hidden: true, "aria-label": `Avanzamento: ${a.titolo}`});
  const mine = info.avvio.azione === key && (info.avvio.in_corso || info.avvio.prossima);
  const busyOther = info.avvio.in_corso && info.avvio.azione !== key;
  const busyPc = info.in_corso_sul_pc && !info.avvio.in_corso && a.gruppo;
  const startBtn = h("button", {class: "btn primary", type: "button",
    disabled: (!info.avvio.disponibile || (info.avvio.in_corso && info.avvio.azione === key) || busyOther || busyPc) || null,
    onclick: () => {
      if (opts.check) { const msg = opts.check(); if (msg) { put(stateLine, h("span", {class: "error"}, msg)); return; } }
      confirmBox.hidden = false; startBtn.hidden = true; confirmBox.querySelector("button").focus();
    }}, a.titolo);
  const confirmBox = h("div", {class: "confirm", hidden: true},
    h("p", {style: "margin:0 0 10px"}, a.finestra ? "Si apre una finestra a parte che fa le domande e guida passo per passo. Non modifica il database."
      : a.scrive ? "Prima una copia di sicurezza del database, poi i passi qui sotto che hanno qualcosa da fare. Rilanciarlo è sicuro: salta quello che è già fatto. Non chiudere la finestra del cruscotto."
      : "Non modifica il database. Non chiudere la finestra del cruscotto finché non finisce."),
    h("button", {class: "btn primary", type: "button", onclick: async () => {
      confirmBox.hidden = true;
      try {
        const res = await api("/api/azioni/avvia", {method: "POST", headers: {"X-Orpheus-Token": TOKEN, "Content-Type": "application/json"},
          body: JSON.stringify({azione: key, ...(opts.extra ? opts.extra() : {})})});
        actionsCache = null;
        if (res && res.finestra) { startBtn.hidden = false; put(stateLine, tag(res.messaggio, "ok")); return; }
        consoleNext = 0; consoleBox.replaceChildren(); consoleBox.hidden = false; startBtn.hidden = false; startBtn.disabled = true;
        follow(consoleBox, stateLine, startBtn);
      } catch (e) { startBtn.hidden = false; put(stateLine, h("span", {class: "error"}, e.message)); }
    }}, "Sì, avvia"), " ",
    h("button", {class: "btn", type: "button", onclick: () => { confirmBox.hidden = true; startBtn.hidden = false; startBtn.focus(); }}, "Annulla"));
  const steps = h("ol", {class: "action-steps"}, a.passi.map((x) => h("li", null, x.titolo)));
  let why = null;
  if (!info.avvio.disponibile) why = "Per avviare da qui apri il cruscotto con cruscotto.cmd (in C:\\ORPHEUS).";
  else if (busyOther) why = `Adesso è in corso: ${info.avvio.titolo}. Aspetta che finisca.`;
  else if (busyPc) why = `lavora è già in corso in un'altra finestra (dal ${fmtDate(info.in_corso_sul_pc.iniziato)}).`;
  append(box, [h("div", {class: "block-head"}, h("h2", null, title), stateLine),
    h("p", {class: "muted"}, opts.desc || a.descrizione),
    opts.controls || null,
    h("div", {class: "action-row"}, startBtn, h("details", {class: "action-more"}, h("summary", null, `${a.passi.length} passi, in ordine`), steps)),
    why ? h("p", {class: "muted"}, why) : null, confirmBox, consoleBox,
    h("p", {class: "src"}, `Come il doppio clic su ${a.comando}: registro in C:\\ORPHEUS\\registri, inviato anche a Claude.`)]);
  if (mine) { consoleBox.hidden = false; consoleNext = 0; follow(consoleBox, stateLine, startBtn); }
  return box;
}
async function actionBlock(key, opts) {
  try { return actionPanel(await actionsInfo(true), key, opts); } catch (e) { return null; }
}

/* ------------------------------------------------------------------ sections */

const sections = {};

const YEARS_KEY = "orpheus-panoramica-riviste";
sections.panoramica = async () => {
  const view = liveView;
  const d = await get("/api/panoramica");
  const out = [...head("Panoramica", null, refreshButton(() => { clearCache(); route(); }))];

  /* 1. the two numbers that matter: articles in ORPHEUS and journals covered */
  const art = d.kpi.find((k) => k.id === "articoli") || {};
  const cov = d.copertura.righe;
  const listed = cov.filter((r) => journalNames.has(r.rivista));
  const covered = listed.filter((r) => r.articoli > 0).length;
  const stat = (value, label, note, sec) => h("button", {type: "button", class: "stat", onclick: () => go(sec)},
    h("span", {class: "stat-v"}, fmtN(value)), h("span", {class: "stat-l"}, label), note ? h("span", {class: "stat-n"}, note) : null);
  out.push(h("section", {class: "block stats", "aria-label": "Numeri principali", "data-block": "Numeri principali"},
    stat(art.valore, "Articoli", art.nota || null, "articoli"),
    stat(covered, "Riviste", `con almeno un articolo, su ${fmtN(listed.length)} nell'elenco`, "riviste")));

  /* 2. articles by year of publication, only for the journals chosen */
  const byYear = d.per_anno.righe;
  const totals = new Map();
  for (const r of byYear) totals.set(r.rivista, (totals.get(r.rivista) || 0) + r.n);
  const names = [...totals.keys()];
  const top = () => names.filter((n) => n !== "altre").sort((a, b) => totals.get(b) - totals.get(a)).slice(0, 5);
  const pick = loadSet(YEARS_KEY, names, top);
  const years = [];
  if (byYear.length) {
    const yMin = Math.min(...byYear.map((r) => r.anno)), yMax = Math.max(...byYear.map((r) => r.anno));
    for (let y = yMin; y <= yMax; y++) years.push(y);
  }
  const value = (n, y) => (byYear.find((r) => r.anno === y && r.rivista === n) || {}).n || 0;
  const yearsHolder = h("div"), legendBox = h("ul", {class: "legend"}), tableBox = h("div", {class: "table-wrap"});
  const drawYears = () => {
    const shown = names.filter((n) => pick.has(n)).sort((a, b) => totals.get(b) - totals.get(a));
    const series = shown.map((n) => ({name: jlabel(n), color: colorFor(n), values: years.map((y) => value(n, y))}));
    put(legendBox, shown.map((n) => h("li", null, swatch(n), `${jlabel(n)} · ${fmtN(totals.get(n))}`)));
    put(tableBox, h("table", null, h("thead", null, h("tr", null, h("th", {scope: "col"}, "Anno"),
      shown.map((n) => h("th", {class: "n", scope: "col", title: jlabel(n)}, n === "altre" ? t("altre") : n)))),
      h("tbody", null, years.filter((y, i) => series.some((sr) => sr.values[i])).map((y) => h("tr", null, h("td", null, String(y)),
        series.map((sr) => h("td", {class: "n"}, fmtN(sr.values[years.indexOf(y)]))))))));
    if (!shown.length || !years.length) {
      if (yearsHolder._ro) yearsHolder._ro.disconnect();
      put(yearsHolder, h("p", {class: "empty"}, years.length ? "Scegli almeno una rivista." : "Nessun articolo con l'anno di pubblicazione."));
    } else stackedBars(yearsHolder, years, series, {label: "Articoli per anno e rivista", unit: "articoli", height: 230});
  };
  out.push(h("section", {class: "block", "aria-labelledby": "h-years"},
    h("div", {class: "block-head"}, h("h2", {id: "h-years"}, "Articoli e riviste"),
      multiPick({label: "Riviste nel grafico", items: names.map((n) => ({value: n, label: jlabel(n), color: colorFor(n)}))
        .sort((a, b) => (a.value === "altre") - (b.value === "altre") || a.label.localeCompare(b.label)),
        selected: pick, onChange: () => { saveSet(YEARS_KEY, pick); drawYears(); }})),
    h("p", {class: "muted"}, "Articoli per anno di pubblicazione: un colore per rivista. Scegli le riviste dall'elenco."),
    legendBox, yearsHolder,
    h("details", null, h("summary", null, "Mostra come tabella"), tableBox),
    srcLine(d.per_anno)));

  /* 3. coverage by journal: full name and abbreviation, one colour each */
  const pct = (a, b) => b ? Math.round((a / b) * 100) : 0;
  const covCols = [
    {key: "rivista", label: "Rivista", sort: true, value: (r) => jlabel(r.rivista).toLowerCase(),
      cell: (r) => h("span", {class: "jcell"}, swatch(r.rivista), h("span", null, jlabel(r.rivista)))},
    {key: "articoli", label: "Articoli", num: true, sort: true, cell: (r) => fmtN(r.articoli)},
    {key: "articoli_completi", label: "Completi", num: true, sort: true, cell: (r) => fmtN(r.articoli_completi)},
    {key: "doi_confermato", label: "DOI confermato", num: true, sort: true, cell: (r) => fmtN(r.doi_confermato)},
    {key: "pubmed", label: "PubMed", num: true, sort: true, cell: (r) => fmtN(r.pubmed)},
    {key: "testo_aperto", label: "Testo aperto", num: true, sort: true, cell: (r) => fmtN(r.testo_aperto)},
    {key: "citazioni_verificate", label: "Riferimenti verificati", num: true, sort: true,
      value: (r) => pct(r.citazioni_verificate, r.citazioni),
      cell: (r) => h("span", {class: "bar-cell"}, `${fmtN(r.citazioni_verificate)} su ${fmtN(r.citazioni)}`,
        h("span", {class: "mini", "aria-hidden": "true"}, h("i", {style: `width:${pct(r.citazioni_verificate, r.citazioni)}%`})))},
    {key: "scheda", label: "Schede", num: true, sort: true, cell: (r) => fmtN(r.scheda)},
  ];
  const covState = {sort: "articoli", dir: -1};
  const covHolder = h("div");
  const renderCov = () => put(covHolder, sortableTable(covCols, sortRows(cov, covState, covCols), {state: covState, rerender: renderCov}));
  renderCov();
  out.push(h("section", {class: "block", "aria-labelledby": "h-cov"},
    h("div", {class: "block-head"}, h("h2", {id: "h-cov"}, "Copertura per rivista")), covHolder, srcLine(d.copertura)));
  put(view, ...out);
  drawYears();
};

/* articles */
const artState = {q: "", riviste: new Set(), da: null, a: null, flags: new Set(), sort: "anno", dir: -1, limit: 60};
const FLAGS = [["completo", "Completi"], ["scheda", "Con scheda"], ["affermazioni", "Con affermazioni"],
  ["pubmed", "In PubMed"], ["testo_aperto", "Testo aperto"]];
sections.articoli = async () => {
  const view = liveView;
  const d = await get("/api/articoli");
  const rows = d.righe;
  const siglas = [...new Set(rows.map((r) => r.rivista).filter(Boolean))].sort();
  const holder = h("div");
  const cols = [
    {key: "titolo", label: "Articolo", sort: true, value: (r) => (r.titolo || "").toLowerCase(),
      cell: (r) => [h("div", {class: "t-title"}, r.titolo || "(senza titolo)"), h("div", {class: "t-sub"}, authorLine(r))]},
    {key: "rivista", label: "Rivista", sort: true, cell: (r) => r.rivista ? h("span", {class: "jcell"}, swatch(r.rivista), sgl(r.rivista)) : "—"},
    {key: "anno", label: "Anno", num: true, sort: true, cell: (r) => r.anno || "—"},
    {key: "completo", label: "Completo", sort: true, cell: (r) => yesNo(r.completo)},
    {key: "pubmed", label: "PubMed", sort: true, cell: (r) => yesNo(r.pubmed)},
    {key: "riferimenti", label: "Riferimenti", num: true, sort: true,
      cell: (r) => r.riferimenti ? `${fmtN(r.riferimenti_verificati)} su ${fmtN(r.riferimenti)}` : "—"},
    {key: "scheda", label: "Scheda", sort: true, cell: (r) => yesNo(r.scheda)},
    {key: "affermazioni", label: "Consenso", num: true, sort: true, cell: (r) => r.affermazioni ? fmtN(r.affermazioni) : "—"},
    {key: "livello", label: "Livello", sort: true, value: (r) => ({alto: 3, medio: 2, basso: 1})[r.livello] || 0,
      cell: (r) => level(r.livello)},
  ];
  const filtered = () => {
    const q = artState.q.trim().toLowerCase();
    return rows.filter((r) => {
      if (q && !`${r.titolo} ${r.doi} ${r.autori || ""} ${r.pmid || ""}`.toLowerCase().includes(q)) return false;
      if (artState.riviste.size && !artState.riviste.has(r.rivista)) return false;
      if (artState.da && (r.anno || 0) < artState.da) return false;
      if (artState.a && (r.anno || 9999) > artState.a) return false;
      for (const f of artState.flags) {
        if (!r[f]) return false;
      }
      return true;
    });
  };
  const render = () => {
    const list = sortRows(filtered(), artState, cols);
    put(holder, 
      h("p", {class: "count-line"}, `${fmtN(list.length)} articoli su ${fmtN(rows.length)}`),
      list.length ? sortableTable(cols, list.slice(0, artState.limit), {state: artState, rerender: render, onRow: (r) => openArticle(r.id)})
        : h("p", {class: "empty"}, "Nessun articolo con questi filtri. Togli un filtro per vederne di più."),
      list.length > artState.limit ? h("button", {type: "button", class: "btn more", onclick: () => { artState.limit += 60; render(); }},
        `Mostra altri ${Math.min(60, list.length - artState.limit)}`) : null);
  };
  const filters = h("div", {class: "filters"},
    h("label", {class: "field"}, "Cerca nel titolo, DOI, autori, PMID",
      h("input", {type: "search", value: artState.q, placeholder: "es. hamstring, 10.1111/sms…",
        oninput: debounce((e) => { artState.q = e.target.value; artState.limit = 60; render(); }, 150)})),
    h("div", {class: "field"}, "Riviste", multiPick({label: "Riviste", items: journalItems(siglas), selected: artState.riviste,
      onChange: () => { artState.limit = 60; render(); }})),
    h("label", {class: "field"}, "Dal", h("input", {type: "number", value: artState.da || "", min: 1900, max: 2100, placeholder: "anno",
      oninput: debounce((e) => { artState.da = +e.target.value || null; render(); }, 250)})),
    h("label", {class: "field"}, "Al", h("input", {type: "number", value: artState.a || "", min: 1900, max: 2100, placeholder: "anno",
      oninput: debounce((e) => { artState.a = +e.target.value || null; render(); }, 250)})),
    h("div", {class: "field"}, "Mostra solo", h("div", {class: "checks"}, FLAGS.map(([k, label]) => {
      const b = chip(label, artState.flags.has(k), () => {
        artState.flags.has(k) ? artState.flags.delete(k) : artState.flags.add(k);
        b.setAttribute("aria-pressed", String(artState.flags.has(k))); artState.limit = 60; render();
      });
      return b;
    }))));
  put(view, ...head("Articoli", null, refreshButton(() => { clearCache(); route(); })),
    h("section", {class: "block", "data-block": "Elenco degli articoli"}, filters, holder), srcLine(d));
  render();
};

/* article cards (2026-10-17 19:13): packets by priority; Claude in session is the main route, the free chat the emergency one */
function cardsBlocks(info) {
  const mode = h("select", {"aria-label": "Priorità dei pacchetti"},
    h("option", {value: "rivista"}, "Una rivista"), h("option", {value: "equo"}, "Equo: lo stesso numero per rivista"),
    h("option", {value: "casuale", selected: true}, "Casuale: come il sorteggio"));
  const journal = h("select", {"aria-label": "Rivista"}, journalItems(info.riviste || []).map((j) => h("option", {value: j.value}, j.label)));
  const journalField = h("label", {class: "field", hidden: true}, "Rivista", journal);
  mode.addEventListener("change", () => { journalField.hidden = mode.value !== "rivista"; });
  const howMany = h("input", {type: "number", min: 1, max: 100, value: 10, "aria-label": "Quanti pacchetti nuovi"});
  const packets = actionPanel(info, "pacchetti", {
    controls: h("div", {class: "filters"}, h("label", {class: "field"}, "Priorità", mode), journalField,
      h("label", {class: "field"}, "Quanti pacchetti nuovi", howMany)),
    check: () => (mode.value === "rivista" && !journal.value ? "Scegli la rivista." :
      !(+howMany.value >= 1 && +howMany.value <= 100) ? "Quanti pacchetti: da 1 a 100." : null),
    extra: () => ({modo: mode.value, rivista: mode.value === "rivista" ? journal.value : "", quanti: +howMany.value})});
  const c = info.schede_chat;
  const chatLine = c ? h("p", {class: "muted"}, `Chat gratuita: ${fmtN(c.preparati)} articoli preparati; accettate da inviare ${fmtN(c.da_inviare)}, già nel database ${fmtN(c.inviate)}, rifiutate ${fmtN(c.rifiutate)}, in attesa della risposta ${fmtN(c.in_attesa + c.da_leggere)}.`) : null;
  const send = actionPanel(info, "invia_schede_chat", {controls: chatLine});
  const chat = actionPanel(info, "chat_schede");
  return [h("p", {class: "muted group-note"}, "Strada principale: Claude scrive le schede in sessione dai pacchetti, poi «Invia il lavoro di Claude» (sezione Consenso). Emergenza, per pochi articoli o quando Claude non può lavorare: una chat gratuita, poi «Invia le schede della chat»."),
    packets, chat, send].filter(Boolean);
}

/* claims */
const clState = {q: "", livello: "tutti", tipo: "tutti", controllo: "tutte", riviste: new Set(), limit: 40};
sections.affermazioni = async () => {
  const view = liveView;
  const d = await get("/api/affermazioni");
  const rows = d.righe;
  const siglas = [...new Set(rows.map((r) => r.rivista).filter(Boolean))].sort();
  const holder = h("div");
  const filtered = () => {
    const q = clState.q.trim().toLowerCase();
    return rows.filter((r) => (clState.livello === "tutti" || r.livello === clState.livello)
      && (clState.tipo === "tutti" || r.tipo === clState.tipo)
      && (clState.controllo === "tutte" || r.controllo === clState.controllo)
      && (!clState.riviste.size || clState.riviste.has(r.rivista))
      && (!q || `${r.testo} ${r.articolo} ${r.evidenze.map((e) => e.citazione).join(" ")}`.toLowerCase().includes(q)));
  };
  const render = () => {
    const list = filtered();
    const ul = h("ul", {class: "claims"});
    for (const r of list.slice(0, clState.limit)) ul.append(claimItem(r, true));
    put(holder, h("p", {class: "count-line"}, `${fmtN(list.length)} affermazioni su ${fmtN(rows.length)}`),
      list.length ? h("div", {class: "block"}, ul) : h("p", {class: "empty"}, "Nessuna affermazione con questi filtri."),
      list.length > clState.limit ? h("button", {type: "button", class: "btn more", onclick: () => { clState.limit += 40; render(); }},
        `Mostra altre ${Math.min(40, list.length - clState.limit)}`) : null);
  };
  const rerenderFilters = () => { put(filtersBox, ...filterControls()); render(); };
  const set = (k) => (v) => { clState[k] = v; clState.limit = 40; rerenderFilters(); };
  const filterControls = () => [
    h("label", {class: "field"}, "Cerca nelle affermazioni e nelle frasi",
      h("input", {type: "search", value: clState.q, placeholder: "es. running economy, injury",
        oninput: debounce((e) => { clState.q = e.target.value; clState.limit = 40; render(); }, 150)})),
    h("div", {class: "field"}, "Livello della prova", seg([["tutti", "Tutti"], ["alto", "Alto"], ["medio", "Medio"], ["basso", "Basso"]], clState.livello, set("livello"), "Livello")),
    h("div", {class: "field"}, "Tipo", seg([["tutti", "Tutti"], ["risultato", "Risultati"], ["conclusione", "Conclusioni"], ["limite", "Limiti"]], clState.tipo, set("tipo"), "Tipo")),
    h("div", {class: "field"}, "Controllo a mano", seg([["tutte", "Tutte"], ["giusta", "Giuste"], ["sbagliata", "Sbagliate"], ["da controllare", "Da controllare"]], clState.controllo, set("controllo"), "Controllo")),
    h("div", {class: "field"}, "Rivista", h("div", {class: "checks"}, siglas.map((sg) => chip(sg, clState.riviste.has(sg), () => {
      clState.riviste.has(sg) ? clState.riviste.delete(sg) : clState.riviste.add(sg); rerenderFilters();
    }, swatch(sg))))),
  ];
  const filtersBox = h("div", {class: "filters"}, ...filterControls());
  put(view, ...head("Consenso", "Le affermazioni degli articoli, in inglese come gli articoli. Ognuna è legata alla pagina e alla frase del PDF da cui viene; il livello della prova segue regole fisse (disegno dello studio, partecipanti, gruppo di confronto).",
    refreshButton(() => { clearCache(); route(); })), await actionBlock("invia_lavoro_claude"), filtersBox, holder, srcLine(d));
  render();
};
function claimItem(r, withArticle) {
  const ev = r.evidenze || [];
  const lvl = r.livello || (ev[0] && ev[0].livello);
  const verdict = r.controllo && VERDICT[r.controllo] ? VERDICT[r.controllo] : r.controllo;
  return h("li", {class: "claim", "data-l": lvl || ""},
    h("p", {class: "claim-text", lang: "en"}, r.testo),
    h("div", {class: "claim-meta"},
      level(lvl), tag(r.tipo),
      verdict ? tag(verdict === "giusta" ? "controllata: giusta" : verdict === "sbagliata" ? "controllata: sbagliata" : "da controllare",
        verdict === "giusta" ? "good" : verdict === "sbagliata" ? "crit" : "") : null,
      withArticle ? h("button", {type: "button", class: "linklike", onclick: () => openArticle(r.article_id)},
        `${r.articolo} (${[r.rivista, r.anno].filter(Boolean).join(", ")})`) : null),
    ev.length ? h("details", null, h("summary", null, ev.length > 1 ? `Mostra le ${ev.length} frasi del PDF` : `Mostra la frase del PDF (pagina ${ev[0].pagina})`),
      ev.map((e) => h("blockquote", {class: "q", lang: "en"}, e.citazione,
        h("cite", null, `Pagina ${e.pagina} del PDF; ${e.sostegno === "supports" ? "a sostegno" : e.sostegno}`)))) : null);
}

/* search */
const srState = {q: "", modo: "ibrida", result: null};
const EXAMPLES = ["ACL injuries in female football players", "running economy with minimalist shoes",
  "bowling workload and lumbar stress injuries in young cricketers", "hamstring strain and sprint mechanics"];
sections.ricerca = async () => {
  const view = liveView;
  const input = h("input", {type: "search", value: srState.q, "aria-label": "Domanda", placeholder: "Write a question or a statement in English", lang: "en"});
  const results = h("div", {"aria-live": "polite"});
  const run = async (q) => {
    srState.q = q.trim();
    if (!srState.q) return;
    input.value = srState.q;
    put(results, loading("Cerco nei pezzi di testo…"));
    try {
      srState.result = await api(`/api/ricerca?q=${encodeURIComponent(srState.q)}&modo=${srState.modo}`);
      renderResults();
    } catch (e) { put(results, errorBox(e)); }
  };
  const renderResults = () => {
    const r = srState.result;
    if (!r) { results.replaceChildren(); return; }
    const ol = h("ol", {class: "hits"});
    r.risultati.forEach((x, i) => {
      ol.append(h("li", {class: "hit"},
        h("div", null, h("span", {class: "hit-rank"}, String(i + 1)),
          h("button", {type: "button", class: "linklike t-title", onclick: () => x.article_id && openArticle(x.article_id)}, x.titolo || "(senza titolo)")),
        h("div", {class: "t-sub"}, [x.anno, `pagina ${x.pagine}`, x.sezione ? `sezione ${x.sezione}` : null,
          x.altri_passaggi ? `altri ${x.altri_passaggi} passaggi` : null].filter(Boolean).join(", ")),
        h("p", {class: "hit-pass", lang: "en"}, x.passaggio + "…"),
        x.affermazioni.length ? h("ul", {class: "hit-claims", lang: "en"}, x.affermazioni.map((c) => h("li", null, c.testo))) : null));
    });
    put(results, 
      r.nota ? h("p", {class: "notice"}, r.nota) : null,
      h("p", {class: "count-line"}, `${r.risultati.length} articoli trovati (${r.modo === "ibrida" ? "parole e significato" : r.modo}), i più pertinenti prima`),
      r.risultati.length ? h("div", {class: "block"}, ol) : h("p", {class: "empty"}, "Nessun articolo trovato. Prova con altre parole in inglese."),
      srcLine(r, `Regole ${r.regole}.`));
  };
  const modeBox = h("div");
  const renderMode = () => put(modeBox, seg([["ibrida", "Parole e significato"], ["parole", "Solo parole"], ["significato", "Solo significato"]],
    srState.modo, (v) => { srState.modo = v; renderMode(); if (srState.q) run(srState.q); }, "Modo di ricerca"));
  renderMode();
  const form = h("form", {class: "searchbar", onsubmit: (e) => { e.preventDefault(); run(input.value); }},
    input, h("button", {class: "btn primary", type: "submit"}, "Cerca"));
  put(view, ...head("Ricerca", "Cerca negli articoli completi per parole e per significato. Scrivi la domanda in inglese, come gli articoli: i risultati e le affermazioni sono in inglese."),
    form, modeBox, h("div", {class: "examples"}, h("span", {class: "muted"}, "Prova:"), EXAMPLES.map((q) => chip(q, false, () => run(q)))),
    results);
  renderResults();
  input.focus();
};

/* journals */
const RULES = [["tipo", "Tipo di articolo"], ["parole_testo_max", "Parole del testo (massimo)"], ["abstract_parole_max", "Parole dell'abstract (massimo)"],
  ["abstract_struttura", "Struttura dell'abstract"], ["riferimenti_max", "Riferimenti (massimo)"], ["figure_tabelle_max", "Figure e tabelle (massimo)"],
  ["preprint", "Preprint"], ["politica_ia", "Uso dell'intelligenza artificiale"], ["revisione", "Revisione"], ["orcid", "ORCID"], ["dati", "Dati"]];
sections.riviste = async () => {
  const view = liveView;
  const d = await get("/api/riviste");
  const maxSjr = Math.max(...d.riviste.map((r) => (r.metriche && r.metriche.sjr) || 0), 1);
  const cols = [
    {key: "sigla", label: "Rivista", sort: true, value: (r) => jlabel(r.sigla).toLowerCase(),
      cell: (r) => [h("div", {class: "jcell"}, swatch(r.sigla), h("b", null, jlabel(r.sigla))), r.editore ? h("div", {class: "t-sub"}, r.editore) : null]},
    {key: "sjr", label: "SJR", num: true, sort: true, value: (r) => r.metriche && r.metriche.sjr,
      cell: (r) => r.metriche && r.metriche.sjr != null ? h("span", {class: "bar-cell"}, fmtN(r.metriche.sjr),
        h("span", {class: "mini", "aria-hidden": "true"}, h("i", {style: `width:${(r.metriche.sjr / maxSjr) * 100}%`}))) : "—"},
    {key: "q", label: "Quartile", sort: true, value: (r) => r.metriche && r.metriche.quartile, cell: (r) => (r.metriche && r.metriche.quartile) || "—"},
    {key: "h", label: "h-index", num: true, sort: true, value: (r) => r.metriche && r.metriche.h_index_scimago,
      cell: (r) => r.metriche ? `${fmtN(r.metriche.h_index_scimago)}, ${fmtN(r.metriche.h_index_openalex)}` : "—"},
    {key: "art", label: "Articoli in ORPHEUS", num: true, sort: true, value: (r) => r.copertura && r.copertura.articoli,
      cell: (r) => r.copertura ? `${fmtN(r.copertura.articoli)} (${fmtN(r.copertura.articoli_completi)} completi)` : "—"},
    {key: "lg", label: "Linee guida", sort: true, value: (r) => latestGuide(r) && latestGuide(r).data,
      cell: (r) => { const x = latestGuide(r); return x ? [fmtDate(x.data, false),
        x.indirizzo ? [" ", h("a", {href: x.indirizzo, target: "_blank", rel: "noopener noreferrer"}, "pagina")] : null] : "—"; }},
  ];
  const all = d.riviste.map((r) => r.sigla).filter(Boolean).sort();
  const pick = loadPick(all, d.riviste);
  const st = {sort: "sjr", dir: -1};
  const holder = h("div"), confHolder = h("div");
  const renderTables = () => {
    const rows = d.riviste.filter((r) => pick.has(r.sigla));
    put(holder, rows.length ? sortableTable(cols, sortRows(rows, st, cols), {state: st, rerender: renderTables})
      : h("p", {class: "empty"}, "Scegli almeno una rivista qui sopra."));
    const conf = d.confronto.righe.filter((c) => pick.has(c.rivista));
    put(confHolder, conf.length ? h("div", {class: "table-wrap"}, h("table", null,
      h("thead", null, h("tr", null, h("th", {scope: "col"}, "Regola"), conf.map((c) => h("th", {scope: "col"}, swatch(c.rivista), " ", sgl(c.rivista))))),
      h("tbody", null, RULES.map(([k, label]) => h("tr", null, h("th", {scope: "row"}, label), conf.map((c) => h("td", null, c[k] || "—")))))))
      : h("p", {class: "empty"}, d.confronto.righe.length ? "Nessuna delle riviste scelte ha una scheda." : "Nessuna scheda delle riviste."));
  };
  renderTables();
  put(view, ...head("Riviste", null, refreshButton(() => { clearCache(); route(); })), await journalListBlock(d.riviste),
    h("section", {class: "block"}, h("div", {class: "block-head"}, h("h2", null, "Riviste da confrontare"),
      multiPick({label: "Riviste da confrontare", items: journalItems(all), selected: pick,
        onChange: () => { savePick(pick); renderTables(); }})),
      h("p", {class: "muted"}, "Le tabelle qui sotto mostrano solo le riviste scelte. La scelta resta salvata in questo browser.")),
    h("section", {class: "block"}, h("div", {class: "block-head"}, h("h2", null, "Metriche e linee guida")), holder,
      srcLine(d.metriche, "SJR e quartile: SCImago 2025. h-index: prima SCImago, poi OpenAlex.")),
    h("section", {class: "block"}, h("div", {class: "block-head"}, h("h2", null, "Regole per gli autori a confronto"),
      h("span", {class: "muted"}, "primo tipo di articolo di ogni scheda")), confHolder, srcLine(d.confronto)),
    await journalProfileBlock());
};
/* T-052: the card of a journal — values from the guideline PDF (page) and values written by hand (source and date,
   migration 0019). Correcting = a new row, the old one stays in the history; "Togli" goes back to the PDF value. */
function journalCardEditor(pick, label, onClose) {
  const box = h("section", {class: "jform jcard", "aria-label": `${t("Scheda di")} ${label}`});
  const title = h("div", {class: "block-head"}, h("h3", null, `${t("Scheda di")} ${label}`),
    h("button", {type: "button", class: "btn small", onclick: onClose}, "Chiudi"));
  const holder = h("div"), formBox = h("div");
  const msg = h("p", {class: "muted", role: "status"});
  const today = () => { const n = new Date(); return new Date(n.getTime() - n.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };
  const origin = (v) => v.origin === "manual" ? [tag(t("a mano"), "acc"), " ", h("span", {class: "t-sub"}, `${v.source} · ${t("letto il")} ${v.read_on}`)]
    : v.manual_id ? [tag(t("a mano"), "acc"), " ", h("span", {class: "t-sub"}, `${t("non indicato dalla rivista")} · ${v.source} · ${v.read_on}`)]
    : v.origin === "not_available" ? h("span", {class: "t-sub"}, t("non indicato nelle linee guida"))
    : [tag(v.origin === "interpreted" ? t("PDF, dedotto") : "PDF", "good"), " ", h("span", {class: "t-sub"}, `p. ${v.page_no}`)];
  let cur = null;
  const openForm = (v) => {
    const f = {};
    const field = h("select", {name: "campo"}, cur.campi.map((c) => h("option", {value: c.campo,
      selected: v && v.field === c.campo ? true : null}, `${c.campo} — ${t(c.descrizione)}`)));
    f.campo = field;
    const types = [...new Set([cur.tutti_i_tipi, ...cur.valori.filter((x) => x.field === "tipo_articolo").map((x) => x.article_type)].filter(Boolean))];
    const list = h("datalist", {id: "jcard-types"}, types.map((x) => h("option", {value: x})));
    f.tipo = h("input", {type: "text", name: "tipo", list: "jcard-types", value: v ? v.article_type || "" : ""});
    f.valore = h("input", {type: "text", name: "valore", value: v && v.value ? v.value : ""});
    f.unita = h("input", {type: "text", name: "unita", value: v && v.unit ? v.unit : "", placeholder: t("es. parole")});
    f.fonte = h("input", {type: "text", name: "fonte", required: true, value: v && v.source ? v.source : "",
      placeholder: t("indirizzo della pagina, PDF e pagina, oppure una nota")});
    f.letto = h("input", {type: "date", name: "letto", required: true, value: today()});
    f.nota = h("input", {type: "text", name: "nota", value: ""});
    const na = h("input", {type: "checkbox", name: "na"});
    const item = v && v.item ? v.item : 1;
    const isType = () => (cur.campi.find((c) => c.campo === field.value) || {}).per_tipo;
    const typeRow = h("label", {class: "fld"}, h("span", null, t("Tipo di articolo")), f.tipo, list);
    const sync = () => { typeRow.hidden = !isType(); f.valore.disabled = na.checked; };
    field.addEventListener("change", sync); na.addEventListener("change", sync);
    const out = h("p", {class: "muted", role: "status"});
    const form = h("form", {class: "jform", onsubmit: async (e) => {
      e.preventDefault();
      const btn = form.querySelector("button[type=submit]");
      btn.disabled = true; out.textContent = t("Salvo…");
      try {
        await postJson("/api/riviste/scheda/valore", {journal_id: cur.journal_id, campo: field.value,
          tipo: isType() ? f.tipo.value.trim() : null, item, valore: f.valore.value.trim(), unita: f.unita.value.trim(),
          fonte: f.fonte.value.trim(), letto_il: f.letto.value, nota: f.nota.value.trim(), non_indicato: na.checked});
        clearCache();
        msg.textContent = t("Valore salvato: ORPHEUS lo usa al posto di quello del PDF nei controlli dei manoscritti.");
        formBox.replaceChildren(); load();
      } catch (err) { out.textContent = err.message; btn.disabled = false; }
    }},
      h("h3", null, v ? `${t("Correggi")} ${v.field}${v.article_type ? ` (${v.article_type})` : ""}` : t("Aggiungi un valore")),
      h("div", {class: "form-grid"},
        h("label", {class: "fld"}, h("span", null, t("Campo")), field), typeRow,
        h("label", {class: "fld"}, h("span", null, t("Valore")), f.valore),
        h("label", {class: "fld"}, h("span", null, t("Unità")), f.unita),
        h("label", {class: "fld"}, h("span", null, t("Fonte")), f.fonte),
        h("label", {class: "fld"}, h("span", null, t("Letto il")), f.letto),
        h("label", {class: "fld"}, h("span", null, t("Nota")), f.nota),
        h("label", {class: "fld check"}, na, h("span", null, t("La rivista non lo indica")))),
      h("div", {class: "action-row"}, h("button", {type: "submit", class: "btn primary"}, "Salva"), " ",
        h("button", {type: "button", class: "btn", onclick: () => formBox.replaceChildren()}, "Annulla")),
      out,
      h("p", {class: "src"}, t("Salva = una riga nuova nel database (journal_card_manual, con fonte e data); il valore del PDF resta nella scheda e torna in uso se togli quello a mano. Copia di sicurezza automatica come per gli altri comandi.")));
    sync();
    put(formBox, form);
    setTimeout(() => (v ? f.valore : field).focus(), 0);
  };
  const withdraw = async (v) => {
    if (!confirm(t("Togliere questo valore scritto a mano? Resta nella storia; torna in uso il valore del PDF, se c'è."))) return;
    try { await postJson("/api/riviste/scheda/togli", {id: v.manual_id}); clearCache(); msg.textContent = t("Valore tolto."); load(); }
    catch (err) { msg.textContent = err.message; }
  };
  const load = async () => {
    put(holder, h("p", {class: "muted"}, t("Lettura della scheda…")));
    try { cur = await get(`/api/riviste/scheda?id=${encodeURIComponent(pick)}`, true); } catch (e) { put(holder, errorBox(e)); return; }
    const sc = cur.scheda;
    const hist = cur.storia.filter((x) => x.status !== "current");
    put(holder,
      h("p", null, sc ? `${t("Scheda dal PDF delle linee guida del")} ${fmtDate(sc.data, false)} (${sc.versione}).`
        : t("Nessuna scheda dal PDF delle linee guida: i valori qui sotto sono solo quelli scritti a mano.")),
      h("div", {class: "action-row"}, h("button", {type: "button", class: "btn primary", onclick: () => openForm(null)}, t("Aggiungi un valore"))),
      formBox,
      cur.valori.length ? h("div", {class: "table-wrap"}, h("table", null,
        h("thead", null, h("tr", null, ["Campo", "Tipo di articolo", "Valore", "Da dove", ""].map((c) => h("th", {scope: "col"}, t(c))))),
        h("tbody", null, cur.valori.map((v) => h("tr", null,
          h("th", {scope: "row"}, v.field), h("td", null, v.article_type || "—"),
          h("td", null, v.value || "—", v.unit ? ` ${v.unit}` : ""), h("td", null, origin(v)),
          h("td", null, h("button", {type: "button", class: "btn small", onclick: () => openForm(v)}, t("Correggi")),
            v.manual_id ? [" ", h("button", {type: "button", class: "btn small", onclick: () => withdraw(v)}, t("Togli"))] : null))))))
        : h("p", {class: "empty"}, t("Scheda vuota: aggiungi i valori a mano o scrivi la scheda dal PDF delle linee guida.")),
      hist.length ? h("details", null, h("summary", null, `${t("Storia dei valori a mano")} (${hist.length})`),
        h("ul", {class: "notes"}, hist.map((x) => h("li", null, tag(x.status === "withdrawn" ? t("tolto") : t("sostituito")), " ",
          `${x.field}${x.article_type ? ` (${x.article_type})` : ""}: ${x.action === "set" ? x.value : t("non indicato")} · ${x.source} · ${x.read_on} · ${fmtDate(x.created_at)}`)))) : null,
      msg);
  };
  load();
  put(box, title, h("p", {class: "t-sub"}, "Valori dal PDF delle linee guida e valori scritti a mano (anche per le riviste senza PDF): «Correggi» o «Aggiungi un valore»."), holder);
  setTimeout(() => box.scrollIntoView({behavior: "smooth", block: "nearest"}), 0);
  return box;
}
/* T-057: profile of a journal from its published articles (stats-terms-v1): what they report about statistics,
   transparency and structure; always N and the fixed warning; a count opens the sentences behind it. */
const PROF_KEY = "orpheus-profilo-rivista";
const PROF_YEAR_TERMS = [["effect_size", "Effect size"], ["ci", "IC"], ["power_analysis", "Potenza"],
  ["mixed_model", "Modelli misti"], ["registration", "Registrazione"], ["data_availability", "Dati disponibili"]];
async function journalProfileBlock() {
  const box = h("section", {class: "block", "aria-labelledby": "h-jprof"});
  let d;
  try { d = await get("/api/riviste/profilo"); } catch (e) { return h("section", {class: "block"}, errorBox(e)); }
  const title = h("div", {class: "block-head"}, h("h2", {id: "h-jprof"}, "Profilo della rivista dagli articoli pubblicati"),
    h("span", {class: "muted"}, "metodi statistici, trasparenza e struttura"));
  const rows = d.riviste.filter((r) => r.sigla);
  if (!rows.length) {
    return put(box, title, h("p", {class: "empty"}, "Nessun articolo analizzato: premi «Aggiungi e verifica gli articoli nuovi» (passo «metodi») o lancia lavora."),
      srcLine(d));
  }
  let pick = null;
  try { pick = localStorage.getItem(PROF_KEY); } catch (e) { /* storage not available */ }
  if (!rows.some((r) => r.sigla === pick)) pick = rows[0].sigla;
  const sel = h("select", {"aria-label": t("Rivista"), onchange: (e) => {
    pick = e.target.value; try { localStorage.setItem(PROF_KEY, pick); } catch (err) { /* ignore */ } render(); }},
    rows.map((r) => h("option", {value: r.sigla, selected: r.sigla === pick ? true : null}, `${r.sigla} (N = ${fmtN(r.articoli)})`)));
  const holder = h("div");
  const prec = d.precisione && d.precisione.complessiva;
  const precLine = prec && prec.precisione != null
    ? `${t("Precisione delle regole dal controllo a mano")}: ${fmtN(prec.precisione)} (${fmtN(prec.giusti)} ${t("giuste")}, ${fmtN(prec.sbagliati)} ${t("sbagliate")}).`
    : t("Precisione delle regole non ancora misurata: controllo a mano con il comando metodi-controllo.");
  const sentences = async (r, voce, nome, into) => {
    put(into, h("p", {class: "muted"}, t("Lettura delle frasi…")));
    try {
      const x = await get(`/api/riviste/profilo/frasi?rivista=${encodeURIComponent(r.sigla)}&voce=${encodeURIComponent(voce)}`);
      put(into, h("ul", {class: "notes"}, x.frasi.map((f) => h("li", null,
        h("b", null, `${f.anno || "—"} · p. ${f.pagina}${f.sezione ? ` · ${f.sezione}` : ""}${f.dettaglio ? ` · ${f.dettaglio}` : ""}`), " ",
        h("span", {lang: "en"}, f.frase), " ", h("span", {class: "t-sub"}, f.title || "")))));
    } catch (e) { put(into, errorBox(e)); }
  };
  const render = () => {
    const r = rows.find((x) => x.sigla === pick);
    const years = r.per_anno.map((y) => y.anno).filter((y) => y != null);
    const groups = r.gruppi.map((g) => {
      const found = g.voci.filter((v) => v.articoli > 0), none = g.voci.filter((v) => !v.articoli);
      return h("div", {class: "prof-group"}, h("h3", null, t(g.nome)),
        found.length ? h("div", {class: "hbars prof-bars"}, found.flatMap((v) => {
          const more = h("div", {class: "prof-sent"});
          const btn = h("button", {type: "button", class: "linklike", "aria-expanded": "false", onclick: () => {
            const open = btn.getAttribute("aria-expanded") === "true";
            btn.setAttribute("aria-expanded", String(!open));
            if (open) put(more); else sentences(r, v.voce, v.nome, more);
          }}, t(v.nome));
          const det = Object.entries(v.dettagli || {}).map(([k, n]) => `${k} ${n}`).join(", ");
          return [btn,
            h("span", {class: "track", "aria-hidden": "true"}, h("i", {style: `width:${v.percentuale || 0}%`})),
            h("span", {class: "num"}, `${fmtN(v.articoli)}/${fmtN(r.articoli)} (${fmtN(v.percentuale)}%)`
              + (v.dichiarato_non_fatto ? ` · ${t("dichiarato non fatto")}: ${fmtN(v.dichiarato_non_fatto)}` : "")),
            det ? h("span", {class: "prof-det t-sub"}, det) : null, more].filter(Boolean);
        })) : null,
        none.length ? h("p", {class: "t-sub"}, `${t("Mai trovate")}: ${none.map((v) => t(v.nome)).join(", ")}`) : null);
    });
    const st = Object.entries(r.struttura).filter(([, v]) => v.n);
    const structure = st.length ? h("div", {class: "table-wrap"}, h("table", null,
      h("thead", null, h("tr", null, ["Misura", "Mediana", "Q1–Q3", "Min–max", "N"].map((c) => h("th", {scope: "col"}, t(c))))),
      h("tbody", null, st.map(([, v]) => h("tr", null, h("th", {scope: "row"}, t(v.nome)), h("td", {class: "num"}, fmtN(v.mediana)),
        h("td", {class: "num"}, v.q1 != null ? `${fmtN(v.q1)}–${fmtN(v.q3)}` : "—"),
        h("td", {class: "num"}, `${fmtN(v.min)}–${fmtN(v.max)}`), h("td", {class: "num"}, fmtN(v.n)))))))
      : h("p", {class: "empty"}, "Struttura non misurabile per questi articoli.");
    const perYear = r.per_anno.length > 1 ? h("div", {class: "table-wrap"}, h("table", null,
      h("thead", null, h("tr", null, h("th", {scope: "col"}, t("Anno")), h("th", {scope: "col"}, "N"),
        PROF_YEAR_TERMS.map(([, label]) => h("th", {scope: "col"}, t(label))))),
      h("tbody", null, r.per_anno.map((y) => h("tr", null, h("th", {scope: "row"}, y.anno == null ? "—" : String(y.anno)),
        h("td", {class: "num"}, fmtN(y.articoli)),
        PROF_YEAR_TERMS.map(([k]) => h("td", {class: "num"}, y.voci[k] ? `${fmtN(y.voci[k])} (${Math.round(100 * y.voci[k] / y.articoli)}%)` : "0"))))))) : null;
    put(holder,
      h("p", {class: "warnline", role: "note"}, tag("attenzione", "warn"), " ", t(d.avviso)),
      h("p", null, h("b", null, `${r.sigla} — ${r.titolo || ""}`), ` · ${t("articoli analizzati")}: ${fmtN(r.articoli)}`
        + (years.length ? ` (${Math.min(...years)}–${Math.max(...years)})` : "")
        + ` · ${t("con almeno un metodo statistico trovato")}: ${fmtN(r.articoli_con_metodi_statistici)}`),
      h("p", {class: "t-sub"}, t("Ogni conteggio è il numero di articoli in cui la voce è scritta (una volta per articolo); premi una voce per leggere le frasi con la pagina.")),
      h("div", {class: "prof-groups"}, groups),
      h("h3", null, t("Struttura (parole, figure, tabelle, riferimenti)")), structure,
      perYear ? [h("h3", null, t("Per anno")), perYear] : null,
      h("h3", null, t("Test per obiettivo e disegno")), objectiveTable(r.test_per_obiettivo),
      h("h3", null, t("Frasi utili come modello di scrittura")), writingModels(r.frasi_modello),
      h("p", {class: "t-sub"}, precLine));
  };
  render();
  return put(box, title, h("div", {class: "filters"}, h("label", {class: "field"}, t("Rivista"), sel)), holder,
    srcLine(d, `${t("Articoli analizzati in tutto")}: ${fmtN(d.articoli_analizzati)}.`));
}
function objectiveTable(o) {
  // T-057c: statistics read by objective and design (cards-v2, obiettivi-v1), always with N
  if (!o || !o.combinazioni || !o.combinazioni.length) {
    return h("p", {class: "empty"}, t("Nessun articolo con obiettivo e disegno nella scheda: lancia obiettivi.cmd e chiedi a Claude le schede."));
  }
  return [h("p", {class: "t-sub"}, t(o.nota)),
    h("div", {class: "table-wrap"}, h("table", null,
      h("thead", null, h("tr", null, ["Obiettivo e disegno", "N", "Confermati da te", "Analisi principale (dalle schede)", "Test e modelli (articoli)"].map((c) => h("th", {scope: "col"}, t(c))))),
      h("tbody", null, o.combinazioni.map((c) => h("tr", null, h("th", {scope: "row"}, t(c.disegno)),
        h("td", {class: "num"}, fmtN(c.articoli)), h("td", {class: "num"}, fmtN(c.confermati)),
        h("td", {lang: "en"}, (c.analisi_principali || []).length ? c.analisi_principali.map((x) => `${x.analisi} (${fmtN(x.articoli)})`).join("; ") : "—"),
        h("td", null, c.test.length ? c.test.map((x) => `${t(x.nome)} ${fmtN(x.articoli)}/${fmtN(c.articoli)}`).join(", ") : "—"))))))];
}
function writingModels(m) {
  // point 5 (2026-10-28): short sentences already checked in the cards, with the reference — structure, not text to copy
  if (!m || !m.campi.some((c) => c.frasi.length)) {
    return h("p", {class: "empty"}, t("Nessuna frase breve nelle schede di questa rivista: arriveranno con le schede di obiettivo e disegno."));
  }
  return [h("p", {class: "t-sub"}, t(m.nota)), ...m.campi.filter((c) => c.frasi.length).map((c) => h("details", {class: "models"},
    h("summary", null, `${t(c.nome)} (${fmtN(c.frasi_brevi)})`),
    c.aperture.length ? h("p", {class: "t-sub"}, `${t("Aperture più frequenti")}: `,
      c.aperture.map((a, i) => [i ? "; " : "", h("span", {lang: "en"}, `«${a.inizio}…»`), ` ${fmtN(a.articoli)}`])) : null,
    h("ul", {class: "notes"}, c.frasi.map((f) => h("li", null, h("span", {lang: "en"}, f.frase), " ",
      h("span", {class: "t-sub"}, `— ${f.titolo || ""}${f.anno ? `, ${f.anno}` : ""}, p. ${f.pagina}`
        + (f.tipo_obiettivo ? ` · ${t(f.tipo_obiettivo)}` : "")), " ", f.confermata ? tag(t("confermata"), "good") : tag(t("proposta"))))))
  )];
}
function latestGuide(r) {
  return (r.linee_guida || []).reduce((best, x) => (!best || String(x.data || "") > String(best.data || "")) ? x : best, null);
}
const PICK_KEY = "orpheus-riviste-confronto";
function loadPick(all, rows) {
  try {
    const saved = JSON.parse(localStorage.getItem(PICK_KEY) || "null");
    if (Array.isArray(saved)) return new Set(saved.filter((x) => all.includes(x)));
  } catch (e) { /* storage not available */ }
  if (all.length <= 6) return new Set(all);
  const bySjr = [...rows].filter((r) => r.sigla).sort((a, b) => ((b.metriche && b.metriche.sjr) || 0) - ((a.metriche && a.metriche.sjr) || 0));
  return new Set(bySjr.slice(0, 5).map((r) => r.sigla));
}
function savePick(set) {
  try { localStorage.setItem(PICK_KEY, JSON.stringify([...set])); } catch (e) { /* storage not available */ }
}

/* T-054: the journal list (riviste_prioritarie.csv) from the page — "Nuova rivista" and "Modifica" */
const JCOLS = [["sigla", "Sigla", "es. JSAMS (lettere, numeri, - e _)"], ["titolo", "Titolo", "nome completo della rivista"],
  ["issn_print", "ISSN stampa", "1234-567X"], ["issn_online", "ISSN online", "1234-567X"],
  ["sito_ufficiale", "Sito ufficiale", "https://…"], ["url_istruzioni_autori", "Istruzioni per gli autori", "https://…"],
  ["priorita", "Priorità", "facoltativa"], ["note", "Note", "facoltative"]];
function issnLines(r) {
  const list = [r.issn_print, r.issn_online].filter(Boolean).filter((x, i, a) => a.indexOf(x) === i);
  return list.length ? list.map((x, i) => [i ? h("br") : null, x]) : "—";
}
async function journalListBlock(riviste) {
  const idBySigla = new Map((riviste || []).filter((r) => r.id && r.sigla).map((r) => [r.sigla.toLowerCase(), r.id]));
  let d;
  try { d = await get("/api/riviste/elenco"); } catch (e) { return h("section", {class: "block"}, errorBox(e)); }
  const box = h("section", {class: "block", "aria-labelledby": "h-jlist"});
  const formBox = h("div");
  const msg = h("p", {role: "status", class: "muted"});
  let filter = "", gFilter = "";
  const listBox = h("div");
  const GTAG = {scheda: "good", scheda_da_inviare: "acc", senza_scheda: "acc", scheda_da_rifare: "warn",
    pdf_da_leggere: "warn", senza_linee_guida: "warn"};
  const guideCell = (g) => !g ? null : h("span", {class: "gl"}, tag(g.etichetta, GTAG[g.stato]),
    g.stato === "senza_linee_guida" || g.stato === "pdf_da_leggere" ? h("div", {class: "t-sub", title: t("Cartella")}, g.cartella) : null);
  const renderList = () => {
    const q = filter.trim().toLowerCase();
    const rows = d.righe.filter((r) => (!q || `${r.sigla} ${r.titolo} ${r.issn_print} ${r.issn_online}`.toLowerCase().includes(q))
      && (!gFilter || (r.linee_guida && r.linee_guida.stato === gFilter)));
    put(listBox, rows.length ? h("div", {class: "table-wrap tall"}, h("table", null,
      h("thead", null, h("tr", null, ["Sigla", "Titolo", "ISSN", "Linee guida e scheda", "Stato", ""].map((x) => h("th", {scope: "col"}, x)))),
      h("tbody", null, rows.map((r) => h("tr", null,
        h("td", null, h("b", null, r.sigla)), h("td", null, r.titolo),
        h("td", {class: "num nowrap"}, issnLines(r)),
        h("td", null, guideCell(r.linee_guida)),
        h("td", null, r.automatica ? tag("aggiunta automatica", "warn") : null, " ", r.nel_database ? null : tag("non ancora nel database")),
        h("td", null, h("div", {class: "card-actions"},
          h("button", {type: "button", class: "btn small", onclick: () => openForm(r)}, "Modifica rivista"),
          h("button", {type: "button", class: "btn small", disabled: !idBySigla.has(r.sigla.toLowerCase()) || null,
            title: idBySigla.has(r.sigla.toLowerCase()) ? null : "La rivista non è ancora nel database: premi «Aggiorna le riviste» (Funzioni).",
            onclick: () => put(formBox, journalCardEditor(idBySigla.get(r.sigla.toLowerCase()), r.titolo ? `${r.titolo} (${r.sigla})` : r.sigla,
              () => formBox.replaceChildren()))}, "Modifica scheda"))))))))
      : h("p", {class: "empty"}, "Nessuna rivista con questo filtro."));
  };
  const openForm = (row) => {
    const f = {}, errs = {};
    const original = row ? row.sigla : null;
    const fieldsOut = JCOLS.map(([k, label, ph]) => {
      f[k] = h("input", {type: "text", name: k, value: row ? row[k] || "" : "", placeholder: ph,
        required: k === "sigla" || k === "titolo" ? true : null, oninput: debounce(check, 300)});
      errs[k] = h("span", {class: "fld-err", role: "alert"});
      return h("label", {class: "fld"}, h("span", null, label), f[k], errs[k]);
    });
    const values = () => Object.fromEntries(Object.entries(f).map(([k, el]) => [k, el.value.trim()]));
    const out = h("p", {class: "muted", role: "status"});
    async function check() {
      try {
        const r = await postJson("/api/riviste/controlla", {valori: values(), sigla_originale: original});
        for (const [k, el] of Object.entries(errs)) {
          el.textContent = r.problemi[k] ? t(r.problemi[k]) : "";
          f[k].setAttribute("aria-invalid", r.problemi[k] ? "true" : "false");
        }
        return r.problemi;
      } catch (e) { out.textContent = e.message; return {x: e.message}; }
    }
    const fill = h("button", {type: "button", class: "btn", onclick: async () => {
      const issn = f.issn_print.value.trim() || f.issn_online.value.trim();
      if (!issn) { out.textContent = t("Scrivi prima un ISSN (stampa o online)."); f.issn_print.focus(); return; }
      fill.disabled = true; out.textContent = t("Chiedo a Crossref e OpenAlex…");
      try {
        const r = await postJson("/api/riviste/compila", {issn});
        for (const [k, v] of Object.entries(r.valori)) if (f[k] && v && (!f[k].value.trim() || k !== "sigla")) f[k].value = v;
        out.textContent = [r.fonti.length ? `${t("Compilato da")} ${r.fonti.join(", ")}.` : "",
          r.valori.editore ? `${t("Editore")}: ${r.valori.editore}.` : "", ...r.note.map((x) => t(x) + ".")].filter(Boolean).join(" ")
          + " " + t("Controlla e correggi prima di salvare.");
        check();
      } catch (err) { out.textContent = err.message; }
      fill.disabled = false;
    }}, "Compila dall'ISSN");
    const form = h("form", {class: "jform", onsubmit: async (e) => {
      e.preventDefault();
      const probs = await check();
      if (Object.keys(probs).length) { out.textContent = t("Correggi i campi segnati."); return; }
      const btn = form.querySelector("button[type=submit]");
      btn.disabled = true; out.textContent = t("Salvo…");
      try {
        const r = await postJson("/api/riviste/salva", {valori: values(), sigla_originale: original});
        clearCache();
        msg.textContent = `${t("Salvata")} ${r.sigla}. ` + (r.elenco.copia ? `${t("Copia del vecchio elenco")}: ${r.elenco.copia}. ` : "")
          + (r.cartelle_spostate.length ? `${t("Cartella rinominata")}: ${r.cartelle_spostate[0].da} → ${r.cartelle_spostate[0].a}. ` : "")
          + (r.rivista && r.rivista.azione === "nuova" ? t("Rivista scritta nel database.") : "");
        d = await get("/api/riviste/elenco", true);
        formBox.replaceChildren(); renderList();
      } catch (err) { out.textContent = err.message; btn.disabled = false; }
    }},
      h("h3", null, row ? `${t("Modifica rivista")} ${row.sigla}` : t("Nuova rivista")),
      h("div", {class: "form-grid"}, fieldsOut),
      h("div", {class: "action-row"}, fill, " ", h("button", {type: "submit", class: "btn primary"}, "Salva"), " ",
        h("button", {type: "button", class: "btn", onclick: () => formBox.replaceChildren()}, "Annulla")),
      out,
      h("p", {class: "src"}, row
        ? "Salva = la riga cambia in riviste_prioritarie.csv (prima una copia in data\\archive). Se cambi la sigla, la cartella della rivista viene rinominata (registro in rapporti\\spostamenti)."
        : "Salva = una riga nuova in riviste_prioritarie.csv (prima una copia in data\\archive), le cartelle linee_guida e articoli, la rivista nel database."));
    put(formBox, form);
    setTimeout(() => f[row ? "titolo" : "sigla"].focus(), 0);
    if (row) check();
  };
  const gl = d.linee_guida;
  /* T-055: reminders — journals without guideline PDFs (for the user) and guidelines without a card (for Claude) */
  const guideNotes = () => {
    if (!gl) return null;
    const out = h("div");
    const list = (k) => gl.sigle[k].join(", ");
    if (gl.conteggi.pdf_da_leggere) out.append(h("p", {class: "notice"},
      h("b", null, `${t("PDF delle linee guida da leggere")} (${fmtN(gl.conteggi.pdf_da_leggere)}). `),
      `${list("pdf_da_leggere")}. `, t("«Aggiungi e verifica gli articoli nuovi» li legge.")));
    if (gl.conteggi.senza_scheda) out.append(h("p", {class: "muted"},
      h("b", null, `${t("Per Claude")}: ${t("linee guida senza scheda")} (${fmtN(gl.conteggi.senza_scheda)}). `),
      `${list("senza_scheda")}. `, t("Le schede le scrive Claude in sessione dai PDF; poi «Aggiorna le riviste» le invia.")));
    if (gl.conteggi.scheda_da_inviare) out.append(h("p", {class: "notice"},
      h("b", null, `${t("schede pronte da inviare")} (${fmtN(gl.conteggi.scheda_da_inviare)}). `),
      `${list("scheda_da_inviare")}. `, t("Premi «Aggiorna le riviste».")));
    return out.children.length ? out : null;
  };
  put(box, h("div", {class: "block-head"}, h("h2", {id: "h-jlist"}, "Elenco delle riviste"),
      h("button", {type: "button", class: "btn primary", onclick: () => openForm(null)}, "Nuova rivista")),
    guideNotes(),
    formBox, msg,
    h("div", {class: "filters"},
      d.righe.length > 8 ? h("label", {class: "field"}, "Cerca una rivista", h("input", {type: "search", placeholder: "sigla, nome o ISSN",
        oninput: debounce((e) => { filter = e.target.value; renderList(); }, 150)})) : null,
      gl ? h("label", {class: "field"}, "Linee guida e scheda", h("select", {onchange: (e) => { gFilter = e.target.value; renderList(); }},
        h("option", {value: ""}, "Tutte"),
        Object.entries(d.stati_linee_guida).filter(([k]) => gl.conteggi[k]).map(([k, label]) =>
          h("option", {value: k}, `${t(label)} (${fmtN(gl.conteggi[k])})`)))) : null),
    listBox, srcLine(d, t("Le scritture fanno prima una copia di sicurezza del database se l'ultima è vecchia.")));
  renderList();
  return box;
}

/* T-054: PDFs of articoli_vari without a journal — the user chooses ("Elenco + scelta a mano") */
async function unassignedBlock() {
  let d;
  try { d = await get("/api/articoli/da-assegnare"); } catch (e) { return null; }
  const box = h("section", {class: "block", "aria-labelledby": "h-unas"});
  const msg = h("p", {role: "status", class: "muted"});
  const render = () => {
    put(box, h("div", {class: "block-head"}, h("h2", {id: "h-unas"}, "Da assegnare"),
        h("span", {class: "muted"}, `${fmtN(d.righe.length)} PDF in articoli_vari senza rivista`)),
      d.righe.length ? h("p", {class: "muted"}, "ORPHEUS non ha riconosciuto la rivista (DOI assente o non confermato da Crossref). Scegli la rivista: il PDF va nella sua cartella articoli. \"Probabile\" = l'ISSN della rivista è stampato nelle prime pagine.")
        : h("p", {class: "empty"}, "Nessun PDF da assegnare."),
      d.righe.length ? h("div", {class: "table-wrap"}, h("table", null,
        h("thead", null, h("tr", null, ["File", "Pagine", "Titolo nel PDF", "DOI", "Crossref", "Rivista"].map((x, i) => h("th", {scope: "col", class: i === 1 ? "n" : null}, x)))),
        h("tbody", null, d.righe.map((r) => {
          const sel = h("select", {"aria-label": `Rivista per ${r.file}`},
            h("option", {value: ""}, t("— scegli —")),
            d.riviste.map((j) => h("option", {value: j.sigla, selected: r.probabile && r.probabile.sigla === j.sigla ? true : null},
              `${j.sigla}${j.titolo ? " - " + j.titolo : ""}${r.probabile && r.probabile.sigla === j.sigla ? " " + t("(probabile)") : ""}`)));
          const btn = h("button", {type: "button", class: "btn small primary", onclick: async () => {
            if (!sel.value) { msg.textContent = t("Scegli la rivista."); sel.focus(); return; }
            btn.disabled = true;
            try {
              const out = await postJson("/api/articoli/assegna", {document_id: r.document_id, sigla: sel.value});
              msg.textContent = `${r.file} → ${out.spostati.length ? out.spostati[0].a : sel.value}`;
              clearCache();
              d = await get("/api/articoli/da-assegnare", true); render();
            } catch (e) { msg.textContent = e.message; btn.disabled = false; }
          }}, "Assegna");
          return h("tr", null, h("td", null, r.file), h("td", {class: "n"}, fmtN(r.pagine)),
            h("td", {lang: "en"}, r.titolo_pdf || "—"), h("td", null, r.doi_candidato || "—"), h("td", null, r.esito_crossref),
            h("td", null, h("div", {class: "assign"}, sel, btn), r.probabile ? tag("probabile: " + r.probabile.sigla, "acc") : null));
        })))) : null,
      d.non_ancora_letti.length ? h("p", {class: "muted"}, `${t("PDF in articoli_vari non ancora letti")}: ${fmtN(d.non_ancora_letti.length)}. ${t("Premi \"Aggiungi e verifica gli articoli nuovi\": li legge, verifica i DOI, riconosce le riviste e li sposta.")}`) : null,
      d.da_spostare ? h("p", {class: "muted"}, `${t("PDF con la rivista riconosciuta, da spostare")}: ${fmtN(d.da_spostare)}.`) : null,
      msg, srcLine(d, d.ultimo_registro ? `${t("Ultimo registro degli spostamenti")}: ${d.ultimo_registro}.` : null));
  };
  render();
  return box;
}

/* manuscripts (T-037, Fase 6): a .docx of the manuscripts folder against the journal cards; nothing is written */
const OUT = {ko: ["da correggere", "crit"], guarda: ["da guardare", "warn"], ok: ["conforme", "good"], na: ["non indicato", ""]};
const OUT_ORDER = ["ko", "guarda", "ok", "na"];
const MS_KEY = "orpheus-manoscritti-riviste";
const msState = {file: null, riviste: null, tipo: "auto", result: null, filtro: "ko+guarda", busy: false};
function msQuery() {
  const p = new URLSearchParams({file: msState.file, tipo: msState.tipo});
  if (msState.riviste && msState.riviste.size) p.set("riviste", [...msState.riviste].join(","));
  return p.toString();
}
function outcomeTag(k) { const [t, kind] = OUT[k] || [k, ""]; return tag(t, kind); }
function countsBar(c, total) {
  const box = h("div", {class: "ms-bar", role: "img", "aria-label":
    `da correggere ${c["da correggere"]}, da guardare ${c["da guardare"]}, conformi ${c["conforme"]}`});
  for (const [key, cls] of [["da correggere", "crit"], ["da guardare", "warn"], ["conforme", "good"]]) {
    if (c[key]) box.append(h("i", {class: cls, style: `width:${(c[key] / total) * 100}%`, title: `${key}: ${c[key]}`}));
  }
  return box;
}
sections.manoscritti = async () => {
  const view = liveView;
  const d = await get("/api/manoscritti", true);
  const all = d.riviste.map((r) => r.sigla);
  if (!msState.riviste) {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(MS_KEY) || "null"); } catch (e) { saved = null; }
    msState.riviste = new Set(Array.isArray(saved) ? saved.filter((x) => all.includes(x)) : all);
  }
  /* a manuscript is the name of a file of the manuscripts folder or a path chosen with "Scegli il manoscritto" */
  const keys = [...d.scelti.filter((x) => x.esiste).map((x) => x.percorso), ...d.file.map((f) => f.nome)];
  if (!keys.includes(msState.file)) { msState.file = keys[0] || null; msState.result = null; }
  const fileBox = h("div"), resultBox = h("div"), checkBox = h("div");
  const fileMsg = h("p", {class: "muted", role: "status"});
  const choose = async (body) => {
    fileMsg.textContent = body.percorso ? t("Controllo il percorso…") : t("Si apre la finestra di Windows per scegliere il file…");
    try {
      const r = await postJson("/api/manoscritti/scegli", body);
      if (!r.scelto) { fileMsg.textContent = t("Nessun file scelto."); return; }
      msState.file = r.scelto.percorso; msState.result = null; route();
    } catch (e) { fileMsg.textContent = e.message; }
  };
  const radio = (key, name, sub, extra, disabled) => h("li", null, h("label", {class: disabled ? "off" : null},
    h("input", {type: "radio", name: "ms-file", value: key, checked: key === msState.file || null, disabled: disabled || null,
      onchange: () => { msState.file = key; msState.result = null; renderResult(); renderCheck(); }}),
    h("span", null, h("span", {class: "ms-name"}, name), sub ? h("span", {class: "t-sub ms-path"}, sub) : null), extra));
  const pathInput = h("input", {type: "text", placeholder: "C:\\Users\\…\\manoscritto.docx", "aria-label": "Percorso completo del manoscritto"});
  const renderFiles = () => put(fileBox,
    h("div", {class: "action-row"},
      h("button", {type: "button", class: "btn primary", onclick: () => choose({})}, "Scegli il manoscritto…"),
      h("details", {class: "action-more"}, h("summary", null, "oppure incolla il percorso"),
        h("form", {class: "inline-form", onsubmit: (e) => { e.preventDefault(); if (pathInput.value.trim()) choose({percorso: pathInput.value.trim()}); }},
          pathInput, h("button", {type: "submit", class: "btn"}, "Usa questo file")))),
    fileMsg,
    d.scelti.length ? [h("h3", null, "Manoscritti recenti"),
      h("ul", {class: "ms-files", role: "radiogroup", "aria-label": "Manoscritti recenti"}, d.scelti.map((x) => radio(x.percorso, x.nome,
        x.percorso, h("span", {class: "ms-extra"}, x.esiste ? h("span", {class: "t-sub"}, `${t("usato il")} ${fmtDate(x.ultimo_uso, false)}`) : tag("file non trovato", "warn"),
          iconBtn("cestino", "Togli dallo storico", async () => {
            try { await postJson("/api/manoscritti/dimentica", {percorso: x.percorso}); route(); } catch (e) { fileMsg.textContent = e.message; }
          })), !x.esiste)))] : null,
    d.file.length ? [h("h3", null, "Nella cartella manoscritti"),
      h("ul", {class: "ms-files", role: "radiogroup", "aria-label": "Nella cartella manoscritti"}, d.file.map((f) => radio(f.nome, f.nome, null,
        h("span", {class: "t-sub"}, ` ${fmtDate(f.modificato)}, ${fmtN(f.kb)} kB`))))] : null,
    !keys.length ? h("p", {class: "empty"}, "Scegli il file Word (.docx) del manoscritto, dove si trova: ORPHEUS ne ricorda il percorso per le volte successive.") : null);
  const pickBox = multiPick({label: "Riviste", items: journalItems(all).map((j) => ({...j,
    label: (d.riviste.find((r) => r.sigla === j.value) || {}).titolo ? `${d.riviste.find((r) => r.sigla === j.value).titolo} (${j.value})` : j.label})),
    selected: msState.riviste, onChange: () => { savePickMs(); renderCheck(); }});
  const KINDS = [["auto", "Lo stima ORPHEUS"], ["originale", "Studio originale"], ["revisione", "Revisione sistematica o meta-analisi"], ["protocollo", "Protocollo"]];
  const kindBox = h("div", {class: "radios", role: "radiogroup", "aria-label": "Tipo di articolo"}, KINDS.map(([v, label]) => h("label", {class: "radio"},
    h("input", {type: "radio", name: "ms-kind", value: v, checked: msState.tipo === v || null, onchange: () => { msState.tipo = v; }}), label)));
  const run = async () => {
    if (!msState.file || !msState.riviste.size) return;
    msState.busy = true; renderCheck();
    if (msState.file.includes("\\") || msState.file.includes("/")) {           // a path of the history: last use now
      try { await postJson("/api/manoscritti/scegli", {percorso: msState.file}); } catch (e) { /* the check says what is wrong */ }
    }
    try { msState.result = await api(`/api/manoscritti/controllo?${msQuery()}`); msState.error = null; }
    catch (e) { msState.result = null; msState.error = e; }
    msState.busy = false; renderCheck(); renderResult();
    if (msState.result) resultBox.scrollIntoView({behavior: "smooth", block: "start"});
  };
  const renderCheck = () => {
    const why = !msState.file ? "Scegli prima il manoscritto (punto 1)." : !msState.riviste.size ? "Scegli almeno una rivista (punto 2)." : null;
    put(checkBox, h("div", {class: "action-row"},
      h("button", {type: "button", class: "btn primary", disabled: !!why || msState.busy || null, onclick: run},
        msState.busy ? "Controllo in corso…" : "Controlla il manoscritto"),
      msState.result ? h("a", {class: "btn", href: `/api/manoscritti/rapporto?${msQuery()}`, target: "_blank", rel: "noopener"}, "Apri il rapporto da stampare") : null),
      why ? h("p", {class: "muted"}, why) : null);
  };
  const renderResult = () => {
    const parts = [];
    if (msState.error) parts.push(h("div", {class: "error", role: "alert"}, msState.error.message));
    if (msState.result) parts.push(h("section", {class: "block"}, msResult(msState.result)));
    put(resultBox, ...parts);
  };
  renderFiles(); renderCheck(); renderResult();
  const prepSel = h("select", {"aria-label": "Rivista per cui preparare il manoscritto"},
    d.riviste.map((r) => h("option", {value: r.sigla, selected: msState.prepara === r.sigla || null}, r.titolo && r.titolo !== r.sigla ? `${r.titolo} (${r.sigla})` : r.sigla)));
  if (!msState.prepara && d.riviste.length) msState.prepara = [...msState.riviste][0] || d.riviste[0].sigla;
  prepSel.value = msState.prepara || "";
  prepSel.addEventListener("change", () => { msState.prepara = prepSel.value; });
  const prep = await actionBlock("prepara_manoscritto", {desc: "Revisione simulata e preparazione file",
    controls: h("div", {class: "filters"}, h("label", {class: "field"}, "Rivista (una sola)", prepSel),
      h("span", {class: "muted"}, "Manoscritto: quello scelto al punto 1.")),
    check: () => (!msState.file ? "Scegli prima il manoscritto (punto 1)." : !prepSel.value ? "Scegli la rivista." : null),
    extra: () => ({file: msState.file, rivista: prepSel.value})});
  put(view, ...head("Manoscritti", "Controllo del manoscritto o assistenza nella scrittura da zero", refreshButton(() => { clearCache(); route(); })),
    h("div", {class: "ms-grid"},
      h("div", {class: "stack"},
        h("section", {class: "block"}, h("h2", null, "1. Manoscritto"), fileBox),
        h("section", {class: "block"}, h("h2", null, "2. Riviste"), pickBox, srcLine(d)),
        h("section", {class: "block"}, h("h2", null, "3. Tipo di articolo"), kindBox, checkBox)),
      h("div", {class: "stack"}, prep)),
    resultBox);
};
/* small icon buttons (pencil = edit, trash = remove): label for screen readers and tooltip */
const ICONS = {
  matita: ["M4 20h4l10.5-10.5a2.8 2.8 0 0 0-4-4L4 16v4", "M13.5 6.5l4 4"],
  cestino: ["M4 7h16", "M10 11v6", "M14 11v6", "M5 7l1 12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2l1-12", "M9 7V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v3"],
};
function iconBtn(kind, label, onclick) {
  return h("button", {type: "button", class: "btn small icon-btn", title: t(label), "aria-label": t(label), onclick},
    s("svg", {viewBox: "0 0 24 24", width: "16", height: "16", fill: "none", stroke: "currentColor", "stroke-width": "2",
      "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false"},
      ICONS[kind].map((d) => s("path", {d}))));
}
/* T-056: diario delle sottomissioni e lezioni dai revisori (R10: tutto resta su questo PC) */
const subOpen = new Set();          // papers opened by the user: they stay open after a change
let subForm = null;                 // the form shown: lavoro, nuova, evento
sections.sottomissioni = async () => {
  const view = liveView;
  const d = await get("/api/sottomissioni", true);
  const reload = () => { clearCache(); route(); };
  const opt = (pairs, value) => pairs.map(([v, l]) => h("option", {value: v, selected: v === value ? true : null}, t(l)));
  const subs = d.lavori.flatMap((m) => m.sottomissioni.map((x) => ({...x, lavoro: m.label})));
  const send = async (path, body, errBox) => {
    try { await postJson(`/api/sottomissioni/${path}`, body); reload(); }
    catch (err) { if (errBox) { errBox.hidden = false; errBox.textContent = err.message; } else alert(err.message); }
  };
  const form = (title, fields, path, button, after, help) => {
    const f = {};
    const msg = h("p", {class: "error", role: "alert", hidden: true});
    const el = h("form", {class: "jform", onsubmit: (e) => {
      e.preventDefault();
      const body = {};
      for (const [k, x] of Object.entries(f)) body[k] = x.value;
      msg.hidden = true;
      send(path, body, msg);
      subForm = null;
    }}, h("h3", null, title), help ? h("p", {class: "t-sub"}, help) : null,
      h("div", {class: "form-grid"}, fields.map(([k, label, input]) => field(t(label), (f[k] = input)))),
      after ? after(f) : null, msg, h("div", {class: "action-row"}, h("button", {type: "submit", class: "btn primary"}, button), " ",
        h("button", {type: "button", class: "btn", onclick: () => { subForm = null; showForm(); }}, "Annulla")));
    return el;
  };
  const today = new Date().toISOString().slice(0, 10);
  const inp = (attrs) => h("input", {type: "text", ...(attrs || {})});
  const sel = (pairs, value) => h("select", null, opt(pairs, value));
  const subPairs = subs.map((x) => [x.id, `${x.lavoro} → ${x.journal_title} (${x.submitted_on})`]);
  const FORMS = {
    lavoro: () => form("Nuovo lavoro", [["nome", "Nome del lavoro (tuo)", inp({required: true})], ["titolo", "Titolo", inp({lang: "en"})],
        ["tipo", "Tipo", sel([["", "—"], ...d.tipi.map((k) => [k, k])], "")]], "lavoro", "Aggiungi il lavoro", null,
        "Il lavoro è il tuo articolo: lo registri una volta sola, poi gli aggiungi le sottomissioni."),
    nuova: () => form("Nuova sottomissione", [
        ["lavoro", "Lavoro", sel(d.lavori.map((m) => [m.id, m.label]), "")],
        ["rivista_id", "Rivista (dall'elenco; se manca: Riviste → Nuova rivista)", h("select", {required: true}, opt([["", "— scegli —"], ...d.riviste.map((r) => [r.id, r.titolo])], ""))],
        ["data", "Data di invio", inp({type: "date", value: today, required: true})],
        ["precedente", "Viene da (rifiuto o trasferimento)", sel([["", "—"], ...subPairs], "")]], "nuova", "Aggiungi la sottomissione", null,
        "Una sottomissione è un invio del lavoro a una rivista. Dopo un rifiuto, il nuovo invio a un'altra rivista è una nuova sottomissione."),
    evento: () => form("Nuova decisione della rivista", [
      ["sottomissione", "Sottomissione", sel(subPairs, subPairs.length ? subPairs[subPairs.length - 1][0] : "")],
      ["evento", "Evento", sel(Object.entries(d.eventi), "revisione_maggiore")],
      ["data", "Data", inp({type: "date", value: today, required: true})],
      ["giro", "Giro di revisione", h("input", {type: "number", min: "1", value: "1"})],
      ["scadenza", "Scadenza della revisione", inp({type: "date"})], ["nota", "Nota", inp()]], "evento", "Aggiungi la decisione",
      (f) => field(t("Lettera della rivista (incollala intera: resta solo su questo PC). Le motivazioni e i commenti li aggiungi dopo, sotto la lettera."), (f.lettera = h("textarea", {rows: "8", lang: "en"}))),
      "Ogni risposta o passo della rivista: revisione richiesta, rifiuto, accettazione, invio della revisione…"),
  };
  const formBox = h("div");
  const showForm = () => {
    put(formBox, subForm && FORMS[subForm] ? FORMS[subForm]() : null);
    for (const b of addBar.querySelectorAll("button[data-f]")) b.setAttribute("aria-pressed", String(b.dataset.f === subForm));
    const first = formBox.querySelector("input, select, textarea");
    if (first) first.focus();
  };
  const addBtn = (k, label, disabled, why) => h("button", {type: "button", class: "btn", "data-f": k, "aria-pressed": "false",
    disabled: disabled || null, title: disabled ? why : null, onclick: () => { subForm = subForm === k ? null : k; showForm(); }}, label);
  const addBar = h("div", {class: "action-row add-bar"},
    addBtn("lavoro", "1. Nuovo lavoro"),
    addBtn("nuova", "2. Nuova sottomissione", !d.lavori.length, t("Prima aggiungi il lavoro.")),
    addBtn("evento", "3. Nuova decisione della rivista", !subs.length, t("Prima aggiungi la sottomissione.")));
  const forms = h("section", {class: "block"}, h("h2", null, "Aggiungi"),
    h("p", {class: "muted"}, "In ordine: il lavoro (il tuo articolo), ogni invio a una rivista, ogni risposta della rivista."),
    addBar, formBox);
  showForm();
  const s = d.stato;
  const kpi = (v, l) => h("div", {class: "kpi static", role: "group"}, h("div", {class: "kpi-v"}, fmtN(v)), h("div", {class: "kpi-l"}, t(l)));
  const labelBox = (c) => {
    const e = c.etichetta;
    const box = h("div", {class: "t-sub"});
    const cat = h("select", {"aria-label": t("Categoria")}, opt(Object.entries(d.categorie), e ? e.category : "altro"));
    const sev = h("select", {"aria-label": t("Gravità")}, opt(Object.entries(d.gravita), e ? e.severity : "importante"));
    const fol = h("select", {"aria-label": t("Seguito")}, opt([["", "—"], ...Object.entries(d.seguito)], e ? (e.followed || "") : ""));
    const save = () => send("etichetta", {commento: c.id, categoria: cat.value, gravita: sev.value, seguito: fol.value});
    const fr = c.seguito_dalla_risposta;
    const hint = fr && (!e || e.followed !== fr.seguito) ? h("span", {class: "t-sub"}, " ",
      tag(t("dalla risposta ai revisori"), "acc"), ` ${t(fr.azione)} → ${t("seguito")}: ${t(d.seguito[fr.seguito])} `,
      h("button", {type: "button", class: "btn small", onclick: () => { fol.value = fr.seguito; if (e) save(); }}, t("Usa"))) : null;
    put(box, e ? tag(e.state === "ai" ? t("proposta di Claude") : e.state === "confirmed" ? t("confermata") : t("corretta"), e.state === "ai" ? "warn" : "ok") : tag(t("da classificare"), "warn"),
      " ", cat, " ", sev, " ", fol, " ", h("button", {type: "button", class: "btn small", onclick: save}, e && e.state === "ai" ? t("Conferma o correggi") : t("Salva")), hint);
    return box;
  };
  const commentsBox = (l) => {
    const chi = h("select", {"aria-label": t("Chi")}, opt(Object.entries(d.chi), "ED"));
    const txt = h("textarea", {rows: "3", lang: "en", "aria-label": t("Motivazione o commento")});
    const msg = h("p", {class: "error", role: "alert", hidden: true});
    const list = l.commenti.length ? h("ul", {class: "plain"}, l.commenti.map((c) => h("li", null,
      h("b", null, `${d.chi[c.reviewer] || c.reviewer} (${c.comment_key})`), " ",
      c.nella_lettera ? tag(t("nella lettera"), "ok") : tag(t("non trovato nella lettera"), "warn"), " ",
      h("span", {lang: "en"}, c.text), labelBox(c),
      iconBtn("cestino", "Togli questo commento", () => { if (confirm(t("Togliere questo commento?"))) send("togli_commento", {commento: c.id}); }))))
      : l.no_comments ? h("p", {class: "notice"}, t("La rivista non ha fatto commenti."), " ",
        h("button", {type: "button", class: "btn small", onclick: () => send("nessun_commento", {lettera: l.id, valore: false})}, t("Annulla")))
      : h("p", {class: "empty"}, t("Nessuna motivazione o commento: aggiungili con + oppure segna – se la rivista non ne ha fatti."));
    return h("div", null, h("h4", null, t("Motivazioni e commenti")), list,
      l.no_comments ? null : h("div", {class: "form-grid"}, field(t("Chi"), chi), field(t("Motivazione o commento (incolla solo la parte che conta)"), txt)),
      msg,
      l.no_comments ? null : h("div", {class: "action-row"},
        h("button", {type: "button", class: "btn primary", title: t("Aggiungi una motivazione o un commento"), onclick: () => { msg.hidden = true; send("commento", {lettera: l.id, chi: chi.value, testo: txt.value}, msg); }}, "+ ", t("Aggiungi")), " ",
        l.commenti.length ? null : h("button", {type: "button", class: "btn", title: t("La rivista non ha fatto commenti"), onclick: () => send("nessun_commento", {lettera: l.id, valore: true}, msg)}, "– ", t("Nessun commento"))));
  };
  const eventRow = (e) => {
    const editBox = h("div", {hidden: true});
    const ev = sel(Object.entries(d.eventi), e.event);
    const dt = inp({type: "date", value: e.event_on});
    const gr = h("input", {type: "number", min: "1", value: String(e.round)});
    const due = inp({type: "date", value: e.due_on || ""});
    const nota = inp({value: e.note || ""});
    const msg = h("p", {class: "error", role: "alert", hidden: true});
    put(editBox, h("div", {class: "form-grid"}, field(t("Evento"), ev), field(t("Data"), dt), field(t("Giro di revisione"), gr),
      field(t("Scadenza della revisione"), due), field(t("Nota"), nota)), msg,
      h("div", {class: "action-row"}, h("button", {type: "button", class: "btn primary", onclick: () => send("correggi",
        {evento_id: e.id, evento: ev.value, data: dt.value, giro: gr.value, scadenza: due.value, nota: nota.value}, msg)}, t("Salva la modifica")), " ",
        h("button", {type: "button", class: "btn", onclick: () => { editBox.hidden = true; }}, t("Annulla"))));
    const sug = e.suggerimento ? h("p", {class: "warnline", role: "note"}, tag(t("suggerimento"), "warn"), " ",
      t("La lettera sembra un rifiuto senza revisione."), " ",
      h("button", {type: "button", class: "btn small", onclick: () => send("correggi", {evento_id: e.id, evento: e.suggerimento})}, t("Cambia in «Rifiutato senza revisione»"))) : null;
    return h("li", null, h("div", {class: "eur-bar"},
      h("span", null, `${e.event_on} · ${t(d.eventi[e.event] || e.event)}${e.round > 1 ? ` (${t("giro")} ${e.round})` : ""}${e.due_on ? ` · ${t("scadenza")} ${e.due_on}` : ""}${e.note ? ` · ${e.note}` : ""}`),
      iconBtn("matita", "Modifica questo passo", () => { editBox.hidden = !editBox.hidden; }),
      iconBtn("cestino", "Togli questo passo", () => { if (confirm(t("Togliere questo passo? Resta nella storia ma non si vede e non si conta."))) send("togli_evento", {evento_id: e.id}); })),
      sug, editBox,
      e.lettera ? h("details", null, h("summary", null, `${t("Lettera del")} ${e.lettera.received_on} · ${plural(e.lettera.commenti.length, "commento", "commenti")}${e.lettera.no_comments ? ` · ${t("nessun commento della rivista")}` : ""}`),
        h("div", {class: "pre", lang: "en"}, e.lettera.text), commentsBox(e.lettera)) : null);
  };
  const subBlock = (x) => h("div", {class: "block"},
    h("h3", null, `${x.journal_title}`, " ", tag(t(x.stato_testo), ["accettato"].includes(x.stato) ? "ok" : "warn")),
    h("p", {class: "t-sub"}, [`${t("Inviato")} ${x.submitted_on}`,
      x.giorni_alla_prima_decisione != null ? `${t("prima decisione dopo")} ${plural(x.giorni_alla_prima_decisione, "giorno", "giorni")}` : null,
      x.giorni_in_attesa != null ? `${t("in attesa da")} ${plural(x.giorni_in_attesa, "giorno", "giorni")}` : null,
      x.scadenza_revisione ? `${t("scadenza della revisione")} ${x.scadenza_revisione} (${plural(x.giorni_alla_scadenza, "giorno", "giorni")})` : null].filter(Boolean).join(" · ")),
    h("ol", {class: "notes"}, x.eventi.map(eventRow)));
  const papers = d.lavori.length ? d.lavori.map((m) => {
    const last = m.sottomissioni[m.sottomissioni.length - 1];
    const box = h("details", {class: "block paper", open: subOpen.has(m.id) || null, ontoggle: () => {
      if (box.open) subOpen.add(m.id); else subOpen.delete(m.id); }},
      h("summary", null, h("span", {class: "paper-head"}, h("span", {class: "paper-name"}, m.label),
        m.title ? h("span", {class: "t-title", lang: "en"}, m.title) : null,
        h("span", {class: "t-sub"}, plural(m.sottomissioni.length, "sottomissione", "sottomissioni"),
          last ? [" · ", t("ultima"), ": ", last.journal_title, " ", tag(t(last.stato_testo), last.stato === "accettato" ? "good" : "warn")] : null))),
      m.sottomissioni.length ? m.sottomissioni.map(subBlock) : h("p", {class: "empty"}, t("Nessuna sottomissione.")));
    return box;
  }) : [h("p", {class: "empty"}, t("Nessun lavoro: aggiungi il primo con «1. Nuovo lavoro» (anche le sottomissioni passate)."))];
  const les = d.lezioni;
  const dec = (j) => Object.entries(j.decisioni).map(([k, n]) => `${t(les.eventi[k] || k)} ${n}`).join(", ") || "—";
  const lessons = h("section", {class: "block"}, h("h2", null, "Storico per rivista"), h("p", {class: "t-sub"}, t(les.nota)),
    les.riviste.length ? h("div", {class: "table-wrap"}, h("table", null,
      h("thead", null, h("tr", null, ["Rivista", "Sottomissioni", "Decisioni", "Commenti (confermati)", "Categorie (commenti)", "Giorni alla prima decisione"].map((c) => h("th", {scope: "col"}, t(c))))),
      h("tbody", null, les.riviste.map((j) => h("tr", null, h("th", {scope: "row"}, j.rivista), h("td", {class: "num"}, fmtN(j.sottomissioni)),
        h("td", null, dec(j)), h("td", {class: "num"}, `${fmtN(j.commenti)} (${fmtN(j.confermati)})`),
        h("td", null, j.categorie.length ? j.categorie.map((c) => `${t(c.nome)} ${c.commenti}`).join(", ") : "—"),
        h("td", {class: "num"}, j.giorni_alla_prima_decisione.n ? `${fmtN(j.giorni_alla_prima_decisione.mediana)} (N = ${j.giorni_alla_prima_decisione.n})` : "—"))))))
      : h("p", {class: "empty"}, t("Ancora nessuna sottomissione.")));
  put(view, ...head("Sottomissioni", "Storico delle sottomissioni", refreshButton(reload)),
    h("div", {class: "kpis", "data-block": "Riepilogo"}, kpi(s.lavori, "lavori"), kpi(s.sottomissioni, "sottomissioni"), kpi(s.commenti, "commenti"),
      kpi(s.commenti_classificati, "classificati"), kpi(s.classificazioni_confermate, "confermati da te")),
    forms, h("section", {class: "group"}, h("h2", {class: "group-title"}, "I tuoi lavori"),
      h("p", {class: "muted"}, "Clic sul titolo per aprire lo storico del lavoro."), ...papers), lessons,
    h("p", {class: "t-sub"}, "Le lettere restano solo su questo PC: mai su GitHub, nei registri o nei pacchetti dei colleghi."), srcLine(d));
};
function savePickMs() { try { localStorage.setItem(MS_KEY, JSON.stringify([...msState.riviste])); } catch (e) { /* not available */ } }
function msResult(r) {
  const m = r.manoscritto;
  const kpi = (v, l, n) => h("div", {class: "kpi static", role: "group"}, h("div", {class: "kpi-v"}, v), h("div", {class: "kpi-l"}, l), n ? h("div", {class: "kpi-n"}, n) : null);
  const out = [h("p", {class: "notice"}, `Tipo di articolo: ${r.tipo_testo} (${r.scelto_da === "utente" ? "scelto da te" : "stimato: " + r.motivo_tipo}). ${r.nota}`)];
  if (r.titolo) out.push(h("p", {class: "t-title"}, r.titolo));
  out.push(h("div", {class: "kpis"},
    kpi(fmtN(m.parole_testo), "parole del testo", "da Introduction alla Conclusion"),
    kpi(fmtN(m.parole_abstract), "parole dell'abstract", `${m.titoletti_abstract} titoletti`),
    kpi(fmtN(m.parole_chiave), "parole chiave"),
    kpi(fmtN(m.riferimenti), "riferimenti", r.verifica_riferimenti
      ? `${fmtN(r.verifica_riferimenti.conteggi["verificato"])} verificati, ${fmtN(r.verifica_riferimenti.conteggi["discordante"])} discordanti (Crossref, ${r.verifica_riferimenti.data})`
      : `${fmtN(m.riferimenti_con_doi)} con DOI; non verificati (manoscritto.cmd)`),
    kpi(`${fmtN(m.figure)} + ${fmtN(m.tabelle)}`, "figure + tabelle"),
    kpi(m.citazioni.split(" (")[0], "citazioni nel testo", m.citazioni.includes(" (") ? m.citazioni.split(" (")[1].replace(")", "") : null),
    kpi(m.lingua === "en" ? "inglese" : (m.lingua || "?"), "lingua"),
    kpi(`${fmtN(m.corpo_pt)} pt`, "carattere", `${m.carattere || "?"}, interlinea ${m.interlinea}`),
    kpi(m.numeri_di_riga ? "sì" : "no", "numeri di riga"),
    kpi(fmtN(m.revisioni_non_accettate + m.commenti), "revisioni e commenti", "da togliere prima dell'invio")));
  if (!r.riviste.length) {                       // never stop in silence (2026-10-28)
    out.push(h("p", {class: "notice", role: "alert"}, h("b", null, t("Nessuna rivista controllata.")), " ",
      (r.riviste_sconosciute || []).length ? `${t("Non trovo una scheda per")}: ${r.riviste_sconosciute.join(", ")}. ` : "",
      t("Controlla in Riviste che la rivista abbia la scheda e la sigla nell'elenco delle riviste.")));
  }
  if (r.riviste.length > 1) {
    const max = Math.max(...r.riviste.map((j) => j.conteggi["da correggere"] + j.conteggi["da guardare"] + j.conteggi["conforme"]), 1);
    const near = (j) => j.affinita ? j.affinita.vicinanza_temi : null;
    const cols = [
      {key: "rivista", label: "Rivista", sort: true, cell: (j) => [h("div", null, swatch(j.rivista), " ", sgl(j.rivista, true)),
        h("div", {class: "t-sub"}, j.tipo_scheda || "tipo non trovato")]},
      {key: "temi", label: "Vicinanza ai temi", num: true, sort: true, value: near,
        cell: (j) => near(j) === null ? "—" : h("span", {class: "bar-cell", title: j.affinita.incerta ?
          `solo ${j.affinita.articoli_in_orpheus} articoli di questa rivista in ORPHEUS: stima incerta` : null},
          `${fmtN(near(j))}${j.affinita.incerta ? " *" : ""}`,
          h("span", {class: "mini", "aria-hidden": "true"}, h("i", {style: `width:${Math.max(0, Math.min(100, near(j)))}%`})))},
      {key: "sjr", label: "SJR", num: true, sort: true, value: (j) => j.affinita && j.affinita.metriche ? j.affinita.metriche.sjr : null,
        cell: (j) => { const mm = j.affinita && j.affinita.metriche; return mm && mm.sjr != null ? `${fmtN(mm.sjr)}${mm.quartile ? ` (${mm.quartile})` : ""}` : "—"; }},
      {key: "ko", label: "Da correggere", num: true, sort: true, value: (j) => j.conteggi["da correggere"], cell: (j) => fmtN(j.conteggi["da correggere"])},
      {key: "guarda", label: "Da guardare", num: true, sort: true, value: (j) => j.conteggi["da guardare"], cell: (j) => fmtN(j.conteggi["da guardare"])},
      {key: "ok", label: "Conformi", num: true, sort: true, value: (j) => j.conteggi["conforme"], cell: (j) => fmtN(j.conteggi["conforme"])},
      {key: "bar", label: "", cell: (j) => h("div", {style: "min-width:120px"}, countsBar(j.conteggi, max))},
    ];
    const st = msState.sortRiviste || (msState.sortRiviste = {sort: "temi", dir: -1});
    const holder = h("div");
    const draw = () => put(holder, sortableTable(cols, sortRows(r.riviste, st, cols), {state: st, rerender: draw,
      onRow: (j) => document.getElementById(`ms-${j.journal_id}`).scrollIntoView({behavior: "smooth"})}));
    draw();
    const aff = r.affinita || {};
    out.push(h("h2", null, "Riviste a confronto"),
      h("p", {class: "muted"}, `Vicinanza ai temi (0–100): quanto il manoscritto somiglia agli articoli che ogni rivista ha pubblicato, fra quelli in ORPHEUS (${aff.modo === "significato" ? "per significato, con il modello locale" : "per parole: indicativa, il modello non è su questo PC"}); * = pochi articoli della rivista, stima incerta. Clic sulle intestazioni per ordinare. Non è una probabilità di accettazione.`),
      holder);
  }
  const filt = h("div");
  const journalsBox = h("div");
  const renderJournals = () => {
    const keep = (c) => msState.filtro === "tutti" || (msState.filtro === "ko+guarda" ? ["ko", "guarda"].includes(c.esito) : c.esito === msState.filtro);
    put(filt, seg([["ko+guarda", "Da correggere e da guardare"], ["ko", "Da correggere"], ["guarda", "Da guardare"], ["ok", "Conformi"], ["tutti", "Tutti"]],
      msState.filtro, (v) => { msState.filtro = v; renderJournals(); }, "Mostra"));
    put(journalsBox, r.riviste.map((j) => {
      const groups = [...new Set(j.controlli.map((c) => c.gruppo))];
      const rows = j.controlli.filter(keep);
      return h("section", {class: "ms-journal", id: `ms-${j.journal_id}`},
        h("div", {class: "block-head"}, h("h2", null, swatch(j.rivista), " ", j.titolo),
          h("span", {class: "muted"}, `${j.tipo_scheda || "tipo non trovato"} · scheda del ${fmtDate(j.data_scheda, false)} · linee guida del ${j.data_linee_guida ? fmtDate(j.data_linee_guida, false) : "?"}`)),
        h("p", {class: "count-line"}, OUT_ORDER.slice(0, 3).map((k) => [outcomeTag(k), ` ${j.conteggi[OUT[k][0] === "conforme" ? "conforme" : OUT[k][0]]}  `])),
        j.affinita && j.affinita.articoli_simili && j.affinita.articoli_simili.length ? h("p", {class: "ms-near"},
          `Temi${j.affinita.vicinanza_temi != null ? ` (vicinanza ${fmtN(j.affinita.vicinanza_temi)}/100)` : ""}: articoli più vicini pubblicati da questa rivista: `,
          j.affinita.articoli_simili.map((x, n) => [n ? "; " : "", h("button", {type: "button", class: "linklike", onclick: () => openArticle(x.article_id)},
            x.titolo || x.article_id), x.anno ? ` (${x.anno})` : ""])) : null,
        rows.length ? h("div", {class: "table-wrap"}, h("table", null,
          h("thead", null, h("tr", null, h("th", {scope: "col"}, "Controllo"), h("th", {scope: "col"}, "Esito"), h("th", {scope: "col"}, "Nel manoscritto"), h("th", {scope: "col"}, "Regola della rivista"))),
          h("tbody", null, groups.flatMap((g) => {
            const gr = rows.filter((c) => c.gruppo === g).sort((a, b) => OUT_ORDER.indexOf(a.esito) - OUT_ORDER.indexOf(b.esito));
            if (!gr.length) return [];
            return [h("tr", {class: "ms-group"}, h("th", {colspan: "4", scope: "rowgroup"}, g)),
              ...gr.map((c) => h("tr", null, h("td", null, c.titolo), h("td", null, outcomeTag(c.esito)),
                h("td", null, c.trovato, c.dettagli && c.dettagli.length ? h("details", null, h("summary", null, `dettagli (${c.dettagli.length})`),
                  h("ul", {class: "ms-det"}, c.dettagli.slice(0, 40).map((x) => h("li", null, x)))) : null),
                h("td", null, c.regola || "—", c.fonte && c.fonte.pagina ? h("span", {class: "t-sub"}, ` (pag. ${c.fonte.pagina})`) : null)))];
          }))))
          : h("p", {class: "empty"}, "Niente da mostrare con questo filtro."));
    }));
  };
  renderJournals();
  out.push(h("div", {class: "block-head ms-filter"}, h("h2", null, "Controlli per rivista"), filt), journalsBox,
    srcLine(r, "Le regole vengono dalle schede delle riviste controllate a mano; la pagina è quella del PDF delle linee guida. I riferimenti non sono ancora verificati con Crossref."));
  return h("div", null, out);
}

/* checks */
sections.verifiche = async () => {
  const view = liveView;
  const d = await get("/api/riviste");
  const v = d.verifiche, c = d.citazioni;
  const rows = (obj) => Object.entries(obj || {}).sort((a, b) => b[1] - a[1]);
  const total = (obj) => Object.values(obj || {}).reduce((a, b) => a + b, 0);
  const box = (title, lede, src, ...kids) => h("section", {class: "block"}, h("div", {class: "block-head"}, h("h2", null, title)),
    lede ? h("p", {class: "muted"}, lede) : null, ...kids, srcLine(src));
  put(view, ...head("Verifiche", "Ogni dato bibliografico è controllato con una fonte esterna gratuita; qui l'esito dell'ultimo controllo.",
    refreshButton(() => { clearCache(); route(); })),
    h("div", {class: "cols"},
      box("DOI con Crossref", "Ultimo controllo di ogni PDF con un DOI.", v.doi, hbars(rows(v.doi.esiti), total(v.doi.esiti))),
      box("PubMed", `Articoli con termini MeSH: ${fmtN(v.pubmed.con_mesh)}.`, v.pubmed, hbars(rows(v.pubmed.esiti), total(v.pubmed.esiti))),
      box("Europe PMC", `Testi aperti salvati (licenza Creative Commons): ${fmtN(v.europepmc.testi_salvati)}, con ${fmtN(v.europepmc.sezioni)} sezioni.`, v.europepmc,
        hbars(rows(v.europepmc.esiti), total(v.europepmc.esiti)), h("h3", null, "Disponibilità del testo"),
        hbars(rows(v.europepmc.disponibilita), total(v.europepmc.disponibilita))),
      box("Bibliografie", `${fmtN(c.riferimenti)} riferimenti di ${fmtN(c.articoli_con_bibliografia)} articoli; ${fmtN(c.verso_articoli_di_orpheus)} puntano ad articoli di ORPHEUS.`, c,
        hbars(rows(c.stato), c.riferimenti))));
};

/* lavora */
let pollTimer = null, consoleNext = 0;
sections.funzioni = async () => {
  const view = liveView;
  const [d, info, pano] = await Promise.all([api("/api/lavora"), actionsInfo(true).catch(() => null), get("/api/panoramica").catch(() => null)]);
  const out = [];
  out.push(...head("Funzioni", null, refreshButton(() => { clearCache(); route(); })));

  /* what needs a look: alerts (guidelines, lavora with errors) and the work lavora does not do, without repetitions */
  const todo = h("ul", {class: "notes"});
  for (const a of (pano ? pano.avvisi.righe : []).filter((x) => x.tipo !== "per te")) {
    todo.append(h("li", null, tag(a.tipo, a.tipo === "lavora" ? "warn" : "acc"),
      h("button", {type: "button", class: "linklike", onclick: () => go(a.vai === "lavora" ? "funzioni" : a.vai)}, a.testo)));
  }
  const workItem = (who, x) => h("li", null, tag(who, who === "per te" ? "warn" : ""), h("div", null,
    h("b", null, `${x.lavoro}: ${fmtN(x.quanti)}`), x.sigle ? h("div", {class: "t-sub"}, x.sigle.join(", ")) : null,
    x.file ? h("div", {class: "t-sub"}, x.file) : null, x.come ? h("div", {class: "t-sub"}, x.come) : null));
  for (const x of d.lavori.per_te) todo.append(workItem("per te", x));
  for (const x of d.lavori.per_claude) todo.append(workItem("per Claude", x));
  if (!todo.children.length) todo.append(h("li", null, h("span", {class: "empty"}, "Niente da fare: è tutto aggiornato.")));
  const last = pano && pano.ultimo_lavora;
  out.push(h("section", {class: "block", "aria-labelledby": "h-todo"},
    h("div", {class: "block-head"}, h("h2", {id: "h-todo"}, "Da guardare"),
      last ? h("span", {class: "muted"}, `ultimo lavora: ${fmtDate(last.finito || last.iniziato)}`) : null),
    todo, srcLine(d)));

  /* index of the commands: a button goes to its panel (here or in its section) */
  if (info) {
    out.push(h("section", {class: "block", "aria-labelledby": "h-act"},
      h("div", {class: "block-head"}, h("h2", {id: "h-act"}, "Comandi"), h("span", {class: "muted"}, "nell'ordine d'uso; clic per andare al comando")),
      h("ol", {class: "flow"}, info.azioni.map((a) => h("li", null, h("button", {type: "button", class: "flow-step", onclick: () => {
        const here = document.getElementById(`act-${a.azione}`);
        if (here) { here.scrollIntoView({behavior: "smooth", block: "start"}); const b = here.querySelector("button.primary"); if (b) b.focus({preventScroll: true}); }
        else go(a.sezione);
      }}, h("span", {class: "flow-t"}, a.titolo), h("span", {class: "flow-n"}, `${a.comando}${a.sezione !== "funzioni" ? ` · ${t("in")} ${t(SECTION_NAMES[a.sezione] || a.sezione)}` : ""}`),
        info.avvio.in_corso && info.avvio.azione === a.azione ? tag("in corso", "acc") : null))))));
  }
  const group = (id, title, kids) => h("section", {class: "group", "aria-labelledby": id}, h("h2", {class: "group-title", id}, title), ...kids.filter(Boolean));
  if (info) {
    out.push(group("g-art", "Articoli", [actionPanel(info, "aggiorna_articoli"), await unassignedBlock(),
      h("h3", {class: "group-sub"}, "Schede degli articoli"), ...cardsBlocks(info)]));
    out.push(group("g-riv", "Riviste", [actionPanel(info, "aggiorna_riviste")]));
  }

  const startBox = h("section", {class: "block", id: "act-lavora", "aria-labelledby": "h-start"});
  const busyElsewhere = d.in_corso_sul_pc && !d.avvio.in_corso;
  const startBtn = h("button", {class: "btn primary", type: "button", disabled: (!d.avvio.disponibile || d.avvio.in_corso || busyElsewhere) || null,
    onclick: () => { confirmBox.hidden = false; startBtn.hidden = true; confirmBox.querySelector("button").focus(); }}, "Avvia lavora");
  const consoleBox = h("pre", {class: "console", hidden: !d.avvio.prossima && !d.avvio.in_corso, "aria-label": "Avanzamento di lavora"});
  const stateLine = h("div", {class: "state-line"});
  const confirmBox = h("div", {class: "confirm", hidden: true},
    h("p", {style: "margin:0 0 10px"}, "Lavora fa una copia di sicurezza del database, poi i passi che hanno qualcosa da fare (lettura, verifiche con internet, pezzi di testo, report). Può durare da pochi secondi a qualche minuto; non chiudere la finestra del cruscotto."),
    h("button", {class: "btn primary", type: "button", onclick: async () => {
      confirmBox.hidden = true;
      try {
        await api("/api/lavora/avvia", {method: "POST", headers: {"X-Orpheus-Token": TOKEN}});
        consoleNext = 0; consoleBox.replaceChildren(); consoleBox.hidden = false; startBtn.hidden = false; startBtn.disabled = true;
        follow(consoleBox, stateLine, startBtn);
      } catch (e) { startBtn.hidden = false; put(stateLine, h("span", {class: "error"}, e.message)); }
    }}, "Sì, avvia lavora"), " ",
    h("button", {class: "btn", type: "button", onclick: () => { confirmBox.hidden = true; startBtn.hidden = false; startBtn.focus(); }}, "Annulla"));
  let why = null;
  if (!d.avvio.disponibile) why = "Per avviare lavora da qui apri il cruscotto con cruscotto.cmd (in C:\\ORPHEUS).";
  else if (busyElsewhere) why = `lavora.cmd è già in corso in un'altra finestra (dal ${fmtDate(d.in_corso_sul_pc.iniziato)}, passo ${d.in_corso_sul_pc.passo || "—"}).`;
  append(startBox, [h("div", {class: "block-head"}, h("h2", {id: "h-start"}, "Avvia lavora"), stateLine),
    h("p", {class: "muted"}, "Lo stesso di lavora.cmd: una copia di sicurezza, poi in ordine tutti i passi che hanno qualcosa da fare. Rilanciarlo è sicuro."),
    startBtn, why ? h("p", {class: "muted"}, why) : null, confirmBox, consoleBox,
    h("p", {class: "src"}, "Ogni avvio scrive un registro in C:\\ORPHEUS\\registri e lo invia a Claude, come lavora.cmd.")]);
  const lavoraKids = [startBox];
  if (d.avvio.in_corso || d.avvio.prossima) { consoleNext = 0; follow(consoleBox, stateLine, startBtn); }

  const planBox = h("div");
  const planBtn = h("button", {class: "btn", type: "button", onclick: () => loadPlan(true)}, "Controlla cosa manca");
  const loadPlan = async (fresh) => {
    put(planBox, loading("Conto cosa manca per ogni passo (senza internet, circa 10-20 secondi)…"));
    try {
      const p = await api(`/api/lavora/piano${fresh ? "?ricalcola=1" : ""}`);
      const ol = h("ol", {class: "steps"});
      p.passi.forEach((x, i) => ol.append(h("li", null, h("span", {class: "k"}, String(i + 1)), h("span", null, x.titolo),
        x.errore ? tag("conteggio non riuscito", "warn") : x.da_fare ? tag(`da fare: ${fmtN(x.da_fare)} ${x.unita}`, "acc") : h("span", {class: "no"}, "niente"))));
      put(planBox, ol, srcLine({fonte: "conteggi di engine-v1 (nessuna scrittura)", data: p.calcolato}));
    } catch (e) { put(planBox, errorBox(e)); }
  };
  lavoraKids.push(h("section", {class: "block"}, h("div", {class: "block-head"}, h("h2", null, "Cosa manca adesso"), planBtn),
    h("p", {class: "muted"}, `I ${d.passi.length} passi di lavora, in ordine. Il conteggio legge il database e le cartelle, senza internet.`), planBox));

  const runs = d.esecuzioni;
  const rState = {sort: "iniziato", dir: -1, filtro: "tutte"};
  const runsHolder = h("div");
  const runCols = [
    {key: "iniziato", label: "Quando", sort: true, cell: (r) => fmtDate(r.iniziato)},
    {key: "titolo", label: "Passo", sort: true, cell: (r) => r.titolo},
    {key: "esito", label: "Esito", sort: true, cell: (r) => tag(r.esito, r.esito === "fatto" ? "good" : r.esito === "in corso" ? "acc" : r.esito === "fatto con errori" ? "warn" : "crit")},
    {key: "tentativo", label: "Tentativo", num: true, sort: true, cell: (r) => fmtN(r.tentativo)},
    {key: "da_fare", label: "Da fare", num: true, sort: true, cell: (r) => fmtN(r.da_fare)},
    {key: "pc", label: "PC", sort: true, cell: (r) => r.pc || "—"},
    {key: "errore", label: "Errore", cell: (r) => r.errore ? h("span", {title: r.errore}, r.errore.length > 90 ? r.errore.slice(0, 90) + "…" : r.errore) : ""},
  ];
  const renderRuns = () => {
    const list = runs.filter((r) => rState.filtro === "tutte" || (rState.filtro === "errori" ? r.esito !== "fatto" : r.esito === "interrotto"));
    put(runsHolder, 
      h("div", {class: "filters"}, seg([["tutte", "Tutte"], ["errori", "Con errori"], ["interrotte", "Interrotte"]], rState.filtro,
        (v) => { rState.filtro = v; renderRuns(); }, "Filtro delle esecuzioni")),
      list.length ? sortableTable(runCols, sortRows(list, rState, runCols), {state: rState, rerender: renderRuns})
        : h("p", {class: "empty"}, "Nessuna esecuzione con questo filtro."));
  };
  renderRuns();
  lavoraKids.push(h("details", {class: "block runs"}, h("summary", null, h("h2", {class: "inline-h"}, "Ultime esecuzioni dei passi")), runsHolder,
    srcLine({fonte: "task, workflow_run (engine-v1)", data: runs[0] && (runs[0].finito || runs[0].iniziato)})));
  out.push(group("g-lav", "Lavora: tutto quello che manca", lavoraKids));
  put(view, ...out);
  if (location.hash.includes("?vai=")) { const el = document.getElementById(hashParam("vai")); if (el) el.scrollIntoView({block: "start"}); }
};
const SECTION_NAMES = {articoli: "Articoli", riviste: "Riviste", affermazioni: "Consenso", manoscritti: "Manoscritti",
  sistema: "Sistema", condivisione: "Condivisione", funzioni: "Funzioni"};
function follow(consoleBox, stateLine, startBtn) {
  clearTimeout(pollTimer);
  const tick = async () => {
    let snap;
    try { snap = await api(`/api/lavora/uscita?da=${consoleNext}`); } catch (e) { pollTimer = setTimeout(tick, 3000); return; }
    for (const line of snap.righe) {
      const cls = /^\d+\. |^  RIPRESA|^Niente da fare/.test(line) ? "hl" : /ERRORE|^\s+! /.test(line) ? "err" : null;
      consoleBox.append(cls ? h("span", {class: cls}, line) : line, "\n");
    }
    consoleNext = snap.prossima;
    if (snap.righe.length) consoleBox.scrollTop = consoleBox.scrollHeight;
    updateBadge(snap);
    if (snap.in_corso) {
      put(stateLine, h("span", {class: "pulse", "aria-hidden": "true"}), h("span", null, `In corso dalle ${fmtDate(snap.iniziato)}`));
      pollTimer = setTimeout(tick, 1000);
    } else {
      const what = snap.titolo || "lavora";
      put(stateLine, tag(snap.codice === 0 ? `${what}: finito` : `${what}: finito con codice ${snap.codice}`, snap.codice === 0 ? "good" : "warn"),
        h("span", {class: "muted"}, fmtDate(snap.finito)));
      startBtn.disabled = false;
      clearCache();
    }
  };
  tick();
}
const badge = document.getElementById("lavora-badge");
const SECTION_OF = {aggiorna_articoli: "funzioni", pacchetti: "funzioni", chat_schede: "funzioni", invia_schede_chat: "funzioni",
  aggiorna_riviste: "funzioni", invia_lavoro_claude: "affermazioni",
  prepara_manoscritto: "manoscritti", manutenzione: "sistema", esporta_schede: "condivisione", importa_schede: "condivisione",
  lavora: "funzioni"};
badge.addEventListener("click", () => go(badge.dataset.sec || "funzioni"));
function updateBadge(snap) {
  badge.hidden = !snap.in_corso;
  badge.textContent = snap.in_corso ? `${snap.titolo || "Lavora"}: in corso` : "";
  badge.dataset.sec = (snap.azione && SECTION_OF[snap.azione]) || "funzioni";
}

/* system */
const HEALTH_NAMES = {lavora: "Giri di lavora", copie_di_sicurezza: "Copie di sicurezza", prova_ripristino: "Prova di ripristino",
  disco: "Spazio su disco", migrazioni: "Migrazioni", servizi_a_pagamento: "Servizi a pagamento"};
function healthBlock(sa) {
  if (!sa) return null;
  const kind = (e) => (e === "ok" ? "good" : e === "errore" ? "crit" : "warn");
  return h("section", {class: "block"}, h("h2", null, "Salute"),
    h("p", null, tag(sa.esito, kind(sa.esito)), " ", sa.da_guardare.length ? `${sa.da_guardare.length} cose da guardare` : "tutto in ordine"),
    h("div", {class: "table-wrap"}, h("table", null,
      h("thead", null, h("tr", null, h("th", null, "Controllo"), h("th", null, "Esito"), h("th", null, "Dettaglio"), h("th", null, "Fonte e data"))),
      h("tbody", null, sa.controlli.map((c) => h("tr", null, h("td", null, HEALTH_NAMES[c.nome] || c.nome), h("td", null, tag(c.esito, kind(c.esito))),
        h("td", null, c.messaggio), h("td", {class: "t-sub"}, c.fonte + (c.data ? `, ${fmtDate(c.data)}` : ""))))))),
    h("p", {class: "src"}, `Fonte: orpheus salute (${sa.contratto}), controllato il ${fmtDate(sa.controllato_il)}. Sola lettura.`));
}

/* ------------------------------------------------------------------ condivisione con i colleghi (T-053, 0.54.1) */

function shareCounts(rows) {
  return h("dl", {class: "kv"}, rows.filter(([, v]) => v !== undefined && v !== null).map(([k, v]) => [h("dt", null, k), h("dd", null, fmtN(v))]));
}
function shareFileLine(f, received, onTrial) {
  const btns = received
    ? [h("button", {class: "btn small", type: "button", onclick: () => onTrial(f.nome)}, "Prova l'unione")]
    : [h("a", {class: "btn small", href: `/api/condivisione/scarica?nome=${encodeURIComponent(f.nome)}`, download: f.nome}, "Scarica"),
       h("button", {class: "btn small", type: "button", onclick: (e) => shareShow(f.nome, false, e.currentTarget.parentNode)}, "Mostra nella cartella")];
  return h("li", null, h("b", null, f.nome), ` · ${fmtN(f.mb)} MB · ${fmtDate(f.data)} `, ...btns);
}
async function shareShow(nome, received, where) {
  try {
    const r = await postJson("/api/condivisione/mostra", {nome, ricevuto: received});
    if (!r.aperta) where.append(h("div", {class: "t-sub"}, `${t("Il file è in")} ${r.percorso}`));
  } catch (e) { where.append(h("div", {class: "t-sub"}, e.message)); }
}

function shareExportBlock(d) {
  const out = h("div", {role: "status"});
  const btn = h("button", {class: "btn primary", type: "button", onclick: async () => {
    btn.disabled = true;
    put(out, h("p", {class: "loading"}, "Preparo il file (può volerci un minuto)…"));
    try {
      const r = await postJson("/api/condivisione/esporta");
      const x = r.riepilogo;
      put(out, h("p", {class: "notice"}, "File pronto."),
        shareCounts([["Riviste", x.riviste], ["Schede delle riviste", x.schede_riviste], ["Articoli", x.articoli],
          ["Schede degli articoli", x.schede_articoli], ["Valori delle schede uniche (EURYDIKE)", x.valori_scheda_unica],
          ["Pezzi di testo", x.pezzi_di_testo], ["Affermazioni", x.affermazioni], ["Dimensione (MB)", r.dimensione_mb]]),
        h("p", null, h("b", null, r.nome), " ",
          h("a", {class: "btn small", href: `/api/condivisione/scarica?nome=${encodeURIComponent(r.nome)}`, download: r.nome}, "Scarica"), " ",
          h("button", {class: "btn small", type: "button", onclick: (e) => shareShow(r.nome, false, e.currentTarget.parentNode)}, "Mostra nella cartella")));
    } catch (e) { put(out, h("div", {class: "error", role: "alert"}, e.message)); }
    btn.disabled = false;
  }}, "Crea il file per i colleghi");
  const old = d.creati.slice(0, 3);
  return h("section", {class: "block"}, h("h2", null, "Esporta per i colleghi"),
    h("p", null, "Un file .zip con le schede delle riviste e degli articoli, i metadati, i pezzi di testo e le affermazioni. Il database non cambia."),
    h("p", {class: "t-sub"}, "Contiene testo di articoli non aperti: mandalo solo a colleghi con gli stessi accessi, mai su internet o GitHub."),
    btn, out,
    old.length ? [h("h3", null, "File già creati"), h("ul", {class: "plain"}, old.map((f) => shareFileLine(f, false)))] : null);
}

function shareImportBlock(d) {
  const out = h("div", {role: "status"});
  async function trial(nome) {
    put(out, h("p", {class: "loading"}, "Controllo cosa entrerebbe…"));
    try {
      const r = await postJson("/api/condivisione/unisci", {nome, prova: true});
      const x = r.riepilogo;
      const nothing = !Object.keys(r.nuove || {}).length;
      const go = h("button", {class: "btn primary", type: "button", onclick: async () => {
        go.disabled = true;
        put(out, h("p", {class: "loading"}, "Copia di sicurezza e unione in corso…"));
        try {
          const m = await postJson("/api/condivisione/unisci", {nome, prova: false});
          const y = m.riepilogo;
          put(out, h("p", {class: "notice"}, "Importato."),
            shareCounts([["Riviste nuove", y.riviste_nuove], ["Schede delle riviste nuove", y.schede_riviste_nuove],
              ["Articoli nuovi", y.articoli_nuovi], ["Schede degli articoli nuove", y.schede_articoli_nuove],
              ["Pezzi di testo nuovi", y.pezzi_di_testo_nuovi], ["Righe che non sono entrate", m.problemi_totali]]),
            h("p", {class: "t-sub"}, `${t("Copia di sicurezza")}: ${m.copia_di_sicurezza}`),
            h("p", {class: "t-sub"}, `${t("Controllo del database")}: ${m.controllo.integrity}, ${t("collegamenti rotti")} ${m.controllo.foreign_key_violations}`));
          clearCache();
        } catch (e) { put(out, h("div", {class: "error", role: "alert"}, e.message)); }
      }}, "Importa adesso");
      put(out, h("p", null, h("b", null, nome), r.creato_il ? ` · ${t("creato il")} ${fmtDate(r.creato_il)}` : ""),
        shareCounts([["Riviste nuove", x.riviste_nuove], ["Schede delle riviste nuove", x.schede_riviste_nuove],
          ["Articoli nuovi", x.articoli_nuovi], ["Schede degli articoli nuove", x.schede_articoli_nuove],
          ["Pezzi di testo nuovi", x.pezzi_di_testo_nuovi], ["Schede che hai già (restano le tue)", x.schede_gia_presenti_tenute_le_tue],
          ["Righe che non possono entrare", r.problemi_totali]]),
        nothing ? h("p", {class: "notice"}, "Niente di nuovo: hai già tutto quello che c'è nel file.")
          : [h("p", {class: "t-sub"}, "Prima dell'unione viene fatta una copia di sicurezza del database. Quello che hai già non cambia."), go]);
    } catch (e) { put(out, h("div", {class: "error", role: "alert"}, e.message)); }
  }
  const input = h("input", {type: "file", accept: ".zip", id: "share-file", class: "file-hidden", onchange: async () => {
    const f = input.files && input.files[0];
    if (!f) return;
    put(out, h("p", {class: "loading"}, "Copio il file nella cartella dei dati…"));
    try {
      const r = await api("/api/condivisione/carica", {method: "POST", body: f,
        headers: {"X-Orpheus-Token": TOKEN, "X-Nome-File": encodeURIComponent(f.name), "Content-Type": "application/zip"}});
      input.value = "";
      await trial(r.nome);
    } catch (e) { put(out, h("div", {class: "error", role: "alert"}, e.message)); }
  }});
  const rec = d.ricevuti.slice(0, 5);
  return h("section", {class: "block"}, h("h2", null, "Importa da un collega"),
    h("p", null, "Scegli il file .zip ricevuto: ORPHEUS ti mostra cosa entrerebbe e chiede conferma. Quello che hai già resta tuo."),
    h("p", null, input, h("label", {for: "share-file", class: "btn primary file-pick"}, "Scegli il file .zip del collega")),
    out,
    rec.length ? [h("h3", null, "File ricevuti"), h("ul", {class: "plain"}, rec.map((f) => shareFileLine(f, true, trial)))] : null);
}

sections.condivisione = async () => {
  const view = liveView;
  const d = await api("/api/condivisione");
  put(view, ...head("Condivisione", "Scambia con i colleghi le schede delle riviste e degli articoli, i metadati e i pezzi di testo. Niente PDF, niente manoscritti.",
    refreshButton(() => route())),
    h("div", {class: "cols"}, shareExportBlock(d), shareImportBlock(d)),
    h("p", {class: "t-sub"}, "Vale per ORPHEUS ed EURYDIKE: le schede uniche degli articoli (Estrazione di EURYDIKE) viaggiano con le altre schede. Le revisioni sistematiche (protocollo, screening, decisioni) restano sul tuo PC."),
    h("p", {class: "src"}, `${t("Cartelle")}: ${d.cartella_creati} · ${d.cartella_ricevuti}`));
};

sections.sistema = async () => {
  const view = liveView;
  const d = await api("/api/sistema");
  const cc = d.controllo_copia;
  const applied = d.migrazioni.filter((m) => m.stato === "applied").length;
  put(view, ...head("Sistema", "Dove sono i dati, se la copia del database è quella giusta, le copie di sicurezza e il budget.",
    refreshButton(() => route())), await actionBlock("manutenzione"),
    healthBlock(d.salute), textsBlock(),
    h("div", {class: "cols"},
      h("section", {class: "block"}, h("h2", null, "Database"), h("dl", {class: "kv"},
        h("dt", null, "File"), h("dd", null, d.database.percorso),
        h("dt", null, "Dimensione"), h("dd", null, `${fmtN(d.database.mb)} MB`),
        h("dt", null, "Identità"), h("dd", null, `${(d.database.id || "").slice(0, 8)}…, posizione ${fmtN(d.database.posizione)} della catena di audit`),
        h("dt", null, "Controllo della copia"), h("dd", null, tag(cc.stato, cc.bloccante ? "crit" : "good"), cc.messaggio ? h("div", {class: "t-sub"}, cc.messaggio) : null),
        h("dt", null, "Migrazioni"), h("dd", null, `${applied} su ${d.migrazioni.length} applicate (ultima ${d.migrazioni.at(-1).versione})`),
        h("dt", null, "Eventi di audit"), h("dd", null, `${fmtN(d.audit.eventi)}, l'ultimo il ${fmtDate(d.audit.ultimo)}`),
        h("dt", null, "PC"), h("dd", null, d.pc),
        h("dt", null, "Versione"), h("dd", null, d.versione),
        h("dt", null, "Modello della ricerca"), h("dd", null, d.modello_ricerca ? "presente su questo PC" : "non presente: lavora.cmd lo scarica")), srcLine(d)),
      h("section", {class: "block"}, h("h2", null, "Budget"),
        h("p", null, d.budget.servizi_a_pagamento ? tag("servizi a pagamento abilitati", "crit") : tag("nessun servizio a pagamento", "good")),
        h("div", {class: "table-wrap"}, h("table", null, h("thead", null, h("tr", null, h("th", null, "Fonte"), h("th", null, "Costo"), h("th", null, "Abilitata"))),
          h("tbody", null, d.budget.fonti.map((f) => h("tr", null, h("td", null, f.nome), h("td", null, f.costo === "free" ? "gratis" : "a pagamento"), h("td", null, yesNo(f.abilitata))))))),
        h("p", {class: "src"}, "Fonte: config/orpheus.yaml e config/sources.yaml."))),
    h("section", {class: "block"}, h("h2", null, "Copie di sicurezza su questo PC"),
      d.copie_di_sicurezza.length ? h("div", {class: "table-wrap"}, h("table", null,
        h("thead", null, h("tr", null, h("th", null, "Quando"), h("th", null, "Motivo"), h("th", {class: "n"}, "Posizione"), h("th", null, "File"))),
        h("tbody", null, d.copie_di_sicurezza.map((b) => h("tr", null, h("td", null, fmtDate(b.data)), h("td", null, b.motivo || "—"),
          h("td", {class: "n"}, fmtN(b.posizione)), h("td", {class: "t-sub"}, b.file))))))
        : h("p", {class: "empty"}, "Nessuna copia di sicurezza di questo database su questo PC."),
      h("p", {class: "src"}, "Fonte: cartella locale del PC (backups/index.json). Le ultime 10.")));
};

/* ------------------------------------------------------------------ article drawer */

const drawer = document.getElementById("drawer"), scrim = document.getElementById("scrim"), dbody = document.getElementById("drawer-body");
let opener = null;
function closeDrawer() {
  drawer.hidden = true; scrim.hidden = true;
  if (opener && document.contains(opener)) opener.focus();
}
document.getElementById("drawer-close").addEventListener("click", closeDrawer);
scrim.addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !drawer.hidden) closeDrawer(); });

async function openArticle(id) {
  opener = document.activeElement;
  drawer.hidden = false; scrim.hidden = false;
  put(dbody, loading());
  document.getElementById("drawer-close").focus();
  let d;
  try { d = await api(`/api/articoli/${encodeURIComponent(id)}`); } catch (e) { put(dbody, errorBox(e)); return; }
  const a = d.articolo;
  const doi = shortDoi(a.doi);
  const tabs = [["sintesi", "Sintesi"], ["scheda", `Scheda${d.scheda ? ` (${d.scheda.valori.length})` : ""}`],
    ["affermazioni", `Affermazioni (${d.affermazioni.length})`], ["riferimenti", "Riferimenti"]];
  const panel = h("div", {role: "tabpanel"});
  const tabBar = h("div", {class: "tabs", role: "tablist"});
  const show = (key) => {
    for (const b of tabBar.children) b.setAttribute("aria-selected", String(b.dataset.k === key));
    put(panel, ...renderTab(key, d));
  };
  for (const [k, label] of tabs) tabBar.append(h("button", {type: "button", role: "tab", dataset: {k}, onclick: () => show(k)}, label));
  put(dbody, 
    h("h2", {class: "d-title", id: "drawer-title"}, a.titolo || "(senza titolo)"),
    h("div", {class: "t-sub"}, [a.nome_rivista || a.rivista, a.anno, a.editore].filter(Boolean).join(", ")),
    h("div", {class: "claim-meta", style: "margin-top:8px"},
      doi ? h("a", {href: `https://doi.org/${doi}`, target: "_blank", rel: "noopener noreferrer"}, `DOI ${doi}`) : null,
      a.pmid ? h("a", {href: `https://pubmed.ncbi.nlm.nih.gov/${a.pmid}/`, target: "_blank", rel: "noopener noreferrer"}, `PubMed ${a.pmid}`) : null,
      a.accesso === "full_text" ? tag("articolo completo") : tag("solo abstract"),
      a.licenza ? tag(a.licenza.length > 40 ? "licenza dell'editore" : a.licenza) : null),
    tabBar, panel, srcLine(d));
  show(d.affermazioni.length ? "affermazioni" : "sintesi");
}
function renderTab(key, d) {
  if (key === "sintesi") {
    return [h("dl", {class: "kv"},
      h("dt", null, "Autori"), h("dd", null, d.autori.length ? d.autori.map((x, i) => [i ? ", " : "", x.nome,
        x.orcid ? [" ", h("a", {href: `https://orcid.org/${x.orcid}`, target: "_blank", rel: "noopener noreferrer", title: "ORCID"}, "ORCID")] : null]) : "—"),
      h("dt", null, "Tipo"), h("dd", null, d.articolo.tipo || "—"),
      h("dt", null, "PDF"), h("dd", null, d.documenti.map((x) => h("div", null, `${x.file} (${fmtN(x.pagine)} pagine, ${x.classe || "—"})`,
        x.doi ? h("div", {class: "t-sub"}, `DOI ${x.doi.esito === "confirmed" ? "confermato" : x.doi.esito} da Crossref il ${fmtDate(x.doi.data, false)} (${x.doi.regole})`) : null))),
      h("dt", null, "PubMed"), h("dd", null, d.pubmed ? [`${d.pubmed.esito === "confirmed" ? "trovato" : d.pubmed.esito}, controllato il ${fmtDate(d.pubmed.data, false)}`,
        d.pubmed.mesh.length ? h("div", {class: "checks", style: "margin-top:6px"}, d.pubmed.mesh.map((m) => tag(m))) : null] : "non controllato"),
      h("dt", null, "Europe PMC"), h("dd", null, d.europepmc ? `${d.europepmc.availability === "open_access" ? "testo aperto" : d.europepmc.availability}, controllato il ${fmtDate(d.europepmc.checked_at, false)}` : "non controllato"),
      h("dt", null, "Pezzi di testo"), h("dd", null, fmtN(d.pezzi)),
      d.revisioni && d.revisioni.length ? [h("dt", null, t("Usato nelle revisioni")), h("dd", null, d.revisioni.map((r) => h("div", null,
        h("a", {href: `#/eurydike/fulltext?r=${encodeURIComponent(r.revisione)}`, onclick: () => closeDrawer()}, r.titolo), ` (${r.studio}, ${t("full text")}: ${t((EUR_FT[r.full_text] || [r.full_text])[0])})`)))] : null)];
  }
  if (key === "scheda") {
    if (!d.scheda) return [h("p", {class: "empty"}, "Nessuna scheda per questo articolo. Le schede le scrive Claude in sessione (pacchetti.cmd).")];
    const ul = h("ul", {class: "values"});
    for (const v of d.scheda.valori) {
      ul.append(h("li", null, h("div", {class: "v-field"}, FIELD[v.campo] || v.campo, v.nome ? `: ${v.nome}` : ""),
        h("div", {class: "v-val"}, v.valore || "non riportato", v.unita ? ` ${v.unita}` : ""),
        v.citazione ? h("details", null, h("summary", null, `Frase del PDF${v.pagina ? `, pagina ${v.pagina}` : ""}`),
          h("blockquote", {class: "q", lang: "en"}, v.citazione)) : null));
    }
    return [h("p", {class: "muted"}, `Scheda accettata il ${fmtDate(d.scheda.data, false)}: ogni valore ha la sua frase nel PDF, controllata da ORPHEUS.`), ul];
  }
  if (key === "affermazioni") {
    if (!d.affermazioni.length) return [h("p", {class: "empty"}, "Nessuna affermazione per questo articolo.")];
    const ul = h("ul", {class: "claims"});
    for (const c of d.affermazioni) {
      ul.append(claimItem({...c, controllo: c.controllo || "da controllare", livello: c.evidenze[0] && c.evidenze[0].livello}, false));
    }
    const why = d.affermazioni[0].evidenze[0] && d.affermazioni[0].evidenze[0].ragioni;
    return [why && why.length ? h("p", {class: "muted"}, `Livello della prova: ${why.join("; ")}.`) : null, ul];
  }
  const r = d.riferimenti;
  const label = {verified: "verificati", unverified: "non verificati", not_found: "non trovati", mismatch: "discordanti"};
  const rows = Object.entries(r.per_stato).map(([k, n]) => [label[k] || k, n]).sort((a, b) => b[1] - a[1]);
  const total = rows.reduce((a, b) => a + b[1], 0);
  const list = (items) => items.length ? h("ul", {class: "notes"}, items.map((x) => h("li", null,
    h("button", {type: "button", class: "linklike", onclick: () => openArticle(x.id)}, `${x.titolo} (${x.anno || "—"})`)))) : h("p", {class: "empty"}, "Nessuno.");
  return [total ? hbars(rows, total) : h("p", {class: "empty"}, "Nessun riferimento letto per questo articolo."),
    h("h3", null, "Cita questi articoli di ORPHEUS"), list(r.cita_articoli_di_orpheus),
    h("h3", null, "È citato da questi articoli di ORPHEUS"), list(r.citato_da)];
}

/* ------------------------------------------------------------------ router, theme */

/* language (IT/EN): the Italian text is the source; English entries are added to EN as they are translated */

function applyLang() {
  document.documentElement.lang = lang;
  for (const el of document.querySelectorAll("[data-i18n]")) { el.textContent = t(el.dataset.i18n); if (el.firstChild) el.firstChild.__src = el.dataset.i18n; }
  for (const el of document.querySelectorAll("[data-i18n-aria]")) el.setAttribute("aria-label", t(el.dataset.i18nAria));
  for (const b of document.querySelectorAll("[data-lang-set]")) b.setAttribute("aria-pressed", String(b.dataset.langSet === lang));
}
for (const b of document.querySelectorAll("[data-lang-set]")) b.addEventListener("click", () => {
  lang = b.dataset.langSet;
  try { localStorage.setItem("orpheus-lingua", lang); } catch (e) { /* storage not available */ }
  applyLang(); buildNav(); route();
});

/* modules: ORPHEUS (green) and EURYDIKE (red); lavora, condivisione and sistema are shared */
const SHARED = ["funzioni", "condivisione", "sistema"];
const NAV = {
  orpheus: [["panoramica", "Panoramica"], ["articoli", "Articoli"], ["riviste", "Riviste"], ["manoscritti", "Manoscritti"],
    ["sottomissioni", "Sottomissioni"], ["ricerca", "Ricerca"], ["affermazioni", "Consenso"], ["verifiche", "Verifiche"], null, ["funzioni", "Funzioni"], ["condivisione", "Condivisione"], ["sistema", "Sistema"]],
  eurydike: [["eurydike/panoramica", "Panoramica"], ["eurydike/protocollo", "Protocollo"], ["eurydike/screening", "Screening"],
    ["eurydike/fulltext", "Full text"], ["eurydike/estrazione", "Estrazione (schede)"], ["eurydike/bias", "Rischio di bias"],
    ["eurydike/consenso", "Consenso"], ["eurydike/meta", "Meta-analisi"], ["eurydike/esporta", "Esporta"], null,
    ["funzioni", "Funzioni"], ["condivisione", "Condivisione"], ["sistema", "Sistema"]],
};
let moduleNow = "orpheus";
let moduleShown = null;
let navTodo;
const EUR_MARK = "M10.4 11H21.6M16 16.6V27M11.5 22H20.5";
const FAV = {
  orpheus: document.querySelector("link[rel=icon]") ? document.querySelector("link[rel=icon]").getAttribute("href") : "",
  eurydike: "data:image/svg+xml," + encodeURIComponent(`<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='7' fill='#b3261e'/><g fill='none' stroke='white' stroke-width='2.4' stroke-linecap='round' stroke-linejoin='round'><circle cx='16' cy='11' r='5.6'/><path d='${EUR_MARK}'/></g></svg>`),
};
function turnMark() {
  const mark = document.getElementById("brand-mark");
  if (!mark) return;
  mark.classList.remove("turn-right", "turn-left");
  void mark.getBoundingClientRect();
  mark.classList.add(moduleNow === "eurydike" ? "turn-right" : "turn-left");
}
function buildNav() {
  const ul = document.getElementById("nav");
  ul.replaceChildren();
  for (const it of NAV[moduleNow]) {
    if (!it) { ul.append(h("li", {class: "sep", "aria-hidden": "true"})); continue; }
    const key = it[0].startsWith("eurydike/") ? it[0].slice(9) : null;
    ul.append(h("li", null, h("a", {href: `#/${it[0]}`, "data-sec": it[0]}, h("span", null, t(it[1])),
      key && key !== "panoramica" ? h("span", {class: "nav-n", "data-count": key}) : null)));
  }
  document.body.dataset.module = moduleNow;
  for (const b of document.querySelectorAll("[data-module-set]")) b.setAttribute("aria-pressed", String(b.dataset.moduleSet === moduleNow));
  document.getElementById("brand-name").textContent = moduleNow === "eurydike" ? "EURYDIKE" : "ORPHEUS";
  document.getElementById("brand-sub").textContent = moduleNow === "eurydike" ? t("revisioni sistematiche e meta-analisi") : "";
  const fav = document.querySelector("link[rel=icon]");
  if (fav && FAV[moduleNow]) fav.setAttribute("href", FAV[moduleNow]);
  if (moduleNow === "eurydike" && navTodo !== undefined) setNavCounts(navTodo);
  if (moduleShown && moduleShown !== moduleNow) turnMark();
  moduleShown = moduleNow;
}
for (const b of document.querySelectorAll("[data-module-set]")) b.addEventListener("click", () => {
  go(b.dataset.moduleSet === "eurydike" ? "eurydike/panoramica" : "panoramica");
});

const EUR_PHASES = [["1 · Protocollo", "domanda, PICOS, criteri"], ["2 · Importazione", "RIS, PubMed, WoS, CSV; doppioni"],
  ["3 · Screening", "lotti da 25; tu + Claude"], ["4 · Full text", "includi / escludi con motivo"],
  ["5 · Estrazione", "scheda: Conferma / Correggi / Non riportato"], ["6 · Rischio di bias", "strumento per studio"],
  ["7 · Meta-analisi", "forest e funnel"], ["8 · Bozza", "Metodi e Risultati in Word"]];
const EUR_TITLES = {protocollo: "Protocollo", screening: "Screening", fulltext: "Full text", estrazione: "Estrazione (schede)",
  bias: "Rischio di bias", consenso: "Consenso", meta: "Meta-analisi", esporta: "Esporta"};
/* ------------------------------------------------------------------ EURYDIKE · Estrazione (schede), T-051o */
const STATE_TAG = {ai: ["da controllare", "warn"], confirmed: ["confermato", "good"], edited: ["corretto a mano", "acc"],
  nr: ["non riportato", ""]};
const QUOTE_TAG = {exact: ["frase esatta", "good"], fuzzy: ["frase simile", "warn"], not_found: ["frase non trovata", "crit"], none: ["senza frase", ""]};
const STATS_LABELS = [["n", "n"], ["mean", "media"], ["sd", "DS"], ["median", "mediana"], ["q1", "Q1"], ["q3", "Q3"],
  ["min", "min"], ["max", "max"], ["se", "ES"], ["ci_low", "IC basso"], ["ci_high", "IC alto"]];
const cardState = {sort: "da_controllare", dir: -1, filter: "tutti"};
function hashParam(name) {
  const q = location.hash.split("?")[1] || "";
  return new URLSearchParams(q).get(name);
}
async function cardWrite(aid, body) {
  return api(`/api/eurydike/schede/${encodeURIComponent(aid)}/decisione`, {method: "POST",
    headers: {"X-Orpheus-Token": TOKEN, "Content-Type": "application/json"}, body: JSON.stringify(body)});
}
function field(label, input) { return h("label", {class: "fld"}, h("span", null, label), input); }
function quoteCell(x) {
  const [qt, qk] = QUOTE_TAG[x.controllo_citazione] || [x.controllo_citazione, ""];
  return h("div", {class: "quote-cell"}, x.citazione ? h("q", null, x.citazione) : h("span", {class: "muted"}, "—"),
    h("div", null, x.pagina ? `p. ${x.pagina}` : (x.sezione !== null && x.sezione !== undefined ? `sezione ${x.sezione}` : ""), " ",
      x.citazione ? tag(qt, qk) : null));
}
async function historyBox(aid, params) {
  const box = h("div", {class: "history"}, loading("Lettura della storia…"));
  try {
    const d = await api(`/api/eurydike/schede/${encodeURIComponent(aid)}/storia?${new URLSearchParams(params)}`);
    put(box, h("ol", null, d.storia.map((r) => {
      const [st] = STATE_TAG[r.state] || [r.state];
      const nums = STATS_LABELS.filter(([k]) => r[k] !== undefined && r[k] !== null).map(([k, l]) => `${l} ${r[k]}`).join(" · ");
      return h("li", null, h("b", null, r.value ?? (nums || "—")), " ", r.unit || "", " — ", st, ", ",
        ({human: "tu", claude_session: "Claude", chat_manuale: "chat"})[r.actor] || r.actor, ", ", fmtDate(r.created_at) || "", r.note ? ` · ${r.note}` : "");
    })));
  } catch (e) { put(box, h("span", {class: "error"}, e.message)); }
  return box;
}
function editForm(x, isRow, onSave, onCancel) {
  const inputs = {};
  const mk = (k, v, attrs) => (inputs[k] = h("input", {type: "text", value: v ?? "", ...(attrs || {})}));
  const kids = isRow
    ? [h("div", {class: "fld-grid"}, STATS_LABELS.map(([k, l]) => field(l, mk(k, x[k], {inputmode: "decimal", size: 6})))),
       field("Unità", mk("unita", x.unita))]
    : [field("Valore corretto", mk("valore", x.valore)), field("Unità", mk("unita", x.unita))];
  kids.push(field("Frase dell'articolo (copiata dal PDF)", mk("citazione", x.citazione)),
    field("Pagina", mk("pagina", x.pagina, {inputmode: "numeric", size: 4})), field("Nota", mk("nota", "")));
  const msg = h("p", {class: "error", role: "alert", hidden: true});
  const form = h("form", {class: "edit-form", onsubmit: async (e) => {
    e.preventDefault();
    const body = {};
    for (const [k, el] of Object.entries(inputs)) body[k] = el.value;
    try { await onSave(body); } catch (err) { msg.hidden = false; msg.textContent = err.message; }
  }}, kids, msg, h("div", {class: "action-row"}, h("button", {type: "submit", class: "btn primary"}, "Salva la correzione"), " ",
    h("button", {type: "button", class: "btn", onclick: onCancel}, "Annulla")),
    h("p", {class: "src"}, "Il valore vecchio resta nella storia; nel file esportato il valore è segnato come corretto a mano."));
  setTimeout(() => (isRow ? inputs.n : inputs.valore).focus(), 0);
  return form;
}
async function cardsList() {
  const view = liveView;
  const d = await get("/api/eurydike/schede", true);
  const columns = [
    {key: "titolo", label: "Articolo", sort: true, cell: (r) => h("div", null, h("b", null, r.titolo || r.id), h("div", {class: "muted"}, [r.anno, shortDoi(r.doi)].filter(Boolean).join(" · ")))},
    {key: "valori", label: "Valori", num: true, sort: true, cell: (r) => fmtN(r.valori)},
    {key: "righe_esito", label: "Righe di esito", num: true, sort: true, cell: (r) => fmtN(r.righe_esito)},
    {key: "da_controllare", label: "Da controllare", num: true, sort: true, cell: (r) => r.da_controllare ? tag(fmtN(r.da_controllare), "warn") : tag("0", "good")},
    {key: "confermati", label: "Confermati", num: true, sort: true, cell: (r) => fmtN(r.confermati)},
    {key: "corretti_a_mano", label: "Corretti", num: true, sort: true, cell: (r) => fmtN(r.corretti_a_mano)},
    {key: "non_riportati", label: "Non riportati", num: true, sort: true, cell: (r) => fmtN(r.non_riportati)}];
  const holder = h("div");
  const render = () => {
    const rows = sortRows(d.schede.filter((r) => cardState.filter === "tutti" || r.da_controllare > 0), cardState, columns);
    put(holder, rows.length ? sortableTable(columns, rows, {state: cardState, rerender: render,
      onRow: (r) => go(`eurydike/estrazione?a=${encodeURIComponent(r.id)}`)}) : h("p", {class: "muted"}, "Nessuna scheda da mostrare."));
  };
  const todo = d.schede.reduce((a, r) => a + r.da_controllare, 0);
  put(view, ...head(t("Estrazione (schede)"), `${fmtN(d.schede.length)} schede, ${fmtN(todo)} valori da controllare. Apri una scheda per confermare, correggere o segnare come non riportato ogni valore.`),
    h("div", {class: "action-row"}, seg([["tutti", "Tutte"], ["da", "Con valori da controllare"]], cardState.filter, (v) => { cardState.filter = v; cardsList(); }, "Filtro")),
    holder, srcLine(d));
  render();
}
async function cardDetail(aid) {
  const view = liveView;
  let d = await api(`/api/eurydike/schede/${encodeURIComponent(aid)}`);
  const status = h("div", {class: "state-line", role: "status"});
  const valuesBox = h("div"), rowsBox = h("div"), headBox = h("div");
  const done = (res, text) => { d = res.scheda; clearCache(); put(status, tag(text, "good")); draw(); };
  const fail = (e) => put(status, h("span", {class: "error"}, e.message));
  function actionsFor(x, isRow, tr, colspan) {
    const key = isRow ? {esito: x.esito, gruppo: x.gruppo, tempo: x.tempo} : {campo: x.campo, ordine: x.ordine};
    const label = isRow ? `${x.esito}, ${x.gruppo}, ${x.tempo}` : (FIELD[x.campo] || x.campo);
    const after = (box) => { const extra = h("tr", {class: "row-extra"}, h("td", {colspan}, box)); tr.after(extra); return extra; };
    const confirmBtn = h("button", {type: "button", class: "btn small", disabled: (x.stato === "confirmed" || x.stato === "nr" || (!isRow && x.valore === null)) || null,
      "aria-label": `Conferma: ${label}`, onclick: () => cardWrite(aid, {azione: "conferma", ...key}).then((r) => done(r, `Confermato: ${label}`), fail)}, "Conferma");
    const editBtn = h("button", {type: "button", class: "btn small", "aria-label": `Correggi: ${label}`, onclick: () => {
      if (tr.nextElementSibling && tr.nextElementSibling.classList.contains("row-extra")) tr.nextElementSibling.remove();
      const extra = after(editForm(x, isRow, async (body) => done(await cardWrite(aid, {azione: "correggi", ...key, ...body}), `Corretto: ${label}`),
        () => { extra.remove(); editBtn.focus(); }));
    }}, "Correggi");
    const nrBtn = isRow ? null : h("button", {type: "button", class: "btn small", disabled: x.stato === "nr" || null, "aria-label": `Non riportato: ${label}`,
      onclick: () => cardWrite(aid, {azione: "nr", ...key}).then((r) => done(r, `Segnato non riportato: ${label}`), fail)}, "Non riportato");
    const histBtn = h("button", {type: "button", class: "linklike", "aria-label": `Storia: ${label}`, onclick: async () => {
      if (tr.nextElementSibling && tr.nextElementSibling.classList.contains("row-extra")) { tr.nextElementSibling.remove(); return; }
      after(await historyBox(aid, key));
    }}, "Storia");
    return h("div", {class: "card-actions"}, confirmBtn, editBtn, nrBtn, histBtn);
  }
  function draw() {
    const r = d.riepilogo;
    put(headBox, ...head(d.articolo.titolo || d.articolo.id,
      `${fmtN(r.valori)} valori e ${fmtN(r.righe_esito)} righe di esito: ${fmtN(r.da_controllare)} da controllare, ${fmtN(r.confermati)} confermati, ${fmtN(r.corretti_a_mano)} corretti a mano, ${fmtN(r.non_riportati)} non riportati.`),
      h("div", {class: "action-row"},
        h("button", {type: "button", class: "btn primary", disabled: !d.esatte_da_confermare || null, onclick: () =>
          cardWrite(aid, {azione: "conferma_esatte"}).then((res) => done(res, `Confermati ${res.esito.confermati} valori con la frase esatta`), fail)},
          `Conferma tutti quelli con la frase esatta (${fmtN(d.esatte_da_confermare)})`), " ",
        h("button", {type: "button", class: "btn", onclick: () => cardWrite(aid, {azione: "esporta"}).then((res) =>
          { d = res.scheda; put(status, tag(`Esportata: ${res.esito.json} e .csv`, "good")); }, fail)}, "Esporta JSON + CSV")), status);
    const cols = 4;
    const tbody = h("tbody");
    for (const x of d.valori) {
      const [st, sk] = STATE_TAG[x.stato] || [x.stato, ""];
      const tr = h("tr");
      append(tr, [h("th", {scope: "row"}, FIELD[x.campo] || x.campo, x.ordine > 1 ? ` (${x.ordine})` : "", x.nome ? h("div", {class: "muted"}, x.nome) : null),
        h("td", null, x.valore !== null ? h("b", null, x.valore) : h("span", {class: "muted"}, "—"), x.unita ? ` ${x.unita}` : "", x.gruppo ? h("div", {class: "muted"}, x.gruppo) : null,
          h("div", null, tag(st, sk)), x.nota ? h("div", {class: "muted"}, x.nota) : null),
        h("td", null, quoteCell(x))]);
      tr.append(h("td", null, actionsFor(x, false, tr, cols)));
      tbody.append(tr);
    }
    put(valuesBox, h("h2", null, "Valori"), d.valori.length ? h("div", {class: "table-wrap"}, h("table", {class: "card-table"},
      h("thead", null, h("tr", null, ["Campo", "Valore", "Frase e pagina", "Azione"].map((c) => h("th", {scope: "col"}, c)))), tbody))
      : h("p", {class: "muted"}, "Nessun valore."));
    const rb = h("tbody");
    for (const x of d.righe_esito) {
      const [st, sk] = STATE_TAG[x.stato] || [x.stato, ""];
      const nums = STATS_LABELS.filter(([k]) => x[k] !== null && x[k] !== undefined).map(([k, l]) => `${l} ${x[k]}`).join(" · ");
      const tr = h("tr");
      append(tr, [h("th", {scope: "row"}, x.esito, h("div", {class: "muted"}, `${x.gruppo} · ${x.tempo}`)),
        h("td", null, h("b", null, nums || "—"), x.unita ? ` ${x.unita}` : "", h("div", null, tag(st, sk))), h("td", null, quoteCell(x))]);
      tr.append(h("td", null, actionsFor(x, true, tr, cols)));
      rb.append(tr);
    }
    put(rowsBox, d.righe_esito.length ? h("section", {class: "block"}, h("h2", null, "Righe di esito"), h("div", {class: "table-wrap"}, h("table", {class: "card-table"},
      h("thead", null, h("tr", null, ["Esito", "Numeri", "Frase e pagina", "Azione"].map((c) => h("th", {scope: "col"}, c)))), rb))) : null);
  }
  put(view, h("a", {class: "linklike", href: "#/eurydike/estrazione"}, "← Tutte le schede"), headBox,
    h("section", {class: "block"}, valuesBox), rowsBox,
    h("p", {class: "src"}, "Conferma in blocco solo per le frasi trovate esattamente nel testo. Ogni modifica è una riga nuova: niente si cancella. L'originale (cards-v1) non si tocca."),
    srcLine(d));
  draw();
}

/* ------------------------------------------------------------------ EURYDIKE · revisioni (T-051n) */
const EUR_FT = {pending: ["da decidere", "warn"], include: ["incluso", "good"], exclude: ["escluso", "crit"],
  not_retrieved: ["non reperito", ""]};
const EUR_DEC = {include: ["includi", "good"], exclude: ["escludi", "crit"], maybe: ["forse", "warn"], unsure: ["incerto", "warn"]};
const EUR_STATUS = {pending: ["da decidere", ""], partial: ["un solo revisore", "warn"], conflict: ["conflitto", "crit"],
  agreed: ["d'accordo", "good"], resolved: ["consenso", "acc"], doppione: ["doppione", ""]};
const EUR_FILTERS = [["tutti", "Tutti"], ["da_decidere", "Da decidere"], ["parziali", "Un solo revisore"],
  ["conflitti", "Conflitti"], ["inclusi", "Inclusi"], ["esclusi", "Esclusi"], ["forse", "Forse"], ["doppioni", "Doppioni"]];
const EUR_SECS = [["protocollo", "Protocollo", "domanda, PICOS, criteri, importazioni"],
  ["screening", "Importazione e screening", "record, doppioni, decisioni"], ["fulltext", "Full text", "includi / escludi con motivo"],
  ["estrazione", "Estrazione", "scheda: Conferma / Correggi / Non riportato"], ["bias", "Rischio di bias", "strumento per studio"],
  ["consenso", "Consenso", "conflitti e accordo tu–Claude"], ["meta", "Meta-analisi", "forest e funnel"],
  ["esporta", "Esporta", "PRISMA, studi, decisioni"]];
const PRISMA_ROWS = [["identified", "Record identificati"], ["duplicates", "Doppioni tolti"], ["screened", "Record esaminati"],
  ["excluded", "Esclusi allo screening"], ["sought", "Cercati per intero"], ["not_retrieved", "Non reperiti (senza PDF)"],
  ["assessed", "Valutati per intero"], ["ft_excluded", "Esclusi al full text"], ["included", "Studi inclusi"]];
const eurState = {rid: null, filtro: "tutti", q: "", sort: "studio", dir: 1};
try { eurState.rid = localStorage.getItem("eurydike-revisione"); } catch (e) { eurState.rid = null; }

function setNavCounts(todo) {
  navTodo = todo;
  for (const el of document.querySelectorAll("#nav [data-count]")) {
    const v = todo ? todo[el.dataset.count] : undefined;
    el.textContent = todo ? (v ? fmtN(v) : "0") : "—";
    el.classList.toggle("has", !!(todo && v));
    el.title = todo ? (v ? `${fmtN(v)} ${t("da fare")}` : t("niente da fare")) : t("nessuna revisione");
  }
}
async function eurLoad() {
  const list = await get("/api/eurydike/revisioni", true);
  const want = hashParam("r") || eurState.rid;
  const pick = list.revisioni.find((r) => r.id === want) || list.revisioni[0] || null;
  if (!pick) { setNavCounts(null); return {list, cur: null}; }
  if (eurState.rid !== pick.id) { eurState.rid = pick.id; try { localStorage.setItem("eurydike-revisione", pick.id); } catch (e) { /* not available */ } }
  const cur = await get(`/api/eurydike/revisioni/${encodeURIComponent(pick.id)}`, true);
  setNavCounts(cur.da_fare);
  return {list, cur};
}
function eurBar(list, cur, sub) {
  const r = cur.revisione;
  const sel = list.revisioni.length > 1 ? h("label", {class: "fld inline"}, h("span", null, t("Revisione")),
    h("select", {onchange: (e) => go(`eurydike/${sub}?r=${encodeURIComponent(e.target.value)}`)},
      list.revisioni.map((x) => h("option", {value: x.id, selected: x.id === r.id ? true : null}, x.titolo)))) : null;
  return h("div", {class: "eur-bar"}, h("span", {class: "muted"}, t("Revisione")), " ", h("b", null, r.titolo), " ",
    tag(t(r.tipo), "acc"), " ", tag(`${t("fase")}: ${t(r.stato)}`), sel, h("a", {class: "btn small", href: "#/eurydike/nuova"}, t("+ Nuova revisione")));
}
function eurEmpty(sub) {
  const back = h("a", {class: "linklike", href: "#/panoramica"}, t("← Torna a ORPHEUS"));
  if (sub !== "panoramica") {
    return [back, ...head(t(EUR_TITLES[sub] || "EURYDIKE"), t("Nessuna revisione ancora: questa fase si riempie quando c'è una revisione.")),
      h("p", null, h("a", {href: "#/eurydike/panoramica"}, t("Vai alla panoramica della revisione")))];
  }
  return [back, ...head(t("Nessuna revisione ancora"), t("Claude propone, tu disponi. Ogni dato ha frase e pagina; i numeri li calcola il codice.")),
    h("div", {class: "action-row", style: "margin-bottom:16px"}, h("a", {class: "btn module", href: "#/eurydike/nuova"}, t("Nuova revisione")), " ",
      h("a", {class: "btn", href: "#/manoscritti", title: t("Il controllo di una revisione pubblicata o in scrittura si fa con revisione_sistematica.cmd")}, t("Controlla una revisione (.docx)"))),
    h("section", {class: "block"}, h("h2", null, t("Le fasi")),
      h("div", {class: "phases"}, EUR_PHASES.map(([a, b]) => h("div", {class: "phase"}, h("b", null, t(a)), h("span", null, t(b)))))),
    h("ul", {class: "checks principles"}, ["Claude propone, tu disponi", "Ogni dato con frase e pagina", "Claude è il 2° revisore, in cieco",
      "Numeri calcolati, mai digitati"].map((x) => h("li", null, tag(t(x), "acc"))))];
}
function kappaText(k) {
  if (!k) return t("Accordo: serve almeno un record deciso da due revisori.");
  const who = k.uno_e_claude ? t("tu e Claude") : `${k.tra[0]} e ${k.tra[1]}`;
  return `${t("Accordo")} ${who}: κ = ${k.kappa.toFixed(2).replace(".", ",")} (${t(k.label)}), ${t("su")} ${fmtN(k.n)} ${t("record decisi da entrambi")}; ${t("accordo osservato")} ${Math.round(k.po * 100)}%.`;
}
function prismaFlow(p) {
  const box = (key, label, side) => h("div", {class: `pf-box${side ? " pf-aside" : ""}`}, h("span", {class: "pf-n"}, fmtN(p[key])), h("span", null, t(label)));
  return h("div", {class: "pf", role: "group", "aria-label": t("Diagramma PRISMA 2020")},
    h("div", {class: "pf-row"}, box("identified", "Record identificati"), box("duplicates", "Doppioni tolti", true)),
    h("div", {class: "pf-row"}, box("screened", "Record esaminati"), box("excluded", "Esclusi allo screening", true)),
    h("div", {class: "pf-row"}, box("sought", "Cercati per intero"), box("not_retrieved", "Non reperiti (senza PDF)", true)),
    h("div", {class: "pf-row"}, box("assessed", "Valutati per intero"), box("ft_excluded", "Esclusi al full text", true)),
    h("div", {class: "pf-row"}, box("included", "Studi inclusi")));
}
function eurOverview(list, cur) {
  const r = cur.revisione, p = cur.prisma;
  const kpi = (v, l, sec) => h("button", {type: "button", class: "kpi", onclick: () => go(`eurydike/${sec}`)},
    h("div", {class: "kpi-v"}, fmtN(v)), h("div", {class: "kpi-l"}, t(l)));
  return [h("a", {class: "linklike", href: "#/panoramica"}, t("← Torna a ORPHEUS")), eurBar(list, cur, "panoramica"),
    ...head(r.titolo, r.domanda),
    h("div", {class: "kpis"}, kpi(p.identified, "record identificati", "protocollo"), kpi(p.duplicates, "doppioni", "screening"),
      kpi(p.screened, "record esaminati", "screening"), kpi(p.sought, "studi cercati per intero", "fulltext"),
      kpi(p.included, "studi inclusi", "fulltext")),
    h("div", {class: "cols"},
      h("section", {class: "block", "aria-labelledby": "h-pf"}, h("h2", {id: "h-pf"}, t("Diagramma PRISMA 2020")),
        prismaFlow(p),
        p.problemi.length ? h("ul", {class: "notes"}, p.problemi.map((x) => h("li", null, tag(t("da guardare"), "crit"), " ", x)))
          : h("p", null, tag(t("i numeri tornano"), "good")),
        srcLine(p, t("Ogni numero è contato dai dati, mai scritto a mano."))),
      h("section", {class: "block", "aria-labelledby": "h-ph"}, h("h2", {id: "h-ph"}, t("Le fasi")),
        h("ol", {class: "flow"}, EUR_SECS.map(([sec, a, b]) => h("li", null, h("button", {type: "button", class: "flow-step", onclick: () => go(`eurydike/${sec}`)},
          h("span", {class: "flow-t"}, t(a)), h("span", {class: "flow-n"}, t(b)),
          cur.da_fare[sec] ? tag(`${fmtN(cur.da_fare[sec])} ${t("da fare")}`, "warn") : tag(t("niente da fare"), "good"))))),
        h("p", {class: "src"}, kappaText(cur.screening.kappa)))),
    srcLine(cur)];
}
function eurProtocol(list, cur) {
  const r = cur.revisione, pr = cur.protocollo;
  const crit = (kind, letter) => {
    const now = pr.criteri.filter((c) => c.tipo === kind && !c.sostituito_da);
    const old = pr.criteri.filter((c) => c.tipo === kind && c.sostituito_da);
    return [now.length ? h("ol", {class: "crit-list"}, now.map((c) => h("li", null, h("b", null, `${letter}${c.ord}`), " ", h("span", {lang: "en"}, c.testo),
      c.regola ? h("code", null, ` ${JSON.stringify(c.regola)}`) : null))) : h("p", {class: "empty"}, t("Nessuno.")),
      old.length ? h("details", null, h("summary", null, `${t("Sostituiti")} (${old.length})`),
        h("ul", {class: "notes"}, old.map((c) => h("li", null, h("s", null, `${letter}${c.ord} ${c.testo}`), " ", h("span", {class: "muted"}, fmtDate(c.creato, false)))))) : null];
  };
  const pico = Object.entries(r.pico || {});
  return [eurBar(list, cur, "protocollo"), ...head(t("Protocollo"), t("Domanda, PICOS e criteri: un criterio non si cambia, uno nuovo lo sostituisce e il vecchio resta nella storia.")),
    searchBlock(cur),
    h("div", {class: "cols"},
      h("section", {class: "block"}, h("h2", null, t("Domanda")), h("p", {lang: "en"}, r.domanda),
        h("dl", {class: "kv"}, pico.map(([k, v]) => [h("dt", null, k), h("dd", {lang: "en"}, String(v))]),
          h("dt", null, t("Registrazione")), h("dd", null, r.registrazione || "—"),
          h("dt", null, t("Revisori allo screening")), h("dd", null, fmtN(r.revisori_screening)),
          h("dt", null, t("Cieco")), h("dd", null, `${r.cieco ? t("sì") : t("no")} (${t("revisori")}), ${r.cieco_ai ? t("sì") : t("no")} (Claude)`),
          h("dt", null, t("Cartella")), h("dd", null, h("code", null, r.cartella))),
        r.strategia ? h("details", null, h("summary", null, t("Strategia di ricerca")), h("pre", {class: "pre", lang: "en"}, r.strategia)) : null),
      h("section", {class: "block"}, h("h2", null, t("Criteri di inclusione")), crit("inclusion", "I"),
        h("h2", null, t("Criteri di esclusione")), crit("exclusion", "E"),
        pr.criteri.some((c) => c.tipo === "prefilter") ? [h("h2", null, t("Prefiltri")), crit("prefilter", "P")] : null)),
    h("section", {class: "block"}, h("h2", null, t("Importazioni")),
      pr.importazioni.length ? h("div", {class: "table-wrap"}, h("table", null,
        h("thead", null, h("tr", null, ["File", "Banca dati", "Formato", "Cercato il", "Record", "Doppioni", "Importato il"].map((x, i) => h("th", {scope: "col", class: i > 3 && i < 6 ? "n" : null}, t(x))))),
        h("tbody", null, pr.importazioni.map((i) => h("tr", null, h("td", null, i.fonte_file), h("td", null, i.banca_dati), h("td", null, i.formato.toUpperCase()),
          h("td", null, i.cercato_il || "—"), h("td", {class: "n"}, fmtN(i.record)), h("td", {class: "n"}, fmtN(i.doppioni)), h("td", null, fmtDate(i.importato_il))))))) 
        : h("p", {class: "empty"}, t("Nessuna importazione."))),
    h("div", {class: "cols"},
      h("section", {class: "block"}, h("h2", null, t("Revisori")), h("ul", {class: "notes"}, pr.revisori.map((v) => h("li", null, h("b", null, v.nome), " ", tag(t(v.tipo)), v.modello ? h("span", {class: "muted"}, ` ${v.modello}`) : null)))),
      h("section", {class: "block"}, h("h2", null, t("Registro delle deviazioni e delle note")),
        pr.registro.length ? h("ul", {class: "notes"}, pr.registro.map((g) => h("li", null, tag(t(g.tipo)), " ", h("b", null, g.titolo), g.perche ? ` — ${g.perche}` : "",
          h("span", {class: "muted"}, ` (${g.revisore}, ${fmtDate(g.data, false)}${g.record !== null ? `, ${fmtN(g.record)} record` : ""})`)))) : h("p", {class: "empty"}, t("Nessuna nota.")))),
    srcLine(pr)];
}
function decisionTags(r) {
  return h("div", {class: "checks"}, r.decisioni.length ? r.decisioni.map((d) => {
    const [txt, k] = EUR_DEC[d.decisione] || [d.decisione, ""];
    return h("span", {class: "dec", title: d.motivo || null}, h("span", {class: "muted"}, `${d.revisore}: `), tag(t(txt), k), d.criterio ? h("span", {class: "muted"}, ` E${d.criterio}`) : null);
  }) : h("span", {class: "muted"}, "—"));
}
function recordCell(r) {
  return h("div", null, h("b", {lang: "en"}, r.titolo), h("div", {class: "muted"}, [r.autori ? r.autori.split(";")[0] + (r.autori.includes(";") ? " et al." : "") : null, r.anno, r.rivista, shortDoi(r.doi)].filter(Boolean).join(" · ")));
}
function finalTag(r) {
  const [txt, k] = EUR_STATUS[r.stato] || [r.stato, ""];
  if (r.finale && (r.stato === "agreed" || r.stato === "resolved")) {
    const [d, dk] = EUR_DEC[r.finale] || [r.finale, ""];
    return h("span", null, tag(t(d), dk), " ", h("span", {class: "muted"}, `(${t(txt)})`));
  }
  return tag(t(txt), k);
}
async function eurScreening(list, cur) {
  const sc = cur.screening;
  const holder = h("div"), more = h("div");
  let shown = [];
  const load = async (from) => {
    const qs = new URLSearchParams({filtro: eurState.filtro, q: eurState.q, da: String(from), quanti: "100"});
    const d = await api(`/api/eurydike/revisioni/${encodeURIComponent(cur.revisione.id)}/record?${qs}`);
    shown = from ? shown.concat(d.righe) : d.righe;
    put(holder, shown.length ? h("div", {class: "table-wrap"}, h("table", null,
      h("thead", null, h("tr", null, h("th", {scope: "col"}, t("Record")), h("th", {scope: "col"}, t("Decisioni")), h("th", {scope: "col"}, t("Decisione finale")))),
      h("tbody", null, shown.map((r) => h("tr", null, h("td", null, recordCell(r)), h("td", null, decisionTags(r)), h("td", null, r.doppione_di ? tag(t("doppione")) : finalTag(r))))))) 
      : h("p", {class: "empty"}, t("Nessun record con questo filtro.")));
    put(more, h("p", {class: "muted"}, `${fmtN(shown.length)} ${t("di")} ${fmtN(d.totale)} ${t("record")}`),
      shown.length < d.totale ? h("button", {type: "button", class: "btn", onclick: () => load(shown.length)}, t("Mostra altri 100")) : null);
  };
  const chips = h("div", {class: "checks", role: "group", "aria-label": t("Filtro")}, EUR_FILTERS.map(([k, l]) =>
    h("button", {type: "button", class: "chip", "aria-pressed": String(eurState.filtro === k), onclick: (e) => {
      eurState.filtro = k; for (const b of e.currentTarget.parentNode.children) b.setAttribute("aria-pressed", String(b === e.currentTarget)); load(0);
    }}, t(l), h("span", {class: "muted"}, ` ${fmtN(sc.conteggi[k])}`))));
  const search = h("input", {type: "search", placeholder: t("Cerca nel titolo, autori, rivista, DOI"), value: eurState.q,
    "aria-label": t("Cerca nei record"), oninput: debounce((e) => { eurState.q = e.target.value; load(0); }, 300)});
  await load(0);
  return [eurBar(list, cur, "screening"), ...head(t("Screening"), t("Titolo e abstract: la decisione finale è calcolata dalle decisioni dei revisori (due d'accordo, oppure il consenso). Due \"forse\" sono un conflitto: decide il full text.")),
    h("p", {class: "notice"}, t("Sola lettura: le decisioni si scriveranno qui con la fase di screening (T-051e).")),
    h("div", {class: "toolbar"}, chips, h("div", {class: "search"}, search)),
    holder, more, h("p", {class: "src"}, kappaText(sc.kappa)), srcLine(sc)];
}
function proposalCell(p) {
  if (!p) return h("span", {class: "muted"}, "—");
  const [txt, k] = EUR_DEC[p.decisione] || [p.decisione, ""];
  return h("div", null, tag(`Claude: ${t(txt)}`, k), h("div", {lang: "en"}, p.sintesi),
    h("details", null, h("summary", null, t("Motivo")), h("p", {lang: "en"}, p.motivo), p.frase ? h("q", {lang: "en"}, p.frase) : null, p.pagina ? h("span", {class: "muted"}, ` p. ${p.pagina}`) : null));
}
function articleLink(aid, label) {
  return aid ? h("button", {type: "button", class: "linklike", onclick: () => openArticle(aid)}, label || t("apri l'articolo")) : h("span", {class: "muted"}, t("non in ORPHEUS"));
}
function eurFulltext(list, cur) {
  const f = cur.fulltext;
  const cols = [
    {key: "studio", label: t("Studio"), sort: true, cell: (r) => h("div", null, h("b", null, r.studio), r.titolo ? h("div", {class: "muted", lang: "en"}, r.titolo) : null)},
    {key: "articolo", label: t("Articolo di ORPHEUS"), cell: (r) => articleLink(r.articolo)},
    {key: "pagine", label: t("PDF"), num: true, sort: true, cell: (r) => r.pagine ? `${fmtN(r.pagine)} p.` : tag(t("manca"), "warn")},
    {key: "proposta", label: t("Proposta di Claude"), cell: (r) => proposalCell(r.proposta)},
    {key: "decisione", label: t("Decisione"), sort: true, cell: (r) => { const [x, k] = EUR_FT[r.decisione] || [r.decisione, ""];
      return h("div", null, tag(t(x), k), r.criterio ? h("div", {class: "muted"}, `E${r.criterio_ord} ${r.criterio}`) : null,
        r.nota ? h("div", null, h("i", null, r.nota)) : null, r.deciso_da ? h("div", {class: "muted"}, `${r.deciso_da}, ${fmtDate(r.deciso_il, false)}`) : null); }}];
  const holder = h("div");
  const render = () => put(holder, f.studi.length ? sortableTable(cols, sortRows(f.studi, eurState, cols), {state: eurState, rerender: render})
    : h("p", {class: "empty"}, t("Nessuno studio ancora: gli studi nascono dai record inclusi allo screening.")));
  render();
  return [eurBar(list, cur, "fulltext"), ...head(t("Full text"), t("La decisione sul full text la prendi tu (o il consenso): Claude propone soltanto, con la frase del PDF.")),
    holder, srcLine(f)];
}
async function eurExtraction(list, cur) {
  const view = liveView;
  const out = [];
  if (cur) {
    const e = cur.estrazione;
    out.push(eurBar(list, cur, "estrazione"), h("section", {class: "block"}, h("h2", null, t("Studi inclusi nella revisione")),
      e.studi.length ? h("ul", {class: "notes"}, e.studi.map((x) => h("li", null, h("b", null, x.studio), " ",
        x.scheda ? [tag(`${fmtN(x.scheda.valori)} ${t("valori")}`), " ", x.scheda.da_controllare ? tag(`${fmtN(x.scheda.da_controllare)} ${t("da controllare")}`, "warn") : tag(t("tutto controllato"), "good"), " ",
          h("a", {href: `#/eurydike/estrazione?a=${encodeURIComponent(x.articolo)}`}, t("apri la scheda"))]
          : tag(x.articolo ? t("scheda da scrivere") : t("non in ORPHEUS: scheda da scrivere"), "warn"))))
        : h("p", {class: "empty"}, t("Nessuno studio incluso al full text.")), srcLine(e)));
  }
  await cardsList();
  if (out.length) view.prepend(...out);
}
function judgementTag(j) {
  const cls = {low: "good", some: "warn", moderate: "warn", unclear: "warn", high: "crit", serious: "crit", critical: "crit"}[j] || "";
  const it = {low: "basso", some: "alcune preoccupazioni", moderate: "moderato", unclear: "non chiaro", high: "alto", serious: "serio", critical: "critico"}[j] || j;
  return tag(t(it), cls);
}
function eurBias(list, cur) {
  const b = cur.bias;
  const robCell = (x) => {
    if (!x) return tag(t("strumento da scegliere"), "warn");
    const sug = x.suggerito;
    return h("div", null, h("b", null, x.nome), h("div", {class: "muted"}, `${fmtN(x.giudicate)} / ${fmtN(x.voci)} ${t("domini giudicati")}`),
      sug ? (sug.stars !== undefined ? tag(`${sug.stars}/${sug.max} ★`) : sug.j ? [t("suggerito"), ": ", judgementTag(sug.j), sug.partial ? h("span", {class: "muted"}, ` (${t("parziale")})`) : null, sug.note ? h("div", {class: "muted"}, sug.note) : null] : tag(t("solo 'nessuna informazione'"))) : null);
  };
  const qualCell = (x) => {
    if (!x) return h("span", {class: "muted"}, "—");
    const p = x.punteggio;
    const txt = !p ? "" : p.empty ? t("non valutato") : p.kind === "points" ? `${p.pts}/${p.max} (${String(p.pct).replace(".", ",")}%) ${p.band || ""}` : p.kind === "count" ? `${p.yes}/${p.applicable} ${t("sì")}` : p.label;
    return h("div", null, h("b", null, x.nome), h("div", {class: "muted"}, `${fmtN(x.giudicate)} / ${fmtN(x.voci)} ${t("voci")}`), txt ? tag(txt) : null);
  };
  return [eurBar(list, cur, "bias"), ...head(t("Rischio di bias"), t("Uno strumento per studio (10 strumenti, uguali a Tabula v19). Il giudizio complessivo qui è solo un suggerimento calcolato: quello finale lo scrivi tu.")),
    b.studi.length ? h("div", {class: "table-wrap"}, h("table", null,
      h("thead", null, h("tr", null, h("th", {scope: "col"}, t("Studio")), h("th", {scope: "col"}, t("Rischio di bias")), h("th", {scope: "col"}, t("Qualità")))),
      h("tbody", null, b.studi.map((x) => h("tr", null, h("td", null, h("b", null, x.studio), h("div", null, articleLink(x.articolo))), h("td", null, robCell(x.rob)), h("td", null, qualCell(x.qualita)))))))
      : h("p", {class: "empty"}, t("Nessuno studio incluso al full text.")), srcLine(b)];
}
function eurConsensus(list, cur) {
  const c = cur.consenso;
  return [eurBar(list, cur, "consenso"), ...head(t("Consenso"), t("Dove i revisori non sono d'accordo decide il consenso; qui anche le proposte di Claude sul full text diverse dalla tua decisione.")),
    h("section", {class: "block"}, h("h2", null, t("Accordo allo screening")), h("p", null, kappaText(c.kappa)),
      h("p", {class: "src"}, t("κ di Cohen sulle decisioni ridotte a includi/escludi (forse = includi), come in Tabula; Claude è dichiarato come secondo revisore."))),
    h("section", {class: "block"}, h("h2", null, `${t("Conflitti allo screening")} (${fmtN(c.conflitti.length)})`),
      c.conflitti.length ? h("div", {class: "table-wrap"}, h("table", null,
        h("thead", null, h("tr", null, h("th", {scope: "col"}, t("Record")), h("th", {scope: "col"}, t("Decisioni e motivi")))),
        h("tbody", null, c.conflitti.map((r) => h("tr", null, h("td", null, recordCell(r)),
          h("td", null, r.decisioni.map((d) => h("div", null, h("b", null, `${d.revisore}: `), tag(t((EUR_DEC[d.decisione] || [d.decisione])[0]), (EUR_DEC[d.decisione] || ["", ""])[1]),
            d.criterio ? ` E${d.criterio}` : "", d.motivo ? h("span", {class: "muted", lang: "en"}, ` — ${d.motivo}`) : null))))))))
        : h("p", null, tag(t("nessun conflitto"), "good"))),
    h("section", {class: "block"}, h("h2", null, `${t("Full text: Claude diverso da te")} (${fmtN(c.full_text_diversi.length)})`),
      c.full_text_diversi.length ? h("ul", {class: "notes"}, c.full_text_diversi.map((x) => h("li", null, h("b", null, x.studio), ": ",
        t("tu"), " ", tag(t((EUR_FT[x.decisione] || [x.decisione])[0]), (EUR_FT[x.decisione] || ["", ""])[1]), ", Claude ",
        tag(t((EUR_DEC[x.proposta.decisione] || [x.proposta.decisione])[0]), (EUR_DEC[x.proposta.decisione] || ["", ""])[1]),
        x.nota ? h("span", {class: "muted"}, ` — ${x.nota}`) : null))) : h("p", null, tag(t("nessuna differenza"), "good"))),
    srcLine(c)];
}
function eurMeta(list, cur) {
  const m = cur.meta;
  const num = (v) => (v === null || v === undefined) ? "—" : Number(v).toFixed(2).replace(".", ",");
  return [eurBar(list, cur, "meta"), ...head(t("Meta-analisi"), t("MD o SMD con effetti fissi o casuali (DL, REML), IC di Wald o HKSJ: ogni analisi registrata con i suoi dati di partenza, ricalcolabile.")),
    m.analisi.length ? h("div", {class: "table-wrap"}, h("table", null,
      h("thead", null, h("tr", null, ["Esito", "Tempo", "Misura", "Modello", "IC", "k", "Stima [IC 95%]", "I²", "Registrata"].map((x) => h("th", {scope: "col"}, t(x))))),
      h("tbody", null, m.analisi.map((a) => h("tr", null, h("td", {lang: "en"}, a.esito), h("td", null, a.tempo || "—"), h("td", null, a.misura), h("td", null, a.modello),
        h("td", null, a.ic.toUpperCase()), h("td", {class: "n"}, fmtN(a.k)),
        h("td", null, a.risultato.estimate !== undefined ? `${num(a.risultato.estimate)} [${num(a.risultato.ci_low)}; ${num(a.risultato.ci_high)}]` : "—"),
        h("td", null, a.risultato.i2 !== undefined ? `${num(a.risultato.i2)}%` : "—"), h("td", null, fmtDate(a.creata, false)))))))
      : h("p", {class: "empty"}, t("Nessuna analisi registrata: le analisi arrivano con la fase 7 (E9, T-051j), dalle righe di esito confermate.")),
    srcLine(m)];
}
function csvOf(rows) {
  const esc = (v) => { const x = v === null || v === undefined ? "" : String(v); return /[";\n]/.test(x) ? `"${x.replace(/"/g, '""')}"` : x; };
  return "﻿" + rows.map((r) => r.map(esc).join(";")).join("\r\n") + "\r\n";
}
function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], {type: "text/csv;charset=utf-8"}));
  const a = h("a", {href: url, download: name});
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
function eurExport(list, cur) {
  const r = cur.revisione, p = cur.prisma;
  const stamp = new Date().toISOString().slice(0, 10);
  const slug = r.id;
  const msg = h("p", {class: "muted", role: "status"});
  const btn = (label, fn) => h("button", {type: "button", class: "btn", onclick: async () => {
    msg.textContent = t("Preparo il file…");
    try { await fn(); msg.textContent = t("File scaricato dal browser."); } catch (e) { msg.textContent = e.message; }
  }}, t(label));
  const records = async () => {
    const out = [["id", "titolo", "autori", "anno", "rivista", "doi", "pmid", "doppione_di", "decisioni", "finale", "stato"]];
    for (const f of ["tutti", "doppioni"]) {
      let from = 0, total = 1;
      while (from < total) {
        const d = await api(`/api/eurydike/revisioni/${encodeURIComponent(r.id)}/record?filtro=${f}&da=${from}&quanti=500`);
        total = d.totale;
        for (const x of d.righe) out.push([x.id, x.titolo, x.autori, x.anno, x.rivista, x.doi, x.pmid, x.doppione_di,
          x.decisioni.map((y) => `${y.revisore}=${y.decisione}${y.criterio ? ` E${y.criterio}` : ""}`).join(" | "), x.finale, x.stato]);
        from += d.righe.length;
        if (!d.righe.length) break;
      }
    }
    download(`${slug}_screening_${stamp}.csv`, csvOf(out));
  };
  return [eurBar(list, cur, "esporta"), ...head(t("Esporta"), t("File CSV scaricati dal browser (separatore punto e virgola, si aprono con Excel): il database non cambia.")),
    h("section", {class: "block"}, h("h2", null, t("Numeri PRISMA 2020")),
      h("div", {class: "table-wrap"}, h("table", null, h("tbody", null, PRISMA_ROWS.map(([k, l]) => h("tr", null, h("th", {scope: "row"}, t(l)), h("td", {class: "n"}, fmtN(p[k]))))))),
      srcLine(p)),
    h("div", {class: "action-row"},
      btn("Scarica i numeri PRISMA", async () => download(`${slug}_prisma_${stamp}.csv`, csvOf([["voce", "numero"], ...PRISMA_ROWS.map(([k, l]) => [l, p[k]])]))),
      btn("Scarica gli studi (full text)", async () => download(`${slug}_studi_${stamp}.csv`, csvOf([["studio", "articolo", "doi", "pagine_pdf", "decisione", "criterio", "nota", "deciso_da", "deciso_il", "proposta_claude"],
        ...cur.fulltext.studi.map((x) => [x.studio, x.articolo, x.doi, x.pagine, x.decisione, x.criterio, x.nota, x.deciso_da, x.deciso_il, x.proposta ? x.proposta.decisione : ""])]))),
      btn("Scarica le decisioni dello screening", records)),
    msg];
}

/* ------------------------------------------------------------------ EURYDIKE · nuova revisione e importazione (T-051d) */
function postJson(path, body) {
  return api(path, {method: "POST", headers: {"X-Orpheus-Token": TOKEN, "Content-Type": "application/json"}, body: JSON.stringify(body || {})});
}
async function eurNewForm() {
  const view = liveView;
  let tools = {rob: [], qualita: []};
  try { tools = await get("/api/eurydike/strumenti"); } catch (e) { /* the form works without the tools */ }
  let me = "";
  try { me = localStorage.getItem("eurydike-revisore") || ""; } catch (e) { me = ""; }
  const f = {};
  const inp = (k, attrs) => (f[k] = h("input", {type: "text", name: k, ...(attrs || {})}));
  const area = (k, rows, attrs) => (f[k] = h("textarea", {name: k, rows: String(rows), ...(attrs || {})}));
  const sel = (k, opts, value) => (f[k] = h("select", {name: k}, opts.map(([v, l]) => h("option", {value: v, selected: v === value ? true : null}, t(l)))));
  const msg = h("p", {class: "error", role: "alert", hidden: true});
  const form = h("form", {class: "new-review", onsubmit: async (e) => {
    e.preventDefault();
    const body = {};
    for (const [k, el] of Object.entries(f)) body[k] = el.value;
    msg.hidden = true;
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      const out = await postJson("/api/eurydike/nuova", body);
      try { localStorage.setItem("eurydike-revisore", body.revisore || ""); localStorage.setItem("eurydike-revisione", out.id); } catch (x) { /* */ }
      clearCache();
      go(`eurydike/protocollo?r=${encodeURIComponent(out.id)}`);
    } catch (err) { msg.hidden = false; msg.textContent = err.message; btn.disabled = false; }
  }},
    h("section", {class: "block"}, h("h2", null, t("La revisione")),
      h("div", {class: "form-grid"},
        field(t("Titolo"), inp("titolo", {required: true, lang: "en", placeholder: "e.g. Training load and running injuries"})),
        field(t("Tipo"), sel("tipo", [["systematic_review", "revisione sistematica"], ["meta_analysis", "meta-analisi"], ["scoping_review", "scoping review"]], "systematic_review"))),
      field(t("Domanda di ricerca (in inglese)"), area("domanda", 2, {required: true, lang: "en"})),
      h("div", {class: "form-grid five"}, ["P", "I", "C", "O", "S"].map((k) => field(k, inp(k, {lang: "en", placeholder: {P: "Population", I: "Intervention / exposure", C: "Comparison", O: "Outcome", S: "Study design"}[k]})))),
      h("div", {class: "form-grid"},
        field(t("Registrazione (PROSPERO, OSF)"), inp("registrazione", {placeholder: "CRD42…"})),
        field(t("Il tuo nome (revisore)"), inp("revisore", {value: me, placeholder: "Nome"})),
        field(t("Revisori allo screening"), sel("revisori_screening", [["2", "2 (tu + Claude)"], ["1", "1 (solo tu)"]], "2")))),
    h("section", {class: "block"}, h("h2", null, t("Criteri")), h("p", {class: "muted"}, t("Uno per riga, in inglese. I criteri non si cambiano: uno nuovo sostituisce il vecchio, che resta nella storia.")),
      h("div", {class: "form-grid"},
        field(t("Criteri di inclusione"), area("inclusione", 5, {required: true, lang: "en"})),
        field(t("Criteri di esclusione"), area("esclusione", 5, {required: true, lang: "en"})))),
    h("section", {class: "block"}, h("h2", null, t("Ricerca e strumenti")),
      field(t("Strategia di ricerca (le stringhe di ogni banca dati)"), area("strategia", 5, {lang: "en"})),
      h("div", {class: "form-grid"},
        field(t("Rischio di bias predefinito"), sel("rob", [["", "— da scegliere —"], ...tools.rob.map((x) => [x.id, x.nome])], "")),
        field(t("Qualità predefinita"), sel("qualita", [["", "— da scegliere —"], ...tools.qualita.map((x) => [x.id, x.nome])], ""))),
      h("p", {class: "src"}, t("Lo strumento si può cambiare per ogni studio."))),
    msg,
    h("div", {class: "action-row"}, h("button", {type: "submit", class: "btn primary"}, t("Crea la revisione")), " ",
      h("a", {class: "btn", href: "#/eurydike/panoramica"}, t("Annulla"))),
    h("p", {class: "src"}, t("ORPHEUS crea la cartella revisioni\\<nome>\\ sul disco dei dati, con protocollo.yaml e la cartella ricerca\\ dove metti i file esportati dalle banche dati. Prima di scrivere fa una copia di sicurezza se l'ultima è vecchia.")));
  put(view, h("a", {class: "linklike", href: "#/eurydike/panoramica"}, t("← Torna a EURYDIKE")),
    ...head(t("Nuova revisione"), t("Il protocollo: domanda, PICOS, criteri, registrazione, strategia di ricerca e strumenti predefiniti.")), form);
  setTimeout(() => f.titolo.focus(), 0);
}
function searchBlock(cur) {
  const box = h("section", {class: "block", "aria-labelledby": "h-search"});
  const fill = async () => {
    let d;
    try { d = await api(`/api/eurydike/revisioni/${encodeURIComponent(cur.revisione.id)}/ricerca`); }
    catch (e) { put(box, h("h2", {id: "h-search"}, t("File della ricerca")), h("p", {class: "error"}, e.message)); return; }
    const fresh = d.file.filter((x) => x.stato === "nuovo");
    const msg = h("p", {role: "status", class: "muted"});
    const STATO = {nuovo: ["da importare", "warn"], importato: ["importato", "good"], "non leggibile": ["non leggibile", "crit"]};
    put(box, h("div", {class: "block-head"}, h("h2", {id: "h-search"}, t("File della ricerca")),
        h("button", {type: "button", class: "btn", onclick: fill}, t("Aggiorna"))),
      h("p", {class: "muted"}, t("Esporta la ricerca da ogni banca dati (RIS, PubMed .nbib, Web of Science, CSV) e mettila in questa cartella:"), " ", h("code", null, d.cartella)),
      d.file.length ? h("div", {class: "table-wrap"}, h("table", null,
        h("thead", null, h("tr", null, ["File", "Banca dati", "Formato", "Record", "Stato"].map((x, i) => h("th", {scope: "col", class: i === 3 ? "n" : null}, t(x))))),
        h("tbody", null, d.file.map((x) => { const [st, k] = STATO[x.stato] || [x.stato, ""];
          return h("tr", null, h("td", null, x.file), h("td", null, x.banca_dati || "—"), h("td", null, x.formato ? x.formato.toUpperCase() : "—"),
            h("td", {class: "n"}, fmtN(x.record)), h("td", null, tag(t(st), k), x.errore ? h("div", {class: "muted"}, x.errore) : null,
              x.senza_titolo ? h("div", {class: "muted"}, `${fmtN(x.senza_titolo)} ${t("senza titolo: non entrano")}`) : null)); })))) 
        : h("p", {class: "empty"}, t("Nessun file nella cartella ricerca.")),
      fresh.length ? h("div", {class: "action-row"}, h("button", {type: "button", class: "btn primary", onclick: async (e) => {
        e.currentTarget.disabled = true; msg.textContent = t("Importazione in corso…");
        try {
          const out = await postJson(`/api/eurydike/revisioni/${encodeURIComponent(cur.revisione.id)}/importa`);
          clearCache();
          const lines = out.importati.map((x) => `${x.file}: ${fmtN(x.record)} record, ${fmtN(x.doppioni)} ${t("doppioni")}`).join(" · ");
          try { sessionStorage.setItem("eurydike-import", lines); } catch (x) { /* */ }
          route();
        } catch (err) { msg.textContent = err.message; e.currentTarget.disabled = false; }
      }}, `${t("Importa i file nuovi")} (${fresh.length})`)) : null, msg,
      h("p", {class: "src"}, t("Ogni record resta; un doppione (stesso DOI, PMID o titolo e anno) punta al primo. Lo stesso file non si importa due volte.")));
    let last = null;
    try { last = sessionStorage.getItem("eurydike-import"); sessionStorage.removeItem("eurydike-import"); } catch (x) { last = null; }
    if (last) box.insertBefore(h("p", {class: "notice", role: "status"}, `${t("Importati")}: ${last}`), box.children[1]);
  };
  box.append(loading());
  fill();
  return box;
}

async function eurydikeSection(sub) {
  const view = liveView;
  if (sub === "nuova") return eurNewForm();
  const {list, cur} = await eurLoad();
  if (sub === "estrazione") {
    const aid = hashParam("a");
    if (aid) return cardDetail(aid);
    return eurExtraction(list, cur);
  }
  if (!cur) { put(view, ...eurEmpty(EUR_TITLES[sub] ? sub : "panoramica")); return; }
  const fn = {protocollo: eurProtocol, screening: eurScreening, fulltext: eurFulltext, bias: eurBias, consenso: eurConsensus,
    meta: eurMeta, esporta: eurExport}[sub] || eurOverview;
  put(view, ...(await fn(list, cur)));
}

function go(sec) { location.hash = `#/${sec}`; }
async function route() {
  let raw = (location.hash.replace(/^#\/?/, "").split("?")[0]) || "panoramica";
  if (raw === "lavora") raw = "funzioni";                     // old name of the section (links and bookmarks)
  const sec = raw;
  if (raw.startsWith("eurydike/")) moduleNow = "eurydike";
  else if (!(SHARED.includes(raw) && moduleNow === "eurydike")) moduleNow = "orpheus";
  buildNav();
  const fn = raw.startsWith("eurydike/") ? () => eurydikeSection(raw.slice(9)) : (sections[sec] || sections.panoramica);
  for (const a of document.querySelectorAll("#nav a")) {
    if (a.dataset.sec === sec) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  clearTimeout(pollTimer);
  const box = h("div");
  liveView = box;
  viewRoot.replaceChildren(box);
  put(box, loading());
  try {
    await fn();
    if (LAYOUT_SECTIONS.has(sec) && liveView === box) { applyLayout(box, sec); decorate(); }
    const h1 = box.querySelector("h1");
    if (h1 && document.activeElement === document.body) h1.focus({preventScroll: true});
  } catch (e) { put(box, errorBox(e)); }
}
window.addEventListener("hashchange", route);

function setTheme(th) {
  if (th === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", th);
  const eff = th === "auto" ? (window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : th;
  for (const b of document.querySelectorAll("[data-theme-set]")) b.setAttribute("aria-pressed", String(b.dataset.themeSet === eff));
  try { localStorage.setItem("orpheus-tema", th); } catch (e) { /* storage not available */ }
}
for (const b of document.querySelectorAll("[data-theme-set]")) b.addEventListener("click", () => setTheme(b.dataset.themeSet));
let saved = "auto";
try { saved = localStorage.getItem("orpheus-tema") || "auto"; } catch (e) { saved = "auto"; }
setTheme(saved);

/* the lavora badge in the side bar follows a run started from this page */
api("/api/lavora/uscita?da=999999999").then((snap) => { updateBadge(snap); if (snap.in_corso) {
  const t = setInterval(async () => { try { const x = await api("/api/lavora/uscita?da=999999999"); updateBadge(x); if (!x.in_corso) clearInterval(t); } catch (e) { clearInterval(t); } }, 3000);
} }).catch(() => {});

Promise.all([
  api("/api/sigle").then((x) => { setJournals(x.sigle, x.articoli); journalNames = new Map(Object.entries(x.titoli || {})); }).catch(() => {}),
  api("/api/testi").then((x) => { setOverrides(x.testi); textsFile = x.file; }).catch(() => {}),
  api("/api/aspetto").then((x) => { LOOK = x; applyStyle(x.stile); }).catch(() => {}),
]).finally(() => { applyLang(); buildNav(); route(); });

/* ------------------------------------------------------------------ "Modifica i testi" (2026-10-30)
   Clic on a text of the page, write the new one: it is saved in testi_cruscotto.json (data folder) and used at once.
   The original Italian text stays the key, so "Ripristina" always brings it back. */
let textsFile = "", editing = false;
const editBtn = document.getElementById("text-edit");
const editBar = h("div", {class: "tedit-bar", role: "status", hidden: true},
  h("span", null, "Modifica dei testi: clic su un testo per cambiarlo."), " ",
  h("button", {type: "button", class: "btn small", onclick: () => setEditing(false)}, "Fine"));
document.body.append(editBar);
function setEditing(on) {
  if (on && lang === "en") { alert(t("I testi si modificano in italiano: passa a IT.")); return; }
  if (on && layoutEditing) setLayoutEditing(false);
  editing = on;
  document.body.classList.toggle("text-edit", on);
  editBar.hidden = !on;
  editBtn.setAttribute("aria-pressed", String(on));
  if (!on) closeTextEditor();
}
editBtn.addEventListener("click", () => setEditing(!editing));
function textNodeAt(e) {
  let n = null;
  if (document.caretPositionFromPoint) { const pos = document.caretPositionFromPoint(e.clientX, e.clientY); n = pos && pos.offsetNode; }
  else if (document.caretRangeFromPoint) { const r = document.caretRangeFromPoint(e.clientX, e.clientY); n = r && r.startContainer; }
  if (n && n.nodeType === 3 && n.__src) return n;
  const el = e.target;
  for (const c of (el && el.childNodes) || []) if (c.nodeType === 3 && c.__src && c.textContent.trim()) return c;
  return null;
}
let teditBox = null;
function closeTextEditor() { if (teditBox) { teditBox.remove(); teditBox = null; } }
function openTextEditor(node) {
  closeTextEditor();
  let src = node.__src;
  if (!hasOwn(IT_OVR, src) && IT_REV.has(src)) src = IT_REV.get(src);       // text already changed upstream
  const changed = hasOwn(IT_OVR, src);
  const area = h("textarea", {rows: String(Math.min(8, Math.max(2, Math.ceil(src.length / 60)))), "aria-label": "Testo nuovo"});
  area.value = changed ? IT_OVR[src] : src;
  const msg = h("p", {class: "error", role: "alert", hidden: true});
  const save = async (value) => {
    try {
      const r = await postJson("/api/testi", {originale: src, nuovo: value});
      setOverrides(r.testi); closeTextEditor(); applyLang(); buildNav(); route();
    } catch (err) { msg.hidden = false; msg.textContent = err.message; }
  };
  teditBox = h("div", {class: "tedit", role: "dialog", "aria-label": "Modifica il testo"},
    h("p", {class: "t-sub"}, "Testo originale"), h("p", {class: "tedit-orig", text: src}),
    /\d/.test(src) ? h("p", {class: "t-sub"}, "Contiene numeri o nomi che cambiano: la modifica vale solo finché il testo resta identico.") : null,
    area, msg,
    h("div", {class: "action-row"}, h("button", {type: "button", class: "btn primary", onclick: () => save(area.value)}, "Salva"),
      changed ? h("button", {type: "button", class: "btn", onclick: () => save("")}, "Ripristina l'originale") : null,
      h("button", {type: "button", class: "btn", onclick: closeTextEditor}, "Annulla")));
  for (const n of teditBox.querySelectorAll("*")) for (const c of n.childNodes) if (c.nodeType === 3) c.__src = null;
  document.body.append(teditBox);
  area.focus(); area.select();
}
document.addEventListener("click", (e) => {
  if (!editing || e.target.closest("#text-edit, .tedit, .tedit-bar")) return;
  e.preventDefault(); e.stopPropagation();
  const node = textNodeAt(e);
  if (node) openTextEditor(node);
}, true);
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || !editing) return;
  if (teditBox) closeTextEditor(); else setEditing(false);
});
function textsBlock() {
  const rows = Object.entries(IT_OVR);
  return h("section", {class: "block"}, h("div", {class: "block-head"}, h("h2", null, "Testi del cruscotto"),
      h("button", {type: "button", class: "btn", onclick: () => setEditing(true)}, "Modifica i testi")),
    h("p", {class: "muted"}, "Premi «Modifica i testi» (anche in fondo alla barra a sinistra), poi fai clic su qualsiasi testo della pagina e scrivi quello nuovo. Le modifiche valgono su ogni PC che usa questo disco dei dati."),
    rows.length ? h("div", {class: "table-wrap"}, h("table", null,
      h("thead", null, h("tr", null, h("th", {scope: "col"}, "Testo originale"), h("th", {scope: "col"}, "Testo nuovo"), h("th", {scope: "col"}, ""))),
      h("tbody", null, rows.map(([k, v]) => h("tr", null, h("td", {text: k}), h("td", {text: v}),
        h("td", null, h("button", {type: "button", class: "btn small", onclick: async () => {
          try { const r = await postJson("/api/testi", {originale: k, nuovo: ""}); setOverrides(r.testi); applyLang(); buildNav(); route(); }
          catch (err) { alert(err.message); }
        }}, "Ripristina"))))))) : h("p", {class: "empty"}, "Nessun testo modificato."),
    textsFile ? h("p", {class: "src"}, `${t("File")}: ${textsFile} (${t("si può anche aprire con il Blocco note: «testo originale»: «testo nuovo»")})`) : null);
}

/* ------------------------------------------------------------------ look (2026-10-30): styles and blocks
   The style (colours, corners, headings) and, for each section, the order of the blocks, their width (whole or half)
   and the blocks taken away: in aspetto_cruscotto.json of the data folder, the same on every PC. */
let LOOK = {stile: "classico", sezioni: {}};
const STYLE_NAMES = ["classico", "moderno", "industriale", "oceano", "terra"];
const styleSel = document.getElementById("style-set");
function applyStyle(st) {
  if (!STYLE_NAMES.includes(st)) st = "classico";
  document.documentElement.setAttribute("data-style", st);
  styleSel.value = st;
  try { localStorage.setItem("orpheus-stile", st); } catch (e) { /* not available */ }
}
styleSel.addEventListener("change", async () => {
  applyStyle(styleSel.value); LOOK.stile = styleSel.value;
  try { await postJson("/api/aspetto", {stile: styleSel.value}); } catch (e) { /* the style still applies here */ }
});
const LAYOUT_SECTIONS = new Set(["panoramica", "articoli", "riviste", "manoscritti", "sottomissioni", "verifiche", "funzioni",
  "condivisione", "sistema"]);
const BLOCK_SEL = ".block, .group, .stats, .kpis, details.block, section";
let layoutEditing = false, layoutSec = null, layoutBox = null, dragItem = null;
const layoutBtn = document.getElementById("layout-edit");
function blockName(el, used) {
  let txt = el.dataset.block || "";
  const hd = txt ? null : el.querySelector("h1, h2, h3") || el.querySelector(".paper-name, summary:not(.mpick-sum)");
  if (hd) {
    const w = document.createTreeWalker(hd, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = w.nextNode())) if (n.textContent.trim() && !n.parentElement.closest(".sr-only")) { txt = n.__src || n.textContent; break; }
  }
  txt = (txt || el.getAttribute("aria-label") || "blocco").trim().slice(0, 120);
  let k = txt, i = 2;
  while (used.has(k)) k = `${txt} #${i++}`;
  used.add(k);
  return k;
}
function arrange(natural, saved) {
  const out = saved.filter((k) => natural.includes(k));
  natural.forEach((k, i) => {
    if (out.includes(k)) return;
    const prev = natural.slice(0, i).reverse().find((x) => out.includes(x));
    out.splice(prev ? out.indexOf(prev) + 1 : 0, 0, k);
  });
  return out;
}
function applyLayout(box, sec) {
  layoutSec = sec; layoutBox = box;
  const kids = [...box.children];
  let start = 0;
  while (start < kids.length && kids[start].matches(".view-head, .lede")) start++;
  const units = [];
  let buf = [];
  for (const el of kids.slice(start)) {
    if (el.matches(".cols, .ms-grid")) { for (const c of [...el.children]) { units.push({els: [...buf, c], el: c, half: true}); buf = []; } el.remove(); continue; }
    if (el.matches(BLOCK_SEL)) { units.push({els: [...buf, el], el, half: false}); buf = []; } else buf.push(el);
  }
  if (!units.length) return;
  const used = new Set();
  for (const u of units) u.key = blockName(u.el, used);
  const conf = (LOOK.sezioni || {})[sec] || {};
  const hidden = new Set(conf.nascosti || []), width = conf.larghezza || {};
  const grid = h("div", {class: "lay"});
  for (const k of arrange(units.map((u) => u.key), conf.ordine || [])) {
    const u = units.find((x) => x.key === k);
    const half = width[k] ? width[k] === "mezza" : u.half;
    const item = h("div", {class: `lay-item${half ? " half" : ""}${hidden.has(k) ? " is-hidden" : ""}`});
    item.dataset.key = k;
    item.append(...u.els);
    grid.append(item);
  }
  if (buf.length) box.insertBefore(grid, buf[0]); else box.append(grid);
}
function layoutItems(grid) { return [...grid.querySelectorAll(":scope > .lay-item")]; }
const saveLayout = debounce(async () => {
  const grid = layoutBox && layoutBox.querySelector(":scope > .lay");
  if (!grid) return;
  const items = layoutItems(grid);
  const conf = {ordine: items.map((i) => i.dataset.key), nascosti: items.filter((i) => i.classList.contains("is-hidden")).map((i) => i.dataset.key),
    larghezza: Object.fromEntries(items.map((i) => [i.dataset.key, i.classList.contains("half") ? "mezza" : "intera"]))};
  LOOK.sezioni = LOOK.sezioni || {};
  LOOK.sezioni[layoutSec] = conf;
  try { await postJson("/api/aspetto", {sezione: layoutSec, ...conf}); } catch (e) { alert(e.message); }
}, 400);
function moveItem(it, d) {
  const sib = d < 0 ? it.previousElementSibling : it.nextElementSibling;
  if (!sib || !sib.classList.contains("lay-item")) return;
  if (d < 0) sib.before(it); else sib.after(it);
  saveLayout();
  const b = it.querySelector(`.lay-tools [data-move="${d}"]`);
  if (b) b.focus();
}
function decorate() {
  const grid = layoutBox && document.contains(layoutBox) ? layoutBox.querySelector(":scope > .lay") : null;
  if (!grid) return;
  grid.querySelectorAll(":scope > .lay-bar, :scope > .lay-item > .lay-tools").forEach((x) => x.remove());
  grid.classList.toggle("editing", layoutEditing);
  const items = layoutItems(grid);
  for (const it of items) { it.hidden = it.classList.contains("is-hidden") && !layoutEditing; it.draggable = layoutEditing; }
  if (!layoutEditing) return;
  for (const it of items) {
    const half = it.classList.contains("half"), off = it.classList.contains("is-hidden");
    it.prepend(h("div", {class: "lay-tools"},
      h("span", {class: "lay-grip", "aria-hidden": "true", title: "Trascina per spostare"}, "⠿"),
      h("span", {class: "lay-name"}, it.dataset.key), off ? tag("tolto", "warn") : null,
      h("span", {class: "lay-btns"},
        h("button", {type: "button", class: "btn small", "data-move": "-1", "aria-label": "Sposta prima", title: "Sposta prima", onclick: () => moveItem(it, -1)}, "↑"),
        h("button", {type: "button", class: "btn small", "data-move": "1", "aria-label": "Sposta dopo", title: "Sposta dopo", onclick: () => moveItem(it, 1)}, "↓"),
        h("button", {type: "button", class: "btn small", onclick: () => { it.classList.toggle("half"); saveLayout(); decorate(); }},
          half ? "Larghezza intera" : "Metà larghezza"),
        h("button", {type: "button", class: "btn small", onclick: () => { it.classList.toggle("is-hidden"); saveLayout(); decorate(); }},
          off ? "Rimetti" : "Togli"))));
  }
  const off = items.filter((i) => i.classList.contains("is-hidden"));
  grid.prepend(h("div", {class: "lay-bar", role: "region", "aria-label": "Personalizza i blocchi"},
    h("p", null, h("b", null, "Personalizza i blocchi di questa pagina."), " ",
      "Trascina un blocco o usa le frecce; scegli la larghezza; «Togli» lo nasconde. Le modifiche si salvano da sole."),
    off.length ? h("div", {class: "checks"}, h("span", {class: "t-sub"}, "Blocchi tolti, da aggiungere di nuovo:"),
      off.map((it) => h("button", {type: "button", class: "btn small", onclick: () => { it.classList.remove("is-hidden"); saveLayout(); decorate(); }}, `+ ${it.dataset.key}`))) : null,
    h("div", {class: "action-row"},
      h("button", {type: "button", class: "btn small", onclick: async () => {
        if (!confirm(t("Rimettere i blocchi di questa pagina come all'inizio?"))) return;
        try { await postJson("/api/aspetto", {sezione: layoutSec, ripristina: true}); delete (LOOK.sezioni || {})[layoutSec]; route(); } catch (e) { alert(e.message); }
      }}, "Ripristina la disposizione"),
      h("button", {type: "button", class: "btn small primary", onclick: () => setLayoutEditing(false)}, "Fine"))));
}
function setLayoutEditing(on) {
  if (on && editing) setEditing(false);
  layoutEditing = on;
  document.body.classList.toggle("layout-edit", on);
  layoutBtn.setAttribute("aria-pressed", String(on));
  if (on && !(layoutBox && document.contains(layoutBox) && layoutBox.querySelector(":scope > .lay"))) {
    alert(t("Questa pagina non ha blocchi da spostare."));
    layoutEditing = false; document.body.classList.remove("layout-edit"); layoutBtn.setAttribute("aria-pressed", "false");
  }
  decorate();
}
layoutBtn.addEventListener("click", () => setLayoutEditing(!layoutEditing));
document.addEventListener("dragstart", (e) => {
  const it = e.target.closest && e.target.closest(".lay.editing > .lay-item");
  if (!it || e.target !== it) return;
  dragItem = it; it.classList.add("dragging");
  e.dataTransfer.effectAllowed = "move";
  try { e.dataTransfer.setData("text/plain", it.dataset.key); } catch (x) { /* ignore */ }
});
document.addEventListener("dragover", (e) => {
  if (!dragItem) return;
  const it = e.target.closest && e.target.closest(".lay.editing > .lay-item");
  e.preventDefault();
  if (!it || it === dragItem || it.parentNode !== dragItem.parentNode) return;
  const r = it.getBoundingClientRect();
  const before = it.classList.contains("half") && dragItem.classList.contains("half") ? e.clientX < r.left + r.width / 2 : e.clientY < r.top + r.height / 2;
  it.parentNode.insertBefore(dragItem, before ? it : it.nextSibling);
});
document.addEventListener("drop", (e) => { if (dragItem) e.preventDefault(); });
document.addEventListener("dragend", () => { if (dragItem) { dragItem.classList.remove("dragging"); dragItem = null; saveLayout(); } });

/* full name of a journal after one second on its abbreviation */
let jTimer = null, jEl = null;
function jShow(el) {
  put(tip, h("b", null, el.dataset.jname));
  tip.hidden = false;
  const r = el.getBoundingClientRect(), w = tip.offsetWidth;
  tip.style.left = `${Math.max(8, Math.min(r.left, window.innerWidth - w - 8))}px`;
  tip.style.top = `${r.bottom + 6 + tip.offsetHeight > window.innerHeight ? r.top - tip.offsetHeight - 6 : r.bottom + 6}px`;
}
function jHide() { clearTimeout(jTimer); if (jEl) { jEl = null; hideTip(); } }
function jStart(el) {
  if (el === jEl) return;
  jHide();
  jEl = el;
  jTimer = setTimeout(() => { if (jEl === el && document.contains(el)) jShow(el); }, 1000);
}
document.addEventListener("mouseover", (e) => {
  const el = e.target.closest ? e.target.closest("[data-jname]") : null;
  if (el) jStart(el); else jHide();
});
document.addEventListener("focusin", (e) => { const el = e.target.closest("[data-jname]"); if (el) jStart(el); });
document.addEventListener("focusout", jHide);
window.addEventListener("scroll", jHide, true);
window.addEventListener("hashchange", jHide);
})();
