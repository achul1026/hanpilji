"""서울 확대 · 단독다가구 전월세 시험(DB 없이)."""
import unittest
from pathlib import Path

from hanpilji import regions
from hanpilji.collectors import rtms

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


if __name__ == "__main__":
    unittest.main()
