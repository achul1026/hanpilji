"""단지 카드의 「호갱노노 대비」 칸 시험(2026-10-30): 세대당 주차 · 가장 많이 거래된 면적 · 기간별 변동률 · 토지거래허가 기간.
DB 시험은 HANPILJI_TEST_DSN이 있을 때만 돎. 시험용 구(99960)에만 쓰고 끝나면 지움.
"""
import datetime
import os
import unittest

DSN = os.environ.get("HANPILJI_TEST_DSN")


@unittest.skipUnless(DSN, "HANPILJI_TEST_DSN 없음")
class Card(unittest.TestCase):
    SGG = "99960"

    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg = psycopg
        cls.cleanup()
        t = datetime.date.today()
        ago = lambda months: t - datetime.timedelta(days=round(months * 30.44))
        with psycopg.connect(DSN) as c:
            c.execute("INSERT INTO core.dim_region (lawd_cd, sido, sigungu, emd, source, asof) VALUES "
                      "('9996000000', '시험시', '카드구', NULL, 't-pc', %s), ('9996010300', '시험시', '카드구', '도곡동', 't-pc', %s)",
                      (t, t))
            c.execute("INSERT INTO core.dim_complex (complex_id, name, lawd_cd, jibun, kind, households, parking_total, source, asof) "
                      "VALUES ('t-pc1', '시험렉슬', '9996010300', '527', '아파트', 100, 148, 't-pc', %s), "
                      "('t-pc2', '시험오피스', '9996010300', '528', '오피스텔', NULL, NULL, 't-pc', %s)", (t, t))
            deals = []
            # 85㎡: 3년 전 창 20억 · 1년 전 창 30억 · 최근 29.7억(각 3건). 60㎡는 최근 1건뿐이라 변동률에 안 들어감
            for k, (months, amt) in enumerate([(33, 2_000_000_000), (15, 3_000_000_000), (2, 2_970_000_000)]):
                deals += [(f"t-pc-{k}-{i}", "t-pc1", ago(months) + datetime.timedelta(days=i), amt, 84.9) for i in range(3)]
            deals.append(("t-pc-60", "t-pc1", ago(1), 2_000_000_000, 59.9))
            deals += [(f"t-pc-o{i}", "t-pc2", ago(2), 1_000_000_000, 40.0) for i in range(3)]
            for d in deals:
                c.execute("INSERT INTO core.fact_deal (deal_id, lawd_cd, complex_id, deal_type, contract_date, amount, area, "
                          "source, asof) VALUES (%s, '9996000000', %s, '아파트', %s, %s, %s, 't-pc', %s)", (*d, t))
            # 오늘 걸린 것 하나 + 이미 끝난 것 하나(카드에 안 나와야 함)
            c.execute("INSERT INTO core.ref_permit_zone (zone_id, name, sigungu, target, start_date, end_date, rule, law_name, "
                      "article_no, source, source_url, asof) VALUES "
                      "('t-pc-now', '시험 토허', %s, '아파트', %s, %s, '허가 후 2년 실거주', '법', '제10조', 't-pc 출처', 'https://x', %s), "
                      "('t-pc-old', '지난 토허', %s, '아파트', %s, %s, '지난 것', '법', '제10조', 't-pc 출처', 'https://x', %s)",
                      ([cls.SGG], t - datetime.timedelta(days=100), t + datetime.timedelta(days=60), t,
                       [cls.SGG], t - datetime.timedelta(days=900), t - datetime.timedelta(days=500), t))

    @classmethod
    def tearDownClass(cls):
        cls.cleanup()

    @classmethod
    def cleanup(cls):
        with cls.psycopg.connect(DSN) as c:
            c.execute("DELETE FROM core.ref_permit_zone WHERE zone_id LIKE 't-pc-%'")
            c.execute("DELETE FROM core.fact_deal WHERE source = 't-pc'")
            c.execute("DELETE FROM core.dim_complex WHERE source = 't-pc'")
            c.execute("DELETE FROM core.dim_region WHERE source = 't-pc'")

    def card(self, name):
        with self.psycopg.connect(DSN) as c:
            cur = c.execute("SELECT * FROM mart.q_property_card(%s, %s, 12)", (self.SGG, name))
            return dict(zip([d.name for d in cur.description], cur.fetchone()))

    def test_parking_and_top_area(self):
        r = self.card("시험렉슬")
        self.assertEqual(float(r["bld_parking_per_household"]), 1.48)
        self.assertEqual((r["trade_top_area"], r["trade_top_count"], r["trade_top_avg"]), (85, 3, 2_970_000_000),
                         "12개월 안에서 가장 많이 거래된 면적 하나")

    def test_change_rates(self):
        r = self.card("시험렉슬")
        self.assertEqual(float(r["chg_1y_pct"]), -1.0, "29.7억 ÷ 30억")
        self.assertEqual(float(r["chg_3y_pct"]), 48.5, "29.7억 ÷ 20억")
        self.assertIn("3건", r["chg_1y_basis"], "60㎡ 1건은 1년 전 창에 짝이 없어 빠짐")
        self.assertIn("3년 전 6개월", r["chg_3y_basis"])

    def test_permit_zone(self):
        r = self.card("시험렉슬")
        self.assertEqual(r["permit_name"], "시험 토허", "끝난 지정은 빼고 오늘 효력이 있는 것만")
        self.assertEqual(r["permit_rule"], "허가 후 2년 실거주")
        self.assertEqual(r["permit_url"], "https://x", "화면이 근거를 열 수 있게 출처 주소도 줌")
        self.assertIn("t-pc 출처", r["source"])
        o = self.card("시험오피스")
        self.assertIsNone(o["permit_to"], "오피스텔은 아파트 토허 대상이 아님")
        self.assertIsNone(o["chg_1y_pct"], "앞선 거래가 없으면 셈하지 않음")
        self.assertEqual(o["chg_1y_basis"], "거래가 적어 셈하지 않음")


if __name__ == "__main__":
    unittest.main()
