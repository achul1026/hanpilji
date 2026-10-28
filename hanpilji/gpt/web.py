"""한필지 웹(대화 + 근거 서랍). 서버는 파이썬 표준 라이브러리만, 화면은 static/ 의 파일 셋.

    HANPILJI_LLM_API_KEY=... python -m hanpilji.gpt.web [--port 8000] [--host 127.0.0.1]

브라우저에서 http://127.0.0.1:8000 을 열면 됨. 대화는 서버 메모리에만 두고(창마다 따로), 껐다 켜면 사라짐.

지도 블록의 배경지도: 기본 V-World(HANPILJI_VWORLD_MAP_KEY, 키에 적은 서비스 URL에서만 열림). HANPILJI_MAP_TILES=osm · none으로 바꿈.
키가 없으면 배경 없이 필지 경계 · 건물 윤곽만 도면처럼 그림. 배경지도 키는 브라우저가 타일을 받을 때 쓰므로 화면에 드러남.

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
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hanpilji import config as config_mod
from hanpilji.gpt import blocks, engine, llm, tools

MAX_SESSIONS = 200
MAX_QUESTION = 1000
MAX_BODY = 16 * 1024          # 질문 1,000자면 넉넉함. 넘으면 읽지 않고 거절
PER_HOUR = 30                 # 접속 주소마다 시간당 질문 수
TOKEN_ENV = "HANPILJI_WEB_TOKEN"
ASK_FAILED = "답을 만드는 중 오류가 났습니다. 잠시 뒤 다시 물어 주세요."

STATIC = Path(__file__).with_name("static")
FILES = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
         "/app.css": ("app.css", "text/css; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
         "/favicon.svg": ("favicon.svg", "image/svg+xml")}
TOOL_PER_HOUR = 240           # 블록 기간 바꾸기(모델 없이 DB만 읽음)는 넉넉히

# 배경지도 타일 주소. {key}는 V-World 키, {style}은 화면 색(밝음 white · 어두움 midnight)으로 채움
TILES = {
    "vworld": {"url": "https://api.vworld.kr/req/wmts/1.0.0/{key}/{style}/{z}/{y}/{x}.png",
               "styles": {"light": "white", "dark": "midnight"}, "max_zoom": 19, "attr": "배경지도 © V-World(국토교통부)"},
    "osm": {"url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png", "max_zoom": 19, "attr": "배경지도 © OpenStreetMap"},
}


def map_config(env=None):
    """화면에 줄 지도 설정. 정하지 않았거나 V-World 키가 없으면 배경 없음(tiles: None)."""
    env = os.environ if env is None else env
    # 기본은 V-World(2026-10-28 결정): 배경지도 키만 넣으면 켜지고, 키가 없으면 선만 그림
    kind = (env.get("HANPILJI_MAP_TILES") or "vworld").strip().lower()
    t = TILES.get(kind)
    if not t:
        return {"tiles": None}
    t = dict(t)
    if kind == "vworld":
        key = (env.get("HANPILJI_VWORLD_MAP_KEY") or "").strip()
        if not key:
            return {"tiles": None}
        t["url"] = t["url"].replace("{key}", key)
    return {"tiles": t}


def page(name):
    return (STATIC / name).read_text(encoding="utf-8")


def make_handler(cfg, model, rounds, token=None, per_hour=PER_HOUR, tool_per_hour=TOOL_PER_HOUR):
    sessions, lock = {}, threading.Lock()   # sid → {"messages", "lock"}
    hits = {}                               # (종류, 접속 주소) → 최근 1시간 요청 시각들

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

        def _allowed(self):
            if token is None:
                return True
            given = self.headers.get("X-Token") or parse_qs(urlsplit(self.path).query).get("token", [""])[0]
            return hmac.compare_digest(given.encode(), token.encode())

        def _rate_ok(self, kind="ask", limit=None):
            limit = per_hour if limit is None else limit
            now, key = time.monotonic(), (kind, self.client_address[0])
            with lock:
                recent = [t for t in hits.get(key, []) if now - t < 3600]
                if len(recent) >= limit:
                    hits[key] = recent
                    return False
                hits[key] = recent + [now]
                return True

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path in FILES:   # 화면 파일은 요금이 없어 토큰 없이 줌(토큰은 /api/ 에만)
                name, ctype = FILES[url.path]
                return self._send(200, page(name), ctype)
            if url.path == "/config.json":
                return self._json(200, map_config())
            if not self._allowed():
                return self._json(403, {"error": "토큰이 필요합니다"})
            if url.path == "/api/card":
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

        def _body(self):
            """요청 JSON 객체를 읽음. 틀리면 (None, 오류 응답 코드, 문구)."""
            try:
                size = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None, 400, "Content-Length가 숫자가 아님"
            if size > MAX_BODY:
                return None, 413, "요청이 너무 큽니다"
            try:
                body = json.loads(self.rfile.read(size) or b"{}")
            except ValueError:
                return None, 400, "JSON이 아님"
            if not isinstance(body, dict):
                return None, 400, "JSON 객체여야 함"
            return body, 200, ""

        def do_POST(self):
            path = urlsplit(self.path).path
            if path not in ("/api/ask", "/api/ask/stream", "/api/tool"):
                return self._json(404, {"error": "없는 주소"})
            if not self._allowed():
                return self._json(403, {"error": "토큰이 필요합니다"})
            body, code, err = self._body()
            if body is None:
                return self._json(code, {"error": err})
            if path == "/api/tool":
                return self._tool(body)
            question = str(body.get("q", "")).strip()[:MAX_QUESTION]
            if not question:
                return self._json(400, {"error": "질문이 비어 있음"})
            if not self._rate_ok():
                return self._json(429, {"error": f"질문이 너무 많습니다. 한 시간에 {per_hour}개까지 받습니다."})
            sid, sess = self._session(body.get("sid"))
            if path == "/api/ask":
                return self._ask(question, sid, sess)
            self._stream(question, sid, sess)

        def _session(self, sid):
            with lock:
                if not isinstance(sid, str) or sid not in sessions:
                    sid = secrets.token_urlsafe(12)
                    if len(sessions) >= MAX_SESSIONS:
                        sessions.pop(next(iter(sessions)))
                    sessions[sid] = {"messages": None, "lock": threading.Lock()}
                return sid, sessions[sid]

        def _answer(self, question, sess, on_event=None):
            """같은 창의 질문은 한 번에 하나씩(대화가 엇갈려 사라지지 않게). 실패하면 None."""
            with sess["lock"]:
                try:
                    with engine.connect(cfg) as conn:
                        ans = engine.ask(question, model, conn, sess["messages"], max_rounds=rounds, on_event=on_event,
                                         screen=True)
                except Exception as e:
                    print(f"오류: {e!r}", file=sys.stderr)
                    return None
                sess["messages"] = engine.trim(ans.messages)
                return ans

        def _ask(self, question, sid, sess):
            ans = self._answer(question, sess)
            if ans is None:
                return self._json(500, {"error": ASK_FAILED, "sid": sid})
            self._json(200, {"answer": ans.text, "footer": ans.footer, "sid": sid,
                             "blocks": blocks.blocks(ans.results)})

        def _stream(self, question, sid, sess):
            """진행을 Server-Sent Events로 흘려보냄: tool(조회 시작) · block(조회 결과) · answer(답) · error."""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            alive = [True]

            def send(obj):
                if not alive[0]:
                    return
                try:
                    self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                except OSError:   # 사용자가 창을 닫아도 답은 끝까지 만들어 대화에 남김
                    alive[0] = False

            def on_event(e):
                if e["type"] == "tool":
                    send({"type": "tool", "tool": e["tool"], "label": blocks.TOOL_DOING.get(e["tool"], "자료를 찾는 중")})
                elif e["type"] == "result":
                    send({"type": "block", "block": blocks.block(e["result"])})

            send({"type": "start", "sid": sid})
            ans = self._answer(question, sess, on_event)
            if ans is None:
                send({"type": "error", "error": ASK_FAILED})
            else:
                send({"type": "answer", "answer": ans.text, "footer": ans.footer, "stop": ans.stop})
            self.close_connection = True

        def _tool(self, body):
            """블록의 기간만 바꿔 다시 조회(모델을 부르지 않아 요금 없음). 인자는 도구 형식대로 확인함."""
            name, args = body.get("tool"), body.get("args")
            if name not in blocks.MONTHS_CHOICES or not isinstance(args, dict):
                return self._json(400, {"error": "다시 조회할 수 없는 블록"})
            if not self._rate_ok("tool", tool_per_hour):
                return self._json(429, {"error": "너무 자주 바꿨습니다. 잠시 뒤 다시 해 주세요."})
            try:
                with engine.connect(cfg) as conn:
                    out = tools.run(conn, name, args)
            except tools.ToolError as e:
                return self._json(400, {"error": str(e)})
            except Exception as e:
                print(f"오류: {e!r}", file=sys.stderr)
                return self._json(500, {"error": "다시 조회하는 중 오류가 났습니다."})
            out["ref"] = body.get("ref") if isinstance(body.get("ref"), int) else None
            self._json(200, {"block": blocks.block(out)})

        def log_message(self, fmt, *args):   # 질문 내용이 접속 기록에 남지 않게 경로만
            sys.stderr.write(f"{self.address_string()} {self.command} {urlsplit(self.path).path} {args[1] if len(args) > 1 else ''}\n")

    return Handler


def _loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m hanpilji.gpt.web", description="한필지 웹")
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
