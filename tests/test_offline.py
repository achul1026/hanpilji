"""DB 없이 도는 시험: 숫자 표기 보강 · 법령 응답 파싱."""
import unittest
from pathlib import Path

from hanpilji.collectors import law
from hanpilji.textnorm import percent_aliases, search_text

FIX = Path(__file__).parent / "fixtures"


class TextNorm(unittest.TestCase):
    def test_fraction_to_percent(self):
        self.assertEqual(percent_aliases("연 100분의 5"), ["5%"])
        self.assertEqual(percent_aliases("20분의 1의 금액"), ["5%"])
        self.assertEqual(percent_aliases("100분의 150 이하"), ["150%"])
        self.assertEqual(percent_aliases("1,000분의 3"), ["0.3%"])

    def test_no_fraction_keeps_body(self):
        self.assertEqual(search_text("제1조(목적)"), "제1조(목적)")


class LawParse(unittest.TestCase):
    def test_pick_exact_name(self):
        x = (FIX / "law_search_sample.xml").read_bytes()
        self.assertEqual(law.pick_current(x, "주택임대차보호법")["mst"], "800001")
        self.assertEqual(law.pick_current(x, "주택임대차보호법 시행령")["mst"], "900001")
        self.assertIsNone(law.pick_current(x, "없는 법"))

    def test_parse_articles(self):
        meta, arts, annex = law.parse_law((FIX / "law_service_sample.xml").read_bytes())
        self.assertEqual(meta["name"], "주택임대차보호법 시행령")
        self.assertEqual(len(arts), 3, "장 제목(전문)은 빼고 조문 3개")
        a8 = arts[1]
        self.assertIn("20분의 1", a8["body"])
        self.assertIn("가. 시험용 목", a8["body"], "항 · 호 · 목까지 이어 붙임")
        self.assertEqual(law.article_label(arts[2]["no"], arts[2]["branch"]), "제8조의2")
        self.assertEqual(len(annex), 1)


class LawParseReal(unittest.TestCase):
    """2026-10-22 법제처 실제 응답(OC=test) 그대로 저장한 파일로 확인."""

    def test_pick_real(self):
        x = (FIX / "law_search_real.xml").read_bytes()
        self.assertEqual(law.pick_current(x, "주택임대차보호법")["law_id"], "001248")
        self.assertEqual(law.pick_current(x, "주택임대차보호법 시행령")["kind"], "대통령령")

    def test_parse_real_skips_deleted(self):
        meta, arts, _ = law.parse_law((FIX / "law_service_real.xml").read_bytes())
        self.assertEqual(meta["name"], "주택임대차보호법")
        labels = [law.article_label(a["no"], a["branch"]) for a in arts]
        self.assertNotIn("제5조", labels, "「제5조 삭제」는 빼야 함")
        self.assertIn("제3조의2", labels)
        a7 = arts[labels.index("제7조")]
        self.assertIn("20분의 1", a7["body"])


if __name__ == "__main__":
    unittest.main()
