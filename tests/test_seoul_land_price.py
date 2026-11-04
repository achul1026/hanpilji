"""서울 개별공시지가 수집기 시험. 견본은 실제 파일(2026년, 2026-11-04 받음)에서 다섯 줄 · 화면 목록 두 줄만 남김.
DB 시험은 HANPILJI_TEST_DSN이 있을 때만(scripts/init_db.sh로 만든 빈 DB)."""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import psycopg

from hanpilji.collectors import seoul_land_price as s

FIX = Path(__file__).parent / "fixtures"
ZIP = FIX / "seoul_land_price_sample.zip"
DSN = os.environ.get("HANPILJI_TEST_DSN")


class Offline(unittest.TestCase):
    def test_parse_list(self):
        got = s.parse_list((FIX / "seoul_land_price_list.html").read_text())
        self.assertEqual(got, {2026: {"seq": "93", "updated": "2026-09-03"}, 2025: {"seq": "89", "updated": "2026-01-03"}})

    def test_rows_and_normalize(self):
        rows = list(s.read_rows(ZIP))
        self.assertEqual(len(rows), 5)
        v, why = s.normalize(rows[0])
        self.assertIsNone(why)
        self.assertEqual(v, ("1129013600100170009", 2026, 7_070_000, "1129013600", "성북구", "하월곡동", "17-9", "토지"))
        v, _ = s.normalize(rows[2])
        self.assertEqual((v[6], v[7]), ("산 5-51", "임야"))
        self.assertEqual([s.normalize(r)[0] is None for r in rows], [False, False, False, True, True])

    def test_empty_price_rejected(self):
        row = dict(next(s.read_rows(ZIP)), **{"공시지가(원/㎡)": ""})
        self.assertEqual(s.normalize(row), (None, "공시지가 없음"))


@unittest.skipUnless(DSN, "HANPILJI_TEST_DSN 없음")
class Load(unittest.TestCase):
    def test_load_twice_and_keep_other_sources(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        cfg = Path(tmp) / "config.toml"
        cfg.write_text(f'[db]\ndsn = "{DSN}"\n[storage]\nraw_dir = "{tmp}/raw"\n[pilot]\nsigungu = ["11680"]\n[law]\noc = "test"\n')
        files = {2026: {"seq": "93", "updated": "2026-09-03"}}
        fetch = lambda seq, dest: shutil.copy(ZIP, dest)
        with psycopg.connect(DSN, autocommit=True) as c:
            c.execute("DELETE FROM core.fact_land_price WHERE pnu LIKE '11%'")
            c.execute("DELETE FROM ops.ingest_cursor WHERE source_id = 'seoul_land_price'")
            # 다른 수집처(V-World)가 먼저 넣은 줄은 그대로 남아야 함
            c.execute("""INSERT INTO core.dim_region (lawd_cd, source, asof) VALUES ('1129013600', 't', '2026-01-01')
                         ON CONFLICT DO NOTHING""")
            c.execute("""INSERT INTO core.dim_parcel (pnu, lawd_cd, source, asof) VALUES ('1129013600100170009', '1129013600', 't', '2026-01-01')
                         ON CONFLICT DO NOTHING""")
            c.execute("""INSERT INTO core.fact_land_price (pnu, base_year, is_standard, price_per_m2, source, asof)
                         VALUES ('1129013600100170009', 2026, false, 1, 'V-World', '2026-01-01')""")
        self.assertEqual(s.main(["--config", str(cfg)], lister=lambda: files, fetch=fetch), 0)
        self.assertEqual(s.main(["--config", str(cfg)], lister=lambda: files, fetch=fetch), 0)   # 바뀐 파일 없음
        self.assertEqual(s.main(["--config", str(cfg), "--force"], lister=lambda: files, fetch=fetch), 0)
        with psycopg.connect(DSN) as c:
            rows = dict(c.execute("SELECT pnu, price_per_m2 FROM core.fact_land_price WHERE pnu LIKE '11%'").fetchall())
            emd = c.execute("SELECT emd FROM core.dim_region WHERE lawd_cd = '1129013600'").fetchone()[0]
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows["1129013600100170009"], 1)          # V-World 줄은 덮지 않음
        self.assertEqual(rows["1129013600100100031"], 5_984_000)
        self.assertEqual(emd, "하월곡동")                         # 비어 있던 동 이름을 채움


if __name__ == "__main__":
    unittest.main()
