"""Supabase HTTPS 연결 시험. API는 가짜로 바꿔서 토큰 없이 돎.

검증하는 것 — 프로젝트 ref 읽기 · %s 자리 채우기와 따옴표 처리 · 읽기 전용 트랜잭션 · GPT 계정으로 실행 ·
도구 실행 결과가 psycopg 연결과 같은 모양 · HANPILJI_DB_VIA로 엔진이 이 연결을 고름.
"""
import json
import os
import unittest
from unittest import mock

from hanpilji import supabase
from hanpilji.gpt import engine, tools

DSN = "postgresql://postgres.abcdefghijklmnopqrst:pw@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres"


class Bind(unittest.TestCase):
    def test_ref(self):
        self.assertEqual(supabase.project_ref(DSN), "abcdefghijklmnopqrst")
        self.assertEqual(supabase.project_ref("postgresql://postgres:pw@db.abcdefghijklmnopqrst.supabase.co/postgres"),
                         "abcdefghijklmnopqrst")
        with self.assertRaises(supabase.ApiError):
            supabase.project_ref("postgresql://localhost/hanpilji")

    def test_literals(self):
        self.assertEqual(supabase.bind("SELECT %s, %s, %s, %s, %s", ("a'b", 3, None, True, 84.5)),
                         "SELECT 'a''b', 3, NULL, true, 84.5")
        self.assertEqual(supabase.bind("SELECT '100%%', %s", ("x",)), "SELECT '100%', 'x'")
        self.assertEqual(supabase.bind("SELECT %s", ("\\'; DROP TABLE x; --",)), "SELECT '\\''; DROP TABLE x; --'")

    def test_arity(self):
        with self.assertRaises(ValueError):
            supabase.bind("SELECT %s, %s", ("a",))
        with self.assertRaises(ValueError):
            supabase.bind("SELECT %s", ("a", "b"))
        with self.assertRaises(ValueError):
            supabase.literal("a\x00b")
        with self.assertRaises(TypeError):
            supabase.literal(object())


class Conn(unittest.TestCase):
    def test_tool_through_api(self):
        rows = [{"r": {"complex_name": "도곡렉슬", "trade_count": 21, "row_total": 1,
                       "source": "국토교통부", "asof": "2026-10-27"}}]
        with mock.patch.object(supabase, "query", return_value=rows) as q:
            out = tools.run(supabase.SupabaseConn(ref="abcdefghijklmnopqrst"), "q_complex_price",
                            {"name": "도곡'렉슬", "area_m2": 84})
        sql = q.call_args.args[0]
        self.assertTrue(sql.startswith("SET TRANSACTION READ ONLY;"))
        self.assertIn("SET LOCAL ROLE hanpilji_gpt;", sql)
        self.assertIn("statement_timeout = 5000", sql)
        self.assertIn("SELECT row_to_json(t) AS r FROM (SELECT * FROM mart.q_complex_price("
                      "'11680'::text, '도곡''렉슬'::text, 84::numeric, 12::int)) t", sql)
        self.assertEqual(out["rows"], [{"complex_name": "도곡렉슬", "trade_count": 21}])
        self.assertEqual((out["row_total"], out["sources"], out["asof"]), (1, ["국토교통부"], "2026-10-27"))

    def test_empty(self):
        with mock.patch.object(supabase, "query", return_value=[]):
            out = tools.run(supabase.SupabaseConn(ref="abcdefghijklmnopqrst"), "q_law_search", {"query": "용적률"})
        self.assertEqual((out["shown"], out["row_total"], out["rows"]), (0, 0, []))

    def test_request_shape(self):
        seen = {}

        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def read(self):
                return b'[{"n": 1}]'

        def fake_open(req, timeout):
            seen["url"], seen["body"] = req.full_url, json.loads(req.data)
            seen["auth"] = req.get_header("Authorization")
            return Resp()

        with mock.patch.dict(os.environ, {"SUPABASE_ACCESS_TOKEN": "sbp_test"}), \
                mock.patch("urllib.request.urlopen", fake_open):
            self.assertEqual(supabase.query("SELECT 1", read_only=True, ref="abcdefghijklmnopqrst"), [{"n": 1}])
        self.assertEqual(seen["url"], "https://api.supabase.com/v1/projects/abcdefghijklmnopqrst/database/query")
        self.assertEqual(seen["body"], {"query": "SELECT 1", "read_only": True})
        self.assertEqual(seen["auth"], "Bearer sbp_test")

    def _flaky(self, errors, idempotent):
        """errors를 차례로 낸 뒤 성공하는 가짜 API로 query를 부르고 (결과 또는 예외, 부른 횟수)를 돌려줌."""
        import io
        import urllib.error
        calls = []

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        def fake_open(req, timeout):
            calls.append(1)
            if len(calls) <= len(errors):
                err = errors[len(calls) - 1]
                raise urllib.error.HTTPError(req.full_url, err, "x", {}, io.BytesIO(b"")) if isinstance(err, int) else err
            return Resp(b'[{"n": 1}]')

        with mock.patch.dict(os.environ, {"SUPABASE_ACCESS_TOKEN": "sbp_test"}), \
                mock.patch("urllib.request.urlopen", fake_open), mock.patch("time.sleep"):
            try:
                out = supabase.query("SELECT 1", ref="abcdefghijklmnopqrst", idempotent=idempotent)
            except supabase.ApiError as e:
                out = e
        return out, len(calls)

    def test_retry_rules(self):
        # 실행 전에 막힌 429 · 503은 늘 다시 보냄
        self.assertEqual(self._flaky([429, 503], idempotent=False), ([{"n": 1}], 3))
        # 끊김 · 502 · 504는 이미 실행됐을 수 있어 조회만 다시 보냄
        for err in (ConnectionResetError(104, "reset"), 502, 504):
            out, n = self._flaky([err], idempotent=False)
            self.assertIsInstance(out, supabase.Ambiguous)
            self.assertEqual(n, 1)
            self.assertEqual(self._flaky([err], idempotent=True), ([{"n": 1}], 2))
        out, n = self._flaky([400], idempotent=True)
        self.assertNotIsInstance(out, supabase.Ambiguous)
        self.assertEqual(n, 1)

    def test_engine_picks_api(self):
        with mock.patch.dict(os.environ, {"HANPILJI_DB_VIA": "supabase-api", "HANPILJI_DSN": DSN}):
            conn = engine.connect({"db": {"dsn": DSN}})
        self.assertIsInstance(conn, supabase.SupabaseConn)
        self.assertEqual(conn.ref, "abcdefghijklmnopqrst")


if __name__ == "__main__":
    unittest.main()
