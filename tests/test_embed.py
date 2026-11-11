"""7단계 의미 검색: kb_chunk 채우기 · 벡터 넣기 · 섞은 검색(q_kb_search). 실제 모델 대신 가짜 임베더를 씀.

DB 시험은 HANPILJI_TEST_DSN이 있고 그 DB에 pgvector · 28_kb_vector.sql이 있을 때만 돎.
"""
import hashlib
import math
import os
import tempfile
import unittest
from pathlib import Path

from hanpilji.embedding import DIM, MODEL_NAME, to_literal

DSN = os.environ.get("HANPILJI_TEST_DSN")


class FakeEmbedder:
    """낱말마다 정해진 차원에 1을 더한 뒤 정규화(같은 낱말이 많을수록 가까움)."""
    def __init__(self):
        self.calls = 0

    def encode(self, texts):
        self.calls += 1
        out = []
        for t in texts:
            v = [0.0] * DIM
            for w in (t or "").split():
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


class Unit(unittest.TestCase):
    def test_literal(self):
        self.assertEqual(to_literal([0.5, -0.25]), "[0.500000,-0.250000]")
        self.assertIn("bge-m3", MODEL_NAME)


def _has_vector():
    if not DSN:
        return False
    import psycopg
    with psycopg.connect(DSN) as c:
        return c.execute("SELECT to_regclass('core.kb_chunk') IS NOT NULL").fetchone()[0]


@unittest.skipUnless(_has_vector(), "pgvector · 28_kb_vector.sql이 든 HANPILJI_TEST_DSN 없음")
class Load(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg = psycopg
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = Path(cls.tmp.name) / "config.toml"
        cfg.write_text(f'[db]\ndsn = "{DSN}"\n[storage]\nraw_dir = "./raw"\n[law]\noc = "t"\nnames = []\n', encoding="utf-8")
        cls.cfg = str(cfg)
        os.environ.pop("HANPILJI_DSN", None)
        with psycopg.connect(DSN) as c:
            c.execute("DELETE FROM core.kb_concept WHERE concept_id IN ('C901', 'C902')")
            c.execute("""INSERT INTO core.kb_concept (concept_id, topic_id, term, summary, body, search_text, version_id,
                                                      source, asof) VALUES
                ('C901', 'G10', '지렛대 효과', '빌린 돈으로 자기자본수익률 확대', '저당 금리 보다 수익률 높으면 정의 지렛대',
                 '지렛대 효과 빌린 돈', 'x', 't', current_date),
                ('C902', 'G16', '환원율', '순수익 을 가액 으로 바꾸는 비율', '직접환원법 에서 쓴다', '환원율', 'x', 't', current_date)""")

    @classmethod
    def tearDownClass(cls):
        with cls.psycopg.connect(DSN) as c:
            c.execute("DELETE FROM core.kb_concept WHERE concept_id IN ('C901', 'C902')")
            c.execute("SELECT mart.kb_chunk_refresh()")
        cls.tmp.cleanup()

    def q(self, sql, *args):
        with self.psycopg.connect(DSN) as c:
            return c.execute(sql, args or None).fetchall()

    def test_embed_resume_and_search(self):
        from hanpilji.collectors import embed
        fake = FakeEmbedder()
        self.assertEqual(embed.main(["--config", self.cfg, "--batch", "4"], embedder=fake), 0)
        self.assertEqual(self.q("SELECT count(*) FROM core.kb_chunk WHERE embedding IS NULL"), [(0,)])
        self.assertEqual(self.q("SELECT DISTINCT model FROM core.kb_chunk"), [(MODEL_NAME,)])

        fake.calls = 0
        self.assertEqual(embed.main(["--config", self.cfg], embedder=fake), 0)
        self.assertEqual(fake.calls, 0, "바뀐 조각이 없으면 벡터를 다시 만들지 않음")

        with self.psycopg.connect(DSN) as c:
            c.execute("UPDATE core.kb_concept SET body = '바뀐 본문 직접환원법' WHERE concept_id = 'C902'")
        self.assertEqual(embed.main(["--config", self.cfg], embedder=fake), 0)
        self.assertEqual(fake.calls, 1, "바뀐 조각 하나만 다시 만듦")

        vec = to_literal(fake.encode(["저당 금리 보다 수익률 높으면"])[0])
        got = self.q("SELECT title, vector_rank FROM mart.q_kb_search('지렛대', %s::public.vector, 3)", vec)
        self.assertEqual(got[0], ("지렛대 효과", 1), "글자 · 의미 모두 1위면 맨 위")
        only_vec = self.q("SELECT title, text_rank FROM mart.q_kb_search('없는낱말', %s::public.vector, 1)", vec)
        self.assertEqual(only_vec, [("지렛대 효과", None)], "글자가 안 맞아도 의미로 찾음")
        no_vec = self.q("SELECT title FROM mart.q_kb_search('환원율', NULL, 30, ARRAY['개념'])")
        self.assertIn(("환원율",), no_vec, "벡터 없이도 글자 검색은 됨")

        with self.psycopg.connect(DSN) as c:
            c.execute("DELETE FROM core.kb_concept WHERE concept_id = 'C901'")
        self.assertEqual(embed.main(["--config", self.cfg], embedder=fake), 0)
        self.assertEqual(self.q("SELECT count(*) FROM core.kb_chunk WHERE chunk_id = 'concept:C901'"), [(0,)],
                         "원본에서 지운 조각은 지움")


if __name__ == "__main__":
    unittest.main()
