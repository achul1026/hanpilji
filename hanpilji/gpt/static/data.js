"use strict";
// 데이터 현황 화면: /api/data-status 를 읽어 묶음마다 표로 그림(모델을 부르지 않음)
const token = (() => {
  const t = new URLSearchParams(location.search).get("token");
  try { if (t) localStorage.setItem("hanpilji.token", t); return t || localStorage.getItem("hanpilji.token") || ""; } catch (e) { return t || ""; }
})();
const out = document.getElementById("out");

function el(tag, attrs, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null) continue;
    if (k === "class") n.className = v; else n.setAttribute(k, v);
  }
  for (const c of kids) if (c != null) n.append(c);
  return n;
}
const fmt = (n) => Number(n).toLocaleString("ko-KR");
function period(a, b) {
  if (a && b && a !== b) return `${a} ~ ${b}`;
  return b || a || "-";
}
// 마지막 성공이 주기보다 많이 지났으면 눈에 띄게(매일 3일 · 매주 10일 · 그 밖 45일)
function stale(when, cycles) {
  if (!when) return false;
  const days = (Date.now() - new Date(when.replace(" ", "T") + ":00+09:00").getTime()) / 86400000;
  const lim = cycles.some((c) => /매일/.test(c || "")) ? 3 : cycles.some((c) => /매주/.test(c || "")) ? 10 : 45;
  return days > lim;
}
function safeUrl(u) {
  try { const x = new URL(u); return x.protocol === "https:" || x.protocol === "http:" ? x.href : null; } catch (e) { return null; }
}

function row(it) {
  const srcs = it.sources.length
    ? it.sources.map((s) => {
        const href = s.url && safeUrl(s.url);
        const name = href ? el("a", { href, target: "_blank", rel: "noopener" }, s.name) : s.name;
        const sub = [s.operator, s.cycle && `${s.cycle} 갱신`, s.license].filter(Boolean).join(" · ");
        return el("span", { class: "src" }, name, sub ? el("span", { class: "sub" }, sub) : null);
      })
    : [el("span", { class: "src" }, "직접 입력", el("span", { class: "sub" }, it.detail))];
  const old = stale(it.updated, it.sources.map((s) => s.cycle));
  return el("tr", {},
    el("td", { "data-k": "데이터" }, el("span", { class: "lbl" }, it.label), it.sources.length ? el("span", { class: "sub" }, it.detail) : null),
    el("td", { class: "num", "data-k": "양" }, fmt(it.rows) + "줄"),
    el("td", { class: "mono", "data-k": "기간" }, period(it.period_from, it.period_to)),
    el("td", { class: "mono" + (old ? " old" : ""), "data-k": "최신화", title: old ? "갱신 주기보다 오래됐어요" : null }, it.updated || "-"),
    el("td", { "data-k": "출처" }, ...srcs));
}

function render(d) {
  const items = d.groups.flatMap((g) => g.items);
  const srcCount = new Set(items.flatMap((i) => i.sources.map((s) => s.name))).size;
  const latest = items.map((i) => i.updated).filter(Boolean).sort().pop();
  out.replaceChildren(
    el("div", { class: "dsum" },
      el("div", {}, el("b", {}, fmt(d.total_rows)), el("span", {}, "전체 줄 수")),
      el("div", {}, el("b", {}, String(items.length)), el("span", {}, "데이터 묶음")),
      el("div", {}, el("b", {}, String(srcCount)), el("span", {}, "출처(수집처)")),
      el("div", {}, el("b", {}, latest || "-"), el("span", {}, "가장 최근 최신화"))),
    ...d.groups.map((g) => el("section", { class: "dgroup" },
      el("h2", {}, g.name),
      el("table", { class: "dtable" },
        el("thead", {}, el("tr", {}, ...["데이터", "양", "데이터 기간", "최신화", "출처"].map((h) => el("th", {}, h)))),
        el("tbody", {}, ...g.items.map(row))))),
    el("p", { class: "dnote" }, `숫자를 센 때: ${d.checked_at}`));
}

(async () => {
  try {
    const r = await fetch("/api/data-status", { headers: { "X-Token": token } });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || "읽지 못했어요");
    render(j);
  } catch (e) {
    out.replaceChildren(el("p", { class: "dmsg" }, `데이터 현황을 읽지 못했어요. ${e.message}`));
  }
})();
