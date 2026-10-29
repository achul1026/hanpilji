// 한필지 웹 화면. 글자는 모두 textContent로 넣음(답 속 HTML이 실행되지 않게). HTML 문자열로 그리지 않음.
"use strict";
const $ = id => document.getElementById(id);
const app = $("app"), log = $("log"), welcome = $("welcome"), q = $("q"), go = $("go"), scroll = $("scroll");
const SVG = "http://www.w3.org/2000/svg";
const STORE = "hanpilji.convs.v1", MAX_CONVS = 40;

const EXAMPLES = [
  ["단지", "도곡렉슬 종합해서 알려줘"],
  ["시세", "은마 76㎡ 최근 6개월 최고가"],
  ["추이", "대치동 아파트 시세 2년 추이"],
  ["규제", "제2종일반주거지역에 휴게음식점 지을 수 있어?"],
  ["법령", "전월세 재계약 때 5% 상한 조문 찾아줘"],
  ["공시지가", "래미안대치팰리스 공시지가 몇 년 동안 얼마나 올랐어?"],
  ["지표", "기준금리랑 서울 아파트 매매가격지수 최근 2년"],
  ["정책", "10·15 대책 전후로 강남구 아파트 거래와 가격은 어떻게 달라졌어?"],
];

// ------------------------------------------------------------ 저장(이 브라우저에만)
function load() { try { return JSON.parse(localStorage.getItem(STORE)) || []; } catch (e) { return []; } }
// 화면 요소(el)는 빼고 저장. 저장 못 해도(사생활 보호 창 등) 화면은 그대로
function save() {
  const put = lite => localStorage.setItem(STORE, JSON.stringify(convs.slice(0, MAX_CONVS).map(c => strip(c, lite))));
  try { put(false); } catch (e) { try { put(true); } catch (e2) { /* 무시 */ } }
}
// lite: 저장 공간이 모자라면 지도 블록의 이웃 필지 · 건물 도형은 빼고 대상 필지만 남김
function strip(c, lite) {
  const slim = b => b.kind === "map" ? { ...b, features: (b.features || []).filter(f => f.layer === "대상 필지") } : b;
  return { ...c, items: c.items.map(({ el: _, ...rest }) => lite && rest.blocks ? { ...rest, blocks: rest.blocks.map(slim) } : rest) };
}
let convs = load(), cur = null, busy = false;
const token = (() => {
  const t = new URLSearchParams(location.search).get("token");
  try { if (t) localStorage.setItem("hanpilji.token", t); return t || localStorage.getItem("hanpilji.token") || ""; } catch (e) { return t || ""; }
})();

// ------------------------------------------------------------ 작은 도구
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function svg(tag, attrs, text) { const e = document.createElementNS(SVG, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (text != null) e.textContent = text; return e; }
function btn(cls, text, onclick, label) { const b = el("button", cls, text); b.type = "button"; if (onclick) b.onclick = onclick; if (label) b.setAttribute("aria-label", label); return b; }
const nf = new Intl.NumberFormat("ko-KR");

// 금액(원) → 「35억 6,000만」. 1억 미만은 「8,500만」, 1만 미만은 원 그대로
function won(v, unit) {
  if (v == null || v === "") return "-";
  const n = Math.round(Number(v)), eok = Math.floor(n / 1e8), man = Math.round((n % 1e8) / 1e4);
  let s = n >= 1e4 ? ((eok ? nf.format(eok) + "억" : "") + (man ? (eok ? " " : "") + nf.format(man) + "만" : "")) || "0" : nf.format(n) + "원";
  return s + (unit || "");
}
const FMT = {
  won: v => won(v), won_m2: v => won(v, "/㎡"), won_py: v => won(v, "/평"),
  int: v => v == null ? "-" : nf.format(v), num: v => v == null ? "-" : nf.format(Math.round(v * 100) / 100),
  pct: v => v == null ? "-" : (Math.round(v * 10) / 10) + "%", pp: v => v == null ? "-" : (v > 0 ? "+" : "") + v + "%p",
  m2: v => v == null ? "-" : (Math.round(v * 100) / 100) + "㎡", date: v => v || "-",
  ym: v => { v = String(v ?? ""); return /^\d{6}$/.test(v) ? v.slice(0, 4) + "." + v.slice(4) : v.replace("-", "."); },
  text: v => v == null || v === "" ? "-" : String(v), allowed: v => v == null ? "-" : String(v),
};
// 그래프 눈금용 짧은 금액: 1.35억 · 1,800만
function shortWon(v) { v = Number(v); return Math.abs(v) >= 1e8 ? (Math.round(v / 1e6) / 100) + "억" : Math.abs(v) >= 1e4 ? nf.format(Math.round(v / 1e4)) + "만" : nf.format(v); }
const NUMERIC = new Set(["won", "won_m2", "won_py", "int", "num", "pct", "pp", "m2"]);
const fmt = (f, v) => (FMT[f] || FMT.text)(v);

// ------------------------------------------------------------ 마크다운(제목 · 목록 · 표 · 굵게 · 코드 · 근거 번호)
let citeHandler = null;   // 지금 그리는 답의 근거 번호를 누르면 할 일
let checks = [];          // 지금 그리는 답의 숫자 검산 결과(엔진이 원자료와 맞춰 본 것)
function cite(n, onclick) {
  const b = btn("cite", String(n), onclick, "근거 " + n + " 보기");
  if (!onclick) { b.classList.add("dead"); b.disabled = true; }
  return b;
}
function inline(parent, text) {
  const re = /\*\*(.+?)\*\*|`([^`]+)`|\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]/g; let i = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > i) parent.append(text.slice(i, m.index));
    if (m[1] != null) parent.append(el("strong", null, m[1]));
    else if (m[2] != null) parent.append(el("code", null, m[2]));
    else for (const n of m[3].split(",").map(s => +s.trim())) parent.append(cite(n, citeHandler && citeHandler(n)));
    i = re.lastIndex;
  }
  if (i < text.length) parent.append(text.slice(i));
  return parent;
}
function cells(line) { return line.trim().replace(/^\|/, "").replace(/(^|[^\\])\|$/, "$1").split(/(?<!\\)\|/).map(c => c.trim().replace(/\\\|/g, "|")); }
const HD = /^(#{1,6})\s+(.*)$/, LI = /^(\s*)(?:[-*+•]|(\d+)[.)])\s+(.*)$/, SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/, ROW = /(^|[^\\])\|/;
const isTable = (lines, i) => ROW.test(lines[i]) && i + 1 < lines.length && ROW.test(lines[i + 1]) && SEP.test(lines[i + 1]);
const depth = s => s.replace(/\t/g, "    ").length;
function list(lines, i, ind) {
  let m = lines[i].match(LI); const ord = !!m[2], L = el(ord ? "ol" : "ul"); if (ord) L.start = +m[2]; let last = null;
  while (i < lines.length && (m = lines[i].match(LI))) {
    const d = depth(m[1]);
    if (d < ind) break;
    if (d > ind && last) { const r = list(lines, i, d); last.append(r[0]); i = r[1]; continue; }
    if (!!m[2] !== ord) break;
    last = inline(el("li"), m[3]); L.append(last); i++;
  }
  return [L, i];
}
// 「:::숫자」 · 「:::확인 못 함」 칸(엔진 프롬프트가 정한 모양). 숫자는 카드로, 확인 못 함은 노란 상자로
const FENCE = /^\s*:::\s*(숫자|확인 못 함)\s*$/, FENCE_END = /^\s*:::\s*$/;
function checkMark(ok) {
  const c = el("span", "chk " + (ok ? "ok" : "unk")), s = svg("svg", { width: 12, height: 12, viewBox: "0 0 12 12", "aria-hidden": "true" });
  s.append(ok ? svg("path", { d: "M2 6.5l2.5 2.5L10 3.5", fill: "none", stroke: "currentColor", "stroke-width": 2 }) : svg("circle", { cx: 6, cy: 6, r: 4, fill: "none", stroke: "currentColor", "stroke-width": 1.5 }));
  c.append(s, ok ? "원자료 일치" : "원자료에서 바로 못 찾음"); return c;
}
// 핵심 숫자 값이 검산 목록의 어느 숫자와 같은지(띄어쓰기 · 「원」은 빼고 비교). 검산하지 않은 값이면 null
function checkOf(value) {
  const norm = t => String(t).replace(/\s|원/g, ""), v = norm(value);
  const hit = checks.filter(c => v.includes(norm(c.text)));
  return hit.length ? hit.every(c => c.ok) : null;
}
function figs(lines) {
  const box = el("div", "figs");
  for (const line of lines) {
    const [label, value, note, ref] = line.split("|").map(x => x.trim());
    if (!label || !value) continue;
    const f = el("div", "fig"), s = el("span", "s"), n = parseInt(String(ref || "").replace(/\D/g, ""), 10);
    f.append(el("span", "l", label), el("span", "v", value.replace(/(\d[억만])\s*원$/, "$1")));   // 「35억 6,000만 원」 → 원은 빼고 크게
    if (n) s.append(cite(n, citeHandler && citeHandler(n)), " ");
    if (note) s.append(note);
    const ok = checkOf(value);
    if (ok != null) { if (s.childNodes.length) s.append(el("br")); s.append(checkMark(ok)); }
    if (s.childNodes.length) f.append(s);
    box.append(f);
  }
  return box;
}
function gaps(lines) {
  const box = el("div", "gap"), ul = el("ul");
  box.append(el("b", null, "확인 못 한 것"));
  for (const line of lines) { const t = line.replace(/^\s*[-*•]\s*/, "").trim(); if (t) ul.append(inline(el("li"), t)); }
  box.append(ul); return box;
}
function md(text) {
  const box = el("div", "md"), lines = text.replace(/\r/g, "").split("\n"); let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) { i++; continue; }
    const fence = l.match(FENCE);
    if (fence) {
      const body = []; i++;
      while (i < lines.length && !FENCE_END.test(lines[i])) body.push(lines[i++]);
      i++;
      box.append(fence[1] === "숫자" ? figs(body) : gaps(body)); continue;
    }
    const h = l.match(HD);
    if (h) { box.append(inline(el("h" + Math.min(h[1].length + 2, 6)), h[2])); i++; continue; }
    if (isTable(lines, i)) {
      const t = el("table"), hr = el("tr"), head = cells(l), num = [];
      cells(lines[i + 1]).forEach((c, k) => num[k] = /-:$/.test(c));
      head.forEach((c, k) => hr.append(inline(el("th", num[k] ? "n" : null), c))); t.append(hr); i += 2;
      while (i < lines.length && lines[i].trim() && ROW.test(lines[i])) { const tr = el("tr"); cells(lines[i]).forEach((c, k) => tr.append(inline(el("td", num[k] ? "n" : null), c))); t.append(tr); i++; }
      const w = el("div", "tw"); w.append(t); box.append(w); continue;
    }
    const m = l.match(LI);
    if (m) { const r = list(lines, i, depth(m[1])); box.append(r[0]); i = r[1]; continue; }
    const para = [];
    while (i < lines.length && lines[i].trim() && !LI.test(lines[i]) && !HD.test(lines[i]) && !isTable(lines, i) && !FENCE.test(lines[i])) para.push(lines[i++]);
    if (!para.length) para.push(lines[i++]);
    const p = el("p"); para.forEach((x, k) => { if (k) p.append(el("br")); inline(p, x); }); box.append(p);
  }
  // 첫 단락은 결론 한 줄(엔진 프롬프트). 뒤에 더 있을 때만 크게
  if (box.firstElementChild && box.firstElementChild.tagName === "P" && box.children.length > 1) box.firstElementChild.classList.add("lead");
  return box;
}

// ------------------------------------------------------------ 블록
function sourceLine(b) {
  const s = b.source || {}, n = s.row_total == null ? "" : (s.shown === s.row_total ? nf.format(s.row_total) + "건" : "전체 " + nf.format(s.row_total) + "건 중 " + s.shown + "건");
  return [(s.sources || []).join(" · "), n, s.asof && "기준 " + s.asof].filter(Boolean).join(" · ");
}

function tableEl(b, limit) {
  const cols = b.columns || [], rows = b.rows || [], t = el("table"), hr = el("tr");
  for (const c of cols) hr.append(el("th", NUMERIC.has(c.fmt) ? "n" : null, c.label));
  t.append(hr);
  for (const r of rows.slice(0, limit || rows.length)) {
    const tr = el("tr");
    for (const c of cols) {
      const v = r[c.key];
      if (c.fmt === "allowed") {
        const td = el("td"), s = String(v ?? "-");
        td.append(el("span", "pill " + (/불가/.test(s) ? "no" : /가능/.test(s) ? "ok" : "mid"), s)); tr.append(td); continue;
      }
      tr.append(el("td", NUMERIC.has(c.fmt) ? "n" : (["condition_text", "what_to_check", "where_to_look", "name"].includes(c.key) ? "wrap" : null), fmt(c.fmt, v)));
    }
    t.append(tr);
  }
  const w = el("div", "tw"); w.append(t); return w;
}

function csv(b) {
  const cols = b.columns || [], esc = v => { const s = v == null ? "" : String(v); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
  const text = "﻿" + [cols.map(c => esc(c.label)).join(","), ...(b.rows || []).map(r => cols.map(c => esc(r[c.key])).join(","))].join("\n");
  const a = el("a"); a.href = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  a.download = (b.title || "한필지").replace(/[\\/:*?"<>|]/g, "_") + ".csv"; document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

const COLORS = ["var(--survey)", "var(--seal)", "var(--ok)", "var(--warn)"];
// 선 그래프. 같은 단위의 선만 한 번에 그림(단위가 다른 선은 위의 단추로 바꿔 봄). 점 크기 = 그 달 건수, 빈 점 = 잠정
function chartEl(b, which, narrow) {
  const box = el("div", "chart"), rows = b.rows || [];
  const series = which == null ? b.series : [b.series[which]];
  const W = narrow ? 400 : 680, H = narrow ? 210 : 230, L = 52, R = 12, T = 14, B = H - 34;
  const vals = []; for (const s of series) for (const r of rows) if (r[s.key] != null) vals.push(+r[s.key]);
  if (!vals.length) { box.append(el("p", "dempty", "그릴 값이 없습니다.")); return box; }
  let lo = Math.min(...vals), hi = Math.max(...vals); const pad = (hi - lo) * .12 || Math.abs(hi) * .05 || 1; lo -= pad; hi += pad;
  const x = i => L + (W - L - R) * (rows.length === 1 ? .5 : i / (rows.length - 1)), y = v => B - (B - T) * (v - lo) / (hi - lo);
  const s = svg("svg", { viewBox: `0 0 ${W} ${H + 8}`, role: "img", "aria-label": b.title + " 그래프" });
  // 눈금 4개: 보기 좋은 간격으로
  const step = (() => { const raw = (hi - lo) / 4, p = Math.pow(10, Math.floor(Math.log10(raw))), m = raw / p; return (m < 1.5 ? 1 : m < 3 ? 2 : m < 7 ? 5 : 10) * p; })();
  for (let g = Math.ceil(lo / step) * step; g <= hi; g += step) {
    s.append(svg("line", { x1: L, x2: W - R, y1: y(g), y2: y(g), stroke: "var(--grid)", "stroke-width": 1 }));
    s.append(svg("text", { x: L - 8, y: y(g) + 4, "text-anchor": "end", "font-size": 11, fill: "var(--muted)", "font-family": "var(--mono)" }, /^won/.test(series[0].fmt) ? shortWon(g) : fmt(series[0].fmt, g)));
  }
  const ticks = rows.length <= 6 ? rows.map((_, i) => i) : [0, Math.round((rows.length - 1) / 3), Math.round((rows.length - 1) * 2 / 3), rows.length - 1];
  for (const i of ticks) s.append(svg("text", { x: x(i), y: B + 20, "text-anchor": i === 0 ? "start" : i === rows.length - 1 ? "end" : "middle", "font-size": 11, fill: "var(--muted)", "font-family": "var(--mono)" }, fmt(b.x_fmt, rows[i][b.x])));
  // 정책 발표 같은 표시선: 그 달 자리에 세로 점선과 이름
  for (const m of b.markers || []) {
    const i = rows.findIndex(r => String(r[b.x]) === String(m.x)); if (i < 0) continue;
    s.append(svg("line", { x1: x(i), x2: x(i), y1: T, y2: B, stroke: "var(--ink2)", "stroke-width": 1.2, "stroke-dasharray": "4 3" }));
    s.append(svg("text", { x: x(i) + 4, y: T + 10, "font-size": 11, fill: "var(--ink2)", "font-family": "var(--mono)" }, m.label));
  }
  const maxCount = b.count ? Math.max(1, ...rows.map(r => +r[b.count] || 0)) : 1;
  series.forEach((ser, k) => {
    const color = COLORS[(which ?? k) % COLORS.length], pts = [];
    rows.forEach((r, i) => { if (r[ser.key] != null) pts.push([x(i), y(+r[ser.key]), r]); });
    if (!pts.length) return;
    const d = pts.map((p, j) => (j ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    if (series.length === 1) s.append(svg("path", { d: d + ` L${pts[pts.length - 1][0]} ${B} L${pts[0][0]} ${B} Z`, fill: color, opacity: .1 }));
    s.append(svg("path", { d, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    for (const [px, py, r] of pts) {
      const part = b.partial && r[b.partial], n = b.count ? +r[b.count] || 0 : 0;
      const c = svg("circle", { cx: px, cy: py, r: b.count ? 2.2 + 4 * Math.sqrt(n / maxCount) : 2.6, fill: part ? "var(--sheet)" : color, stroke: color, "stroke-width": 1.5 });
      c.append(svg("title", {}, `${fmt(b.x_fmt, r[b.x])} · ${fmt(ser.fmt, r[ser.key])}` + (b.count ? ` · ${n}건` : "") + (part ? " · 잠정(신고 기간 중)" : "")));
      s.append(c);
    }
  });
  box.append(s);
  const lg = el("div", "legend");
  series.forEach((ser, k) => { const sp = el("span"), i = el("i"); i.style.background = COLORS[(which ?? k) % COLORS.length]; sp.append(i, ser.label); lg.append(sp); });
  if (b.count) lg.append(el("span", null, "점 크기 = 건수"));
  if (b.partial && rows.some(r => r[b.partial])) lg.append(el("span", null, "빈 점 = 잠정(신고 기간 중)"));
  if (b.note) lg.append(el("span", null, b.note));
  box.append(lg);
  return box;
}

// ------------------------------------------------------------ 지도(지적도 · 건물 윤곽)
// 배경지도는 /config.json 이 정함(V-World · OSM · 없음). 없으면 도면처럼 선만 그림. 웹 메르카토르로 직접 투영
let mapCfg = { tiles: null };
fetch("/config.json").then(r => r.ok ? r.json() : null).then(j => { if (j) mapCfg = j; }).catch(() => {});
const dark = () => document.documentElement.dataset.theme === "dark" || (document.documentElement.dataset.theme !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
const merc = (lon, lat) => { const s = Math.sin(lat * Math.PI / 180); return [(lon + 180) / 360, .5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)]; };
function polys(g) { return !g ? [] : g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : []; }
function niceLen(m) { let best = 5; for (const n of [5, 10, 20, 25, 50, 100, 200, 250, 500, 1000]) if (n <= m) best = n; return best; }

function mapEl(b, narrow) {
  const box = el("div", "map"), feats = b.features || [];
  const target = feats.filter(f => f.layer === "대상 필지");
  if (!target.length) { box.append(el("p", "dempty", "이 곳의 지적도가 아직 없습니다. 지금은 서울 강남구만 받아 두었습니다.")); return box; }
  const W = narrow ? 400 : 680, H = narrow ? 270 : 440, tiles = mapCfg.tiles;
  // 대상 필지가 화면 가운데 60% 남짓을 차지하게 맞춤(이웃 필지 · 건물은 그 둘레로 보임)
  let x0 = 1, y0 = 1, x1 = 0, y1 = 0;
  for (const f of target) for (const p of polys(f.geometry)) for (const [lon, lat] of p[0]) { const [x, y] = merc(lon, lat); x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
  const fit = Math.min(W * .62 / Math.max(x1 - x0, 1e-9), H * .62 / Math.max(y1 - y0, 1e-9)) / 256;
  let z = Math.min(Math.log2(fit), tiles ? tiles.max_zoom || 19 : 20.5);
  if (tiles) z = Math.floor(z);
  const S = 256 * Math.pow(2, z), cx = (x0 + x1) / 2 * S, cy = (y0 + y1) / 2 * S;
  const px = (lon, lat) => { const [x, y] = merc(lon, lat); return [x * S - cx + W / 2, y * S - cy + H / 2]; };
  const path = g => polys(g).map(p => p.map(r => r.map(([lon, lat], i) => { const [x, y] = px(lon, lat); return (i ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1); }).join("") + "Z").join("")).join("");
  const s = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": b.title + " 지도" });
  s.append(svg("rect", { x: 0, y: 0, width: W, height: H, fill: "var(--sheet)" }));
  if (tiles) {
    const g = svg("g", { class: "tiles" }), n = Math.pow(2, z), url = tiles.url.replace("{style}", (tiles.styles || {})[dark() ? "dark" : "light"] || "");
    for (let ty = Math.floor((cy - H / 2) / 256); ty <= Math.floor((cy + H / 2) / 256); ty++)
      for (let tx = Math.floor((cx - W / 2) / 256); tx <= Math.floor((cx + W / 2) / 256); tx++) {
        if (ty < 0 || ty >= n) continue;
        const im = svg("image", { x: tx * 256 - cx + W / 2, y: ty * 256 - cy + H / 2, width: 256, height: 256, preserveAspectRatio: "none" });
        im.setAttribute("href", url.replace("{z}", z).replace("{x}", ((tx % n) + n) % n).replace("{y}", ty));
        im.addEventListener("error", () => im.remove());
        g.append(im);
      }
    s.append(g);
  } else {   // 배경이 없으면 모눈종이처럼
    for (let gx = (W / 2) % 40; gx < W; gx += 40) s.append(svg("line", { x1: gx, x2: gx, y1: 0, y2: H, stroke: "var(--grid)", "stroke-width": 1 }));
    for (let gy = (H / 2) % 40; gy < H; gy += 40) s.append(svg("line", { x1: 0, x2: W, y1: gy, y2: gy, stroke: "var(--grid)", "stroke-width": 1 }));
  }
  const tip = f => [f.label, f.layer === "건물" ? null : [f.jibun, f.jimok && "(" + f.jimok + ")"].filter(Boolean).join(" "),
    f.floors_above ? f.floors_above + "층" : null, f.area_m2 != null ? nf.format(Math.round(f.area_m2)) + "㎡" : null].filter(Boolean).join(" · ");
  const shape = (f, attrs) => { const e = svg("path", { d: path(f.geometry), "fill-rule": "evenodd", "vector-effect": "non-scaling-stroke", ...attrs }); e.append(svg("title", {}, tip(f) || f.layer)); return e; };
  const labels = svg("g", { "font-family": "var(--mono)", "text-anchor": "middle" });
  const span = f => { const bb = polys(f.geometry).flatMap(p => p[0]).map(([lon, lat]) => px(lon, lat)), xs = bb.map(p => p[0]), ys = bb.map(p => p[1]);
    return [Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)]; };
  const placed = target.map(f => f.lon == null ? [-99, -99, 0] : [...px(f.lon, f.lat), 30]);
  for (const f of feats.filter(f => f.layer === "이웃 필지")) {
    s.append(shape(f, { class: "m-nb", fill: "transparent", stroke: "var(--muted)", "stroke-width": .8, "stroke-opacity": .55 }));
    const [w, h] = span(f);
    if (f.lon != null && w > 34 && h > 16) {
      const [x, y] = px(f.lon, f.lat), t = f.jibun + (f.jimok || ""), hw = t.length * 3.2 + 2;
      if (placed.some(([a, b, c]) => Math.abs(a - x) < c + hw && Math.abs(b - y) < 12)) continue;   // 글자끼리 겹치면 뺌
      placed.push([x, y, hw]); labels.append(svg("text", { x, y: y + 3, "font-size": 9.5, fill: "var(--muted)" }, t));
    }
  }
  for (const f of target) s.append(shape(f, { class: "m-tg", fill: "var(--survey)", "fill-opacity": .14, stroke: "var(--survey)", "stroke-width": 2.2, "stroke-linejoin": "round" }));
  for (const f of feats.filter(f => f.layer === "건물")) s.append(shape(f, { class: "m-bd", fill: "var(--ink)", "fill-opacity": .16, stroke: "var(--ink)", "stroke-width": .8, "stroke-opacity": .5 }));
  // 큰 필지부터: 작은 필지는 글자가 겹치니 도장 · 번지를 빼고 마우스를 올리면 보이게(title)
  for (const f of [...target].sort((a, b) => (b.area_m2 || 0) - (a.area_m2 || 0))) {
    if (f.lon == null) continue;
    const [w, h] = span(f), big = w > 46 && h > 26;
    if (!big && target.length > 1) continue;
    const [x, y] = px(f.lon, f.lat), t = f.jibun ? f.jibun + (f.jimok || "") : "";
    s.append(svg("rect", { x: x - 3.5, y: y - 3.5, width: 7, height: 7, fill: "var(--seal)", transform: `rotate(8 ${x} ${y})` }));
    if (t) { const tx = svg("text", { x, y: y - 9, "font-size": 12, "font-weight": 600, fill: "var(--survey)", stroke: "var(--sheet)", "stroke-width": 3, "paint-order": "stroke" }, t); labels.append(tx); }
  }
  s.append(labels);
  // 축척 막대 · 방위
  const mpp = 40075016.686 * Math.cos(target[0].lat * Math.PI / 180) / S, len = niceLen(mpp * (narrow ? 110 : 140)), bar = len / mpp;
  const sc = svg("g", { "font-family": "var(--mono)", "font-size": 10.5, fill: "var(--ink)" });
  sc.append(svg("rect", { x: 10, y: H - 28, width: bar + 44, height: 20, rx: 3, fill: "var(--sheet)", "fill-opacity": .85 }));
  sc.append(svg("path", { d: `M16 ${H - 16}v5h${bar.toFixed(1)}v-5`, fill: "none", stroke: "var(--ink)", "stroke-width": 1.4 }));
  sc.append(svg("text", { x: 22 + bar, y: H - 13 }, len + "m"));
  sc.append(svg("text", { x: W - 16, y: 22, "text-anchor": "middle", "font-weight": 600 }, "N"));
  sc.append(svg("path", { d: `M${W - 16} 26l-4 10h8z`, fill: "var(--ink)" }));
  s.append(sc);
  box.append(s);
  const lg = el("div", "legend");
  const key = (cls, label) => { const sp = el("span"), i = el("i", "mk mk-" + cls); sp.append(i, label); lg.append(sp); };
  key("tg", "대상 필지 " + target.length + "개");
  if (feats.some(f => f.layer === "이웃 필지")) key("nb", "이웃 필지");
  if (feats.some(f => f.layer === "건물")) key("bd", "건물 윤곽");
  lg.append(el("span", null, b.note || ""));
  if (tiles && tiles.attr) lg.append(el("span", null, tiles.attr));
  box.append(lg);
  return box;
}

// 묻는 낱말이 가장 많이(2개 이상) 든 문장 하나에 형광 표시. 낱말마다 칠하면 어지러워서 문장으로
function highlight(parent, text, words) {
  const parts = text.split(/(?<=다\.|\n)/), score = p => (words || []).filter(w => p.includes(w)).length;
  let best = -1, top = 1;
  parts.forEach((p, i) => { const sc = score(p); if (sc > top) { top = sc; best = i; } });
  parts.forEach((p, i) => parent.append(i === best ? el("mark", null, p) : p));
  return parent;
}
function docsEl(b, short) {
  const box = el("div");
  for (const it of (b.items || []).slice(0, short ? 2 : 99)) {
    const d = el("div", "doc" + (short ? " short" : ""));
    d.append(el("p", "law", it.title), el("div", "meta", [it.subtitle, it.meta].filter(Boolean).join(" · ")));
    // 본문 첫 줄은 「제7조(제목)」 반복이라 뺌
    const body = it.body.replace(/^제\d+조(의\d+)?\([^)]*\)\s*\n?/, "");
    d.append(highlight(el("div", "body"), body, b.highlight));
    if (!short && it.url) { const a = el("a", "link", it.link || "법제처 원문 ↗"); a.href = it.url; a.target = "_blank"; a.rel = "noopener"; d.append(a); }
    box.append(d);
  }
  return box;
}

// 정책 전후 비교: 정책 머리 · 지수 그래프(발표 표시선) · 전후 맞댄 표. 짧게(inline)면 핵심 줄만
const day = p => String(p || "").replace(/(\d{2})(\d{2})-(\d{2})-(\d{2})/g, "$2.$3.$4");   // 2025-07-17 → 25.07.17
const signed = (f, v) => v == null ? "-" : (v > 0 ? "+" : "") + fmt(f, v);
function policyEl(b, short) {
  const box = el("div", "policy");
  if (b.head) {
    const h = el("div", "phead"), note = String(b.head.note || ""), url = (note.match(/https?:\/\/\S+$/) || [])[0];
    const [body, when] = (url ? note.slice(0, -url.length).replace(/[ ·]+$/, "") : note).split(/ (?=발표 \d{4}-)/);
    h.append(el("p", "law", b.head.name), el("div", "meta", [b.head.scope, when].filter(Boolean).join(" · ")));
    h.append(el("div", "body", body));
    if (url && !short) { const a = el("a", "link", "발표 · 보도 원문 ↗"); a.href = url; a.target = "_blank"; a.rel = "noopener"; h.append(a); }
    box.append(h);
  }
  for (const c of (b.charts || []).slice(0, short ? 1 : 9)) { if (!short) box.append(el("p", "ptitle", c.title)); box.append(chartEl({ ...c, title: b.title + " " + c.title }, null, !short)); }
  const rows = (b.rows || []).filter(r => !short || ["실거래", "같은 단지", "지정 동", "겹친 정책"].includes(r.section) || /상승률 차/.test(r.metric));
  const t = el("table", "ptable"), hr = el("tr");
  for (const [lbl, n] of [["항목", 0], ["전", 1], ["후", 1], ["변화", 1]]) hr.append(el("th", n ? "n" : null, lbl));
  t.append(hr);
  let last = null;
  for (const r of rows.slice(0, short ? 12 : rows.length)) {
    if (r.section !== last) {   // 날짜 기간(실거래)은 묶음 머리에 한 번만
      const tr = el("tr", "grp"), td = el("td", null, r.section), range = /~/.test(r.before_period || "");
      if (range) td.append(el("small", null, " 전 " + day(r.before_period) + " · 후 " + day(r.after_period)));
      td.colSpan = 4; tr.append(td); t.append(tr); last = r.section;
    }
    const tr = el("tr"), name = el("td", "wrap");
    name.append(el("span", null, r.metric));
    if (r.scope) name.append(el("small", null, " " + r.scope));
    if (r.partial) name.append(el("span", "pill mid", "잠정"));
    if (r.note && (!short || r.section === "겹친 정책")) name.append(el("div", "pnote", r.note));
    const cell = (per, v, n) => {
      const td = el("td", "n"), sub = [/^\d{6}$/.test(per || "") && fmt("ym", per), n != null && nf.format(n) + "건"].filter(Boolean).join(" · ");
      td.append(el("span", null, v == null ? "-" : fmt(r.fmt, v))); if (sub) td.append(el("small", null, sub)); return td;
    };
    const ch = el("td", "n chg");
    ch.textContent = r.section === "겹친 정책" ? (r.change ?? 0) + "건"
      : (r.unit === "%p" || (r.unit === "%" && r.change_pct == null)) ? fmt("pp", r.change)
      : signed("pct", r.change_pct);
    tr.append(name, cell(r.before_period, r.before_value, r.n_before), cell(r.after_period, r.after_value, r.n_after), ch);
    t.append(tr);
  }
  const w = el("div", "tw"); w.append(t); box.append(w);
  box.append(el("p", "pnote", "발표 전후를 맞댄 값입니다. 같은 시기의 금리 · 다른 정책 · 계절도 함께 움직여 정책 하나의 효과로 나눌 수 없습니다."));
  return box;
}

function sec(title, lines, todo) {
  const s = el("div", "sec"); s.append(el("b", null, title));
  if (todo) s.append(el("div", "todo", "데이터 수집 전")); else for (const [l, c] of lines) s.append(el("div", c || null, l));
  return s;
}
function cardEl(b, compact) {
  const box = el("div", "card");
  if (!b.rows || !b.rows.length) { box.append(el("p", null, "맞는 단지가 없습니다. 지금 데이터는 서울 강남구 아파트만 있습니다.")); return box; }
  for (const r of b.rows.slice(0, compact ? 1 : 5)) {
    const miss = r.missing || [], has = k => !miss.some(m => m.startsWith(k));
    const head = el("div"); head.append(el("h3", null, r.complex_name));
    box.append(head, el("p", "addr", [r.address, r.road_addr && "도로명 " + r.road_addr].filter(Boolean).join(" · ")));
    if (r.pnu) box.append(el("div", "pnu", "PNU " + r.pnu));
    const g = el("div", "grid");
    g.append(sec("거래 · 최근 12개월", [[r.trade_count ? "평당 " + won(r.trade_per_pyeong) : "매매 없음", "big"],
      ["매매 " + r.trade_count + "건" + (r.trade_last_date ? " · 마지막 " + r.trade_last_date + " " + won(r.trade_last_amount) : "")],
      ["전세 " + r.jeonse_count + "건 평균 " + won(r.jeonse_avg) + (r.jeonse_ratio != null ? " · 전세가율 " + r.jeonse_ratio + "%" : "")]]));
    g.append(sec("토지 · 공시지가", [["공시지가 " + (r.land_price_per_m2 ? won(r.land_price_per_m2, "/㎡") + " (" + r.land_price_year + ")" : "-"), "big"],
      ["필지 " + (r.land_parcel_count || 0) + "개 · " + (r.land_category || "-") + " · " + (r.land_area_m2 != null ? nf.format(r.land_area_m2) + "㎡" : "-")]],
      !has("토지 지목") && !has("토지 면적") && !has("공시지가")));
    g.append(sec("용도지역 · 규제", [[(r.zones || "-").split(", ").filter(z => /지역$/.test(z)).join(", ") || "-", "big"], ["행위제한 규칙 " + (r.zone_rule_count || 0) + "개"],
      [(r.zones || "").split(", ").filter(z => z && !/지역$/.test(z)).slice(0, 4).join(" · ")]], !has("용도지역")));
    g.append(sec("건물 · 건축물대장", [[(r.bld_households != null ? nf.format(r.bld_households) + "세대" : "-") + " · " + (r.bld_floors_above || "-") + "층", "big"],
      ["준공 " + (r.build_year || "-") + " · 주차 " + (r.bld_parking != null ? nf.format(r.bld_parking) : "-") + "대"],
      ["건폐율 " + (r.bld_bc_rat != null ? r.bld_bc_rat + "%" : "-") + " · 용적률 " + (r.bld_vl_rat != null ? r.bld_vl_rat + "%" : "-")]], !has("건축물대장")));
    box.append(g);
    if (!compact && r.recent_deals && r.recent_deals.length) {
      const t = { columns: [{ key: "contract_date", label: "계약일", fmt: "date" }, { key: "area_m2", label: "전용", fmt: "m2" }, { key: "floor", label: "층", fmt: "int" }, { key: "amount", label: "매매가", fmt: "won" }], rows: r.recent_deals };
      const cap = el("div", "eyebrow", "최근 매매"); cap.style.margin = "12px 0 4px"; box.append(cap, tableEl(t));
    }
  }
  return box;
}

// 블록 하나. inline = 답 안에 짧게, 아니면 서랍에 전부. 기간을 바꾸면 다시 조회해 같은 자리에서 바꿈
function blockEl(item, idx, inlineMode) {
  const b = item.blocks[idx], wrap = el("div", "blk"); wrap.dataset.ref = b.ref;
  const hd = el("div", "hd"), t = el("div", "t"), tools = el("div", "tools");
  t.append(cite(b.ref, () => openDrawer(item, b.ref)), el("span", null, b.title)); hd.append(t, tools);
  const bd = el("div", "bd");
  let which = b.kind === "chart" && b.series.length > 1 && b.series.some(s => s.fmt !== b.series[0].fmt) ? 0 : null;
  const draw = () => {
    bd.textContent = "";
    if (b.kind === "table") { bd.className = "tw-wrap"; bd.append(tableEl(b, inlineMode ? 6 : 0)); }
    else if (b.kind === "chart") { bd.className = "bd"; bd.append(chartEl(b, which, !inlineMode)); }
    else if (b.kind === "document") { bd.className = ""; bd.append(docsEl(b, inlineMode)); }
    else if (b.kind === "card") { bd.className = "bd"; bd.append(cardEl(b, inlineMode)); }
    else if (b.kind === "map") { bd.className = "bd"; bd.append(mapEl(b, !inlineMode || !wide())); }
    else if (b.kind === "policy") { bd.className = "bd"; bd.append(policyEl(b, inlineMode)); }
  };
  if (which != null) {
    const seg = el("div", "seg"); seg.setAttribute("role", "group"); seg.setAttribute("aria-label", "보기");
    b.series.forEach((s, k) => { const x = btn(k === which ? "on" : "", s.label.replace(/ 중위가?$/, ""), () => { which = k; [...seg.children].forEach((c, j) => c.classList.toggle("on", j === k)); draw(); }); seg.append(x); });
    tools.append(seg);
  }
  if (b.requery) {
    const seg = el("div", "seg"); seg.setAttribute("role", "group"); seg.setAttribute("aria-label", b.requery.label);
    for (const n of b.requery.choices) {
      seg.append(btn(n === b.requery.current ? "on" : "", n % 12 === 0 ? (n / 12) + "년" : n + "개월", async () => {
        if (n === b.requery.current) return;
        wrap.classList.add("busy");
        try {
          const r = await fetch("/api/tool", { method: "POST", headers: { "Content-Type": "application/json", "X-Token": token }, body: JSON.stringify({ tool: b.tool, args: { ...b.args, [b.requery.param]: n }, ref: b.ref }) });
          const j = await r.json();
          if (!r.ok) throw new Error(j.error);
          item.blocks[idx] = j.block; saveItem(item);
          wrap.replaceWith(blockEl(item, idx, inlineMode));
          refreshTwin(item, idx, inlineMode);
        } catch (e) { wrap.classList.remove("busy"); alertLine(wrap, "다시 조회하지 못했습니다: " + e.message); }
      }));
    }
    tools.append(seg);
  }
  if ((b.kind === "table" || b.kind === "policy") && (b.rows || []).length) tools.append(btn("tbtn", "CSV", () => csv(b), b.title + " CSV로 받기"));
  wrap.append(hd, bd); draw();
  if (inlineMode && ((b.kind === "table" && (b.rows || []).length > 6) || b.kind === "document" || b.kind === "policy" || (b.kind === "card" && b.rows && b.rows.length))) {
    const more = { table: `${(b.rows || []).length}줄 모두 보기`, document: b.tool === "q_policy_events" ? "정책 목록 전체 보기" : "조문 원문 전체 보기", policy: "전후 비교 전체 보기 · 그래프" };
    wrap.append(btn("more", more[b.kind] || "단지 기록 전체 보기", () => openDrawer(item, b.ref)));
  }
  const ft = el("div", "ft", sourceLine(b)); wrap.append(ft);
  return wrap;
}
function alertLine(after, text) { const p = el("div", "err-msg", text); after.after(p); setTimeout(() => p.remove(), 5000); }
// 답 안과 서랍에 같은 블록이 둘 다 떠 있으면 나머지 하나도 바꿈
function refreshTwin(item, idx, inlineMode) {
  const ref = String(item.blocks[idx].ref);
  if (inlineMode && drawerItem === item) renderDrawer(item, activeRef);
  if (!inlineMode && item.el) { const old = item.el.querySelector(`.inline .blk[data-ref="${ref}"]`); if (old) old.replaceWith(blockEl(item, idx, true)); }
}

// ------------------------------------------------------------ 근거 서랍
let drawerItem = null, activeRef = null;
function renderDrawer(item, ref) {
  drawerItem = item; activeRef = ref;
  const tabs = $("tabs"), body = $("dbody"); tabs.textContent = ""; body.textContent = "";
  item.blocks.forEach((b, i) => {
    const t = btn("tab" + (b.ref === ref ? " on" : ""), null, () => renderDrawer(item, b.ref));
    t.setAttribute("role", "tab"); t.setAttribute("aria-selected", b.ref === ref);
    const c = el("span", "cite", String(b.ref)); t.append(c, el("span", null, b.title)); tabs.append(t);
    if (b.ref === ref) body.append(blockEl(item, i, false));
  });
  document.querySelectorAll(".a .cite.on").forEach(c => c.classList.remove("on"));
  if (item.el) item.el.querySelectorAll(".cite").forEach(c => { if (c.textContent === String(ref)) c.classList.add("on"); });
}
function openDrawer(item, ref) {
  if (!item.blocks.some(b => b.ref === ref)) return;
  renderDrawer(item, ref);
  app.classList.remove("nodrawer");
  if (!wide()) { app.classList.add("sheet"); $("scrim").hidden = false; }
}
function closeSheets() { app.classList.remove("sheet", "menu"); $("scrim").hidden = true; }
$("drawerClose").onclick = () => { if (wide()) app.classList.add("nodrawer"); else closeSheets(); };
$("drawerBtn").onclick = () => { app.classList.add("sheet"); $("scrim").hidden = false; };
$("menuBtn").onclick = () => { app.classList.add("menu"); $("scrim").hidden = false; };
$("scrim").onclick = closeSheets;

// ------------------------------------------------------------ 답 그리기
function stampEl(item) {
  const s = el("div", "stamp"), ol = el("ol");
  s.append(el("div", "seal", "근거"));
  if (!item.blocks.length) ol.append(el("li", null, "데이터 조회 없이 답함"));
  for (const b of item.blocks) { const li = el("li"); li.append(cite(b.ref, () => openDrawer(item, b.ref)), el("span", null, b.title + " · " + sourceLine(b))); ol.append(li); }
  for (const line of (item.footer || "").split("\n")) if (/→ 오류/.test(line)) ol.append(el("li", "err", "조회 실패: " + line.replace(/^- /, "").replace(/\(.*\)\s*→ 오류:/, " ·")));
  const v = item.verify;
  if (v && v.checked) {
    const miss = v.items.filter(x => !x.ok).map(x => x.text), li = el("li", "verify" + (miss.length ? " part" : ""));
    const sv = svg("svg", { width: 14, height: 14, viewBox: "0 0 12 12", "aria-hidden": "true" });
    sv.append(svg("path", { d: "M2 6.5l2.5 2.5L10 3.5", fill: "none", stroke: "currentColor", "stroke-width": 2 }));
    const t = el("span", null, miss.length ? `답의 숫자 ${v.checked}개 중 ${v.matched}개를 원자료에서 그대로 찾았어요` : `답의 숫자 ${v.checked}개를 모두 원자료에서 그대로 찾았어요`);
    if (miss.length) t.append(el("small", null, "바로 찾지 못한 숫자: " + miss.join(", ") + " (원자료로 계산한 값이거나 확인이 필요한 값)"));
    li.append(sv, t); ol.append(li);
  }
  s.append(ol); return s;
}
// 답이 다룬 단지 한 곳의 머리(카드 블록이 한 줄일 때만)
function subjectEl(item) {
  const b = item.blocks.find(x => x.kind === "card" && (x.rows || []).length === 1); if (!b) return null;
  const r = b.rows[0], h = el("div", "subject"), nm = el("div", "nm"), chips = el("div", "chips");
  nm.append(el("h2", null, r.complex_name || r.address || "-"), el("span", "ad", [r.address, r.road_addr].filter(Boolean).join(" · ")));
  if (r.pnu) nm.append(el("span", "pnu", "PNU " + r.pnu + (r.land_parcel_count > 1 ? ` · 필지 ${r.land_parcel_count}개` : "")));
  for (const t of [r.build_year && r.build_year + "년 준공", r.bld_households && nf.format(r.bld_households) + "세대", r.bld_floors_above && "최고 " + r.bld_floors_above + "층"]) if (t) chips.append(el("span", null, t));
  h.append(nm, chips); return h;
}
// 이어서 볼 것: 엔진이 고른 조회를 모델 없이 바로 엶. 연 블록은 이 답의 근거에 새 번호로 붙음
function nextEl(item) {
  if (!(item.next || []).length && !cardRow(item)) return null;
  const box = el("div", "next"), row = el("div", "row");
  box.append(el("span", "lbl", "이어서 볼 것 · 모델 요금 없이 바로 열림"));
  const r = cardRow(item);
  if (r && r.complex_name) {   // 같은 단지를 인쇄용 한 장으로
    const b = btn("nx", r.complex_name + " 필지 리포트", () => { setMode("report"); send(r.complex_name); });
    b.append(el("i", null, "바로")); row.append(b);
  }
  for (const n of item.next) {
    const b = btn("nx", n.label, async () => {
      b.classList.add("busy");
      const ref = Math.max(0, ...item.blocks.map(x => x.ref || 0)) + 1;
      try {
        const r = await fetch("/api/tool", { method: "POST", headers: { "Content-Type": "application/json", "X-Token": token }, body: JSON.stringify({ tool: n.tool, args: n.args, ref }) });
        const j = await r.json();
        if (!r.ok) throw new Error(j.error);
        item.blocks.push(j.block); item.next = item.next.filter(x => x !== n); saveItem();
        renderAnswer(item, item.el); openDrawer(item, ref);
      } catch (e) { b.classList.remove("busy"); alertLine(box, "열지 못했습니다: " + e.message); }
    });
    b.append(el("i", null, "바로")); row.append(b);
  }
  box.append(row); return box;
}
// 답 안에 바로 보일 블록: 카드 · 그래프 · 표는 앞에서 3개까지, 조문은 2개까지 짧게. 나머지는 근거 도장에서 서랍으로
// 단지 머리(subjectEl)를 그린 답이면 같은 내용인 단지 카드는 서랍에만 둠
function inlinePicks(blocks, skipCard) {
  const out = [], docs = [];
  blocks.forEach((b, i) => {
    if (skipCard && b.kind === "card") return;
    if (b.kind === "document") { if (docs.length < 2) docs.push(i); } else if (out.length < 3 && (b.rows || b.features || []).length) out.push(i);
  });
  return [...out, ...docs];
}
function renderAnswer(item, box) {
  box.textContent = ""; item.el = box;
  if (item.error) { box.append(el("div", "err-msg", item.error)); return; }
  if (item.choices) return box.append(choicesEl(item));
  if (item.kind === "report") return renderReport(item, box);
  if (item.kind === "compare") return renderCompare(item, box);
  const head = subjectEl(item); if (head) box.append(head);
  citeHandler = n => item.blocks.some(b => b.ref === n) ? () => openDrawer(item, n) : null;
  checks = (item.verify && item.verify.items) || [];
  box.append(md(item.answer || ""));
  citeHandler = null; checks = [];
  const inl = el("div", "inline a"); for (const i of inlinePicks(item.blocks, !!head)) inl.append(blockEl(item, i, true));
  if (inl.children.length) box.append(inl);
  const nx = nextEl(item); if (nx) box.append(nx);
  box.append(stampEl(item));
}

// ------------------------------------------------------------ 필지 리포트 · 두 단지 비교(모델 없이 마트 함수만, 요금 없음)
let mode = "ask";
const MODES = {
  ask: ["질문", "단지 이름, 주소, 번지, 법 조문 무엇이든. 예: 도곡렉슬 84㎡ 최근 시세와 땅 규제"],
  report: ["필지 리포트", "단지 이름이나 「동 지번」 하나. 예: 도곡렉슬, 도곡동 527"],
  compare: ["두 단지 비교", "두 단지를 쉼표로 나눠서. 예: 도곡렉슬, 래미안대치팰리스"],
};
function setMode(m) {
  mode = m;
  for (const b of $("modes").children) { const on = b.dataset.mode === m; b.classList.toggle("on", on); b.setAttribute("aria-selected", on); }
  q.placeholder = MODES[m][1];
}
for (const m in MODES) { const b = btn("", MODES[m][0], () => { setMode(m); q.focus(); }); b.dataset.mode = m; b.setAttribute("role", "tab"); $("modes").append(b); }
const splitPair = t => { const p = t.split(/\s*(?:,|\/|·|\s+vs\.?\s+|\s+와\s+|\s+과\s+)\s*/i).map(x => x.trim()).filter(Boolean); return p.length === 2 ? p : null; };

async function lookup(item, text, m) {
  let url;
  if (m === "report") url = "/api/report?q=" + encodeURIComponent(text);
  else {
    const p = splitPair(text);
    if (!p) throw new Error("두 단지를 쉼표로 나눠 넣어 주세요. 예: 도곡렉슬, 래미안대치팰리스");
    url = "/api/compare?a=" + encodeURIComponent(p[0]) + "&b=" + encodeURIComponent(p[1]); item.pair = p;
  }
  const r = await fetch(url, { headers: { "X-Token": token } }), j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || "연결 오류 " + r.status);
  item.kind = m;
  if (j.choices) { item.choices = j.choices; item.choiceFor = j.query; return; }
  item.blocks = j.blocks || []; item.subjects = j.subjects || [j.subject];
}
// 이름이 여러 단지에 맞거나 하나도 없을 때: 고르면 같은 방식으로 다시 엶
function choicesEl(item) {
  const box = el("div", "gap");
  if (!item.choices.length) { box.append(el("b", null, "맞는 단지가 없어요"), el("p", null, `「${item.choiceFor}」에 맞는 단지를 찾지 못했어요. 지금 데이터는 서울 강남구 아파트만 있어요.`)); return box; }
  box.append(el("b", null, `「${item.choiceFor}」에 맞는 단지가 여럿이에요. 하나를 고르세요`));
  const row = el("div", "row"); row.style.cssText = "display:flex;flex-wrap:wrap;gap:8px;margin-top:8px";
  for (const c of item.choices) row.append(btn("nx", c, () => {
    setMode(item.kind);
    send(item.kind === "compare" ? item.pair.map(x => x === item.choiceFor ? c : x).join(", ") : c);
  }));
  box.append(row); return box;
}
const cardRow = (item, k) => { const rows = (item.blocks.filter(b => b.kind === "card")[k || 0] || { rows: [] }).rows || []; return rows.length === 1 ? rows[0] : undefined; };
function fact(label, value, note, open) {
  const f = el("div", "fig"), s = el("span", "s");
  f.append(el("span", "l", label), el("span", "v" + (value.length > 9 ? " long" : ""), value));
  s.append(cite(1, open), " ", note || "");
  s.append(el("br"), el("span", "chk ok", "원자료 그대로")); f.append(s); return f;
}
function reportTools(item, title) {
  const bar = el("div", "rbar");
  bar.append(el("span", null, title + " · 모델 없이 공공기록만으로 만듦 · " + new Date().toISOString().slice(0, 10)));
  bar.append(btn("tbtn", "인쇄 · PDF로 저장", () => {
    item.el.classList.add("print-me"); app.classList.add("printing");
    const done = () => { app.classList.remove("printing"); item.el.classList.remove("print-me"); removeEventListener("afterprint", done); };
    addEventListener("afterprint", done); print();
  }));
  return bar;
}
function renderReport(item, box) {
  const r = cardRow(item), one = () => openDrawer(item, 1);
  const head = subjectEl(item); if (head) box.append(head);
  box.append(reportTools(item, "필지 리포트"));
  if (r) {
    const g = el("div", "figs"), zone = (r.zones || "").split(", ").filter(z => /지역$/.test(z)).join(", ");
    if (r.trade_count) g.append(fact("평당 매매가 · 최근 12개월", won(r.trade_per_pyeong), `매매 ${r.trade_count}건`, one));
    if (r.trade_last_amount) g.append(fact("마지막 매매", won(r.trade_last_amount), `${r.trade_last_date} · ${r.trade_last_area}㎡`, one));
    if (r.jeonse_ratio != null) g.append(fact("전세가율", r.jeonse_ratio + "%", `${r.jeonse_ratio_area}㎡ 같은 면적끼리 · 전세 ${r.jeonse_count}건`, one));
    if (r.land_price_per_m2) g.append(fact(`개별공시지가 · ${r.land_price_year}`, won(r.land_price_per_m2, "/㎡"), `필지 ${r.land_parcel_count}개 · ${r.land_category || "-"}`, one));
    if (zone) g.append(fact("용도지역", zone, `행위제한 규칙 ${r.zone_rule_count || 0}개`, one));
    if (r.bld_vl_rat != null) g.append(fact("용적률 · 건폐율", r.bld_vl_rat + "% · " + r.bld_bc_rat + "%", `건축물대장 · 주차 ${nf.format(r.bld_parking || 0)}대`, one));
    box.append(g);
  }
  const inl = el("div", "inline a");
  item.blocks.forEach((b, i) => { if (b.kind !== "card") inl.append(blockEl(item, i, true)); });
  box.append(inl);
  const miss = [...((r && r.missing) || []).map(m => m + ": 아직 수집 전이에요."),
    ...(r && r.bld_violation == null ? ["위반건축물 여부: 건축물대장에 이 항목 데이터가 아직 없어요."] : []),
    "근저당 · 가압류 같은 권리관계: 등기부는 모으지 않아요.", "지금 나온 매물 호가: 매물 데이터는 없어요. 실거래만 봤어요."];
  box.append(gaps(miss));
  box.append(stampEl(item));
}
// 두 단지 비교 표. 넷째 값이 차이 셈법: rel(오른쪽이 몇 % 높은지) · pp(%p 차) · yr(해 차) · 없음(면적이 달라 견주지 않음 등)
const CMP = [
  ["준공", r => r.build_year, v => v + "년", "yr"], ["세대", r => r.bld_households, v => nf.format(v) + "세대", "rel"],
  ["최고층", r => r.bld_floors_above, v => v + "층", "rel"], ["용적률", r => r.bld_vl_rat, v => v + "%", "pp"],
  ["평당 매매가 · 12개월", r => r.trade_count ? r.trade_per_pyeong : null, v => won(v), "rel"],
  ["매매 건수 · 12개월", r => r.trade_count, v => v + "건", "rel"],
  ["마지막 매매", r => r.trade_last_amount, (v, r) => won(v) + " (" + r.trade_last_area + "㎡ · " + r.trade_last_date + ")"],
  ["전세 평균 · 12개월", r => r.jeonse_avg, v => won(v), "rel"], ["전세가율", r => r.jeonse_ratio, (v, r) => v + "% (" + r.jeonse_ratio_area + "㎡)", "pp"],
  ["공시지가 ㎡당", r => r.land_price_per_m2, (v, r) => won(v) + " (" + r.land_price_year + ")", "rel"],
  ["세대당 주차", r => r.bld_parking && r.bld_households ? Math.round(r.bld_parking / r.bld_households * 100) / 100 : null, v => v + "대", "rel"],
  ["용도지역", r => (r.zones || "").split(", ").filter(z => /지역$/.test(z)).join(", ") || null, v => v],
];
const sign = x => (x > 0 ? "+" : "") + x;
function diff(kind, a, b) {
  if (typeof a !== "number" || typeof b !== "number") return "";
  if (kind === "yr") return sign(b - a) + "년";
  if (kind === "pp") return sign(Math.round((b - a) * 10) / 10) + "%p";
  if (kind === "rel" && a) return sign(Math.round((b / a - 1) * 1000) / 10) + "%";
  return "";
}
function renderCompare(item, box) {
  const a = cardRow(item, 0), b = cardRow(item, 1);
  const h = el("div", "subject"), nm = el("div", "nm");
  nm.append(el("h2", null, (item.subjects || []).join("  ·  ")), el("span", "ad", [a && a.address, b && b.address].filter(Boolean).join("  |  ")));
  h.append(nm); box.append(h, reportTools(item, "두 단지 비교"));
  if (a && b) {
    const t = el("table", "cmp"), hr = el("tr");
    hr.append(el("th", null, "항목"), el("th", null, a.complex_name), el("th", null, b.complex_name), el("th", "n", "차이"));
    t.append(hr);
    for (const [label, get, show, kind] of CMP) {
      const va = get(a), vb = get(b); if (va == null && vb == null) continue;
      const tr = el("tr");
      tr.append(el("th", null, label), el("td", null, va == null ? "-" : show(va, a)), el("td", null, vb == null ? "-" : show(vb, b)), el("td", "n", diff(kind, va, vb)));
      t.append(tr);
    }
    const w = el("div", "tw cmpw"); w.append(t); box.append(w);
    box.append(el("p", "fine left", "차이 = 오른쪽 단지가 왼쪽보다 몇 % 높은지(비율 항목은 %p). 마지막 매매는 면적이 달라 견주지 않아요. 모두 같은 마트 함수(단지 종합 카드 [1] [2])의 원자료 그대로예요."));
  }
  const inl = el("div", "inline a");
  item.blocks.forEach((bl, i) => { if (bl.kind !== "card") inl.append(blockEl(item, i, true)); });
  box.append(inl, stampEl(item));
}

// ------------------------------------------------------------ 대화
function newConv(title) { const c = { id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6), title, sid: null, items: [], at: Date.now() }; convs.unshift(c); return c; }
function saveItem() { if (cur) { cur.at = Date.now(); save(); renderConvs(); } }

function renderConvs() {
  const box = $("convs"); box.textContent = "";
  const today = new Date().toDateString(); let lastGroup = null;
  for (const c of convs) {
    const g = new Date(c.at).toDateString() === today ? "오늘" : "이전";
    if (g !== lastGroup) { box.append(el("h4", null, g)); lastGroup = g; }
    const row = el("div", "conv" + (cur && cur.id === c.id ? " on" : ""));
    row.append(btn("open", c.title, () => { openConv(c); closeSheets(); }),
      btn("del", "×", () => { convs = convs.filter(x => x !== c); save(); if (cur === c) reset(); else renderConvs(); }, c.title + " 지우기"));
    box.append(row);
  }
}
// 처음 화면에서는 입력창이 제목 아래에, 대화가 시작되면 화면 아래로
function showLog(on) { welcome.hidden = on; app.classList.toggle("home", !on); (on ? $("main") : $("slot")).append($("form")); }
function addQuestion(text) { log.append(el("div", "q", text)); }
function openConv(c) {
  cur = c; log.textContent = ""; showLog(true); $("topTitle").textContent = c.title;
  for (const it of c.items) { addQuestion(it.q); const box = el("div", "a"); log.append(box); renderAnswer(it, box); }
  const last = c.items[c.items.length - 1];
  if (last && last.blocks.length && wide()) openDrawer(last, last.blocks[0].ref); else clearDrawer();
  renderConvs(); scroll.scrollTop = scroll.scrollHeight;
}
const wide = () => !matchMedia("(max-width:1020px)").matches;
function clearDrawer() { drawerItem = null; if (wide()) app.classList.add("nodrawer"); $("tabs").textContent = ""; const body = $("dbody"); body.textContent = ""; body.append(emptyDrawer.cloneNode(true)); }
const emptyDrawer = $("dbody").firstElementChild.cloneNode(true);
function reset() { cur = null; log.textContent = ""; showLog(false); $("topTitle").textContent = "한필지"; clearDrawer(); renderConvs(); q.focus(); }
$("newChat").onclick = () => { reset(); closeSheets(); };

async function send(text) {
  text = (text ?? q.value).trim(); if (!text || busy) return;
  busy = true; go.disabled = true; q.value = ""; grow();
  if (!cur) cur = newConv(text.length > 40 ? text.slice(0, 40) + "…" : text);
  const conv = cur;   // 답을 기다리는 사이 다른 대화를 열어도 이 대화에 남김
  showLog(true); $("topTitle").textContent = cur.title; renderConvs();
  const m = mode, qtext = m === "ask" ? text : MODES[m][0] + " · " + text;
  addQuestion(qtext);
  const item = { q: qtext, answer: "", footer: "", blocks: [] }, box = el("div", "a"), steps = el("ul", "steps");
  box.append(steps); log.append(box);
  const step = label => { const li = el("li", null, label); steps.append(li); box.scrollIntoView({ block: "end", behavior: "smooth" }); return li; };
  let pending = step(m === "ask" ? "질문을 읽는 중" : "공공기록을 모으는 중(모델 없이)");
  if (m !== "ask") {
    try { await lookup(item, text, m); } catch (e) { item.error = String(e.message || e); }
  } else try {
    const r = await fetch("/api/ask/stream", { method: "POST", headers: { "Content-Type": "application/json", "X-Token": token }, body: JSON.stringify({ q: text, sid: conv.sid }) });
    if (!r.ok || !r.body) { const j = await r.json().catch(() => ({})); throw new Error(j.error || "연결 오류 " + r.status); }
    const reader = r.body.getReader(), dec = new TextDecoder(); let buf = "", done = false;
    while (!done) {
      const { value, done: end } = await reader.read(); if (end) break;
      buf += dec.decode(value, { stream: true });
      let k;
      while ((k = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, k); buf = buf.slice(k + 2);
        if (!chunk.startsWith("data: ")) continue;
        const e = JSON.parse(chunk.slice(6));
        if (e.type === "start") conv.sid = e.sid;
        else if (e.type === "tool") { pending.classList.add("done"); pending = step(e.label); }
        else if (e.type === "block") { item.blocks.push(e.block); }
        else if (e.type === "answer") { item.answer = e.answer; item.footer = e.footer; item.verify = e.verify; item.next = e.next; done = true; }
        else if (e.type === "error") { item.error = e.error; done = true; }
      }
    }
    if (!item.answer && !item.error) item.error = "답을 받지 못했습니다. 잠시 뒤 다시 물어 주세요.";
  } catch (e) { item.error = String(e.message || e); }
  renderAnswer(item, box);
  conv.items.push(item); conv.at = Date.now(); save(); renderConvs();
  if (cur === conv && item.blocks.length && wide()) openDrawer(item, item.blocks[0].ref);
  busy = false; go.disabled = !q.value.trim(); q.focus();
}

// ------------------------------------------------------------ 입력
function grow() { q.style.height = "auto"; q.style.height = Math.min(q.scrollHeight, 180) + "px"; go.disabled = busy || !q.value.trim(); }
q.addEventListener("input", grow);
q.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });
$("form").onsubmit = e => { e.preventDefault(); send(); };
for (const [k, t] of EXAMPLES) { const b = btn("ex", null, () => send(t)); b.append(el("small", null, k), el("span", null, t)); $("examples").append(b); }
renderConvs(); showLog(false); grow();
if (wide()) app.classList.add("nodrawer");
