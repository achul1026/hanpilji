"""공공데이터포털 API 수집기 시험(행위제한 · 아파트 매매 · 전월세). 인증키 없이 샘플 응답으로 돎.

샘플(tests/fixtures/rtms_*, luinfo_*)은 포털 명세 항목 이름으로 손으로 만든 것. 실제 응답이 오면 바꿈.
DB 시험은 HANPILJI_TEST_DSN이 있을 때만 돎.
"""
import datetime
import os
import tempfile
import unittest
from pathlib import Path

import psycopg

from hanpilji import datagokr
from hanpilji.collectors import luinfo, rtms

DSN = os.environ.get("HANPILJI_TEST_DSN")
FIX = Path(__file__).parent / "fixtures"


def fixture_fetch(path, params):
    name = "rtms_trade_sample.xml" if "Trade" in path else "rtms_rent_sample.xml" if "Rent" in path else "luinfo_sample.xml"
    data = (FIX / name).read_bytes()
    return data, datagokr.parse(data)


class Portal(unittest.TestCase):
    def test_key_error_raises(self):
        with self.assertRaises(datagokr.ApiError) as e:
            datagokr.parse((FIX / "datagokr_key_error.xml").read_bytes())
        self.assertIn("SERVICE_KEY_IS_NOT_REGISTERED", str(e.exception))

    def test_key_encoding(self):
        self.assertIn("serviceKey=a%2Bb%3D%3D", datagokr.build_url("x/y", {}, key="a+b=="))   # Decoding 키
        self.assertIn("serviceKey=a%2Bb%3D%3D", datagokr.build_url("x/y", {}, key="a%2Bb%3D%3D"))  # Encoding 키

    def test_json_and_single_item(self):
        obj = datagokr.parse(b'{"response":{"header":{"resultCode":"000","resultMsg":"OK"},'
                             b'"body":{"items":{"item":{"aptNm":"A"}},"totalCount":1}}}')
        self.assertEqual(datagokr.items(obj), [{"aptNm": "A"}])
        empty = datagokr.parse(b'<response><header><resultCode>000</resultCode></header>'
                               b'<body><items/><totalCount>0</totalCount></body></response>')
        self.assertEqual(datagokr.items(empty), [])


class RtmsParse(unittest.TestCase):
    def setUp(self):
        self.rows = datagokr.items(datagokr.parse((FIX / "rtms_trade_sample.xml").read_bytes()))

    def test_deal_values(self):
        d, why = rtms.to_deal(self.rows[0], "11680")
        self.assertIsNone(why)
        self.assertEqual(d["amount"], 2_500_000_000, "만원 → 원")
        self.assertEqual(d["lawd_cd"], "1168010300")
        self.assertEqual(d["contract_date"], datetime.date(2026, 9, 3))
        self.assertFalse(d["is_cancelled"])
        self.assertTrue(rtms.to_deal(self.rows[2], "11680")[0]["is_cancelled"])
        self.assertEqual(rtms.to_deal(self.rows[3], "11680"), (None, "전용면적 없음"))

    def test_same_complex_for_trade_and_rent(self):
        rent = datagokr.items(datagokr.parse((FIX / "rtms_rent_sample.xml").read_bytes()))
        self.assertEqual(rtms.complex_key(self.rows[0]), rtms.complex_key(rent[0]))
        self.assertEqual(rtms.to_rent(rent[0], "11680")[0]["lawd_cd"], "1168000000", "umdCd 없으면 시군구 단위")

    def test_months_back(self):
        self.assertEqual(rtms.months_back(3, datetime.date(2026, 2, 10)), ["202602", "202601", "202512"])


class LuinfoParse(unittest.TestCase):
    def test_rules(self):
        rules = luinfo.parse_rules(datagokr.parse((FIX / "luinfo_sample.xml").read_bytes()))
        got = {r["act_name"]: (r["allowed"], r["conditions"]) for r in rules}
        self.assertEqual(got["제1종근린생활시설"], ("가능", []))
        self.assertEqual(got["제2종근린생활시설 중 단란주점"][0], "불가")
        self.assertEqual(got["제2종근린생활시설 중 일반음식점"], ("조건부", ["주거지역에서 50미터 이상 떨어진 경우"]))
        self.assertIsNone(got["시험행위"][0])


@unittest.skipUnless(DSN, "HANPILJI_TEST_DSN 없음")
class Stage1Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = Path(cls.tmp.name) / "config.toml"
        cfg.write_text(
            f'[db]\ndsn = "{DSN}"\n[storage]\nraw_dir = "./raw"\n[pilot]\nsigungu = ["11680"]\n'
            '[law]\noc = "test"\nnames = []\n[rtms]\nmonths = 1\nrefetch_months = 1\n'
            '[luinfo]\nacts = ["근린생활시설"]\n',
            encoding="utf-8",
        )
        cls.cfg = str(cfg)
        os.environ.pop("HANPILJI_DSN", None)
        with psycopg.connect(DSN) as c:
            c.execute("TRUNCATE core.fact_deal, core.fact_rent, core.dim_complex, core.dim_region, core.reg_rule,"
                      " stg.rtms_raw, stg.luinfo_raw, ops.reject_estate, ops.ingest_cursor, ops.ingest_task,"
                      " ops.ingest_run RESTART IDENTITY CASCADE")

    def q(self, sql, *args):
        with psycopg.connect(DSN) as c:
            cur = c.execute(sql, args or None)
            return cur.fetchall() if cur.description else None

    def test_1_rtms_idempotent(self):
        for _ in range(2):  # 두 번 돌려도 같은 건수
            self.assertEqual(rtms.main(["--config", self.cfg], fetch=fixture_fetch), 0)
        self.assertEqual(self.q("SELECT count(*), count(*) FILTER (WHERE is_cancelled) FROM core.fact_deal")[0], (3, 1))
        self.assertEqual(self.q("SELECT count(*) FROM core.fact_rent")[0][0], 3)
        self.assertEqual(self.q("SELECT count(*) FROM core.dim_complex")[0][0], 1, "매매 · 전월세가 한 단지로 묶임")
        self.assertEqual(self.q("SELECT count(*) FROM ops.reject_estate")[0][0], 2, "면적 없는 줄 x 2회")

    def test_2_complex_price(self):
        rows = self.q("SELECT * FROM mart.q_complex_price('11680', '시험 아파트', 85, 600)")
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r[0], "시험아파트")
        self.assertEqual(r[3], 2, "해제된 거래는 뺌")
        self.assertEqual(r[4], 2_550_000_000)
        self.assertEqual(r[7], datetime.date(2026, 9, 20))
        self.assertEqual(r[9], 2, "월세는 전세에서 뺌")
        self.assertEqual(r[10], 1_250_000_000)
        self.assertEqual(float(r[12]), 49.0, "전세가율 %")
        self.assertEqual(r[-1], 1)

    def test_3_luinfo_rules(self):
        self.assertEqual(luinfo.main(["--config", self.cfg, "--zone", "UQA122"], fetch=fixture_fetch), 0)
        rows = self.q("SELECT allowed, scope FROM mart.q_zoning_check('제2종일반주거지역', '일반음식점', '11680')")
        self.assertEqual(rows, [("조건부", "조례(11680)")])
        self.assertEqual(self.q("SELECT count(*) FROM ops.reject_estate WHERE source_id = 'dg_luinfo'")[0][0], 1)

    def test_4_no_key_fails_cleanly(self):
        os.environ.pop("DATA_GO_KR_KEY", None)
        self.assertEqual(rtms.main(["--config", self.cfg, "--only", "trade"]), 1)
        err = self.q("SELECT error FROM ops.ingest_task WHERE status = 'failed' ORDER BY task_id DESC LIMIT 1")[0][0]
        self.assertIn("DATA_GO_KR_KEY", err)


if __name__ == "__main__":
    unittest.main()
