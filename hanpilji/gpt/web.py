"""시연용 웹 채팅(파이썬 표준 라이브러리만 씀).

    HANPILJI_LLM_API_KEY=... python -m hanpilji.gpt.web [--port 8000] [--host 127.0.0.1]

브라우저에서 http://127.0.0.1:8000 을 열면 됨. 대화는 서버 메모리에만 두고(창마다 따로), 껐다 켜면 사라짐.

남에게 열 때(--host 0.0.0.0 등): 질문마다 API 요금이 나가므로 HANPILJI_WEB_TOKEN을 정해야만 켜짐.
그때 주소는 http://서버:8000/?token=정한값 . 토큰이 있어도 접속 주소(IP)마다 시간당 질문 수를 제한함.
"""
import argparse
import hmac
import ipaddress
import json
import os
import secrets
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hanpilji import config as config_mod
from hanpilji.gpt import engine, llm, tools

MAX_SESSIONS = 200
MAX_QUESTION = 1000
MAX_BODY = 16 * 1024          # 질문 1,000자면 넉넉함. 넘으면 읽지 않고 거절
PER_HOUR = 30                 # 접속 주소마다 시간당 질문 수
TOKEN_ENV = "HANPILJI_WEB_TOKEN"

PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>한필지 부동산 GPT</title>
<style>
:root{--bg:#f7f6f2;--card:#fff;--ink:#1d2433;--muted:#6b7385;--line:#e4e2da;--accent:#2f6f4f;--on-accent:#fff;--me:#e9f2ec;--soft:#f1efe8;--warn:#9a6a12}
@media (prefers-color-scheme:dark){:root{--bg:#14171d;--card:#1d222b;--ink:#e8eaef;--muted:#9aa3b5;--line:#2c333f;--accent:#7cc79f;--on-accent:#10221a;--me:#1f3328;--soft:#232a35;--warn:#e0b25c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 -apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif}
main{max-width:760px;margin:0 auto;padding:24px 16px 120px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 10px}.sub{color:var(--muted);font-size:14px;margin:0 0 16px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px}
.chips button{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:6px 12px;font-size:14px;cursor:pointer}
.msg{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 16px;margin:12px 0;white-space:pre-wrap;word-break:keep-all;overflow-wrap:anywhere}
.me{background:var(--me);margin-left:15%}
.md{white-space:normal}.md>*{margin:0 0 8px}.md>*:last-child{margin-bottom:0}
.md h3,.md h4,.md h5,.md h6{font-size:16px;margin-top:12px}.md h5,.md h6{font-size:15px}.md ul,.md ol{padding-left:20px}
.md .tw{overflow-x:auto}.md table{width:auto;min-width:60%}.md td,.md th{white-space:nowrap}.md code{font-size:.92em;background:var(--soft);border-radius:4px;padding:0 4px}
.src{margin-top:10px;padding-top:8px;border-top:1px dashed var(--line);color:var(--muted);font-size:13px}
.look{display:flex;gap:8px}.look input{flex:1}
.pc{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin:12px 0}
.pc h3{margin:0;font-size:19px}.pc .addr{color:var(--muted);font-size:14px;margin:2px 0 12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}
.sec{background:var(--soft);border-radius:10px;padding:10px 12px;font-size:14px;word-break:keep-all;overflow-wrap:anywhere}
.sec b{display:block;font-size:13px;color:var(--muted);font-weight:600;margin-bottom:4px}
.sec .big{font-size:18px;font-weight:700}.todo{color:var(--warn)}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:10px}td,th{padding:4px 6px;border-bottom:1px solid var(--line);text-align:left}
th{color:var(--muted);font-weight:600}td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.pc .ask{margin-top:12px;border:1px solid var(--accent);background:none;color:var(--accent);border-radius:8px;padding:6px 12px;font:inherit;font-size:14px;cursor:pointer}
form{position:fixed;left:0;right:0;bottom:0;background:var(--bg);border-top:1px solid var(--line);padding:12px 16px}
.row{max-width:760px;margin:0 auto;display:flex;gap:8px}
input{min-width:0;font:inherit;padding:10px 12px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--ink)}
.row input{flex:1}
button.go{font:inherit;padding:10px 16px;border:0;border-radius:10px;background:var(--accent);color:var(--on-accent);cursor:pointer}
button:disabled{opacity:.5}
</style></head><body><main>
<h1>한필지 부동산 GPT</h1>
<p class="sub">공공데이터(실거래가 · 행위제한 · 법령)로만 답합니다. 데이터에 없으면 모른다고 합니다. 투자 조언은 하지 않습니다.</p>
<h2>단지 · 주소 한눈에</h2>
<div class="look"><input id="cq" autocomplete="off" placeholder="단지명 또는 동 지번. 예: 도곡렉슬, 대치동 316" maxlength="100"><button class="go" id="cb" type="button">보기</button></div>
<div id="cards"></div>
<h2>물어보기</h2>
<div class="chips" id="chips"></div>
<div id="log"></div>
</main>
<form id="f"><div class="row"><input id="q" autocomplete="off" placeholder="예: 도곡렉슬 84㎡ 최근 1년 매매가랑 전세가" maxlength="1000"><button class="go" id="b">묻기</button></div></form>
<script>
const EX=["도곡렉슬 종합해서 알려줘","은마 76㎡ 최근 6개월 최고가","강남구 최근 1년 전세 거래 많은 단지 3곳","제2종일반주거지역에 휴게음식점 지을 수 있어?","전월세 계약 갱신할 때 임대료 5% 상한 조문 찾아줘","래미안대치팰리스 지금 사도 될까?"];
const $=id=>document.getElementById(id),log=$("log"),q=$("q"),b=$("b");
let sid=null;const token=new URLSearchParams(location.search).get("token")||"";
for(const t of EX){const c=document.createElement("button");c.type="button";c.textContent=t;c.onclick=()=>{q.value=t;send()};$("chips").append(c)}
function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e}
function won(v){if(v==null)return"-";const eok=Math.floor(v/1e8),man=Math.round(v%1e8/1e4);
 return (eok?eok.toLocaleString()+"억 ":"")+(man?man.toLocaleString()+"만 ":"")+"원"}
// 답변의 마크다운(제목 · 목록 · 표 · 굵게 · 코드)을 요소를 하나씩 만들어 그림. 글자는 textContent로만 넣어 답 속 HTML은 글자로만 보임
function inline(parent,text){const re=/\*\*(.+?)\*\*|`([^`]+)`/g;let i=0,m;
 while((m=re.exec(text))){if(m.index>i)parent.append(text.slice(i,m.index));parent.append(m[1]!=null?el("strong",null,m[1]):el("code",null,m[2]));i=re.lastIndex}
 if(i<text.length)parent.append(text.slice(i));return parent}
function cells(line){return line.trim().replace(/^\|/,"").replace(/(^|[^\\])\|$/,"$1").split(/(?<!\\)\|/).map(c=>c.trim().replace(/\\\|/g,"|"))}
const HD=/^(#{1,6})\s+(.*)$/,LI=/^(\s*)(?:[-*+•]|(\d+)[.)])\s+(.*)$/,SEP=/^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/,ROW=/(^|[^\\])\|/;
const isTable=(lines,i)=>ROW.test(lines[i])&&i+1<lines.length&&ROW.test(lines[i+1])&&SEP.test(lines[i+1]);
const depth=s=>s.replace(/\t/g,"    ").length;
// 들여쓴 항목은 안쪽 목록으로, 번호 · 글머리 표시가 바뀌면 새 목록으로
function list(lines,i,ind){let m=lines[i].match(LI);const ord=!!m[2],L=el(ord?"ol":"ul");if(ord)L.start=+m[2];let last=null;
 while(i<lines.length&&(m=lines[i].match(LI))){const d=depth(m[1]);
  if(d<ind)break;
  if(d>ind&&last){const r=list(lines,i,d);last.append(r[0]);i=r[1];continue}
  if(!!m[2]!==ord)break;
  last=inline(el("li"),m[3]);L.append(last);i++}
 return [L,i]}
function md(text){const box=el("div","md"),lines=text.replace(/\r/g,"").split("\n");let i=0;
 while(i<lines.length){const l=lines[i];
  if(!l.trim()){i++;continue}
  // 페이지 제목이 h1 · h2라서 답의 #~###### 는 h3~h6 으로 한 단계씩 내려 붙임
  let h=l.match(HD);if(h){box.append(inline(el("h"+Math.min(h[1].length+2,6)),h[2]));i++;continue}
  if(isTable(lines,i)){
   const t=el("table"),hr=el("tr"),head=cells(l),num=[];cells(lines[i+1]).forEach((c,k)=>num[k]=/-:$/.test(c));
   head.forEach((c,k)=>hr.append(inline(el("th",num[k]?"n":null),c)));t.append(hr);i+=2;
   while(i<lines.length&&lines[i].trim()&&ROW.test(lines[i])){const tr=el("tr");cells(lines[i]).forEach((c,k)=>tr.append(inline(el("td",num[k]?"n":null),c)));t.append(tr);i++}
   const w=el("div","tw");w.append(t);box.append(w);continue}
  let m=l.match(LI);
  if(m){const r=list(lines,i,depth(m[1]));box.append(r[0]);i=r[1];continue}
  const para=[];while(i<lines.length&&lines[i].trim()&&!LI.test(lines[i])&&!HD.test(lines[i])&&!isTable(lines,i))para.push(lines[i++]);
  if(!para.length)para.push(lines[i++]);
  const p=el("p");para.forEach((x,k)=>{if(k)p.append(el("br"));inline(p,x)});box.append(p)}
 return box}
function add(text,cls,src,rich){const d=el("div","msg "+(cls||""),rich?null:text);if(rich)d.append(md(text));if(src)d.append(el("div","src",src));log.append(d);d.scrollIntoView({behavior:"smooth"});return d}
function sec(title,lines,todo){const s=el("div","sec");s.append(el("b",null,title));
 if(todo){s.append(el("div","todo","데이터 수집 전"))}else for(const[l,c]of lines)s.append(el("div",c||null,l));return s}
function card(r,deals){const c=el("div","pc");c.append(el("h3",null,r.complex_name));
 c.append(el("div","addr",[r.address,r.road_addr&&("도로명 "+r.road_addr)].filter(Boolean).join(" · ")));
 const g=el("div","grid"),miss=r.missing||[],has=k=>!miss.some(m=>m.startsWith(k));
 g.append(sec("위치",[["PNU "+(r.pnu||"없음")],["준공 "+(r.build_year||"-")+"년"],["전용 "+(r.area_types||[]).join(" · ")+"㎡"]]));
 g.append(sec("거래 (최근 12개월)",[[r.trade_count?"평당 "+won(r.trade_per_pyeong):"매매 없음","big"],
  ["매매 "+r.trade_count+"건 · 마지막 "+(r.trade_last_date?r.trade_last_date+" "+won(r.trade_last_amount)+" ("+r.trade_last_area+"㎡)":"-")],
  ["전세 "+r.jeonse_count+"건 평균 "+won(r.jeonse_avg)+(r.jeonse_ratio!=null?" · 전세가율 "+r.jeonse_ratio+"% ("+r.jeonse_ratio_area+"㎡ 기준)":"")],["월세 "+r.wolse_count+"건"]]));
 g.append(sec("토지",[["필지 "+(r.land_parcel_count||0)+"개 · 지목 "+(r.land_category||"수집 전")],["면적 "+(r.land_area_m2!=null?Number(r.land_area_m2).toLocaleString()+"㎡"+(r.land_area_kind?" ("+r.land_area_kind+")":""):"-")],
  ["공시지가 "+(r.land_price_per_m2?won(r.land_price_per_m2)+"/㎡ ("+r.land_price_year+")":"수집 전")]],!has("토지 지목")&&!has("토지 면적")&&!has("공시지가")));
 g.append(sec("용도지역 · 규제",[[r.zones||"-"],["행위제한 규칙 "+(r.zone_rule_count||0)+"개"]],!has("용도지역")));
 g.append(sec("건물",[["건물 "+(r.bld_count||0)+"동 · "+(r.bld_main_use||"-")+" · 최고 "+(r.bld_floors_above||"-")+"층"],["사용승인 "+(r.bld_approval_date||"-")+" · 세대 "+(r.bld_households??"-")+" · 주차 "+(r.bld_parking??"-")+"대"],
  ["건폐율 "+(r.bld_bc_rat!=null?r.bld_bc_rat+"%":"-")+" · 용적률 "+(r.bld_vl_rat!=null?r.bld_vl_rat+"%":"-")],
  ["승강기 "+(r.bld_elevator==null?"-":r.bld_elevator?"있음":"없음")+" · 위반 "+(r.bld_violation==null?"-":r.bld_violation?"있음":"없음")]],!has("건축물대장")));
 c.append(g);
 if(deals&&deals.length){const t=el("table");const h=el("tr");["계약일","전용","층","매매가"].forEach((x,i)=>h.append(el("th",i?"n":null,x)));t.append(h);
  for(const d of deals.slice(0,5)){const tr=el("tr");tr.append(el("td",null,d.contract_date),el("td","n",d.area_m2+"㎡"),el("td","n",(d.floor??"-")+"층"),el("td","n",won(d.amount)));t.append(tr)}
  c.append(el("div","src","최근 매매 5건"),t)}
 c.append(el("div","src",(r.sources||"")+" · 기준 "+(r.asof||"-")+(miss.length?" · 수집 전: "+miss.join(", "):"")));
 const a=el("button","ask","이 단지 GPT에게 자세히 묻기");a.type="button";a.onclick=()=>{q.value=r.complex_name+" 종합해서 알려줘";send()};c.append(a);return c}
async function look(){const t=$("cq").value.trim();if(!t)return;const box=$("cards");box.textContent="찾는 중…";$("cb").disabled=true;
 try{const r=await fetch("/api/card?q="+encodeURIComponent(t),{headers:{"X-Token":token}});const j=await r.json();box.textContent="";
  if(!r.ok){box.append(el("div","msg",j.error))}else{
  if(!j.rows.length){box.append(el("div","msg","「"+t+"」에 맞는 단지가 없습니다. 지금 데이터는 서울 강남구 아파트만 있습니다."))}
  for(const row of j.rows){row.sources=j.sources.join(" · ");row.asof=j.asof;box.append(card(row,row.recent_deals))}
  if(j.row_total>j.rows.length)box.append(el("div","src","전체 "+j.row_total+"곳 중 "+j.rows.length+"곳. 이름을 더 자세히 써 주세요."))}}
 catch(e){box.textContent="연결 오류: "+e}$("cb").disabled=false}
$("cb").onclick=look;$("cq").onkeydown=e=>{if(e.key==="Enter")look()};
async function send(){const t=q.value.trim();if(!t)return;q.value="";add(t,"me");b.disabled=true;const w=add("찾는 중…");
 try{const r=await fetch("/api/ask",{method:"POST",headers:{"Content-Type":"application/json","X-Token":token},body:JSON.stringify({q:t,sid})});
  const j=await r.json();w.remove();if(j.sid)sid=j.sid;add(j.answer||j.error,"",j.footer,!!j.answer)}catch(e){w.textContent="연결 오류: "+e}
 b.disabled=false;q.focus()}
$("f").onsubmit=e=>{e.preventDefault();send()};
</script></body></html>"""


def make_handler(cfg, model, rounds, token=None, per_hour=PER_HOUR):
    sessions, lock = {}, threading.Lock()   # sid → {"messages", "lock"}
    hits = {}                               # 접속 주소 → 최근 1시간 질문 시각들

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

        def _allowed(self):
            if token is None:
                return True
            given = self.headers.get("X-Token") or parse_qs(urlsplit(self.path).query).get("token", [""])[0]
            return hmac.compare_digest(given.encode(), token.encode())

        def _rate_ok(self):
            now, ip = time.monotonic(), self.client_address[0]
            with lock:
                recent = [t for t in hits.get(ip, []) if now - t < 3600]
                if len(recent) >= per_hour:
                    hits[ip] = recent
                    return False
                hits[ip] = recent + [now]
                return True

        def do_GET(self):
            if not self._allowed():
                return self._json(403, {"error": "토큰이 필요합니다"})
            url = urlsplit(self.path)
            if url.path in ("/", "/index.html"):
                self._send(200, PAGE, "text/html; charset=utf-8")
            elif url.path == "/api/card":
                self._card(parse_qs(url.query).get("q", [""])[0].strip()[:100])
            else:
                self._json(404, {"error": "없는 주소"})

        def _card(self, query):
            """단지 카드(LLM 없이 마트 함수 하나만 부름, 요금 없음). 최근 매매 5건도 같은 결과에 들어 있음."""
            if not query:
                return self._json(400, {"error": "단지명이나 주소를 넣어 주세요"})
            try:
                with engine.connect(cfg) as conn:
                    out = tools.run(conn, "q_property_card", {"query": query})
            except Exception as e:
                print(f"오류: {e!r}", file=sys.stderr)
                return self._json(500, {"error": "카드를 만드는 중 오류가 났습니다."})
            self._json(200, out)

        def do_POST(self):
            if self.path != "/api/ask":
                return self._json(404, {"error": "없는 주소"})
            if not self._allowed():
                return self._json(403, {"error": "토큰이 필요합니다"})
            try:
                size = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._json(400, {"error": "Content-Length가 숫자가 아님"})
            if size > MAX_BODY:
                return self._json(413, {"error": "요청이 너무 큽니다"})
            try:
                body = json.loads(self.rfile.read(size) or b"{}")
            except ValueError:
                return self._json(400, {"error": "JSON이 아님"})
            if not isinstance(body, dict):
                return self._json(400, {"error": "JSON 객체여야 함"})
            question = str(body.get("q", "")).strip()[:MAX_QUESTION]
            if not question:
                return self._json(400, {"error": "질문이 비어 있음"})
            if not self._rate_ok():
                return self._json(429, {"error": f"질문이 너무 많습니다. 한 시간에 {per_hour}개까지 받습니다."})
            sid = body.get("sid")
            with lock:
                if not isinstance(sid, str) or sid not in sessions:
                    sid = secrets.token_urlsafe(12)
                    if len(sessions) >= MAX_SESSIONS:
                        sessions.pop(next(iter(sessions)))
                    sessions[sid] = {"messages": None, "lock": threading.Lock()}
                sess = sessions[sid]
            with sess["lock"]:   # 같은 창의 질문은 한 번에 하나씩(대화가 엇갈려 사라지지 않게)
                try:
                    with engine.connect(cfg) as conn:
                        ans = engine.ask(question, model, conn, sess["messages"], max_rounds=rounds)
                except Exception as e:
                    print(f"오류: {e!r}", file=sys.stderr)
                    return self._json(500, {"error": "답을 만드는 중 오류가 났습니다. 잠시 뒤 다시 물어 주세요.", "sid": sid})
                sess["messages"] = engine.trim(ans.messages)
            self._json(200, {"answer": ans.text, "footer": ans.footer, "sid": sid})

    return Handler


def _loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m hanpilji.gpt.web", description="한필지 부동산 GPT 웹 채팅")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    a = p.parse_args(argv)
    cfg = config_mod.load(a.config)
    token = os.environ.get(TOKEN_ENV) or None
    if token is None and not _loopback(a.host):
        print(f"{a.host}로 열려면 환경변수 {TOKEN_ENV}를 정하세요(질문마다 API 요금이 나감).", file=sys.stderr)
        return 2
    try:
        rounds = engine.rounds_from(cfg)
        model = llm.make(cfg)
    except llm.MissingKey as e:
        print(f"{e}. README의 「부동산 GPT」 절에서 키 넣는 법을 보세요.", file=sys.stderr)
        return 2
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2
    server = ThreadingHTTPServer((a.host, a.port), make_handler(cfg, model, rounds, token))
    print(f"http://{a.host}:{a.port} 에서 열었습니다 (끄려면 Ctrl+C)", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
