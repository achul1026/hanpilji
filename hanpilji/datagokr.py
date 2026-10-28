"""공공데이터포털(apis.data.go.kr) 공통 — 인증키 · 호출 · 응답 풀기.

인증키는 환경변수 DATA_GO_KR_KEY 에서만 읽음(파일 · 저장소에 쓰지 않음).
포털이 주는 키는 두 가지(Encoding · Decoding)인데 어느 쪽을 넣어도 되게 함.
응답은 XML이 기본이고 JSON이 올 수도 있어서 둘 다 같은 dict 모양으로 풂.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET

from hanpilji import ingest

BASE = "https://apis.data.go.kr/1613000"
OK_CODES = {"00", "000", "0"}


class ApiError(RuntimeError):
    """포털 · 기관 쪽 오류 응답(키 미등록, 한도 초과 등)."""


def is_fatal(e):
    """다음 호출도 똑같이 실패할 오류(키 · 한도 · 429)면 True — 수집을 멈춰야 함."""
    if isinstance(e, ApiError):
        return "KEY" in str(e).upper() or "LIMIT" in str(e).upper()
    return isinstance(e, urllib.error.HTTPError) and e.code in (401, 403, 429)


def service_key():
    key = os.environ.get("DATA_GO_KR_KEY", "").strip()
    if not key:
        raise ApiError("환경변수 DATA_GO_KR_KEY 가 없음 — 공공데이터포털 인증키를 넣어야 함")
    return key


def build_url(path, params, key=None, base=BASE):
    """Decoding 키는 한 번 인코딩하고, 이미 인코딩된 키(% 포함)는 그대로 붙임. base는 기관 경로(국토부 1613000이 기본)."""
    key = key or service_key()
    enc_key = key if "%" in key else urllib.parse.quote(key, safe="")
    query = urllib.parse.urlencode(params)
    return f"{base}/{path}?serviceKey={enc_key}" + (f"&{query}" if query else "")


def _gateway_error(h, text):
    msg = " ".join(v for v in (h.get("errMsg"), h.get("returnAuthMsg")) if v) or text[:200]
    return ApiError(f"{h.get('returnReasonCode', '?')} {msg}")


def _xml_to_obj(el):
    children = list(el)
    if not children:
        return (el.text or "").strip()
    out = {}
    for c in children:
        v = _xml_to_obj(c)
        if c.tag in out:
            if not isinstance(out[c.tag], list):
                out[c.tag] = [out[c.tag]]
            out[c.tag].append(v)
        else:
            out[c.tag] = v
    return out


def parse(data: bytes):
    """응답 → {'header': {...}, 'body': {...}}. 오류 응답이면 ApiError."""
    try:
        text = data.decode("utf-8-sig").strip()
    except UnicodeDecodeError:  # 행위제한 API는 EUC-KR로 옴(선언이 없을 때도 있음, 2026-10-27 실측)
        text = data.decode("cp949").strip()
    if text.startswith("{"):
        obj = json.loads(text)
        if "OpenAPI_ServiceResponse" in obj:   # JSON으로 물어도 게이트웨이 오류는 이 모양(2026-10-28 온비드 실측)
            raise _gateway_error(obj["OpenAPI_ServiceResponse"].get("cmmMsgHeader") or {}, text)
        obj = obj.get("response", obj)
    else:
        root = ET.fromstring(re.sub(r"^<\?xml[^>]*\?>", "", text))  # 이미 str이라 인코딩 선언은 뺌
        obj = _xml_to_obj(root)
        if root.tag == "OpenAPI_ServiceResponse":  # 게이트웨이 오류(키 미등록 · 한도 초과 등)
            raise _gateway_error(obj.get("cmmMsgHeader", {}) if isinstance(obj, dict) else {}, text)
    if not isinstance(obj, dict) or "header" not in obj:
        raise ApiError(f"알 수 없는 응답: {text[:200]}")
    code = str(obj["header"].get("resultCode", "")).strip()
    if code not in OK_CODES:
        raise ApiError(f"{code} {obj['header'].get('resultMsg', '')}")
    return obj


def as_list(v):
    """item이 1건이면 dict, 여러 건이면 list, 0건이면 빈 문자열로 오는 것을 list로 맞춤."""
    if v in (None, ""):
        return []
    return v if isinstance(v, list) else [v]


def items(obj):
    body = obj.get("body") or {}
    its = body.get("items") or {}
    if isinstance(its, list):
        return its
    return as_list(its.get("item")) if isinstance(its, dict) else []


def fetch(path, params, http_get=ingest.http_get, base=BASE):
    """한 번 호출해서 (원본 bytes, 풀린 응답)을 돌려줌. 원본은 호출한 쪽이 보관."""
    try:
        data = http_get(build_url(path, params, base=base))
    except urllib.error.HTTPError as e:  # 키 오류는 HTTP 403 + 오류 XML로 옴(2026-10-23 실측)
        try:
            parse(e.read())  # 오류 XML이면 여기서 ApiError
        except ApiError:
            raise
        except Exception:
            pass
        raise
    return data, parse(data)
