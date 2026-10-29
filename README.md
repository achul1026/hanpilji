<div align="center">

<img src="docs/assets/banner.svg" alt="HANPILJI 한필지 도트 그림" width="100%">

# 한필지 (가칭)

**필지 하나로 잇는 부동산 공공데이터셋**<br>
땅 · 건물 · 거래 · 규제 데이터를 필지 번호(PNU)로 묶고, 그 데이터로만 답하는 부동산 GPT를 얹습니다.

![stage](https://img.shields.io/badge/stage-4%20%EB%8D%B0%EB%AA%A8-f6d365?style=for-the-badge)
![laws](https://img.shields.io/badge/%EB%B2%95%EB%A0%B9-17%EA%B0%9C%20%C2%B7%201%2C542%EC%A1%B0-81c995?style=for-the-badge)
![tests](https://img.shields.io/badge/tests-76%20passed-4fc3f7?style=for-the-badge)

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Claude](https://img.shields.io/badge/LLM-Claude%20API-d97757?logo=anthropic&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![PostGIS](https://img.shields.io/badge/PostGIS-optional-5CAE58)
![pgvector](https://img.shields.io/badge/pgvector-optional-8a63d2)
![Data](https://img.shields.io/badge/data-%EB%B2%95%EC%A0%9C%EC%B2%98%20%C2%B7%20%ED%86%A0%EC%A7%80%EC%9D%B4%EC%9D%8C%20%C2%B7%20%EA%B3%B5%EA%B3%B5%EB%8D%B0%EC%9D%B4%ED%84%B0%ED%8F%AC%ED%84%B8-f28b82)

[데이터 흐름](#-데이터-흐름) · [빠른 시작](#-빠른-시작) · [GPT가 부르는 함수](#-gpt가-부르는-마트-함수) · [부동산 GPT](#-부동산-gpt) · [Supabase](#️-supabase-영구-db) · [자동 최신화](#-자동-최신화) · [진행 상황](#-진행-상황) · [남은 일](#-남은-일)

</div>

---

## 🧭 데이터 흐름

원본은 지우지 않고 쌓고(`raw`), 받은 그대로 한 번 담았다가(`stg`), 필지 번호로 정리해(`core`), 질문별 함수로만 내보냅니다(`mart`). GPT 계정은 `mart`만 읽습니다.

```mermaid
flowchart LR
    subgraph SRC["공공 데이터"]
        LAW["⚖️ 법제처<br/>국가법령정보 API"]
        EUM["🗺️ 토지이음<br/>행위제한 CSV"]
        DG["🏢 공공데이터포털<br/>행위제한 · 실거래가 · 건축물대장"]
    end
    RAW[("📦 raw/<br/>원본 보관")]
    STG[("stg<br/>받은 그대로")]
    CORE[("core<br/>PNU로 정리한 18개 표")]
    MART[["mart<br/>질문별 함수"]]
    GPT(("🤖 부동산 GPT"))

    LAW --> RAW
    EUM --> RAW
    DG -.키 발급 후.-> RAW
    RAW --> STG --> CORE --> MART --> GPT
    OPS[("ops<br/>수집 기록 · 커서")] -.- STG
```

| 층 | 하는 일 | 예 |
|---|---|---|
| `raw/` | 받은 파일을 날짜별로 그대로 보관 | `raw/law/2026-10-22/건축법_273437.xml` |
| `stg` | 원본 모양 그대로 한 번 담기 | `stg.law_article`, `stg.eum_rule_raw` |
| `core` | PNU · 법령 ID 기준으로 정리 | `core.doc_law`, `core.reg_rule`, `core.dim_zone` |
| `mart` | GPT가 부르는 질문별 함수 | `mart.q_law_search()` |
| `ops` | 언제 무엇을 받았는지, 어디까지 받았는지 | `ops.ingest_run`, `ops.ingest_cursor` |

## 🚀 빠른 시작

```bash
pip install -r requirements.txt
export HANPILJI_DSN=postgresql://user@localhost/hanpilji
bash scripts/init_db.sh                               # 여러 번 돌려도 됨. --postgis --pgvector 선택
python -m hanpilji.collectors.law                     # 법령 21개(부동산 17 + 경매 · 공매 4) → core.doc_law
python -m hanpilji.collectors.eum_rule 행위제한.csv    # 토지이음에서 내려받은 파일

# 공공데이터포털 인증키가 있으면 (키는 환경변수로만)
export DATA_GO_KR_KEY=...
python -m hanpilji.collectors.luinfo                  # 행위제한 API → core.reg_rule (조례 기준)
python -m hanpilji.collectors.rtms                    # 아파트 · 오피스텔 · 연립다세대 매매 · 전월세 36개월 → core.fact_deal · fact_rent
python -m hanpilji.collectors.rtms --only offi_trade --only offi_rent   # 오피스텔만(API 2개를 따로 활용신청)
python -m hanpilji.collectors.rtms --only rh_trade --only rh_rent       # 연립다세대만(API 2개를 따로 활용신청)
python -m hanpilji.collectors.bld                     # 단지 필지마다 건축물대장 → core.dim_building · 용도지역 (강남구 2~3시간)
python -m hanpilji.collectors.vworld                  # 필지마다 V-World → 지목 · 토지 면적 · 개별공시지가 · 토지이용계획 (키: VWORLD_KEY)
# V-World는 해외(클라우드) IP를 끊음 → 국내 PC에서 받기만 하고 클라우드에서 적재
python -m hanpilji.collectors.vworld --export-pnus pnus.txt                    # 받을 필지 목록
python -m hanpilji.collectors.vworld --fetch-only pnus.txt --out vworld_raw    # 국내 PC, DB 없이 (표준 라이브러리만)
python -m hanpilji.collectors.vworld --from-dir vworld_raw                     # 받은 파일 적재
# 지적도(연속지적도 필지 경계) · 건물 윤곽 → core.geo_parcel · geo_building (PostGIS: init_db.sh --postgis)
python scripts/vworld_fetch_geom_gangnam.py                                     # 국내 PC, 강남구를 약 500m 칸으로 → zip
python -m hanpilji.collectors.vworld_geom --from-zip vworld_geom_11680.zip       # 받은 zip 적재(경위도 → 5186)

# 시장 · 거시 지표 (키: ECOS_KEY · RONE_KEY · KOSIS_KEY, 없는 기관은 건너뜀) → core.fact_market
python -m hanpilji.collectors.market                  # 금리 · 물가(ECOS) · 아파트 가격지수(R-ONE) · 인구 · 세대(KOSIS) 10년
python -m hanpilji.collectors.market --find rone 매매가격지수   # 표 번호 확인
python -m hanpilji.collectors.market --items 121Y006            # ECOS 항목 코드 확인

# 공매 물건 (키: DATA_GO_KR_KEY, 온비드 물건목록 API를 따로 활용신청) → core.fact_auction
python -m hanpilji.collectors.onbid                   # 시범 구의 입찰 진행 · 예정 공매 물건(재산유형마다 끝 쪽까지)
```

시험:

```bash
HANPILJI_TEST_DSN=postgresql://user@localhost/hanpilji_test python -m unittest discover -s tests -t .
```

> [!TIP]
> 인증키는 [data.go.kr](https://www.data.go.kr)에서 아래 4개를 활용신청하면 하나로 다 됩니다(자동 승인).
> [토지이용규제정보서비스](https://www.data.go.kr/data/15058410/openapi.do) ·
> [아파트 매매 실거래가](https://www.data.go.kr/data/15126469/openapi.do) ·
> [아파트 전월세 실거래가](https://www.data.go.kr/data/15126474/openapi.do) ·
> [건축HUB 건축물대장정보](https://www.data.go.kr/data/15134735/openapi.do).
> 공매 물건은 「한국자산관리공사_차세대 온비드 부동산 물건목록 조회서비스」를 하나 더 신청하면 같은 키로 됩니다.
> Encoding · Decoding 키 어느 쪽을 넣어도 됩니다.

> [!NOTE]
> 법제처 수집은 공식 테스트 계정 `OC=test`로도 돌아갑니다. 운영 전에 [open.law.go.kr](https://open.law.go.kr)에서 ID를 받아 `LAW_OC` 환경변수로 넣으세요.

## 🔎 GPT가 부르는 마트 함수

| 함수 | 이런 질문에 | 최대 줄 수 |
|---|---|---|
| `mart.q_law_search(질문, 법령명?)` | 「차임 증액 청구」 「임대료 5% 상한」 | 10 |
| `mart.q_zoning_check(용도지역, 건물종류, 시군구?)` | 「이 땅에 근린생활시설 지을 수 있나?」 | 20 |
| `mart.q_building_filter(법정동 앞자리, 경과연수?, 승강기?, 위반?, 용도?, 주차?)` | 「준공 20년 넘고 승강기 없는 건물」 | 50 |
| `mart.q_complex_price(시군구, 단지명, 전용면적?, 개월?)` | 「개포동 ○○아파트 84㎡ 최근 1년 매매가 · 전세가」 | 20 |
| `mart.q_property_card(시군구, 단지명 또는 동 지번, 개월?)` | 「도곡렉슬 종합해서 알려줘」 「대치동 316 어떤 곳이야?」 | 5 |
| `mart.q_deal_list(시군구, 단지명, 매매·전세·월세?, 전용면적?, 개월?, 정렬?)` | 「은마 76㎡ 최근 6개월 최고가」 「최근 전세 거래를 층과 함께」 | 30 |
| `mart.q_complex_rank(시군구, 지표, 개월?, 전용면적?, 준공연도?, 몇 곳?, 정렬?)` | 「전세 거래가 가장 많은 단지 3곳」 「2020년 이후 준공 평당가 상위 5곳」 「전세가율 낮은 곳」 | 20 |
| `mart.q_price_trend(시군구, 개월?, 동?, 단지?, 전용면적?)` | 「요즘 강남 집값 추세」 「대치동 시세 추이」 | 36 |
| `mart.q_land_price_trend(시군구, 동 · 단지?, 시작 연도?)` | 「도곡렉슬 공시지가 5년간 얼마나 올랐어?」 | 14 |
| `mart.q_price_vs_official(시군구, 단지?, 개월?, 몇 곳?, 정렬?)` | 「공시지가 대비 실거래가가 높은 단지」 | 20 |
| `mart.q_market_series(지표?, 지역?, 개월?)` | 「요즘 기준금리랑 주담대 금리」 「강남구 매매가격지수」 | 120 |
| `mart.q_auction_discount(시군구, 검색어?, 진행 중만?, 최소 할인율?, 개월?, 몇 건?)` | 「강남구 공매 아파트 중 실거래보다 싼 것」 | 30 |
| `mart.q_auction_checklist(경매·공매?, 항목?)` | 「경매 권리분석 뭘 봐야 해?」 「말소기준권리가 뭐야」 | 14 |
| `mart.q_parcel_map(시군구, 단지명 · 동 지번 · PNU)` | 「도곡렉슬 어디야?」 「대치동 316 필지 경계 보여줘」 | 대상 필지 전부 + 이웃 필지 · 건물 250개씩(화면 전용) |

`q_complex_price`는 단지 x 전용면적마다 거래단가 · 거래가격 · 전세단가 · 전세시세를 한 줄로 돌려줍니다. 해제된 거래와 월세는 빼고, ㎡당 · 평당 단가와 전세가율도 같이 줍니다.

`q_deal_list`는 거래를 한 건씩(계약일 · 면적 · 층 · 금액 · 신규/갱신) 돌려주고, `price_desc`로 정렬하면 첫 줄이 최고가입니다. `q_complex_rank`는 단지 이름 없이 매매 · 전세 · 월세 건수, ㎡당 단가, 전세가율로 줄을 세웁니다. 단가 · 전세가율 순위는 거래가 3건 이상인 단지만 넣고, 준공연도는 매매 신고의 건축년도를 씁니다.

`q_price_trend`는 달마다 매매 · 전세 ㎡당 **중위** 단가와 전월 · 전년 같은 달 대비를 줍니다. 신고 기한(계약 후 30일)이 안 지난 달은 `partial`로 표시합니다. `q_land_price_trend`의 상승률은 두 해 모두 값이 있는 같은 필지끼리 비교한 값의 중위값입니다. `q_price_vs_official`의 배수(거래 단가 ÷ 공시지가)는 땅 ㎡당과 집 전용 ㎡당을 나눈 비교 지표라 시세 반영률이 아니고, 용적률이 높을수록 작게 나옵니다. `q_market_series`는 지표를 비우면 지표마다 최근 값 한 줄씩 돌려줍니다.

`q_auction_discount`는 공매 물건마다 현재 회차의 최저입찰가를 같은 단지(지번 PNU, 없으면 「동 지번」으로 찾음) · 비슷한 면적(±3㎡)의 최근 매매 ㎡당 중위 단가 x 면적과 비교해 할인율을 줍니다. 권리관계는 보지 않으므로 할인이 커도 선순위 임차인 · 유치권이 있으면 실제로는 비쌀 수 있고, 그래서 `q_auction_checklist`가 입찰 전 확인 항목 14개를 근거 조문(민사집행법 · 주택임대차보호법 · 국세기본법 등)과 함께 돌려줍니다. 법원경매는 공식 API가 없어 아직 넣지 않았습니다(`kind` 열로 같은 표에 넣을 자리는 있음).

모든 함수가 `row_total`(전체 건수)을 함께 돌려줘서, GPT가 「10건만 보여드렸고 전체는 39건」처럼 말할 수 있습니다.

<details>
<summary><b>예시: 「5% 증액」 검색 결과 (실데이터, 2026-10-22)</b></summary>

```sql
SELECT article_no, law_name, row_total FROM mart.q_law_search('5% 증액') LIMIT 4;
```

| article_no | law_name | row_total |
|---|---|---|
| 제29조의2 | 도시 및 주거환경정비법 | 39 |
| 제14조의5 | 주택법 | 39 |
| 제7조 | 주택임대차보호법 | 39 |
| 제8조 | 주택임대차보호법 시행령 | 39 |

조문에는 「20분의 1」로 적혀 있어도 `search_text`에 「5%」를 덧붙여 두어서 찾힙니다.

</details>

## 🤖 부동산 GPT

질문을 받으면 LLM이 위 마트 함수 중 맞는 것을 **도구로 골라 부르고**, 돌아온 줄만 보고 답합니다. LLM은 SQL을 쓰지 않고 DB에도 직접 닿지 않습니다. 답 끝의 근거(어떤 함수 · 몇 건 · 출처 · 기준일)는 모델이 아니라 엔진이 실행 기록으로 붙이므로 빠지지 않습니다.

```mermaid
sequenceDiagram
    autonumber
    actor U as 사용자
    participant E as 엔진<br/>hanpilji.gpt
    participant L as LLM<br/>(Claude API)
    participant M as mart 함수<br/>(읽기 전용)
    U->>E: 도곡렉슬 84㎡ 최근 1년 시세?
    E->>L: 질문 + 도구 7개 정의 + 지킬 것
    L->>E: q_complex_price(name=도곡렉슬, area_m2=84)
    E->>M: SELECT * FROM mart.q_complex_price(...)
    M-->>E: 1줄 · row_total 1 · 출처 · 기준일
    E->>L: 결과(JSON)
    L-->>E: 「최근 12개월 매매 21건 평균 …」
    E-->>U: 답 + 근거 (함수 · 건수 · 출처 · 기준일)
```

| 지키는 것 | 어떻게 |
|---|---|
| 데이터로만 답함 | 도구 결과에 없는 숫자 · 조문은 쓰지 않도록 지시. 조회 없이 답하면 근거에 「데이터 조회 없이 답함」이 찍힘 |
| 모르면 모른다 | 0건이면 「가진 데이터에는 없어 모릅니다」 + 무엇이 없는지(지역 · 기간 · 종류) |
| 건수를 밝힘 | 「매매 21건 평균」, 「전체 39건 중 10건」. 근거 줄에도 `shown` / `row_total` |
| 투자 조언 안 함 | 사라 · 팔아라 · 오른다 같은 권유 · 전망 대신 확인 가능한 사실만 |
| 틀린 호출 막기 | 없는 도구 · 모르는 인자 · 형식이 틀린 인자는 실행하지 않고 모델에게 오류로 돌려줌. 질문 하나에 주고받기 최대 6번 · 도구 실행 최대 12개, 마지막 결과는 정리할 기회를 한 번 더 줌 |
| 대화 길이 | 이어 묻기에는 최근 질문 10개만 넘김(문맥 · 메모리가 끝없이 늘지 않게) |
| DB 보호 | 읽기 전용 접속(`default_transaction_read_only`) · 5초 제한. `hanpilji_gpt` 계정은 `mart`만 읽음 |

### 써 보기

```bash
pip install -r requirements.txt
export HANPILJI_LLM_API_KEY=...                       # 아래 「API 키」 참고. 파일에 쓰지 않음
export HANPILJI_GPT_DSN=postgresql://hanpilji_gpt:...@localhost/hanpilji   # 없으면 HANPILJI_DSN

python -m hanpilji.gpt "도곡렉슬 84㎡ 최근 1년 매매가랑 전세가"   # 한 번 묻기
python -m hanpilji.gpt                                          # 이어서 묻기(대화)
python -m hanpilji.gpt.web --port 8000                          # 웹 → http://127.0.0.1:8000
```

웹은 대화(가운데)와 **근거 서랍**(오른쪽)으로 되어 있습니다. 답 속 `[1]` 같은 번호를 누르면 그 숫자가 나온 원본(표 · 월별 그래프 · 조문 원문 · 단지 카드)이 서랍에 열립니다. 서버는 파이썬 표준 라이브러리만 쓰고, 화면은 `hanpilji/gpt/static/`의 파일 셋(HTML · CSS · JS, 빌드 없음)입니다.

누구나 쓰게 공개하는 법(Google Cloud Run 서울, `Dockerfile`, 하루 질문 한도, V-World 운영키 신청)은 [docs/deploy_cloudrun.md](docs/deploy_cloudrun.md)에 있습니다.

<p align="center"><img src="docs/assets/web_home.png" width="860" alt="한필지 첫 화면: 제목 아래 입력창, 예시 질문, 같은 질문에 대한 흔한 AI 답과 한필지 답 비교"></p>

<p align="center"><img src="docs/assets/web_answer.png" width="860" alt="한필지 답: 도곡렉슬 단지 머리, 결론 한 줄, 원자료 일치 표시가 붙은 핵심 숫자 카드, 근거 서랍의 단지 카드"></p>

- **답 모양:** 결론 한 줄 → 핵심 숫자 카드(값 · 몇 건에서 나온 값인지 · 근거 번호) → 요점 몇 문장 → 「확인 못 한 것」(빈 칸 · 수집 전 · 범위 밖). 단지 하나를 다루면 맨 위에 단지 머리(주소 · PNU · 준공 · 세대)를 둡니다. 모양은 엔진 프롬프트(`engine.SCREEN`)의 `:::숫자` · `:::확인 못 함` 칸으로 정합니다.
- **숫자 검산:** 답에 나온 금액 · 비율 · 면적 · 건수를 그 답에서 부른 도구 결과(원자료)와 맞춰 봅니다(`hanpilji/gpt/verify.py`). 답의 반올림 자리까지 같으면 「원자료 일치」, 아니면 「원자료에서 바로 못 찾음」으로 표시하고, 근거 도장 아래에 「답의 숫자 26개 중 24개를 원자료에서 그대로 찾았어요」처럼 적습니다. 못 찾은 숫자는 모델이 더하거나 나눈 값일 수 있어 「틀림」이라고 하지 않습니다.
- **이어서 볼 것:** 답이 다룬 단지로 시세 추이 · 실거래 목록 · 공시지가 추이 · 지적도 같은 다음 조회를 단추로 줍니다. 모델 없이 마트 함수만 불러 바로 열고(요금 없음), 연 결과는 그 답의 근거에 새 번호로 붙습니다.
- **필지 리포트 · 두 단지 비교:** 입력창 위 「필지 리포트」 · 「두 단지 비교」를 고르면 모델 없이 마트 함수만 차례로 불러 한 장으로 모읍니다(요금 없음, `hanpilji/gpt/report.py`). 리포트는 종합 카드 · 지적도 · 월별 시세 · 공시지가 추이 · 공시지가 대비 실거래가와 「확인 못 한 것」, 비교는 두 단지의 준공 · 세대 · 용적률 · 평당가 · 전세가율 · 공시지가를 한 표에 놓고 차이(%, 비율은 %p)를 적습니다. 「인쇄 · PDF로 저장」은 그 리포트만 A4로 찍습니다. 이름이 여러 단지에 맞으면 고를 단지를 먼저 보여 줍니다.
- **글꼴:** 한글은 모두 Pretendard(굵기로만 위계), PNU · 날짜 같은 영문 · 숫자는 JetBrains Mono. 숫자는 자릿수가 맞게(tabular) 씁니다.
- **답변 블록:** 모델은 말만 쓰고, 표 · 그래프 · 조문 · 단지 카드는 엔진이 도구 결과 그대로 그립니다(`hanpilji/gpt/blocks.py`). 모델이 숫자를 잘못 옮겨도 블록 속 값은 원본과 같습니다.
- **근거 도장:** 답마다 기관 · 건수 · 기준일을 조회 기록에서 붙입니다. 조회 없이 답하면 그렇다고 씁니다.
- **조문 원문:** 묻는 낱말이 가장 많이 든 문장에 형광 표시를 하고 법제처 원문 링크를 붙입니다.
- **기간 바꾸기:** 그래프 · 표의 1년 · 2년 · 3년 단추는 모델을 다시 부르지 않고 마트 함수만 다시 부릅니다(요금 없음).
- **진행 표시:** 답을 만드는 동안 「실거래 목록을 찾는 중」처럼 지금 무엇을 찾는지 보여 줍니다(Server-Sent Events).
- **지적도:** 단지 · 주소를 물으면 대상 필지 경계(측량 청색), 둘레 이웃 필지와 번지 · 지목, 건물 윤곽, 축척 막대를 그립니다. 도형은 화면에만 가고 모델에게는 번지 · 면적 · 중심 좌표만 넘깁니다(토큰 절약). 배경지도는 V-World(밝은 화면 white · 어두운 화면 midnight)이고 `HANPILJI_VWORLD_MAP_KEY`를 넣으면 켜집니다. 키가 없으면 모눈 위에 선만 그립니다(`HANPILJI_MAP_TILES=osm` · `none`으로 바꿀 수 있음). 연속지적도는 참고용 도면이라 측량 · 경계 확정에 쓸 수 없다고 늘 함께 적습니다.
- **대화 목록:** 이 브라우저에만 저장합니다. 휴대폰에서는 근거 서랍이 아래에서 올라옵니다.

<p align="center"><img src="docs/assets/web_report.png" width="860" alt="필지 리포트: 도곡렉슬 단지 머리, 원자료 그대로인 핵심 숫자 6개, 지적도, 인쇄 단추"></p>

<p align="center"><img src="docs/assets/web_compare.png" width="860" alt="두 단지 비교: 도곡렉슬과 래미안대치팰리스의 준공 · 세대 · 용적률 · 평당가 · 전세가율 · 공시지가와 차이"></p>

<p align="center"><img src="docs/assets/web_map.png" width="860" alt="지적도 블록: 도곡렉슬 필지 5개 경계와 이웃 필지 번지 · 지목, 건물 윤곽, 축척 막대"></p>

<p align="center"><img src="docs/assets/web_mobile.png" width="860" alt="휴대폰 화면: 첫 화면, 래미안대치팰리스 답의 핵심 숫자 카드, 요점"></p>

| 주소 | 하는 일 |
|---|---|
| `POST /api/ask/stream` | 질문 → 진행(tool) · 블록(block) · 답(answer) 이벤트 |
| `POST /api/ask` | 같은 일을 한 번에 JSON으로(답 · 근거 · 블록) |
| `GET /api/report?q=` · `GET /api/compare?a=&b=` | 필지 리포트 · 두 단지 비교(모델 없이 마트 함수만). 이름이 여럿에 맞으면 `choices` |
| `POST /api/tool` | 블록 기간 바꾸기와 「이어서 볼 것」 열기(모델 없이 마트 함수만). 정한 도구만, 인자는 도구 형식대로 확인 |
| `GET /api/card?q=` | 단지 카드만(LLM 없이) |
| `GET /config.json` | 지도 배경 타일 설정(토큰 없이. 배경지도 키는 원래 브라우저에 드러남) |

<p align="center"><img src="docs/assets/web_card.png" width="860" alt="단지 종합 질문: 답과 단지 카드(거래 · 토지 · 용도지역 · 건물)"></p>

카드의 다섯 칸은 PNU로 잇습니다. 실거래가의 동 이름 · 지번을 법정동 코드표로 바꿔 단지마다 PNU(19자리)를 만들고, 토지 · 용도지역 · 건축물대장 표에 같은 PNU가 들어오면 그 칸이 자동으로 채워집니다. 아직 수집 전인 칸은 「데이터 수집 전」으로 보이고, GPT도 그 칸은 모른다고 답합니다.

남에게 열 때(`--host 0.0.0.0`)는 질문마다 API 요금이 나가므로 `HANPILJI_WEB_TOKEN`을 정해야만 켜지고, 주소는 `http://서버:8000/?token=정한값`입니다. 접속 주소마다 시간당 질문 30개, 기간 바꾸기 240번까지 받습니다. 화면 파일은 토큰 없이 열립니다(요금이 나가는 `/api/`만 토큰 필요).

> [!IMPORTANT]
> 클라우드 환경의 로컬 PostgreSQL은 세션마다 새로 떠서 데이터가 남지 않습니다. 오래 남길 데이터는 [Supabase](#️-supabase-영구-db)에 있고, 클라우드 세션에서는 `HANPILJI_DB_VIA=supabase-api`를 주면 GPT · 웹 카드가 거기서 읽습니다.
>
> 수집이 끝날 때마다 프로젝트 공유 폴더에도 백업을 남깁니다. 로컬 DB가 필요하면 다시 수집하지 말고 백업에서 되살리세요(몇 분).
>
> ```bash
> HANPILJI_DSN=postgresql:///hanpilji scripts/restore_db.sh            # /mnt/project-files/hanpilji-data/ 의 가장 최근 백업
> pg_dump -Fc -Z 6 -d "$HANPILJI_DSN" -f /mnt/project-files/hanpilji-data/hanpilji_YYYYMMDD.dump   # 새로 수집한 뒤 백업
> ```
>
> 접속 주소는 `HANPILJI_DSN` 하나로만 받으므로 Supabase 같은 바깥 DB로 옮겨도 같은 스크립트를 씁니다. 백업 파일은 저장소에 올리지 않습니다.

### 모델과 제공자

| 항목 | 기본값 | 바꾸는 곳 |
|---|---|---|
| 제공자 | Anthropic Claude (Messages API 도구 호출) | `config.toml` `[gpt] provider` · `HANPILJI_LLM_PROVIDER` |
| 모델 | `claude-opus-5-5` (생각 adaptive · effort `medium`) | `[gpt] model` · `HANPILJI_LLM_MODEL`. 비용을 줄이려면 `claude-sonnet-5-5` |
| 거절 시 | 서버가 다른 모델로 다시 돌림(`fallbacks = "default"`) | `[gpt] fallbacks = ""`로 끔 |
| 키 | 환경변수 `HANPILJI_LLM_API_KEY` | 파일 · 채팅에 쓰지 않음 |

엔진은 제공자와 상관없는 대화 형식만 알고, 제공자가 자기 API 모양으로 바꿉니다. 다른 LLM을 붙이려면 [`hanpilji/gpt/llm.py`](hanpilji/gpt/llm.py)에 `chat(system, messages, tools) -> Reply` 하나를 만들고 `PROVIDERS`에 등록하면 됩니다. 마트 함수와 도구 정의는 그대로 씁니다.

<details>
<summary><b>API 키 넣는 법 (Claude Code 클라우드 환경)</b></summary>

1. [console.anthropic.com](https://console.anthropic.com) 로그인 → **Settings › Billing**에서 결제 수단 등록(선불 크레딧)
2. **Settings › API Keys › Create Key** → 이름 `hanpilji-gpt` → 만든 키를 복사(다시 볼 수 없음)
3. 프로젝트 설정 › **Cloud environment** › 환경 변수에 `HANPILJI_LLM_API_KEY=복사한 키` 한 줄 추가 → 저장
4. 키는 **저장한 뒤 새로 시작한 세션**에만 보입니다

`ANTHROPIC_API_KEY`라는 이름을 쓰지 않는 까닭: 클라우드 환경에 그 이름으로 넣으면 Claude Code 세션 자체가 그 키로 돌아 요금이 섞일 수 있습니다.

</details>

## 🗄️ Supabase (영구 DB)

데이터를 오래 두는 곳은 Supabase(무료 플랜, 서울 리전)입니다. 클라우드 작업 환경은 프록시가 웹(HTTPS)만 통과시켜 DB 직접 연결(5432)이 막히므로, Supabase **Management API**의 SQL 실행 주소로 넣고 읽습니다. 웹사이트를 올릴 서버처럼 직접 연결이 되는 곳에서는 `HANPILJI_DSN`(Session pooler 주소)으로 평소대로 씁니다.

| 환경변수 | 쓰임 |
|---|---|
| `HANPILJI_DSN` | Supabase Session pooler 주소. 사용자명 `postgres.<ref>`에서 프로젝트를 알아냄 |
| `SUPABASE_ACCESS_TOKEN` | 개인 액세스 토큰(`sbp_…`, 계정 전체 권한이라 만료를 짧게). 파일 · 채팅에 쓰지 않음 |
| `HANPILJI_DB_VIA=supabase-api` | GPT · 웹 카드가 DB 직접 연결 대신 HTTPS로 읽음 |

```bash
# 백업(pg_dump)을 Supabase에 덮어쓰기 복원 → 테이블별 건수 비교. 파일을 안 주면 공유 폴더의 가장 최근 백업
python -m hanpilji.supabase restore /mnt/project-files/hanpilji-data/hanpilji_YYYYMMDD.dump
python -m hanpilji.supabase counts            # 건수만 다시 비교

HANPILJI_DB_VIA=supabase-api python -m hanpilji.gpt "도곡렉슬 84㎡ 최근 1년 매매가랑 전세가"
HANPILJI_DB_VIA=supabase-api python -m hanpilji.gpt.web --port 8000
```

복원은 백업을 로컬 임시 DB(`hanpilji_restore`)에 푼 뒤 스키마 → 데이터(테이블마다 약 1MB씩 JSON 묶음) → 제약 · 색인 · 시퀀스 → 권한 순으로 보냅니다. `ops` · `stg` · `core` · `mart` 네 스키마를 지우고 다시 만들므로 여러 번 돌려도 됩니다. 기본 권한(DEFAULT ACL)은 백업에 원래 소유자 이름이 박혀 있어 빼고, `hanpilji_gpt`에 다시 줍니다.

HTTPS로 읽을 때도 지키는 것은 같습니다. 요청마다 읽기 전용 · 5초 제한 · `SET ROLE hanpilji_gpt`(mart만 보임)으로 돌리고, 도구 인자는 따옴표를 풀어 쓴 리터럴로만 넣습니다. 요청 한 번에 1~3초가 더 걸립니다.

> [!NOTE]
> 무료 플랜은 7일 동안 쓰지 않으면 멈춥니다. 대시보드에서 **Restore**를 누르면 돌아옵니다. 액세스 토큰은 만료일이 지나면 새로 만들어 환경변수를 바꿔야 합니다.

## 🔄 자동 최신화

[`.github/workflows/refresh.yml`](.github/workflows/refresh.yml)이 깃허브 실행기에서 수집기를 정해진 시각에 돌려 Supabase에 바로 넣습니다. 수집기마다 `ops.ingest_cursor`에 어디까지 받았는지 남기므로 매번 전부가 아니라 바뀐 부분만 받습니다. 실행이 겹치면 앞 실행이 끝날 때까지 기다립니다.

| 언제(KST) | 무엇 | 얼마나 |
|---|---|---|
| 매일 04:17 | 실거래 매매 · 전월세 | 최근 3개월만 다시(늦은 신고 · 해제 반영). 새 달은 저절로 추가 |
| 매주 월 05:07 | 법령 17개 | 현행 법령 번호(MST)가 바뀐 것만 본문 받기 |
| 매주 월 05:07 | 금리 · 물가 · 가격지수 · 인구 | 최근 12개월 덮어쓰기 |
| 매월 2일 05:37 | 건축물대장 · 행위제한 | 전부 다시(호출 약 2,900 · 576번, 하루 한도 안) |
| 매년 6월 | 공시지가 · 지목(V-World) | V-World가 해외 IP를 끊어 국내 PC에서 `--fetch-only` → `--from-dir` |

실행 요약에 수집처별 마지막 성공 시각(`ops.v_source_freshness`)이 표로 나오고, 받은 원본은 14일 동안 실행 결과물로 남습니다. 실패하면 깃허브가 메일을 보냅니다. **Actions → 데이터 자동 최신화 → Run workflow**로 아무 때나 돌릴 수 있습니다.

비밀값(Settings → Secrets and variables → Actions): `HANPILJI_DSN`(Session pooler 주소) · `DATA_GO_KR_KEY` · `ECOS_KEY` · `RONE_KEY` · `KOSIS_KEY` · `LAW_OC`(없으면 테스트 계정). 매일 도니 무료 플랜이 7일 쉬어 멈추는 일도 없습니다.

## 📊 진행 상황

| 단계 | 내용 | 상태 |
|---|---|---|
| 0 | 규제 축: 법제처 법령 · 행위제한 | 🟢 법령 완료 · 🟢 행위제한 API로 강남구 수집 (CSV 없이) |
| 1 | 공공데이터포털 인증키 · 호출 확인 | 🟢 실제 응답으로 항목 이름 · 코드 확인 (2026-10-27) |
| 2 | 시세 축: 시범 구(강남구) 3년치 | 🟢 매매 1만 432건 · 전월세 7만 924건 적재 |
| 3 | GPT 엔진 연결 | 🟢 Claude API 도구 호출 엔진, 실제 질문 15개로 확인 (2026-10-27) |
| 4 | 데모 | 🟡 단지 카드 · 웹사이트 첫 버전(대화 + 근거 서랍, 2026-10-28). 건축물대장으로 건물 · 용도지역 · 대지면적 채움 (2026-10-27). 강남구 1,168필지 지목 · 공시지가(2013~2026) · 토지이용계획 채움 (2026-10-28), 단지 721개 중 719개 카드에 공시지가 |

**0단계 실측 (2026-10-22)**

| 항목 | 결과 |
|---|---|
| 법령 | 17개 · 조문 1,542개 · 별표 99개 (`삭제`된 조문 163개는 뺌) |
| 가장 큰 법령 | 도시 및 주거환경정비법 165조, 국토계획법 · 시행령 각 158조 |
| 시험 | 29개 통과 (시험용 응답은 법제처 · 포털 모두 실제 응답) |

**1~2단계 실측: 강남구 (2026-10-27)**

| 항목 | 결과 |
|---|---|
| 아파트 매매 | 1만 432건 (2023-11 ~ 2026-10), 해제 692건 표시 · 시세에서 뺌 |
| 아파트 전월세 | 7만 924건, 전세(월세 0원) 3만 5,821건 |
| 단지 | 721개. 매매 · 전월세 둘 다 있는 단지 495개 |
| 적재 제외 | 0건 (실거래가 · 행위제한 모두) |
| 행위제한 | 용도지역 16개 x 건물 종류 36개 = 576번 호출 모두 성공. 규칙 1,072건(시설 67종, 가능 701 · 불가 371), 조례 조건 붙은 것 196건 |

```sql
SELECT * FROM mart.q_complex_price('11680', '도곡렉슬', 84, 12);
```

| 단지 | ㎡ | 매매 | 매매 평균 | 평당 | 전세 | 전세 평균 | 전세가율 |
|---|---|---|---|---|---|---|---|
| 도곡렉슬 | 85 | 21건 | 35억 4,904만 | 1억 3,806만 | 76건 | 14억 9,511만 | 42.1% |

**4단계 실측: 강남구 건축물대장 (2026-10-27)**

| 항목 | 결과 |
|---|---|
| 받은 필지 | 단지 721개 중 PNU가 있는 719개가 걸친 필지 709개 전부(한 필지에 단지가 둘인 곳이 있음). 호출 약 3,000번, 하루 한도 10,000번 안 |
| 건물 | 2,883동(주건축물 2,133동). 건물이 붙은 단지 711개 |
| 용도지역 · 지구 | 필지 695개, 용도지역이 붙은 단지 705개. 그중 「제3종일반주거지역」처럼 종까지 적혀 행위제한 규칙(1,072건)이 이어지는 단지 286개, 「일반주거지역」만 적혀 규칙을 붙일 수 없는 단지 417개 |
| 대지면적 · 세대 · 주차 · 건폐율 · 용적률 | 총괄표제부가 있는 큰 단지 140개는 단지 전체 값, 나머지는 동별 표제부를 합침 |
| 적재 제외 | 부속지번 1줄(지번 칸에 「0^205-3」처럼 읽을 수 없는 값) |

```sql
SELECT bld_count, bld_households, bld_parking, land_area_m2, zones FROM mart.q_property_card('11680', '도곡렉슬');
```

| 동 | 세대 | 주차 | 대지면적 | 용도지역 |
|---|---|---|---|---|
| 35 | 3,002 | 4,443 | 138,750㎡ (건축물대장) | 일반주거지역 |

<details>
<summary><b>건축물대장 응답에서 알게 된 것</b></summary>

- 공공데이터포털의 옛 토지 API(토지특성 · 개별공시지가 · 토지이용계획, `1611000/nsdi/...`)는 「서비스가 없거나 폐기됨」(12)으로 옵니다. 지금은 V-World로만 제공되고, V-World는 해외(클라우드) IP의 연결을 끊어서 국내 PC에서 받아 클라우드에서 적재합니다(`--fetch-only` → `--from-dir`).
- 총괄표제부(단지 한 장)는 큰 단지에만 있습니다. 집합건물 동의 표제부는 대지면적 · 건폐율 · 용적률이 0으로 옵니다.
- 지역지구 코드는 「1023」 「UQA123」 두 체계가 섞여 오고, 「도시지역」 「일반주거지역」 같은 상위 이름도 같이 옵니다. 이름으로 UQ 코드에 맞추고 더 구체적인 이름이 있으면 상위 이름은 뺍니다. 「제3종」이 아니라 「일반주거지역」만 적힌 필지도 많습니다(417개).
- 오래된 아파트는 6층이 넘어도 승강기가 0대로 적힌 경우가 많아 그때는 「없음」이 아니라 모름으로 둡니다. 동별 주차 합이 0인 단지(은마 등)도 모름으로 둡니다. 위반건축물 여부는 이 API에 없습니다.
- 부속지번은 번 · 지가 `0000`이고 글(`atchEtcJibunNm` 「36-6」)로만 오는 줄이 많아 글에서 읽습니다.
- 빈 응답 · `05 SERVICETIMEOUT`이 가끔 와서(709필지 중 10번) 잠깐 뒤 다시 부릅니다.
- 지역지구 코드는 구분(지역 · 지구 · 구역)마다 따로 매겨져 겹칩니다(「980」 = 용도지구미지정 · 용도구역미지정). 「구분-코드」로 저장하고 「…미지정」은 뺍니다.
- 정리 규칙을 바꿨을 때는 API를 다시 부르지 않고 저장해 둔 응답으로 다시 만들 수 있습니다: `python -m hanpilji.collectors.bld --from-stg`.

</details>

**지표 실측: ECOS · R-ONE · KOSIS (2026-10-28)**

| 지표 | 기관 · 표 | 지역 | 기간 | 값 |
|---|---|---|---|---|
| 기준금리 · 국고채 3년 · 소비자물가 | ECOS 722Y001 · 721Y001 · 901Y009 | 전국 | 2016-11 ~ 2026-09 | 각 119개월 |
| 주택담보대출 금리(신규) | ECOS 121Y006 | 전국 | 2016-11 ~ 2026-08 | 118개월 (한 달 늦게 나옴) |
| 아파트 매매 · 전세 가격지수(월간) | R-ONE A_2024_00045 · A_2024_00050 | 전국 · 서울 · 강남구 | 2016-11 ~ 2026-09 | 각 357개 |
| 주민등록 인구 · 세대 | KOSIS DT_1B040A3 · DT_1B040B3 | 전국 · 서울 · 강남구 | 2016-11 ~ 2026-09 | 각 357개 |

지표 8개 · 2,975개 값, 실패 0. 한 번 다 받는 데 호출 14번(ECOS 4 · R-ONE 표 목록 2 + 값 6 · KOSIS 2).

<details>
<summary><b>지표 응답에서 알게 된 것</b></summary>

- ECOS 표 번호 · 항목 코드(기준금리 `0101000`, 주담대 `BECBLA0302`, 국고채 3년 `5020000`, 소비자물가 총지수 `0`)는 처음 정한 값이 모두 맞았습니다.
- R-ONE에서 「(월) 매매가격지수_아파트」를 넓게 찾으면 규모별 · 연령별 · 계절조정 표까지 4개가 걸려서 표 이름을 정확히 맞춥니다. 표 전체를 받으면 2003년부터 전 지역(226곳)이 와서 10년에 27쪽이므로, 지역 번호(`CLS_ID`: 전국 500001 · 서울 500008 · 강남구 530038)와 기간(`START_WRTTIME` · `END_WRTTIME`)을 걸어 지역마다 1번씩 받습니다.
- KOSIS는 한 번에 40,000셀까지만 줍니다(넘으면 `err 31`). 지역을 전부(`ALL`) 물으면 10년에 넘어서 지역 코드(`00` 전국 · `11` 서울 · `11680` 강남구)를 정해 받습니다.
- 오류 응답 모양(ECOS `INFO-100` · `INFO-200`, R-ONE `ERROR-290`, KOSIS `err 11` · `30`)은 문서와 같았습니다. 시험용 응답은 모두 실제 응답을 줄인 것입니다(`tests/fixtures/*_real.json`).

</details>

<details>
<summary><b>실제 응답에서 알게 된 것</b></summary>

- 행위제한 API는 `type` 값이 없으면 오류(04)를 돌려주고, 응답은 EUC-KR로 옵니다.
- 행위제한은 「아파트」 「휴게음식점」 같은 세부 이름으로 물어야 합니다. 「공동주택」 「근린생활시설」은 결과가 없습니다. 같은 이름이 1종 · 2종 근생처럼 여러 시설군으로 오므로 `휴게음식점(제2종근린생활시설)`처럼 시설군을 붙여 저장합니다.
- 용도지역 코드 16개(UQA111 ~ UQA430)는 모두 맞았습니다.
- 매매 응답에는 단지 번호(`aptSeq`)와 법정동 코드가 없습니다. 전월세에는 단지 번호가 있고, 동 · 지번 · 단지명과 정확히 1:1이라 지금 묶는 방식이 맞습니다. 단지명 끝 괄호만 다른 경우(「강남브리즈힐(토지임대부아파트)」)는 같은 단지로 봅니다.
- 전월세 응답에는 해제 여부 항목이 없습니다. 매매만 `cdealType = O`로 해제를 알려 줍니다.

</details>

<details>
<summary><b>법령 17개 목록</b></summary>

국토의 계획 및 이용에 관한 법률 · 시행령 · 시행규칙, 건축법 · 시행령, 주택법, 주택임대차보호법 · 시행령, 상가건물 임대차보호법, 민간임대주택에 관한 특별법 · 시행령, 부동산 거래신고 등에 관한 법률, 공인중개사법, 도시 및 주거환경정비법, 부동산 가격공시에 관한 법률, 집합건물의 소유 및 관리에 관한 법률, 공동주택관리법

바꾸는 곳: [`config.toml`](config.toml) `[law] names`

</details>

## ⚙️ 정하지 않은 것의 기본값

| 항목 | 기본값 | 바꾸는 곳 |
|---|---|---|
| DB | PostgreSQL 16, DB 하나에 스키마 4개 (+ raw 폴더). 영구 DB는 Supabase(서울) | `HANPILJI_DSN` |
| 공간 · 의미 검색 | 없이도 동작 | `scripts/init_db.sh --postgis --pgvector` |
| 시범 구 | 서울 강남구 (11680) | `config.toml` `[pilot]` |
| GPT 엔진 | Anthropic Claude `claude-opus-5-5`. 마트 함수는 엔진과 무관 | `config.toml` `[gpt]` |
| 법제처 ID | 테스트 계정 `test` | `LAW_OC` |

## 📝 남은 일

- [x] 법제처 실제 응답으로 파서 확인 (2026-10-22)
- [ ] 토지이음 행위제한 CSV를 받아 `eum_rule.py`의 `COLUMN_MAP` · `ALLOWED_MAP` 채우기
- [ ] 토지이음 용도지역 이름이 `core.dim_zone`과 같은지 확인
- [x] 행위제한 API · 아파트 매매 · 전월세 수집기, 시세 마트 함수 (2026-10-23)
- [x] 공공데이터포털 인증키 발급 → 첫 실제 응답으로 항목 이름 · UQ 코드 확인 (2026-10-27)
- [x] 강남구 법정동 코드표로 단지마다 동 · 지번 · 도로명 · PNU 채움 (2026-10-27)
- [x] 단지 카드(`q_property_card`) · 웹 카드 화면 (2026-10-27)
- [x] 건축물대장 수집기 · 필지별 용도지역(건축물대장 지역지구) · 대지면적 → 카드의 건물 · 규제 칸 (2026-10-27)
- [x] 토지 지목 · 공시지가 · 토지이용계획: V-World 강남구 1,168필지 (2026-10-28). 개발키라 공개 웹사이트 전에는 운영키 필요
- [x] 부동산 GPT 엔진: 마트 함수 도구 호출 · 근거 자동 첨부 · 터미널 / 웹 채팅 (2026-10-27)
- [x] Anthropic API 키 등록 → 실제 질문으로 답 품질 확인(모른다 · 투자 질문 포함) (2026-10-27)
- [x] 거래 목록 · 단지 순위 도구 (2026-10-27)
- [ ] 단지명 · 동 이름으로 시군구를 찾는 도구(시범 구가 늘어나면)
- [x] Supabase(서울)에 백업 복원 · 클라우드에서 HTTPS로 GPT · 카드 읽기 (2026-10-27)
- [x] 지표 분석: 실거래 월별 추세 · 공시지가 연도별 추세 · 공시지가 대비 실거래가, 지표 수집기(ECOS · R-ONE · KOSIS) (2026-10-28)
- [x] ECOS · R-ONE · KOSIS 키 발급 · 클라우드 허용 도메인 추가 → 첫 실제 수집으로 표 번호 · 항목 코드 확인 (2026-10-28)
- [x] 자동 최신화: 깃허브 실행기에서 매일 · 매주 · 매월 바뀐 부분만 Supabase로 (2026-10-28)
- [x] 웹사이트 첫 버전: 대화 + 근거 서랍, 답변 블록, 진행 표시, 기간 바꾸기, 휴대폰 화면 (2026-10-28)
- [x] 웹 2차 1부: Pretendard 글꼴, 결론 + 핵심 숫자 + 확인 못 한 것 답 모양, 숫자 검산, 이어서 볼 것, 첫 화면 비교 (2026-10-28)
- [x] 웹 2차 2부: 필지 리포트(모델 없이, 인쇄용) · 두 단지 비교 (2026-10-29)
- [ ] 웹사이트 올리기(호스팅 · 도메인 결정, 지도 블록은 V-World 운영키 뒤)
- [x] 경매 · 공매 분석: 공매 수집기(온비드) · 실거래 대비 할인율 · 입찰 전 권리 확인 항목, 경매 법령 4개 추가 (2026-10-28)
- [x] 온비드 물건목록 API 활용신청 → 첫 실제 응답으로 항목 이름 확인 (2026-10-28: 강남구 입찰 예정 공매 18건, 대부분 오피스텔 · 업무 · 상가)
- [ ] 경매 법령 4개 Supabase 적재
- [x] 오피스텔 실거래 수집기 · 공매 할인율 오피스텔 비교(같은 필지 주상복합도 종류별로) · 매일 최신화 (2026-10-28)
- [x] 오피스텔 API 2개 활용신청 → 실제 응답 항목 이름 확인 · 강남구 36개월 로컬 적재: 매매 2,890건 · 전월세 12,047건 · 225단지 (2026-10-29)
- [x] 오피스텔 실거래 Supabase 반영 (2026-10-29)
- [x] 연립다세대 실거래 수집기 · 공매 할인율 다세대 비교 · 매일 최신화 (2026-10-29)
- [ ] 연립다세대 강남구 36개월 적재 · Supabase 반영
- [ ] 연립다세대 실거래 수집(강남구 공매에 다세대 1건)
- [ ] 새 공매 물건 · 유찰로 값이 내려간 물건 알림(매주, 웹사이트가 생기면)
- [ ] 법원경매 물건: 공식 API가 생기거나 제휴 데이터가 정해지면
- [ ] V-World 상업 이용 회신
- [x] 지적도 · 건물 윤곽 적재 코드 · 지도 함수(`q_parcel_map`) · 웹 지도 블록 (2026-10-28)
- [x] 국내 PC에서 강남구 지적도 받기 → 필지 경계 34,658개(면적 합 39.5㎢) · 건물 윤곽 24,235개, 한필지 필지 1,149/1,168개에 경계 (2026-10-28)
- [x] 지적도 Supabase 반영(`python -m hanpilji.supabase push-geo`) (2026-10-28)
- [ ] 경계가 없는 한필지 필지 19개: 본번 0 같은 잘못 만든 PNU · 합병 · 분할로 바뀐 번지 확인

## 📁 폴더

```text
hanpilji/
├── config.toml              # 정하지 않은 값은 여기만 바꾸면 됨
├── hanpilji/
│   ├── collectors/
│   │   ├── law.py           # 법제처 → core.doc_law
│   │   ├── eum_rule.py      # 토지이음 CSV → core.reg_rule
│   │   ├── luinfo.py        # 행위제한 API → core.reg_rule
│   │   ├── rtms.py          # 아파트 · 오피스텔 · 연립다세대 매매 · 전월세 → core.fact_deal · fact_rent
│   │   ├── bld.py           # 건축물대장 → core.dim_building · bridge_parcel_zone · dim_complex
│   │   ├── vworld.py        # V-World 토지특성 · 토지이용계획 → dim_parcel 지목 · fact_land_price · bridge_parcel_zone
│   │   ├── vworld_geom.py   # V-World 연속지적도 · 도로명주소 건물(국내 PC에서 받은 zip) → core.geo_parcel · geo_building
│   │   ├── market.py        # ECOS · R-ONE · KOSIS → core.fact_market
│   │   └── onbid.py         # 온비드 공매 물건 → core.fact_auction
│   ├── datagokr.py          # 공공데이터포털 공통(키 · 호출 · 응답 풀기)
│   ├── gpt/
│   │   ├── engine.py        # 질문 → 도구 호출 반복 → 답 + 근거
│   │   ├── tools.py         # 마트 함수 14개 = 도구 정의 · 인자 확인
│   │   ├── llm.py           # 제공자(Anthropic) · 교체 지점
│   │   ├── __main__.py      # 터미널 대화
│   │   ├── blocks.py        # 도구 결과 → 화면 블록(표 · 그래프 · 조문 · 카드 · 지도)
│   │   ├── web.py           # 웹 서버(질문 흐름 · 블록 다시 조회)
│   │   └── static/          # 웹 화면(index.html · app.css · app.js)
│   ├── ingest.py            # 원본 보관 · 수집 기록 · 커서
│   ├── supabase.py          # Supabase를 HTTPS로: 백업 복원 · GPT용 읽기 연결
│   └── textnorm.py          # 「100분의 5」 → 「5%」
├── sql/00~15_*.sql          # 스키마 · 표 · 마트 · 권한 · 기준값 (15_geo는 PostGIS 있을 때)
├── scripts/init_db.sh       # 빈 DB에 스키마 올리기
├── scripts/restore_db.sh    # 백업에서 되살리기
├── scripts/vworld_fetch_geom_gangnam.py  # 국내 PC용: 강남구 지적도 · 건물 윤곽 받기(표준 라이브러리만)
├── tests/                   # fixtures/*_real.xml 은 법제처 실제 응답
└── docs/assets/             # README 도트 그림 (make_banner.py로 다시 만듦)
```

---

<div align="center">
<sub>데이터 출처: 법제처 국가법령정보센터 · 국토교통부 토지이음 · 공공데이터포털 &nbsp;|&nbsp; 법령 본문은 저작권법 제7조에 따라 보호 대상이 아닌 저작물입니다<br>머리 그림은 이 저장소의 <code>docs/assets/make_banner.py</code>가 그린 것입니다</sub>
</div>
