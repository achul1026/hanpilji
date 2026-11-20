"""상권 · 점포 · 생활인구 수집기(sbiz_store · seoul_trdar · seoul_living_pop)와 공통 모듈(seoul_api).

Open API 응답은 2026-11-20 서울 열린데이터광장 화면의 샘플 호출(sample 키, 서비스마다 1~2줄)로 받은 실제 줄
(fixtures/seoul_trdar_sample_real.json). 상가(상권)정보 열은 같은 날 받은 2026-09 파일의 머리줄과 첫 줄.
DB 시험은 HANPILJI_TEST_DSN(sql/30_trade_area.sql이 올라간 DB)이 있을 때만."""
import csv
import datetime
import io
import json
import os
import struct
import tempfile
import unittest
import urllib.parse
import zipfile
from pathlib import Path

from hanpilji.collectors import sbiz_store, seoul_api, seoul_living_pop, seoul_trdar

DSN = os.environ.get("HANPILJI_TEST_DSN")
SAMPLES = json.loads((Path(__file__).parent / "fixtures" / "seoul_trdar_sample_real.json").read_text(encoding="utf-8"))
TODAY = datetime.date(2026, 11, 20)
STORE_HEAD = list(sbiz_store.COLS) + ["시도코드", "시도명", "시군구명", "법정동명", "대지구분코드", "대지구분명",
                                      "지번본번지", "지번부번지", "도로명코드", "도로명", "건물본번지", "건물부번지",
                                      "구우편번호", "신우편번호", "동정보"]
STORE = {"상가업소번호": "MA010120220803318646", "상호명": "레드신낙성대역점", "지점명": "", "상권업종대분류코드": "I2",
         "상권업종대분류명": "음식", "상권업종중분류코드": "I210", "상권업종중분류명": "기타 간이", "상권업종소분류코드": "I21007",
         "상권업종소분류명": "김밥/만두/분식", "표준산업분류코드": "I56194", "표준산업분류명": "김밥 및 기타 간이 음식점업",
         "시군구코드": "11620", "행정동코드": "11620625", "행정동명": "인헌동", "법정동코드": "1162010100",
         "지번코드": "1162010100116580001", "지번주소": "서울특별시 관악구 봉천동 1658-1",
         "도로명주소": "서울특별시 관악구 남부순환로 1932", "건물관리번호": "1162010100116580001023928", "건물명": "",
         "층정보": "1", "호정보": "", "경도": "126.963551171247", "위도": "37.4768193424844"}


def fake_fetch(samples=SAMPLES, calls=None):
    """Open API 주소 → 샘플 줄로 만든 응답. 분기 · 날짜 조건이 있으면 그 줄만, 페이지는 1,000줄씩."""
    def fetch(url):
        parts = urllib.parse.urlparse(url).path.strip("/").split("/")
        service, start, end, args = parts[2], int(parts[3]), int(parts[4]), parts[5:]
        if calls is not None:
            calls.append((service, tuple(args)))
        rows = samples.get(service, [])
        if args:
            rows = [r for r in rows if str(r.get("STDR_YYQU_CD") or r.get("STDR_DE_ID")) == args[0]]
        if not rows:
            return json.dumps({"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}}).encode()
        page = rows[start - 1:end]
        return json.dumps({service: {"list_total_count": len(rows), "RESULT": {"CODE": "INFO-000"}, "row": page}},
                          ensure_ascii=False).encode()
    return fetch


class SeoulApi(unittest.TestCase):
    def test_parse_codes(self):
        self.assertEqual(seoul_api.parse("X", b'{"RESULT": {"CODE": "INFO-200"}}'), (0, []))
        with self.assertRaises(seoul_api.ApiError):
            seoul_api.parse("X", '{"RESULT": {"CODE": "INFO-100", "MESSAGE": "인증키가 유효하지 않습니다."}}')
        total, rows = seoul_api.parse("X", '{"X": {"list_total_count": 3, "RESULT": {"CODE": "INFO-000"}, "row": [{"a": 1}]}}')
        self.assertEqual((total, rows), (3, [{"a": 1}]))

    def test_url(self):
        self.assertEqual(seoul_api.api_url("k y", "Svc", 1, 1000, 20252),
                         "http://openapi.seoul.go.kr:8088/k%20y/json/Svc/1/1000/20252/")

    def test_paging_and_limit(self):
        rows = [{"STDR_YYQU_CD": "20251", "i": i} for i in range(2500)]
        calls = []
        c = seoul_api.Client("k", fetch=fake_fetch({"S": rows}, calls))
        self.assertEqual(len(c.rows("S")), 2500)
        self.assertEqual(c.calls, 3)
        with self.assertRaises(seoul_api.LimitReached):
            seoul_api.Client("k", fetch=fake_fetch({"S": rows}), max_calls=2).rows("S")
        with self.assertRaises(seoul_api.ApiError):
            seoul_api.Client("")

    def test_tm_roundtrip_and_known_point(self):
        # 서울시청(경위도) → 5181 → 경위도
        x, y = seoul_api.lonlat_to_tm(126.9779692, 37.5662952)
        lon, lat = seoul_api.tm_to_lonlat(x, y)
        self.assertAlmostEqual(lon, 126.9779692, places=7)
        self.assertAlmostEqual(lat, 37.5662952, places=7)
        # 상권 「황학동벼룩시장」 중심점(원본 5181) → 황학동(신당역 북쪽)
        lon, lat = seoul_api.tm_to_lonlat(201642, 452260)
        self.assertTrue(127.015 < lon < 127.023 and 37.566 < lat < 37.573, (lon, lat))

    def test_shp_dbf_and_wkt(self):
        outer = [(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]        # 시계 방향 = 바깥
        hole = [(2, 2), (4, 2), (4, 4), (2, 4), (2, 2)]             # 반시계 = 구멍
        shp, dbf = _shapefile([[outer, hole], []], [{"TRDAR_CD": "3110001"}, {"TRDAR_CD": "3110002"}])
        shapes = seoul_api.read_shp_polygons(shp)
        self.assertEqual(len(shapes), 2)
        self.assertEqual(shapes[1], [])
        self.assertEqual(seoul_api.read_dbf(dbf)[0]["TRDAR_CD"], "3110001")
        wkt = seoul_api.rings_to_wkt(shapes[0], dy=100000)
        self.assertTrue(wkt.startswith("MULTIPOLYGON(((0.000 100000.000,"), wkt)
        self.assertEqual(wkt.count("(("), 1, "구멍은 같은 다각형 안")
        self.assertIsNone(seoul_api.rings_to_wkt([]))

    def test_shapes_from_zip(self):
        outer = [(0, 0), (0, 10), (10, 10), (10, 0), (0, 0)]
        shp, dbf = _shapefile([[outer]], [{"ADSTRD_CD": "11110515"}])
        with tempfile.TemporaryDirectory() as d:
            p = f"{d}/a.zip"
            with zipfile.ZipFile(p, "w") as zf:
                zf.writestr("x.shp", shp)
                zf.writestr("x.dbf", dbf)
                zf.writestr("x.cpg", "UTF-8")
            got = seoul_trdar.shapes_from_zip(p, "ADSTRD_CD")
        self.assertEqual([c for c, _ in got], ["11110515"])

    def test_list_files(self):
        page = ('<form name="frmFile" id="frmFile"><input type="hidden" name="infSeq" value="3"/></form>'
                "<tr><td>서울시 상권분석서비스(영역-상권).zip</td><td>"
                "<a onclick=\"downloadFile('5')\">서울시 상권분석서비스(영역-상권).zip 다운로드</a></td></tr>")

        class R:
            def read(self):
                return page.encode()

        inf_seq, files = seoul_api.list_files("OA-15560", opener=lambda req, timeout: R())
        self.assertEqual((inf_seq, files), ("3", [("5", "서울시 상권분석서비스(영역-상권).zip")]))


def _shapefile(shapes, records):
    """시험용 다각형 SHP · DBF 바이트(주소 파일 SHX는 읽지 않아 안 만듦)."""
    body = b""
    for i, rings in enumerate(shapes, 1):
        if not rings:
            content = struct.pack("<i", 0)
        else:
            pts = [p for r in rings for p in r]
            parts, k = [], 0
            for r in rings:
                parts.append(k)
                k += len(r)
            content = struct.pack("<i4d2i", 5, 0, 0, 10, 10, len(rings), len(pts))
            content += struct.pack(f"<{len(parts)}i", *parts) + b"".join(struct.pack("<2d", *p) for p in pts)
        body += struct.pack(">2i", i, len(content) // 2) + content
    shp = struct.pack(">7i", 9994, 0, 0, 0, 0, 0, (100 + len(body)) // 2) + struct.pack("<2i4d4d", 1000, 5, 0, 0, 10, 10, 0, 0, 0, 0)
    shp += body
    name = list(records[0])[0]
    fields = struct.pack("<11sc4xB15x", name.encode(), b"C", 10)
    header_len = 32 + len(fields) + 1
    dbf = struct.pack("<B3BIHH20x", 3, 126, 11, 20, len(records), header_len, 11) + fields + b"\r"
    for r in records:
        dbf += b" " + r[name].encode().ljust(10)
    return shp, dbf + b"\x1a"


class TrdarParse(unittest.TestCase):
    def test_quarters(self):
        self.assertEqual(seoul_trdar.quarters(20253, TODAY), [20253, 20254, 20261, 20262, 20263, 20264])
        self.assertEqual(len(seoul_trdar.quarters(20211, TODAY)), 24)

    def test_stat_row(self):
        v = seoul_trdar.stat_row("trdar_sales", SAMPLES["VwsmTrdarSelngQq"][0])
        ds, yyqu, level, area, area_nm, ind, ind_nm, m = v
        self.assertEqual((ds, yyqu, level, area, ind), ("trdar_sales", 20222, "상권", "3111083", "CS100010"))
        self.assertEqual(area_nm, "GS강동자이아파트")
        self.assertEqual(m["THSMON_SELNG_AMT"], 503135509)
        self.assertNotIn("TRDAR_CD", m)
        v = seoul_trdar.stat_row("dong_change", SAMPLES["VwsmAdstrdIxQq"][0])
        self.assertEqual((v[2], v[3], v[5]), ("행정동", "11110630", ""))
        self.assertEqual(v[7]["TRDAR_CHNGE_IX_NM"], "정체")
        self.assertIsNone(seoul_trdar.stat_row("trdar_sales", {"STDR_YYQU_CD": "20251"}))

    def test_num(self):
        self.assertEqual(seoul_trdar._num("201642"), 201642)
        self.assertEqual(seoul_trdar._num("14304.9203"), 14304.9203)
        self.assertEqual(seoul_trdar._num(3.0), 3)
        self.assertEqual(seoul_trdar._num("HH"), "HH")

    def test_every_dataset_has_sample(self):
        for ds, (service, *_ ) in seoul_trdar.DATASETS.items():
            self.assertIn(service, SAMPLES, ds)
            self.assertIsNotNone(seoul_trdar.stat_row(ds, SAMPLES[service][0]), ds)


class LivingParse(unittest.TestCase):
    def test_normalize(self):
        day, tm, adm, tot, arr = seoul_living_pop.normalize(SAMPLES["SPOP_LOCAL_RESD_DONG"][0])
        self.assertEqual((day, tm, adm), (datetime.date(2026, 7, 31), 0, "11110515"))
        self.assertAlmostEqual(tot, 14304.9203)
        self.assertEqual(len(arr), 28)
        self.assertAlmostEqual(arr[0], 435.2258)
        row = dict(SAMPLES["SPOP_LOCAL_RESD_DONG"][0], MALE_F0T9_LVPOP_CO="*")
        self.assertEqual(seoul_living_pop.normalize(row)[4][0], 0.0, "3명 이하(*)는 0")
        self.assertIsNone(seoul_living_pop.normalize({"STDR_DE_ID": "2026"}))


class StoreParse(unittest.TestCase):
    def test_normalize(self):
        v, why = sbiz_store.normalize(STORE)
        self.assertIsNone(why)
        self.assertEqual((v["pnu"], v["adm_cd"], v["floor"]), ("1162010100116580001", "11620625", "1"))
        self.assertIsNone(v["branch"])
        self.assertAlmostEqual(v["lon"], 126.9635512)
        self.assertEqual(sbiz_store.normalize(dict(STORE, 시군구코드="41135"))[1], "서울이 아님")
        self.assertIsNone(sbiz_store.normalize(dict(STORE, 지번코드="12"))[0]["pnu"])
        self.assertIsNone(sbiz_store.normalize(dict(STORE, 경도="0", 위도="0"))[0]["lon"])

    def test_base_ym(self):
        self.assertEqual(sbiz_store.base_ym("소상공인시장진흥공단_상가(상권)정보_20260930"), "202609")
        self.assertEqual(sbiz_store.base_ym("소상공인시장진흥공단_상가(상권)정보_서울_202609.csv"), "202609")
        self.assertIsNone(sbiz_store.base_ym("store.zip"))

    def test_seoul_member_broken_name(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("#Uc18c_#Uacbd#Uae30_202609.csv", "x")
            zf.writestr("#Uc18c_#Uc11c#Uc6b8_202609.csv", "x")
        with zipfile.ZipFile(buf) as zf:
            self.assertIn("#Uc11c#Uc6b8", sbiz_store.seoul_member(zf).filename)


def _store_zip(path, stores):
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=STORE_HEAD)
    w.writeheader()
    for s in stores:
        w.writerow({k: s.get(k, "") for k in STORE_HEAD})
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("소상공인시장진흥공단_상가(상권)정보_서울_202609.csv", "﻿" + out.getvalue())


@unittest.skipUnless(DSN, "HANPILJI_TEST_DSN 없음")
class Db(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg = psycopg
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cfg = Path(cls.tmp.name) / "config.toml"
        cls.cfg.write_text(f'[db]\ndsn = "{DSN}"\n[storage]\nraw_dir = "{cls.tmp.name}/raw"\n'
                           '[law]\noc = "t"\nnames = []\n', encoding="utf-8")
        os.environ.pop("HANPILJI_DSN", None)
        with psycopg.connect(DSN) as c:
            c.execute("DELETE FROM core.fact_trdar_stat; DELETE FROM core.dim_trdar; DELETE FROM core.dim_adm_dong;"
                      "DELETE FROM core.fact_living_pop; DELETE FROM core.ref_store;"
                      "DO $$ BEGIN IF to_regclass('core.geo_trdar') IS NOT NULL THEN "
                      "DELETE FROM core.geo_trdar; DELETE FROM core.geo_adm_dong; END IF; END $$;"
                      "DELETE FROM ops.ingest_cursor WHERE source_id IN ('seoul_trdar', 'seoul_living_pop', 'sbiz_store')")

    def q(self, sql, *args):
        with self.psycopg.connect(DSN) as c:
            return c.execute(sql, args).fetchall()

    def test_1_trdar(self):
        calls = []
        client = seoul_api.Client("k", fetch=fake_fetch(calls=calls))
        args = ["--config", str(self.cfg), "--since", "20251"]
        # 경계 파일(PostGIS DB에서만 받음): 황학동벼룩시장 중심점 둘레 네모, 행정동도 같은 네모
        sq = [(201600, 452200), (201600, 452300), (201700, 452300), (201700, 452200), (201600, 452200)]

        def fetch(inf, inf_seq, seq, dest):
            key, code = ("TRDAR_CD", "3110055") if inf == "OA-15560" else ("ADSTRD_CD", "11140670")
            shp, dbf = _shapefile([[sq]], [{key: code}])
            with zipfile.ZipFile(dest, "w") as zf:
                zf.writestr("a.shp", shp)
                zf.writestr("a.dbf", dbf)

        self.assertEqual(seoul_trdar.main(args, client=client, today=TODAY,
                                          lister=lambda inf: ("3", [("1", "a.zip")]), fetch=fetch), 0)
        if self.q("SELECT to_regclass('core.geo_trdar') IS NOT NULL")[0][0]:
            self.assertEqual(self.q("SELECT trdar_cd FROM core.geo_trdar WHERE ST_Contains(geom, "
                                    "ST_Transform(ST_SetSRID(ST_MakePoint(127.0186, 37.5699), 4326), 5186))"), [("3110055",)])
        self.assertEqual(self.q("SELECT trdar_nm, adm_cd FROM core.dim_trdar"), [("황학동벼룩시장", "11140670")])
        lon, lat = self.q("SELECT lon, lat FROM core.dim_trdar")[0]
        self.assertTrue(127.01 < float(lon) < 127.03 and 37.56 < float(lat) < 37.58)
        self.assertEqual(self.q("SELECT count(*) FROM core.dim_adm_dong")[0][0], 2)
        # 분기로 부르는 지표는 2025년 1분기부터만, 통째로 받는 지표는 모든 분기
        got = dict(self.q("SELECT dataset, count(*) FROM core.fact_trdar_stat GROUP BY 1"))
        self.assertEqual(got.get("trdar_change"), 2)       # 20252 · 20254
        self.assertNotIn("trdar_sales", got)               # 샘플은 20222뿐
        self.assertEqual(got.get("dong_sales"), None)      # 20211뿐
        self.assertEqual(got.get("trdar_repop"), 2)        # 통째
        self.assertEqual(got.get("dong_change"), 1)
        # 다시 돌리면(--refresh-last 1) 받은 분기 중 옛 것(20252)은 건너뛰고, 가장 최근 받은 20254와 자료가 없던 분기는 다시 물음
        calls.clear()
        client = seoul_api.Client("k", fetch=fake_fetch(calls=calls))
        seoul_trdar.main(args + ["--only", "stat", "--dataset", "trdar_change", "--refresh-last", "1"], client=client, today=TODAY)
        self.assertEqual([a[0] for _, a in calls], ["20251", "20253", "20254", "20261", "20262", "20263", "20264"])
        self.assertEqual(self.q("SELECT count(*) FROM core.fact_trdar_stat WHERE dataset = 'trdar_change'")[0][0], 2)

    def test_2_trdar_trend_and_removed_rows(self):
        samples = dict(SAMPLES, VwsmTrdarSelngQq=[dict(r, STDR_YYQU_CD="20262") for r in SAMPLES["VwsmTrdarSelngQq"]],
                       VwsmTrdarStorQq=[dict(r, TRDAR_CD="3111083") for r in SAMPLES["VwsmTrdarStorQq"]])
        client = seoul_api.Client("k", fetch=fake_fetch(samples))
        args = ["--config", str(self.cfg), "--only", "stat", "--since", "20262",
                "--dataset", "trdar_sales", "--dataset", "trdar_store"]
        seoul_trdar.main(args, client=client, today=TODAY)
        rows = self.q("SELECT yyqu, sales_amt, store_cnt FROM mart.q_trdar_trend('3111083')")
        self.assertEqual(rows[0][0], 20262)
        self.assertGreater(rows[0][1], 0)
        self.assertGreater(rows[0][2], 0)
        # 같은 분기를 다시 받았는데 한 줄이 빠졌으면 그 줄은 지움
        samples["VwsmTrdarSelngQq"] = samples["VwsmTrdarSelngQq"][:1]
        seoul_trdar.main(args, client=seoul_api.Client("k", fetch=fake_fetch(samples)), today=TODAY)
        self.assertEqual(self.q("SELECT count(*) FROM core.fact_trdar_stat WHERE dataset = 'trdar_sales'")[0][0], 1)

    def test_3_living_pop(self):
        args = ["--config", str(self.cfg), "--since", "2026-07-30"]
        today = datetime.date(2026, 8, 3)
        seoul_living_pop.main(args, client=seoul_api.Client("k", fetch=fake_fetch()), today=today)
        self.assertEqual(self.q("SELECT stdr_de, count(*) FROM core.fact_living_pop GROUP BY 1"),
                         [(datetime.date(2026, 7, 31), 2)])
        self.assertEqual(len(self.q("SELECT by_sex_age FROM core.fact_living_pop")[0][0]), 28)
        calls = []
        seoul_living_pop.main(args, client=seoul_api.Client("k", fetch=fake_fetch(calls=calls)), today=today)
        self.assertEqual([a for _, a in calls], [("20260730",)], "받은 7/31은 건너뛰고, 아직 없던 7/30만 다시 물음")

    def test_4_store(self):
        with tempfile.TemporaryDirectory() as d:
            z = f"{d}/s.zip"
            other = dict(STORE, 상가업소번호="MA2", 상호명="둘째", 지번코드="")
            _store_zip(z, [STORE, other, dict(STORE, 시군구코드="41135", 상가업소번호="MA3")])
            args = ["--config", str(self.cfg), "--zip", z]
            self.assertEqual(sbiz_store.main(args, today=TODAY, min_rows=1), 0)
            self.assertEqual(self.q("SELECT store_id, pnu, base_ym FROM core.ref_store ORDER BY 1"),
                             [("MA010120220803318646", "1162010100116580001", "202609"), ("MA2", None, "202609")])
            self.assertEqual(self.q("SELECT name FROM mart.q_store_by_pnu('1162010100116580001')"), [("레드신낙성대역점",)])
            # 같은 파일은 넣지 않음, 새 파일에서 빠진 점포는 지움
            self.assertEqual(sbiz_store.main(args, today=TODAY, min_rows=1), 0)
            _store_zip(z, [STORE])
            sbiz_store.main(args, today=TODAY, min_rows=1)
            self.assertEqual(self.q("SELECT store_id FROM core.ref_store"), [("MA010120220803318646",)])
            # 너무 적으면(잘린 파일) 넣지도 지우지도 않음
            _store_zip(z, [other])
            self.assertEqual(sbiz_store.main(args, today=TODAY, min_rows=5), 1)
            self.assertEqual(self.q("SELECT store_id FROM core.ref_store"), [("MA010120220803318646",)])


if __name__ == "__main__":
    unittest.main()
