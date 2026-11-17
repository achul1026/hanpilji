"""건축HUB 대용량 파일 적재 시험. 줄은 2026-09월 파일 첫 줄들(서버에서 본 것)의 모양 그대로이고, 서울 필지 줄은
열 위치를 지키며 값만 바꿔 만듦. DB 시험은 HANPILJI_TEST_DSN이 있을 때만 돎."""
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

import psycopg

from hanpilji.collectors import bld, bld_file

DSN = os.environ.get("HANPILJI_TEST_DSN")

# 실제 파일 첫 줄(대구 · 서울 종로구). 열 수 확인용
REAL = {
    "mart_djy_03": "1002120998|1|일반|2|일반건축물|서울특별시 종로구 누상동 59-45번지|서울특별시 종로구 필운대로12가길 35 (누상동)|조은어린이집|11110|17500|0|0059|0045||||0|111104100379|17501|0|35|||0|주건축물|172.3|0|0|285.06|0|0|21|철근콘크리트구조|철근콘크리트조|11000|노유자시설|어린이집|10|(철근)콘크리트|슬라브|0|0|0|2|1|0|0|0|0|285.06|0|0|0|0|0|0|0|0|19901122||19910331||||||0||0|0||0||0|20220813|0|",
    "mart_djy_02": "1050115|1|일반|1|총괄표제부|1|신대장|대구광역시 수성구 수성동4가 1125-14번지|대구광역시 수성구 들안로77길 27 (수성동4가)||27260|10600|0|1125|0014||||0|272604238182|10602|0|27|0|0|78.73|0|202.1|202.1|0|01000|단독주택|주택, 소매점|0|3|2|1|1|0|0|0|0|0|0|0|0|0|||||||||0||0|0||0||0|20221126",
    "mart_djy_10": "1002133|서울특별시 종로구 구기동 266번지|서울특별시 종로구 홍지문길 69 (구기동)|11110|18200|0|0266|0000||||1|용도지역코드|1020|일반주거지역|1|일반주거지역|20220813",
    "mart_djy_05": "10591100306356|1|일반|2|일반건축물|인천광역시 부평구 구산동 87-3번지||창고(컨테이너)21|28237|10900|0|0087|0003||||||0|0|0|1|일반|28237|10900|0|0261|0000|||||20220625",
}

# 시험 필지: 종로구 누상동 59-45(단독 2동 + 총괄), 강남구 도곡동 400(빌라 단지, API로 받은 적 없음),
# 강남구 일원동 999(API로 받은 필지라 건너뜀), 블록 대장 1줄, 대구 1줄(시군구 밖)
P1, P2, P3 = "1111017500100590045", "1168011800104000000", "1168011400109990000"
TITLE = [
    REAL["mart_djy_03"].replace("1002120998|", "T-1|"),
    REAL["mart_djy_03"].replace("1002120998|", "T-2|").replace("|0|주건축물|", "|1|부속건축물|"),
    REAL["mart_djy_03"].replace("1002120998|", "T-3|").replace("|11110|17500|0|0059|0045|", "|11680|11800|0|0400|0000|")
        .replace("|11000|노유자시설|어린이집|", "|02000|공동주택|다세대주택|").replace("조은어린이집", "망원빌라"),
    REAL["mart_djy_03"].replace("1002120998|", "T-4|").replace("|11110|17500|0|0059|0045|", "|11680|11400|0|0999|0000|"),
    REAL["mart_djy_03"].replace("1002120998|", "T-5|").replace("|11110|17500|0|0059|0045|", "|11680|11400|1|0000|0000|"),
    REAL["mart_djy_03"].replace("1002120998|", "T-6|").replace("|11110|17500|0|0059|0045|", "|27260|10600|0|1125|0014|"),
]
RECAP = [REAL["mart_djy_02"].replace("1050115|", "R-1|").replace("|27260|10600|0|1125|0014|", "|11110|17500|0|0059|0045|")]
ZONE = [REAL["mart_djy_10"].replace("1002133|", "T-1|").replace("|11110|18200|0|0266|0000|", "|11110|17500|0|0059|0045|")
        .replace("|1020|일반주거지역|1|일반주거지역|", "|UQA122|제2종일반주거지역|1||")]
ATCH = [REAL["mart_djy_05"].replace("10591100306356|", "T-1|").replace("|28237|10900|0|0087|0003|", "|11110|17500|0|0059|0045|")
        .replace("|28237|10900|0|0261|0000|", "|11110|17500|0|0059|0046|")]


def make_zips(folder):
    for inner, lines in (("mart_djy_03", TITLE), ("mart_djy_02", RECAP), ("mart_djy_10", ZONE), ("mart_djy_05", ATCH)):
        with zipfile.ZipFile(Path(folder) / f"{inner}.zip", "w") as z:
            z.writestr(f"{inner}.txt", "\r\n".join(lines) + "\r\n")


class Parse(unittest.TestCase):
    def test_columns_match_real_lines(self):
        for name, line in REAL.items():
            row = bld_file.to_row(name, line)
            self.assertIsNotNone(row, name)
            self.assertTrue(row["sigunguCd"].isdigit() and len(row["sigunguCd"]) == 5, name)
        t = bld_file.to_row("mart_djy_03", REAL["mart_djy_03"])
        b = bld.to_building(t)
        self.assertEqual((b["pnu"], b["main_use"], b["floors_above"], b["floors_below"], str(b["approval_date"]),
                          b["total_area"], b["site_area"]),
                         (P1, "노유자시설", 2, 1, "1991-03-31", 285.06, 172.3))
        r = bld_file.to_row("mart_djy_02", REAL["mart_djy_02"])
        self.assertEqual((r["totArea"], r["mainPurpsCdNm"], r["fmlyCnt"], r["mainBldCnt"], r["crtnDay"]),
                         ("202.1", "단독주택", "3", "2", "20221126"))
        a = bld_file.to_row("mart_djy_05", REAL["mart_djy_05"])
        self.assertEqual(bld.item_pnu(a, "atch"), "2823710900102610000")
        z = bld_file.to_row("mart_djy_10", REAL["mart_djy_10"])
        self.assertEqual(bld.pick_zones([z])[0][1], "일반주거지역")
        self.assertIsNone(bld_file.to_row("mart_djy_10", "1|2|3"), "열 수가 다르면 None")

    def test_read_filters_and_groups(self):
        with tempfile.TemporaryDirectory() as d:
            make_zips(d)
            files = bld_file.find_files(d)
            self.assertEqual(set(files), set(bld.OPS))
            groups, block, bad = bld_file.read(*files["getBrTitleInfo"][:2], ["11110", "11680"])
            self.assertEqual({k: len(v) for k, v in groups.items()}, {P1: 2, P2: 1, P3: 1})
            self.assertEqual((block, bad), (1, 0), "블록 대장은 세기만, 대구 줄은 시군구 밖")


@unittest.skipUnless(DSN, "HANPILJI_TEST_DSN 없음")
class Load(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = Path(cls.tmp.name) / "config.toml"
        cfg.write_text(f'[db]\ndsn = "{DSN}"\n[storage]\nraw_dir = "./raw"\n[pilot]\nsigungu = ["11110", "11680"]\n'
                       '[law]\noc = "test"\nnames = []\n', encoding="utf-8")
        cls.cfg = str(cfg)
        cls.hub = Path(cls.tmp.name) / "hub"
        cls.hub.mkdir()
        make_zips(cls.hub)
        os.environ.pop("HANPILJI_DSN", None)
        cls.cleanup()
        with psycopg.connect(DSN) as c:
            c.execute("INSERT INTO core.dim_complex (complex_id, name, lawd_cd, jibun, pnu, kind, source, asof) "
                      "VALUES ('t-bf', '망원빌라', '1168011800', '400', %s, '연립다세대', 't', '2026-11-17')", (P2,))
            c.execute("INSERT INTO ops.ingest_cursor (source_id, cursor_key, cursor_value) VALUES (%s, %s, '2099-01-01')",
                      (bld.SOURCE, P3))
            run = c.execute("INSERT INTO ops.ingest_run (source_id, status) VALUES (%s, 'done') RETURNING run_id",
                            (bld.SOURCE,)).fetchone()[0]
            c.execute("INSERT INTO stg.bld_raw (run_id, pnu, op, row_data) VALUES (%s, %s, 'getBrTitleInfo', %s)",
                      (run, P3, '[{"mgmBldrgstPk": "api"}]'))

    @classmethod
    def tearDownClass(cls):
        cls.cleanup()
        cls.tmp.cleanup()

    @staticmethod
    def cleanup():
        with psycopg.connect(DSN) as c:
            pk = ["T-1", "T-2", "T-3", "T-4", "T-5", "T-6"]
            c.execute("DELETE FROM core.bridge_complex_building WHERE bld_pk = ANY(%s)", (pk,))
            c.execute("DELETE FROM core.bridge_parcel_building WHERE bld_pk = ANY(%s)", (pk,))
            c.execute("DELETE FROM core.dim_building WHERE bld_pk = ANY(%s)", (pk,))
            c.execute("DELETE FROM core.bridge_parcel_zone WHERE pnu = %s", (P1,))
            c.execute("DELETE FROM core.bridge_parcel_building WHERE pnu = ANY(%s)", ([P1, P2, P1[:-1] + "6"],))
            c.execute("DELETE FROM core.dim_parcel WHERE pnu = ANY(%s) AND source = %s",
                      ([P1, P2, P1[:-1] + "6"], bld.SOURCE_LABEL))
            c.execute("DELETE FROM core.dim_region r WHERE lawd_cd = '1111017500' AND source = %s AND NOT EXISTS "
                      "(SELECT 1 FROM core.dim_parcel p WHERE p.lawd_cd = r.lawd_cd)", (bld.SOURCE_LABEL,))
            c.execute("DELETE FROM core.dim_complex WHERE complex_id = 't-bf'")
            c.execute("DELETE FROM ops.ingest_cursor WHERE source_id = %s AND cursor_key = %s", (bld.SOURCE, P3))
            c.execute("DELETE FROM stg.bld_raw WHERE pnu = %s", (P3,))
            c.execute("DELETE FROM ops.reject_estate WHERE source_id = %s", (bld_file.SOURCE,))
            c.execute("DELETE FROM ops.ingest_cursor WHERE source_id = %s", (bld_file.SOURCE,))

    def q(self, sql, *args):
        with psycopg.connect(DSN) as c:
            return c.execute(sql, args or None).fetchall()

    def test_load(self):
        self.assertEqual(bld_file.main(["--config", self.cfg, "--dir", str(self.hub), "--dry-run"]), 0)
        self.assertEqual(self.q("SELECT count(*) FROM core.dim_building WHERE bld_pk LIKE 'T-%%'")[0][0], 0,
                         "--dry-run은 쓰지 않음")
        self.assertEqual(bld_file.main(["--config", self.cfg, "--dir", str(self.hub)]), 0)
        self.assertEqual(self.q("SELECT bld_pk, pnu, is_main FROM core.dim_building WHERE bld_pk LIKE 'T-%%' ORDER BY 1"),
                         [("T-1", P1, True), ("T-2", P1, False), ("T-3", P2, True)],
                         "API로 받은 필지(T-4) · 블록(T-5) · 시군구 밖(T-6)은 빼고")
        self.assertEqual(self.q("SELECT complex_id FROM core.bridge_complex_building WHERE bld_pk = 'T-3'"), [("t-bf",)],
                         "빌라 단지에 건물이 붙음")
        self.assertEqual(self.q("SELECT pnu, is_main_lot FROM core.bridge_parcel_building WHERE bld_pk = 'T-1' ORDER BY 1"),
                         [(P1, True), ("1111017500100590046", False)], "부속지번")
        self.assertEqual(self.q("SELECT z.zone_name FROM core.bridge_parcel_zone b JOIN core.dim_zone z USING (zone_cd) "
                                "WHERE b.pnu = %s", P1), [("제2종일반주거지역",)])
        run = self.q("SELECT status, rows_loaded FROM ops.ingest_run WHERE source_id = %s ORDER BY run_id DESC LIMIT 1",
                     bld_file.SOURCE)
        self.assertEqual(run, [("done", 3)])
        self.assertEqual(bld_file.main(["--config", self.cfg, "--dir", str(self.hub)]), 0, "다시 돌려도 같은 값")
        self.assertEqual(self.q("SELECT count(*) FROM core.dim_building WHERE bld_pk LIKE 'T-%%'")[0][0], 3)
        self.q("UPDATE ops.ingest_cursor SET cursor_value = '2099-01-01' WHERE source_id = %s AND cursor_key = 'file' "
               "RETURNING 1", bld_file.SOURCE)
        self.assertEqual(bld_file.main(["--config", self.cfg, "--dir", str(self.hub)]), 1, "더 예전 파일이면 멈춤")
        self.q("UPDATE ops.ingest_cursor SET cursor_value = '2000-01-01' WHERE source_id = %s AND cursor_key = %s "
               "RETURNING 1", bld.SOURCE, P3)
        self.assertEqual(bld_file.main(["--config", self.cfg, "--dir", str(self.hub), "--allow-older"]), 0)
        self.assertEqual(self.q("SELECT pnu FROM core.dim_building WHERE bld_pk = 'T-4'"), [(P3,)],
                         "파일보다 먼저 API로 받은 필지는 파일 값으로 올림")

    def test_z_moved_building_drops_old_main_lot(self):
        """대장의 대표 지번이 바뀌면 예전 대표 필지 연결을 지움(부속지번은 그대로)."""
        bld_file.main(["--config", self.cfg, "--dir", str(self.hub), "--allow-older"])
        self.q("INSERT INTO core.bridge_parcel_building (pnu, bld_pk, is_main_lot, source, asof) "
               "VALUES (%s, 'T-3', true, %s, '2026-01-01') RETURNING 1", P1, bld.SOURCE_LABEL)
        bld_file.main(["--config", self.cfg, "--dir", str(self.hub), "--allow-older"])
        self.assertEqual(self.q("SELECT pnu FROM core.bridge_parcel_building WHERE bld_pk = 'T-3'"), [(P2,)])

    def test_incomplete_or_bad_files_stop(self):
        with tempfile.TemporaryDirectory() as d:
            make_zips(d)
            (Path(d) / "mart_djy_10.zip").unlink()
            self.assertEqual(bld_file.main(["--config", self.cfg, "--dir", d, "--dry-run"]), 1, "zip이 빠지면 멈춤")
        with tempfile.TemporaryDirectory() as d:
            make_zips(d)
            with zipfile.ZipFile(Path(d) / "mart_djy_05.zip", "w") as z:
                z.writestr("mart_djy_05.txt", ATCH[0] + "|더한열\r\n")
            self.assertEqual(bld_file.main(["--config", self.cfg, "--dir", d, "--dry-run"]), 1, "열 수가 다르면 멈춤")

    def test_mixed_dates_stop(self):
        with tempfile.TemporaryDirectory() as d:
            make_zips(d)
            with zipfile.ZipFile(Path(d) / "mart_djy_02.zip", "w") as z:
                z.writestr(zipfile.ZipInfo("mart_djy_02.txt", (2026, 8, 20, 0, 0, 0)), RECAP[0])
            self.assertEqual(bld_file.main(["--config", self.cfg, "--dir", d, "--dry-run"]), 1)
