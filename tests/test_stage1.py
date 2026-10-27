"""공공데이터포털 API 수집기 시험(행위제한 · 아파트 매매 · 전월세). 인증키 없이 저장해 둔 응답으로 돎.

tests/fixtures/*_real.xml 은 실제 응답(2026-10-27 수집). 실거래가는 한 단지(세곡푸르지오 2026년 8월)만 남겨 줄였고
항목 내용은 그대로임. DB 시험은 HANPILJI_TEST_DSN이 있을 때만 돎.
"""
import datetime
import os
import tempfile
import unittest
from pathlib import Path

import psycopg

from hanpilji import datagokr
from hanpilji.collectors import luinfo, rtms

DSN = os.environ.get("HANPILJI_TEST_DSN")
FIX = Path(__file__).parent / "fixtures"


def as_month(rows, ym):
    """저장한 거래의 계약 연 · 월을 요청한 달로 바꿈(실제 API도 그 달 계약만 돌려줌). 29일 이후는 28일로(2월 대비)."""
    return [{**r, "dealYear": ym[:4], "dealMonth": str(int(ym[4:])), "dealDay": str(min(int(r["dealDay"]), 28))}
            for r in rows]


def fixture_fetch(path, params):
    name = ("rtms_trade_real.xml" if "Trade" in path else "rtms_rent_real.xml" if "Rent" in path
            else "luinfo_real_snackbar.xml")
    data = (FIX / name).read_bytes()
    obj = datagokr.parse(data)
    if "DEAL_YMD" in params:
        obj["body"]["items"]["item"] = as_month(datagokr.items(obj), str(params["DEAL_YMD"]))
    return data, obj


class Portal(unittest.TestCase):
    def test_key_error_raises(self):
        with self.assertRaises(datagokr.ApiError) as e:
            datagokr.parse((FIX / "datagokr_key_error.xml").read_bytes())
        self.assertIn("SERVICE_KEY_IS_NOT_REGISTERED", str(e.exception))

    def test_key_encoding(self):
        self.assertIn("serviceKey=a%2Bb%3D%3D", datagokr.build_url("x/y", {}, key="a+b=="))   # Decoding 키
        self.assertIn("serviceKey=a%2Bb%3D%3D", datagokr.build_url("x/y", {}, key="a%2Bb%3D%3D"))  # Encoding 키

    def test_json_and_single_item(self):
        obj = datagokr.parse(b'{"response":{"header":{"resultCode":"000","resultMsg":"OK"},'
                             b'"body":{"items":{"item":{"aptNm":"A"}},"totalCount":1}}}')
        self.assertEqual(datagokr.items(obj), [{"aptNm": "A"}])
        empty = datagokr.parse(b'<response><header><resultCode>000</resultCode></header>'
                               b'<body><items/><totalCount>0</totalCount></body></response>')
        self.assertEqual(datagokr.items(empty), [])


class RtmsParse(unittest.TestCase):
    def setUp(self):
        self.rows = datagokr.items(datagokr.parse((FIX / "rtms_trade_real.xml").read_bytes()))
        self.rent = datagokr.items(datagokr.parse((FIX / "rtms_rent_real.xml").read_bytes()))

    def test_deal_values(self):
        d, why = rtms.to_deal(self.rows[0], "11680")
        self.assertIsNone(why)
        self.assertEqual(d["amount"], 2_050_000_000, "「205,000」만원 → 원")
        self.assertEqual(d["area"], 84.84)
        self.assertEqual(d["contract_date"], datetime.date(2026, 8, 21))
        self.assertEqual(d["lawd_cd"], "1168000000", "응답에 umdCd가 없어서 시군구 단위")
        self.assertFalse(d["is_cancelled"], "해제 아님은 빈칸(공백 한 칸)으로 옴")
        self.assertTrue(rtms.to_deal(self.rows[2], "11680")[0]["is_cancelled"], "해제는 cdealType=O")
        self.assertEqual(rtms.to_deal({**self.rows[0], "excluUseAr": " "}, "11680"), (None, "전용면적 없음"))

    def test_rent_values(self):
        r, why = rtms.to_rent(self.rent[1], "11680")
        self.assertIsNone(why)
        self.assertEqual((r["deposit"], r["monthly_rent"]), (200_000_000, 2_450_000))
        self.assertEqual((r["contract_kind"], r["renewal"]), ("갱신", True))
        self.assertEqual(rtms.to_rent(self.rent[0], "11680")[0]["renewal"], None, "갱신요구권 빈칸")

    def test_rent_has_no_cancel_flag(self):
        """전월세 응답에는 해제 여부 항목이 없음(2026-10-27, 강남구 36개월 전부 확인). 매매에만 cdealType이 있음."""
        self.assertTrue(all("cdealType" not in r and "cdealDay" not in r for r in self.rent))
        self.assertTrue(all("cdealType" in r for r in self.rows))

    def test_same_complex_for_trade_and_rent(self):
        """매매 응답에는 단지 일련번호(aptSeq)가 없어서 시군구 · 동 · 지번 · 단지명으로 묶음. 전월세의 aptSeq와 1:1."""
        self.assertNotIn("aptSeq", self.rows[0])
        self.assertEqual({r["aptSeq"] for r in self.rent}, {"11680-4090"})
        self.assertEqual({rtms.complex_key(r) for r in self.rows + self.rent}, {rtms.complex_key(self.rows[0])})

    def test_complex_name_suffix(self):
        """실제로 매매 「강남브리즈힐(토지임대부아파트)」 · 전월세 「강남브리즈힐」(자곡동 618)로 옴."""
        a = {"sggCd": "11680", "umdNm": "자곡동", "jibun": "618", "aptNm": "강남브리즈힐(토지임대부아파트)"}
        self.assertEqual(rtms.complex_key(a), rtms.complex_key({**a, "aptNm": "강남브리즈힐"}))
        self.assertNotEqual(rtms.complex_key(a), rtms.complex_key({**a, "aptNm": "강남브리즈힐2"}))

    def test_months_back(self):
        self.assertEqual(rtms.months_back(3, datetime.date(2026, 2, 10)), ["202602", "202601", "202512"])


class LuinfoParse(unittest.TestCase):
    """실제 응답(강남구 · 제2종일반주거지역, 2026-10-27). EUC-KR로 옴."""

    def rules(self, name):
        return {r["act_name"]: r for r in luinfo.parse_rules(datagokr.parse((FIX / name).read_bytes()))}

    def test_rules_real(self):
        got = self.rules("luinfo_real_academy.xml")
        self.assertEqual({k: v["allowed"] for k, v in got.items()}, {
            "학원(제2종근린생활시설)": "가능", "학원(교육연구시설)": "가능",
            "무도학원(위락시설)": "불가", "운전학원(자동차관련시설)": "불가", "정비학원(자동차관련시설)": "불가",
        })
        self.assertEqual(got["무도학원(위락시설)"]["law_ref"], "건축법 시행령 별표 1 제16호마목")

    def test_conditions_real(self):
        got = self.rules("luinfo_real_snackbar.xml")
        self.assertEqual(got["휴게음식점(제2종근린생활시설)"]["conditions"], [])
        self.assertTrue(got["휴게음식점(제1종근린생활시설)"]["conditions"][0].startswith("경관관리 등을 위하여"))

    def test_no_hit(self):
        data = ('<response><header><resultCode>0</resultCode></header><body><items><item><UNAME>제2종일반주거지역</UNAME>'
                '<UCODE>UQA122</UCODE><actRegList><luInfoList><NODE_DESC>관련내용 없음</NODE_DESC></luInfoList>'
                '</actRegList></item></items><totalCount>1</totalCount></body></response>').encode("cp949")
        self.assertEqual(luinfo.parse_rules(datagokr.parse(data)), [])

    def test_unknown_reg(self):
        self.assertIsNone(luinfo.allowed_value("???"))
        self.assertEqual(luinfo.allowed_value("금지"), "불가")


@unittest.skipUnless(DSN, "HANPILJI_TEST_DSN 없음")
class Stage1Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = Path(cls.tmp.name) / "config.toml"
        cfg.write_text(
            f'[db]\ndsn = "{DSN}"\n[storage]\nraw_dir = "./raw"\n[pilot]\nsigungu = ["11680"]\n'
            '[law]\noc = "test"\nnames = []\n[rtms]\nmonths = 1\nrefetch_months = 1\n'
            '[luinfo]\nacts = ["휴게음식점"]\n',
            encoding="utf-8",
        )
        cls.cfg = str(cfg)
        os.environ.pop("HANPILJI_DSN", None)
        with psycopg.connect(DSN) as c:
            c.execute("TRUNCATE core.fact_deal, core.fact_rent, core.dim_complex, core.dim_region, core.reg_rule,"
                      " stg.rtms_raw, stg.luinfo_raw, ops.reject_estate, ops.ingest_cursor, ops.ingest_task,"
                      " ops.ingest_run RESTART IDENTITY CASCADE")

    def q(self, sql, *args):
        with psycopg.connect(DSN) as c:
            cur = c.execute(sql, args or None)
            return cur.fetchall() if cur.description else None

    def test_1_rtms_idempotent(self):
        for _ in range(2):  # 두 번 돌려도 같은 건수
            self.assertEqual(rtms.main(["--config", self.cfg], fetch=fixture_fetch), 0)
        self.assertEqual(self.q("SELECT count(*), count(*) FILTER (WHERE is_cancelled) FROM core.fact_deal")[0], (4, 1))
        self.assertEqual(self.q("SELECT count(*) FROM core.fact_rent")[0][0], 8, "똑같은 전월세 2건도 따로 셈(실제 응답)")
        self.assertEqual(self.q("SELECT count(*) FROM core.dim_complex")[0][0], 1, "매매 · 전월세가 한 단지로 묶임")
        self.assertEqual(self.q("SELECT count(*) FROM ops.reject_estate")[0][0], 0)

    def test_1b_month_refresh(self):
        """같은 달을 다시 받았을 때: 응답에서 빠진 거래는 core에서도 빠지고, 똑같은 거래 2건은 따로 셈."""
        trade = datagokr.parse((FIX / "rtms_trade_real.xml").read_bytes())
        rows = datagokr.items(trade)
        def fetch(path, params, rows=rows):
            rows = as_month(rows, str(params["DEAL_YMD"]))
            obj = {"header": {"resultCode": "000"}, "body": {"items": {"item": rows}, "totalCount": len(rows)}}
            return b"<x/>", obj
        twice = [rows[0], rows[0]]   # 첫 거래가 두 번(동일 항목), 나머지는 빠짐
        self.assertEqual(rtms.main(["--config", self.cfg, "--only", "trade"],
                                   fetch=lambda p, q: fetch(p, q, twice)), 0)
        self.assertEqual(self.q("SELECT count(*) FROM core.fact_deal")[0][0], 2)
        self.assertEqual(rtms.main(["--config", self.cfg, "--only", "trade"], fetch=fixture_fetch), 0)
        self.assertEqual(self.q("SELECT count(*) FROM core.fact_deal")[0][0], 4)

    def test_2_complex_price(self):
        self.assertEqual(rtms.main(["--config", self.cfg], fetch=fixture_fetch), 0)  # 혼자 돌려도 되게(멱등)
        rows = self.q("SELECT * FROM mart.q_complex_price('11680', '세곡 푸르지오', 85, 600)")
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r[0], "세곡푸르지오")
        self.assertEqual(r[3], 3, "해제된 거래(74㎡)는 뺌")
        self.assertEqual(r[4], 2_066_666_667)
        this_month = datetime.date.today().replace(day=1)
        self.assertEqual((r[7], r[8]), (this_month.replace(day=24), 2_100_000_000))
        self.assertEqual(r[9], 6, "월세 1건은 전세에서 뺌")
        self.assertEqual(r[10], 845_000_000)
        self.assertEqual(float(r[12]), 40.9, "전세가율 %")
        self.assertEqual(r[-1], 1)

    def test_3_luinfo_rules(self):
        self.assertEqual(luinfo.main(["--config", self.cfg, "--zone", "UQA122"], fetch=fixture_fetch), 0)
        rows = self.q("SELECT act_name, allowed, scope, condition_text FROM mart.q_zoning_check("
                      "'제2종일반주거지역', '휴게음식점', '11680')")
        self.assertEqual([r[:3] for r in rows], [("휴게음식점(제1종근린생활시설)", "가능", "조례(11680)"),
                                                 ("휴게음식점(제2종근린생활시설)", "가능", "조례(11680)")])
        self.assertTrue(rows[0][3].endswith("근거: 건축법 시행령 별표 1 제3호나목"))
        self.assertEqual(self.q("SELECT count(*) FROM ops.reject_estate WHERE source_id = 'dg_luinfo'")[0][0], 0)

    def test_4_no_key_fails_cleanly(self):
        os.environ.pop("DATA_GO_KR_KEY", None)
        self.assertEqual(rtms.main(["--config", self.cfg, "--only", "trade"]), 1)
        err = self.q("SELECT error FROM ops.ingest_task WHERE status = 'failed' ORDER BY task_id DESC LIMIT 1")[0][0]
        self.assertIn("DATA_GO_KR_KEY", err)


if __name__ == "__main__":
    unittest.main()
