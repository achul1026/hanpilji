"""주변 정보(학교 · 지하철역) 수집기와 q_nearby.

표준데이터 응답 모양은 2026-11-11 포털(standard.json)에서 받은 실제 줄을 줄여 씀.
DB 시험은 HANPILJI_TEST_DSN(PostGIS · 15_geo.sql · 22_poi.sql이 올라간 DB)이 있을 때만."""
import datetime
import os
import tempfile
import unittest
from pathlib import Path

from hanpilji.collectors import poi

DSN = os.environ.get("HANPILJI_TEST_DSN")
TODAY = datetime.date(2026, 11, 11)
SCHOOLS = [
    {"SCHOOL_ID": "B1", "SCHOOL_NM": "시험초등학교", "SCHOOL_SE": "초등학교", "FOND_TYPE": "공립", "BNHH_SE": "본교",
     "OPER_STTUS": "운영", "RDNMADR": "서울특별시 강남구 시험로 1", "LATITUDE": "37.4920", "LONGITUDE": "127.0560",
     "REFERENCE_DATE": "2026-10-01"},
    {"SCHOOL_ID": "B2", "SCHOOL_NM": "문닫은중학교", "SCHOOL_SE": "중학교", "OPER_STTUS": "폐교",
     "LATITUDE": "37.4921", "LONGITUDE": "127.0561", "REFERENCE_DATE": "2026-10-01"},
    {"SCHOOL_ID": "B3", "SCHOOL_NM": "먼고등학교", "SCHOOL_SE": "고등학교", "OPER_STTUS": "운영",
     "LATITUDE": "35.1992", "LONGITUDE": "126.8970", "REFERENCE_DATE": "2026-10-01"},
]
STATIONS = [
    {"STATN_NM": "시험역", "ROUTE_NM": "서울 도시철도 3호선", "LATITUDE": "37.4935", "LONGITUDE": "127.0550",
     "RDNMADR": "서울특별시 강남구 시험로 9", "REFERENCE_DATE": "2021-04-09"},
    {"STATN_NM": "시험역", "ROUTE_NM": "서울 도시철도 7호선", "LATITUDE": "37.4936", "LONGITUDE": "127.0551",
     "REFERENCE_DATE": "2021-06-30"},
    {"STATN_NM": "부산역", "ROUTE_NM": "부산 1호선", "LATITUDE": "35.1150", "LONGITUDE": "129.0410"},
]


class Parse(unittest.TestCase):
    def test_schools(self):
        rows = poi.schools(SCHOOLS, TODAY)
        self.assertEqual([r["name"] for r in rows], ["시험초등학교"], "폐교 · 서울 둘레 밖은 뺌")
        self.assertEqual((rows[0]["kind"], rows[0]["detail"], rows[0]["asof"]), ("초등학교", "공립 · 본교", "2026-10-01"))

    def test_stations_merge_transfer(self):
        rows = poi.stations(STATIONS, TODAY)
        self.assertEqual(len(rows), 1, "같은 이름 · 같은 자리의 노선은 한 줄, 부산은 뺌")
        self.assertEqual(rows[0]["detail"], "서울 도시철도 3호선 · 서울 도시철도 7호선")
        self.assertEqual(rows[0]["asof"], "2021-06-30")


class FakePortal:
    def rows(self, pk):
        return SCHOOLS if pk == poi.SETS["school"]["pk"] else STATIONS


@unittest.skipUnless(DSN, "HANPILJI_TEST_DSN 없음")
class Nearby(unittest.TestCase):
    PNU = "1168011800199990000"

    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg = psycopg
        os.environ.pop("HANPILJI_DSN", None)
        with psycopg.connect(DSN) as c:
            c.execute("INSERT INTO core.dim_region (lawd_cd, sido, sigungu, emd, source, asof) "
                      "VALUES ('1168011800', '서울특별시', '강남구', '도곡동', 't', '2026-10-28') ON CONFLICT DO NOTHING")
            c.execute("INSERT INTO core.dim_parcel (pnu, lawd_cd, jibun, source, asof) VALUES (%s, '1168011800', '9999', 't-poi', "
                      "'2026-11-11') ON CONFLICT DO NOTHING", (cls.PNU,))
            c.execute("INSERT INTO core.dim_complex (complex_id, name, lawd_cd, jibun, pnu, source, asof) "
                      "VALUES ('t-poi', '시험주변렉슬', '1168011800', '9999', %s, 't', '2026-11-11') ON CONFLICT DO NOTHING",
                      (cls.PNU,))
            c.execute("SET search_path = public, extensions")
            c.execute("INSERT INTO core.geo_parcel (pnu, jibun, jimok, geom, source, asof) VALUES (%s, '9999', '대', "
                      "ST_Multi(ST_Transform(ST_MakeEnvelope(127.054, 37.490, 127.0555, 37.4912, 4326), 5186)), 't-poi', '2026-11-11') "
                      "ON CONFLICT (pnu) DO NOTHING", (cls.PNU,))
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = Path(cls.tmp.name) / "c.toml"
        cfg.write_text(f'[db]\ndsn = "{DSN}"\n[storage]\nraw_dir = "./raw"\n[law]\noc = "t"\nnames = []\n', encoding="utf-8")
        cls.cfg = str(cfg)

    @classmethod
    def tearDownClass(cls):
        with cls.psycopg.connect(DSN) as c:
            c.execute("DELETE FROM core.ref_poi WHERE source IN (%s, %s)",
                      (poi.SETS["school"]["label"], poi.SETS["station"]["label"]))
            c.execute("DELETE FROM core.geo_parcel WHERE source = 't-poi'")
            c.execute("DELETE FROM core.dim_complex WHERE complex_id = 't-poi'")
            c.execute("DELETE FROM core.dim_parcel WHERE source = 't-poi'")
        cls.tmp.cleanup()

    def test_load_and_nearby(self):
        for _ in range(2):   # 다시 돌려도 같음
            self.assertEqual(poi.main(["--config", self.cfg], portal=FakePortal(), today=TODAY), 0)
        with self.psycopg.connect(DSN) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM core.ref_poi").fetchone()[0], 2)
            rows = c.execute("SELECT kind, place_name, distance_m, walk_min, row_total FROM mart.q_nearby('11680', '시험주변렉슬')").fetchall()
        self.assertEqual([r[:2] for r in rows], [("지하철역", "시험역"), ("초등학교", "시험초등학교")], "역이 먼저")
        self.assertTrue(all(0 < r[2] < 400 for r in rows), rows)
        self.assertEqual(rows[0][3], -(-rows[0][2] // 67))
        with self.psycopg.connect(DSN) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM mart.q_nearby('11680', '시험주변렉슬', 200)").fetchone()[0], 1,
                             "반경 200m면 역(약 250m)은 빠짐")
            self.assertEqual(c.execute("SELECT count(*) FROM mart.q_nearby('11680', '없는단지')").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
