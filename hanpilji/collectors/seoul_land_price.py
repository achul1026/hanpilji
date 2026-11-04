"""서울 열린데이터광장 「서울시 개별공시지가 정보」(OA-1180) 파일 → core.fact_land_price(서울 전체 필지).

    python -m hanpilji.collectors.seoul_land_price [--years 1] [--year 2026] [--zip 받아 둔.zip] [--force]

해마다 zip 하나(공시지가_YYYY년.zip, 압축 8MB · 풀면 CSV 110MB, 2026년 88만 9천 필지)가 올라옴. 인증키 없이 받음.
이용허락: 공공누리 1유형(출처표시, 상업적 이용 · 변경 가능). 원본은 부동산정보광장(KOREPS).
CSV는 CP949, 열은 시도명 · 시군구명 · 법정동명 · 토지코드(PNU 19자리) · 공시지가(원/㎡) · 시군구코드 · 법정동코드 ·
필지구분코드 · 필지구분명 · 본번 · 부번 · 기준년도 · 기준년월 (2026-11-04 실제 파일로 확인).
필지구분 1(토지) · 2(임야)만 넣음. 가지번 · 블럭지번 · 기타지번(코드 3~9, 0.2%)은 PNU가 아니라 뺌.
필지(core.dim_parcel) · 법정동(core.dim_region)이 없으면 같이 만듦(법정동 이름도 채워짐).
같은 필지 · 같은 해를 V-World 등 다른 수집처가 이미 넣었으면 그대로 둠(이 수집처 줄만 고침).
화면의 파일 목록에서 해마다 파일 번호(seq)와 수정일을 읽어, 바뀐 해만 다시 받음(커서).
"""
import argparse
import csv
import datetime
import io
import re
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile

from hanpilji import db, config as config_mod
from hanpilji import ingest

SOURCE = "seoul_land_price"
SOURCE_LABEL = "서울시 개별공시지가 정보(서울 열린데이터광장)"
SOURCE_ROW = ("서울시 개별공시지가 정보", "서울특별시(부동산정보광장)", "file", None, "CP949", "매년",
              "공공누리 1유형(출처표시)", "https://data.seoul.go.kr/dataList/OA-1180/F/1/datasetView.do")
LIST_URL = "https://data.seoul.go.kr/dataList/OA-1180/F/1/datasetView.do"
DOWNLOAD_URL = "https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do?&useCache=false"
UA = {"User-Agent": "hanpilji-collector/0.1"}
COLS = ("토지코드", "공시지가(원/㎡)", "시군구명", "법정동명", "필지구분코드", "본번", "부번", "기준년도", "기준년월")
LEDGER = {"1": "토지", "2": "임야"}


# ── 파일 목록 · 받기 ──

def parse_list(page):
    """화면 HTML → {연도: {'seq': 번호, 'updated': 'YYYY-MM-DD' 또는 None}}."""
    out = {}
    for row in re.findall(r"<tr.*?</tr>", page, flags=re.S):
        m_seq = re.search(r"downloadFile\('(\d+)'\)", row)
        m_year = re.search(r"공시지가_(\d{4})년\.zip", row)
        if not (m_seq and m_year):
            continue
        m_day = re.search(r"(\d{4})\.(\d{2})\.(\d{2})\.", re.sub(r"<[^>]+>", " ", row))
        out[int(m_year.group(1))] = {"seq": m_seq.group(1),
                                     "updated": "-".join(m_day.groups()) if m_day else None}
    return out


def list_files(opener=urllib.request.urlopen):
    page = opener(urllib.request.Request(LIST_URL, headers=UA), timeout=60).read().decode("utf-8", "replace")
    files = parse_list(page)
    if not files:
        raise RuntimeError("파일 목록을 찾지 못함(화면이 바뀌었는지 확인)")
    return files


def download(seq, dest, opener=urllib.request.urlopen):
    data = urllib.parse.urlencode({"infId": "OA-1180", "seqNo": "", "seq": seq, "infSeq": "3"}).encode()
    with opener(urllib.request.Request(DOWNLOAD_URL, data=data, headers=UA), timeout=600) as r, open(dest, "wb") as out:
        while chunk := r.read(1 << 20):
            out.write(chunk)
    if not zipfile.is_zipfile(dest):
        raise RuntimeError(f"zip이 아닌 응답(seq {seq})")


# ── 읽기 ──

def read_rows(zip_path):
    """압축 속 CSV 줄을 dict로 하나씩. 열 이름이 바뀌었으면 멈춤."""
    with zipfile.ZipFile(zip_path) as zf:
        member = max((i for i in zf.infolist() if i.filename.lower().endswith(".csv")), key=lambda i: i.file_size)
        with zf.open(member) as raw:
            reader = csv.DictReader(io.TextIOWrapper(raw, encoding="cp949", newline=""))
            missing = [c for c in COLS if c not in (reader.fieldnames or [])]
            if missing:
                raise RuntimeError(f"원본 열 이름이 바뀜: {missing} 없음")
            yield from reader


def jibun(row):
    bon, bu = int(row["본번"]), int(row["부번"])
    return ("산 " if row["필지구분코드"] == "2" else "") + str(bon) + (f"-{bu}" if bu else "")


def normalize(row):
    """한 줄 → (pnu, 해, 단가, 법정동 10자리, 시군구명, 동이름, 지번, 대장 구분) 또는 (None, 이유)."""
    pnu, price = row["토지코드"], row["공시지가(원/㎡)"]
    kind = row["필지구분코드"]
    if kind not in LEDGER or not re.fullmatch(r"\d{19}", pnu) or pnu[10] != kind:
        return None, "PNU가 아닌 지번(가지번 · 블럭 · 기타)"
    if not price.isdigit() or int(price) <= 0:
        return None, "공시지가 없음"
    if not (row["본번"].isdigit() and row["부번"].isdigit()):
        return None, "본번 · 부번 형식 오류"
    return (pnu, int(row["기준년도"]), int(price), pnu[:10], row["시군구명"], row["법정동명"], jibun(row),
            LEDGER[kind]), None


# ── 적재 ──

def load_year(conn, run_id, rows, asof):
    """한 해치를 임시 표에 COPY한 뒤 법정동 · 필지 · 공시지가로 옮김 → (넣은 줄, 버린 줄)."""
    loaded = rejected = 0
    samples = []   # COPY 중에는 같은 연결로 다른 SQL을 못 보내서, 뺀 줄 견본은 모아 두었다가 뒤에 남김
    with conn.cursor() as cur:
        cur.execute("""CREATE TEMP TABLE land_in (pnu varchar(25), base_year smallint, price bigint, lawd_cd char(10),
                           sigungu text, emd text, jibun text, ledger text) ON COMMIT DROP""")
        with cur.copy("COPY land_in FROM STDIN") as cp:
            for row in rows:
                v, why = normalize(row)
                if v is None:
                    rejected += 1
                    if len(samples) < 200:   # 이유만 보이면 되니 몇 줄만 남김
                        samples.append((why, row))
                    continue
                cp.write_row(v)
                loaded += 1
        for why, row in samples:
            ingest.reject(conn, run_id, SOURCE, "core.fact_land_price", why, row)
        cur.execute("""INSERT INTO core.dim_region (lawd_cd, sido, sigungu, emd, source, asof)
                       SELECT DISTINCT ON (lawd_cd) lawd_cd, '서울특별시', sigungu, emd, %s, %s FROM land_in
                       ON CONFLICT (lawd_cd) DO UPDATE SET emd = coalesce(core.dim_region.emd, excluded.emd),
                           sigungu = coalesce(core.dim_region.sigungu, excluded.sigungu),
                           sido = coalesce(core.dim_region.sido, excluded.sido)""", (SOURCE_LABEL, asof))
        cur.execute("""INSERT INTO core.dim_parcel (pnu, lawd_cd, jibun, ledger_type, source, asof)
                       SELECT pnu, lawd_cd, jibun, ledger, %s, %s FROM land_in
                       ON CONFLICT (pnu) DO NOTHING""", (SOURCE_LABEL, asof))
        cur.execute("""INSERT INTO core.fact_land_price (pnu, base_year, is_standard, price_per_m2, source, asof)
                       SELECT pnu, base_year, false, price, %s, %s FROM land_in
                       ON CONFLICT (pnu, base_year, is_standard) DO UPDATE
                           SET price_per_m2 = excluded.price_per_m2, asof = excluded.asof
                           WHERE core.fact_land_price.source = excluded.source""", (SOURCE_LABEL, asof))
    return loaded, rejected


def ensure_source(conn):
    conn.execute("""INSERT INTO ops.ingest_source (source_id, name, operator, method, daily_limit, encoding, cycle, license, url)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (source_id) DO NOTHING""",
                 (SOURCE, *SOURCE_ROW))
    conn.commit()


def main(argv=None, lister=list_files, fetch=download, today=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--years", type=int, default=1, help="최근 몇 해(기본 1: 올해 공시분)")
    ap.add_argument("--year", type=int, action="append", help="이 해만(여러 번 줄 수 있음)")
    ap.add_argument("--zip", help="받아 둔 압축 파일 하나(--year 하나와 함께)")
    ap.add_argument("--force", action="store_true", help="이미 넣은 파일이어도 다시 넣음")
    args = ap.parse_args(argv)
    cfg = config_mod.load(args.config)
    today = today or datetime.date.today()
    with db.connect(cfg["db"]["dsn"]) as conn:
        ensure_source(conn)
        run_id = ingest.start_run(conn, SOURCE)
        loaded = rejected = 0
        status, note = "done", None
        tmp = tempfile.TemporaryDirectory()
        try:
            if args.zip:
                if not args.year or len(args.year) != 1:
                    raise RuntimeError("--zip은 --year 하나와 함께")
                files = {args.year[0]: {"seq": "local", "updated": None}}
            else:
                files = lister()
            years = args.year or sorted(files, reverse=True)[: args.years]
            for y in sorted(years):
                f = files.get(y)
                if not f:
                    raise RuntimeError(f"{y}년 파일이 목록에 없음")
                mark = f"{f['seq']}:{f['updated']}"
                if not args.force and ingest.get_cursor(conn, SOURCE, str(y)) == mark:
                    print(f"{y}년: 바뀐 파일 없음")
                    continue
                path = args.zip or f"{tmp.name}/{y}.zip"
                if not args.zip:
                    print(f"{y}년 받는 중(seq {f['seq']})")
                    fetch(f["seq"], path)
                asof = datetime.date.fromisoformat(f["updated"]) if f["updated"] else today
                task = ingest.add_task(conn, run_id, SOURCE, str(y))
                n, bad = load_year(conn, run_id, read_rows(path), asof)
                ingest.end_task(conn, task, "done")
                ingest.set_cursor(conn, SOURCE, str(y), mark)
                conn.commit()
                loaded, rejected = loaded + n, rejected + bad
                print(f"{y}년 서울 개별공시지가: {n:,}필지 (뺌 {bad:,})")
        except Exception as e:
            conn.rollback()
            status, note = "failed", str(e)[:500]
            print(f"failed: {e}", file=sys.stderr)
        finally:
            tmp.cleanup()
        ingest.finish_run(conn, run_id, status, loaded, rejected, note)
    return 0 if status == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
