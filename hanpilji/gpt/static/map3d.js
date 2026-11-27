// 답 카드 아래 「3D 지도 · 주변」(2026-11-27). /api/map3d 가 준 필지 · 건물 · 도로 · 역 · 학교 · 주변 단지로
// 건물을 층수만큼 세운 도면식 3D 지도를 그림(직교 카메라, 해 그림자, 윤곽선). three.js는 처음 펼칠 때만 받음.
// 건물 색 · 창 · 옥상 설비 · 가로수는 용도와 위치에 따라 자동으로 그린 것이라 실제와 다를 수 있음.
// 한 화면에 움직이는 3D 지도는 하나만 둠(WebGL 창 수 제한). 다른 지도를 펼치면 앞의 것은 그림으로 멈춤.
(function () {
"use strict";
const THREE_URL = "https://cdn.jsdelivr.net/npm/three@0.147.0/";
const nf = new Intl.NumberFormat("ko-KR");
const cache = new Map();          // 단지명 → 자료(같은 답을 다시 열 때 다시 받지 않음)
let threeP = null, live = null;

function script(src) {
  return new Promise((ok, no) => { const s = document.createElement("script"); s.src = src; s.onload = ok; s.onerror = () => no(new Error("three.js를 받지 못했어요")); document.head.append(s); });
}
function loadThree() {
  if (window.THREE && THREE.OrbitControls) return Promise.resolve();
  return threeP || (threeP = script(THREE_URL + "build/three.min.js")
    .then(() => Promise.all([script(THREE_URL + "examples/js/controls/OrbitControls.js"), script(THREE_URL + "examples/js/utils/BufferGeometryUtils.js")])));
}
const won = v => v >= 1e8 ? (Math.round(v / 1e6) / 100) + "억" : nf.format(Math.round(v / 1e4)) + "만";
const cname = c => /^\(/.test(c.n) ? (c.k || "") + " " + c.n.replace(/[()]/g, "") : c.n;
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
const polysOf = g => !g || !g.length ? [] : typeof g[0][0][0] === "number" ? [g] : g;   // Polygon · MultiPolygon 좌표 → 다각형 목록
function hash(s, k) { let h = 2166136261 ^ (k || 0); for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return ((h >>> 0) % 100000) / 100000; }
function area(r) { let a = 0; for (let i = 0; i < r.length; i++) { const p = r[i], q = r[(i + 1) % r.length]; a += p[0] * q[1] - q[0] * p[1]; } return a / 2; }
function inRing(x, y, r) { let c = false; for (let i = 0, j = r.length - 1; i < r.length; j = i++) { const a = r[i], b = r[j]; if ((a[1] > y) !== (b[1] > y) && x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]) c = !c; } return c; }
function inPoly(x, y, poly) { if (!inRing(x, y, poly[0])) return false; for (let k = 1; k < poly.length; k++) if (inRing(x, y, poly[k])) return false; return true; }
function hull(pts) {
  const p = pts.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]), cr = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lo = [], up = [];
  for (const q of p) { while (lo.length >= 2 && cr(lo[lo.length - 2], lo[lo.length - 1], q) <= 0) lo.pop(); lo.push(q); }
  for (const q of p.reverse()) { while (up.length >= 2 && cr(up[up.length - 2], up[up.length - 1], q) <= 0) up.pop(); up.push(q); }
  return lo.slice(0, -1).concat(up.slice(0, -1));
}
function minRect(ring) {   // 가장 작은 돌린 직사각형: 가운데, 긴 변 방향, 긴 변, 짧은 변
  const h = hull(ring); let best = null;
  for (let i = 0; i < h.length; i++) {
    const a = h[i], b = h[(i + 1) % h.length], ang = Math.atan2(b[1] - a[1], b[0] - a[0]), c = Math.cos(ang), s = Math.sin(ang);
    let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
    for (const q of h) { const u = q[0] * c + q[1] * s, v = -q[0] * s + q[1] * c; x0 = Math.min(x0, u); x1 = Math.max(x1, u); y0 = Math.min(y0, v); y1 = Math.max(y1, v); }
    const A = (x1 - x0) * (y1 - y0);
    if (!best || A < best.A) { const u = (x0 + x1) / 2, v = (y0 + y1) / 2; best = { A, cx: u * c - v * s, cy: u * s + v * c, ang, L: x1 - x0, W: y1 - y0 }; }
  }
  if (best && best.L < best.W) best = { ...best, ang: best.ang + Math.PI / 2, L: best.W, W: best.L };
  return best;
}

// 건물 모양: 용도 · 이름 · 층수로 셋으로 나눔(다세대 · 근린 / 공동주택 / 업무 · 상업)
const APT = /아파트|APT|래미안|자이|푸르지오|아이파크|롯데캐슬|더샵|힐스테이트|e편한|렉슬|타워팰리스|현대|은마|선경|미도|우성|쌍용|삼성|한신|개포|주공/;
const OFC = /빌딩|타워|센터|TOWER|오피스|프라자|PLAZA|은행|호텔|병원|백화점/;
function styleOf(b) {
  const f = Math.max(1, b.f || 1), key = JSON.stringify(polysOf(b.g)[0][0][0]), r = hash(key);
  let c = "v";
  if ((b.u === "공동주택" && f >= 6) || (APT.test(b.n) && f >= 6)) c = "a";
  else if (/업무|판매|교육|숙박|의료|문화/.test(b.u || "") || f >= 8 || OFC.test(b.n)) c = "o";
  let fh, wall, roof, wt;
  if (c === "v") { fh = 2.9; wall = ["#b65a43", "#b65a43", "#a8513d", "#c06a4c", "#9c6b4e", "#d6c09c", "#ece8e0", "#e4e0d6"][Math.floor(r * 8)]; roof = ["#5fa88d", "#5fa88d", "#5fa88d", "#6aae8f", "#c3beb5", "#c3beb5", "#d9d5cd", "#5fa88d"][Math.floor(hash(key, 1) * 8)]; wt = 1; }
  else if (c === "a") { fh = 2.8; wall = ["#efece6", "#ebe7de", "#f1eee8"][Math.floor(r * 3)]; roof = ["#c9c5bd", "#5fa88d"][Math.floor(hash(key, 1) * 1.6)]; wt = 3; }
  else { fh = 3.6; if (f >= 12 || hash(key, 2) < 0.35) { wall = ["#8fa9be", "#9fb3c2", "#7f9bb3"][Math.floor(r * 3)]; wt = 2; } else { wall = ["#cfccc6", "#d9d5cd", "#bdbab4", "#e6e2da"][Math.floor(r * 4)]; wt = 4; } roof = ["#d6d2cb", "#cdc9c1"][Math.floor(hash(key, 1) * 2)]; }
  return { c, f, fh, H: Math.round((f * fh + 0.6) * 10) / 10, wall, roof, wt, key };
}
function roofItems(b, st) {   // 옥탑 계단실 · 물탱크 · 실외기 · 태양광 · 설비실(위치는 어림)
  const poly = polysOf(b.g)[0], ring = poly[0], m = minRect(ring); if (!m) return [];
  const ar = Math.abs(area(ring)), ca = Math.cos(m.ang), sa = Math.sin(m.ang), out = [], k = st.key;
  const fits = (x, y, w, d) => [[-1, -1], [1, -1], [1, 1], [-1, 1]].every(([i, j]) => {
    const dx = i * (w / 2 + 0.6), dy = j * (d / 2 + 0.6); return inPoly(x + dx * ca - dy * sa, y + dx * sa + dy * ca, poly); });
  const put = (dx, dy, w, d, h, col, kind) => { const x = m.cx + dx * ca - dy * sa, y = m.cy + dx * sa + dy * ca; if (kind === "s" || fits(x, y, w, d)) out.push([x, y, w, d, h, m.ang, col, kind || "b"]); };
  if (ar > 40 && st.f >= 3) put(-m.L * 0.18, m.W * 0.12, Math.min(4.2, m.L * 0.3), Math.min(3.4, m.W * 0.35), 2.8, "#ece9e3");
  if (st.c === "v" && ar > 40 && hash(k, 3) < 0.7) put(m.L * 0.22, 0, 1.6, 1.6, 1.8, "#7b9cc0", "t");
  for (let i = 0; i < Math.floor(Math.min(5, ar / 60)); i++) put(m.L * (hash(k, 10 + i) - .5) * .7, m.W * (hash(k, 20 + i) - .5) * .7, 0.9, 0.6, 0.7, "#f4f2ee");
  if (st.c !== "v" && ar > 300 && ar < 2500 && hash(k, 4) < 0.45) { const n = Math.floor(m.W * 0.5 / 2.2); for (let q = -n; q <= n; q += 2) put(m.L * 0.12, q * 2.2, m.L * 0.45, 1.4, 0.35, "#33415e", "s"); }
  if (st.c === "o" && ar > 250) put(m.L * 0.2, -m.W * 0.15, Math.min(8, m.L * 0.25), Math.min(6, m.W * 0.3), 2.4, "#d9d6d0");
  return out.filter(it => it[7] !== "s" || fits(it[0], it[1], it[2] * 0.3, it[3]));
}
function trees(D, bboxes) {   // 도로 포장면 가장자리 1.5m 밖을 따라 18m 간격(실제 가로수 위치 아님)
  const out = [], half = D.half;
  for (const poly of polysOf(D.roads && D.roads.coordinates)) for (const ring of poly) {
    let acc = 9;
    for (let i = 0; i < ring.length; i++) {
      const a = ring[i], b = ring[(i + 1) % ring.length], L = Math.hypot(b[0] - a[0], b[1] - a[1]); if (L < 0.01) continue;
      const nx = (b[1] - a[1]) / L, ny = -(b[0] - a[0]) / L;
      while (acc < L) {
        const x = a[0] + (b[0] - a[0]) * acc / L, y = a[1] + (b[1] - a[1]) * acc / L;
        let px = x + nx * 1.5, py = y + ny * 1.5; if (inPoly(px, py, poly)) { px = x - nx * 1.5; py = y - ny * 1.5; }
        if (Math.abs(px) < half - 2 && Math.abs(py) < half - 2 && !bboxes.some(bb => px > bb[0] - 1.5 && px < bb[2] + 1.5 && py > bb[1] - 1.5 && py < bb[3] + 1.5 && bb[4](px, py)))
          out.push([px, py, 0.85 + hash(px.toFixed(1) + py.toFixed(1)) * 0.35, ["#e9b93a", "#e3ad2f", "#f0c64a", "#d9a12a", "#7d9a55"][Math.floor(hash(px + "," + py, 7) * 5)]]);
        acc += 18;
      }
      acc -= L;
    }
  }
  return out.slice(0, 2500);
}

const VS = `attribute vec3 aCol;attribute vec3 aRoof;attribute vec4 aWin;attribute float aId;attribute float aCls;
varying vec3 vCol;varying vec3 vRoof;varying vec4 vWin;varying float vId;varying float vCls;varying vec3 vN;varying vec3 vP;
#ifdef TREE
attribute vec3 iCol;attribute float iId;
#endif
void main(){vec4 p=vec4(position,1.);vec3 n=normal;
#ifdef USE_INSTANCING
p=instanceMatrix*p;n=mat3(instanceMatrix)*n;
#endif
p=modelMatrix*p;vP=p.xyz;vN=normalize(mat3(modelMatrix)*n);
#ifdef TREE
vCol=iCol;vRoof=iCol;vWin=vec4(0.);vId=iId;vCls=2.;
#else
vCol=aCol;vRoof=aRoof;vWin=aWin;vId=aId;vCls=aCls;
#endif
gl_Position=projectionMatrix*viewMatrix*p;}`;
const FS = `uniform float uMode;uniform vec3 uSun;uniform mat4 uSunVP;uniform sampler2D uShadow;uniform float uTexel;uniform sampler2D uSt;uniform vec2 uStSz;
varying vec3 vCol;varying vec3 vRoof;varying vec4 vWin;varying float vId;varying float vCls;varying vec3 vN;varying vec3 vP;
float shadowAt(vec3 P,vec3 N){vec4 s=uSunVP*vec4(P+N*0.35,1.);vec3 q=s.xyz/s.w*0.5+0.5;
 if(q.x<0.||q.x>1.||q.y<0.||q.y>1.)return 0.;float sh=0.;
 for(int i=-1;i<=1;i++)for(int j=-1;j<=1;j++){float d=texture2D(uShadow,q.xy+vec2(float(i),float(j))*uTexel).r;sh+=step(d,q.z-0.0004);}
 return sh/9.;}
float box(float x,float a,float b){return step(a,x)*step(x,b);}
float edge(float x,float a,float b,float w){float fw=fwidth(x)*w;return (1.-smoothstep(0.,fw,abs(x-a)))+(1.-smoothstep(0.,fw,abs(x-b)));}
void main(){vec3 N=normalize(vN);
 if(uMode>0.5){float nh=floor(N.x*4.+.5)+9.*floor(N.y*4.+.5)+81.*floor(N.z*4.+.5);gl_FragColor=vec4(vId,vCls,nh,gl_FragCoord.z);return;}
 float st=floor(texture2D(uSt,(vec2(mod(vId,256.),floor(vId/256.))+.5)/uStSz).r*255.+.5);
 float roof=step(0.6,N.y);vec3 base=mix(vCol,vRoof,roof);
 if(st>2.5&&st<3.5)base=roof>0.5?vec3(0.247,0.435,0.82):vec3(0.478,0.627,0.902);
 if(st>4.5&&st<5.5)base=mix(base,vec3(0.95,0.66,0.33),roof>0.5?0.6:0.35);
 if(vCls<0.5){if(st>1.5&&st<2.5)base=vec3(0.55,0.67,0.93);if(st>3.5&&st<4.5)base=mix(base,vec3(0.96,0.7,0.42),0.6);}
 float wt=vWin.z;
 if(wt>0.5&&roof<0.5&&abs(N.y)<0.3){
  vec2 T=normalize(vec2(-N.z,N.x));float u=dot(vP.xz,T);float v=vP.y;float fh=vWin.x;float ws=vWin.y;
  if(v>0.4&&v<vWin.w-0.9){float fy=fract(v/fh);float fx=fract(u/ws);vec3 g=vec3(0.29,0.34,0.4);float ink=0.;
   if(wt<1.5){float w=box(fx,.28,.72)*box(fy,.32,.82);base=mix(base,g,w*.85);ink=edge(fx,.28,.72,1.2)*box(fy,.32,.82)+edge(fy,.32,.82,1.2)*box(fx,.28,.72);}
   else if(wt<2.5){float sp=1.-smoothstep(0.,.1,fy);base=mix(base*1.05,base*0.82,0.35*smoothstep(.1,.9,fy));base=mix(base,vec3(.86,.86,.84),sp*.6);ink=edge(fract(u/1.6),0.,1.,0.9)*0.55+edge(fy,0.,1.,0.9)*0.55;}
   else if(wt<3.5){float rail=box(fy,0.,.34);base=mix(base,g*1.15,box(fy,.42,.92)*.7);base=mix(base,base*1.06,rail);ink=edge(fy,.34,.42,1.0)*0.8+edge(fract(u/ws),0.,1.,0.8)*box(fy,.42,.92)*0.5;}
   else{float w=box(fx,.14,.86)*box(fy,.3,.86);base=mix(base,g,w*.8);ink=(edge(fx,.14,.86,1.1)*box(fy,.3,.86)+edge(fy,.3,.86,1.1)*box(fx,.14,.86))*0.8;}
   if(v<fh&&wt!=1.&&wt!=3.){base=mix(base,g*1.1,box(fy,.1,.85)*.7);}
   base=mix(base,vec3(.165,.149,.133),clamp(ink,0.,1.)*0.55);}
 }
 float ndl=dot(N,uSun);float sh=shadowAt(vP,N);float lit=(ndl>0.02?1.:0.)*(1.-sh);
 float face=roof>0.5?1.0:(abs(N.y)<0.3?0.94:0.97);
 vec3 shade=base*vec3(0.70,0.72,0.80);if(vCls<0.5)shade=base*vec3(0.87,0.88,0.92);
 gl_FragColor=vec4(mix(shade,base*face,lit),1.);}`;
const INK = `uniform sampler2D tC;uniform sampler2D tG;uniform vec2 px;varying vec2 vU;
 float ink(vec4 g,vec2 o){vec4 n=texture2D(tG,vU+o*px);
  if(abs(n.r-g.r)>0.5){bool gb=g.g>0.5&&g.g<1.5||g.g>2.5,nb=n.g>0.5&&n.g<1.5||n.g>2.5;
   if(nb&&(n.a<g.a||!gb))return 1.;if(gb&&!nb)return 0.;if(n.g>1.5&&n.g<2.5&&(n.a<g.a||g.g<0.5))return 0.6;
   if(g.g<0.5&&n.g<0.5&&g.g>-0.5&&n.g>-0.5)return 0.22;return 0.;}
  if(g.g>0.5&&g.g<1.5&&abs(n.b-g.b)>0.5)return 0.6;return 0.;}
 void main(){vec4 c=texture2D(tC,vU);vec4 g=texture2D(tG,vU);float e=0.;
  e=max(e,ink(g,vec2(2.4,0.)));e=max(e,ink(g,vec2(-2.4,0.)));e=max(e,ink(g,vec2(0.,2.4)));e=max(e,ink(g,vec2(0.,-2.4)));
  e=max(e,ink(g,vec2(1.7,1.7)));e=max(e,ink(g,vec2(-1.7,1.7)));e=max(e,ink(g,vec2(1.7,-1.7)));e=max(e,ink(g,vec2(-1.7,-1.7)));
  float f=0.;f=max(f,ink(g,vec2(1.,0.)));f=max(f,ink(g,vec2(0.,1.)));f=max(f,ink(g,vec2(-1.,0.)));f=max(f,ink(g,vec2(0.,-1.)));
  float k=max(e>=0.99?1.:0.,min(f,0.75));gl_FragColor=vec4(mix(c.rgb,vec3(.165,.149,.133),k),1.);}`;
const GC = { 대: "#ece8df", 공: "#c3d9a6", 임: "#b4cf98", 학: "#e7ddc6", 천: "#a9c8de", 구: "#a9c8de", 유: "#a9c8de", 주: "#d9d6cf", 철: "#d6d0c4", 체: "#c3d9a6", 종: "#e9e5dc", 잡: "#e9e5dc", 전: "#dfe4c8", 답: "#dfe4c8", 도: "#e1ddd4" };
const POI_COL = { 지하철역: "#2f6b4f", 초등학교: "#a1541f", 중학교: "#a1541f", 고등학교: "#a1541f" };
const BLD_KIND = { v: "다세대 · 근린생활", a: "공동주택", o: "업무 · 상업" };

// 3D 장면 하나(그림을 그릴 상자 frame, 자료 D). 겹친 2D 캔버스에 핀 · 이름표를 그림
function Scene(frame, D, ui) {
  const T = THREE, C = s => new T.Color(s);
  let W = frame.clientWidth, H = frame.clientHeight;
  const PR = Math.min(2, Math.max(1.5, devicePixelRatio || 1)), DPR = Math.min(2, devicePixelRatio || 1);
  const RD = new T.WebGLRenderer({ antialias: false, preserveDrawingBuffer: true }); RD.setPixelRatio(PR); RD.setSize(W, H);
  frame.prepend(RD.domElement);
  const ov = el("canvas", "m3-ov"), ctx = ov.getContext("2d"); frame.append(ov);
  const gl = RD.getContext(), floatOK = RD.capabilities.isWebGL2 && !!gl.getExtension("EXT_color_buffer_float");
  const scene = new T.Scene();
  const U = { uMode: { value: 0 }, uSun: { value: new T.Vector3(0, 1, 0) }, uSunVP: { value: new T.Matrix4() }, uShadow: { value: null }, uTexel: { value: 1 / 2048 }, uSt: { value: null }, uStSz: { value: new T.Vector2(256, 1) } };
  const mk = t => new T.ShaderMaterial({ uniforms: U, vertexShader: VS, fragmentShader: FS, defines: t ? { TREE: 1 } : {}, extensions: { derivatives: true } });
  const geos = []; let nid = 1; const INFO = {}, PID = {}, BIDS = [];
  const v2 = r => r.map(q => new T.Vector2(q[0], q[1]));
  function paint(g, col, roof, win, id, cls) {
    if (g.index) g = g.toNonIndexed(); const n = g.attributes.position.count;
    const a = new Float32Array(n * 3), b = new Float32Array(n * 3), w = new Float32Array(n * 4), ia = new Float32Array(n), ca = new Float32Array(n);
    for (let i = 0; i < n; i++) { a[i * 3] = col.r; a[i * 3 + 1] = col.g; a[i * 3 + 2] = col.b; b[i * 3] = roof.r; b[i * 3 + 1] = roof.g; b[i * 3 + 2] = roof.b; w.set(win, i * 4); ia[i] = id; ca[i] = cls; }
    g.setAttribute("aCol", new T.BufferAttribute(a, 3)); g.setAttribute("aRoof", new T.BufferAttribute(b, 3)); g.setAttribute("aWin", new T.BufferAttribute(w, 4)); g.setAttribute("aId", new T.BufferAttribute(ia, 1)); g.setAttribute("aCls", new T.BufferAttribute(ca, 1));
    for (const k of Object.keys(g.attributes)) if (!["position", "normal", "aCol", "aRoof", "aWin", "aId", "aCls"].includes(k)) g.deleteAttribute(k);
    geos.push(g);
  }
  const shape = poly => { const s = new T.Shape(v2(poly[0])); for (const h of poly.slice(1)) if (h.length > 2) s.holes.push(new T.Path(v2(h))); return s; };
  const flat = (poly, y) => { const g = new T.ShapeGeometry(shape(poly)); g.rotateX(-Math.PI / 2); g.translate(0, y || 0, 0); return g; };
  const extr = (s, d, y0) => { const g = new T.ExtrudeGeometry(s, { depth: d, bevelEnabled: false }); g.rotateX(-Math.PI / 2); g.translate(0, y0, 0); return g; };
  const half = D.half;
  { const g = new T.BoxGeometry(half * 2, 6, half * 2); g.translate(0, -3.05, 0); paint(g, C("#d6d0c4"), C("#ece8df"), [0, 0, 0, 0], nid++, 3); }
  const target = new Set(D.target || []);
  for (const p of D.parcels || []) { const id = nid++; PID[p.p] = id; INFO[id] = { k: "p", p }; const c = C(GC[p.k] || "#e9e5dc"); for (const poly of polysOf(p.g)) if (poly[0].length > 2) paint(flat(poly), c, c, [0, 0, 0, 0], id, 0); }
  for (const poly of polysOf(D.roads && D.roads.coordinates)) if (poly[0].length > 2) { const c = C("#cdc9c1"); paint(flat(poly, 0.06), c, c, [0, 0, 0, 0], nid++, 0); }
  const B = D.buildings || [], bboxes = [];
  B.forEach((b, bi) => {
    const id = nid++, ids = [id]; BIDS[bi] = ids; INFO[id] = { k: "b", b };
    const polys = polysOf(b.g).filter(p => p[0].length > 2); if (!polys.length) return;
    const st = b.st = styleOf(b); b.H = st.H; const wc = C(st.wall), rc = C(st.roof);
    let cx = 0, cy = 0, x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9; const r0 = polys[0][0];
    for (const q of r0) { cx += q[0]; cy += q[1]; x0 = Math.min(x0, q[0]); x1 = Math.max(x1, q[0]); y0 = Math.min(y0, q[1]); y1 = Math.max(y1, q[1]); }
    b.cx = cx / r0.length; b.cy = cy / r0.length; b.area = Math.abs(area(r0)); bboxes.push([x0, y0, x1, y1, (x, y) => polys.some(p => inPoly(x, y, p))]);
    for (const poly of polys) paint(extr(shape(poly), st.H, 0), wc, rc, [st.fh, st.wt == 1 ? 3.4 : st.wt == 3 ? 4.2 : st.wt == 4 ? 2.2 : 1.6, st.wt, st.H], id, 1);
    for (const ip of polysOf(b.i)) if (ip[0].length > 3 && polys.length === 1) { const s = shape(polys[0]); s.holes = [new T.Path(v2(ip[0]))]; paint(extr(s, 0.9, st.H), wc, wc, [0, 0, 0, 0], id, 1); }
    for (const it of roofItems(b, st)) {
      const [x, y, w, d, h, ang, col, k] = it, iid = nid++; ids.push(iid); INFO[iid] = { k: "b", b }; let g;
      if (k === "t") { g = new T.CylinderGeometry(w / 2, w / 2, h, 10); g.translate(0, h / 2, 0); }
      else if (k === "s") { g = new T.BoxGeometry(w, 0.25, d); g.rotateX(-0.35); g.translate(0, 0.6, 0); }
      else { g = new T.BoxGeometry(w, h, d); g.translate(0, h / 2, 0); }
      g.rotateY(ang); g.translate(x, st.H, -y); const c = C(col); paint(g, c, c, [0, 0, 0, 0], iid, 1);
    }
  });
  const mesh = new T.Mesh(T.BufferGeometryUtils.mergeBufferGeometries(geos, false), mk(false)); scene.add(mesh);
  geos.forEach(g => g.dispose());
  const TR = trees(D, bboxes);
  if (TR.length) {
    const crown = new T.IcosahedronGeometry(2.4, 0); crown.translate(0, 6, 0); const trunk = new T.CylinderGeometry(0.22, 0.3, 4.2, 5); trunk.translate(0, 2.1, 0);
    const tg = T.BufferGeometryUtils.mergeBufferGeometries([crown.toNonIndexed(), trunk.toNonIndexed()]), n = TR.length, im = new T.InstancedMesh(tg, mk(true), n);
    const ic = new Float32Array(n * 3), ii = new Float32Array(n), m = new T.Matrix4();
    TR.forEach((t, i) => { m.compose(new T.Vector3(t[0], 0, -t[1]), new T.Quaternion().setFromAxisAngle(new T.Vector3(0, 1, 0), i * 1.7), new T.Vector3(t[2], t[2], t[2])); im.setMatrixAt(i, m); const c = C(t[3]); ic.set([c.r, c.g, c.b], i * 3); ii[i] = nid++; });
    tg.setAttribute("iCol", new T.InstancedBufferAttribute(ic, 3)); tg.setAttribute("iId", new T.InstancedBufferAttribute(ii, 1)); scene.add(im);
  }
  // id별 강조 값(2: 고른 필지, 3: 대상 · 고른 단지 건물, 4: 누른 필지, 5: 누른 건물)
  const SH = Math.ceil(nid / 256), SA = new Uint8Array(256 * SH * 4), STX = new T.DataTexture(SA, 256, SH, T.RGBAFormat);
  STX.magFilter = STX.minFilter = T.NearestFilter; STX.needsUpdate = true; U.uSt.value = STX; U.uStSz.value.set(256, SH);
  const BP = {}; B.forEach((b, i) => { (BP[b.p] = BP[b.p] || []).push(i); });
  let pick = null, focus = null;   // focus: 목록에서 고른 주변 단지
  function paintState() {
    SA.fill(0);
    const mark = (pnu, pv, bv) => { if (PID[pnu] && pv) SA[PID[pnu] * 4] = pv; for (const i of BP[pnu] || []) for (const id of BIDS[i]) SA[id * 4] = bv; };
    for (const p of target) mark(p, 2, 3);
    if (focus) mark(focus.pnu, 2, 3);
    if (pick) { if (pick.b) for (const id of BIDS[B.indexOf(pick.b)]) SA[id * 4] = 5; else if (pick.p) SA[PID[pick.p.p] * 4] = 4; }
    STX.needsUpdate = true;
  }
  // 해(오늘 날짜 13시, 서울)
  const sunCam = new T.OrthographicCamera(-half * 1.5, half * 1.5, half * 1.5, -half * 1.5, 1, half * 8), SMS = Math.min(4096, RD.capabilities.maxTextureSize);
  const sRT = new T.WebGLRenderTarget(SMS, SMS); sRT.depthTexture = new T.DepthTexture(SMS, SMS); sRT.depthTexture.type = T.UnsignedIntType; U.uShadow.value = sRT.depthTexture; U.uTexel.value = 1 / SMS;
  {
    const now = new Date(), doy = Math.floor((now - new Date(now.getFullYear(), 0, 0)) / 864e5), r = Math.PI / 180, lat = 37.5 * r;
    const dec = -23.44 * r * Math.cos(2 * Math.PI / 365 * (doy + 10)), ha = (13 - 0.32 - 12) * 15 * r;
    const alt = Math.asin(Math.sin(lat) * Math.sin(dec) + Math.cos(lat) * Math.cos(dec) * Math.cos(ha));
    const az = Math.atan2(-Math.sin(ha), Math.tan(dec) * Math.cos(lat) - Math.sin(lat) * Math.cos(ha));
    const d = new T.Vector3(Math.sin(az) * Math.cos(alt), Math.sin(alt), -Math.cos(az) * Math.cos(alt));
    U.uSun.value.copy(d); sunCam.position.copy(d).multiplyScalar(half * 4); sunCam.lookAt(0, 0, 0); sunCam.updateMatrixWorld();
    U.uSunVP.value.multiplyMatrices(sunCam.projectionMatrix, sunCam.matrixWorldInverse);
    scene.overrideMaterial = new T.MeshBasicMaterial({ colorWrite: false }); RD.setRenderTarget(sRT); RD.clear(); RD.render(scene, sunCam); scene.overrideMaterial = null; RD.setRenderTarget(null);
  }
  const cRT = new T.WebGLRenderTarget(W * PR, H * PR);
  const gRT = new T.WebGLRenderTarget(W * PR, H * PR, { type: floatOK ? T.FloatType : T.HalfFloatType, minFilter: T.NearestFilter, magFilter: T.NearestFilter });
  const quad = new T.Mesh(new T.PlaneGeometry(2, 2), new T.ShaderMaterial({ uniforms: { tC: { value: cRT.texture }, tG: { value: gRT.texture }, px: { value: new T.Vector2(1 / (W * PR), 1 / (H * PR)) } },
    vertexShader: "varying vec2 vU;void main(){vU=uv;gl_Position=vec4(position.xy,0.,1.);}", fragmentShader: INK, depthTest: false, depthWrite: false }));
  const qs = new T.Scene(); qs.add(quad); const qc = new T.OrthographicCamera(-1, 1, 1, -1, 0, 1), bg = C("#efece6");
  // 카메라: 비스듬히 내려다보는 직교 카메라. 처음엔 대상 단지가 가운데 오고 둘레 250m 남짓이 보이게
  const VIEW0 = half * 2.4; let asp = W / H;
  const cam = new T.OrthographicCamera(-VIEW0 / 2, VIEW0 / 2, VIEW0 / 2 / asp, -VIEW0 / 2 / asp, -half * 10, half * 16);
  const ctl = new T.OrbitControls(cam, RD.domElement); ctl.enableDamping = false; ctl.screenSpacePanning = false; ctl.maxPolarAngle = 1.2; ctl.minZoom = 0.9; ctl.maxZoom = 14;
  ctl.mouseButtons = { LEFT: T.MOUSE.PAN, MIDDLE: T.MOUSE.DOLLY, RIGHT: T.MOUSE.ROTATE }; ctl.touches = { ONE: T.TOUCH.PAN, TWO: T.TOUCH.DOLLY_ROTATE };
  let mode = "axo";
  function orient() { const pol = mode === "top" ? 0.0001 : 0.9, az = mode === "top" ? 0 : -0.62, t = ctl.target, R = half * 5; cam.position.set(t.x + R * Math.sin(pol) * Math.sin(az), R * Math.cos(pol), t.z + R * Math.sin(pol) * Math.cos(az)); cam.updateProjectionMatrix(); ctl.update(); }
  // 처음 확대: 대상 필지가 화면 너비의 절반 안팎이 되게(큰 단지는 멀리, 작은 필지는 가까이)
  let ext = 60; for (const p of D.parcels || []) if (target.has(p.p)) for (const poly of polysOf(p.g)) for (const q of poly[0]) ext = Math.max(ext, Math.abs(q[0]) * 2, Math.abs(q[1]) * 2);
  const zoom0 = Math.max(1, Math.min(3.2, (W < 500 ? 0.55 : 0.5) * VIEW0 / ext));
  cam.zoom = zoom0; orient();
  const ppm = () => W * cam.zoom / VIEW0, prj = v => { const p = v.clone().project(cam); return [(p.x + 1) / 2 * W, (1 - p.y) / 2 * H]; };
  const tH = Math.max(0, ...B.filter(b => target.has(b.p)).map(b => b.H || 0));
  const pins = [];
  pins.push({ x: 0, y: 0, h: tH + 3, t: D.name || D.query, col: "#1f2328", main: true });
  for (const p of D.pois || []) pins.push({ x: p.x, y: p.y, h: 2, t: p.n, col: POI_COL[p.k] || "#5b6470", sub: p.k === "지하철역" ? "역" : "학교", poi: p });
  for (const c of D.complexes || []) if (!c.t && c.nd) pins.push({ x: c.x, y: c.yy, h: 2, t: ((n => n.length > 7 ? n.slice(0, 6) + "…" : n)(c.n.replace(/[()]/g, ""))) + (c.py ? " " + won(c.py) : ""), col: "#3f6fd1", cx: c, small: true });
  pins.forEach(p => { p.v = new T.Vector3(p.x, p.h, -p.y); });
  let drawn = [];
  function overlay() {
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0); ctx.clearRect(0, 0, W, H);
    const placed = []; drawn = [];
    const order = pins.slice().sort((a, b) => (b.main ? 3 : b.poi ? 2 : (focus && b.cx && b.cx.id === focus.id) ? 2.5 : 1) - (a.main ? 3 : a.poi ? 2 : (focus && a.cx && a.cx.id === focus.id) ? 2.5 : 1));
    const labels = [], dots = [];
    for (const p of order) {   // 자리 먼저 정함: 겹치면 점으로 줄임(점은 아래, 글자 핀은 위에 그림)
      const [x, y] = prj(p.v); if (x < -40 || y < -40 || x > W + 40 || y > H + 40) continue;
      const on = focus && p.cx && p.cx.id === focus.id, font = (p.main || on ? "700 13px " : p.small ? "600 11px " : "600 12px ") + "Pretendard,sans-serif";
      ctx.font = font;
      const txt = p.sub ? p.t + " " : p.t, w = ctx.measureText(txt).width + (p.sub ? 30 : 14), h = p.main ? 26 : 22, bx = x - w / 2, by = y - h - 8;
      if (!p.main && !on && placed.some(q => bx < q[0] + q[2] && bx + w > q[0] && by < q[1] + q[3] && by + h > q[1])) { dots.push({ p, x, y }); continue; }
      placed.push([bx, by, w, h]); labels.push({ p, x, y, on, font, txt, w, h, bx, by });
    }
    for (const d of dots) { ctx.fillStyle = d.p.col; ctx.beginPath(); ctx.arc(d.x, d.y - 3, 4.5, 0, 7); ctx.fill(); ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5; ctx.stroke(); drawn.push({ p: d.p, box: [d.x - 8, d.y - 11, 16, 16] }); }
    for (const L of labels.reverse()) {
      const { p, x, on, txt, w, h, bx, by } = L; ctx.font = L.font;
      ctx.fillStyle = on ? "#1f2328" : p.col; ctx.shadowColor = "#0004"; ctx.shadowBlur = 4; ctx.beginPath(); ctx.roundRect(bx, by, w, h, 6); ctx.fill(); ctx.shadowBlur = 0;
      ctx.beginPath(); ctx.moveTo(x - 5, by + h); ctx.lineTo(x, by + h + 7); ctx.lineTo(x + 5, by + h); ctx.fill();
      ctx.fillStyle = "#fff"; ctx.textAlign = "left"; ctx.fillText(txt, bx + 7, by + h / 2 + 4.5);
      if (p.sub) { const sx = bx + w - 25; ctx.fillStyle = "#ffffff33"; ctx.beginPath(); ctx.roundRect(sx, by + 4, 20, h - 8, 4); ctx.fill(); ctx.fillStyle = "#fff"; ctx.font = "600 10px Pretendard,sans-serif"; ctx.textAlign = "center"; ctx.fillText(p.sub, sx + 10, by + h / 2 + 3.5); }
      drawn.push({ p, box: [bx, by, w, h + 7] });
    }
    const k = ppm();
    if (k > 1.6) {   // 큰 건물 이름(확대했을 때)
      ctx.font = "600 11px Pretendard,sans-serif"; ctx.lineWidth = 3; ctx.strokeStyle = "#fbfaf7"; ctx.fillStyle = "#3b352f"; ctx.lineJoin = "round"; ctx.textAlign = "center";
      for (const b of B) {
        if (!b.n || !b.H || b.area * k * k < 2400 || target.has(b.p)) continue;
        const [x, y] = prj(new T.Vector3(b.cx, b.H + 1, -b.cy)); if (x < 0 || y < 0 || x > W || y > H) continue;
        const t = b.n.length > 12 ? b.n.slice(0, 11) + "…" : b.n, w = ctx.measureText(t).width + 6;
        if (placed.some(q => x - w / 2 < q[0] + q[2] && x + w / 2 > q[0] && y - 12 < q[1] + q[3] && y + 4 > q[1])) continue;
        placed.push([x - w / 2, y - 12, w, 16]); ctx.strokeText(t, x, y); ctx.fillText(t, x, y);
      }
    }
    // 축척 · 북쪽
    const ms = [10, 20, 50, 100, 200, 500], m = ms[ms.findIndex(v => v * k > 60)] || 500;
    ctx.fillStyle = "#5f6670"; ctx.font = "11px Pretendard,sans-serif"; ctx.textAlign = "left"; ctx.fillRect(W - 16 - m * k, H - 14, m * k, 2); ctx.fillText(m + "m", W - 16 - m * k, H - 20);
    const o = prj(new T.Vector3(ctl.target.x, 0, ctl.target.z)), n = prj(new T.Vector3(ctl.target.x, 0, ctl.target.z - 40)), a = Math.atan2(n[0] - o[0], -(n[1] - o[1]));
    ctx.save(); ctx.translate(24, H - 26); ctx.rotate(a); ctx.fillStyle = "#1f2328"; ctx.beginPath(); ctx.moveTo(0, -11); ctx.lineTo(6, 7); ctx.lineTo(0, 3); ctx.lineTo(-6, 7); ctx.closePath(); ctx.fill(); ctx.restore();
    ctx.fillStyle = "#1f2328"; ctx.font = "700 10px Pretendard,sans-serif"; ctx.textAlign = "center"; ctx.fillText("북", 24 + Math.sin(a) * 19, H - 26 - Math.cos(a) * 19 + 4);
  }
  let raf = 0, dead = false;
  function draw() {
    if (dead || !W) return; paintState();
    U.uMode.value = 0; RD.setRenderTarget(cRT); RD.setClearColor(bg, 1); RD.clear(); RD.render(scene, cam);
    U.uMode.value = 1; RD.setRenderTarget(gRT); RD.setClearColor(new T.Color(-1, -1, -1), 1); RD.clear(); RD.render(scene, cam);
    RD.setRenderTarget(null); RD.setClearColor(bg, 1); RD.render(qs, qc); U.uMode.value = 0; overlay();
  }
  const req = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; draw(); }); };
  ctl.addEventListener("change", req);
  function size() {
    W = frame.clientWidth; H = frame.clientHeight; if (!W || !H) return; asp = W / H; RD.setSize(W, H); cRT.setSize(W * PR, H * PR); gRT.setSize(W * PR, H * PR); quad.material.uniforms.px.value.set(1 / (W * PR), 1 / (H * PR));
    cam.left = -VIEW0 / 2; cam.right = VIEW0 / 2; cam.top = VIEW0 / 2 / asp; cam.bottom = -VIEW0 / 2 / asp; cam.updateProjectionMatrix();
    ov.width = W * DPR; ov.height = H * DPR; ov.style.width = W + "px"; ov.style.height = H + "px"; draw();
  }
  const ro = new ResizeObserver(() => size()); ro.observe(frame);
  let anim = 0;
  function flyTo(x, y, z, ms) {
    const a = ctl.target.clone(), t = new T.Vector3(x, 0, -y), za = cam.zoom, off = cam.position.clone().sub(ctl.target), t0 = performance.now(); cancelAnimationFrame(anim);
    const st = n => { const k = Math.min(1, (n - t0) / (ms || 420)), e = 1 - Math.pow(1 - k, 3); ctl.target.lerpVectors(a, t, e); cam.position.copy(ctl.target).add(off); cam.zoom = za + (z - za) * e; cam.updateProjectionMatrix(); draw(); if (k < 1) anim = requestAnimationFrame(st); else ctl.update(); };
    anim = requestAnimationFrame(st);
  }
  // 누르기: 핀 먼저, 아니면 윤곽선용 버퍼에서 누른 자리의 건물 · 필지 번호를 읽음
  const gc = RD.domElement; let down = null;
  gc.addEventListener("pointerdown", e => { down = [e.offsetX, e.offsetY]; });
  gc.addEventListener("pointerup", e => { if (!down || Math.hypot(e.offsetX - down[0], e.offsetY - down[1]) > 6) { down = null; return; } down = null; tap(e.offsetX, e.offsetY); });
  gc.addEventListener("pointermove", e => { if (e.buttons || e.pointerType !== "mouse") return; gc.style.cursor = pinAt(e.offsetX, e.offsetY) ? "pointer" : "grab"; });
  function pinAt(x, y) { for (let i = drawn.length - 1; i >= 0; i--) { const b = drawn[i].box; if (x >= b[0] && x <= b[0] + b[2] && y >= b[1] && y <= b[1] + b[3]) return drawn[i].p; } return null; }
  function tap(x, y) {
    const p = pinAt(x, y);
    if (p) { pick = null; if (p.cx) { focus = { id: p.cx.id, pnu: p.cx.p }; ui.complex(p.cx); } else if (p.poi) ui.poi(p.poi); else ui.target(); draw(); return; }
    const buf = new Float32Array(4); RD.readRenderTargetPixels(gRT, Math.floor(x * PR), Math.floor((H - y) * PR), 1, 1, buf);
    const inf = INFO[Math.round(buf[0])];
    if (!inf) { pick = null; ui.clear(); draw(); return; }
    if (inf.k === "b") { pick = { b: inf.b }; ui.building(inf.b, target.has(inf.b.p)); }
    else { pick = { p: inf.p }; ui.parcel(inf.p, target.has(inf.p.p)); }
    draw();
  }
  size();
  return {
    draw, flyTo, size,
    zoom(f) { flyTo(ctl.target.x, -ctl.target.z, Math.max(ctl.minZoom, Math.min(ctl.maxZoom, cam.zoom * f)), 280); },
    home() { pick = null; focus = null; flyTo(0, 0, zoom0); },
    view() { mode = mode === "axo" ? "top" : "axo"; orient(); draw(); return mode; },
    focusComplex(c) { pick = null; focus = { id: c.id, pnu: c.p }; flyTo(c.x, c.yy, Math.max(cam.zoom, 2.6)); },
    snapshot() { draw(); const c = document.createElement("canvas"); c.width = W * PR; c.height = H * PR; const g = c.getContext("2d"); g.drawImage(RD.domElement, 0, 0); g.drawImage(ov, 0, 0, W * PR, H * PR); return c.toDataURL("image/jpeg", 0.85); },
    dispose() { dead = true; ro.disconnect(); ctl.dispose(); mesh.geometry.dispose(); [cRT, gRT, sRT, STX].forEach(x => x.dispose()); RD.dispose(); RD.forceContextLoss(); RD.domElement.remove(); ov.remove(); },
  };
}

// 답 카드 아래 붙는 칸. 화면에 보이면 자료를 받아 그림
function section(name, token) {
  const box = el("section", "m3"), head = el("div", "m3-h");
  head.append(el("b", null, "3D 지도 · 주변"), el("span", "m3-s", name + " 둘레 사방 300m"));
  const frame = el("div", "m3-f"), msg = el("div", "m3-msg", "3D 지도를 그리는 중이에요…"); frame.append(msg);
  const info = el("div", "m3-info"); info.hidden = true;
  const near = el("div", "m3-near"), note = el("p", "m3-note", "건물 높이는 건축물대장 층수 기준이고, 건물 색 · 창 · 옥상 설비 · 가로수는 용도와 위치에 따라 자동으로 그린 것이라 실제와 다를 수 있어요. 연속지적도는 참고용 도면이에요.");
  box.append(head, frame, info, near, note);
  let S = null, D = null, img = null;
  const ui = {
    clear() { info.hidden = true; },
    show(title, rows, extra) {
      info.textContent = ""; info.hidden = false; const x = el("button", "m3-x", "×"); x.type = "button"; x.setAttribute("aria-label", "닫기"); x.onclick = () => { info.hidden = true; };
      info.append(x, el("b", null, title)); const t = el("table"); for (const [k, v] of rows) if (v != null && v !== "") { const tr = el("tr"); tr.append(el("td", null, k), el("td", null, String(v))); t.append(tr); } info.append(t); if (extra) info.append(el("p", "m3-note", extra));
    },
    target() { const c = (D.complexes || []).find(c => c.t); this.show(D.name || D.query, c ? [["준공", c.y && c.y + "년"], ["세대", c.hh && nf.format(c.hh) + "세대"], ["최근 12개월 매매", c.nd + "건"], ["전용 평당 가운데값", c.py && won(c.py)]] : [], "이 답이 다룬 곳이에요. 파랗게 칠한 필지와 건물입니다."); },
    complex(c) { this.show(cname(c), [["종류", c.k], ["준공", c.y && c.y + "년"], ["세대", c.hh && nf.format(c.hh) + "세대"], ["최근 12개월 매매", c.nd + "건"], ["전용 평당 가운데값", c.py && won(c.py)], ["거리", "직선 " + nf.format(c.m) + "m"]], "국토교통부 실거래가 기준, 취소된 거래는 뺐어요."); },
    poi(p) { this.show(p.n, [["종류", p.k], [p.k === "지하철역" ? "노선" : "구분", p.d], ["거리", "직선 " + nf.format(p.m) + "m · 걸어서 약 " + Math.ceil(p.m / 67) + "분"]]); },
    building(b, isT) { this.show(b.n || "이름 없는 건물", [["층수", "지상 " + (b.f || "-") + "층"], ["용도", b.u || BLD_KIND[b.st && b.st.c]], ["그린 높이", b.H && b.H.toFixed(1) + "m (층수 × 층고)"]], isT ? "이 답이 다룬 단지의 건물이에요." : null); },
    parcel(p, isT) { this.show((p.j || "") + " 필지", [["지목", p.k], ["필지 고유번호", p.p]], isT ? "이 답이 다룬 단지의 필지예요." : "빈 땅이나 도로를 누르면 필지 정보가 나와요."); },
  };
  
  function nearList() {
    near.textContent = "";
    const pois = (D.pois || []).filter(p => p.m <= D.half * 1.6), cx = (D.complexes || []).filter(c => !c.t && c.nd).slice(0, 8);
    if (pois.length) {
      const g = el("div", "m3-col"); g.append(el("h4", null, "역 · 학교"));
      for (const p of pois.slice(0, 8)) { const r = el("button", "m3-row"); r.type = "button"; r.append(el("span", null, p.n), el("span", "m3-d", (p.k === "지하철역" ? p.d || "역" : p.k) + " · 걸어서 약 " + Math.ceil(p.m / 67) + "분")); r.onclick = () => { live === api && S && (S.flyTo(p.x, p.y, 2.4), ui.poi(p)); }; g.append(r); }
      near.append(g);
    }
    if (cx.length) {
      const g = el("div", "m3-col"); g.append(el("h4", null, "주변 단지 · 최근 12개월 매매(전용 평당 가운데값)"));
      for (const c of cx) { const r = el("button", "m3-row"); r.type = "button"; r.append(el("span", null, cname(c)), el("span", "m3-d", [c.y && c.y + "년", "매매 " + c.nd + "건", c.py && "평당 " + won(c.py)].filter(Boolean).join(" · "))); r.onclick = () => { if (live === api && S) { S.focusComplex(c); ui.complex(c); } }; g.append(r); }
      near.append(g);
    }
    if (!pois.length && !cx.length) near.append(el("p", "m3-note", "둘레에서 찾은 역 · 학교 · 단지 거래가 없어요."));
  }
  function controls() {
    const bar = el("div", "m3-ctl");
    const b = (t, f, l) => { const x = el("button", null, t); x.type = "button"; x.onclick = f; x.setAttribute("aria-label", l); bar.append(x); return x; };
    b("+", () => S && S.zoom(1.5), "확대"); b("−", () => S && S.zoom(1 / 1.5), "축소"); b("⌂", () => S && S.home(), "처음 위치로");
    const v = b("위", () => { if (S) { const m = S.view(); v.textContent = m === "axo" ? "위" : "3D"; } }, "위에서 보기 · 비스듬히 보기");
    frame.append(bar, el("div", "m3-tip", "끌어서 이동 · 두 손가락이나 휠로 확대 · 오른쪽 버튼이나 두 손가락으로 회전"));
  }
  const api = {
    freeze() { if (!S) return; img = el("img", "m3-img"); img.src = S.snapshot(); img.alt = name + " 둘레 3D 지도"; S.dispose(); S = null; frame.querySelectorAll(".m3-ctl,.m3-tip").forEach(x => x.remove()); const w = el("button", "m3-wake", "눌러서 다시 움직이기"); w.type = "button"; frame.append(img, w); w.onclick = img.onclick = () => wake(); },
  };
  async function wake() {
    if (live && live !== api) live.freeze();
    live = api; frame.querySelectorAll(".m3-img,.m3-wake").forEach(x => x.remove());
    try { await loadThree(); } catch (e) { msg.textContent = e.message; return; }
    if (!window.WebGLRenderingContext) { msg.textContent = "이 브라우저는 3D 그리기(WebGL)를 지원하지 않아요."; return; }
    msg.remove(); S = Scene(frame, D, ui); controls();
  }
  async function start() {
    try {
      D = cache.get(name);
      if (!D) { const r = await fetch("/api/map3d?q=" + encodeURIComponent(name), { headers: { "X-Token": token || "" } }); D = await r.json(); if (!r.ok) throw new Error(D.error || "연결 오류 " + r.status); cache.set(name, D); }
    } catch (e) { msg.textContent = "3D 지도를 받지 못했어요. " + e.message; return; }
    if (D.empty || !(D.buildings || []).length) { msg.textContent = "이 곳은 지적도 · 건물 윤곽이 아직 없어 3D 지도를 그리지 못했어요."; return; }
    if (D.name && D.name !== name) head.querySelector(".m3-s").textContent = D.name + " 둘레 사방 " + D.half + "m";
    nearList(); wake();
  }
  const io = new IntersectionObserver(es => { if (es.some(e => e.isIntersecting)) { io.disconnect(); start(); } }, { rootMargin: "200px" });
  io.observe(box);
  return box;
}

window.Map3D = { section };
})();
