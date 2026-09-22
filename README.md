# AI 마음 탐사대 백엔드

계명대학교 디지털상담 연구실의 청소년 생성형 AI 사용 종단 연구를 위한 Python 백엔드입니다. 별도 관리되는 `ai_us` 프론트엔드의 기존 HTML 및 inline script 구조를 유지하면서, 설문·대화문·참여자 계정·연구자 관리 데이터를 MongoDB에 저장합니다.

## 주요 기능

- 참여 신청: 동의 항목, 연락처, 학교급·학년을 검증하고 중복 휴대폰 번호 신청을 차단합니다.
- 참여자 인증: 휴대폰 번호 로그인, JWT 인증, 공통 초기 비밀번호 `1234`의 최초 변경 강제를 지원합니다.
- 비밀번호 재설정: 참여자 본인 휴대폰 번호와 보호자 휴대폰 번호가 일치하면 비밀번호를 `1234`로 초기화하고 다음 로그인에서 변경을 강제합니다.
- 연구자 권한: `admin`은 연구자 계정을 생성하고, `admin`과 `researcher`는 신청 조회·승인, CSV 등록, 참여 현황, 미완료자·어뷰징 검토, 결과 다운로드를 수행합니다.
- 설문 응답: `surveyRound`와 `surveyVersion`으로 회차와 문항 버전을 구분하고, 최종 제출을 MongoDB 영속 Queue에 접수합니다.
- 대화문 제출: 링크·본문·ZIP·이미지 원본을 보존합니다. 본문 parser, 서비스별 export adapter, Gemini screenshot parser를 비동기 작업으로 실행하고 버전·경고·정규화 결과를 기록합니다.
- 대화문 검토: 기존 `chat_submissions` 문서에 관리 상태, 메모, 수정 시각, 연구자 ID를 기록하며 미검토/검토 결과별 조회와 다운로드를 지원합니다.
- 결과 파일: 설문 CSV, 개별 원본 ZIP, 개별 파싱 JSON/CSV, 선택 원본 ZIP, 선택 대화문 CSV ZIP을 제공합니다. 대량 파일 생성은 Queue에서 처리합니다.
- 운영 분석: 설문 heartbeat와 익명화된 일별 funnel 지표로 참여 현황, 활성 사용자, 유입 경로를 집계합니다.

설문 결과 CSV의 휴대폰 아이디는 Excel에서 앞자리 `0`을 보존하도록 텍스트 형식으로 출력합니다.

## 참여자 등록 경로

- 웹 신청: 공개된 `탐사 신청하기`에서 제출한 신청은 `pending` 상태로 회원 신청 관리에 표시됩니다. 연구자가 승인하면 참여자 계정이 생성됩니다.
- CSV 등록: 연구자가 이미 확보한 명단을 CSV로 등록하면 승인 대기 없이 참여자 계정이 즉시 생성됩니다. CSV 등록 대상은 회원 신청 목록에 표시되지 않습니다.

두 경로에서 같은 휴대폰 번호가 사용되면 중복 계정은 만들지 않습니다. 기존 CSV 등록 참여자와 같은 번호의 웹 신청은 `409 Conflict`로 승인되지 않고 신청 대기 상태를 유지합니다.

CloudType 사전구성 MongoDB의 wire version 7(MongoDB 4.0 계열)과 호환되도록 `pymongo>=3.12,<4.0`을 사용합니다. FastAPI의 비동기 route에서 동기 PyMongo 작업은 thread 기반 호환 계층으로 실행합니다.

## 역할과 권한

| 역할 | 권한 |
| --- | --- |
| `participant` | 본인 설문·대화문 제출 및 제출 상태 조회 |
| `researcher` | 신청 조회·승인, 참여자 CSV 등록, 참여·검토 현황 조회, 설문·대화문 결과 다운로드 |
| `admin` | `researcher`의 모든 권한과 연구자 계정·설문 정의 등록 |

CloudType Secret의 bootstrap 계정은 최초 backend 실행 시 `admin`으로 한 번만 생성됩니다. 이후 `admin`이 공동연구 실무자용 `researcher` 계정을 추가합니다.

## 설문과 Queue

설문 작성 중 임시 데이터는 프론트엔드가 약 30초 간격으로 브라우저 `localStorage`에 저장합니다. 최종 제출은 API가 Queue에 먼저 접수하고 `202 Accepted` 및 `submissionId`를 반환합니다. 같은 backend 컨테이너의 worker daemon이 MongoDB에 저장한 뒤 상태를 `completed`로 전환합니다. 프론트는 이 상태를 확인한 경우에만 임시 저장을 삭제하고 해당 회차의 입력·제출을 잠급니다.

설문지는 회차별로 변경될 수 있으므로 다음 값을 함께 사용합니다.

- `surveyRound`: 설문 회차. 예: `1`, `2`
- `surveyVersion`: 해당 회차 설문지 버전. 예: `2026-round-2-v2`

`admin`은 응답 수집 전에 문항 키·CSV 열 이름·순서를 설문 정의로 등록합니다. 실제 설문 초안처럼 `scales[].questions[]` 구조를 가진 JSON은 원본 spec을 MongoDB에 보존하고, 응답 검증과 CSV export에 사용할 평면 문항 목록을 자동 생성합니다. 문항이 많은 경우 CSV 또는 Excel 원본을 검토해 일괄 등록하는 방식을 사용합니다.

현재 1회차 수집은 대상·파트별 `v2-0916` 네 버전을 사용합니다. 이전 `v1-draft` 정의와 응답은 관리자 조회 및 export를 위해 병행 보존하며, 새 문항 구조는 기존 버전을 덮어쓰지 않고 새 `surveyVersion`으로 등록합니다.

연구자 페이지의 미참여자와 결과 다운로드 selector는 기본적으로 v2만 표시하고, 과거 v1 조회가 필요할 때 `과거 v1 포함` 옵션으로 접근합니다. 참여 현황은 버전이 아닌 회차별 고유 참여자 수로 집계합니다.

초기 개발 중 등록된 이전 설문 정의의 `csv_column` 형식과 현재 API의 `csvColumn` 형식을 모두 읽도록 호환 처리합니다.

## CloudType 배포

개발과 운영 모두 아래 3개 서비스를 사용합니다.

```text
frontend container   # ai_us GitHub 저장소
backend container    # 이 저장소: Uvicorn + Queue worker daemon
MongoDB container    # CloudType 사전구성 컨테이너
```

- 무료 CloudType 계정은 개발·통합 테스트용, 유료 계정은 실제 연구 운영용으로 분리합니다.
- 두 환경은 MongoDB 데이터, Secret, 공개 URL을 공유하지 않습니다.
- MongoDB URI는 코드가 `MONGODB_HOST`, `MONGODB_PORT`, `MONGODB_USERNAME`, `MONGODB_PASSWORD`로 조합합니다. host·port는 CloudType MongoDB 주소이고, 사용자명·비밀번호만 Secret으로 입력합니다.
- 루트 [Dockerfile](Dockerfile)은 CloudType 템플릿을 FastAPI용으로 적용한 배포 설정입니다. Python 3.11에서 의존성을 설치하고 backend 컨테이너 시작 명령으로 `./deploy/start.sh`를 실행합니다.
- 필수 Secret과 최초 배포 점검 절차는 [CloudType 배포 체크리스트](docs/cloudtype-deployment-checklist.md)를 따릅니다.
- `.env.example`은 CloudType Configure 패널에 입력할 변수 이름의 참고 목록입니다. 실제 `.env`와 Secret은 저장소에 커밋하지 않습니다.

로컬 개발에서는 `.env.example`을 복사해 `.env`를 만들고 MongoDB 및 `RESEARCHER_BOOTSTRAP_USERNAME`/`RESEARCHER_BOOTSTRAP_PASSWORD` 값을 설정합니다. `python-dotenv`가 앱과 관리 스크립트 시작 시 이를 자동으로 읽습니다. CloudType에서는 `.env` 대신 동일한 이름의 Secret을 사용합니다.

## 디렉터리

```text
app/
  api/        # FastAPI route handlers
  core/       # 환경설정, JWT, 비밀번호 해시
  db/         # MongoDB 연결과 인덱스 초기화
  schemas/    # 요청·응답 모델
  services/   # 신청, 인증, Queue, 설문, 대화문 파싱·검토, 결과 파일 처리
  worker.py   # Queue worker daemon
deploy/       # CloudType 시작 스크립트
docs/         # API 계약, 현재 구조, 배포, 프론트 AI 인수인계
tests/        # API와 서비스 테스트
```

## 문서 기준

- [API 계약](docs/api-contract.md): 요청·응답, 필터, 상태값의 기준
- [백엔드 구조와 남은 과제](docs/backend-implementation-plan.md): 현재 내부 구조와 미구현 범위
- [CloudType 배포 체크리스트](docs/cloudtype-deployment-checklist.md): 환경변수, 배포, 운영 검증
- [프론트엔드 AI 인수인계](docs/frontend-claude-handoff.md): `ai_us` 작업 원칙과 저장소 간 계약

과거 설계보다 현재 코드와 `api-contract.md`를 우선합니다. 문서와 구현이 다르면 테스트와 route/schema를 확인한 뒤 문서를 함께 갱신합니다.

## 로컬 검증

이 개발 서버에는 Docker와 MongoDB를 설치하지 않습니다. CloudType 연결 전에도 아래 테스트를 실행할 수 있습니다.

```bash
python3 -m pytest -q
```