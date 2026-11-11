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
        os.environ.setdefault("DATA_GO_KR_KEY", "test-key")  # 법령 목록은 공공데이터포털 주소로 만듦(호출은 가짜)
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

    def test_3b_changed_law_without_body_is_pending(self):
        """MST가 바뀌었는데 법제처가 본문을 안 주면(OC=test 차단) 실패가 아니라 「본문 대기」, 커서는 그대로."""
        before = self.q("SELECT cursor_value FROM ops.ingest_cursor WHERE cursor_key = 'law:주택임대차보호법 시행령'")
        blocked = ('<?xml version="1.0" encoding="UTF-8"?><Response><result>사용자 정보 검증에 실패하였습니다.</result>'
                   '</Response>').encode()

        def fetch(url):
            if "lawSearch" in url:
                return (FIX / "law_search_sample.xml").read_bytes().replace(b"900001", b"900099")
            return blocked
        self.q("DELETE FROM ops.ingest_cursor")  # 커서가 없어도 현행 조문 출처의 MST로 비교함
        self.assertEqual(law.main(["--config", self.cfg], fetch=fetch), 0)
        last = self.q("SELECT status, note FROM ops.ingest_run ORDER BY run_id DESC LIMIT 1")[0]
        self.assertEqual(last[0], "done")
        self.assertIn("본문 대기", last[1])
        self.assertEqual(self.q("SELECT status FROM ops.ingest_task ORDER BY task_id DESC LIMIT 1")[0][0], "pending")
        self.assertEqual(self.q("SELECT count(*) FROM ops.ingest_cursor")[0][0], 0)
        # 같은 MST면 커서를 채우고 건너뜀
        self.assertEqual(law.main(["--config", self.cfg], fetch=fake_fetch), 0)
        self.assertEqual(self.q("SELECT cursor_value FROM ops.ingest_cursor WHERE cursor_key = 'law:주택임대차보호법 시행령'"),
                         before)

    def test_3d_future_version_is_refetched_and_not_loaded(self):
        """시행 전 판이 현행으로 들어가 있으면 MST가 같아도 다시 받고, 또 시행 전 판이 오면 넣지 않음."""
        self.assertEqual(law.main(["--config", self.cfg], fetch=fake_fetch), 0)
        self.q("UPDATE core.doc_law SET effective_date = current_date + 100, article_id = article_id || ':f' WHERE law_name = '주택임대차보호법 시행령'")
        urls = []

        def future(url):
            urls.append(url)
            if "lawSearch" in url:
                return fake_fetch(url)
            return fake_fetch(url).replace("<시행일자>20260101".encode(), "<시행일자>20991231".encode())

        self.assertEqual(law.main(["--config", self.cfg], fetch=future), 0)
        self.assertTrue(any("target=eflaw" in u and "efYd=20260101" in u for u in urls), urls)
        st = self.q("SELECT status, error FROM ops.ingest_task WHERE task_key = 'law:주택임대차보호법 시행령' ORDER BY task_id DESC LIMIT 1")
        self.assertEqual(st[0][0], "pending")
        self.assertIn("시행 전", st[0][1])
        # 제대로 된 현행 판이 오면 다시 넣고, 시행 전 판은 현행에서 내려감
        self.assertEqual(law.main(["--config", self.cfg], fetch=fake_fetch), 0)
        self.assertEqual(self.q("SELECT count(*) FROM core.doc_law WHERE law_name = '주택임대차보호법 시행령' "
                                "AND is_current AND effective_date > current_date")[0][0], 0)
        self.assertGreater(self.q("SELECT count(*) FROM core.doc_law WHERE law_name = '주택임대차보호법 시행령' "
                                  "AND is_current")[0][0], 0)

    def test_3c_gateway_error_is_failure(self):
        err = (b'<?xml version="1.0" encoding="UTF-8"?><OpenAPI_ServiceResponse><cmmMsgHeader>'
               b'<errMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</errMsg><returnReasonCode>30</returnReasonCode>'
               b'</cmmMsgHeader></OpenAPI_ServiceResponse>')
        self.assertEqual(law.main(["--config", self.cfg], fetch=lambda url: err), 1)
        note = self.q("SELECT error FROM ops.ingest_task ORDER BY task_id DESC LIMIT 1")[0][0]
        self.assertIn("SERVICE_KEY", note)

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

    def test_6_data_status_rows(self):
        """법령은 법령ID마다 한 줄 + 가장 새 시행일의 이름, dim_stat에 없는 지표는 source_ids NULL."""
        lawd = self.q("SELECT lawd_cd FROM core.dim_region LIMIT 1")
        try:
            with psycopg.connect(DSN) as c:
                for aid, name, eff in (("t-ds-1", "옛이름법", "2020-01-01"), ("t-ds-2", "새이름법", "2025-01-01")):
                    c.execute("INSERT INTO core.doc_law (article_id, law_id, law_name, article_no, article_title, body,"
                              " search_text, effective_date, source, asof) VALUES (%s, 't-ds', %s, '제1조', 't', 'b', 'b',"
                              " %s, 't-ds', current_date)", (aid, name, eff))
                if lawd:
                    c.execute("INSERT INTO core.fact_market (lawd_cd, stat_id, period, value, source, asof)"
                              " VALUES (%s, 'zz_t_ds', '202601', 1, '어딘가 표', current_date)", (lawd[0][0],))
            rows = self.q("SELECT label, row_count, source_ids FROM mart.q_data_status() WHERE grp = '법령'"
                          " AND label IN ('옛이름법', '새이름법')")
            self.assertEqual(rows, [("새이름법", 2, ["law_go_kr"])], "이름이 바뀌어도 한 줄, 최근 이름")
            if lawd:
                st = self.q("SELECT source_ids FROM mart.q_data_status() WHERE label = 'zz_t_ds'")
                self.assertEqual(st, [(None,)], "출처 미등록은 NULL(직접 입력 []과 구별)")
        finally:
            self.q("DELETE FROM core.doc_law WHERE law_id = 't-ds'")
            self.q("DELETE FROM core.fact_market WHERE stat_id = 'zz_t_ds'")


if __name__ == "__main__":
    unittest.main()
