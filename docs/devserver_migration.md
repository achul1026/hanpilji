# 한필지 DB를 공용 PostgreSQL 서버의 스키마 하나로 옮기기

Supabase에 있는 한필지 데이터를 다른 서비스와 같이 쓰는 PostgreSQL 컨테이너 `<컨테이너>`의 DB `<DB>` 안,
**스키마 `hanpilji`** 로 옮기는 절차입니다. 서버 명령은 사람이 직접 실행합니다. 비밀번호 자리는 `<…>`로 비워 두었습니다.

- 정한 이름: 스키마 **`hanpilji`**, 앱 계정 **`hanpilji_app`**(스키마 주인). 웹 · GPT용 읽기 계정 **`hanpilji_gpt`** 를 하나 더 권합니다(조회 함수 · 뷰만 읽고 표는 못 읽음. 지금 Supabase와 같은 구성).
- 다른 서비스의 스키마와 `public`에는 아무것도 만들지 않습니다. 한필지 객체는 바깥 스키마를 참조하지 않습니다(확인함).
- 이전은 끝났습니다. 웹사이트(Cloud Run)와 자동 갱신(서버 cron의 `scripts/batch.sh`)은 개발서버의 `hanpilji` 스키마만 씁니다. Supabase는 2026-11-24부터 쓰지 않고, 최종 백업은 프로젝트 공유 폴더 `hanpilji-data/supabase_final_20261120/`에 있습니다. 아래 1~6단계는 그때 한 절차의 기록입니다.

## 1. 지금 DB 파악(이미 확인한 결과)

| 항목 | 값 |
|---|---|
| 원래 DB | Supabase, PostgreSQL 17.11 (18 이하 ✓) |
| 쓰는 확장 | `postgis` 3.3.7 · **`pg_trgm` 1.6** (법령 검색 점수 `word_similarity`와 색인 하나) · `vector` 0.8.2는 설치만 돼 있고 쓰는 열 없음 |
| 표가 있던 곳 | `public` 아님. 자기 스키마 4개(ops · stg · core · mart) → **이미 `hanpilji` 하나로 합친 백업**을 만들어 둠. 서버에서 임시 DB가 필요 없음 |
| 크기 | 표 40개 · 562,959줄 · DB 약 322MB · 백업 파일 약 27MB |
| 객체 수 | 표 40 · 뷰 6 · 함수 20 · 시퀀스 4 · 색인 59 · 트리거 0 · 바깥 스키마를 가리키는 외래키 0 |
| 함수 본문의 `public.` | 0곳. 함수 17개는 `SECURITY DEFINER` + `SET search_path = pg_catalog, public` (PostGIS · pg_trgm을 `public`에서 찾음) |
| 공간 · 벡터 열 | 공간: `geo_parcel.geom` · `geo_building.geom` (SRID 5186). 벡터 열은 없음 |
| 정렬 규칙 | 원래 ICU `en-US` → 서버 `C.UTF-8`. 조회 함수 7개를 C.UTF-8 DB에서 돌려 Supabase와 줄 순서까지 같음을 확인 |

**확인이 필요한 것 하나:** `pg_trgm`이 대상 DB에 없으면 복원이 멈춥니다. 2단계에서 먼저 조회하고, 없으면 설치 여부를 알려 주세요(공용 DB라 확장 설치는 관리자가 정함).

## 2. 서버 상태 조회(바꾸는 것 없음)

```bash
# [PC]
ssh -p <SSH 포트> <서버 계정>@<서버>
```

```bash
# [서버] 확장 · 스키마 · 계정이 지금 어떤지
docker exec -i <컨테이너> psql -U <관리자> -d <DB> <<'SQL'
SELECT extname, extversion, extnamespace::regnamespace AS schema FROM pg_extension ORDER BY 1;
SELECT name, default_version FROM pg_available_extensions WHERE name = 'pg_trgm';
SELECT nspname FROM pg_namespace WHERE nspname = 'hanpilji';
SELECT rolname FROM pg_roles WHERE rolname IN ('hanpilji_app', 'hanpilji_gpt');
SQL
df -h /data
```

바라는 결과: 확장 목록에 `postgis` · `vector`가 `public`에 있고, **`pg_trgm`도 있어야 함**. `hanpilji` 스키마와 두 계정은 아직 없어야 함.
`pg_trgm`이 첫 목록에 없고 두 번째 조회에만 보이면(PostgreSQL에 기본으로 딸려 있는 확장이라 보통 보임) 알려 주세요.

## 3. 파일 옮기기

프로젝트 파일 `hanpilji-migration/` 폴더를 PC로 받습니다. 안에 든 것:

| 파일 | 쓰임 |
|---|---|
| `hanpilji_20261102_single.dump` | `hanpilji` 스키마만 든 백업(`pg_dump -Fc --no-owner --no-acl -n hanpilji`, 확장 없음) |
| `grants.sql` | 웹 · GPT 읽기 계정 권한 |
| `check_counts.sql` | 표 건수 한 줄 지문 |

```bash
# [서버] 작업 폴더
mkdir -p <작업 폴더>
```

```bash
# [PC] hanpilji-migration 폴더가 있는 곳에서
scp -P <SSH 포트> hanpilji-migration/* <서버 계정>@<서버>:<작업 폴더>/
```

```bash
# [서버] 받은 파일이 맞는지(크기 약 27MB, sha256은 아래와 같아야 함)
ls -la <작업 폴더>/
sha256sum <작업 폴더>/hanpilji_20261102_single.dump
```

`5be6182386cce9401fe6b8a652d0898bd7e3f2f687e229f159fbbfde4bd71176`

## 4. 계정 · 스키마 만들기

```bash
# [서버] 비밀번호 두 개 만들기(메모해 두기)
openssl rand -hex 20   # hanpilji_app
openssl rand -hex 20   # hanpilji_gpt
```

```bash
# [서버] 관리자 계정으로
docker exec -i <컨테이너> psql -U <관리자> -d <DB> -v ON_ERROR_STOP=1 <<'SQL'
CREATE ROLE hanpilji_app LOGIN PASSWORD '<hanpilji_app 비밀번호>';
CREATE ROLE hanpilji_gpt LOGIN PASSWORD '<hanpilji_gpt 비밀번호>';
CREATE SCHEMA hanpilji AUTHORIZATION hanpilji_app;
REVOKE ALL ON SCHEMA hanpilji FROM PUBLIC;
ALTER ROLE hanpilji_app IN DATABASE <DB> SET search_path = hanpilji, public;
ALTER ROLE hanpilji_gpt IN DATABASE <DB> SET search_path = hanpilji, public;
SQL
```

## 5. 복원

백업 안에 `CREATE SCHEMA hanpilji`가 들어 있어서, 4단계에서 만든 스키마와 부딪치지 않게 그 한 줄을 뺀 목록(`restore.list`)으로 복원합니다.
여기서부터는 사람이 덜 쓰는 시간에 하세요(시험 복원은 5초 걸림).

```bash
# [서버]
docker cp <작업 폴더>/hanpilji_20261102_single.dump <컨테이너>:/tmp/hanpilji.dump
docker exec <컨테이너> sh -c \
  "pg_restore -l /tmp/hanpilji.dump | grep -v ' SCHEMA - hanpilji ' > /tmp/hanpilji.list"
docker exec <컨테이너> pg_restore -U <관리자> -d <DB> \
  --no-owner --no-privileges --role=hanpilji_app --single-transaction --exit-on-error \
  -L /tmp/hanpilji.list /tmp/hanpilji.dump
docker exec <컨테이너> rm /tmp/hanpilji.dump /tmp/hanpilji.list
```

```bash
# [서버] 읽기 계정 권한 + hanpilji 표만 통계 갱신(통계가 없으면 단지 순위 조회가 5초 제한에 걸림)
docker exec -i <컨테이너> psql -U <관리자> -d <DB> -v ON_ERROR_STOP=1 < <작업 폴더>/grants.sql
docker exec -i <컨테이너> psql -U <관리자> -d <DB> -v ON_ERROR_STOP=1 <<'SQL'
SELECT format('ANALYZE hanpilji.%I', tablename) FROM pg_tables WHERE schemaname = 'hanpilji' \gexec
SQL
```

## 6. 확인

```bash
# [서버] 한 줄 지문: 아래 값과 같으면 표 40개 건수가 모두 같음
docker exec -i <컨테이너> psql -U <관리자> -d <DB> < <작업 폴더>/check_counts.sql
```

| fingerprint | tables | total_rows |
|---|---|---|
| `a36eb2849347a16f16d3ae457eff524d` | 40 | 562959 |

이 값은 2026-11-02 03:07 UTC에 Supabase에서 같은 쿼리로 뽑은 것입니다. 백업은 이때 Supabase를 조회만 해서 복사했고, 표 40개 모두 줄 내용까지 같음을 비교했습니다.

```bash
# [서버] 공간 열 · 조회 함수 · 막혀야 하는 것
docker exec -i <컨테이너> psql -U <관리자> -d <DB> <<'SQL'
SET ROLE hanpilji_app;
SELECT pnu, ST_SRID(geom) AS srid, round(ST_Area(ST_Transform(geom, 4326)::geography)) AS m2
FROM hanpilji.geo_parcel ORDER BY pnu LIMIT 1;           -- 1168010100106010000 | 5186 | 494
RESET ROLE;
SET ROLE hanpilji_gpt;
SELECT count(*) FROM hanpilji.q_law_search('건폐율', NULL);   -- 43
SELECT count(*) FROM hanpilji.fact_deal;                   -- permission denied (표는 못 읽어야 맞음)
SQL
```

```bash
# [서버] 앱 계정으로 접속(컨테이너 안 소켓)해 다른 서비스 스키마(<다른 스키마>) 표 하나를 읽으면 막혀야 함
docker exec -i <컨테이너> psql -U hanpilji_app -d <DB> <<'SQL'
SELECT current_user, current_setting('search_path') AS search_path;      -- hanpilji_app | hanpilji, public
SELECT format('SELECT 1 FROM <다른 스키마>.%I LIMIT 1', tablename) FROM pg_tables WHERE schemaname = '<다른 스키마>' LIMIT 1 \gexec
-- ERROR:  permission denied for schema <다른 스키마> 가 나오면 맞음
SQL
```

벡터 열은 한필지에 아직 없어서 확인할 것이 없습니다.

## 7. 앱 연결 바꾸기

코드에 `public.`을 직접 적은 곳은 없습니다. 대신 SQL이 `core.` · `mart.` · `ops.` · `stg.`를 쓰므로,
환경변수 `HANPILJI_DB_SCHEMA=hanpilji`를 넣으면 보내기 직전에 `hanpilji.`로 바꿉니다(`hanpilji/db.py`, PR #41). 없으면 네 스키마 이름 그대로 보냅니다(로컬 DB용).

| 환경변수 | 값 |
|---|---|
| `HANPILJI_DB_SCHEMA` | `hanpilji` |
| `HANPILJI_DSN` (수집기) | `postgresql://hanpilji_app:<비밀번호>@<서버>:<포트>/<DB>` |
| `HANPILJI_GPT_DSN` (웹 · GPT) | `postgresql://hanpilji_gpt:<비밀번호>@<서버>:<포트>/<DB>` |

지금 구성(2026-11-24):

1. Cloud Run 서비스 `hanpilji-git`: `HANPILJI_GPT_DSN`은 개발서버 `hanpilji_gpt` 주소, `HANPILJI_DB_SCHEMA=hanpilji`.
2. 자동 갱신: 서버 cron이 `scripts/batch.sh daily|weekly|monthly`를 돌림(설정은 저장소 옆 `.env`). 예전 GitHub Actions `refresh.yml`은 껐고 저장소에서 지웠습니다.

## 8. 이후 백업

```bash
# [서버] hanpilji 스키마만. 다른 서비스의 복원 · 동기화가 자기 스키마만 다루면 이 스키마는 그대로 남음
docker exec <컨테이너> pg_dump -U <관리자> -d <DB> -n hanpilji -Fc -Z 6 --no-owner --no-privileges \
  -f /tmp/hanpilji_$(date +%Y%m%d).dump
docker cp <컨테이너>:/tmp/hanpilji_$(date +%Y%m%d).dump <작업 폴더>/
docker exec <컨테이너> rm /tmp/hanpilji_$(date +%Y%m%d).dump
```
