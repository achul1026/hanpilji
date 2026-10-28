"""공매 수집기(온비드)와 경매 · 공매 마트 함수 시험.

tests/fixtures/onbid_list_real.json · onbid_nodata_real.json 은 실제 응답(2026-10-28 강남구 압류재산, 물건 2개 x 10회차).
onbid_list_doc.json 은 단지 · 실거래와 잇는 DB 시험용으로 실제 응답 항목 이름에 맞춰 만든 자료.
DB 시험은 HANPILJI_TEST_DSN이 있을 때만 돎.
"""
import datetime
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import psycopg

from hanpilji import datagokr
from hanpilji.collectors import onbid

DSN = os.environ.get("HANPILJI_TEST_DSN")
FIX = Path(__file__).parent / "fixtures"
TODAY = datetime.date(2026, 10, 28)


def fixture_get(url):
    assert url.startswith(onbid.BASE + "/" + onbid.PATH), url
    return (FIX / "onbid_list_doc.json").read_bytes()


class Parse(unittest.TestCase):
    def rows(self, name="onbid_list_doc.json"):
        return datagokr.items(datagokr.parse((FIX / name).read_bytes()))

    def test_real_response(self):
        rows = [onbid.normalize(r) for r in self.rows("onbid_list_real.json")]
        self.assertEqual(len(rows), 20)
        r = next(x for x in rows if x["pbct_cdtn_no"] == "6156035")
        self.assertEqual((r["cltr_mng_no"], r["round_no"], r["appraisal"], r["min_bid"]),
                         ("2025-15753-001", 44, 479000000, 479000000), "숫자로 오는 칸도 읽음")
        self.assertEqual((r["emd"], r["jibun"], r["pnu"], r["usage"]), ("도곡동", "467-6", "1168011800004670006", "업무시설"))
        self.assertEqual(r["address"], "서울특별시 강남구 도곡동 467-6 대림아크로텔 제17층 제1714호", "주소 칸이 없어 물건명을 씀")
        self.assertEqual((r["area_m2"], r["status"], r["org"]), (27.93, "입찰준비중", "남동세무서"))
        self.assertEqual(r["bid_end"], datetime.datetime(2026, 12, 9, 17, 0))

    def test_normalize(self):
        rows = [onbid.normalize(r) for r in self.rows()]
        self.assertIsNone(rows[-1], "물건번호 없는 줄은 버림")
        r = rows[1]
        self.assertEqual((r["cltr_mng_no"], r["pbct_cdtn_no"], r["round_no"]), ("2026-0001-000001", "1002", 2))
        self.assertEqual((r["appraisal"], r["min_bid"], r["fail_count"], r["area_m2"]), (400000000, 250000000, 1, 100.0))
        self.assertEqual((r["emd"], r["jibun"], r["usage"]), ("가동", "1", "아파트"))
        self.assertEqual(r["status"], "입찰준비중", "코드만 오면 이름으로")
        self.assertEqual(r["bid_end"], datetime.datetime(2099, 11, 11, 17, 0))
        self.assertEqual(rows[0]["min_bid"], 320000000, "쉼표가 든 금액")

    def test_split_jibun(self):
        cases = {
            "서울특별시 강남구 대치동 316 은마아파트 제28동": ("대치동", "316"),
            "서울특별시 강남구 개포동 12-3번지": ("개포동", "12-3"),
            "서울특별시 강남구 세곡동 산 4": ("세곡동", "산4"),
            "경기도 성남시 분당구 정자동 1 - 2, 101호": ("정자동", "1-2"),
            "서울특별시 강남구 테헤란로 152": (None, None),
        }
        for text, want in cases.items():
            self.assertEqual(onbid.split_jibun(text), want, text)
        self.assertEqual(onbid.split_jibun(None, "강남구 대치동 316"), ("대치동", "316"), "주소가 없으면 물건명에서")

    def test_emd_mismatch_drops_jibun(self):
        r = onbid.normalize({"cltrMngNo": "x", "lctnEmdNm": "대치동", "zadrNm": "서울특별시 강남구 도곡동 527"})
        self.assertEqual((r["emd"], r["jibun"]), ("대치동", None))

    def test_gateway_errors(self):
        with self.assertRaises(datagokr.ApiError) as cm:
            datagokr.parse('{"OpenAPI_ServiceResponse": {"cmmMsgHeader": {"errMsg": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR", '
                           '"returnAuthMsg": "등록되지 않은 서비스키", "returnReasonCode": "30"}}}'.encode())
        self.assertTrue(str(cm.exception).startswith("30 "))
        self.assertTrue(datagokr.is_fatal(cm.exception), "키 오류는 멈춤")

    def test_nodata_is_empty(self):
        for body in ((FIX / "onbid_nodata_real.json").read_bytes(), b'{"header": {"resultCode": "03", "resultMsg": "NODATA_ERROR"}}'):
            f = onbid.Fetcher(lambda url: body, tempfile.mkdtemp())
            with mock.patch.dict(os.environ, {"DATA_GO_KR_KEY": "k"}):
                self.assertEqual(f.page("서울특별시", "강남구", "0010", 1), (b"", []))

    def test_budget(self):
        f = onbid.Fetcher(fixture_get, tempfile.mkdtemp(), max_calls=1)
        with mock.patch.dict(os.environ, {"DATA_GO_KR_KEY": "k"}):
            f.page("시험시", "경매구", "0007", 1)
            with self.assertRaises(onbid.Budget):
                f.page("시험시", "경매구", "0007", 2)

    def test_url(self):
        url = datagokr.build_url(onbid.PATH, {"lctnSggnm": "강남구"}, key="a/b", base=onbid.BASE)
        self.assertEqual(url, f"{onbid.BASE}/{onbid.PATH}?serviceKey=a%2Fb&lctnSggnm=%EA%B0%95%EB%82%A8%EA%B5%AC")


@unittest.skipUnless(DSN, "HANPILJI_TEST_DSN 없음")
class WithDb(unittest.TestCase):
    """수집 → q_auction_discount, 그리고 q_auction_checklist를 시험용 구(99980 시험시 경매구)에서 확인."""
    SGG = "99980"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = Path(cls.tmp.name) / "config.toml"
        cfg.write_text(f'[db]\ndsn = "{DSN}"\n[storage]\nraw_dir = "{cls.tmp.name}/raw"\n[pilot]\nsigungu = ["{cls.SGG}"]\n'
                       '[law]\noc = "test"\nnames = []\n[onbid]\nprpt_divs = ["0007"]\n', encoding="utf-8")
        cls.cfg = str(cfg)
        os.environ.pop("HANPILJI_DSN", None)
        cls.cleanup()
        d = datetime.date.today()
        with psycopg.connect(DSN) as c:
            c.execute("INSERT INTO core.dim_region (lawd_cd, sido, sigungu, emd, source, asof) VALUES "
                      "('9998000000', '시험시', '경매구', NULL, 't-au', %s), ('9998010100', '시험시', '경매구', '가동', 't-au', %s)",
                      (d, d))
            c.execute("INSERT INTO core.dim_complex (complex_id, name, lawd_cd, jibun, pnu, source, asof) VALUES "
                      "('t-au1', '시험파크', '9998010100', '1', NULL, 't-au', %s), "
                      "('t-au2', '피엔유빌', '9998010100', '500', '9998010100105000000', 't-au', %s)", (d, d))
            # 시험파크 100㎡: ㎡당 300만 · 330만 · 360만(중위 330만 → 추정 시세 3억 3천만), 60㎡ 거래는 면적이 달라 뺌
            for i, (area, unit) in enumerate([(100, 3000000), (101, 3300000), (99, 3600000), (60, 9000000)]):
                c.execute("INSERT INTO core.fact_deal (deal_id, lawd_cd, complex_id, deal_type, contract_date, amount, area, "
                          "source, asof) VALUES (%s, '9998000000', 't-au1', '아파트', %s, %s, %s, 't-au', %s)",
                          (f"t-au-{i}", d - datetime.timedelta(days=30), unit * area, area, d))

    @classmethod
    def tearDownClass(cls):
        cls.cleanup()

    @classmethod
    def cleanup(cls):
        with psycopg.connect(DSN) as c:
            c.execute("DELETE FROM core.fact_auction WHERE sido = '시험시'")
            c.execute("DELETE FROM stg.auction_raw WHERE req_key LIKE '시험시%%'")
            c.execute("DELETE FROM core.fact_deal WHERE source = 't-au'")
            c.execute("DELETE FROM core.dim_complex WHERE source = 't-au'")
            c.execute("DELETE FROM core.dim_region WHERE source = 't-au'")
            c.execute("DELETE FROM core.doc_law WHERE source = 't-au'")

    def q(self, sql, *args):
        with psycopg.connect(DSN) as c:
            return c.execute(sql, args or None).fetchall()

    def collect(self):
        with mock.patch.dict(os.environ, {"DATA_GO_KR_KEY": "k"}):
            self.assertEqual(onbid.main(["--config", self.cfg], http_get=fixture_get, today=TODAY), 0)

    def test_collect_and_discount(self):
        self.collect()
        self.collect()
        self.assertEqual(self.q("SELECT count(*), count(DISTINCT sigungu_cd) FROM core.fact_auction WHERE sido = '시험시'")[0],
                         (4, 1), "다시 돌려도 안 쌓이고, 시군구 코드가 붙음")
        self.assertEqual(self.q("SELECT sigungu_cd FROM core.fact_auction WHERE sido = '시험시' LIMIT 1")[0][0], self.SGG)
        rows = self.q("SELECT cltr_mng_no, round_no, min_bid, complex_name, deal_count, est_market_price, discount_pct, label, "
                      "min_bid_pct_of_appraisal, source, row_total FROM mart.q_auction_discount(%s)", self.SGG)
        self.assertEqual(len(rows), 2, "입찰이 끝난 물건(낙찰)은 뺌")
        top = rows[0]
        self.assertEqual(top[:6], ("2026-0001-000001", 2, 250000000, "시험파크", 3, 330000000), "현재 회차 · 같은 면적 3건")
        self.assertEqual(float(top[6]), 24.2)
        self.assertIn("15% 이상", top[7])
        self.assertEqual(float(top[8]), 62.5)
        self.assertIn("실거래가", top[9])
        self.assertEqual((rows[1][3], rows[1][6]), ("피엔유빌", None), "지번(999)이 달라도 PNU로 단지를 찾음")
        self.assertIn("아파트가 아님", rows[1][7], "다세대는 아파트 실거래와 비교하지 않음")
        self.assertEqual(self.q("SELECT count(*) FROM mart.q_auction_discount(%s, NULL, false)", self.SGG)[0][0], 3)
        self.assertEqual(self.q("SELECT count(*) FROM mart.q_auction_discount(%s, NULL, true, 20)", self.SGG)[0][0], 1)
        self.assertEqual(self.q("SELECT count(*) FROM mart.q_auction_discount(%s, '다세대')", self.SGG)[0][0], 1)

    def test_checklist(self):
        rows = self.q("SELECT step, law_name, article_no, article_title, row_total FROM mart.q_auction_checklist()")
        self.assertEqual(len(rows), 14)
        self.assertEqual(rows[0][1:3], ("민사집행법", "제91조"))
        self.assertTrue(all(r[1] is None or r[2] for r in rows), "법령이 있으면 조문 번호도")
        self.assertIn(12, [r[0] for r in self.q("SELECT step FROM mart.q_auction_checklist('공매')")])
        self.assertNotIn(12, [r[0] for r in self.q("SELECT step FROM mart.q_auction_checklist('경매')")])
        self.assertEqual([r[0] for r in self.q("SELECT step FROM mart.q_auction_checklist(NULL, '유치권')")], [7])
        with psycopg.connect(DSN) as c:
            c.execute("INSERT INTO core.doc_law (article_id, law_id, law_name, article_no, article_title, body, search_text, "
                      "effective_date, source, asof) VALUES ('t-au-91', 'x', '민사집행법', '제91조', '인수주의와 잉여주의의 선택 등', "
                      "'제91조(인수주의와 잉여주의의 선택 등) ②매각부동산 위의 모든 저당권은 매각으로 소멸된다.', 's', "
                      "'2026-01-01', 't-au', %s)", (TODAY,))
        r = self.q("SELECT article_title, article_body, source FROM mart.q_auction_checklist(NULL, '말소기준')")[0]
        self.assertEqual(r[0], "인수주의와 잉여주의의 선택 등")
        self.assertIn("저당권은 매각으로 소멸", r[1])
        self.assertIn("법제처", r[2])


if __name__ == "__main__":
    unittest.main()
