"""공공데이터포털 파일 「국토교통부_주택 공시가격 정보」 → core.fact_house_price(공동주택 호별 공시가격).

    python -m hanpilji.collectors.house_price [--sigungu 11680] [--zip 받아 둔.zip] [--force]

전국 한 해치가 CSV 하나(2026년 1,585만 호, 압축 155MB · 풀면 3.5GB)로 올라옴. 압축을 풀지 않고 읽으면서 시범 구 줄만 골라 넣음.
같은 정보를 주던 오픈API(1611000/nsdi/ApartHousingPriceService)는 폐기됨(2026-10-30 확인: NO_OPENAPI_SERVICE_ERROR).
파일은 인증키 · 활용신청 없이 받음. 포털 화면의 「다운로드」 버튼과 같은 순서로 부름(파일 번호 묻기 → 한도 확인 → 받기).
포털에 올라온 파일 번호(atchFileId)를 커서에 남겨서, 새 해 파일이 올라오기 전에는 받지 않고 끝냄(매월 돌려도 됨).
같은 해를 다시 넣으면 그 구 · 그 해 줄을 지우고 새로 넣음(공시 뒤 이의신청으로 값이 고쳐질 수 있어서).
열 이름 · 값은 2026-10-07 수정본(2026년 1월 1일 기준)의 레이아웃 · 실제 파일로 확인함.
"""
import argparse
import csv
import datetime
import http.cookiejar
import io
import json
import re
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile

from hanpilji import config as config_mod
from hanpilji import ingest

SOURCE = "molit_house_price"
SOURCE_LABEL = "국토교통부 주택 공시가격 정보"
PORTAL = "https://www.data.go.kr"
DATASET = "3073746"
KIND = "공동"
COLS = ("기준연도", "법정동코드", "특수지코드", "본번", "부번", "특수지명", "단지명", "동명", "호명", "전용면적", "공시가격",
        "단지코드", "동코드", "호코드", "건축물대장PK")
FIELDS = ("house_kind", "base_year", "complex_code", "dong_code", "ho_code", "lawd_cd", "pnu", "lot_name",
          "complex_name", "dong", "ho", "excl_area", "price", "bld_unit_pk", "source", "asof")


class Portal:
    """포털 화면이 쓰는 주소를 쿠키를 이어 가며 부름(파일 번호를 물은 세션으로 받아야 함)."""

    def __init__(self, opener=None):
        self.opener = opener or urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _open(self, path, data=None, timeout=60):
        req = urllib.request.Request(PORTAL + path, headers={"User-Agent": "hanpilji-collector/0.1",
                                                             "X-Requested-With": "XMLHttpRequest"},
                                     data=urllib.parse.urlencode(data).encode() if data is not None else None)
        return self.opener.open(req, timeout=timeout)

    def latest(self):
        """지금 올라와 있는 파일 → {'atch': 파일 번호, 'sn': 순번, 'name': 데이터 이름, 'updated': 수정일}."""
        page = self._open(f"/data/{DATASET}/fileData.do").read().decode("utf-8", "replace")
        m = re.search(r"fn_fileDataDown\('%s',\s*'(uddi:[0-9a-f-]+)'" % DATASET, page)
        if not m:
            raise RuntimeError("포털 화면에서 내려받기 단추를 찾지 못함(화면이 바뀌었는지 확인)")
        res = json.loads(self._open("/tcs/dss/selectFileDataDownload.do", {
            "publicDataDetailPk": m.group(1), "publicDataPk": DATASET, "atchFileId": "", "fileDetailSn": "1",
            "publicDataTyCode": "PR0051"}).read())
        if not res.get("status"):
            raise RuntimeError(f"파일 번호를 받지 못함: {res.get('error') or res}")
        info = res.get("dataSetFileDetailInfo") or {}
        updated = (info.get("updtDt") or info.get("confmDt") or "")[:10] or None
        return {"atch": res["atchFileId"], "sn": str(res["fileDetailSn"]), "name": info.get("dataNm"), "updated": updated}

    def download(self, f, dest):
        lim = json.loads(self._open("/cmm/cmm/check-limit.json", {"atchFileId": f["atch"], "fileDetailSn": f["sn"]}).read())
        if lim.get("needCaptcha"):
            raise RuntimeError("포털이 내려받기 한도에 닿아 보안문자를 요구함 — 하루 뒤 다시 돌림")
        q = urllib.parse.urlencode({"atchFileId": f["atch"], "fileDetailSn": f["sn"], "dataNm": f["name"] or DATASET})
        with self._open(f"/cmm/cmm/fileDownload.do?{q}", timeout=600) as r, open(dest, "wb") as out:
            shutil.copyfileobj(r, out, 1 << 20)


def pnu(row):
    """법정동코드 + 산(2) · 일반(1) + 본번 4 + 부번 4. 블럭지번(6) · 기타(9) · 가지번(2)은 지번이 아니라 None."""
    if row["특수지코드"] not in ("0", "1") or not row["본번"].isdigit() or int(row["본번"]) == 0:
        return None
    return row["법정동코드"] + ("2" if row["특수지코드"] == "1" else "1") + row["본번"].zfill(4) + row["부번"].zfill(4)


def normalize(row, asof):
    """CSV 한 줄 → fact_house_price 한 줄. 값이 모자라면 None(적재 제외)."""
    try:
        price = int(row["공시가격"])
        key = (str(int(row["단지코드"])), int(row["동코드"]), int(row["호코드"]))
        year = int(row["기준연도"])
    except (KeyError, ValueError):
        return None
    if price <= 0 or not re.fullmatch(r"\d{10}", row.get("법정동코드") or ""):
        return None
    p = pnu(row)
    return {"house_kind": KIND, "base_year": year, "complex_code": key[0], "dong_code": key[1], "ho_code": key[2],
            "lawd_cd": row["법정동코드"], "pnu": p, "lot_name": None if p else (row["특수지명"] or None),
            "complex_name": row["단지명"] or None, "dong": row["동명"] or None, "ho": row["호명"] or None,
            "excl_area": float(row["전용면적"]) if row["전용면적"] else None, "price": price,
            "bld_unit_pk": row["건축물대장PK"] or None, "source": SOURCE_LABEL, "asof": asof}


def data_member(zf):
    """압축 속 데이터 CSV(샘플 · 레이아웃이 아닌 가장 큰 CSV)."""
    csvs = [i for i in zf.infolist() if i.filename.lower().endswith(".csv")]
    if not csvs:
        raise RuntimeError("압축 파일에 CSV가 없음")
    return max(csvs, key=lambda i: i.file_size)


def pick(zip_path, sigungus):
    """압축을 풀지 않고 읽으면서 법정동코드 앞 5자리가 시범 구인 줄만 → {시군구: [줄 dict]}, 헤더."""
    out = {s: [] for s in sigungus}
    with zipfile.ZipFile(zip_path) as zf, zf.open(data_member(zf)) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")
        header = next(csv.reader([text.readline()]))
        missing = [c for c in COLS if c not in header]
        if missing:
            raise RuntimeError(f"원본 열 이름이 바뀜: {missing} 없음")
        i = header.index("법정동코드")
        for line in text:
            # 1,585만 줄을 모두 csv로 풀면 느려서, 따옴표 안 셋째 칸(법정동코드) 앞 5자리만 먼저 봄
            parts = line.split('","', i + 1)
            if len(parts) > i and parts[i][:5] in out:
                row = next(csv.reader([line]))
                out[row[i][:5]].append(dict(zip(header, row)))
    return out, header


def save_picked(raw_dir, name, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writerow(header)
    w.writerows([r.get(h, "") for h in header] for r in rows)
    return ingest.save_raw(raw_dir, "house_price", name, buf.getvalue().encode("utf-8"))


def load(conn, run_id, sigungu, rows, asof):
    """그 구의 그 해 줄을 지우고 새로 넣음 → (넣은 줄, 버린 줄)."""
    good, bad, seen = [], 0, set()
    for row in rows:
        r = normalize(row, asof)
        k = r and (r["base_year"], r["complex_code"], r["dong_code"], r["ho_code"])
        if r is None or k in seen:
            ingest.reject(conn, run_id, SOURCE, "core.fact_house_price", "값 모자람" if r is None else "같은 호가 두 번", row)
            bad += 1
            continue
        seen.add(k)
        good.append(r)
    with conn.cursor() as cur:
        for year in sorted({r["base_year"] for r in good}):
            cur.execute("DELETE FROM core.fact_house_price WHERE house_kind = %s AND base_year = %s AND lawd_cd LIKE %s",
                        (KIND, year, sigungu + "%"))
        with cur.copy(f"COPY core.fact_house_price ({', '.join(FIELDS)}) FROM STDIN") as cp:
            for r in good:
                cp.write_row([r[f] for f in FIELDS])
    return len(good), bad


def main(argv=None, portal=None, today=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--sigungu", action="append", help="시군구 코드 5자리(기본 config [pilot] sigungu)")
    ap.add_argument("--zip", help="받아 둔 압축 파일(주면 포털에서 받지 않음)")
    ap.add_argument("--force", action="store_true", help="이미 넣은 파일이어도 다시 넣음")
    args = ap.parse_args(argv)

    import psycopg
    cfg = config_mod.load(args.config)
    sigungus = args.sigungu or cfg["pilot"]["sigungu"]
    today = today or datetime.date.today()
    portal = portal or Portal()
    with psycopg.connect(cfg["db"]["dsn"]) as conn:
        run_id = ingest.start_run(conn, SOURCE)
        loaded = rejected = 0
        status, note = "done", None
        tmp = tempfile.TemporaryDirectory()
        try:
            f = {"atch": "local:" + args.zip, "updated": None} if args.zip else portal.latest()
            done = {s: ingest.get_cursor(conn, SOURCE, s) for s in sigungus}
            todo = [s for s in sigungus if args.force or done[s] != f["atch"]]
            if not todo:
                note = f"새 파일 없음({f['atch']} 이미 넣음)"
                print(note)
            else:
                zip_path = args.zip
                if not zip_path:
                    zip_path = f"{tmp.name}/house_price.zip"
                    print(f"받는 중: {f['name']} ({f['atch']})")
                    portal.download(f, zip_path)
                asof = datetime.date.fromisoformat(f["updated"]) if f["updated"] else today
                picked, header = pick(zip_path, todo)
                for sgg in todo:
                    rows = picked[sgg]
                    if not rows:
                        raise RuntimeError(f"파일에 시군구 {sgg} 줄이 없음")
                    year = rows[0]["기준연도"]
                    raw_path, sha = save_picked(cfg["storage"]["raw_dir"], f"{year}_{sgg}.csv", header, rows)
                    task = ingest.add_task(conn, run_id, SOURCE, f"{year}:{sgg}", raw_path, sha)
                    n, bad = load(conn, run_id, sgg, rows, asof)
                    ingest.end_task(conn, task, "done")
                    ingest.set_cursor(conn, SOURCE, sgg, f["atch"])
                    conn.commit()
                    loaded, rejected = loaded + n, rejected + bad
                    print(f"{sgg} {year}년 공동주택 공시가격: {n:,}호 (버림 {bad})")
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
