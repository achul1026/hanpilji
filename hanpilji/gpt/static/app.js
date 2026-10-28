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
  ["순위", "강남구 최근 1년 전세 거래 많은 단지 5곳"],
  ["규제", "제2종일반주거지역에 휴게음식점 지을 수 있어?"],
  ["법령", "전월세 재계약 때 5% 상한 조문 찾아줘"],
  ["공시지가", "래미안대치팰리스 공시지가 몇 년 동안 얼마나 올랐어?"],
  ["지표", "기준금리랑 서울 아파트 매매가격지수 최근 2년"],
];

// ------------------------------------------------------------ 저장(이 브라우저에만)
function load() { try { return JSON.parse(localStorage.getItem(STORE)) || []; } catch (e) { return []; } }
// 화면 요소(el)는 빼고 저장. 저장 못 해도(사생활 보호 창 등) 화면은 그대로
function save() { try { localStorage.setItem(STORE, JSON.stringify(convs.slice(0, MAX_CONVS).map(strip))); } catch (e) { /* 무시 */ } }
function strip(c) { return { ...c, items: c.items.map(({ el: _, ...rest }) => rest) }; }
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
function md(text) {
  const box = el("div", "md"), lines = text.replace(/\r/g, "").split("\n"); let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) { i++; continue; }
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
    while (i < lines.length && lines[i].trim() && !LI.test(lines[i]) && !HD.test(lines[i]) && !isTable(lines, i)) para.push(lines[i++]);
    if (!para.length) para.push(lines[i++]);
    const p = el("p"); para.forEach((x, k) => { if (k) p.append(el("br")); inline(p, x); }); box.append(p);
  }
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
      tr.append(el("td", NUMERIC.has(c.fmt) ? "n" : (c.key === "condition_text" ? "wrap" : null), fmt(c.fmt, v)));
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
    if (!short && it.url) { const a = el("a", "link", "법제처 원문 ↗"); a.href = it.url; a.target = "_blank"; a.rel = "noopener"; d.append(a); }
    box.append(d);
  }
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
  if (b.kind === "table" && (b.rows || []).length) tools.append(btn("tbtn", "CSV", () => csv(b), b.title + " CSV로 받기"));
  wrap.append(hd, bd); draw();
  if (inlineMode && ((b.kind === "table" && (b.rows || []).length > 6) || b.kind === "document" || (b.kind === "card" && b.rows && b.rows.length))) {
    wrap.append(btn("more", b.kind === "table" ? `${b.rows.length}줄 모두 보기` : b.kind === "document" ? "조문 원문 전체 보기" : "단지 기록 전체 보기", () => openDrawer(item, b.ref)));
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
  s.append(ol); return s;
}
// 답 안에 바로 보일 블록: 카드 · 그래프 · 표는 앞에서 3개까지, 조문은 2개까지 짧게. 나머지는 근거 도장에서 서랍으로
function inlinePicks(blocks) {
  const out = [], docs = [];
  blocks.forEach((b, i) => { if (b.kind === "document") { if (docs.length < 2) docs.push(i); } else if (out.length < 3 && (b.rows || []).length) out.push(i); });
  return [...out, ...docs];
}
function renderAnswer(item, box) {
  box.textContent = ""; item.el = box;
  if (item.error) { box.append(el("div", "err-msg", item.error)); return; }
  citeHandler = n => item.blocks.some(b => b.ref === n) ? () => openDrawer(item, n) : null;
  box.append(md(item.answer || ""));
  citeHandler = null;
  const inl = el("div", "inline a"); for (const i of inlinePicks(item.blocks)) inl.append(blockEl(item, i, true));
  if (inl.children.length) box.append(inl);
  box.append(stampEl(item));
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
function showLog(on) { welcome.hidden = on; }
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
  showLog(true); $("topTitle").textContent = cur.title; renderConvs();
  addQuestion(text);
  const item = { q: text, answer: "", footer: "", blocks: [] }, box = el("div", "a"), steps = el("ul", "steps");
  box.append(steps); log.append(box);
  const step = label => { const li = el("li", null, label); steps.append(li); box.scrollIntoView({ block: "end", behavior: "smooth" }); return li; };
  let pending = step("질문을 읽는 중");
  try {
    const r = await fetch("/api/ask/stream", { method: "POST", headers: { "Content-Type": "application/json", "X-Token": token }, body: JSON.stringify({ q: text, sid: cur.sid }) });
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
        if (e.type === "start") cur.sid = e.sid;
        else if (e.type === "tool") { pending.classList.add("done"); pending = step(e.label); }
        else if (e.type === "block") { item.blocks.push(e.block); }
        else if (e.type === "answer") { item.answer = e.answer; item.footer = e.footer; done = true; }
        else if (e.type === "error") { item.error = e.error; done = true; }
      }
    }
    if (!item.answer && !item.error) item.error = "답을 받지 못했습니다. 잠시 뒤 다시 물어 주세요.";
  } catch (e) { item.error = String(e.message || e); }
  renderAnswer(item, box);
  cur.items.push(item); saveItem(item);
  if (item.blocks.length && wide()) openDrawer(item, item.blocks[0].ref);
  busy = false; go.disabled = !q.value.trim(); q.focus();
}

// ------------------------------------------------------------ 입력
function grow() { q.style.height = "auto"; q.style.height = Math.min(q.scrollHeight, 180) + "px"; go.disabled = busy || !q.value.trim(); }
q.addEventListener("input", grow);
q.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });
$("form").onsubmit = e => { e.preventDefault(); send(); };
for (const [k, t] of EXAMPLES) { const b = btn("ex", null, () => send(t)); b.append(el("small", null, k), el("span", null, t)); $("examples").append(b); }
renderConvs(); grow();
if (wide()) app.classList.add("nodrawer");
