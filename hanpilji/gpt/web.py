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
from hanpilji.gpt import engine, llm

MAX_SESSIONS = 200
MAX_QUESTION = 1000
MAX_BODY = 16 * 1024          # 질문 1,000자면 넉넉함. 넘으면 읽지 않고 거절
PER_HOUR = 30                 # 접속 주소마다 시간당 질문 수
TOKEN_ENV = "HANPILJI_WEB_TOKEN"

PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>한필지 부동산 GPT</title>
<style>
:root{--bg:#f7f6f2;--card:#fff;--ink:#1d2433;--muted:#6b7385;--line:#e4e2da;--accent:#2f6f4f;--me:#e9f2ec}
@media (prefers-color-scheme:dark){:root{--bg:#14171d;--card:#1d222b;--ink:#e8eaef;--muted:#9aa3b5;--line:#2c333f;--accent:#7cc79f;--me:#1f3328}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 -apple-system,"Apple SD Gothic Neo","Noto Sans KR",sans-serif}
main{max-width:760px;margin:0 auto;padding:24px 16px 120px}
h1{font-size:22px;margin:0 0 4px}.sub{color:var(--muted);font-size:14px;margin:0 0 20px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:20px}
.chips button{border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:999px;padding:6px 12px;font-size:14px;cursor:pointer}
.msg{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 16px;margin:12px 0;white-space:pre-wrap;word-break:keep-all;overflow-wrap:anywhere}
.me{background:var(--me);margin-left:15%}
.src{margin-top:10px;padding-top:8px;border-top:1px dashed var(--line);color:var(--muted);font-size:13px}
form{position:fixed;left:0;right:0;bottom:0;background:var(--bg);border-top:1px solid var(--line);padding:12px 16px}
.row{max-width:760px;margin:0 auto;display:flex;gap:8px}
input{flex:1;min-width:0;font:inherit;padding:10px 12px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--ink)}
form button{font:inherit;padding:10px 16px;border:0;border-radius:10px;background:var(--accent);color:#fff;cursor:pointer}
form button:disabled{opacity:.5}
</style></head><body><main>
<h1>한필지 부동산 GPT</h1>
<p class="sub">공공데이터(실거래가 · 행위제한 · 법령)로만 답합니다. 데이터에 없으면 모른다고 합니다. 투자 조언은 하지 않습니다.</p>
<div class="chips" id="chips"></div>
<div id="log"></div>
</main>
<form id="f"><div class="row"><input id="q" autocomplete="off" placeholder="예: 도곡렉슬 84㎡ 최근 1년 매매가랑 전세가" maxlength="1000"><button id="b">묻기</button></div></form>
<script>
const EX=["도곡렉슬 84㎡ 최근 1년 매매가랑 전세가 알려줘","제2종일반주거지역에 휴게음식점 지을 수 있어?","전월세 계약 갱신할 때 임대료 5% 상한 조문 찾아줘","래미안대치팰리스 지금 사도 될까?"];
const log=document.getElementById("log"),q=document.getElementById("q"),b=document.getElementById("b");
let sid=null;const token=new URLSearchParams(location.search).get("token")||"";
for(const t of EX){const c=document.createElement("button");c.type="button";c.textContent=t;c.onclick=()=>{q.value=t;send()};document.getElementById("chips").append(c)}
function add(text,cls,src){const d=document.createElement("div");d.className="msg "+(cls||"");d.textContent=text;
 if(src){const s=document.createElement("div");s.className="src";s.textContent=src;d.append(s)}log.append(d);d.scrollIntoView({behavior:"smooth"});return d}
async function send(){const t=q.value.trim();if(!t)return;q.value="";add(t,"me");b.disabled=true;const w=add("찾는 중…");
 try{const r=await fetch("/api/ask",{method:"POST",headers:{"Content-Type":"application/json","X-Token":token},body:JSON.stringify({q:t,sid})});
  const j=await r.json();w.remove();if(j.sid)sid=j.sid;add(j.answer||j.error,"",j.footer)}catch(e){w.textContent="연결 오류: "+e}
 b.disabled=false;q.focus()}
document.getElementById("f").onsubmit=e=>{e.preventDefault();send()};
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
            if urlsplit(self.path).path in ("/", "/index.html"):
                self._send(200, PAGE, "text/html; charset=utf-8")
            else:
                self._json(404, {"error": "없는 주소"})

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
