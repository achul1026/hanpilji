"""서울 확대 · 단독다가구 전월세 시험(DB 없이)."""
import unittest
from pathlib import Path

from hanpilji import datagokr, regions
from hanpilji.collectors import rtms

FIX = Path(__file__).parent / "fixtures"


def real_rows(name):
    return datagokr.items(datagokr.parse((FIX / name).read_bytes()))

# 포털 명세의 항목 이름으로 만든 한 줄(활용신청 전이라 실제 응답은 아직 없음, 2026-11-04)
SH_ROW = {"sggCd": "11680", "umdNm": "논현동", "houseType": "다가구", "totalFloorAr": "45.2", "buildYear": "1995",
          "dealYear": "2026", "dealMonth": "9", "dealDay": "12", "deposit": "20,000", "monthlyRent": "50",
          "contractTerm": "26.09~28.09", "contractType": "신규", "useRRRight": " "}


class Regions(unittest.TestCase):
    def test_seoul_is_25_unique_gu(self):
        # 종로 중 용산 성동 광진 동대문 중랑 성북 강북 도봉 노원 은평 서대문 마포 양천 강서 구로 금천 영등포 동작 관악 서초 강남 송파 강동
        expected = ("11110 11140 11170 11200 11215 11230 11260 11290 11305 11320 11350 11380 11410 11440 11470 "
                    "11500 11530 11545 11560 11590 11620 11650 11680 11710 11740").split()
        self.assertEqual(list(regions.SEOUL), expected)

    def test_expand(self):
        self.assertEqual(regions.expand(["11680"]), ["11680"])
        got = regions.expand(["11680", "seoul", "41135"])
        self.assertEqual(got, ["11680"] + [c for c in regions.SEOUL if c != "11680"] + ["41135"])
        with self.assertRaises(ValueError):
            regions.expand(["gangnam"])


class ShRent(unittest.TestCase):
    def test_new_source_registered(self):
        self.assertIn(rtms.KINDS["sh_rent"]["source"], rtms.NEW_SOURCES)
        sql = (Path(__file__).parent.parent / "sql" / "09_stage1.sql").read_text()
        self.assertIn("'dg_sh_rent'", sql)

    def test_kind(self):
        self.assertEqual(rtms.KINDS["sh_rent"]["label"], "단독다가구")
        self.assertIn("sh_rent", rtms.NO_COMPLEX)

    def test_to_rent_uses_contract_area_and_no_complex(self):
        v, why = rtms.to_rent(SH_ROW, "11680")
        self.assertIsNone(why)
        self.assertIsNone(v["complex_id"])
        self.assertEqual(v["area"], 45.2)
        self.assertEqual(v["deposit"], 200_000_000)
        self.assertEqual(v["monthly_rent"], 500_000)
        self.assertEqual(v["lawd_cd"], "1168000000")
        self.assertIsNone(v["floor"])

    def test_row_key_tells_houses_apart(self):
        other = {**SH_ROW, "totalFloorAr": "60.0"}
        self.assertNotEqual(rtms.row_key("rent", SH_ROW), rtms.row_key("rent", other))
        self.assertEqual(rtms.row_key("rent", SH_ROW), rtms.row_key("rent", dict(SH_ROW)))

    def test_apartment_keys_unchanged(self):
        apt = {"sggCd": "11680", "umdNm": "세곡동", "jibun": "1", "aptNm": "a", "excluUseAr": "84.9",
               "dealYear": "2026", "dealMonth": "9", "dealDay": "1", "deposit": "1", "monthlyRent": "0"}
        self.assertNotIn("totalFloorAr", apt)
        v, _ = rtms.to_rent(apt, "11680")
        self.assertTrue(v["complex_id"].startswith("rtms:"))


class NewTrades(unittest.TestCase):
    """단독다가구 매매 · 상업업무용 매매(실제 응답, 강남구 2026-11-04). 상업업무용은 60건 중 앞 30건만 받아 totalCount를 30으로 고침."""

    def test_sources_registered(self):
        sql = (FIX.parent.parent / "sql" / "09_stage1.sql").read_text()
        for kind in ("sh_trade", "nrg_trade"):
            src = rtms.KINDS[kind]["source"]
            self.assertIn(src, rtms.NEW_SOURCES)
            self.assertIn(f"'{src}'", sql)
            self.assertIn(kind, rtms.NO_COMPLEX)
            self.assertEqual(rtms.KINDS[kind]["base"], "trade")

    def test_sh_trade_uses_total_floor_area(self):
        rows = real_rows("rtms_sh_trade_real.xml")
        v, why = rtms.to_deal(rows[0], "11680")
        self.assertIsNone(why)
        self.assertIsNone(v["complex_id"], "지번이 가려져 와서 단지 없이 법정동에만")
        self.assertEqual(v["area"], 379.2)
        self.assertEqual(v["amount"], 3_590_000_000)
        self.assertIsNone(v["floor"])
        self.assertFalse(v["is_cancelled"])

    def test_nrg_trade_uses_building_area_and_cancel(self):
        rows = real_rows("rtms_nrg_trade_real.xml")
        v, why = rtms.to_deal(rows[0], "11680")
        self.assertIsNone(why)
        self.assertIsNone(v["complex_id"])
        self.assertEqual(v["area"], 645.08)
        self.assertEqual(v["amount"], 5_800_000_000)
        cancelled = [r for r in rows if r.get("cdealType") == "O"]
        self.assertTrue(cancelled and rtms.to_deal(cancelled[0], "11680")[0]["is_cancelled"])

    def test_nrg_row_key_tells_buildings_apart(self):
        rows = real_rows("rtms_nrg_trade_real.xml")
        keys = [rtms.row_key("trade", r) for r in rows]
        # 모든 항목이 같은 줄(같은 날 같은 건물의 두 호실)만 같은 키 → load_month가 -1을 붙여 따로 셈
        same = {tuple(sorted((k, v) for k, v in r.items() if k not in ("cdealType", "cdealDay"))) for r in rows}
        self.assertEqual(len(set(keys)), len(same))
        a = rows[0]
        self.assertNotEqual(rtms.row_key("trade", a), rtms.row_key("trade", {**a, "buildingAr": "1.0"}))
        self.assertNotEqual(rtms.row_key("trade", a), rtms.row_key("trade", {**a, "buildingUse": "업무"}))
        self.assertEqual(rtms.row_key("trade", a), rtms.row_key("trade", {**a, "cdealType": "O"}),
                         "해제 여부는 나중에 바뀌므로 키에 넣지 않음")

    def test_sh_rent_key_unchanged(self):
        # 전월세 키는 예전 그대로(이미 적재된 줄과 같은 번호)
        import hashlib
        keys = ["sggCd", "umdNm", "jibun", "aptNm", "aptDong", "dealYear", "dealMonth", "dealDay", "excluUseAr", "floor",
                "deposit", "monthlyRent", "contractTerm", "contractType", "houseType", "totalFloorAr"]
        old = hashlib.sha1("|".join(rtms._s(SH_ROW, k) for k in keys).encode()).hexdigest()
        self.assertEqual(rtms.row_key("rent", {**SH_ROW, "plottageAr": "100"}), old)

    def test_capped(self):
        calls = []
        f = rtms.capped(lambda p, q: calls.append(p) or (b"", {}), 2)
        f("a", {}), f("b", {})
        with self.assertRaises(rtms.CallCap):
            f("c", {})
        self.assertEqual(calls, ["a", "b"])
        self.assertIs(rtms.capped(len, None), len)


if __name__ == "__main__":
    unittest.main()


DSN = __import__("os").environ.get("HANPILJI_TEST_DSN")


@unittest.skipUnless(DSN, "HANPILJI_TEST_DSN 없음")
class DailyLimit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        __import__("os").environ.pop("HANPILJI_DSN", None)   # 셸의 운영 DB 주소가 config보다 앞서지 않게

    def test_limit_stops_only_that_kind_and_is_not_failure(self):
        import tempfile
        from hanpilji import datagokr
        from tests.test_stage1 import fixture_fetch
        calls = []

        def fetch(path, params):
            calls.append(path)
            if "OffiTrade" in path:
                raise datagokr.ApiError("22 LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR")
            return fixture_fetch(path, params)

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.toml"
            cfg.write_text(f'[db]\ndsn = "{DSN}"\n[storage]\nraw_dir = "{tmp}/raw"\n[pilot]\nsigungu = ["11680"]\n'
                           '[law]\noc = "test"\n')
            rc = rtms.main(["--config", str(cfg), "--months", "2", "--only", "offi_trade", "--only", "offi_rent"],
                           fetch=fetch)
        self.assertEqual(rc, 0, "하루 한도는 실패가 아님(내일 이어 받음)")
        self.assertEqual(sum("OffiTrade" in c for c in calls), 1, "한도에 닿으면 그 종류는 바로 멈춤")
        self.assertTrue(any("OffiRent" in c for c in calls), "다른 종류(따로 한도)는 계속")


@unittest.skipUnless(DSN, "HANPILJI_TEST_DSN 없음")
class NewTradesDb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        __import__("os").environ.pop("HANPILJI_DSN", None)   # 셸의 운영 DB 주소가 config보다 앞서지 않게

    def _run(self, args, fetch):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.toml"
            cfg.write_text(f'[db]\ndsn = "{DSN}"\n[storage]\nraw_dir = "{tmp}/raw"\n[pilot]\nsigungu = ["11680"]\n'
                           '[law]\noc = "test"\n')
            return rtms.main(["--config", str(cfg)] + args, fetch=fetch)

    def tearDown(self):
        import psycopg
        with psycopg.connect(DSN) as conn:
            conn.execute("DELETE FROM core.fact_deal WHERE deal_type IN ('상업업무용', '단독다가구')")
            conn.execute("DELETE FROM stg.rtms_raw WHERE source_id IN ('dg_nrg_trade', 'dg_sh_trade')")
            conn.execute("DELETE FROM ops.ingest_cursor WHERE source_id IN ('dg_nrg_trade', 'dg_sh_trade')")

    def test_load_without_complex(self):
        import psycopg
        from tests.test_stage1 import fixture_fetch
        self.assertEqual(self._run(["--months", "1", "--only", "nrg_trade", "--only", "sh_trade"], fixture_fetch), 0)
        with psycopg.connect(DSN) as conn:
            got = dict(conn.execute("""SELECT deal_type, count(*) FROM core.fact_deal
                                       WHERE deal_type IN ('상업업무용', '단독다가구') AND complex_id IS NULL
                                       GROUP BY 1""").fetchall())
            n_cx = conn.execute("""SELECT count(*) FROM core.fact_deal
                                   WHERE deal_type IN ('상업업무용', '단독다가구') AND complex_id IS NOT NULL""").fetchone()[0]
        self.assertEqual(got, {"상업업무용": 30, "단독다가구": 5}, "같은 줄 두 번(-1)도 따로 셈")
        self.assertEqual(n_cx, 0)

    def test_max_calls_stops_kind_not_failure(self):
        from tests.test_stage1 import fixture_fetch
        calls = []

        def fetch(path, params):
            calls.append(path)
            return fixture_fetch(path, params)
        rc = self._run(["--months", "3", "--only", "nrg_trade", "--only", "sh_trade", "--max-calls", "2"], fetch)
        self.assertEqual(rc, 0, "--max-calls에 닿아도 실패가 아님(내일 이어 받음)")
        self.assertEqual(sum("NrgTrade" in c for c in calls), 2)
        self.assertEqual(sum("SHTrade" in c for c in calls), 2, "종류마다 따로 셈")
