"""5단계 공공 해설자료: 행정규칙 수집기(collectors/admrul.py) · 해설서 넣기(collectors/guide.py) · 글 뽑기(scripts/guide_extract.py).

행정규칙 응답 모양은 2026-11-11 개발서버에서 받은 실제 응답(감정평가 실무기준)을 줄인 것.
DB 시험은 HANPILJI_TEST_DSN이 있을 때만 돎(24_kb_topics.sql · 26_guide.sql · 23_data_status.sql이 들어간 DB).
"""
import datetime
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

from hanpilji.collectors import admrul, guide
from hanpilji.collectors.prec import NotVerified

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures"
DSN = os.environ.get("HANPILJI_TEST_DSN")
SEARCH = (FIX / "admrul_search_sample.xml").read_bytes()
BODY = (FIX / "admrul_service_sample.xml").read_bytes()
DENIED = ('<?xml version="1.0" encoding="UTF-8"?><Response><result>사용자 정보 검증에 실패하였습니다.</result>'
          '<msg>IP 등록</msg></Response>').encode()

_spec = importlib.util.spec_from_file_location("guide_extract", ROOT / "scripts" / "guide_extract.py")
extract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(extract)


class Admrul(unittest.TestCase):
    def test_pick_current_by_name(self):
        rows = admrul.parse_search(SEARCH)
        self.assertEqual(len(rows), 3)
        hit = admrul.pick(rows, "감정평가  실무기준")
        self.assertEqual((hit["seq"], hit["rule_id"], hit["issued_no"]), ("2100000229230", "2066025", "2023-522"),
                         "띄어쓰기가 달라도 이름이 같은 현행본(연혁본은 뺌)")
        self.assertIsNone(admrul.pick(rows, "감정평가"), "이름 일부만 같으면 고르지 않음")
        self.assertEqual(admrul.norm("도시ㆍ군관리계획 수립지침"), admrul.norm("도시·군관리계획수립지침"))

    def test_body_parts(self):
        r = admrul.parse_body(BODY)
        self.assertEqual((r["title"], r["rule_kind"], r["effective_on"]), ("감정평가 실무기준", "고시", datetime.date(2023, 9, 13)))
        self.assertEqual([p["label"] for p in r["parts"]], ["100 총칙", "610 토지의 감정평가"],
                         "CDATA 하나 = 조각 하나, 빈 CDATA · 부칙 · 첨부는 뺌")
        last = r["parts"][1]["body"]
        self.assertIn("한다.\n다만,", last, "<br/>은 줄바꿈")
        self.assertNotIn("<img", last)
        many = BODY.replace("<조문내용>".encode(), "<조문내용>제1조(목적) 이 지침은 &lt;별표&gt; 기준을 정한다.</조문내용><조문내용>".encode(), 1)
        self.assertEqual([p["label"] for p in admrul.parse_body(many)["parts"]][:2], ["제1조", "100 총칙"],
                         "조마다 <조문내용>이 따로 와도 모두 넣음")
        with self.assertRaises(ValueError):
            admrul.parse_body(BODY.replace(b">2066025<", b">../x<"))
        with self.assertRaises(NotVerified):
            admrul.parse_search(DENIED)

    def test_article_label_and_split(self):
        self.assertEqual(admrul.label_of("제3조의2(정의) 이 지침에서"), ("제3조의2", "정의"))
        self.assertEqual(admrul.label_of("1-2-1 목적\n본문"), ("1-2-1 목적", None))
        parts = admrul.split_long("가" * 10 + "\n" + "나" * 25, size=8)
        self.assertTrue(all(len(p) <= 8 for p in parts))
        self.assertEqual("".join(parts), "가" * 10 + "나" * 25)

    def test_rules_config(self):
        self.assertEqual(admrul.rules({"admrul": {"rules": ["G16:감정평가 실무기준"]}}), [("G16", "감정평가 실무기준")])
        for bad in ({}, {"admrul": {"rules": []}}, {"admrul": {"rules": ["G16감정평가"]}}):
            with self.assertRaises(ValueError):
                admrul.rules(bad)
        self.assertTrue(admrul.BASE.startswith("https://"))

    def test_real_config_rules(self):
        import tomllib
        with open(ROOT / "config.toml", "rb") as f:
            got = admrul.rules(tomllib.load(f))
        self.assertGreaterEqual(len(got), 5)


class Catalog(unittest.TestCase):
    def test_repo_catalog_and_jsonl(self):
        docs = guide.catalog(guide.DEFAULT_DIR)
        self.assertGreaterEqual(len(docs), 3)
        for d in docs:
            pages, version = guide.read_pages(guide.DEFAULT_DIR / f"{d['file']}.jsonl")
            self.assertGreater(len(pages), 10, d["file"])
            self.assertEqual(len(version), 16)

    def test_bad_catalog(self):
        with tempfile.TemporaryDirectory() as t:
            Path(t, "catalog.toml").write_text('[[doc]]\nfile = "a b"\ntitle = "x"\nissuer = "y"\nlicense = "z"\n',
                                               encoding="utf-8")
            with self.assertRaises(ValueError):
                guide.catalog(t)
            Path(t, "catalog.toml").write_text('[[doc]]\nfile = "ab"\ntitle = "x"\n', encoding="utf-8")
            with self.assertRaises(ValueError):
                guide.catalog(t)

    def test_extract_text_without_pages(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t, "a.txt")
            p.write_text(("문단 하나 " * 20 + "\r\n\r\n") * 30, encoding="utf-8")
            rows = extract.extract_text(str(p), size=500)
            self.assertGreater(len(rows), 3)
            self.assertTrue(all(r["page"] is None and r["label"].endswith("번째 부분") for r in rows))
            self.assertEqual(extract.parse_ranges("1-3,7"), {1, 2, 3, 7})


@unittest.skipUnless(DSN, "HANPILJI_TEST_DSN 없음")
class Load(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg = psycopg
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = Path(cls.tmp.name) / "config.toml"
        cfg.write_text(f'[db]\ndsn = "{DSN}"\n[storage]\nraw_dir = "./raw"\n[law]\noc = "t"\nnames = []\n'
                       '[admrul]\nrules = ["G16:감정평가 실무기준"]\n', encoding="utf-8")
        cls.cfg = str(cfg)
        bad = Path(cls.tmp.name) / "bad.toml"
        bad.write_text(cfg.read_text(encoding="utf-8").replace('"G16:감정평가 실무기준"', '"G16:없는 지침", "Z99:감정평가 실무기준"'),
                       encoding="utf-8")
        cls.bad = str(bad)
        cls.gdir = Path(cls.tmp.name) / "guides"
        cls.gdir.mkdir()
        os.environ.pop("HANPILJI_DSN", None)
        os.environ.pop("LAW_OC", None)
        cls.clean()

    @classmethod
    def clean(cls):
        with cls.psycopg.connect(DSN) as c:
            c.execute("DELETE FROM core.doc_guide")

    @classmethod
    def tearDownClass(cls):
        cls.clean()
        cls.tmp.cleanup()

    def q(self, sql, *args):
        with self.psycopg.connect(DSN) as c:
            cur = c.execute(sql, args or None)
            return cur.fetchall() if cur.description else None

    def write_guide(self, pages, topics='["M12"]'):
        (self.gdir / "catalog.toml").write_text(
            '[[doc]]\nfile = "lease"\ntitle = "주택임대차 해설"\nissuer = "국토교통부"\nedition = "2020"\n'
            f'issued_on = 2020-08-01\nlicense = "공공누리 제1유형"\ntopics = {topics}\n', encoding="utf-8")
        with open(self.gdir / "lease.jsonl", "w", encoding="utf-8") as f:
            for p in pages:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")

    def test_1_admrul_load_search_no_refetch(self):
        calls = []

        def fetch(url):
            calls.append("body" if "lawService" in url else "search")
            return BODY if "lawService" in url else SEARCH

        self.assertEqual(admrul.main(["--config", self.cfg], fetch=fetch), 0)
        self.assertEqual(calls, ["search", "body"])
        got = self.q("SELECT doc_id, kind, version_id, part_count FROM core.doc_guide WHERE kind = '행정규칙'")
        self.assertEqual(got, [("admrul:2066025", "행정규칙", "2100000229230", 2)])
        hit = self.q("SELECT title, label FROM mart.q_guide_search('공시지가기준법 실거래가')")
        self.assertEqual(hit, [("감정평가 실무기준", "610 토지의 감정평가")])
        self.assertEqual(self.q("SELECT title FROM mart.q_kb_topic_guides('G16')"), [("감정평가 실무기준",)])
        calls.clear()
        self.assertEqual(admrul.main(["--config", self.cfg], fetch=fetch), 0)
        self.assertEqual(calls, ["search"], "일련번호가 같으면 본문을 다시 받지 않음")

    def test_2_admrul_bad_name_and_topic_fail_without_deleting(self):
        self.assertEqual(admrul.main(["--config", self.bad], fetch=lambda u: SEARCH), 1)
        self.assertEqual(self.q("SELECT count(*) FROM core.doc_guide WHERE kind = '행정규칙'"), [(1,)],
                         "실패가 있으면 지난번 자료를 지우지 않음")

    def test_3_admrul_denied_stops(self):
        calls = []
        self.assertEqual(admrul.main(["--config", self.bad], fetch=lambda u: calls.append(u) or DENIED), 1)
        self.assertEqual(len(calls), 1)

    def test_4_guide_load_change_and_remove(self):
        pages = [{"page": 5, "printed": 3, "text": "주택임대차보호법은 주거용 건물의 임대차에 적용됩니다. 대항력은 전입신고 다음 날"},
                 {"page": None, "label": "2번째 부분", "text": "확정일자를 받으면 우선변제권이 생깁니다."}]
        self.write_guide(pages)
        self.assertEqual(guide.main(["--config", self.cfg, "--dir", str(self.gdir)]), 0)
        got = self.q("SELECT label, page FROM core.doc_guide_part WHERE doc_id = 'pdf:lease' ORDER BY part_no")
        self.assertEqual(got, [("3쪽", 5), ("2번째 부분", None)], "책에 인쇄된 쪽 번호로 이름을 붙임")
        hit = self.q("SELECT title, label, license FROM mart.q_guide_search('대항력 전입신고')")
        self.assertEqual(hit, [("주택임대차 해설", "3쪽", "공공누리 제1유형")])
        asof1 = self.q("SELECT version_id FROM core.doc_guide WHERE doc_id = 'pdf:lease'")
        self.assertEqual(guide.main(["--config", self.cfg, "--dir", str(self.gdir)]), 0)
        self.assertEqual(self.q("SELECT version_id FROM core.doc_guide WHERE doc_id = 'pdf:lease'"), asof1)

        self.write_guide(pages[:1], topics='["M13"]')
        self.assertEqual(guide.main(["--config", self.cfg, "--dir", str(self.gdir)]), 0)
        self.assertEqual(self.q("SELECT count(*) FROM core.doc_guide_part WHERE doc_id = 'pdf:lease'"), [(1,)])
        self.assertEqual(self.q("SELECT topic_id FROM core.kb_topic_guide WHERE doc_id = 'pdf:lease'"), [("M13",)])
        st = self.q("SELECT row_count, source_ids FROM mart.q_data_status() WHERE grp = '해설 자료' ORDER BY 1")
        self.assertEqual(st, [(1, ["gov_guide_pdf"]), (2, ["law_go_kr_admrul"])])

        (self.gdir / "catalog.toml").write_text("", encoding="utf-8")
        self.assertEqual(guide.main(["--config", self.cfg, "--dir", str(self.gdir)]), 0)
        self.assertEqual(self.q("SELECT count(*) FROM core.doc_guide WHERE kind = '해설서'"), [(0,)],
                         "목록에서 뺀 해설서는 지움")
        self.assertEqual(self.q("SELECT count(*) FROM core.doc_guide WHERE kind = '행정규칙'"), [(1,)],
                         "행정규칙은 건드리지 않음")

    def test_5_guide_unknown_topic_fails(self):
        self.write_guide([{"page": 1, "text": "글"}], topics='["Z99"]')
        self.assertEqual(guide.main(["--config", self.cfg, "--dir", str(self.gdir)]), 1)


if __name__ == "__main__":
    unittest.main()
