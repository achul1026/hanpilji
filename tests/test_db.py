"""PostgreSQL에 붙어서 도는 시험. HANPILJI_TEST_DSN이 없으면 건너뜀.

검증하는 것 — 재실행 멱등(중복이 안 쌓임) · 마트 결과와 직접 센 숫자 일치 · row_total ·
「5%」로 물어도 「20분의 1」 조문이 나옴 · 읽기 전용 계정이 core를 못 읽음 · 실패 시 커서 정지.
DB는 scripts/init_db.sh로 새로 만든 빈 DB여야 함(시험이 데이터를 지우고 넣음).
"""
import csv
import os
import tempfile
import unittest
from pathlib import Path

import psycopg

from hanpilji.collectors import eum_rule, law

DSN = os.environ.get("HANPILJI_TEST_DSN")
FIX = Path(__file__).parent / "fixtures"


def fake_fetch(url):
    return (FIX / ("law_search_sample.xml" if "lawSearch" in url else "law_service_sample.xml")).read_bytes()


@unittest.skipUnless(DSN, "HANPILJI_TEST_DSN 없음")
class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = Path(cls.tmp.name) / "config.toml"
        cfg.write_text(
            f'[db]\ndsn = "{DSN}"\n[storage]\nraw_dir = "./raw"\n[pilot]\nsigungu = ["11680"]\n'
            '[law]\noc = "test"\nnames = ["주택임대차보호법 시행령"]\n',
            encoding="utf-8",
        )
        cls.cfg = str(cfg)
        os.environ.pop("HANPILJI_DSN", None)
        with psycopg.connect(DSN) as c:
            c.execute("TRUNCATE core.reg_rule, core.doc_law, stg.law_article, stg.law_annex, stg.eum_rule_raw,"
                      " ops.reject_estate, ops.ingest_cursor, ops.ingest_task, ops.ingest_run RESTART IDENTITY CASCADE")

    def q(self, sql, *args):
        with psycopg.connect(DSN) as c:
            cur = c.execute(sql, args or None)
            return cur.fetchall() if cur.description else None

    def test_1_law_idempotent(self):
        self.assertEqual(law.main(["--config", self.cfg], fetch=fake_fetch), 0)
        n1 = self.q("SELECT count(*) FROM core.doc_law")[0][0]
        self.assertEqual(n1, 3)
        self.assertEqual(law.main(["--config", self.cfg], fetch=fake_fetch), 0)  # 두 번째: 커서로 건너뜀
        self.q("DELETE FROM ops.ingest_cursor")                                # 커서를 지워도 해시로 건너뜀
        self.assertEqual(law.main(["--config", self.cfg], fetch=fake_fetch), 0)
        self.assertEqual(self.q("SELECT count(*) FROM core.doc_law")[0][0], n1)
        self.assertEqual(self.q("SELECT count(*) FROM stg.law_article")[0][0], n1)

    def test_2_law_search_percent(self):
        rows = self.q("SELECT article_no, row_total FROM mart.q_law_search(%s)", "5%")
        self.assertEqual([r[0] for r in rows], ["제8조"])
        self.assertEqual(rows[0][1], 1)

    def test_3_failure_keeps_cursor(self):
        before = self.q("SELECT cursor_value FROM ops.ingest_cursor")

        calls = []

        def broken(url):
            calls.append(url)
            raise OSError("network down")
        only = sum((["--only", f"법령{i}"] for i in range(5)), [])
        self.assertEqual(law.main(["--config", self.cfg, *only], fetch=broken), 1)
        self.assertEqual(len(calls), law.MAX_NET_FAILS)  # 연달아 접속 실패하면 남은 법령은 부르지 않음
        self.assertEqual(self.q("SELECT cursor_value FROM ops.ingest_cursor"), before)
        self.assertEqual(self.q("SELECT status FROM ops.ingest_run ORDER BY run_id DESC LIMIT 1")[0][0], "failed")

    def test_4_eum_rule_mapping(self):
        path = Path(self.tmp.name) / "행위제한_시험.csv"
        with open(path, "w", encoding="cp949", newline="") as f:
            w = csv.writer(f)
            w.writerow(["용도지역", "건축물", "허용", "비고"])
            w.writerow(["제2종일반주거지역", "제1종근린생활시설", "O", ""])
            w.writerow(["제2종일반주거지역", "제2종근린생활시설", "△", "조례로 정함"])
            w.writerow(["없는지역", "단독주택", "O", ""])
            w.writerow(["전용공업지역", "아파트", "X", ""])
        eum_rule.COLUMN_MAP.update(zone_name="용도지역", act_name="건축물", allowed="허용", condition_text="비고")
        eum_rule.ALLOWED_MAP.update({"O": "가능", "△": "조건부", "X": "불가"})
        try:
            self.assertEqual(eum_rule.main([str(path), "--config", self.cfg]), 0)
            self.assertEqual(eum_rule.main([str(path), "--config", self.cfg]), 0)   # 같은 파일 재실행
        finally:
            eum_rule.COLUMN_MAP.update(dict.fromkeys(eum_rule.COLUMN_MAP))
            eum_rule.ALLOWED_MAP.clear()
        self.assertEqual(self.q("SELECT count(*) FROM core.reg_rule")[0][0], 3)
        self.assertEqual(self.q("SELECT count(*) FROM ops.reject_estate")[0][0], 1)
        rows = self.q("SELECT act_name, allowed, condition_text, row_total"
                      " FROM mart.q_zoning_check('제2종일반주거지역', '근린생활시설')")
        self.assertEqual(rows, [("제1종근린생활시설", "가능", None, 2), ("제2종근린생활시설", "조건부", "조례로 정함", 2)])

    def test_5_gpt_role_reads_mart_only(self):
        with psycopg.connect(DSN, autocommit=True) as c:
            c.execute("SET ROLE hanpilji_gpt")
            self.assertTrue(c.execute("SELECT count(*) FROM mart.q_law_search('목적')").fetchone()[0] >= 1)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                c.execute("SELECT count(*) FROM core.doc_law")


if __name__ == "__main__":
    unittest.main()
