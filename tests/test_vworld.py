"""V-World 수집기 시험. 인증키 없이 저장해 둔 응답으로 돎.

tests/fixtures/vworld_*_doc.json 은 실제 응답이 아니라 V-World 개발자센터 문서의 항목 이름으로 만든 것
(클라우드에서 api.vworld.kr이 막혀 아직 실제 응답을 받지 못함, 2026-10-28). 키가 생기면 실제 응답으로 바꿈.
DB 시험은 HANPILJI_TEST_DSN이 있을 때만 돎.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

import psycopg

from hanpilji.collectors import vworld

DSN = os.environ.get("HANPILJI_TEST_DSN")
FIX = Path(__file__).parent / "fixtures"
PNU = "1168011800105270000"   # 도곡동 527
FILES = {"getLandCharacteristics": "vworld_char_doc.json", "getLandUseAttr": "vworld_landuse_doc.json"}


def fixture_fetch(op, params):
    data = (FIX / FILES[op]).read_bytes()
    return (data, *vworld.parse(data))


class Parse(unittest.TestCase):
    def test_parse_and_errors(self):
        rows, total = vworld.parse((FIX / "vworld_char_doc.json").read_bytes())
        self.assertEqual((len(rows), total), (2, 2))
        rows, total = vworld.parse(b'{"landUses": {"field": {"pnu": "1"}, "totalCount": "1"}}')
        self.assertEqual((rows, total), ([{"pnu": "1"}], 1), "한 줄이면 list가 아니라 dict로 올 수 있음")
        self.assertEqual(vworld.parse(b'{"landUses": {"totalCount": "0"}}'), ([], 0))
        with self.assertRaises(vworld.ApiError) as cm:
            vworld.parse((FIX / "vworld_error_doc.json").read_bytes())
        self.assertTrue(vworld.is_fatal(cm.exception), "키 오류면 멈춤")
        with self.assertRaises(vworld.ApiError):
            vworld.parse(b"<html>blocked</html>")
        self.assertFalse(vworld.is_fatal(vworld.ApiError("SYSTEM_ERROR 일시 오류")))

    def test_char(self):
        rows, _ = vworld.parse((FIX / "vworld_char_doc.json").read_bytes())
        c = vworld.to_char(rows[-1])
        self.assertEqual((c["year"], c["category"], c["area"], c["price"], c["zone1"]), (2025, "대", 38926.4, 19600000, "15"))
        self.assertIsNone(vworld.to_char({"stdrYear": ""}))
        self.assertIsNone(vworld.to_char({"stdrYear": "2025", "pblntfPclnd": "0"})["price"], "0원은 값 없음")

    def test_zones(self):
        rows, _ = vworld.parse((FIX / "vworld_landuse_doc.json").read_bytes())
        self.assertEqual(vworld.pick_zones(rows), [
            ("UQA123", "제3종일반주거지역", "포함"), ("UQQ300", "지구단위계획구역", "포함"),
            ("UQS112", "광로1류", "접함"), ("UBA100", "과밀억제권역", "포함")],
            "상위 이름(도시지역 · 일반주거지역)은 빼고, 같은 코드는 가장 강한 관계 하나만")

    def test_url_keeps_key_out_of_errors(self):
        os.environ["VWORLD_KEY"] = "K"
        try:
            url = vworld.build_url("getLandUseAttr", {"pnu": PNU})
        finally:
            del os.environ["VWORLD_KEY"]
        self.assertTrue(url.startswith("https://api.vworld.kr/ned/data/getLandUseAttr?key=K&domain="))
        self.assertIn("format=json", url)
        with self.assertRaises(vworld.ApiError):
            vworld.api_key()


class FetchOnly(unittest.TestCase):
    def test_writes_one_file_per_parcel_and_resumes(self):
        with tempfile.TemporaryDirectory() as d:
            pnus = Path(d) / "pnus.txt"
            pnus.write_text(f"{PNU}\n1168011800100010000\n", encoding="utf-8")
            out = Path(d) / "out"
            self.assertEqual(vworld.main(["--fetch-only", str(pnus), "--out", str(out), "--max-calls", "3"],
                                         fetch=fixture_fetch), 0)
            got = json.loads((out / f"{PNU}.json").read_text(encoding="utf-8"))
            self.assertEqual({k: len(v) for k, v in got.items()}, {"getLandCharacteristics": 2, "getLandUseAttr": 7})
            self.assertFalse((out / "1168011800100010000.json").exists(), "한도에 닿은 필지는 파일을 남기지 않음")
            calls = []
            def counting(op, params):
                calls.append(params["pnu"])
                return fixture_fetch(op, params)
            vworld.main(["--fetch-only", str(pnus), "--out", str(out)], fetch=counting)
            self.assertEqual(calls, ["1168011800100010000"] * 2, "이미 받은 필지는 건너뜀")

    def test_fatal_error_stops(self):
        def bad(op, params):
            raise vworld.ApiError("INCORRECT_KEY 인증키 오류")
        with tempfile.TemporaryDirectory() as d:
            pnus = Path(d) / "pnus.txt"
            pnus.write_text(f"{PNU}\n1168011800100010000\n", encoding="utf-8")
            calls = []
            def counting(op, params):
                calls.append(op)
                return bad(op, params)
            self.assertEqual(vworld.main(["--fetch-only", str(pnus), "--out", f"{d}/out"], fetch=counting), 1)
            self.assertEqual(len(calls), 1)


@unittest.skipUnless(DSN, "HANPILJI_TEST_DSN 없음")
class Load(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = Path(cls.tmp.name) / "config.toml"
        cfg.write_text(f'[db]\ndsn = "{DSN}"\n[storage]\nraw_dir = "{cls.tmp.name}/raw"\n[pilot]\nsigungu = ["11680"]\n'
                       '[law]\noc = "test"\nnames = []\n', encoding="utf-8")
        cls.cfg = str(cfg)
        os.environ.pop("HANPILJI_DSN", None)
        cls.cleanup()
        with psycopg.connect(DSN) as c:
            c.execute("INSERT INTO core.dim_region (lawd_cd, source, asof) VALUES ('1168011800', 't', '2026-10-28') "
                      "ON CONFLICT DO NOTHING")
            c.execute("INSERT INTO core.dim_parcel (pnu, lawd_cd, jibun, source, asof) "
                      "VALUES (%s, '1168011800', '527', 't-vw', '2026-10-28')", (PNU,))
            c.execute("INSERT INTO core.dim_zone (zone_cd, code_system, zone_name, source, asof) "
                      "VALUES ('BLDt-1020', 'bld_jijigu', '일반주거지역', 't-vw', '2026-10-28')")
            c.execute("INSERT INTO core.bridge_parcel_zone (pnu, zone_cd, overlap, source, asof) "
                      "VALUES (%s, 'BLDt-1020', '포함', 't-vw', '2026-10-28')", (PNU,))
            c.execute("INSERT INTO core.dim_complex (complex_id, name, lawd_cd, jibun, pnu, source, asof) "
                      "VALUES ('t-vw', '시험렉슬', '1168011800', '527', %s, 't', '2026-10-28')", (PNU,))

    @classmethod
    def tearDownClass(cls):
        cls.cleanup()
        cls.tmp.cleanup()

    @staticmethod
    def cleanup():
        with psycopg.connect(DSN) as c:
            c.execute("DELETE FROM core.dim_complex WHERE complex_id = 't-vw'")
            c.execute("DELETE FROM core.bridge_parcel_zone WHERE pnu = %s", (PNU,))
            c.execute("DELETE FROM core.fact_land_price WHERE pnu = %s", (PNU,))
            c.execute("DELETE FROM core.fact_land_char WHERE pnu = %s", (PNU,))
            c.execute("DELETE FROM core.dim_parcel WHERE pnu = %s", (PNU,))
            c.execute("DELETE FROM core.dim_zone WHERE code_system = 'vworld_lup' OR zone_cd = 'BLDt-1020'")
            c.execute("DELETE FROM stg.vworld_raw")
            c.execute("DELETE FROM ops.ingest_cursor WHERE source_id = %s", (vworld.SOURCE,))

    def q(self, sql, *args):
        with psycopg.connect(DSN) as c:
            return c.execute(sql, args or None).fetchall()

    def test_1_budget(self):
        self.assertEqual(vworld.main(["--config", self.cfg, "--pnu", PNU, "--max-calls", "1"], fetch=fixture_fetch), 0)
        self.assertEqual(self.q("SELECT count(*) FROM core.fact_land_price WHERE pnu = %s", PNU)[0][0], 0,
                         "필지 하나를 다 받기 전에 멈추면 아무것도 넣지 않음")

    def test_2_load_and_card(self):
        self.assertEqual(vworld.main(["--config", self.cfg, "--pnu", PNU], fetch=fixture_fetch), 0)
        self.assertEqual(self.q("SELECT land_category, area_m2::float, source FROM core.dim_parcel WHERE pnu = %s", PNU),
                         [("대", 38926.4, vworld.LAND_LABEL)])
        self.assertEqual(self.q("SELECT base_year, price_per_m2 FROM core.fact_land_price WHERE pnu = %s ORDER BY 1", PNU),
                         [(2024, 18500000), (2025, 19600000)])
        self.assertEqual(self.q("SELECT zone_cd1, zone_cd2 FROM core.fact_land_char WHERE pnu = %s AND base_year = 2025",
                                PNU), [("15", None)], "「00」(지정되지 않음)은 코드표에 없어 비움")
        card = self.q("SELECT land_category, land_price_per_m2, land_price_year, zones, missing "
                      "FROM mart.q_property_card('11680', '시험렉슬')")[0]
        self.assertEqual(card[:3], ("대", 19600000, 2025))
        self.assertEqual(card[3], "과밀억제권역, 제3종일반주거지역, 지구단위계획구역",
                         "V-World가 있는 필지는 건축물대장의 「일반주거지역」을 쓰지 않고, 「접함」은 뺌")
        self.assertNotIn("공시지가", card[4])
        self.assertNotIn("토지 지목", card[4])
        raw = self.q("SELECT op, jsonb_array_length(row_data) FROM stg.vworld_raw WHERE pnu = %s ORDER BY 1", PNU)
        self.assertEqual(raw, [("getLandCharacteristics", 2), ("getLandUseAttr", 7)])

    def test_3_resume_skips_done(self):
        calls = []
        def counting(op, params):
            calls.append(op)
            return fixture_fetch(op, params)
        self.assertEqual(vworld.main(["--config", self.cfg, "--pnu", PNU], fetch=counting), 0)
        self.assertEqual(calls, [], "최근에 받은 필지는 다시 부르지 않음")

    def test_4_export_and_from_dir(self):
        with tempfile.TemporaryDirectory() as d:
            pnus = Path(d) / "pnus.txt"
            self.assertEqual(vworld.main(["--config", self.cfg, "--pnu", PNU, "--export-pnus", str(pnus)]), 0)
            self.assertEqual(pnus.read_text(encoding="utf-8").split(), [PNU])
            vworld.main(["--fetch-only", str(pnus), "--out", f"{d}/out"], fetch=fixture_fetch)
            with psycopg.connect(DSN) as c:
                c.execute("DELETE FROM core.fact_land_price WHERE pnu = %s", (PNU,))
            def never(op, params):
                raise AssertionError("--from-dir는 부르지 않음")
            self.assertEqual(vworld.main(["--config", self.cfg, "--pnu", PNU, "--from-dir", f"{d}/out"], fetch=never), 0)
            self.assertEqual(self.q("SELECT count(*) FROM core.fact_land_price WHERE pnu = %s", PNU)[0][0], 2,
                             "커서가 최근이어도 --from-dir는 다시 적재")
            self.assertEqual(vworld.main(["--config", self.cfg, "--pnu", "1168011800100010000",
                                          "--from-dir", f"{d}/out"], fetch=never), 0, "파일 없는 필지는 건너뜀")


if __name__ == "__main__":
    unittest.main()
