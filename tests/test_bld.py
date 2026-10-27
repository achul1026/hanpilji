"""건축물대장 수집기 시험. 인증키 없이 저장해 둔 응답으로 돎.

tests/fixtures/bld_*_real.xml 은 실제 응답(2026-10-27 수집). 표제부는 일원동 우성7차(일원동 615) 17동 중 2동,
지역지구는 같은 필지의 응답에서 겹치는 줄을 뺀 것, 부속지번은 도곡렉슬(도곡동 527) 응답 중 2줄만 남겨 줄였고
항목 내용은 그대로임. DB 시험은 HANPILJI_TEST_DSN이 있을 때만 돎.
"""
import os
import tempfile
import unittest
from pathlib import Path

import psycopg

from hanpilji import datagokr
from hanpilji.collectors import bld

DSN = os.environ.get("HANPILJI_TEST_DSN")
FIX = Path(__file__).parent / "fixtures"
PNU = "1168011400106150000"   # 일원동 615
FILES = {"getBrTitleInfo": "bld_title_real.xml", "getBrRecapTitleInfo": "bld_recap_real.xml",
         "getBrJijiguInfo": "bld_jijigu_real.xml", "getBrAtchJibunInfo": "bld_atch_real.xml"}


def items(name):
    return datagokr.items(datagokr.parse((FIX / name).read_bytes()))


def fixture_fetch(path, params):
    data = (FIX / FILES[path.split("/")[-1]]).read_bytes()
    return data, datagokr.parse(data)


class Parse(unittest.TestCase):
    def test_params_and_pnu(self):
        self.assertEqual(bld.params("1168011800105270000"),
                         {"sigunguCd": "11680", "bjdongCd": "11800", "platGbCd": "0", "bun": "0527", "ji": "0000"})
        self.assertEqual(bld.params("1168011800200040001")["platGbCd"], "1", "산 지번")
        title = items("bld_title_real.xml")
        self.assertEqual(bld.item_pnu(title[0]), PNU)
        atch = items("bld_atch_real.xml")
        self.assertEqual([bld.item_pnu(r, "atch") for r in atch], ["1168011800105300000", "1168011800105280000"])
        text_only = {"atchSigunguCd": "11680", "atchBjdongCd": "10400", "atchPlatGbCd": "", "atchBun": "0000",
                     "atchJi": "0000", "atchEtcJibunNm": "36-6"}   # 청담동 36 부속지번(실제 응답 모양)
        self.assertEqual(bld.item_pnu(text_only, "atch"), "1168010400100360006", "글로만 온 부속지번")
        self.assertIsNone(bld.item_pnu({**text_only, "atchEtcJibunNm": "BL 3"}, "atch"))

    def test_retry_empty_response(self):
        """빈 응답 · 05는 다시 부르고, 한도 오류는 바로 올림."""
        seq = [b"", fixture_fetch("x/getBrRecapTitleInfo", {})[0]]
        def flaky(path, params):
            data = seq.pop(0)
            return data, datagokr.parse(data)
        with tempfile.TemporaryDirectory() as d:
            c = bld.Caller(flaky, d)
            c.retry_wait = 0
            self.assertEqual(len(c.all_items("getBrRecapTitleInfo", PNU)), 1)
            self.assertEqual(c.calls, 2)
            def over(path, params):
                raise datagokr.ApiError("22 LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR")
            c = bld.Caller(over, d)
            with self.assertRaises(datagokr.ApiError):
                c.all_items("getBrTitleInfo", PNU)
            self.assertEqual(c.calls, 1)

    def test_building(self):
        b = bld.to_building(items("bld_title_real.xml")[0])
        self.assertEqual((b["main_use"], b["floors_above"], b["households"], b["is_main"]), ("공동주택", 14, 84, True))
        self.assertEqual(str(b["approval_date"]), "1987-12-11")
        self.assertIsNone(b["has_elevator"], "14층인데 승강기 0대로 적힘: 없음이 아니라 모름")
        self.assertIs(bld.has_elevator(0, 3), False)
        self.assertIs(bld.has_elevator(2, 17), True)
        self.assertIsNone(b["site_area"], "집합건물 동의 대지면적 0은 값 없음")
        self.assertIsNone(b["vl_rat"])

    def test_recap(self):
        r = bld.to_recap(items("bld_recap_real.xml"))
        self.assertEqual((r["households"], r["dong_count"]), (802, 17))
        self.assertIsNone(bld.to_recap([]))

    def test_zones_drop_broad_names(self):
        """[일반주거지역, 제3종일반주거지역, 도시지역, 지구단위계획구역] → 상위 이름 둘은 뺌. 「3종…」은 「제3종…」으로."""
        got = [z[1] for z in bld.pick_zones(items("bld_jijigu_real.xml"))]
        self.assertEqual(got, ["제3종일반주거지역", "지구단위계획구역"])
        got = [z[1] for z in bld.pick_zones(items("bld_jijigu_uq_real.xml"))]
        self.assertEqual(got, ["제3종일반주거지역", "과밀억제지역", "대공방어협조구역"], "UQA 코드로 온 응답")
        self.assertEqual([z[1] for z in bld.pick_zones([{"jijiguCdNm": "도시지역"}])], ["도시지역"], "하나뿐이면 남김")


@unittest.skipUnless(DSN, "HANPILJI_TEST_DSN 없음")
class Load(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = Path(cls.tmp.name) / "config.toml"
        cfg.write_text(f'[db]\ndsn = "{DSN}"\n[storage]\nraw_dir = "./raw"\n[pilot]\nsigungu = ["11680"]\n'
                       '[law]\noc = "test"\nnames = []\n', encoding="utf-8")
        cls.cfg = str(cfg)
        os.environ.pop("HANPILJI_DSN", None)
        cls.cleanup()
        with psycopg.connect(DSN) as c:
            c.execute("INSERT INTO core.dim_complex (complex_id, name, lawd_cd, jibun, pnu, source, asof) "
                      "VALUES ('t-bld', '시험우성7', '1168011400', '615', %s, 't', '2026-10-27')", (PNU,))

    @classmethod
    def tearDownClass(cls):
        cls.cleanup()   # 다른 시험(카드의 「수집 전」 확인)에 남지 않게

    @staticmethod
    def cleanup():
        with psycopg.connect(DSN) as c:
            src = bld.SOURCE_LABEL
            c.execute("DELETE FROM core.bridge_complex_building WHERE source = %s", (src,))
            c.execute("DELETE FROM core.bridge_parcel_building WHERE source = %s", (src,))
            c.execute("DELETE FROM core.bridge_parcel_zone WHERE source = %s", (bld.ZONE_LABEL,))
            c.execute("DELETE FROM core.dim_building WHERE source = %s", (src,))
            c.execute("DELETE FROM core.dim_parcel WHERE source = %s", (src,))
            c.execute("DELETE FROM core.dim_zone WHERE code_system = 'bld_jijigu'")
            c.execute("DELETE FROM core.dim_complex WHERE complex_id = 't-bld'")
            c.execute("DELETE FROM stg.bld_raw")
            c.execute("DELETE FROM ops.ingest_cursor WHERE source_id = %s", (bld.SOURCE,))
            c.execute("DELETE FROM ops.reject_estate WHERE source_id = %s", (bld.SOURCE,))

    def q(self, sql, *args):
        with psycopg.connect(DSN) as c:
            cur = c.execute(sql, args or None)
            return cur.fetchall() if cur.description else None

    def card(self):
        return self.q("SELECT land_parcel_count, land_area_kind, zones, bld_count, bld_households, bld_main_use, "
                      "bld_floors_above, missing FROM mart.q_property_card('11680', '시험우성7')")[0]

    def test_1_budget_then_resume(self):
        self.assertEqual(bld.main(["--config", self.cfg, "--pnu", PNU, "--max-calls", "2"], fetch=fixture_fetch), 0)
        self.assertEqual(self.q("SELECT count(*) FROM core.dim_building WHERE source = %s", bld.SOURCE_LABEL)[0][0], 0,
                         "필지 하나를 다 받기 전에 멈추면 아무것도 넣지 않음")
        calls = []
        def counting(path, params):
            calls.append(path)
            return fixture_fetch(path, params)
        self.assertEqual(bld.main(["--config", self.cfg, "--pnu", PNU], fetch=counting), 0)
        self.assertEqual(len(calls), 4, "기능 4개를 한 번씩")
        self.assertEqual(bld.main(["--config", self.cfg, "--pnu", PNU], fetch=counting), 0)
        self.assertEqual(len(calls), 4, "받은 필지는 건너뜀")

    def test_2_card_filled(self):
        bld.main(["--config", self.cfg, "--pnu", PNU, "--refresh"], fetch=fixture_fetch)
        parcels, kind, zones, n, hh, use, floors, missing = self.card()
        self.assertEqual(parcels, 3, "대표 필지 + 부속지번 2개")
        self.assertIsNone(kind, "이 단지 총괄표제부는 대지면적이 0이라 면적 없음")
        self.assertEqual(zones, "제3종일반주거지역, 지구단위계획구역")
        self.assertEqual((n, hh, use, floors), (2, 802, "공동주택", 14), "세대는 총괄표제부 값")
        self.assertEqual(missing, ["토지 지목", "토지 면적", "공시지가"])
        self.assertEqual(self.q("SELECT count(*) FROM ops.reject_estate WHERE source_id = %s", bld.SOURCE)[0][0], 0)

    def test_3_quota_stops(self):
        def over(path, params):
            raise datagokr.ApiError("22 LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR")
        self.q("INSERT INTO core.dim_complex (complex_id, name, lawd_cd, pnu, source, asof) "
               "VALUES ('t-bld2', '시험2', '1168011400', '1168011400106160000', 't', '2026-10-27')")
        try:
            self.assertEqual(bld.main(["--config", self.cfg, "--refresh", "--pnu", PNU,
                                       "--pnu", "1168011400106160000"], fetch=over), 1)
            tasks = self.q("SELECT status FROM ops.ingest_task WHERE source_id = %s AND run_id = "
                           "(SELECT max(run_id) FROM ops.ingest_run WHERE source_id = %s)", bld.SOURCE, bld.SOURCE)
            self.assertEqual([t[0] for t in tasks].count("failed"), 1, "한도 오류가 나면 다음 필지는 부르지 않음")
        finally:
            self.q("DELETE FROM core.dim_complex WHERE complex_id = 't-bld2'")


if __name__ == "__main__":
    unittest.main()
