"""주택 공시가격 수집기(공공데이터포털 파일)와 mart.q_house_price 시험.

tests/fixtures/house_price_real.zip 은 2026-10-07 수정본 실제 파일에서 고른 8줄(종로구 2 · 은마 3 · 블럭지번 2 · 빌라 1)과
샘플 CSV를 같은 모양으로 다시 묶은 것. DB 시험은 HANPILJI_TEST_DSN이 있을 때만 돌고, 강남구 코드를 시험용 구(99970)로 바꿔 넣음.
"""
import datetime
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from hanpilji.collectors import house_price

DSN = os.environ.get("HANPILJI_TEST_DSN")
FIX = Path(__file__).parent / "fixtures"
ZIP = FIX / "house_price_real.zip"


class Parse(unittest.TestCase):
    def test_pick_only_pilot_rows_from_the_big_csv(self):
        picked, header = house_price.pick(ZIP, ["11680"])
        rows = picked["11680"]
        self.assertEqual(len(rows), 6, "종로구 줄 · 샘플 CSV는 빼고 강남구 6줄")
        self.assertIn("건축물대장PK", header)
        self.assertEqual({r["단지명"] for r in rows}, {"은마", "디아크리온강남", "진성빌라트"})

    def test_normalize(self):
        rows = {(r["단지명"], r["호명"]): r for r in house_price.pick(ZIP, ["11680"])[0]["11680"]}
        d = datetime.date(2026, 10, 7)
        r = house_price.normalize(rows[("은마", "1401")], d)
        self.assertEqual((r["pnu"], r["lawd_cd"], r["dong"], r["excl_area"], r["price"], r["base_year"]),
                         ("1168010600103160000", "1168010600", "1", 76.79, 2358000000, 2026))
        self.assertIsNone(r["lot_name"])
        b = house_price.normalize(rows[("디아크리온강남", "201")], d)
        self.assertIsNone(b["pnu"], "블럭지번은 본번이 0이라 PNU를 만들지 않음")
        self.assertEqual(b["lot_name"], "수서역세권 공공주택지구내 A3블록")
        v = house_price.normalize(rows[("진성빌라트", "201")], d)
        self.assertEqual((v["pnu"], v["dong"]), ("1168010100106010023", None), "빌라는 동명이 비어 있음")
        self.assertIsNone(house_price.normalize({**rows[("은마", "101")], "공시가격": ""}, d))

    def test_mountain_lot(self):
        row = {"법정동코드": "1168010300", "특수지코드": "1", "본번": "12", "부번": "3"}
        self.assertEqual(house_price.pnu(row), "1168010300200120003")

    def test_header_change_is_an_error(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("a.csv", '"기준연도","법정동코드"\r\n"2026","1168010600"\r\n')
        with self.assertRaisesRegex(RuntimeError, "열 이름"):
            house_price.pick(buf, ["11680"])


class FakePortal:
    def __init__(self, zip_path, atch="FILE_1"):
        self.zip_path, self.atch, self.downloads = zip_path, atch, 0

    def latest(self):
        return {"atch": self.atch, "sn": "1", "name": "국토교통부_주택 공시가격 정보_20260101", "updated": "2026-10-07"}

    def download(self, f, dest):
        self.downloads += 1
        Path(dest).write_bytes(Path(self.zip_path).read_bytes())


@unittest.skipUnless(DSN, "HANPILJI_TEST_DSN 없음")
class WithDb(unittest.TestCase):
    SGG = "99970"

    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg = psycopg
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = Path(cls.tmp.name) / "config.toml"
        cfg.write_text(f'[db]\ndsn = "{DSN}"\n[storage]\nraw_dir = "{cls.tmp.name}/raw"\n[pilot]\nsigungu = ["{cls.SGG}"]\n'
                       '[law]\noc = "test"\nnames = []\n', encoding="utf-8")
        cls.cfg = str(cfg)
        os.environ.pop("HANPILJI_DSN", None)
        # 실제 파일 줄의 강남구 코드를 시험용 구로 바꾼 압축
        cls.zip = Path(cls.tmp.name) / "hp.zip"
        with zipfile.ZipFile(ZIP) as src, zipfile.ZipFile(cls.zip, "w") as dst:
            for i in src.infolist():
                dst.writestr(i.filename, src.read(i).replace(b'"11680', b'"' + cls.SGG.encode()))
        cls.cleanup()
        d = datetime.date.today()
        with psycopg.connect(DSN) as c:
            c.execute("INSERT INTO core.dim_region (lawd_cd, sido, sigungu, emd, source, asof) VALUES "
                      "('9997000000', '시험시', '공시구', NULL, 't-hp', %s), ('9997010600', '시험시', '공시구', '대치동', 't-hp', %s), "
                      "('9997011200', '시험시', '공시구', '자곡동', 't-hp', %s)", (d, d, d))
            c.execute("INSERT INTO core.dim_complex (complex_id, name, lawd_cd, jibun, pnu, kind, source, asof) VALUES "
                      "('t-hp1', '은마', '9997010600', '316', '9997010600103160000', '아파트', 't-hp', %s), "
                      "('t-hp2', '디아크리온 강남', '9997011200', '0', NULL, '아파트', 't-hp', %s)", (d, d))
            # 은마 77㎡ 매매 2건: 평균 33억 → 공시가격 중간값 22.37억은 67.8%
            for i, amt in enumerate([3_200_000_000, 3_400_000_000]):
                c.execute("INSERT INTO core.fact_deal (deal_id, lawd_cd, complex_id, deal_type, contract_date, amount, area, "
                          "source, asof) VALUES (%s, '9997000000', 't-hp1', '아파트', %s, %s, 76.79, 't-hp', %s)",
                          (f"t-hp-{i}", d - datetime.timedelta(days=30), amt, d))

    @classmethod
    def tearDownClass(cls):
        cls.cleanup()

    @classmethod
    def cleanup(cls):
        with cls.psycopg.connect(DSN) as c:
            c.execute("DELETE FROM core.fact_house_price WHERE lawd_cd LIKE %s", (cls.SGG + "%",))
            c.execute("DELETE FROM ops.ingest_cursor WHERE source_id = %s AND cursor_key = %s",
                      (house_price.SOURCE, cls.SGG))
            c.execute("DELETE FROM core.fact_deal WHERE source = 't-hp'")
            c.execute("DELETE FROM core.dim_complex WHERE source = 't-hp'")
            c.execute("DELETE FROM core.dim_region WHERE source = 't-hp'")

    def q(self, sql, *args):
        with self.psycopg.connect(DSN) as c:
            cur = c.execute(sql, args or None)
            return cur.fetchall() if cur.description else None

    def test_1_load_skip_and_reload(self):
        portal = FakePortal(self.zip)
        self.assertEqual(house_price.main(["--config", self.cfg], portal=portal), 0)
        n = self.q("SELECT count(*), min(asof) FROM core.fact_house_price WHERE lawd_cd LIKE %s", self.SGG + "%")[0]
        self.assertEqual(n, (6, datetime.date(2026, 10, 7)), "기준일은 포털 수정일")
        self.assertEqual(house_price.main(["--config", self.cfg], portal=portal), 0)
        self.assertEqual(portal.downloads, 1, "같은 파일 번호면 받지 않음")
        self.assertEqual(house_price.main(["--config", self.cfg, "--force"], portal=portal), 0)
        self.assertEqual(self.q("SELECT count(*) FROM core.fact_house_price WHERE lawd_cd LIKE %s", self.SGG + "%")[0][0], 6,
                         "다시 넣어도 쌓이지 않음")

    def test_2_q_house_price_next_to_trades(self):
        rows = self.q("SELECT complex_name, area_m2, unit_count, official_median, official_min, official_max, trade_count, "
                      "trade_avg, official_ratio::float, base_year, row_total FROM mart.q_house_price(%s, '은마')", self.SGG)
        self.assertEqual(rows, [("은마", 77, 3, 2237000000, 2237000000, 2358000000, 2, 3300000000, 67.8, 2026, 1)])

    def test_3_one_unit_and_block_lot_by_name(self):
        rows = self.q("SELECT dong, ho, official_median FROM mart.q_house_price(%s, '은마', NULL, '1동', '1401호')", self.SGG)
        self.assertEqual(rows, [("1", "1401", 2358000000)])
        rows = self.q("SELECT ho, trade_count FROM mart.q_house_price(%s, '은마', NULL, NULL, '101')", self.SGG)
        self.assertEqual(rows, [("101", 2)], "호마다 줄이 여럿이어도 매매 건수는 한 번만 셈")
        rows = self.q("SELECT complex_name, unit_count, official_median, trade_count, official_ratio "
                      "FROM mart.q_house_price(%s, '디아크리온')", self.SGG)
        self.assertEqual(rows, [("디아크리온 강남", 2, 607000000, 0, None)], "PNU가 없는 블럭지번은 이름으로 맞춤")
