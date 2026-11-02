"""스키마 하나로 합친 서버용 SQL 바꾸기(hanpilji.db.rewrite). DB 없이 돔."""
import os
import unittest
from unittest import mock

from hanpilji import db


class Rewrite(unittest.TestCase):
    def test_off_by_default(self):
        with mock.patch.dict(os.environ, {"HANPILJI_DB_SCHEMA": ""}):
            self.assertEqual(db.rewrite("SELECT * FROM core.fact_deal"), "SELECT * FROM core.fact_deal")

    def test_all_four_schemas(self):
        q = "SELECT * FROM mart.q_deal_list(%s) d JOIN core.dim_complex c ON true, ops.ingest_run, stg.rtms_raw"
        self.assertEqual(db.rewrite(q, "hanpilji"),
                         "SELECT * FROM hanpilji.q_deal_list(%s) d JOIN hanpilji.dim_complex c ON true, "
                         "hanpilji.ingest_run, hanpilji.rtms_raw")

    def test_quoted_regclass_and_copy(self):
        self.assertEqual(db.rewrite("SELECT to_regclass('core.geo_building')", "h"), "SELECT to_regclass('h.geo_building')")
        self.assertEqual(db.rewrite("COPY core.fact_house_price (a) FROM STDIN", "h"), "COPY h.fact_house_price (a) FROM STDIN")

    def test_leaves_similar_words(self):
        q = "SELECT c.core_area, x.mart_id, score.v, 'encore.x', d.core FROM t"
        self.assertEqual(db.rewrite(q, "h"), q)

    def test_non_string_untouched(self):
        obj = object()
        self.assertIs(db.rewrite(obj, "h"), obj)


if __name__ == "__main__":
    unittest.main()
