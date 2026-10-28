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
                         {"q_property_card", "q_complex_price", "q_deal_list", "q_complex_rank", "q_law_search", "q_zoning_check",
                          "q_building_filter", "q_price_trend", "q_land_price_trend", "q_price_vs_official", "q_market_series"})
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
            ("q_deal_list", {"name": "a", "kind": "분양"}, "중 하나"),
            ("q_complex_rank", {"metric": "price"}, "중 하나"),
        ]:
            with self.assertRaises(tools.ToolError, msg=name) as e:
                tools.check_args(name, args)
            self.assertIn(msg, str(e.exception))

    def test_arg_defaults(self):
        args = tools.BY_NAME["q_complex_price"]["args"]
        self.assertEqual(args({"name": "a"}), ("11680", "a", None, 12))
        self.assertEqual(args({"name": "a", "sigungu": "강남구", "months": 999}), ("11680", "a", None, 36))
        self.assertEqual(tools.BY_NAME["q_deal_list"]["args"]({"name": "a"}), ("11680", "a", "매매", None, 12, "recent"))
        self.assertEqual(tools.BY_NAME["q_complex_rank"]["args"]({"metric": "jeonse_count", "limit": 99}),
                         ("11680", "jeonse_count", 12, None, None, 20, "desc"))
        self.assertEqual(tools.BY_NAME["q_price_trend"]["args"]({"emd": "대치동", "months": 99}),
                         ("11680", 36, "대치동", None, None))
        self.assertEqual(tools.BY_NAME["q_land_price_trend"]["args"]({}), ("11680", None, None))
        self.assertEqual(tools.BY_NAME["q_price_vs_official"]["args"]({"order": "asc"}), ("11680", None, 12, 10, "asc"))
        self.assertEqual(tools.BY_NAME["q_market_series"]["args"]({"stat": "base_rate"}), ("base_rate", None, 24))


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

    def test_refs_events_and_results(self):
        events = []
        bot = llm.Scripted([Reply(calls=[call("q_complex_price", {"name": "도곡렉슬"}), call("q_complex_price", {}, id="t2"),
                                         call("q_law_search", {"query": "증액"}, id="t3")], stop="tool"),
                            Reply(text="21건 [1], 조문 [2]", stop="end")])
        with mock.patch.object(tools, "run", side_effect=[dict(PRICE_OUT), tools.ToolError("필수 인자 없음: name"),
                                                         {**PRICE_OUT, "tool": "q_law_search"}]):
            ans = engine.ask("x", bot, conn=None, on_event=events.append, screen=True)
        self.assertEqual([r["ref"] for r in ans.results], [1, 2], "오류 난 호출은 번호를 받지 않음")
        self.assertEqual([e["type"] for e in events], ["tool", "result", "tool", "tool", "result"])
        sent = bot.seen[1]["messages"][-1]["results"]
        self.assertEqual([json.loads(r["content"]).get("ref") for r in sent if not r.get("is_error")], [1, 2])
        self.assertIn("- [1] q_complex_price(name=도곡렉슬, area_m2=84) → 1건", ans.footer)
        self.assertIn("- [2] q_law_search", ans.footer)
        self.assertIn("화면 안내", bot.seen[0]["system"])
        self.assertNotIn("화면 안내", engine.SYSTEM)

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

    def test_deal_list_and_rank(self):
        with engine.connect(self.cfg) as conn:
            top = tools.run(conn, "q_deal_list", {"name": "세곡 푸르지오", "order": "price_desc"})
            self.assertGreater(top["row_total"], 0)
            amounts = [r["amount"] for r in top["rows"]]
            self.assertEqual(amounts, sorted(amounts, reverse=True), "비싼 순이면 첫 줄이 최고가")
            self.assertEqual({r["kind"] for r in top["rows"]}, {"매매"})
            rent = tools.run(conn, "q_deal_list", {"name": "세곡 푸르지오", "kind": "전월세"})
            self.assertTrue({r["kind"] for r in rent["rows"]} <= {"전세", "월세"})
            rank = tools.run(conn, "q_complex_rank", {"metric": "trade_count", "limit": 3})
            self.assertEqual(rank["rows"][0]["rank"], 1)
            self.assertIn("세곡푸르지오", rank["rows"][0]["complex_name"].replace(" ", ""))
            self.assertEqual(tools.run(conn, "q_complex_rank", {"metric": "trade_count", "min_build_year": 2100})
                             ["row_total"], 0)

    def test_property_card(self):
        with engine.connect(self.cfg) as conn:
            card = tools.run(conn, "q_property_card", {"query": "세곡 푸르지오"})
            self.assertEqual(card["shown"], 1)
            row = card["rows"][0]
            self.assertTrue(row["address"].startswith("서울특별시 강남구 세곡동"), row["address"])
            self.assertTrue(row["pnu"].startswith("11680111001"), "세곡동 법정동 코드 + 일반 지번")
            self.assertGreater(row["trade_count"], 0)
            self.assertIn("건축물대장(용도 · 층수 · 주차 · 위반)", row["missing"])
            self.assertLessEqual(len(row["recent_deals"]), 5)
            self.assertTrue(row["recent_deals"], "최근 매매가 카드에 같이 들어 있음")
            json.dumps(card, ensure_ascii=False)   # 배열(전용면적 타입)도 JSON으로 바뀜
            by_addr = tools.run(conn, "q_property_card", {"query": row["address"].split(" ", 2)[2] + "번지"})
            self.assertEqual(by_addr["rows"][0]["complex_name"], row["complex_name"], "동 지번으로도 찾음")
            self.assertEqual(tools.run(conn, "q_property_card", {"query": "없는단지"})["row_total"], 0)

    def test_property_card_joins_all_parcels(self):
        """단지 건물이 선 필지까지 모으고, 「접함」 용도지역은 빼고, 개별공시지가를 먼저 씀(넣은 값은 되돌림)."""
        with psycopg.connect(DSN) as conn:
            cid, pnu = conn.execute("SELECT complex_id, pnu FROM core.dim_complex WHERE pnu IS NOT NULL LIMIT 1").fetchone()
            other = pnu[:11] + "9999" + pnu[15:]
            conn.execute("INSERT INTO core.dim_parcel (pnu, land_category, area_m2, source, asof) VALUES "
                         "(%s, '대', 1000, 't', '2026-10-01'), (%s, '대', 500, 't', '2026-10-01')", (pnu, other))
            conn.execute("INSERT INTO core.fact_land_price (pnu, base_year, is_standard, price_per_m2, source, asof) "
                         "VALUES (%s, 2026, false, 300, 't', '2026-10-01'), (%s, 2026, true, 290, 't', '2026-10-01')",
                         (pnu, pnu))
            conn.execute("INSERT INTO core.bridge_parcel_zone (pnu, zone_cd, overlap, source, asof) VALUES "
                         "(%s, 'UQA123', '포함', 't', '2026-10-01'), (%s, 'UQA130', '접함', 't', '2026-10-01')",
                         (pnu, other))
            conn.execute("INSERT INTO core.dim_building (bld_pk, pnu, main_use, floors_above, total_area, source, asof) "
                         "VALUES ('t1', %s, '공동주택', 35, 2, 't', '2026-10-01'), "
                         "('t2', %s, '공동주택', 30, 1, 't', '2026-10-01')", (pnu, other))
            conn.execute("INSERT INTO core.bridge_complex_building (complex_id, bld_pk, source, asof) "
                         "VALUES (%s, 't2', 't', '2026-10-01')", (cid,))
            row = conn.execute("SELECT land_parcel_count, land_area_m2, land_price_per_m2, zones, bld_count, missing "
                               "FROM mart.q_property_card('11680', (SELECT name FROM core.dim_complex "
                               "WHERE complex_id = %s))", (cid,)).fetchone()
            conn.rollback()
        self.assertEqual(row, (2, 1500, 300, "제3종일반주거지역", 2, []))

    def test_make_pnu(self):
        with engine.connect(self.cfg) as conn:
            got = conn.execute("SELECT core.make_pnu('1168011800', '527'), core.make_pnu('1168011800', '12-3'), "
                               "core.make_pnu('1168011800', '산 4-1'), core.make_pnu('1168011800', 'BL'), "
                               "core.make_pnu('1168000000', '527')").fetchone()
            self.assertEqual(got, ("1168011800105270000", "1168011800100120003", "1168011800200040001", None, None))

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


class Blocks(unittest.TestCase):
    def test_shapes(self):
        from hanpilji.gpt import blocks
        b = blocks.block({**PRICE_OUT, "ref": 1})
        self.assertEqual((b["kind"], b["title"], b["source"]["row_total"]), ("table", "도곡렉슬 84㎡ 면적별 시세", 1))
        self.assertEqual([c["key"] for c in b["columns"]], ["complex_name", "trade_count", "trade_avg"], "값 있는 열만")
        self.assertEqual(b["requery"], {"param": "months", "label": "기간", "choices": [6, 12, 36], "current": 12})
        law = blocks.block({"tool": "q_law_search", "args": {"query": "차임 증액"}, "ref": 2, "shown": 1, "row_total": 1,
                            "sources": [], "asof": None,
                            "rows": [{"law_name": "주택임대차보호법", "article_no": "제7조", "article_title": "차임 등의 증감청구권",
                                      "body": "…", "effective_date": "2026-01-02"}]})
        self.assertEqual(law["kind"], "document")
        self.assertEqual(law["items"][0]["url"], "https://www.law.go.kr/법령/" + "%EC%A3%BC%ED%83%9D%EC%9E%84%EB%8C%80%EC%B0%A8"
                         "%EB%B3%B4%ED%98%B8%EB%B2%95/%EC%A0%9C7%EC%A1%B0")
        self.assertEqual(law["highlight"], ["차임", "증액"])
        trend = blocks.block({"tool": "q_price_trend", "args": {"emd": "대치동"}, "ref": 3, "shown": 1, "row_total": 1,
                              "sources": [], "asof": None, "rows": [{"month": "2026-09", "trade_count": 3, "partial": True,
                                                                     "trade_median_per_pyeong": 1, "scope": "강남구 대치동"}]})
        self.assertEqual((trend["kind"], trend["x"], trend["requery"]["current"]), ("chart", "month", 24))
        series = [{"stat_id": "base_rate", "region": "전국", "period": p, "value": v, "unit": "연%", "note": "월말"}
                  for p, v in [("202607", 2.75), ("202608", 3), ("202609", 3)]]
        m = blocks.block({"tool": "q_market_series", "args": {"stat": "base_rate"}, "ref": 4, "shown": 3, "row_total": 3,
                          "sources": [], "asof": None, "rows": series})
        self.assertEqual((m["kind"], m["series"][0]["label"], len(m["rows"])), ("chart", "전국 (연%)", 3))
        latest = blocks.block({"tool": "q_market_series", "args": {}, "ref": 5, "shown": 1, "row_total": 1,
                               "sources": [], "asof": None, "rows": series[:1]})
        self.assertEqual(latest["kind"], "table", "지표 목록(stat 없음)은 표")


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

    def test_card(self):
        self.serve()
        import urllib.request
        card = {"tool": "q_property_card", "rows": [{"complex_name": "도곡렉슬", "recent_deals": []}], "sources": [],
                "asof": None, "shown": 1, "row_total": 1}
        with mock.patch.object(tools, "run", return_value=card) as run:
            with urllib.request.urlopen(self.base + "/api/card?q=%EB%8F%84%EA%B3%A1") as r:
                got = json.loads(r.read())
        run.assert_called_once()   # 카드와 최근 거래를 한 함수(한 스냅숏)에서 읽음
        self.assertEqual(run.call_args.args[1:], ("q_property_card", {"query": "도곡"}))
        self.assertEqual(got["rows"], card["rows"])

    def test_page_never_uses_innerhtml(self):
        from hanpilji.gpt import web
        js = web.page("app.js")
        self.assertNotIn("innerHTML", js, "답 속 HTML이 실행되지 않게 DOM으로만 그림")
        self.assertNotIn("insertAdjacentHTML", js)
        self.assertIn("function md(", js)

    def test_static_files_need_no_token(self):
        self.serve(token="s3cret")
        import urllib.request
        for path, ctype in [("/", "text/html"), ("/app.js", "text/javascript"), ("/app.css", "text/css"),
                            ("/favicon.svg", "image/svg+xml")]:
            with urllib.request.urlopen(self.base + path) as r:
                self.assertTrue(r.headers["Content-Type"].startswith(ctype), path)
        self.assertEqual(self.post({"q": "a"})[0], 403, "질문(요금 나감)은 여전히 토큰이 필요")

    def test_stream_sends_progress_blocks_and_answer(self):
        self.serve()
        import urllib.request

        def fake_ask(q, *a, on_event=None, screen=False, **k):
            self.assertTrue(screen, "웹은 화면 블록이 보인다고 모델에게 알림")
            on_event = on_event or (lambda e: None)
            on_event({"type": "tool", "tool": "q_complex_price", "args": {"name": "도곡렉슬"}})
            on_event({"type": "result", "result": {**PRICE_OUT, "ref": 1}})
            return engine.Answer("답 [1]", footer="근거", messages=[{"role": "user", "text": q}],
                                 results=[{**PRICE_OUT, "ref": 1}])
        engine.ask.side_effect = fake_ask
        req = urllib.request.Request(self.base + "/api/ask/stream", data=json.dumps({"q": "시세"}).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            self.assertTrue(r.headers["Content-Type"].startswith("text/event-stream"))
            events = [json.loads(line[6:]) for line in r.read().decode().split("\n\n") if line.startswith("data: ")]
        self.assertEqual([e["type"] for e in events], ["start", "tool", "block", "answer"])
        self.assertEqual(events[1]["label"], "실거래가에서 시세를 셈하는 중")
        self.assertEqual((events[2]["block"]["ref"], events[2]["block"]["kind"]), (1, "table"))
        self.assertEqual(events[3]["answer"], "답 [1]")
        code, got = self.post({"q": "또"})
        self.assertEqual(got["blocks"][0]["title"], "도곡렉슬 84㎡ 면적별 시세")

    def test_tool_requery(self):
        self.serve()
        import urllib.error
        import urllib.request

        def tool(body):
            req = urllib.request.Request(self.base + "/api/tool", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        with mock.patch.object(tools, "run", return_value=dict(PRICE_OUT)) as run:
            code, got = tool({"tool": "q_complex_price", "args": {"name": "도곡렉슬", "months": 36}, "ref": 2})
        self.assertEqual(code, 200)
        self.assertEqual(run.call_args.args[1:], ("q_complex_price", {"name": "도곡렉슬", "months": 36}))
        self.assertEqual(got["block"]["ref"], 2)
        self.assertEqual(tool({"tool": "q_law_search", "args": {"query": "x"}})[0], 400, "기간 없는 도구는 다시 조회 안 함")
        self.assertEqual(tool({"tool": "q_complex_price", "args": {"name": "a", "sql": "x"}})[0], 400, "인자 확인")

    def test_token_and_rate_limit(self):
        self.serve(token="s3cret", per_hour=2)
        self.assertEqual(self.post({"q": "a"})[0], 403)
        self.assertEqual(self.post({"q": "a"}, {"X-Token": "wrong"})[0], 403)
        self.assertEqual([self.post({"q": "a"}, {"X-Token": "s3cret"})[0] for _ in range(3)], [200, 200, 429])

    def test_refuses_public_host_without_token(self):
        from hanpilji.gpt import web
        os.environ.pop(web.TOKEN_ENV, None)
        self.assertEqual(web.main(["--host", "0.0.0.0", "--port", "0"]), 2)
