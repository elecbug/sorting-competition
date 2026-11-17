# SORT ARENA

KMU CSV 정렬 대회용 웹 서비스입니다. 교수자가 문제별 **원본 CSV + 정답 CSV**를 등록하고, 학생이 C 함수를 제출하면 격리 환경에서 실행하여 정확성·CPU 시간·Peak RSS를 비교합니다.

```c
#include "csv_sort.h"

void student_sort(rows *table) {
    /* 문제의 규칙대로 table->rows를 정렬 */
}
```

`string`, `row`, `rows` 구조는 [csv_sort.h](examples/csv_sort.h)에 정의되어 있습니다. CSV 파싱은 공통 C 실행 코드가 처리하므로 학생은 정렬 함수에 집중할 수 있습니다. 열 선택, 숫자/문자 비교, 복수 키, 동률 처리 등은 문제 설명과 교수자가 만든 정답 파일로 정합니다. 결과는 CSV 파일 표기 대신 파싱된 **헤더·셀 내용·행 순서**로 비교합니다.

## 시작하기

Python 3.11 이상, uv, Linux Docker Engine이 필요합니다.

```bash
uv sync --frozen
.venv/bin/python scripts/setup.py

# 선택: data/sample_csv의 원본을 보존하면서 시연용 문제 5개 생성
# 기본: 첫 번째 열 strcmp 오름차순, 동률이면 전체 열을 왼쪽부터 비교
.venv/bin/python scripts/import_samples.py

# 서버 서비스 설치, 채점 이미지 빌드, 부팅 시 자동 실행
sudo bash deploy/install.sh
```

기본 접속: `http://서버주소:8100`. 관리자 ID는 `admin`이며 비밀번호와 학생 가입 코드는 자동 생성된 `.env`에서 확인합니다. `.env`는 Git에 포함되지 않으며 파일 권한은 600입니다.

개발 실행은 다음과 같습니다. worker는 Docker 권한이 있는 계정에서 실행합니다. Docker 접근이 불가능하면 웹은 정상 제공되고 제출물은 대기열에 보관됩니다. 호스트에서 학생 코드를 대신 실행하는 경로는 없습니다.

```bash
.venv/bin/python scripts/serve.py
.venv/bin/python -m judge.worker
```

## 주요 기능

- 참여 코드 기반 학생 가입, 비밀번호 로그인, 관리자/학생 권한 구분
- 문제별 설명·원본/정답 CSV 쌍·원본 공개 여부·제출 접수 관리
- 64 KiB 이하 `.c` 파일 불러오기 또는 코드 입력, 본인 제출 상세 및 컴파일 진단
- 고정 CPU + 단일 worker + 제한된 Docker 컨테이너에서 컴파일/실행
- 1회 준비 실행 + 기본 10회 반복, 네이티브 감독자의 CPU 시간 및 Peak RSS 측정
- 정답 일치율 60점 + CPU 상대 성능 30점 + 메모리 상대 성능 10점
- 학생별 최신 채점 완료 제출 순위, 시간/메모리 개별 순위, 동순위, CSV 내보내기
- 데이터·환경 변경 시 순위 기준 분리, 관리자 재채점, 비공개 정답 및 학생 소스 접근 제한

## 구성

| 경로 | 역할 |
| --- | --- |
| `app/` | FastAPI, SQLite, 한국어 웹 UI, 로그인 및 관리 API |
| `judge/worker.py` | 단일 순차 대기열, Docker 격리 실행 |
| `judge/supervisor.py` | CSV 파싱, C 컴파일, 반복 실행, 비공개 정답 검증 |
| `judge/launch.c` | 별도 UID로 학생 실행, 외부 CPU/RSS 측정 |
| `judge/harness.c` | 제공한 구조로 CSV 구성, 학생 함수 호출, 결과 직렬화 |
| `examples/` | 공개 헤더, 제출 템플릿, 직접 작성한 기준 C 구현 |
| `data/sample_csv/` | 사용자가 제공한 원본 샘플 (수정하지 않음) |
| `data/generated_answers/` | 샘플 스크립트가 생성한 시연용 정답 (Git 제외) |
| `deploy/` | systemd 설치 스크립트, TLS proxy 예시, 운영 문서 |

## 검증

```bash
.venv/bin/python -m pytest -q
node --check app/static/app.js

# 저장소의 직접 작성한 기준 코드만 실제 대용량 샘플에 적용하는 점검
# 학생 제출물을 실행하지 않으며 순위표 결과를 생성하지 않음
.venv/bin/python scripts/verify_samples.py
```

테스트는 CSV의 따옴표·개행·한글·빈 셀, 원본 행 보존, 정답 순서, 접근 권한, 요청 제한, profile 분리, 점수와 동순위, 실제 기준 C 코드 컴파일·측정을 확인합니다. Docker 컨테이너 실행은 Docker 권한과 이미지 빌드 후 별도로 확인해야 합니다.

실행 시간은 함수 호출뿐 아니라 공통 데이터 로딩·출력 비용을 포함합니다. Peak RSS에도 입력과 공통 실행 코드가 포함됩니다. 자세한 측정 정의, CSV 제한, TLS 설정, 백업 및 운영 방법은 [deploy/README.md](deploy/README.md)를 참고하세요.
