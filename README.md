<div align="center">

<img src="docs/assets/banner.svg" alt="HANPILJI 한필지 도트 그림" width="100%">

# 한필지 (가칭)

**필지 하나로 잇는 부동산 공공데이터셋**<br>
땅 · 건물 · 거래 · 규제 데이터를 필지 번호(PNU)로 묶고, 그 데이터로만 답하는 부동산 GPT를 얹습니다.

![stage](https://img.shields.io/badge/stage-3%20GPT%20%EC%97%B0%EA%B2%B0-f6d365?style=for-the-badge)
![laws](https://img.shields.io/badge/%EB%B2%95%EB%A0%B9-17%EA%B0%9C%20%C2%B7%201%2C542%EC%A1%B0-81c995?style=for-the-badge)
![tests](https://img.shields.io/badge/tests-54%20passed-4fc3f7?style=for-the-badge)

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![Claude](https://img.shields.io/badge/LLM-Claude%20API-d97757?logo=anthropic&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![PostGIS](https://img.shields.io/badge/PostGIS-optional-5CAE58)
![pgvector](https://img.shields.io/badge/pgvector-optional-8a63d2)
![Data](https://img.shields.io/badge/data-%EB%B2%95%EC%A0%9C%EC%B2%98%20%C2%B7%20%ED%86%A0%EC%A7%80%EC%9D%B4%EC%9D%8C%20%C2%B7%20%EA%B3%B5%EA%B3%B5%EB%8D%B0%EC%9D%B4%ED%84%B0%ED%8F%AC%ED%84%B8-f28b82)

[데이터 흐름](#-데이터-흐름) · [빠른 시작](#-빠른-시작) · [GPT가 부르는 함수](#-gpt가-부르는-마트-함수) · [부동산 GPT](#-부동산-gpt) · [Supabase](#️-supabase-영구-db) · [진행 상황](#-진행-상황) · [남은 일](#-남은-일)

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
python -m hanpilji.collectors.law                     # 법령 17개 → core.doc_law
python -m hanpilji.collectors.eum_rule 행위제한.csv    # 토지이음에서 내려받은 파일

# 공공데이터포털 인증키가 있으면 (키는 환경변수로만)
export DATA_GO_KR_KEY=...
python -m hanpilji.collectors.luinfo                  # 행위제한 API → core.reg_rule (조례 기준)
python -m hanpilji.collectors.rtms                    # 아파트 매매 · 전월세 36개월 → core.fact_deal · fact_rent
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

`q_complex_price`는 단지 x 전용면적마다 거래단가 · 거래가격 · 전세단가 · 전세시세를 한 줄로 돌려줍니다. 해제된 거래와 월세는 빼고, ㎡당 · 평당 단가와 전세가율도 같이 줍니다.

`q_deal_list`는 거래를 한 건씩(계약일 · 면적 · 층 · 금액 · 신규/갱신) 돌려주고, `price_desc`로 정렬하면 첫 줄이 최고가입니다. `q_complex_rank`는 단지 이름 없이 매매 · 전세 · 월세 건수, ㎡당 단가, 전세가율로 줄을 세웁니다. 단가 · 전세가율 순위는 거래가 3건 이상인 단지만 넣고, 준공연도는 매매 신고의 건축년도를 씁니다.

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
python -m hanpilji.gpt.web --port 8000                          # 웹 채팅 → http://127.0.0.1:8000
```

웹 채팅은 파이썬 표준 라이브러리만 씁니다. 위쪽 **단지 · 주소 한눈에**에 단지명이나 「동 지번」(예: `대치동 316`)을 넣으면 LLM을 부르지 않고(요금 없음) 마트 함수만으로 카드를 그립니다. 아래쪽은 예시 질문 버튼 6개(종합 · 최고가 · 순위 · 용도지역 · 법령 · 투자 질문)가 있는 채팅입니다.

<p align="center"><img src="docs/assets/demo_card.png" width="720" alt="단지 카드 화면: 위치 · 거래 · 토지 · 용도지역 · 건물 다섯 칸과 최근 매매 5건"></p>

카드의 다섯 칸은 PNU로 잇습니다. 실거래가의 동 이름 · 지번을 법정동 코드표로 바꿔 단지마다 PNU(19자리)를 만들고, 토지 · 용도지역 · 건축물대장 표에 같은 PNU가 들어오면 그 칸이 자동으로 채워집니다. 아직 수집 전인 칸은 「데이터 수집 전」으로 보이고, GPT도 그 칸은 모른다고 답합니다.

남에게 열 때(`--host 0.0.0.0`)는 질문마다 API 요금이 나가므로 `HANPILJI_WEB_TOKEN`을 정해야만 켜지고, 주소는 `http://서버:8000/?token=정한값`입니다. 접속 주소마다 시간당 30개까지 받습니다.

> [!IMPORTANT]
> 클라우드 환경의 로컬 PostgreSQL은 세션마다 새로 떠서 데이터가 남지 않습니다. 오래 남길 데이터는 [Supabase](#️-supabase-영구-db)에 있고, 클라우드 세션에서는 `HANPILJI_DB_VIA=supabase-api`를 주면 GPT · 웹 카드가 거기서 읽습니다.

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

## 📊 진행 상황

| 단계 | 내용 | 상태 |
|---|---|---|
| 0 | 규제 축: 법제처 법령 · 행위제한 | 🟢 법령 완료 · 🟢 행위제한 API로 강남구 수집 (CSV 없이) |
| 1 | 공공데이터포털 인증키 · 호출 확인 | 🟢 실제 응답으로 항목 이름 · 코드 확인 (2026-10-27) |
| 2 | 시세 축: 시범 구(강남구) 3년치 | 🟢 매매 1만 432건 · 전월세 7만 924건 적재 |
| 3 | GPT 엔진 연결 | 🟢 Claude API 도구 호출 엔진, 실제 질문 15개로 확인 (2026-10-27) |
| 4 | 데모 | 🟡 단지 카드(위치 · 거래 + PNU로 토지 · 용도지역 · 건물 자리) · 웹 채팅. 토지 · 건축물대장 수집 남음 |

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
- [ ] 토지(지목 · 면적 · 공시지가) · 필지별 용도지역 수집 → 카드의 토지 · 규제 칸 채우기
- [ ] 건축물대장 수집기
- [x] 부동산 GPT 엔진: 마트 함수 도구 호출 · 근거 자동 첨부 · 터미널 / 웹 채팅 (2026-10-27)
- [x] Anthropic API 키 등록 → 실제 질문으로 답 품질 확인(모른다 · 투자 질문 포함) (2026-10-27)
- [x] 거래 목록 · 단지 순위 도구 (2026-10-27)
- [ ] 단지명 · 동 이름으로 시군구를 찾는 도구(시범 구가 늘어나면)
- [x] Supabase(서울)에 백업 복원 · 클라우드에서 HTTPS로 GPT · 카드 읽기 (2026-10-27)
- [ ] V-World 상업 이용 회신

## 📁 폴더

```text
hanpilji/
├── config.toml              # 정하지 않은 값은 여기만 바꾸면 됨
├── hanpilji/
│   ├── collectors/
│   │   ├── law.py           # 법제처 → core.doc_law
│   │   ├── eum_rule.py      # 토지이음 CSV → core.reg_rule
│   │   ├── luinfo.py        # 행위제한 API → core.reg_rule
│   │   └── rtms.py          # 아파트 매매 · 전월세 → core.fact_deal · fact_rent
│   ├── datagokr.py          # 공공데이터포털 공통(키 · 호출 · 응답 풀기)
│   ├── gpt/
│   │   ├── engine.py        # 질문 → 도구 호출 반복 → 답 + 근거
│   │   ├── tools.py         # 마트 함수 4개 = 도구 정의 · 인자 확인
│   │   ├── llm.py           # 제공자(Anthropic) · 교체 지점
│   │   ├── __main__.py      # 터미널 대화
│   │   └── web.py           # 웹 채팅
│   ├── ingest.py            # 원본 보관 · 수집 기록 · 커서
│   ├── supabase.py          # Supabase를 HTTPS로: 백업 복원 · GPT용 읽기 연결
│   └── textnorm.py          # 「100분의 5」 → 「5%」
├── sql/00~10_*.sql          # 스키마 · 표 · 마트 · 권한 · 기준값
├── scripts/init_db.sh
├── tests/                   # fixtures/*_real.xml 은 법제처 실제 응답
└── docs/assets/             # README 도트 그림 (make_banner.py로 다시 만듦)
```

---

<div align="center">
<sub>데이터 출처: 법제처 국가법령정보센터 · 국토교통부 토지이음 · 공공데이터포털 &nbsp;|&nbsp; 법령 본문은 저작권법 제7조에 따라 보호 대상이 아닌 저작물입니다<br>머리 그림은 이 저장소의 <code>docs/assets/make_banner.py</code>가 그린 것입니다</sub>
</div>
