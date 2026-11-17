"""건축HUB 건축물대장 대용량 파일(zip) → core.dim_building · dim_parcel · bridge_* · dim_complex.

    python -m hanpilji.collectors.bld_file --dir /data/hub [--sigungu seoul] [--dry-run]

API(hanpilji.collectors.bld)는 단지 PNU마다 4번씩 불러 아파트 · 오피스텔만 받음. 빌라(연립다세대) · 단독 · 상가까지
모든 건물을 받으려면 건축HUB(https://www.hub.go.kr)의 월간 전국 파일을 내려받아 이걸로 올림(로그인 · 이용 목적 선택이
필요해 자동으로 받지 않음. 매월 21일 이후 새 달 파일).

폴더에 둘 zip 4개(이름은 그대로 두면 됨. 안의 파일 이름 mart_djy_XX.txt로 가림):
  표제부(mart_djy_03) · 총괄표제부(02) · 지역지구구역(10) · 부속지번(05)
파일 모양(2026-09월 파일로 확인, 2026-11-17): UTF-8, 구분자 「|」, 머리줄 없음. 열 순서는 API 응답 항목 순서와 같아서
아래 열 이름을 API 이름으로 붙이고 API 수집기의 load()로 그대로 올림(정리 규칙이 하나로 유지됨).

- 고른 시군구의 줄만 읽어 필지(PNU)마다 모은 뒤 500필지씩 한 트랜잭션으로 올림. 서울(표제부 58만 줄 · 지역지구 73만 줄,
  2026-09월)은 줄을 글자 그대로 들고 있어 메모리 1GB 안팎. 서버에서는 docker --memory로 상한을 둠.
- zip 4개가 다 있어야 하고(빠지면 그 값을 비워 버림), 열 수가 다른 줄이 하나라도 있으면 쓰기 전에 멈춤.
- API로 받은 필지(dg_bld_hub 커서)는 파일 날짜 뒤에 받았고 표제부가 실제로 왔으면 건너뜀: API 쪽이 더 최근 값이고
  원본(stg.bld_raw)도 있음. 필지마다 그 자리에서 확인해 적재 중 API 배치가 받은 필지도 덮지 않음. --include-api면 덮음.
- zip 4개의 날짜가 다르면(다른 달 파일이 섞임) 멈춤. 이미 올린 파일(커서 hub_bld · file)보다 예전 파일이면 멈춤
  (--allow-older로 되돌릴 수 있음).
- 지번이 없는 블록 대장(택지지구 「○○동 블록」)은 필지를 정할 수 없어 세기만 하고 건너뜀(API 수집기가 단지별로 찾음).
- 다시 돌려도 같은 값으로 덮을 뿐이라 중간에 멈추면 처음부터 다시 돌리면 됨.
"""
import argparse
import datetime
import io
import re
import sys
import zipfile
from pathlib import Path

from hanpilji import db, config as config_mod
from hanpilji import ingest, regions
from hanpilji.collectors import bld

SOURCE = "hub_bld"
COMMIT_EVERY = 500

_LOC = ["sigunguCd", "bjdongCd", "platGbCd", "bun", "ji", "splotNm", "block", "lot"]
_NA = ["naRoadCd", "naBjdongCd", "naUgrndCd", "naMainBun", "naSubBun"]
_REG = ["mgmBldrgstPk", "regstrGbCd", "regstrGbCdNm", "regstrKindCd", "regstrKindCdNm"]
_PARK = ["indrMechUtcnt", "indrMechArea", "oudrMechUtcnt", "oudrMechArea",
         "indrAutoUtcnt", "indrAutoArea", "oudrAutoUtcnt", "oudrAutoArea"]
_PMS = ["pmsDay", "stcnsDay", "useAprDay", "pmsnoYear", "pmsnoKikCd", "pmsnoKikCdNm", "pmsnoGbCd", "pmsnoGbCdNm",
        "hoCnt", "engrGrade", "engrRat", "engrEpi", "gnBldGrade", "gnBldCert", "itgBldGrade", "itgBldCert", "crtnDay"]

# 파일 안 이름 → (API 기능, 열 이름). 열 수는 2026-09월 파일과 맞춰 봄(표제부 77 · 총괄 64 · 지역지구 18 · 부속지번 33)
LAYOUTS = {
    "mart_djy_03": ("getBrTitleInfo", _REG + ["platPlc", "newPlatPlc", "bldNm"] + _LOC + ["bylotCnt"] + _NA + [
        "dongNm", "mainAtchGbCd", "mainAtchGbCdNm", "platArea", "archArea", "bcRat", "totArea", "vlRatEstmTotArea",
        "vlRat", "strctCd", "strctCdNm", "etcStrct", "mainPurpsCd", "mainPurpsCdNm", "etcPurps", "roofCd",
        "roofCdNm", "etcRoof", "hhldCnt", "fmlyCnt", "heit", "grndFlrCnt", "ugrndFlrCnt", "rideUseElvtCnt",
        "emgenUseElvtCnt", "atchBldCnt", "atchBldArea", "totDongTotArea"] + _PARK + _PMS + [
        "rserthqkDsgnApplyYn", "rserthqkAblty"]),
    "mart_djy_02": ("getBrRecapTitleInfo", _REG + ["newOldRegstrGbCd", "newOldRegstrGbCdNm", "platPlc", "newPlatPlc",
        "bldNm"] + _LOC + ["bylotCnt"] + _NA + [
        "platArea", "archArea", "bcRat", "totArea", "vlRatEstmTotArea", "vlRat", "mainPurpsCd", "mainPurpsCdNm",
        "etcPurps", "hhldCnt", "fmlyCnt", "mainBldCnt", "atchBldCnt", "atchBldArea", "totPkngCnt"] + _PARK + _PMS),
    "mart_djy_10": ("getBrJijiguInfo", ["mgmBldrgstPk", "platPlc", "newPlatPlc"] + _LOC + [
        "jijiguGbCd", "jijiguGbCdNm", "jijiguCd", "jijiguCdNm", "reprYn", "etcJijigu", "crtnDay"]),
    "mart_djy_05": ("getBrAtchJibunInfo", _REG + ["platPlc", "newPlatPlc", "bldNm"] + _LOC + _NA + [
        "atchRegstrGbCd", "atchRegstrGbCdNm", "atchSigunguCd", "atchBjdongCd", "atchPlatGbCd", "atchBun", "atchJi",
        "atchSplotNm", "atchBlock", "atchLot", "atchEtcJibunNm", "crtnDay"]),
}
SIGUNGU_AT = {name: cols.index("sigunguCd") for name, (_, cols) in LAYOUTS.items()}


def find_files(folder):
    """폴더의 zip에서 기능별 (zip 경로, 안 파일 이름, 파일 날짜). 같은 기능이 둘이면 ValueError(어느 달인지 모름)."""
    out = {}
    for z in sorted(Path(folder).glob("*.zip")):
        with zipfile.ZipFile(z) as f:
            for info in f.infolist():
                m = re.search(r"(mart_djy_\d\d)\.txt$", info.filename)
                if not m or m[1] not in LAYOUTS:
                    continue
                op = LAYOUTS[m[1]][0]
                if op in out:
                    raise ValueError(f"{op} 파일이 둘: {out[op][0].name} · {z.name} — 한 달 치만 두세요")
                out[op] = (z, info.filename, datetime.date(*info.date_time[:3]))
    return out


def to_row(name, line):
    """한 줄 → API 항목 모양 dict. 열 수가 다르면 None(파일 모양이 바뀜)."""
    cols = LAYOUTS[name][1]
    vals = line.rstrip("\r\n").split("|")
    return dict(zip(cols, vals)) if len(vals) == len(cols) else None


def read(zpath, inner, sigungus):
    """고른 시군구의 줄만 → ({PNU: [줄]}, 블록 대장 줄 수, 열 수가 다른 줄 수)."""
    name = re.search(r"(mart_djy_\d\d)", inner)[1]
    at, want = SIGUNGU_AT[name], set(sigungus)
    groups, block, bad = {}, 0, 0
    with zipfile.ZipFile(zpath) as f, f.open(inner) as raw:
        for line in io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline=""):
            vals = line.split("|", at + 1)
            if len(vals) <= at or vals[at] not in want:
                continue
            row = to_row(name, line)
            if row is None:
                bad += 1
                continue
            pnu = bld.item_pnu(row)
            if pnu is None:
                block += 1
                continue
            groups.setdefault(pnu, []).append(line)
    return groups, block, bad


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--dir", required=True, help="건축HUB zip 4개를 둔 폴더")
    ap.add_argument("--sigungu", action="append", help="시군구 5자리 또는 seoul. 기본: config [pilot]")
    ap.add_argument("--include-api", action="store_true", help="API로 받은 단지 필지도 파일 값으로 덮음")
    ap.add_argument("--dry-run", action="store_true", help="읽고 세기만 함(DB에 쓰지 않음)")
    ap.add_argument("--allow-older", action="store_true", help="이미 올린 파일보다 이전 달 파일도 올림(되돌릴 때)")
    args = ap.parse_args(argv)
    cfg = config_mod.load(args.config)
    sigungus = regions.expand(args.sigungu or cfg["pilot"]["sigungu"])

    files = find_files(args.dir)
    missing = [op for op in bld.OPS if op not in files]
    if missing:   # 빠진 기능이 있으면 그 값(용도지역 · 총괄표제부)을 비워 버리므로 4개가 다 있어야 올림
        print(f"{args.dir}에 없는 파일: {', '.join(missing)} — zip 4개(표제부 · 총괄표제부 · 지역지구구역 · 부속지번)를 "
              "모두 두세요", file=sys.stderr)
        return 1
    days = {d for _, _, d in files.values()}
    if len(days) > 1:   # 다른 달 파일이 섞이면 한 날짜로 묶을 수 없음(2026-09월 파일 4개는 모두 2026-10-20)
        print("파일 날짜가 서로 다름: " + ", ".join(f"{op} {d}" for op, (_, _, d) in files.items())
              + " — 같은 달 파일 4개를 두세요", file=sys.stderr)
        return 1
    asof = days.pop()
    names = {op: re.search(r"(mart_djy_\d\d)", inner)[1] for op, (_, inner, _d) in files.items()}
    got_all, bad_total = {}, 0
    for op, (z, inner, day) in files.items():
        groups, block, bad = read(z, inner, sigungus)
        got_all[op], bad_total = groups, bad_total + bad
        print(f"{op}: {z.name} ({day}) 필지 {len(groups):,}개 · 줄 {sum(map(len, groups.values())):,} · "
              f"블록 대장 {block:,}줄 건너뜀 · 열 수가 다른 줄 {bad:,}")
    if bad_total:   # 열 수가 다르면 파일 모양이 바뀐 것이라 아무것도 쓰지 않음(LAYOUTS를 새 모양에 맞춰야 함)
        print(f"열 수가 다른 줄 {bad_total:,}개 — 파일 모양이 바뀐 듯해 멈춤", file=sys.stderr)
        return 1
    pnus = sorted(set(got_all.get("getBrTitleInfo", {})) | set(got_all.get("getBrRecapTitleInfo", {})))
    if args.dry_run:
        print(f"올릴 필지 {len(pnus):,}개 (--dry-run이라 쓰지 않음)")
        return 0

    with db.connect(cfg["db"]["dsn"]) as conn:
        last = ingest.get_cursor(conn, SOURCE, "file")
        if last and asof < datetime.date.fromisoformat(last) and not args.allow_older:
            print(f"이미 {last} 파일을 올렸음 — 이 파일({asof})이 더 예전이라 멈춤. 되돌리려면 --allow-older",
                  file=sys.stderr)
            return 1
        # API로 받은 필지는 파일 날짜 뒤에 받았고 실제로 건물이 왔을 때만 건너뜀(커서 값 = 받은 날).
        # 적재 중에 API 배치가 새로 받은 필지도 있으므로 필지마다 그 자리에서 다시 확인함
        api_newer = """SELECT 1 FROM ops.ingest_cursor c JOIN stg.bld_raw s ON s.pnu = c.cursor_key
                       WHERE c.source_id = %s AND c.cursor_key = %s AND c.cursor_value >= %s
                         AND s.op = 'getBrTitleInfo' AND s.row_data <> '[]'::jsonb"""
        run_id = ingest.start_run(conn, SOURCE)
        conn.commit()
        loaded = rejected = done = skipped = failed = 0
        finished = False
        try:
            for start in range(0, len(pnus), COMMIT_EVERY):
                # 500필지를 한 트랜잭션으로 묶고(커밋 횟수를 줄임), 필지마다 savepoint를 둬 한 필지 실패가 묶음을 깨지 않게 함
                with conn.transaction():
                    for pnu in pnus[start:start + COMMIT_EVERY]:
                        if not args.include_api and conn.execute(api_newer, (bld.SOURCE, pnu, asof.isoformat())).fetchone():
                            skipped += 1
                            continue
                        got = {op: [to_row(names[op], line) for line in got_all[op].get(pnu, ())] for op in got_all}
                        try:
                            with conn.transaction():
                                n, r = bld.load(conn, run_id, pnu, got, asof, source=SOURCE, keep_raw=False)
                            loaded, rejected, done = loaded + n, rejected + r, done + 1
                        except Exception as e:  # 한 필지가 실패해도 나머지는 계속
                            failed += 1
                            if failed <= 20:
                                print(f"failed {pnu}: {e}", file=sys.stderr)
                i = min(start + COMMIT_EVERY, len(pnus))
                if i % 20000 < COMMIT_EVERY:
                    print(f"  {i:,}/{len(pnus):,} 필지 · 건물 {loaded:,}동", flush=True)
            note = f"파일 {asof} · 필지 {done:,}개 · API 필지 건너뜀 {skipped:,}개 · 실패 {failed:,}개"
            print(note)
            if not failed and asof.isoformat() > (last or ""):
                ingest.set_cursor(conn, SOURCE, "file", asof.isoformat())
            ingest.finish_run(conn, run_id, "failed" if failed else "done", loaded, rejected, note)
            finished = True
        finally:
            if not finished:
                conn.rollback()
                ingest.finish_run(conn, run_id, "failed", loaded, rejected, "중단됨")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
