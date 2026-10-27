"""시연용 웹 채팅(파이썬 표준 라이브러리만 씀).

    HANPILJI_LLM_API_KEY=... python -m hanpilji.gpt.web [--port 8000] [--host 127.0.0.1]

브라우저에서 http://127.0.0.1:8000 을 열면 됨. 대화는 서버 메모리에만 두고(창마다 따로), 껐다 켜면 사라짐.
"""
import argparse
import json
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hanpilji import config as config_mod
from hanpilji.gpt import engine, llm

MAX_SESSIONS = 200
MAX_QUESTION = 1000

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
let sid=null;
for(const t of EX){const c=document.createElement("button");c.type="button";c.textContent=t;c.onclick=()=>{q.value=t;send()};document.getElementById("chips").append(c)}
function add(text,cls,src){const d=document.createElement("div");d.className="msg "+(cls||"");d.textContent=text;
 if(src){const s=document.createElement("div");s.className="src";s.textContent=src;d.append(s)}log.append(d);d.scrollIntoView({behavior:"smooth"});return d}
async function send(){const t=q.value.trim();if(!t)return;q.value="";add(t,"me");b.disabled=true;const w=add("찾는 중…");
 try{const r=await fetch("/api/ask",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({q:t,sid})});
  const j=await r.json();w.remove();if(j.sid)sid=j.sid;add(j.answer||j.error,"",j.footer)}catch(e){w.textContent="연결 오류: "+e}
 b.disabled=false;q.focus()}
document.getElementById("f").onsubmit=e=>{e.preventDefault();send()};
</script></body></html>"""


def make_handler(cfg, model, rounds):
    sessions, lock = {}, threading.Lock()

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

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, PAGE, "text/html; charset=utf-8")
            else:
                self._json(404, {"error": "없는 주소"})

        def do_POST(self):
            if self.path != "/api/ask":
                return self._json(404, {"error": "없는 주소"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self._json(400, {"error": "JSON이 아님"})
            question = str(body.get("q", "")).strip()[:MAX_QUESTION]
            if not question:
                return self._json(400, {"error": "질문이 비어 있음"})
            sid = body.get("sid") if body.get("sid") in sessions else secrets.token_urlsafe(12)
            with lock:
                history = sessions.get(sid)
            try:
                with engine.connect(cfg) as conn:
                    ans = engine.ask(question, model, conn, history, max_rounds=rounds)
            except Exception as e:
                print(f"오류: {e!r}", file=sys.stderr)
                return self._json(500, {"error": "답을 만드는 중 오류가 났습니다. 잠시 뒤 다시 물어 주세요.", "sid": sid})
            with lock:
                if sid not in sessions and len(sessions) >= MAX_SESSIONS:
                    sessions.pop(next(iter(sessions)))
                sessions[sid] = ans.messages
            self._json(200, {"answer": ans.text, "footer": ans.footer, "sid": sid})

    return Handler


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m hanpilji.gpt.web", description="한필지 부동산 GPT 웹 채팅")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    a = p.parse_args(argv)
    cfg = config_mod.load(a.config)
    try:
        model = llm.make(cfg)
    except llm.MissingKey as e:
        print(f"{e}. README의 「부동산 GPT」 절에서 키 넣는 법을 보세요.", file=sys.stderr)
        return 2
    server = ThreadingHTTPServer((a.host, a.port), make_handler(cfg, model, cfg.get("gpt", {}).get("max_rounds", 6)))
    print(f"http://{a.host}:{a.port} 에서 열었습니다 (끄려면 Ctrl+C)", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
