"""법제처 국가법령정보 → stg.law_article · stg.law_annex → core.doc_law.

    python -m hanpilji.collectors.law [--config config.toml] [--only 건축법]

1) 개정 확인: 공공데이터포털 「법제처 국가법령정보 공유서비스」(data.go.kr 15000115) 법령 목록으로
   현행 법령일련번호(MST)를 받음. 인증키는 DATA_GO_KR_KEY, IP 등록이 없어 깃허브 실행기에서도 됨.
2) 본문: MST가 바뀐 법령만 법제처 DRF(lawService.do, OC=LAW_OC)에서 받음.
   target=law&MST= 로 부르면 시행 전 판(예: 주택법 2026-09-08 현행 대신 2027-03-09 판)이 와서,
   target=eflaw&MST=&efYd=<검색 결과의 시행일자> 로 현행 시행본을 받음(2026-11-04 확인).
   그래도 시행일이 오늘 뒤인 판이 오면 넣지 않고 「본문 대기」로 둠. 이미 시행 전 판이 현행으로 들어간 법령은
   MST가 같아도 다시 받음. 2026-11-01부터 법제처가
   테스트 계정(OC=test)과 등록 안 된 IP를 막아서, 본문을 못 받으면 「개정 감지 · 본문 대기」로 남기고
   커서는 그대로 둠(다음 실행에서 다시 시도). 이건 실패로 치지 않음.

법령마다 한 트랜잭션. 실패한 법령은 커서가 그대로라 다음 실행에서 다시 받음.
응답 XML 구조는 2026-10-22 실제 응답(OC=test, 17개 법령), 목록은 2026-11-02 포털 실제 응답으로 확인함.
"""
import argparse
import datetime
import functools
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET


from hanpilji import db, config as config_mod
from hanpilji import datagokr, ingest
from hanpilji.textnorm import search_text

SOURCE = "law_go_kr"
BASE = "http://www.law.go.kr/DRF"
SEARCH_BASE = "https://apis.data.go.kr/1170000"
# 법제처는 해외(깃허브 실행기)에서 가끔 응답을 아예 안 줌(2026-10-28 두 번). 한 번에 오래 기다리지 않고,
# 접속 실패가 이만큼 연달아 나면 남은 법령은 다음 실행으로 넘김(커서가 그대로라 그때 받음)
fetch_default = functools.partial(ingest.http_get, tries=2, timeout=15)
MAX_NET_FAILS = 3
KST = datetime.timezone(datetime.timedelta(hours=9))   # 한국 표준시(서머타임 없음). 서버 이미지에 tzdata가 없어도 됨


class BodyPending(RuntimeError):
    """개정은 확인했지만 법제처 본문을 못 받음(사용자 검증 실패 등). 다음 실행에서 다시 시도."""


def search_url(name):
    return datagokr.build_url("law/lawSearchList.do",
                              {"target": "law", "query": name, "numOfRows": 100, "pageNo": 1}, base=SEARCH_BASE)


def known_mst(conn, name):
    """마지막으로 넣은 MST. 커서가 없으면(Supabase로 옮길 때 커서가 빠진 법령) 현행 조문 출처에서 읽음."""
    mst = ingest.get_cursor(conn, SOURCE, f"law:{name}")
    if mst:
        return mst
    row = conn.execute(
        """SELECT substring(source FROM 'MST ([0-9]+)') FROM core.doc_law
           WHERE law_name = %s AND is_current ORDER BY effective_date DESC LIMIT 1""", (name,)).fetchone()
    return row[0] if row else None


def today_kst():
    """시행일은 한국 날짜 기준. 깃허브 실행기(UTC)는 월요일 05:07 KST에 아직 일요일이라 따로 맞춤."""
    return datetime.datetime.now(KST).date()


def has_future_current(conn, name):
    """현행으로 표시된 조문이 아직 시행 전인가(예전 수집기가 target=law로 시행 전 판을 넣은 경우)."""
    return conn.execute("SELECT 1 FROM core.doc_law WHERE law_name = %s AND is_current AND effective_date > %s "
                        "LIMIT 1", (name, today_kst())).fetchone() is not None


def body_url(oc, mst, efyd):
    """현행 시행본 본문 주소. 시행일자를 모르면 예전 방식(target=law)으로 부르고, 받은 판의 시행일을 따로 확인함."""
    q = {"OC": oc, "type": "XML", "MST": mst}
    q.update({"target": "eflaw", "efYd": efyd} if efyd else {"target": "law"})
    return f"{BASE}/lawService.do?" + urllib.parse.urlencode(q)


def _t(el, tag):
    v = el.findtext(tag)
    return v.strip() if v else None


# ── 응답 파싱 ──

def pick_current(search_xml: bytes, name):
    """검색 결과에서 이름이 정확히 같은 현행 법령 하나 → {mst, law_id, kind}."""
    root = ET.fromstring(search_xml)
    for law in root.iter("law"):
        if _t(law, "법령명한글") != name:
            continue
        if (_t(law, "현행연혁코드") or "현행") != "현행":
            continue
        return {"mst": _t(law, "법령일련번호"), "law_id": _t(law, "법령ID"), "kind": _t(law, "법령구분명"),
                "effective_date": _t(law, "시행일자")}
    return None


_DELETED = re.compile(r"^제\d+조(의\d+)?\s*삭제\s*(<[^>]*>)?\s*$")


def _article_body(unit):
    """조문내용 + 항 · 호 · 목을 원래 순서대로 이어 붙임."""
    parts = [_t(unit, "조문내용") or ""]
    for hang in unit.findall("항"):
        if _t(hang, "항내용"):
            parts.append(_t(hang, "항내용"))
        for ho in hang.findall("호"):
            if _t(ho, "호내용"):
                parts.append(_t(ho, "호내용"))
            for mok in ho.findall("목"):
                if _t(mok, "목내용"):
                    parts.append(_t(mok, "목내용"))
    return "\n".join(p for p in parts if p)


def parse_law(service_xml: bytes):
    """법령 본문 → (기본정보 dict, 조문 list, 별표 list). 「전문」(장 · 절 제목 줄)은 뺌."""
    root = ET.fromstring(service_xml)
    info = root.find("기본정보")
    if info is None:
        raise ValueError("기본정보가 없는 응답 — 오류 응답이거나 형식이 바뀌었음")
    meta = {
        "law_id": _t(info, "법령ID"),
        "name": _t(info, "법령명_한글"),
        "kind": _t(info, "법종구분"),
        "promulgation_date": _t(info, "공포일자"),
        "effective_date": _t(info, "시행일자"),
    }
    articles = []
    for unit in root.iter("조문단위"):
        if (_t(unit, "조문여부") or "조문") != "조문":
            continue
        no, branch = _t(unit, "조문번호"), _t(unit, "조문가지번호")
        body = _article_body(unit)
        if _DELETED.match(body):  # 「제69조 삭제 <1999.4.30>」 — 내용이 없어 검색에 방해만 됨
            continue
        articles.append({
            "key": unit.get("조문키") or f"{no}-{branch or 0}",
            "no": no,
            "branch": branch,
            "title": _t(unit, "조문제목"),
            "effective_date": _t(unit, "조문시행일자") or meta["effective_date"],
            "body": body,
        })
    annexes = []
    for unit in root.iter("별표단위"):
        annexes.append({
            "key": unit.get("별표키") or f"{_t(unit, '별표번호')}-{_t(unit, '별표가지번호') or 0}",
            "no": _t(unit, "별표번호"),
            "title": _t(unit, "별표제목"),
            "body": _t(unit, "별표내용"),
        })
    return meta, articles, annexes


def article_label(no, branch):
    no = str(int(no)) if no and no.isdigit() else no
    return f"제{no}조" + (f"의{int(branch)}" if branch and branch.isdigit() and int(branch) > 0 else "")


def _date(yyyymmdd):
    return datetime.datetime.strptime(yyyymmdd, "%Y%m%d").date()


# ── 적재 ──

def load_law(conn, run_id, mst, meta, articles, annexes):
    """stg를 이 법령일련번호(mst) 기준으로 갈아 끼우고 core.doc_law에 올림. 반환: 올린 조문 수."""
    conn.execute("DELETE FROM stg.law_article WHERE mst = %s", (mst,))
    conn.execute("DELETE FROM stg.law_annex WHERE mst = %s", (mst,))
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO stg.law_article (run_id, law_id, mst, law_name, law_kind, promulgation_date,
                   effective_date, article_key, article_no, article_branch, article_title, body)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            [(run_id, meta["law_id"], mst, meta["name"], meta["kind"], meta["promulgation_date"],
              a["effective_date"], a["key"], a["no"], a["branch"], a["title"], a["body"]) for a in articles],
        )
        cur.executemany(
            """INSERT INTO stg.law_annex (run_id, law_id, mst, law_name, annex_key, annex_no, annex_title, body)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            [(run_id, meta["law_id"], mst, meta["name"], x["key"], x["no"], x["title"], x["body"]) for x in annexes],
        )
        eff = _date(meta["effective_date"])
        # 같은 법령의 예전 시행본은 현행에서 내림(지우지 않음 — 과거 시점 질문용)
        cur.execute(
            "UPDATE core.doc_law SET is_current = false WHERE law_id = %s AND effective_date <> %s",
            (meta["law_id"], eff),
        )
        today = today_kst()
        cur.executemany(
            """INSERT INTO core.doc_law (article_id, law_id, law_name, law_kind, article_no, article_title,
                   body, search_text, effective_date, is_current, source, asof)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, true, %s, %s)
               ON CONFLICT (article_id) DO UPDATE SET
                   law_name = excluded.law_name, law_kind = excluded.law_kind,
                   article_title = excluded.article_title, body = excluded.body,
                   search_text = excluded.search_text, is_current = true, asof = excluded.asof""",
            [(f"{meta['law_id']}:{a['no']}-{a['branch'] or 0}:{meta['effective_date']}",
              meta["law_id"], meta["name"], meta["kind"], article_label(a["no"], a["branch"]), a["title"],
              a["body"], search_text(a["body"]), eff, f"법제처 국가법령정보(MST {mst})", today)
             for a in articles],
        )
    return len(articles)


def collect_one(conn, cfg, run_id, name, fetch):
    oc = cfg["law"]["oc"]
    search_xml = fetch(search_url(name))
    if b"OpenAPI_ServiceResponse" in search_xml[:300]:  # 포털 게이트웨이 오류(키 미등록 · 한도 초과)
        datagokr.parse(search_xml)
    found = pick_current(search_xml, name)
    if not found:
        raise LookupError(f"검색 결과에 「{name}」 현행 법령이 없음")
    mst = found["mst"]
    old = known_mst(conn, name)
    future = has_future_current(conn, name)
    if old == mst and not future:
        ingest.set_cursor(conn, SOURCE, f"law:{name}", mst)  # 커서가 비어 있던 법령도 채워 둠
        conn.commit()
        return "skipped", 0
    try:
        body = fetch(body_url(oc, mst, found.get("effective_date")))
    except OSError as e:
        raise BodyPending(f"MST {old} → {mst}, 법제처 본문 접속 실패: {e}") from e
    if "<법령".encode() not in body[:2000]:  # 2026-11-01부터 OC=test는 「사용자 정보 검증에 실패」 응답
        why = re.search(r"<result>(.*?)</result>", body.decode("utf-8", "replace"), re.S)
        raise BodyPending(f"MST {old} → {mst}, 법제처 본문을 못 받음(LAW_OC 확인): "
                          f"{why.group(1).strip() if why else body[:80].decode('utf-8', 'replace')}")
    meta, articles, annexes = parse_law(body)
    # 기본정보의 시행일자로 거름(조문시행일자로는 못 거름). 날짜가 없거나 이상하면 넣지 않음
    try:
        eff = _date(meta["effective_date"] or "")
    except ValueError:   # 없음 · 8자리가 아님 · 없는 날짜(20261340 등)
        eff = None
    if eff is None or eff > today_kst():
        raise BodyPending(f"MST {mst}: 시행 전 판이거나 시행일을 모름(시행일 {meta['effective_date']}) → 넣지 않음")
    path, sha = ingest.save_raw(cfg["storage"]["raw_dir"], "law", f"{name}_{mst}.xml", body)
    if ingest.already_loaded(conn, SOURCE, sha) and not future:
        ingest.set_cursor(conn, SOURCE, f"law:{name}", mst)
        conn.commit()
        return "skipped", 0
    task = ingest.add_task(conn, run_id, SOURCE, f"law:{name}", path, sha)
    if not articles:
        raise ValueError(f"「{name}」 조문 0개 — 응답 형식 확인 필요")
    n = load_law(conn, run_id, mst, meta, articles, annexes)
    ingest.end_task(conn, task, "done")
    ingest.set_cursor(conn, SOURCE, f"law:{name}", mst)
    conn.commit()
    return "done", n


def main(argv=None, fetch=fetch_default):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.toml")
    ap.add_argument("--only", action="append", help="이 법령만(여러 번 가능)")
    args = ap.parse_args(argv)
    cfg = config_mod.load(args.config)
    if cfg["law"]["oc"] == "test":
        print("주의: 법제처 본문은 테스트 계정(OC=test)으로 요청함(2026-11-01부터 막힘). 개정된 법령의 본문을 받으려면 "
              "open.law.go.kr에서 ID를 받아 LAW_OC로 넣을 것", file=sys.stderr)
    names = args.only or cfg["law"]["names"]
    with db.connect(cfg["db"]["dsn"]) as conn:
        run_id = ingest.start_run(conn, SOURCE)
        loaded, failed, pending, net_fails = 0, [], [], 0
        for i, name in enumerate(names):
            if net_fails >= MAX_NET_FAILS:
                left = names[i:]
                failed += left
                print(f"법제처 접속 실패 {net_fails}번 연속 → 남은 {len(left)}개는 다음 실행에서", file=sys.stderr)
                break
            try:
                status, n = collect_one(conn, cfg, run_id, name, fetch)
                loaded += n
                net_fails = 0
                print(f"{status:8} {n:5}조  {name}")
            except BodyPending as e:  # 개정은 확인함. 본문만 다음에(커서 그대로)
                conn.rollback()
                task = ingest.add_task(conn, run_id, SOURCE, f"law:{name}", status="pending")
                ingest.end_task(conn, task, "pending", str(e)[:500])
                conn.commit()
                pending.append(name)
                net_fails = 0
                print(f"::warning::개정 감지 · 본문 대기  {name}: {e}")
            except Exception as e:  # 한 법령이 실패해도 나머지는 계속
                conn.rollback()
                task = ingest.add_task(conn, run_id, SOURCE, f"law:{name}", status="failed")
                ingest.end_task(conn, task, "failed", str(e)[:500])
                conn.commit()
                failed.append(name)
                net_fails = net_fails + 1 if isinstance(e, OSError) else 0  # URLError · 시간 초과 · DNS 실패
                print(f"failed       {name}: {e}", file=sys.stderr)
        notes = [("실패: " + ", ".join(failed)) if failed else None,
                 ("개정 감지 · 본문 대기: " + ", ".join(pending)) if pending else None]
        ingest.finish_run(conn, run_id, "failed" if failed else "done", loaded, len(failed),
                          " / ".join(n for n in notes if n) or None)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
