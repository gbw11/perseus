# 복구 시나리오 국소 마감: Authority 진단·출력 검증·조회 예산

## 후보와 수락 범위

기준은 `eca59e1ba272496b7c661bcd5088b73624ed0763`, 작업 브랜치는
`feat/platform/recovery-scenario-closure`다. 후보는 기준 HEAD에 미커밋 diff·새 파일·소스별
해시를 더해 식별한다. 앞선 Zephyr 독립 실행·준비/커밋 검증은
[선행 보고서](zephyr-drive-foundation-20261029.md)에 보존한다.

제품 소스는 플랫폼 하위 Manager/Execution과 Gateway만 수정했다. FastAPI·dom0-manager·
Worker·Spring·프론트·Linux/Agent·장치 제품 소스는 수정하지 않았다. 실보드/서비스/VM/
broker/CPU/네트워크·전체 이미지·SD를 조작하지 않았다. SQLite·새 daemon·runtime 의존성·
MQTT type/필드·프로토콜을 추가하지 않았다. 게스트 소스를 바꾸지 않아 ARM 재빌드는 하지 않았다.

이 보고서는 아래 국소 보장의 구현·로컬 회귀·부분 패키징이다. 전체 복구 시나리오와
실제 차량·제품 종단 수락을 완료한 것은 아니다.

## 요구별 구현과 남은 경계

| 시나리오·요청 | 실제 변경 | 보장과 별도 수락 |
|---|---|---|
| D03-G01·G03·G04, H3·G2 | `authority_ops.py:inspect/revalidate`, Authority marker/원본 읽기 | 진단은 생성/쓰기/출력 0. 현재 정상 원본만 동일 lease·기대 checksum으로 내구 재확인, 원 ID/결과/pending/기한/generation 보존·재무장 요구. 손상 백업 복원은 아님 |
| G2·H3 | `AuthorityStore:initialize/load/verify/read_current` | 잔존 tmp/marker를 최초 빈 상태로 취급하지 않음. symlink·FIFO·공개 marker/원본 거절. 원본 검증 완료 전 RAM 값 채택 없음 |
| D03-G02 | 기존 원본/백업 비교와 증거 부족 차단 | checksum·같은 generation만으로 최신 backup 승인 안 함. marker가 최신 generation/hash를 보존하는 추가 설계는 별도 결정 전 미구현 |
| B3·BO3 | `output_guard.py`, `config.py`, `__main__.py:open_sink` | root 소유 실측 설정을 serial의 필수 입력으로 검증. 원 요청·양자화 출력·연속 제출 목표 변화율 검사. 실제 차량 값·운동 상태·ACK는 별도 |
| B3·H2 | `verified_runner.py:_submit`, `frame.py`, `machine.py:valid_command` | 위반/쓰기 실패 시 RAM의 DRIVE 철회·같은 회차 STOP을 failure journal fsync보다 먼저 시도. STOP 실패도 별도 기록. 극단 유한 값의 scaling overflow와 큰 JSON 정수의 변환 오류 차단. 시리얼 제출은 장치 적용이 아님 |
| F1·F2·F5·FO1 | `Execution.requests(deadline=...)`, Manager의 key 조회·짧은 stop admission·busy 완료 replay | 기존 관측 기한을 순회 중에도 검사. 불완전 조회의 부재/유일성 확정 0. 원 물리 deadline·key·intent revision은 유지 |
| B2·B4 | 시나리오 현재 처리 갱신 | 이미 구현한 독립 worker/cancel/join/앱 신원을 직렬 계산으로 설명하던 과거 표기 정정. 새 계산 알고리즘이나 자동 앱 복구를 구현한 것은 아님 |

`revalidate`는 운영자가 기존 Gateway 실행을 종료해 동일 lease를 확보한 상태의 로컬 도구다.
root의 비협조적인 외부 파일 수정까지 원자적으로 통제하지 않는다. 원본 부재·손상·stale
진단·lease 교체는 거절하며, backup 입력으로 대체하지 않는다. 실제 운영 상태에서 실행하지 않았다.

## 상태 판정

| 파일/관측 | 처리 |
|---|---|
| 검증된 데이터 저장소의 디렉터리 부재/빈 디렉터리 | 읽기 전용 FIRST_INSTALL 관측. 초기화/출력 허가 아님 |
| 정상 private marker·정상 원본 | VALID와 현재 hash/세대/선택·기록 개수 제공. 원본 내용 불변 |
| 원본 없음 + marker/tmp/backup 흔적 | BLOCKED. 정상 최초 빈 상태로 승격하지 않음 |
| marker/control 손상·권한 오류·FIFO·symlink·동시 변경 | BLOCKED, 초기화/출력/세대 발급 없음 |
| 정상 원본·같은 lease·같은 기대 hash | rearm_required=true 내구 재기록. 원 기한/ID/결과 유지 |
| write/fsync/replace 효과 미확정 | 성공 반환 안 함. 낮은 세대나 과거 RAM 사본으로 덮지 않음 |
| 재확인 직후 프로세스 종료 | 실제 다른 프로세스 로드에서 원 요청/세대·재무장 유지, lease 회수 확인 |

## 실행한 검증

원자료는 PC `C:/Users/SSAFY/Documents/Codex/2026-10-30/xenova-recovery-scenario-closure/`,
소유 WSL 시험 사본은 `/home/ssafy/xenova-recovery-scenario-closure-20261030/`에 둔다.
`run_checks.py`와 모드별 JSON이 실제 argv·rc·소요시간을 기록한다. 새 의존성을 설치하지 않고
기존 격리 pytest/FastAPI 환경과 기존 systemd 259 검사기를 재사용했다.

| 시험 | 결과·근거 |
|---|---|
| Gateway 전체 | `gateway-5.log`: 142 passed. 정상 제출/거절, 양자화·변화율·시계·부분 쓰기·저장보다 앞선 같은 회차 STOP, marker/FIFO/원본 오류·실제 프로세스 종료, 기존 warm/계획 전환 포함 |
| 하위 관리 4개 suite | `platform-2.log`: 102 passed. 순회 중 예산 소진, 부분 조회의 거짓 부재/유일성 거절, 미수락 stop의 intent 불변·재전달 뒤 원 90초 보존, 기존 fork/잠금/replay |
| dom0-manager 전체 | `manager-1.log`: 557 passed, 기존 Starlette 경고 1. S01/S02·Worker/Engine·Driver/HTTP 로컬 회귀. 제품 네트워크/DB/보드를 사용한 종단 수락 아님 |
| 복구·reconcile·lifecycle 5개 suite | `recovery-3.log`: 179 passed. 실제 자식 SIGKILL·원 journal/receipt replay·force 승인·systemd 259 loader·부분 초기화/종료/앱 Bootstrap. Xen/Store/장치는 명시적 대역 |

초기 실패도 보존했다. Gateway 첫 실행은 새 설정 시험에서 기존 필수 VM 쌍을 생략한
fixture 오류(139 pass/1 fail)였고 제품의 필수 인자를 완화하지 않았다. 하위 첫 실행은
기존 오류 주입 wrapper가 새 선택형 deadline 인자를 전달하지 않아 생긴 TypeError였으며
대역 signature만 수정한 뒤 102개를 통과했다.

확장 회귀의 초기 5개 실패는 `/mnt/c` 복사본의 정책 권한·자식 PYTHONPATH·systemd 259
검사기 지정 누락이었다. Git 모드로 만든 Linux 사본과 기존 검사기를 사용해 재실행했다.
후속 1개 SIGKILL 자식의 first-install 공용 모듈 import 경로도 시험 launcher에 추가했다.
제품 권한/force 승인 조건·원 결과 assertion·시간값은 낮추지 않았다. SIGKILL/파일 오류는
실제 SD 전원 상실·Xen·장치 수락이 아니다.

## 패키지·소스 연결

Gateway wheel은 기존 setuptools/wheel로 소스 사본에서 빌드하고 archive의 모든 `.py`를
원본과 대조한다. 하위 두 모듈은 기존 recipe의 설치 경로/0644 모드와 실제 바이트를
대조한다. `PACKAGES.json`과 소스 봉인이 경로·해시를 연결한다. 이것은 BitBake RPM·전체
이미지·설치본 검사가 아니며 Gateway의 정규 Yocto recipe/제품 unit 편입은 여전히 남아 있다.

## 남은 종단 수락

- D03-G02의 손상 원본 backup 복원에는 최신성 증거 확정이 필요하다. 현재 initialized는
  설치 흔적이며 최신 세대 증명이 아니다. 추가 신뢰 장치는 기존 요청대로 별도 합의한다.
- 계획 전환 actor queue는 프로세스 내부 연결부다. 별도 Worker/운영자와의 전송·결과 연결,
  입력 없는 MANUAL 선택과 이동 허가, 복구 전 실제 보호 확인의 종단 수락은 남아 있다.
- 차량 한계는 측정·설정 후 수락해야 한다. 실제 가속도/조향/제동·장치 ACK·watchdog/E-stop·
  남은 출력 예산을 만족한 연속 승계는 이번 통제 시험으로 대체하지 않는다.
- 큰 Debian 생성의 전체 자산 검증/복사·전역 실행 잠금은 바꾸지 않았다. key 순회 예산
  보완을 과거 8GiB 등록 비용이나 전체 응답 시간 해결로 해석하지 않는다. 개별 syscall/
  fsync·정렬 지연을 선점하는 hard realtime 한계도 제공하지 않는다.
- Worker의 A1 첫 무수신·D1/D2 보고자/앱 분류·E4 안정 구간/episode 예산·D4 공통 기반,
  G2 host 재시작 보존 정책과 D03 원 결과/Standby 연결은 담당 요청서에 누적했다.
- Spring의 원 key/pending/조회·재시작 회수·조건부 동일 이행, 실제 MANUAL 세션·Jetson
  지도/상태 전달·Linux/Agent·제품 화면 연결은 담당자 수락 전이다.

정식 사용법은 [Gateway](../../platform/services/gateway/README.md), 현재 시나리오는
[복구 시나리오](../platform/recovery-scenarios.md), 담당별 후속은
[요청 색인](../requests/README.md)을 따른다. 기존 ID·미완료 상태와 과거 실패를 보존한다.
