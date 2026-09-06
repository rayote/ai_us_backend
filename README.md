# AI 마음 탐사대 백엔드

계명대학교 디지털상담 연구실의 청소년 생성형 AI 사용 종단 연구를 위한 Python 백엔드입니다. 별도 관리되는 `ai_us` 프론트엔드의 기존 HTML 및 inline script 구조를 유지하면서, 설문·대화문·참여자 계정·연구자 관리 데이터를 MongoDB에 저장합니다.

## 주요 기능

- 참여 신청: 동의 항목, 연락처, 학교급·학년을 검증하고 중복 휴대폰 번호 신청을 차단합니다.
- 참여자 인증: 휴대폰 번호 로그인, JWT 인증, 공통 초기 비밀번호 `1234`의 최초 변경 강제를 지원합니다.
- 연구자 권한: `admin`은 연구자 계정을 생성하고, `admin`과 `researcher`는 신청 조회·승인·CSV 등록·결과 다운로드를 수행합니다.
- 설문 응답: `surveyRound`와 `surveyVersion`으로 회차와 문항 버전을 구분하고, 최종 제출을 MongoDB 영속 Queue에 접수합니다.
- 대화문 제출: 링크 또는 복사 본문의 원본과 정규화 결과를 함께 보존합니다. 현재 본문 화자 표식 기본 parser와 링크 placeholder parser를 제공합니다.
- CSV: 설문 정의의 문항 순서를 기준으로 설문 응답을 CSV로 변환하며, 참여자와 대화문 자료도 연구자 권한으로 내려받습니다.

CloudType 사전구성 MongoDB의 wire version 7(MongoDB 4.0 계열)과 호환되도록 `pymongo>=3.12,<4.0`을 사용합니다. FastAPI의 비동기 route에서 동기 PyMongo 작업은 thread 기반 호환 계층으로 실행합니다.

## 역할과 권한

| 역할 | 권한 |
| --- | --- |
| `participant` | 본인 설문·대화문 제출 및 제출 상태 조회 |
| `researcher` | 신청 조회·승인, 참여자 CSV 등록, 설문·대화문 CSV 다운로드 |
| `admin` | `researcher`의 모든 권한과 연구자 계정·설문 정의 등록 |

CloudType Secret의 bootstrap 계정은 최초 backend 실행 시 `admin`으로 한 번만 생성됩니다. 이후 `admin`이 공동연구 실무자용 `researcher` 계정을 추가합니다.

## 설문과 Queue

설문 작성 중 임시 데이터는 프론트엔드가 약 30초 간격으로 브라우저 `localStorage`에 저장합니다. 최종 제출은 API가 Queue에 먼저 접수하고 `202 Accepted` 및 `submissionId`를 반환합니다. 같은 backend 컨테이너의 worker daemon이 MongoDB에 저장한 뒤 상태를 `completed`로 전환합니다. 프론트는 이 상태를 확인한 경우에만 임시 저장을 삭제합니다.

설문지는 회차별로 변경될 수 있으므로 다음 값을 함께 사용합니다.

- `surveyRound`: 설문 회차. 예: `1`, `2`
- `surveyVersion`: 해당 회차 설문지 버전. 예: `2026-round-2-v2`

`admin`은 응답 수집 전에 문항 키·CSV 열 이름·순서를 설문 정의로 등록합니다. 문항이 많은 경우 CSV 또는 Excel 원본을 검토해 일괄 등록하는 방식을 사용합니다.

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

Gmail SMTP는 비밀번호 재설정 이메일 기능을 활성화할 때만 설정합니다. 현재 Gmail Secret이 없어도 API와 Queue worker는 실행됩니다.

## 디렉터리

```text
app/
  api/        # FastAPI route handlers
  core/       # 환경설정, JWT, 비밀번호 해시
  db/         # MongoDB 연결과 인덱스 초기화
  schemas/    # 요청·응답 모델
  services/   # 신청, 인증, Queue, 설문, 대화문, CSV 처리
  worker.py   # Queue worker daemon
deploy/       # CloudType 시작 스크립트
docs/         # API 계약, 구현 계획, 프론트 Claude 전달 기록
tests/        # API와 서비스 테스트
```

## 문서

- [API 계약](docs/api-contract.md)
- [백엔드 구현 계획](docs/backend-implementation-plan.md)
- [CloudType 배포 체크리스트](docs/cloudtype-deployment-checklist.md)
- [프론트엔드 Claude 전달 기록](docs/frontend-claude-handoff.md)

## 로컬 검증

이 개발 서버에는 Docker와 MongoDB를 설치하지 않습니다. CloudType 연결 전에도 아래 테스트를 실행할 수 있습니다.

```bash
python3 -m pytest -q
```