"""6단계 개념 노트(data/concepts/*.toml → core.kb_concept).

DB 시험은 HANPILJI_TEST_DSN이 있을 때만 돎(24_kb_topics.sql · 27_concepts.sql · 23_data_status.sql이 들어간 DB).
"""
import os
import re
import tempfile
import unittest
from pathlib import Path

from hanpilji.collectors import concepts

DSN = os.environ.get("HANPILJI_TEST_DSN")
ROOT = Path(__file__).resolve().parent.parent


class Notes(unittest.TestCase):
    def test_repo_notes(self):
        notes = concepts.load_notes(concepts.DEFAULT_DIR)
        self.assertGreaterEqual(len(notes), 50)
        topics = {n["topic"] for n in notes}
        self.assertEqual(topics, {f"G{i:02d}" for i in range(1, 18)}, "개론 17단원 모두 노트가 있음")
        units = set(re.findall(r"\('(G\d\d)'", (ROOT / "sql" / "24_kb_topics.sql").read_text(encoding="utf-8")))
        self.assertTrue(topics <= units)
        for n in notes:
            for law in n.get("laws", []):
                self.assertRegex(law, r"^\S.* 제\d+조(의\d+)?$", n["id"])

    def test_bad_notes(self):
        with tempfile.TemporaryDirectory() as t:
            for body in ('[[note]]\nid = "C001"\ntopic = "G01"\nterm = "x"\nsummary = "y"\n',          # body 없음
                         '[[note]]\nid = "X1"\ntopic = "G01"\nterm = "x"\nsummary = "y"\nbody = "z"\n',  # id 꼴
                         '[[note]]\nid = "C001"\ntopic = "G01"\nterm = "x"\nsummary = "y"\nbody = "z"\ndata = ["select 1"]\n'):
                Path(t, "notes_1.toml").write_text(body, encoding="utf-8")
                with self.assertRaises(ValueError):
                    concepts.load_notes(t)
            with self.assertRaises(ValueError):   # 노트 파일이 없음
                concepts.load_notes(Path(t) / "none")

    def test_version_changes_with_content(self):
        a = {"topic": "G01", "term": "x", "summary": "y", "body": "z"}
        self.assertEqual(concepts.version(a), concepts.version(dict(a)))
        self.assertNotEqual(concepts.version(a), concepts.version({**a, "body": "z2"}))


@unittest.skipUnless(DSN, "HANPILJI_TEST_DSN 없음")
class Load(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg = psycopg
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = Path(cls.tmp.name) / "config.toml"
        cfg.write_text(f'[db]\ndsn = "{DSN}"\n[storage]\nraw_dir = "./raw"\n[law]\noc = "t"\nnames = []\n', encoding="utf-8")
        cls.cfg = str(cfg)
        cls.dir = Path(cls.tmp.name) / "c"
        cls.dir.mkdir()
        os.environ.pop("HANPILJI_DSN", None)
        with psycopg.connect(DSN) as c:
            cls.saved = c.execute("SELECT count(*) FROM core.kb_concept").fetchone()[0]
            c.execute("DELETE FROM core.kb_concept")

    @classmethod
    def tearDownClass(cls):
        with cls.psycopg.connect(DSN) as c:
            c.execute("DELETE FROM core.kb_concept")
        cls.tmp.cleanup()

    def q(self, sql, *args):
        with self.psycopg.connect(DSN) as c:
            return c.execute(sql, args or None).fetchall()

    def write(self, text):
        (self.dir / "notes_1.toml").write_text(text, encoding="utf-8")

    def test_load_search_change_remove(self):
        self.write('[[note]]\nid = "C001"\ntopic = "G10"\nterm = "지렛대 효과"\nsummary = "빌린 돈으로 수익률을 키움"\n'
                   'body = "총자본수익률이 저당 금리보다 높으면 정의 레버리지"\nformula = "자기자본수익률 = 이익 ÷ 자기자본"\n'
                   'data = ["q_deal_list"]\nlaws = ["민법 제99조"]\n'
                   '[[note]]\nid = "C002"\ntopic = "G16"\nterm = "환원율"\nsummary = "순수익을 가액으로 바꾸는 비율"\n'
                   'body = "직접환원법에서 쓴다"\n')
        self.assertEqual(concepts.main(["--config", self.cfg, "--dir", str(self.dir)]), 0)
        got = self.q("SELECT term, unit, formula, law_refs FROM mart.q_concept_search('레버리지 금리')")
        self.assertEqual(got, [("지렛대 효과", "투자분석과 화폐의 시간가치", "자기자본수익률 = 이익 ÷ 자기자본", ["민법 제99조"])])
        self.assertEqual(self.q("SELECT concept_id FROM mart.q_kb_topic_concepts('G16')"), [("C002",)])
        st = self.q("SELECT label, row_count FROM mart.q_data_status() WHERE label LIKE '개념 노트%'")
        self.assertEqual(st, [("개념 노트: 부동산학개론", 2)])

        self.write('[[note]]\nid = "C002"\ntopic = "G16"\nterm = "환원율"\nsummary = "바뀐 요약"\nbody = "직접환원법에서 쓴다"\n')
        self.assertEqual(concepts.main(["--config", self.cfg, "--dir", str(self.dir)]), 0)
        self.assertEqual(self.q("SELECT concept_id, summary FROM core.kb_concept"), [("C002", "바뀐 요약")],
                         "파일에서 지운 노트는 DB에서도 지움")

    def test_unknown_topic_fails(self):
        self.write('[[note]]\nid = "C009"\ntopic = "Z99"\nterm = "x"\nsummary = "y"\nbody = "z"\n')
        self.assertEqual(concepts.main(["--config", self.cfg, "--dir", str(self.dir)]), 1)


if __name__ == "__main__":
    unittest.main()
