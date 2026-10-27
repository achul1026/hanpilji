"""부동산 GPT 시험. LLM은 미리 정한 답을 돌려주는 가짜(Scripted)나 가짜 클라이언트로 바꿔서 키 없이 돎.

검증하는 것 — 도구 인자 확인 · 답 끝 근거(건수 · 출처)를 엔진이 붙임 · 도구 오류를 모델에게 돌려줌 ·
거절 · 조회 없이 답한 것 표시 · Anthropic 요청 모양(생각 블록 그대로 돌려주기, 키 환경변수) ·
DB가 있으면 실제 마트 함수로 도구 실행 · 읽기 전용 접속.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import psycopg

from hanpilji.gpt import engine, llm, tools
from hanpilji.gpt.llm import Reply

DSN = os.environ.get("HANPILJI_TEST_DSN")

PRICE_OUT = {"tool": "q_complex_price", "args": {"name": "도곡렉슬", "area_m2": 84}, "shown": 1, "row_total": 1,
             "sources": ["국토교통부 아파트 매매 · 전월세 실거래가"], "asof": "2026-10-27",
             "rows": [{"complex_name": "도곡렉슬", "trade_count": 21, "trade_avg": 3549040000}]}


def call(name, args, id="t1"):
    return {"id": id, "name": name, "input": args}


class Tools(unittest.TestCase):
    def test_specs_are_mart_functions(self):
        self.assertEqual({t["name"] for t in tools.specs()},
                         {"q_complex_price", "q_law_search", "q_zoning_check", "q_building_filter"})
        for t in tools.TOOLS:
            self.assertTrue(t["sql"].startswith("SELECT * FROM mart."), "도구는 mart 함수만 부름")

    def test_check_args(self):
        tools.check_args("q_complex_price", {"name": "도곡렉슬", "area_m2": 84})
        for name, args, msg in [
            ("q_drop_table", {}, "없는 도구"),
            ("q_complex_price", {}, "필수 인자 없음"),
            ("q_complex_price", {"name": "a", "sql": "x"}, "모르는 인자"),
            ("q_complex_price", {"name": "a", "months": "12"}, "integer"),
            ("q_complex_price", {"name": "a", "months": True}, "integer"),
        ]:
            with self.assertRaises(tools.ToolError, msg=name) as e:
                tools.check_args(name, args)
            self.assertIn(msg, str(e.exception))

    def test_arg_defaults(self):
        args = tools.BY_NAME["q_complex_price"]["args"]
        self.assertEqual(args({"name": "a"}), ("11680", "a", None, 12))
        self.assertEqual(args({"name": "a", "sigungu": "강남구", "months": 999}), ("11680", "a", None, 36))


class Engine(unittest.TestCase):
    def run_with(self, replies, outputs):
        bot = llm.Scripted(replies)
        with mock.patch.object(tools, "run", side_effect=outputs):
            ans = engine.ask("도곡렉슬 84 시세", bot, conn=None)
        return ans, bot

    def test_grounded_answer_gets_footer(self):
        ans, bot = self.run_with(
            [Reply(calls=[call("q_complex_price", {"name": "도곡렉슬", "area_m2": 84})], stop="tool"),
             Reply(text="최근 12개월 매매 21건 평균 35억 4,904만 원입니다.", stop="end")],
            [PRICE_OUT])
        self.assertIn("21건", ans.text)
        self.assertIn("q_complex_price(name=도곡렉슬, area_m2=84) → 1건 (국토교통부 아파트 매매 · 전월세 실거래가 · 기준 2026-10-27)",
                      ans.footer)
        sent = bot.seen[1]["messages"][-1]
        self.assertEqual(sent["role"], "tool")
        self.assertEqual(json.loads(sent["results"][0]["content"])["row_total"], 1, "모델은 row_total을 봄")
        self.assertIn("투자 조언은 하지 않는다", bot.seen[0]["system"])

    def test_partial_rows_in_footer(self):
        out = {**PRICE_OUT, "tool": "q_law_search", "args": {"query": "5% 증액"}, "shown": 10, "row_total": 39}
        ans, _ = self.run_with([Reply(calls=[call("q_law_search", {"query": "5% 증액"})], stop="tool"),
                                Reply(text="…", stop="end")], [out])
        self.assertIn("39건 중 10건 표시", ans.footer)

    def test_tool_error_goes_back_to_model(self):
        bot = llm.Scripted([Reply(calls=[call("q_complex_price", {"area_m2": 84})], stop="tool"),
                            Reply(text="단지명을 알려 주세요.", stop="end")])
        ans = engine.ask("84㎡ 시세", bot, conn=None)
        result = bot.seen[1]["messages"][-1]["results"][0]
        self.assertTrue(result["is_error"])
        self.assertIn("필수 인자 없음: name", result["content"])
        self.assertIn("오류", ans.footer)

    def test_db_error_is_hidden_from_user_but_marked(self):
        conn = mock.Mock()
        conn.execute.side_effect = psycopg.OperationalError("connection lost")
        bot = llm.Scripted([Reply(calls=[call("q_law_search", {"query": "증액"})], stop="tool"),
                            Reply(text="지금은 조회가 안 돼 모릅니다.", stop="end")])
        ans = engine.ask("증액", bot, conn)
        self.assertEqual(bot.seen[1]["messages"][-1]["results"][0]["content"], "조회 실패: OperationalError")
        self.assertIn("오류: OperationalError", ans.footer)
        self.assertNotIn("connection lost", ans.full(), "DB 오류 문구는 사용자에게도 모델에게도 보이지 않음")
        self.assertNotIn("connection lost", json.dumps(bot.seen[1]["messages"][-1], ensure_ascii=False))

    def test_no_tool_is_marked(self):
        ans, _ = self.run_with([Reply(text="안녕하세요.", stop="end")], [])
        self.assertEqual(ans.footer, "근거: 데이터 조회 없이 답함")

    def test_refusal(self):
        ans, _ = self.run_with([Reply(stop="refusal")], [])
        self.assertIn("답을 드리기 어렵습니다", ans.text)
        self.assertEqual(ans.messages[-1], {"role": "assistant", "text": ans.text, "calls": [], "raw": None,
                                            "provider": ""}, "사용자가 본 거절 문구도 대화에 남김")

    def test_max_rounds(self):
        loop = [Reply(calls=[call("q_law_search", {"query": "x"}, id=f"t{i}")], stop="tool") for i in range(4)]
        bot = llm.Scripted(loop)
        with mock.patch.object(tools, "run", side_effect=[{**PRICE_OUT, "rows": []}] * 3) as run:
            ans = engine.ask("x", bot, conn=None, max_rounds=3)
        self.assertEqual(ans.stop, "max_rounds")
        self.assertEqual((len(bot.seen), run.call_count), (4, 3), "도구 3번 + 정리할 기회 1번, 4번째 호출은 실행 안 함")
        self.assertEqual([m["role"] for m in ans.messages[-2:]], ["tool", "assistant"], "호출마다 결과 짝을 맞춤")
        self.assertTrue(ans.messages[-2]["results"][0]["is_error"])

    def test_last_round_gets_summarized(self):
        bot = llm.Scripted([Reply(calls=[call("q_complex_price", {"name": "도곡렉슬"})], stop="tool"),
                            Reply(text="정리", stop="end")])
        with mock.patch.object(tools, "run", return_value=PRICE_OUT):
            ans = engine.ask("x", bot, conn=None, max_rounds=1)
        self.assertEqual((ans.text, ans.stop), ("정리", "end"))

    def test_call_cap_across_parallel_calls(self):
        many = [call("q_law_search", {"query": f"q{i}"}, id=f"t{i}") for i in range(engine.MAX_CALLS + 3)]
        bot = llm.Scripted([Reply(calls=many, stop="tool"), Reply(text="끝", stop="end")])
        with mock.patch.object(tools, "run", return_value=PRICE_OUT) as run:
            engine.ask("x", bot, conn=None)
        self.assertEqual(run.call_count, engine.MAX_CALLS)
        self.assertEqual(len(bot.seen[1]["messages"][-1]["results"]), engine.MAX_CALLS + 3)

    def test_bad_max_rounds(self):
        for bad in (0, -1, "6", True):
            with self.assertRaises(ValueError, msg=repr(bad)):
                engine.rounds_from({"gpt": {"max_rounds": bad}})
        self.assertEqual(engine.rounds_from({}), 6)

    def test_follow_up_keeps_history(self):
        first, _ = self.run_with([Reply(text="안녕하세요.", stop="end")], [])
        bot = llm.Scripted([Reply(text="네.", stop="end")])
        engine.ask("또 물어볼게", bot, None, history=first.messages)
        self.assertEqual([(m["role"], m["text"]) for m in bot.seen[0]["messages"]],
                         [("user", "도곡렉슬 84 시세"), ("assistant", "안녕하세요."), ("user", "또 물어볼게")])

    def test_trim_history_at_question(self):
        history = []
        for i in range(engine.KEEP_TURNS + 2):
            history += [{"role": "user", "text": f"q{i}"},
                        {"role": "assistant", "text": "", "calls": [call("q_law_search", {"query": "x"})]},
                        {"role": "tool", "results": [{"id": "t1", "content": "{}"}]},
                        {"role": "assistant", "text": f"a{i}", "calls": []}]
        kept = engine.trim(history)
        self.assertEqual(kept[0], {"role": "user", "text": "q2"})
        self.assertEqual(len(kept), engine.KEEP_TURNS * 4)


def block(**kw):
    return SimpleNamespace(**kw)


class AnthropicProvider(unittest.TestCase):
    def fake_client(self, responses):
        client = mock.Mock()
        client.beta.messages.create.side_effect = responses
        return client

    def test_key_from_own_env(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "x"}, clear=False):
            os.environ.pop(llm.KEY_ENV, None)
            with self.assertRaises(llm.MissingKey):
                llm.Anthropic("claude-opus-5-5")

    def test_round_trip(self):
        thinking = block(type="thinking", thinking="", signature="sig")
        use = block(type="tool_use", id="tu1", name="q_law_search", input={"query": "증액"})
        usage = block(input_tokens=10, output_tokens=5, cache_read_input_tokens=0)
        client = self.fake_client([
            block(stop_reason="tool_use", content=[thinking, use], model="claude-opus-5-5", usage=usage),
            block(stop_reason="end_turn", content=[block(type="text", text="답")], model="claude-opus-5-5", usage=usage),
        ])
        p = llm.Anthropic("claude-opus-5-5", client=client)
        with mock.patch.object(tools, "run", return_value={**PRICE_OUT, "tool": "q_law_search"}):
            ans = engine.ask("증액 조문", p, conn=None)
        self.assertEqual(ans.text, "답")
        kw = client.beta.messages.create.call_args_list[1].kwargs
        self.assertEqual(kw["model"], "claude-opus-5-5")
        self.assertEqual(kw["thinking"], {"type": "adaptive"})
        self.assertEqual(kw["fallbacks"], "default")
        self.assertEqual(kw["messages"][1]["content"], [thinking, use], "생각 블록을 바꾸지 않고 돌려줌")
        self.assertEqual(kw["messages"][2]["content"][0]["tool_use_id"], "tu1")
        self.assertNotIn("tool_choice", kw)

    def test_refusal_and_no_fallbacks(self):
        client = self.fake_client([block(stop_reason="refusal", content=[], model="m", usage=None)])
        p = llm.Anthropic("m", fallbacks="", client=client)
        self.assertEqual(p.chat("s", [{"role": "user", "text": "q"}], []).stop, "refusal")
        self.assertNotIn("betas", client.beta.messages.create.call_args.kwargs)

    def test_make_from_config(self):
        with mock.patch.dict(os.environ, {llm.KEY_ENV: "k", "HANPILJI_LLM_MODEL": "claude-sonnet-5-5"}):
            p = llm.make({"gpt": {"model": "claude-opus-5-5", "effort": "low"}})
        self.assertEqual((p.model, p.effort), ("claude-sonnet-5-5", "low"))
        with self.assertRaises(ValueError):
            with mock.patch.dict(os.environ, {"HANPILJI_LLM_PROVIDER": "nope"}):
                llm.make({})


@unittest.skipUnless(DSN, "HANPILJI_TEST_DSN 없음")
class WithDb(unittest.TestCase):
    """실거래가 시험 응답(세곡푸르지오)을 넣고 실제 마트 함수로 도구를 돌림."""

    @classmethod
    def setUpClass(cls):
        from hanpilji.collectors import rtms
        from tests.test_stage1 import fixture_fetch
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = Path(cls.tmp.name) / "config.toml"
        cfg.write_text(f'[db]\ndsn = "{DSN}"\n[storage]\nraw_dir = "./raw"\n[pilot]\nsigungu = ["11680"]\n'
                       '[law]\noc = "test"\nnames = []\n[rtms]\nmonths = 1\nrefetch_months = 1\n', encoding="utf-8")
        os.environ.pop("HANPILJI_DSN", None)
        assert rtms.main(["--config", str(cfg)], fetch=fixture_fetch) == 0
        cls.cfg = {"db": {"dsn": DSN}}

    def test_price_tool(self):
        with engine.connect(self.cfg) as conn:
            out = tools.run(conn, "q_complex_price", {"name": "세곡 푸르지오", "area_m2": 85})
            self.assertEqual(out["shown"], 1)
            self.assertEqual(out["row_total"], 1)
            self.assertEqual(out["sources"], ["국토교통부 아파트 매매 · 전월세 실거래가"])
            self.assertNotIn("row_total", out["rows"][0])
            json.dumps(out, ensure_ascii=False)   # 날짜 · 숫자가 JSON으로 바뀜
            self.assertEqual(tools.run(conn, "q_complex_price", {"name": "없는단지"})["row_total"], 0)

    def test_every_tool_runs(self):
        with engine.connect(self.cfg) as conn:
            for name, args in [("q_law_search", {"query": "차임 증액"}),
                               ("q_zoning_check", {"zone": "제2종일반주거지역", "act": "휴게음식점"}),
                               ("q_building_filter", {"lawd_prefix": "11680", "min_age": 20, "elevator": False})]:
                self.assertIn("row_total", tools.run(conn, name, args), name)

    def test_read_only(self):
        with engine.connect(self.cfg) as conn:
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                conn.execute("CREATE TABLE mart.x (a int)")


class Web(unittest.TestCase):
    """웹 채팅 서버를 실제 포트로 띄워 요청 처리(키 · DB 없이: 엔진을 가짜로 바꿈)."""

    def serve(self, token=None, per_hour=30):
        import threading
        from http.server import ThreadingHTTPServer

        from hanpilji.gpt import web
        server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler({}, None, 6, token, per_hour))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_address[1]}"
        conn = mock.MagicMock()
        p1 = mock.patch.object(engine, "connect", return_value=conn)
        p2 = mock.patch.object(engine, "ask", side_effect=lambda q, *a, **k: engine.Answer(
            f"답:{q}", footer="근거", messages=[{"role": "user", "text": q}]))
        p1.start(), p2.start()
        self.addCleanup(mock.patch.stopall)

    def post(self, body, headers=None):
        import urllib.error
        import urllib.request
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        req = urllib.request.Request(self.base + "/api/ask", data=data,
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_ask_and_bad_bodies(self):
        self.serve()
        code, got = self.post({"q": "시세", "sid": ["x"]})
        self.assertEqual((code, got["answer"], got["footer"]), (200, "답:시세", "근거"))
        self.assertEqual(self.post({"q": "또", "sid": got["sid"]})[1]["sid"], got["sid"], "같은 창은 같은 대화")
        self.assertEqual(self.post([1, 2])[0], 400)
        self.assertEqual(self.post(b"not json")[0], 400)
        self.assertEqual(self.post({"q": " "})[0], 400)
        self.assertEqual(self.post({"q": "x" * 20000})[0], 413)

    def test_token_and_rate_limit(self):
        self.serve(token="s3cret", per_hour=2)
        self.assertEqual(self.post({"q": "a"})[0], 403)
        self.assertEqual(self.post({"q": "a"}, {"X-Token": "wrong"})[0], 403)
        self.assertEqual([self.post({"q": "a"}, {"X-Token": "s3cret"})[0] for _ in range(3)], [200, 200, 429])

    def test_refuses_public_host_without_token(self):
        from hanpilji.gpt import web
        os.environ.pop(web.TOKEN_ENV, None)
        self.assertEqual(web.main(["--host", "0.0.0.0", "--port", "0"]), 2)
