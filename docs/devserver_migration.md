# 한필지 데이터를 개발서버(AI-PLUG 플레이그라운드 DB)로 옮기기

Supabase에 있는 한필지 데이터를 개발서버 PostgreSQL 안의 **스키마 하나(`hanpilji`)** 로 옮기는 방법입니다.
AI-PLUG 플레이그라운드(5대 도메인)와 같은 DB를 쓰므로, 한필지의 네 스키마(ops · stg · core · mart)를
`hanpilji` 하나로 합쳤습니다. 표 · 뷰 · 함수 이름이 겹치는 게 없어서 합쳐도 잃는 것이 없습니다.

- 옮긴 뒤에도 웹사이트와 자동 갱신(GitHub Actions)은 Supabase를 그대로 씁니다. 바꾸려면 [6단계](#6-나중에-웹사이트--자동-갱신까지-옮기려면)를 보세요.
- 이 문서의 명령은 개발서버에서 돌립니다. 클라우드 작업 환경에서는 회사 서버에 접속할 수 없습니다.

## 준비물

| 파일 | 어디에 | 쓰임 |
|---|---|---|
| `hanpilji_YYYYMMDD_single.dump` | 프로젝트 파일 `hanpilji-data/` | `hanpilji` 스키마 하나로 합친 백업(pg_dump 사용자 지정 형식) |
| `sql/single_schema/grants.sql` | 이 저장소 | 복원 뒤 GPT 읽기 계정 권한 |
| `sql/single_schema/check_counts.sql` | 이 저장소 | 복원 뒤 표마다 건수 |

같은 날짜의 `hanpilji_YYYYMMDD.dump`는 지금 구성(네 스키마) 그대로의 백업입니다. Supabase를 되살릴 때 씁니다.

## 1. 컨테이너

PostgreSQL 17 + PostGIS가 꼭 있어야 합니다(지적도 · 건물 윤곽 표가 `geometry`를 씀). 법령 검색 색인에
`pg_trgm`도 쓰는데 PostgreSQL에 기본으로 들어 있습니다. AI-PLUG 안내서가 pgvector를 쓰므로 함께 넣는 것을 권합니다.

```dockerfile
# Dockerfile: PostGIS 공식 이미지에 pgvector만 더함
FROM postgis/postgis:17-3.5
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-17-pgvector \
 && rm -rf /var/lib/apt/lists/*
```

```bash
docker build -t aiplug-postgres .
docker run -d --name aiplug-db -p 5432:5432 \
  -e POSTGRES_PASSWORD='<관리자 비밀번호>' -e POSTGRES_DB=aiplug_demo \
  -v aiplug-pgdata:/var/lib/postgresql/data aiplug-postgres
```

이미 컨테이너를 만들었다면 `docker exec aiplug-db psql -U postgres -c "select * from pg_available_extensions where name in ('postgis','vector','pg_trgm')"`
로 세 확장이 보이는지만 확인하면 됩니다. 아래 명령의 `aiplug-db` · `aiplug_demo`는 실제 컨테이너 · DB 이름으로 바꾸세요.

## 2. 확장 켜기(한 번만)

```bash
docker exec -i aiplug-db psql -U postgres -d aiplug_demo -v ON_ERROR_STOP=1 <<'SQL'
CREATE EXTENSION IF NOT EXISTS postgis;   -- public 스키마에 만듦(백업이 public.geometry를 찾음)
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;    -- 한필지에는 아직 필요 없음. AI-PLUG용
SQL
```

PostGIS가 이미 다른 스키마(예: `extensions`)에 설치돼 있으면 복원이 `type "public.geometry" does not exist`로 멈춥니다.
그때는 알려 주세요. 그 스키마에 맞춘 백업을 다시 만들어 드립니다.

## 3. 복원

```bash
# 다시 넣는 경우만: 예전 hanpilji 스키마를 지움(다른 도메인 스키마는 그대로)
docker exec aiplug-db psql -U postgres -d aiplug_demo -c "DROP SCHEMA IF EXISTS hanpilji CASCADE"

docker cp hanpilji_YYYYMMDD_single.dump aiplug-db:/tmp/hanpilji.dump
docker exec aiplug-db pg_restore -U postgres -d aiplug_demo --no-owner --no-acl --exit-on-error /tmp/hanpilji.dump
docker exec aiplug-db rm /tmp/hanpilji.dump
docker exec aiplug-db psql -U postgres -d aiplug_demo -c "ANALYZE"   # 꼭 할 것: 통계가 없으면 단지 순위 조회가 5초를 넘겨 끊김
```

1분 안에 끝납니다(시험 복원 5초. 약 56만 줄, 백업 파일 약 27MB).

## 4. GPT 읽기 계정

웹 · GPT는 `hanpilji_gpt` 계정으로 조회 함수(`q_*`)와 뷰 3개만 읽습니다. 표는 직접 못 읽습니다(Supabase와 같음).

```bash
docker exec -i aiplug-db psql -U postgres -d aiplug_demo -v ON_ERROR_STOP=1 < sql/single_schema/grants.sql
docker exec aiplug-db psql -U postgres -d aiplug_demo -c "ALTER ROLE hanpilji_gpt PASSWORD '<GPT 계정 비밀번호>'"
```

수집기(자동 갱신)용으로 쓰기 계정이 따로 필요하면 `hanpilji` 스키마에만 권한을 주면 됩니다.

```sql
CREATE ROLE hanpilji_etl LOGIN PASSWORD '<비밀번호>';
GRANT USAGE, CREATE ON SCHEMA hanpilji TO hanpilji_etl;
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA hanpilji TO hanpilji_etl;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA hanpilji TO hanpilji_etl;
```

## 5. 확인

```bash
docker exec -i aiplug-db psql -U postgres -d aiplug_demo < sql/single_schema/check_counts.sql
```

아래 표와 건수가 같으면 끝입니다. 이 숫자는 백업을 만든 시점의 Supabase 건수이고, 줄마다 내용까지 같은지 비교했습니다.

<!-- COUNTS -->

조회 함수가 도는지도 한 번 봅니다.

```bash
docker exec aiplug-db psql -U postgres -d aiplug_demo -c \
  "SET ROLE hanpilji_gpt; SELECT count(*) FROM hanpilji.q_law_search('건폐율', NULL)"
```

## 앱을 개발서버에 붙이는 법

코드는 그대로 두고 환경변수만 바꿉니다. `HANPILJI_DB_SCHEMA=hanpilji`가 있으면 SQL 속 `core.` · `mart.` · `ops.` · `stg.`를
보내기 직전에 `hanpilji.`로 바꿉니다(`hanpilji/db.py`). 없으면 지금처럼 Supabase 구성으로 돕니다.

| 환경변수 | 값 |
|---|---|
| `HANPILJI_DB_SCHEMA` | `hanpilji` |
| `HANPILJI_DSN` | `postgresql://hanpilji_etl:<비밀번호>@<서버>:5432/aiplug_demo` (수집기) |
| `HANPILJI_GPT_DSN` | `postgresql://hanpilji_gpt:<비밀번호>@<서버>:5432/aiplug_demo` (웹 · GPT) |
| `HANPILJI_DB_VIA` | 비움(이 값은 Supabase를 HTTPS로 읽을 때만 씀) |

## 6. 나중에 웹사이트 · 자동 갱신까지 옮기려면

지금은 하지 않습니다. 옮기기로 하면 아래가 바뀝니다.

1. **접속 경로:** Cloud Run(Google 서울)과 GitHub Actions 실행기(해외 IP)가 개발서버 5432에 닿아야 합니다.
   회사 내부망 서버라면 방화벽을 열거나 터널을 둬야 하고, 이게 안 되면 웹사이트는 Supabase에 남아야 합니다.
2. **Cloud Run:** 서비스 `hanpilji-git`의 환경변수 `HANPILJI_GPT_DSN`을 개발서버 주소로, `HANPILJI_DB_SCHEMA=hanpilji`를 추가.
3. **GitHub Actions(`refresh.yml`):** 비밀값 `HANPILJI_DSN`을 개발서버 쓰기 계정 주소로, `env`에 `HANPILJI_DB_SCHEMA: hanpilji` 추가.
   개발서버가 해외 IP를 막으면 실행기 대신 개발서버의 cron으로 같은 수집기를 돌립니다(V-World도 이때는 받을 수 있음).
4. **Supabase:** 아무 것도 접속하지 않으면 무료 플랜은 7일 뒤 멈춥니다. 지워도 되는지는 그때 정합니다.
   `python -m hanpilji.supabase …`(HTTPS 복원 · 건수 비교)는 Supabase 전용이라 개발서버에는 쓰지 않습니다.

## 백업을 새로 만들 때(클라우드 작업 환경)

클라우드에서는 Supabase에 DB로 바로 붙을 수 없어서(HTTPS만 열림) `pg_dump`를 Supabase에 직접 못 겁니다.
대신 Supabase를 조회만 해서 로컬 PostgreSQL에 그대로 복사한 뒤 백업을 뜹니다. 자동 갱신이 도는 동안에는 숫자가 움직이니
끝난 뒤에 돌립니다.

```bash
apt-get install -y postgresql-16-postgis-3 postgresql-16-pgvector
createdb hanpilji_mirror
HANPILJI_DSN=postgresql:///hanpilji_mirror scripts/init_db.sh --postgis --pgvector
psql -d hanpilji_mirror -c "ALTER TABLE core.dim_building DROP COLUMN geom; ALTER TABLE core.dim_parcel DROP COLUMN geom; ALTER TABLE core.doc_law DROP COLUMN embedding"
python scripts/supabase_mirror.py --verify                     # Supabase → 로컬, 건수 · 줄 내용 비교
pg_dump -Fc -Z 6 -d hanpilji_mirror -n ops -n stg -n core -n mart -f hanpilji_YYYYMMDD.dump
scripts/make_single_schema_dump.sh hanpilji_mirror hanpilji_YYYYMMDD_single.dump
```

`ALTER TABLE … DROP COLUMN` 줄은 Supabase에 없는 열 셋(PostGIS · pgvector 선택 파일에서만 만드는 것)을 맞추는 것입니다.
개발서버가 클라우드에서 닿는 곳이면 서버에서 `pg_dump "$SUPABASE_DSN" …`로 바로 떠도 됩니다.
