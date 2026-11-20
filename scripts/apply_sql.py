"""SQL 파일을 DB에 넣음. HANPILJI_DB_SCHEMA가 있으면(서버) ops · stg · core · mart를 그 스키마 이름으로 바꿔서 넣음.

    python scripts/apply_sql.py sql/30_trade_area.sql [sql/31_trade_area_geo.sql …]

접속은 HANPILJI_DSN(없으면 config.toml [db] dsn). 파일 하나를 한 트랜잭션으로 넣고, 오류가 나면 그 파일은 되돌리고 멈춤.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hanpilji import config as config_mod  # noqa: E402
from hanpilji import db  # noqa: E402


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    dsn = os.environ.get("HANPILJI_DSN") or config_mod.load("config.toml")["db"]["dsn"]
    with db.connect(dsn) as conn:
        for f in argv:
            sql = Path(f).read_text(encoding="utf-8")
            schema = db.target_schema()
            if schema:   # 서버: PostGIS 함수가 public · extensions에 있어도 찾게
                sql = sql.replace("SET search_path = public, extensions;",
                                  f"SET search_path = {schema}, public, extensions;")
            conn.execute(sql)
            conn.commit()
            print(f"넣음: {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
