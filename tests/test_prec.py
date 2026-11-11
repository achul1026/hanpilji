"""판례 수집기(hanpilji/collectors/prec.py). 응답 모양은 2026-11-11 개발서버에서 받은 실제 응답(목록 그대로, 본문은 줄임).

DB 시험은 HANPILJI_TEST_DSN이 있을 때만 돎(24_kb_topics.sql · 25_prec.sql이 들어간 DB).
"""
import datetime
import os
import tempfile
import unittest
from pathlib import Path

from hanpilji.collectors import prec

FIX = Path(__file__).parent / "fixtures"
DSN = os.environ.get("HANPILJI_TEST_DSN")
SEARCH = (FIX / "prec_search_real.xml").read_bytes()
BODY = (FIX / "prec_service_sample.xml").read_bytes()
DENIED = ('<?xml version="1.0" encoding="UTF-8"?><Response><result>사용자 정보 검증에 실패하였습니다.</result>'
          '<msg>IP 등록</msg></Response>').encode()


class Parse(unittest.TestCase):
    def test_search_keeps_supreme_court_only(self):
        rows, n_all = prec.parse_search(SEARCH)
        self.assertEqual(n_all, 2)
        self.assertEqual([r["case_no"] for r in rows], ["2025다210305"], "하급심 · 국세법령정보시스템 자료는 뺌")
        self.assertEqual(rows[0]["decided_on"], datetime.date(2026, 2, 26), "목록은 2026.02.26 꼴")

    def test_body(self):
        r = prec.parse_body(BODY)
        self.assertEqual((r["prec_id"], r["case_no"], r["court"]), ("618185", "2025다210305", "대법원"))
        self.assertEqual(r["decided_on"], datetime.date(2026, 2, 26), "본문은 20260226 꼴")
        self.assertNotIn("<br", r["holding"])
        self.assertTrue(r["holding"].startswith("주택임대차보호법 제3조 제2항"), "앞뒤 <br/> · 빈칸 정리")
        self.assertIn("\n① 주민등록은", r["summary"], "<br/>은 줄바꿈으로")
        self.assertIsNone(r["ref_cases"], "빈 참조판례는 None")
        with self.assertRaises(ValueError):   # 일련번호가 숫자가 아니면(파일 이름에 쓰므로) 받지 않음
            prec.parse_body(BODY.replace(b">618185<", b">../x<"))
        rows, _ = prec.parse_search(SEARCH.replace(b">618185<", b">../x<"))
        self.assertEqual(rows, [])
        self.assertEqual(r["ref_articles"], "주택임대차보호법 제3조 제1항, 제2항, 제3조의2 제2항")

    def test_denied(self):
        with self.assertRaises(prec.NotVerified):
            prec.parse_search(DENIED)
        with self.assertRaises(prec.NotVerified):
            prec.parse_body(DENIED)

    def test_topics_and_urls(self):
        cfg = {"prec": {"topics": ["M12:주택임대차 대항력", "J02:중개보수"]}}
        self.assertEqual(prec.topics(cfg), [("M12", "주택임대차 대항력"), ("J02", "중개보수")])
        self.assertEqual(prec.topics(cfg, ["J02"]), [("J02", "중개보수")])
        for bad in ({}, {"prec": {"topics": []}}, {"prec": {"topics": ["M12:대항력", "잘못된줄"]}},
                    {"prec": {"topics": [":빈 단원"]}}):
            with self.assertRaises(ValueError):
                prec.topics(bad)
        self.assertTrue(prec.BASE.startswith("https://"), "OC가 주소에 들어가므로 https")
        u = prec.search_url("abc", "대항력", 2)
        self.assertIn("target=prec", u)
        self.assertIn("search=2", u, "본문 검색")
        self.assertIn("sort=ddes", u, "최근 선고 순")
        self.assertIn("page=2", u)
        self.assertIn("ID=618185", prec.body_url("abc", "618185"))


@unittest.skipUnless(DSN, "HANPILJI_TEST_DSN 없음")
class Load(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg = psycopg
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = Path(cls.tmp.name) / "config.toml"
        cfg.write_text(f'[db]\ndsn = "{DSN}"\n[storage]\nraw_dir = "./raw"\n[law]\noc = "t"\nnames = []\n'
                       '[prec]\nmax_per_topic = 5\ntopics = ["M12:대항력", "M11:차임", "Z99:없는 단원"]\n', encoding="utf-8")
        cls.cfg = str(cfg)
        os.environ.pop("HANPILJI_DSN", None)
        os.environ.pop("LAW_OC", None)
        cls.clean()

    @classmethod
    def clean(cls):
        with cls.psycopg.connect(DSN) as c:
            c.execute("DELETE FROM core.doc_prec WHERE prec_id IN ('618185', '607289', '999')")

    @classmethod
    def tearDownClass(cls):
        cls.clean()
        cls.tmp.cleanup()

    def q(self, sql, *args):
        with self.psycopg.connect(DSN) as c:
            cur = c.execute(sql, args or None)
            return cur.fetchall() if cur.description else None

    def test_load_link_search_and_no_refetch(self):
        calls = []

        def fetch(url):
            calls.append("body" if "lawService" in url else "search")
            return BODY if "lawService" in url else SEARCH

        self.assertEqual(prec.main(["--config", self.cfg, "--only", "M12"], fetch=fetch), 0)
        self.assertEqual(calls.count("body"), 1, "대법원 1건만 본문을 받음")
        self.assertEqual(calls.count("search"), 1, "100건 미만이면 다음 쪽을 안 부름")
        self.assertEqual(self.q("SELECT topic_id, prec_id, query FROM core.kb_topic_prec WHERE prec_id = '618185'"),
                         [("M12", "618185", "대항력")])
        got = self.q("SELECT case_no, decided_on FROM mart.q_prec_search('대항력 소멸 양수')")
        self.assertEqual(got, [("2025다210305", datetime.date(2026, 2, 26))])
        self.assertEqual(self.q("SELECT case_no, query FROM mart.q_kb_topic_precs('M12')"), [("2025다210305", "대항력")])
        st = self.q("SELECT row_count, source_ids FROM mart.q_data_status() WHERE label = '대법원 판례'")
        self.assertEqual(st, [(1, ["law_go_kr_prec"])])

        calls.clear()
        prec.main(["--config", self.cfg, "--only", "M12"], fetch=fetch)
        self.assertEqual(calls, ["search"], "이미 받은 판례는 본문을 다시 받지 않음")

        # 다음 주 검색에서 새 판례(999)가 자리를 차지하면 밀려난 618185는 이 주제어 연결에서 빠짐
        search2 = SEARCH.replace(b">618185<", b">999<")
        body2 = BODY.decode().replace(">618185<", ">999<").replace("2025다210305", "2026다1").encode()

        def fetch2(url):
            return body2 if "lawService" in url else search2

        self.assertEqual(prec.main(["--config", self.cfg, "--only", "M12"], fetch=fetch2), 0)
        self.assertEqual(self.q("SELECT prec_id FROM core.kb_topic_prec WHERE topic_id = 'M12' AND query = '대항력'"),
                         [("999",)])
        self.assertEqual(self.q("SELECT count(*) FROM core.doc_prec WHERE prec_id = '618185'"), [(1,)],
                         "판례 자체는 남김")

    def test_bad_topic_fails(self):
        self.assertEqual(prec.main(["--config", self.cfg, "--only", "Z99"], fetch=lambda u: SEARCH), 1,
                         "없는 단원은 실패로 남김")

    def test_denied_stops_and_fails(self):
        calls = []

        def fetch(url):
            calls.append(url)
            return DENIED

        self.assertEqual(prec.main(["--config", self.cfg], fetch=fetch), 1)
        self.assertEqual(len(calls), 1, "검증 실패면 남은 주제어는 부르지 않음")


if __name__ == "__main__":
    unittest.main()
