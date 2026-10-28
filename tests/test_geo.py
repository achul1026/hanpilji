"""지적도(연속지적도 · 건물 윤곽) 적재 · 지도 도구 시험. 인증키 없이 만든 응답으로 돎.

tests/fixtures/vworld_geom/ 은 V-World 2D 데이터 API 응답 모양(GeoJSON FeatureCollection)을 본떠 만든 것.
도곡동 527 · 528 · 529(도곡렉슬)와 이웃 531(도로), 두 칸에 걸쳐 두 번 온 528, 서초구 필지 · 건물(버려야 함).
DB 시험은 HANPILJI_TEST_DSN(PostGIS · 15_geo.sql이 올라간 DB)이 있을 때만 돎.
"""
import importlib.util
import json
import os
import unittest
from pathlib import Path

from hanpilji.collectors import vworld_geom
from hanpilji.gpt import blocks, tools, web

DSN = os.environ.get("HANPILJI_TEST_DSN")
FIX = Path(__file__).parent / "fixtures" / "vworld_geom"
ROOT = Path(__file__).resolve().parent.parent
PNUS = ["1168011800105270000", "1168011800105280000", "1168011800105290000"]


class Parse(unittest.TestCase):
    def test_split_jibun(self):
        self.assertEqual(vworld_geom.split_jibun("527대"), ("527", "대"))
        self.assertEqual(vworld_geom.split_jibun("산12-3임"), ("산12-3", "임"))
        self.assertEqual(vworld_geom.split_jibun("1027"), ("1027", None))
        self.assertEqual(vworld_geom.split_jibun(""), (None, None))

    def test_collect_dedupes_and_keeps_district(self):
        parcels, buildings = vworld_geom.collect(vworld_geom.read_files(FIX), "11680")
        self.assertEqual(sorted(parcels), sorted(PNUS + ["1168011800105310000"]),
                         "서초구(11650) 필지는 버리고, 두 칸에 걸친 528은 하나만, 필지번호로 받은 529도 넣음")
        self.assertEqual(parcels[PNUS[0]][:2], ("527", "대"))
        b = buildings["1168011800105270000000001"]
        self.assertEqual((b["name"], b["above"], b["below"]), ("도곡렉슬 101동", 25, 3), "속성 이름은 대소문자 무시")

    def test_fetch_script_tiles_cover_gangnam(self):
        spec = importlib.util.spec_from_file_location("fetch_geom", ROOT / "scripts" / "vworld_fetch_geom_gangnam.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        grid = [box for _, box in m.tiles()]
        self.assertEqual(len(m.PNUS), len(set(m.PNUS)))
        self.assertLessEqual(min(b[0] for b in grid), 127.016)
        self.assertGreaterEqual(max(b[2] for b in grid), 127.124)
        self.assertGreaterEqual(max(b[3] for b in grid), 37.535)
        ok = b'{"response": {"status": "OK", "record": {"total": "2"}, "result": {"featureCollection": {"features": [{"id": 1}]}}}}'
        self.assertEqual(m.parse(ok), ([{"id": 1}], 2))
        self.assertEqual(m.parse(b'{"response": {"status": "NOT_FOUND"}}'), ([], 0))
        with self.assertRaises(m.ApiError) as cm:
            m.parse(b'{"response": {"status": "ERROR", "error": {"code": "INCORRECT_KEY", "text": "x"}}}')
        self.assertTrue(m.fatal(cm.exception))


def _out():
    sq = {"type": "MultiPolygon", "coordinates": [[[[127.0, 37.0], [127.001, 37.0], [127.001, 37.001], [127.0, 37.0]]]]}
    rows = [{"layer": "대상 필지", "pnu": PNUS[0], "label": "도곡렉슬", "jibun": "527", "jimok": "대", "area_m2": 1.5,
             "floors_above": None, "lon": 127.0, "lat": 37.0, "geojson": json.dumps(sq), "screen_only": False},
            {"layer": "건물", "pnu": PNUS[0], "label": "101동", "jibun": None, "jimok": None, "area_m2": 1.0,
             "floors_above": 25, "lon": 127.0, "lat": 37.0, "geojson": json.dumps(sq), "screen_only": True}]
    return {"tool": "q_parcel_map", "args": {"query": "도곡렉슬"}, "shown": 1, "row_total": 1, "ref": 1,
            "sources": ["V-World 연속지적도"], "asof": "2026-10-28", "rows": rows}


class Screen(unittest.TestCase):
    def test_model_never_sees_geometry(self):
        m = tools.for_model(_out())
        self.assertEqual(len(m["rows"]), 1)
        self.assertNotIn("geojson", m["rows"][0])
        self.assertEqual(m["screen_rows"], 1)
        other = {"tool": "q_deal_list", "rows": [{"amount": 1}]}
        self.assertIs(tools.for_model(other), other, "지도 아닌 도구는 그대로")

    def test_map_block(self):
        b = blocks.block(_out())
        self.assertEqual((b["kind"], b["title"]), ("map", "도곡렉슬 지적도 · 건물 윤곽"))
        self.assertEqual([f["layer"] for f in b["features"]], ["대상 필지", "건물"])
        self.assertEqual(b["features"][0]["geometry"]["type"], "MultiPolygon")
        self.assertIn("참고용", b["note"])

    def test_map_config(self):
        self.assertEqual(web.map_config({}), {"tiles": None})
        self.assertEqual(web.map_config({"HANPILJI_MAP_TILES": "vworld"}), {"tiles": None}, "V-World 키가 없으면 배경 없음")
        self.assertEqual(web.map_config({"HANPILJI_MAP_TILES": "none", "HANPILJI_VWORLD_MAP_KEY": "K"}), {"tiles": None})
        t = web.map_config({"HANPILJI_VWORLD_MAP_KEY": "K"})["tiles"]
        self.assertTrue(t["url"].startswith("https://api.vworld.kr/req/wmts/1.0.0/K/{style}/"))
        self.assertEqual(web.map_config({"HANPILJI_MAP_TILES": "OSM"})["tiles"]["max_zoom"], 19)


@unittest.skipUnless(DSN, "HANPILJI_TEST_DSN 없음")
class Load(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg = psycopg
        with psycopg.connect(DSN) as c:
            c.execute("INSERT INTO core.dim_region (lawd_cd, sido, sigungu, emd, source, asof) "
                      "VALUES ('1168011800', '서울특별시', '강남구', '도곡동', 't', '2026-10-28') ON CONFLICT DO NOTHING")
            for p in PNUS:
                c.execute("INSERT INTO core.dim_parcel (pnu, lawd_cd, jibun, source, asof) VALUES (%s, '1168011800', %s, 't-geo', "
                          "'2026-10-28') ON CONFLICT DO NOTHING", (p, str(int(p[11:15]))))
            c.execute("INSERT INTO core.dim_complex (complex_id, name, lawd_cd, jibun, pnu, source, asof) "
                      "VALUES ('t-geo', '시험지도렉슬', '1168011800', '527', %s, 't', '2026-10-28') ON CONFLICT DO NOTHING", (PNUS[0],))
        cfg = Path(__file__).parent / "_geo_config.toml"
        cfg.write_text(f'[db]\ndsn = "{DSN}"\n[storage]\nraw_dir = "./raw"\n[law]\noc = "t"\nnames = []\n', encoding="utf-8")
        cls.cfg = cfg
        os.environ.pop("HANPILJI_DSN", None)
        assert vworld_geom.main(["--config", str(cfg), "--from-dir", str(FIX)]) == 0

    @classmethod
    def tearDownClass(cls):
        cls.cfg.unlink()
        with cls.psycopg.connect(DSN) as c:
            c.execute("DELETE FROM core.geo_building WHERE source = %s", (vworld_geom.BUILDING_LABEL,))
            c.execute("DELETE FROM core.geo_parcel WHERE source = %s", (vworld_geom.PARCEL_LABEL,))
            c.execute("DELETE FROM core.dim_complex WHERE complex_id = 't-geo'")
            c.execute("DELETE FROM core.dim_parcel WHERE source = 't-geo'")

    def q(self, sql, *args):
        with self.psycopg.connect(DSN) as c:
            return c.execute(sql, args or None).fetchall()

    def test_loaded(self):
        self.assertEqual(self.q("SELECT count(*), min(ST_SRID(geom)) FROM core.geo_parcel"), [(4, 5186)])
        self.assertEqual(self.q("SELECT bld_id, pnu FROM core.geo_building"),
                         [("1168011800105270000000001", PNUS[0])], "서초구 건물은 버리고, 가장 많이 겹치는 필지에 이음")
        area = self.q("SELECT ST_Area(geom) FROM core.geo_parcel WHERE pnu = %s", PNUS[0])[0][0]
        self.assertAlmostEqual(area, 17667, delta=200, msg="경위도 0.0015° x 0.0012° ≈ 132m x 133m")

    def test_reload_is_idempotent(self):
        self.assertEqual(vworld_geom.main(["--config", str(self.cfg), "--from-dir", str(FIX)]), 0)
        self.assertEqual(self.q("SELECT count(*) FROM core.geo_parcel")[0][0], 4)

    def test_map_tool(self):
        with self.psycopg.connect(DSN) as conn:
            out = tools.run(conn, "q_parcel_map", {"query": "시험지도렉슬"})
            self.assertEqual((out["row_total"], out["shown"]), (1, 1), "단지의 필지는 dim_complex.pnu 하나")
            layers = [r["layer"] for r in out["rows"]]
            self.assertEqual(layers[0], "대상 필지")
            self.assertIn("이웃 필지", layers)
            self.assertIn("건물", layers)
            self.assertEqual(json.loads(out["rows"][0]["geojson"])["type"], "MultiPolygon")
            self.assertEqual(out["rows"][0]["label"], "시험지도렉슬")
            by_pnu = tools.run(conn, "q_parcel_map", {"query": "1168011800105310000"})
            self.assertEqual((by_pnu["rows"][0]["label"], by_pnu["rows"][0]["jimok"]), ("도곡동 531", "도"))
            self.assertEqual(tools.run(conn, "q_parcel_map", {"query": "없는단지"})["row_total"], 0)
            self.assertEqual(sorted(out["sources"]), [vworld_geom.BUILDING_LABEL, vworld_geom.PARCEL_LABEL])


if __name__ == "__main__":
    unittest.main()
