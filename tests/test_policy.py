"""정책 축: 정책 목록(q_policy_events)과 발표 전후 비교(q_policy_impact), 화면 블록.

DB 시험은 HANPILJI_TEST_DSN이 있을 때만 돎(scripts/init_db.sh로 만든 시험용 DB).
시험용 구(99990)에 가짜 정책 · 거래 · 지수를 넣고, 발표 앞뒤로 값이 어떻게 나오는지 봄.
"""
import datetime
import os
import unittest

import psycopg

from hanpilji.gpt import blocks, tools

DSN = os.environ.get("HANPILJI_TEST_DSN")


def impact_out(rows):
    return {"tool": "q_policy_impact", "args": {"event": "2025-10-15"}, "ref": 1, "shown": 2, "row_total": 2,
            "sources": [], "asof": None, "rows": rows}


class Offline(unittest.TestCase):
    def test_args(self):
        spec = tools.BY_NAME["q_policy_impact"]
        self.assertEqual(spec["args"]({"event": " 10·15 "}), ("10·15", "11680", 90, 6))
        self.assertEqual(spec["args"]({"event": "x", "days": 999, "months": 0}), ("x", "11680", 180, 6), "0은 기본값")
        self.assertEqual(spec["args"]({"event": "x", "days": 1, "months": 99}), ("x", "11680", 30, 24))
        ev = tools.BY_NAME["q_policy_events"]
        self.assertEqual(ev["args"]({"from_date": "2025-01-01"}), (None, None, datetime.date(2025, 1, 1)))
        with self.assertRaises(tools.ToolError):
            ev["args"]({"from_date": "2025/1/1"})
        with self.assertRaises(tools.ToolError):
            tools.check_args("q_policy_events", {"kind": "주식"})

    def test_impact_block(self):
        rows = [{"section": "정책", "metric": "10·15 주택시장 안정화 대책", "scope": "서울 전역", "base_date": "2025-10-15",
                 "note": "규제지역 강화. 내용 발표 2025-10-15 · https://www.fsc.go.kr/edu/news/85498", "screen_only": False},
                {"section": "실거래", "metric": "아파트 매매 ㎡당 중위 단가", "unit": "원/㎡", "before_value": 1, "after_value": 2,
                 "screen_only": False},
                {"section": "같은 단지", "metric": "같은 단지 변화", "unit": "%", "change_pct": 4.9, "screen_only": False}]
        for reg in ("강남구", "서울특별시"):
            for per, v in (("202509", 100), ("202510", 101)):
                rows.append({"section": "그래프", "metric": f"{reg} 매매가격지수(발표 전달 = 100)", "before_period": per,
                             "before_value": v, "unit": "지수", "screen_only": True})
        rows.append({"section": "그래프", "metric": "강남구 아파트 매매 건수", "before_period": "202510", "before_value": 7,
                     "unit": "건", "partial": True, "screen_only": True})
        b = blocks.block(impact_out(rows))
        self.assertEqual((b["kind"], b["title"]), ("policy", "10·15 주택시장 안정화 대책 정책 전후 비교"))
        self.assertEqual(b["head"]["name"], "10·15 주택시장 안정화 대책")
        self.assertEqual([r["fmt"] for r in b["rows"]], ["won_m2", "pct"], "정책 머리 · 그래프 줄은 표에서 뺌")
        idx, cnt = b["charts"]
        self.assertEqual([s["label"] for s in idx["series"]], ["강남구", "서울특별시"])
        self.assertEqual(idx["markers"], [{"x": "202510", "label": "발표"}])
        self.assertEqual(idx["rows"], [{"period": "202509", "v0": 100, "v1": 100}, {"period": "202510", "v0": 101, "v1": 101}])
        self.assertEqual((cnt["series"][0]["fmt"], cnt["rows"][0].get("partial")), ("int", True))
        self.assertEqual(tools.for_model(impact_out(rows))["screen_rows"], 5, "그래프 줄은 모델에게 안 넘김")

    def test_events_block_and_next_steps(self):
        out = {"tool": "q_policy_events", "args": {}, "ref": 1, "shown": 2, "row_total": 2, "sources": [], "asof": None,
               "rows": [{"event_id": "2025-03-19", "name": "토허 지정", "announce_date": "2025-03-19",
                         "effective_date": "2025-03-24", "kind": "토지거래허가", "direction": "강화", "region_scope": "강남구",
                         "summary": "지정", "law_name": "부동산 거래신고 등에 관한 법률", "article_no": "제10조",
                         "article_title": "토지거래허가구역의 지정", "source_url": "https://example.org/a"},
                        {"event_id": "rate-202410", "name": "기준금리 인하", "announce_date": "2024-10-01", "kind": "금리",
                         "direction": "완화", "region_scope": "전국", "summary": "인하", "source_url": "https://ecos.bok.or.kr/"}]}
        b = blocks.block(out)
        self.assertEqual(b["kind"], "document")
        it = b["items"][0]
        self.assertEqual((it["subtitle"], it["url"], it["link"]), ("토지거래허가 강화 · 강남구", "https://example.org/a", "발표 · 보도 원문 ↗"))
        self.assertIn("근거 부동산 거래신고 등에 관한 법률 제10조(토지거래허가구역의 지정)", it["meta"])
        self.assertEqual(blocks.next_steps([out]), [{"label": "토허 지정 전후 비교", "tool": "q_policy_impact",
                                                     "args": {"event": "2025-03-19"}}], "금리 변경은 이어 보기에서 뺌")
        self.assertEqual(blocks.next_steps([impact_out([])]),
                         [{"label": "정책 · 규제 변화 전체 목록", "tool": "q_policy_events", "args": {}}])
        self.assertTrue({"q_policy_impact", "q_policy_events"} <= blocks.FOLLOW_TOOLS)


@unittest.skipUnless(DSN, "HANPILJI_TEST_DSN 없음")
class WithDb(unittest.TestCase):
    """시험용 구(99990): 가동 단지 하나 · 나동 단지 하나. 발표 전 90일에 3건(㎡당 100만), 뒤 90일에 6건(㎡당 110만)."""
    SGG = "99990"

    @classmethod
    def setUpClass(cls):
        cls.cleanup()
        d = datetime.date.today()
        cls.d0 = d0 = d - datetime.timedelta(days=100)
        m_base = (d0.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
        with psycopg.connect(DSN) as c:
            c.execute("INSERT INTO core.dim_region (lawd_cd, sido, sigungu, emd, source, asof) VALUES "
                      "('9999000000', '시험시', '시험구', NULL, 't-pol', %s), ('9999010100', '시험시', '시험구', '가동', 't-pol', %s), "
                      "('9999010200', '시험시', '시험구', '나동', 't-pol', %s)", (d, d, d))
            c.execute("INSERT INTO core.dim_complex (complex_id, name, lawd_cd, source, asof) VALUES "
                      "('t-pol1', '가동파크', '9999010100', 't-pol', %s), ('t-pol2', '나동타워', '9999010200', 't-pol', %s)", (d, d))
            c.execute("INSERT INTO core.ref_policy_event (event_id, name, announce_date, kind, direction, region_scope, sigungu, emd, "
                      "summary, source, source_url, asof) VALUES ('t-pol', '시험 허가구역 지정', %s, '토지거래허가', '강화', "
                      "'시험구 가동', '{99990}', '{가동}', '가동을 허가구역으로', '시험', 'https://example.org/t', %s), "
                      "('t-pol-b', '시험 겹친 대책', %s, '대출', '강화', '전국', NULL, NULL, '겹침', '시험', 'https://example.org/b', %s)",
                      (d0, d, d0 + datetime.timedelta(days=20), d))
            deals = [(d0 - datetime.timedelta(days=10 + i), "t-pol1", 100) for i in range(2)]
            deals += [(d0 - datetime.timedelta(days=5), "t-pol2", 100)]
            deals += [(d0 + datetime.timedelta(days=5 + i), "t-pol1", 110) for i in range(4)]
            deals += [(d0 + datetime.timedelta(days=5 + i), "t-pol2", 110) for i in range(2)]
            deals += [(d0 - datetime.timedelta(days=200), "t-pol1", 50)]   # 비교 기간 밖(데이터 시작일로만 쓰임)
            for i, (day, cx, unit) in enumerate(deals):
                c.execute("INSERT INTO core.fact_deal (deal_id, lawd_cd, complex_id, deal_type, contract_date, amount, area, "
                          "source, asof) VALUES (%s, '9999000000', %s, '아파트', %s, %s, 84, 't-pol', %s)",
                          (f"t-pol-{i}", cx, day, unit * 10000 * 84, d))
            c.execute("INSERT INTO core.dim_stat (stat_id, name, unit, cycle, source_id, source, asof) VALUES "
                      "('apt_sale_idx', '아파트 매매가격지수(월간)', '지수', 'M', 'rone', '한국부동산원 R-ONE', %s) "
                      "ON CONFLICT (stat_id) DO NOTHING", (d,))
            for k in range(-6, 7):
                m = m_base
                for _ in range(abs(k)):
                    m = (m + datetime.timedelta(days=32)).replace(day=1) if k > 0 else (m - datetime.timedelta(days=1)).replace(day=1)
                c.execute("INSERT INTO core.fact_market (lawd_cd, stat_id, period, value, source, asof) "
                          "VALUES ('9999000000', 'apt_sale_idx', %s, %s, 't-pol', %s)", (m.strftime("%Y%m"), 100 + k, d))

    @classmethod
    def tearDownClass(cls):
        cls.cleanup()

    @classmethod
    def cleanup(cls):
        with psycopg.connect(DSN) as c:
            c.execute("DELETE FROM core.fact_market WHERE lawd_cd = '9999000000' AND source = 't-pol'")
            c.execute("DELETE FROM core.fact_deal WHERE source = 't-pol'")
            c.execute("DELETE FROM core.ref_policy_event WHERE source = '시험'")
            c.execute("DELETE FROM core.dim_complex WHERE source = 't-pol'")
            c.execute("DELETE FROM core.dim_region WHERE source = 't-pol'")

    def run_tool(self, name, args):
        with psycopg.connect(DSN, autocommit=True) as c:
            return tools.run(c, name, args)

    def test_events(self):
        out = self.run_tool("q_policy_events", {"query": "시험"})
        self.assertEqual([r["event_id"] for r in out["rows"]], ["t-pol-b", "t-pol"], "최근 것부터")
        self.assertEqual(self.run_tool("q_policy_events", {"kind": "토지거래허가", "query": "시험"})["row_total"], 1)
        real = self.run_tool("q_policy_events", {"query": "10.15"})
        self.assertEqual(real["rows"][0]["event_id"], "2025-10-15", "점(.)으로 써도 가운뎃점(·) 이름을 찾음")

    def test_impact(self):
        out = self.run_tool("q_policy_impact", {"event": "t-pol", "sigungu": self.SGG})
        rows = {(r["section"], r["metric"], r.get("scope")): r for r in out["rows"] if not r.get("screen_only")}
        self.assertEqual(out["row_total"], len(rows), "그래프 줄은 row_total에 안 셈")
        head = rows[("정책", "시험 허가구역 지정", "시험구 가동")]
        self.assertEqual((head["event_id"], head["base_date"]), ("t-pol", self.d0.isoformat()))
        cnt = rows[("실거래", "아파트 매매 건수(30일 환산)", "시험구")]
        self.assertEqual((cnt["n_before"], cnt["n_after"], cnt["before_value"], cnt["after_value"]), (3, 6, 1, 2))
        self.assertEqual(cnt["change_pct"], 100)
        med = rows[("실거래", "아파트 매매 ㎡당 중위 단가", "시험구")]
        self.assertEqual((med["before_value"], med["after_value"], med["change_pct"]), (1000000, 1100000, 10))
        pair = rows[("같은 단지", "같은 단지 · 같은 면적 매매 단가 변화(중위)", "시험구")]
        self.assertEqual((pair["change_pct"], pair["n_before"]), (10, 2))
        inside = rows[("지정 동", "아파트 매매 건수(30일 환산)", "가동")]
        outside = rows[("지정 동", "아파트 매매 건수(30일 환산)", "그 밖의 시험구")]
        self.assertEqual((inside["n_before"], inside["n_after"], outside["n_before"], outside["n_after"]), (2, 4, 1, 2))
        post = rows[("가격지수", "아파트 매매가격지수(월간) 이후 6개월", "시험구")]
        pre = rows[("가격지수", "아파트 매매가격지수(월간) 직전 6개월", "시험구")]
        self.assertEqual((pre["before_value"], pre["after_value"], post["after_value"]), (94, 100, 106))
        ov = rows[("겹친 정책", "앞뒤 90일 안의 다른 정책 · 금리 변경", None)]
        self.assertGreaterEqual(ov["change"], 1, "실제 정책 목록도 함께 들어 있어 1건 이상")
        self.assertIn("시험 겹친 대책", ov["note"])
        graph = [r for r in out["rows"] if r.get("screen_only") and r["unit"] == "지수"]
        self.assertEqual(len(graph), 13)
        self.assertIn(100, [r["before_value"] for r in graph], "발표 전달 = 100")

    def test_outside_deal_range(self):
        with psycopg.connect(DSN) as c:
            c.execute("UPDATE core.ref_policy_event SET announce_date = announce_date - 150 WHERE event_id = 't-pol'")
        try:
            out = self.run_tool("q_policy_impact", {"event": "t-pol", "sigungu": self.SGG})
        finally:
            with psycopg.connect(DSN) as c:
                c.execute("UPDATE core.ref_policy_event SET announce_date = announce_date + 150 WHERE event_id = 't-pol'")
        cnt = next(r for r in out["rows"] if r["section"] == "실거래")
        self.assertIsNone(cnt["before_value"])
        self.assertIn("비교할 수 없음", cnt["note"])

    def test_unknown_event(self):
        self.assertEqual(self.run_tool("q_policy_impact", {"event": "없는 정책 이름"})["row_total"], 0)


if __name__ == "__main__":
    unittest.main()
