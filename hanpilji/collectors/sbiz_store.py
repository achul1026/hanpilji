"""소상공인시장진흥공단 상가(상권)정보(공공데이터포털 파일 15083033) → core.ref_store(서울 점포).

    python -m hanpilji.collectors.sbiz_store [--zip 받아 둔.zip] [--force]

분기마다 전국 zip 하나(2026-09 기준 압축 349MB · 시도별 CSV 17개, 서울 약 55만 점포)가 올라옴. 로그인 · 인증키 없이 받음
(포털 화면의 「다운로드」와 같은 순서: 파일 번호 묻기 → 한도 확인 → 받기. 2026-11-20 실제로 받아 확인).
CSV는 UTF-8(BOM), 열 39개. 지번코드(19자리)가 필지 PNU, 행정동코드(8자리)는 상권분석 · 생활인구와 같음.
이용허락범위 제한 없음. 서울 파일만 읽고, 새 파일에 없는 점포(폐업 등)는 지움. 같은 파일(이름 · 해시)이면 넣지 않음.
"""
import argparse
import csv
import datetime
import hashlib
import http.cookiejar
import io
import json
import re
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile

from hanpilji import db, config as config_mod
from hanpilji import ingest

SOURCE = "sbiz_store"
SOURCE_LABEL = "소상공인시장진흥공단 상가(상권)정보"
PK = "15083033"
PORTAL = "https://www.data.go.kr"
UA = {"User-Agent": "hanpilji-collector/0.1"}
COLS = {   # 원본 열 → 표 칸
    "상가업소번호": "store_id", "상호명": "name", "지점명": "branch",
    "상권업종대분류코드": "ind_l_cd", "상권업종대분류명": "ind_l_nm", "상권업종중분류코드": "ind_m_cd",
    "상권업종중분류명": "ind_m_nm", "상권업종소분류코드": "ind_s_cd", "상권업종소분류명": "ind_s_nm",
    "표준산업분류코드": "ksic_cd", "표준산업분류명": "ksic_nm", "시군구코드": "sigungu_cd", "행정동코드": "adm_cd",
    "행정동명": "adm_nm", "법정동코드": "lawd_cd", "지번코드": "pnu", "지번주소": "jibun_addr", "도로명주소": "road_addr",
    "건물관리번호": "bld_mgt_no", "건물명": "bld_nm", "층정보": "floor", "호정보": "unit", "경도": "lon", "위도": "lat",
}
FIELDS = list(COLS.values())


class Portal:
    """공공데이터포털 파일 받기(쿠키 유지)."""

    def __init__(self, opener=None):
        self.opener = opener or urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _open(self, path, data=None, timeout=60):
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        return self.opener.open(urllib.request.Request(PORTAL + path, data=body, headers=UA), timeout=timeout)

    def latest(self):
        """→ {'detail_pk', 'name', 'atch', 'sn'}. name 예: 소상공인시장진흥공단_상가(상권)정보_20260930."""
        page = self._open(f"/data/{PK}/fileData.do").read().decode("utf-8", "replace")
        m = re.search(r"fn_fileDataDown\('(\d+)',\s*'([^']+)',\s*'[^']*',\s*'(\d+)'", page)
        if not m:
            raise RuntimeError("파일 내려받기 단추를 찾지 못함(화면이 바뀌었는지 확인)")
        info = json.loads(self._open("/tcs/dss/selectFileDataDownload.do", {
            "publicDataDetailPk": m.group(2), "publicDataPk": m.group(1), "atchFileId": "",
            "fileDetailSn": m.group(3), "publicDataTyCode": "PR0051"}).read())
        if not info.get("status"):
            raise RuntimeError(f"파일 정보를 못 받음: {info.get('error') or '이유 없음'}")
        return {"detail_pk": m.group(2), "name": info["dataSetFileDetailInfo"].get("dataNm") or "",
                "atch": info["atchFileId"], "sn": str(info["fileDetailSn"])}

    def download(self, f, dest):
        lim = json.loads(self._open("/cmm/cmm/check-limit.json", {"atchFileId": f["atch"], "fileDetailSn": f["sn"]}).read())
        if lim.get("needCaptcha"):
            raise RuntimeError("포털이 자동 입력 방지 문자를 요구함(하루 내려받기 한도). 내일 다시 하거나 --zip으로 넣을 것")
        q = urllib.parse.urlencode({"atchFileId": f["atch"], "fileDetailSn": f["sn"], "dataNm": f["name"]})
        with self._open(f"/cmm/cmm/fileDownload.do?{q}", timeout=1800) as r, open(dest, "wb") as out:
            while chunk := r.read(1 << 20):
                out.write(chunk)
        if not zipfile.is_zipfile(dest):
            raise RuntimeError("zip이 아닌 응답")


def base_ym(name):
    """파일 이름 끝의 날짜(20260930) 또는 CSV 이름의 연월(202609) → '202609'."""
    m = re.search(r"(20\d{2})(\d{2})(\d{2})?(?:\D*$)", name or "")
    return m.group(1) + m.group(2) if m else None


def seoul_member(zf):
    """서울 CSV(이름에 「서울」). zip 이름이 깨진 한글(#Uc11c#Uc6b8)일 때도 찾음."""
    for i in zf.infolist():
        n = i.filename
        if n.lower().endswith(".csv") and ("서울" in n or "#Uc11c#Uc6b8" in n):
            return i
    raise RuntimeError("압축 안에 서울 CSV가 없음")


def read_rows(zf, member):
    with zf.open(member) as raw:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
        missing = [c for c in COLS if c not in (reader.fieldnames or [])]
        if missing:
            raise RuntimeError(f"원본 열 이름이 바뀜: {missing} 없음")
        yield from reader


def normalize(row):
    """한 줄 → 표 칸 dict 또는 (None, 이유)."""
    v = {COLS[k]: (row.get(k) or "").strip() or None for k in COLS}
    if not v["store_id"] or not v["name"]:
        return None, "상가업소번호 · 상호명 없음"
    if v["sigungu_cd"] and not v["sigungu_cd"].startswith("11"):
        return None, "서울이 아님"
    if v["pnu"] and not re.fullmatch(r"\d{19}", v["pnu"]):
        v["pnu"] = None
    for k in ("lon", "lat"):
        try:
            v[k] = round(float(v[k]), 7) if v[k] else None
        except ValueError:
            v[k] = None
    if v["lon"] is not None and not (124 < v["lon"] < 132 and 33 < (v["lat"] or 0) < 39):
        v["lon"] = v["lat"] = None
    return v, None


def load(conn, run_id, rows, ym, asof):
    loaded = rejected = 0
    samples = []
    with conn.cursor() as cur:
        cur.execute(f"CREATE TEMP TABLE store_in ({', '.join(f + ' text' for f in FIELDS)}) ON COMMIT DROP")
        with cur.copy(f"COPY store_in ({', '.join(FIELDS)}) FROM STDIN") as cp:
            seen = set()
            for row in rows:
                v, why = normalize(row)
                if v is None:
                    rejected += 1
                    if len(samples) < 100:
                        samples.append((why, row))
                    continue
                if v["store_id"] in seen:
                    continue
                seen.add(v["store_id"])
                cp.write_row([None if v[f] is None else str(v[f]) for f in FIELDS])
                loaded += 1
        for why, row in samples:
            ingest.reject(conn, run_id, SOURCE, "core.ref_store", why, row)
        cols = ", ".join(FIELDS)
        sel = ", ".join(f"{f}::numeric" if f in ("lon", "lat") else f for f in FIELDS)
        upd = ", ".join(f"{f} = excluded.{f}" for f in FIELDS if f != "store_id")
        cur.execute(f"""INSERT INTO core.ref_store ({cols}, base_ym, source, asof)
                        SELECT {sel}, %s, %s, %s FROM store_in
                        ON CONFLICT (store_id) DO UPDATE SET {upd}, base_ym = excluded.base_ym, asof = excluded.asof""",
                    (ym, SOURCE_LABEL, asof))
        cur.execute("CREATE INDEX ON store_in (store_id)")
        cur.execute("ANALYZE store_in")
        cur.execute("""DELETE FROM core.ref_store s WHERE s.source = %s
                       AND NOT EXISTS (SELECT 1 FROM store_in i WHERE i.store_id = s.store_id)""", (SOURCE_LABEL,))
    return loaded, rejected


def main(argv=None, portal=None, today=None, min_rows=100000):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--zip", help="받아 둔 전국 zip(포털에서 손으로 받은 것)")
    ap.add_argument("--force", action="store_true", help="이미 넣은 파일이어도 다시 넣음")
    args = ap.parse_args(argv)
    cfg = config_mod.load(args.config)
    today = today or datetime.date.today()
    portal = portal or Portal()
    with db.connect(cfg["db"]["dsn"]) as conn:
        run_id = ingest.start_run(conn, SOURCE)
        loaded = rejected = 0
        status, note = "done", None
        tmp = tempfile.TemporaryDirectory()
        try:
            if args.zip:
                path, name = args.zip, args.zip
            else:
                f = portal.latest()
                name = f["name"]
                if not args.force and ingest.get_cursor(conn, SOURCE, "file") == name:
                    note = f"같은 파일({name})이라 건너뜀"
                    raise StopIteration
                path = f"{tmp.name}/store.zip"
                print(f"{name} 받는 중")
                portal.download(f, path)
            sha = hashlib.sha256()
            with open(path, "rb") as fh:
                while chunk := fh.read(1 << 20):
                    sha.update(chunk)
            sha = sha.hexdigest()
            if not args.force and ingest.get_cursor(conn, SOURCE, "sha256") == sha:
                note = "같은 내용이라 넣지 않음"
                ingest.set_cursor(conn, SOURCE, "file", name)
                raise StopIteration
            with zipfile.ZipFile(path) as zf:
                member = seoul_member(zf)
                ym = base_ym(name) or base_ym(member.filename)
                if not ym:
                    raise RuntimeError("기준 연월을 파일 이름에서 못 찾음")
                loaded, rejected = load(conn, run_id, read_rows(zf, member), ym, today)
            if loaded < min_rows:   # 서울은 50만 곳이 넘음. 크게 모자라면 잘린 파일로 보고 넣지도 지우지도 않음
                raise RuntimeError(f"서울 점포가 {loaded}곳뿐이라 넣지 않음(파일이 잘렸는지 확인)")
            ingest.set_cursor(conn, SOURCE, "file", name)
            ingest.set_cursor(conn, SOURCE, "sha256", sha)
            conn.commit()
        except StopIteration:
            conn.commit()
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            status, note = "failed", f"{type(e).__name__}: {e}"
        finally:
            tmp.cleanup()
        ingest.finish_run(conn, run_id, status, loaded, rejected, note)
        print(f"상가(상권)정보: {status} · 넣은 점포 {loaded} · 뺀 줄 {rejected}" + (f" · {note}" if note else ""))
    return 1 if status == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
