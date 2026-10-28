"""지표 수집기(ECOS · R-ONE · KOSIS)와 지표 · 추세 마트 함수 시험.

tests/fixtures/{ecos,rone,kosis}_*_real.json 은 2026-10-28 첫 실제 수집의 응답을 줄 몇 개만 남겨 줄인 것
(오류 응답은 틀린 키로 받은 실제 응답).
DB 시험은 HANPILJI_TEST_DSN이 있을 때만 돎(scripts/init_db.sh로 만든 시험용 DB).
"""
import datetime
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import psycopg

from hanpilji.collectors import market

DSN = os.environ.get("HANPILJI_TEST_DSN")
FIX = Path(__file__).parent / "fixtures"
TODAY = datetime.date(2026, 10, 28)
KEYS = {"ECOS_KEY": "E", "RONE_KEY": "R", "KOSIS_KEY": "K"}


def fixture_fetch(url):
    if "ecos.bok.or.kr" in url:
        name = "ecos_real.json" if "/722Y001/" in url else "ecos_empty_real.json"
    elif "SttsApiTbl.do" in url:
        name = "rone_tables_real.json"
    elif "SttsApiTblData" in url:
        name = "rone_data_real.json"
    else:
        name = "kosis_real.json"
    return (FIX / name).read_bytes()


class Parse(unittest.TestCase):
    def test_ecos(self):
        rows, total = market.ecos_parse((FIX / "ecos_real.json").read_bytes())
        self.assertEqual((len(rows), total), (3, 3))
        pts = list(market.ecos_points(rows, market.BY_ID["base_rate"]))
        self.assertEqual(pts, [("전국", "202607", 2.75, "연%"), ("전국", "202608", 3.0, "연%"), ("전국", "202609", 3.0, "연%")])
        self.assertEqual(market.ecos_parse((FIX / "ecos_empty_real.json").read_bytes()), ([], 0), "INFO-200은 빈 결과")
        with self.assertRaises(market.Fatal):
            market.ecos_parse((FIX / "ecos_error_real.json").read_bytes())
        with self.assertRaises(market.ApiError):
            market.ecos_parse(b"<html>blocked</html>")

    def test_rone(self):
        rows, total = market.rone_parse((FIX / "rone_data_real.json").read_bytes())
        self.assertEqual((len(rows), total), (6, 6))
        pts = list(market.rone_points(rows, market.BY_ID["apt_sale_idx"]))
        self.assertEqual({p[0] for p in pts}, {"전국", "강남구"}, "받을 지역만(서울 중구는 뺌)")
        self.assertEqual(pts[-1], ("강남구", "202609", 98.4989097493266, "지수"))
        self.assertEqual(market.rone_parse(b'{"RESULT": {"CODE": "INFO-200", "MESSAGE": "x"}}'), ([], 0))
        with self.assertRaises(market.Fatal):
            market.rone_parse((FIX / "rone_error_real.json").read_bytes())

    def test_rone_table_by_name(self):
        f = market.Fetcher(lambda url: (FIX / "rone_tables_real.json").read_bytes(), tempfile.mkdtemp())
        self.assertEqual(market.rone_table(f, "R", market.BY_ID["apt_sale_idx"]), "A_2024_00045",
                         "주간 · 규모별 · 연령별 · 계절조정 표는 뺌")
        self.assertEqual(market.rone_table(f, "R", market.BY_ID["apt_jeonse_idx"]), "A_2024_00050")
        self.assertEqual(market.rone_table(f, "R", market.BY_ID["apt_sale_idx"], "X1"), "X1", "config 값이 먼저")
        s = dict(market.BY_ID["apt_sale_idx"], rone={"table": "매매가격지수_아파트", "cycle": "MM"})
        with self.assertRaises(market.ApiError) as cm:
            market.rone_table(f, "R", s)
        self.assertIn("4개 맞음", str(cm.exception))

    def test_kosis(self):
        rows, _ = market.kosis_parse((FIX / "kosis_real.json").read_bytes())
        pts = list(market.kosis_points(rows, market.BY_ID["population"]))
        self.assertEqual(sorted({p[0] for p in pts}), ["강남구", "서울특별시", "전국"])
        self.assertIn(("강남구", "202609", 551907.0, "명"), pts)
        with self.assertRaises(market.Fatal):
            market.kosis_parse((FIX / "kosis_error_real.json").read_bytes())
        with self.assertRaises(market.ApiError):   # 40,000셀 넘게 물으면 오는 오류(지역을 ALL로 물었을 때)
            market.kosis_parse("{\"err\":\"31\",\"errMsg\":\" 40,000셀을 초과한 결과값은 요청하실 수 없습니다.\"}".encode())
        self.assertEqual(market.kosis_parse(b'{"err": "30", "errMsg": "no data"}'), ([], 0))

    def test_urls_and_months(self):
        self.assertEqual(market.months_back(3, TODAY), ("202608", "202610"))
        self.assertEqual(market.months_back(12, datetime.date(2026, 1, 5)), ("202502", "202601"))
        url = market.ecos_url("E", market.BY_ID["base_rate"], 1, 1000, "202601", "202610")
        self.assertEqual(url, "https://ecos.bok.or.kr/api/StatisticSearch/E/json/kr/1/1000/722Y001/M/202601/202610/0101000")
        url = market.kosis_url("K", market.BY_ID["population"], "202601", "202610")
        self.assertIn("orgId=101", url)
        self.assertIn("objL1=00+11+11680+", url, "지역을 정해 40,000셀 제한 안으로")
        url = market.rone_url("R", "SttsApiTblData", 1, STATBL_ID="A_2024_00045", CLS_ID=530038, START_WRTTIME="202601")
        self.assertIn("CLS_ID=530038&START_WRTTIME=202601", url)
        self.assertEqual(set(market.RONE_CLS), set(market.BY_ID["apt_sale_idx"]["regions"]))
        self.assertTrue(all(r in market.REGION for s in market.SERIES for r in s.get("regions", ["전국"])))


@unittest.skipUnless(DSN, "HANPILJI_TEST_DSN 없음")
class WithDb(unittest.TestCase):
    """수집 → q_market_series, 그리고 실거래 · 공시지가로 만든 추세 함수를 시험용 구(99990)에서 확인."""
    SGG = "99990"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = Path(cls.tmp.name) / "config.toml"
        cfg.write_text(f'[db]\ndsn = "{DSN}"\n[storage]\nraw_dir = "{cls.tmp.name}/raw"\n[pilot]\nsigungu = ["11680"]\n'
                       '[law]\noc = "test"\nnames = []\n[market]\nmonths = 12\n', encoding="utf-8")
        cls.cfg = str(cfg)
        os.environ.pop("HANPILJI_DSN", None)
        cls.cleanup()
        d = datetime.date.today()
        m0 = d.replace(day=1)
        ly = m0.replace(year=m0.year - 1)
        with psycopg.connect(DSN) as c:
            # 다른 시험이 지웠을 수 있는 전국 · 서울 행(13_market.sql이 넣는 것)
            c.execute("INSERT INTO core.dim_region (lawd_cd, sido, source, asof) VALUES ('0000000000', '전국', '한필지 지표용', %s), "
                      "('1100000000', '서울특별시', '행정표준코드 법정동코드', %s) ON CONFLICT DO NOTHING", (d, d))
            c.execute("INSERT INTO core.dim_region (lawd_cd, sido, sigungu, emd, source, asof) VALUES "
                      "('9999000000', '시험시', '시험구', NULL, 't-mk', %s), ('9999010100', '시험시', '시험구', '가동', 't-mk', %s)",
                      (d, d))
            c.execute("INSERT INTO core.dim_complex (complex_id, name, lawd_cd, pnu, source, asof) VALUES "
                      "('t-mk1', '시험파크', '9999010100', '9999010100100010000', 't-mk', %s), "
                      "('t-mk2', '둘째타워', '9999010100', '9999010100100020000', 't-mk', %s)", (d, d))
            c.execute("INSERT INTO core.dim_parcel (pnu, lawd_cd, area_m2, source, asof) VALUES "
                      "('9999010100100010000', '9999010100', 1000, 't-mk', %s), "
                      "('9999010100100020000', '9999010100', 3000, 't-mk', %s)", (d, d))
            for pnu, prices in (("9999010100100010000", (1000000, 1100000)), ("9999010100100020000", (2000000, 2400000))):
                for y, p in zip((2025, 2026), prices):
                    c.execute("INSERT INTO core.fact_land_price (pnu, base_year, price_per_m2, source, asof) "
                              "VALUES (%s, %s, %s, 't-mk', %s)", (pnu, y, p, d))
            # 시험파크: 이번 달 ㎡당 300만 · 330만 · 360만(중위 330만), 1년 전 같은 달 300만 3건
            for i, (day, unit) in enumerate([(m0, 3000000), (m0, 3300000), (m0, 3600000),
                                            (ly, 3000000), (ly, 3000000), (ly, 3000000)]):
                c.execute("INSERT INTO core.fact_deal (deal_id, lawd_cd, complex_id, deal_type, contract_date, amount, area, "
                          "source, asof) VALUES (%s, '9999000000', 't-mk1', '아파트', %s, %s, 100, 't-mk', %s)",
                          (f"t-mk-{i}", day, unit * 100, d))

    @classmethod
    def tearDownClass(cls):
        cls.cleanup()

    @classmethod
    def cleanup(cls):
        with psycopg.connect(DSN) as c:
            c.execute("DELETE FROM core.fact_deal WHERE source = 't-mk'")
            c.execute("DELETE FROM core.fact_land_price WHERE source = 't-mk'")
            c.execute("DELETE FROM core.dim_complex WHERE source = 't-mk'")
            c.execute("DELETE FROM core.dim_parcel WHERE source = 't-mk'")
            c.execute("DELETE FROM core.dim_region WHERE source = 't-mk'")
            c.execute("DELETE FROM core.fact_market WHERE stat_id IN (SELECT stat_id FROM core.dim_stat)")
            c.execute("DELETE FROM core.dim_stat")
            c.execute("DELETE FROM stg.market_raw")

    def q(self, sql, *args):
        with psycopg.connect(DSN) as c:
            return c.execute(sql, args or None).fetchall()

    def test_collect_and_series(self):
        with mock.patch.dict(os.environ, KEYS):
            self.assertEqual(market.main(["--config", self.cfg], fetch=fixture_fetch, today=TODAY), 0)
            n1 = self.q("SELECT count(*) FROM core.fact_market")[0][0]
            market.main(["--config", self.cfg], fetch=fixture_fetch, today=TODAY)
        self.assertEqual(self.q("SELECT count(*) FROM core.fact_market")[0][0], n1, "다시 돌려도 안 쌓임")
        rows = self.q("SELECT region, period, value, change FROM mart.q_market_series('base_rate', NULL, 24)")
        self.assertEqual([(r[0], r[1], float(r[2])) for r in rows][-1], ("전국", "202609", 3.0))
        self.assertEqual([float(r[3]) for r in rows if r[3] is not None], [0.25, 0.0], "전월 대비 %p")
        rows = self.q("SELECT region, period, value FROM mart.q_market_series('apt_sale_idx', '강남구', 24)")
        self.assertEqual([(r[0], r[1]) for r in rows], [("강남구", "202608"), ("강남구", "202609")],
                         "12개월 밖(201611)은 받지 않음")
        latest = self.q("SELECT stat_id, region FROM mart.q_market_series()")
        self.assertIn(("population", "강남구"), latest)
        self.assertIn(("apt_sale_idx", "전국"), latest)

    def test_missing_key_is_skipped(self):
        with mock.patch.dict(os.environ, {"ECOS_KEY": "E"}, clear=False):
            for k in ("RONE_KEY", "KOSIS_KEY"):
                os.environ.pop(k, None)
            calls = []
            market.main(["--config", self.cfg], fetch=lambda u: calls.append(u) or fixture_fetch(u), today=TODAY)
        self.assertTrue(calls and all("ecos.bok.or.kr" in u for u in calls))

    def test_price_trend(self):
        rows = self.q("SELECT month, trade_count, trade_median_per_m2, trade_yoy_pct, scope "
                      "FROM mart.q_price_trend(%s, 13)", self.SGG)
        self.assertEqual(len(rows), 13)
        last = rows[-1]
        self.assertEqual((last[1], last[2], float(last[3])), (3, 3300000, 10.0))
        self.assertEqual(rows[0][1], 3, "1년 전 같은 달")
        self.assertEqual(self.q("SELECT max(trade_count) FROM mart.q_price_trend(%s, 3, '가동')", self.SGG)[0][0], 3,
                         "동은 단지 위치로 가림")
        self.assertIn("(못 찾음)", self.q("SELECT scope FROM mart.q_price_trend(%s, 3, '없는동')", self.SGG)[0][0])
        self.assertEqual(self.q("SELECT sum(trade_count) FROM mart.q_price_trend(%s, 3, NULL, '둘째')", self.SGG)[0][0], 0)

    def test_land_price_trend(self):
        rows = self.q("SELECT base_year, parcel_count, median_per_m2, area_weighted_per_m2, yoy_pct, index_100 "
                      "FROM mart.q_land_price_trend(%s)", self.SGG)
        self.assertEqual([r[:2] for r in rows], [(2025, 2), (2026, 2)])
        self.assertEqual(rows[1][3], 2075000, "(110만 x 1000 + 240만 x 3000) / 4000")
        self.assertEqual(float(rows[1][4]), 15.0, "필지별 10% · 20%의 중위값")
        self.assertEqual(float(rows[1][5]), 115.0)
        one = self.q("SELECT parcel_count, scope FROM mart.q_land_price_trend(%s, '시험파크')", self.SGG)
        self.assertEqual(one[0], (1, "시험구 시험파크"))

    def test_price_vs_official(self):
        rows = self.q("SELECT complex_name, trade_count, trade_median_per_m2, land_price_per_m2, price_multiple, "
                      "land_yoy_pct, row_total FROM mart.q_price_vs_official(%s)", self.SGG)
        self.assertEqual(len(rows), 1, "매매가 3건 이상인 단지만 순위에")
        self.assertEqual(rows[0][:4], ("시험파크", 3, 3300000, 1100000))
        self.assertEqual((float(rows[0][4]), float(rows[0][5])), (3.0, 10.0))
        rows = self.q("SELECT complex_name, trade_count, price_multiple FROM mart.q_price_vs_official(%s, '둘째')", self.SGG)
        self.assertEqual(rows, [("둘째타워", 0, None)], "이름으로 물으면 거래가 없어도 보여 줌")


if __name__ == "__main__":
    unittest.main()
