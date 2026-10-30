# 복구 시나리오 국소 마감: Authority 진단·출력 검증·조회 예산

## 현재 상태 요약

| 구분 | 현재 판정 |
|---|---|
| 2번 최종 구현 | `a15bc3f`: Linux 사전 전체 payload 해시 0회·검증 결합 복사 1회·원 작업과 부분 실패 보존. 설명/기존 측정 기록은 `5f3dba6` |
| 로컬 근거 | 기존 최종 57개 집중·449개 관련 회귀와 세 모듈 부분 설치 바이트 대조. 이후 Debian·Ubuntu 명시 사례 4개를 추가해 집중 61개 통과, 관리·Zephyr 54개 재실행 통과 |
| 보드 상태 | 앞선 2회 처리 후보의 Debian 생성 477.203초만 측정. 최종 1회 후보는 미적용·성능 미측정 |
| 2번 공통 적용 | 기본 lock의 Debian·Ubuntu ID/버전·출처와 실제 카탈로그/준비 경로를 축소 payload로 검증. 실제 고정 대용량 산출물·Ubuntu 부팅 수락은 별도 |
| 실패 후 종결 | 5번 하위 후보에서 원 host/기한·명시 private 수습·동일 수습 재개·내구 예약 반환과 다음 신규 생성까지 로컬 검증. 운영 시간 정책과 상위 연결·설치본 수락은 별도 |
| 3번 로컬 마감 | 단일 Linux 준비 슬롯·원 실행 소유권·private 준비/게시 분리·요청별 출력. 최종 관련 회귀 481개 통과, 두 OS 모두 A 복사 해제 전 B 조회/실제 stop 완료 원자료 확인. 부분 설치 29개 Python 파일·독립 import 일치 |
| 4번 하위 로컬 마감 | 요청별 관측·공통 잠금 밖 조회·유한 재관측·관측 실행량/지연 표본 보호. 기존 541개 후 슬롯 부모 중단/정상 반환·파일 안전 검사 보완 후보로 22 suite 543개 통과. 두 중심 barrier·양 OS polling, 부분 설치 29개 바이트·6개 격리 import 재확인 |
| 남은 범위 | 운영 create 시간값 공급·보드 수락, 상위 제품 연결·실제 HTTP polling/정규 설치/보드 수락·API cgroup의 guest backend 문제는 미완료. 모든 부하/스케줄의 무기아나 지연 상한 수락은 아님 |

아래는 후보별 이력이다. 과거 2회/439개/보드 적용 기록을 최종 1회/449개 이후 후보의
실보드 수락으로 읽지 않는다. OS별 보완과 3번 수정 전 반례는 마지막 절에 연결한다.

## 후보와 수락 범위

기준은 `eca59e1ba272496b7c661bcd5088b73624ed0763`, 작업 브랜치는
`feat/platform/recovery-scenario-closure`다. 후보는 기준 HEAD에 미커밋 diff·새 파일·소스별
해시를 더해 식별한다. 앞선 Zephyr 독립 실행·준비/커밋 검증은
[선행 보고서](zephyr-drive-foundation-20261029.md)에 보존한다.
아래 국소 구현·검증은 기존 4개 커밋의 범위이며, 후속 검토 브랜치 병합과 제품 수락
준비 결과는 후속 절에서 별도로 구분한다. 아래 초기 국소 마감의 미수행 범위는 당시
범위이며, 승인 후 실보드 시험과 이후 Linux 단일 검증 복사 후보는 각 절의 상태를 따른다.

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

## 검토 브랜치 통합과 기본 관리 수락 준비

기존 4개 커밋 `357b6c7`·`d1c87f3`·`e59cd39`·`96231d0`을 보존하고, 검토 완료 ref
`feat/platform/zephyr-drive-foundation`의 `89e37d3e11c0e14eb996fbf894aac813aaac1044`를
일반 merge `33ab19d9358f95913c75cad3e31949d78544c5d7`로 통합했다. 스쿼시·rebase·force push는
하지 않았다. 이 ref에는 검토 4건과 최신 platform의 MQTT·display 선행 변경이 포함된다.
검토 작업방의 MR은 선행 변경 포함 범위 확인으로 제출 전이며, MR 제출 완료로 보고하지 않는다.
검토 자체의 C/native/ARM 원자료·실패 이력은
[검토 보고서](zephyr-drive-foundation-review-20261030.md)에 유지한다.

병합 후보는 별도 Linux 사본의 Git 파일 모드·소스 SHA-256으로 고정했다. 원자료는
PC `C:/Users/SSAFY/Documents/Codex/2026-10-30/xenova-vm-e2e-acceptance/`의
`MERGED_SOURCE.json`·`SUPPLEMENTAL_SOURCE.json`, `merged_checks.py`와 suite별 JSON/log에 둔다.

| 병합 후 검증 | 실제 결과·경계 |
|---|---|
| Gateway 전체 | `merged-gateway.log`: 151 passed. 기존 Authority/출력 한계와 추가 AUTO 재개 보호를 함께 확인 |
| 하위 관리·복구·MQTT·신원·회수 관련 15개 suite | `merged-platform.log`: 377 passed. Xen/Store·파일 오류 등 기존 대체 경계 유지 |
| dom0-manager 전체 | `merged-manager-r2.log`: 575 passed, root 권한 특성으로 1 skipped, 기존 Starlette 경고 1. skip한 쓰기 권한 실패 1건은 일반 사용자 `merged-permissions.log`에서 별도 통과 |
| 최초 Manager 실행 | 571 passed/4 failed/1 skipped. 시험 사본에서 기존 Spring DTO 원문을 빠뜨린 준비 오류. 제품/시험 assertion은 바꾸지 않고 Git 원문 210개를 추가한 뒤 같은 전체 suite를 재실행 |
| Python 부분 패키징 | Gateway wheel의 15개 .py와 하위 recipe 설치 파일 2개의 후보 바이트 일치. `PACKAGES.json`, wheel SHA-256 `0b823b1edcb41c11ce572927c8685fc3564cde25289e47f0abed66b268ce2ea1`. BitBake RPM·전체 이미지·설치 검사는 아님 |
| 게스트 후보 | `platform/apps`·`platform/guests/zephyr`는 검토 ref와 차이 0. 검토 ref의 C/native·ARM 3개 프로필 근거를 재사용하며 이번 병합에서 다시 ARM을 빌드하지 않음 |

EC2에서는 기존 PEM과 검증된 호스트키로 읽기 전용 확인을 수행했다. backend 이미지
`xenova-backend:13`의 실제 container health는 HTTP 200/UP이며, 배포 OpenAPI의 VM
목록/상세·생성·start/stop/delete·metrics/processes·templates/events 경로와 생성 schema를
확인했다. schema 조회와 health 통과는 VM 기능 통과가 아니다. 환경 전체나 비밀값은
출력하지 않았고 허용한 시간 설정의 명시적 override는 없었다. 소스 기본값이 곧 실제
전체 적용값이라고 확정하지 않는다.

첫 사전 확인에서 보드 `192.168.123.123:22`는 timeout이었으며, EC2에서도 설정된 보드 Tailscale
주소의 22·1883·8081이 timeout이었다. Tailscale peer는 offline이었다. 전원·이미지·Wi-Fi
중 무엇이 원인인지는 확인하지 못했다. 당시 로그인 전 관리 화면과 연결 불가 상태에서 VM
생성/조회/제어/삭제·실계측/Agent 전이·이벤트/화면 갱신·웹 콘솔은 **실행하지 못했다**.
이 상태를 제품 기능 실패나 종단 수락으로 분류하지 않는다.

이 시험 단계에서 새 VM·계정·DB 행·remote helper 파일을 만들거나 운영 설정·서비스를
바꾸지 않았다. SSH stdin으로 실행한 조회 도구의 결과만 PC에 보존했다. 이후 기본 관리
수락의 두 시작점·담당·assertion은
[HTTP·Spring·화면 요청](../requests/20261029-request-http-spring-ui.md#기본-vm-관리의-제품-종단-수락)에 연결한다.

## 설치본 FastAPI·EC2 직접 호출

이후 사용자 접속 확인에 따라 재연결했고, 같은 boot
`fc091b7f-05b3-4fcc-9744-05620cde9e7d`의 설치본을 조회했다. 연결이 다시 끊긴 구간과
복구된 구간 모두 보존했다. 사용자는 FastAPI 직접 시험을 먼저 선택했으며 Spring 공개
API 로그인과 프론트 입출력은 아직 수락하지 않았다. EC2에서 FastAPI를 직접 부른 것을
Spring→FastAPI 종단 수락으로 쓰지 않는다.

### 기능별 실제 결과

| 호출·확인 | 관측·원자료 | 판정 |
|---|---|---|
| 인증된 `/healthz` | `board_baseline-r2.stdout`: 200, 약 0.098초 | 서비스 health 통과. 제어·guest 준비 수락 아님 |
| 인증된 `GET /api/v1/vms` | 같은 자료: 200, 약 7.739초, 기존 실행 3개 | 목록 1회 통과. 반복 경합 없는 시간 상한 아님 |
| 새 Zephyr 생성, dom0 localhost | `board_zephyr_create-r2.stdout`: 70.995초, 503 `create waited 30s for the manager` | 공유 Manager 차례 대기에서 거절. 새 등록/실행 없음 |
| 동일 UUID·명세 생성, EC2→FastAPI | `ec2_zephyr_create.stdout`: 58.729초, 503 `get waited 30s for the manager` | 생성 전 조회에서 거절. 다른 UUID나 자동 재시도 없음 |
| 기존 Linux VM 상세 | `board_read_functions.stdout`: 30.091초, 503, 같은 Manager 차례 대기 | 실패를 재현. 기존 VM은 계속 실행 |
| `GET /api/v1/stats` | 같은 자료: 200, 약 0.368초, 실제 Xen CPU 누적 시간·메모리·네트워크/디스크 계수 | 별도 통계 경로 통과. Linux Agent CPU%·프로세스·차트 수락 아님. Zephyr 디스크는 null/NO_DEVICE 유지 |
| Debian·Ubuntu 생성·상태 제어·삭제 | 공통 진입 경합을 확인한 뒤 아직 제출하지 않음 | 미수행. Zephyr 성공이나 전체 템플릿 지원으로 확대하지 않음 |
| Agent 전이·제품 사건·화면 갱신·웹 콘솔 | 소유 VM 생성과 제품 로그인 미완료. 설치 console 환경 항목은 없고 config 기본은 false | 미수행. 기본 VM의 Agent/서비스를 정지해 대신 시험하지 않음 |

원 UUID는 `bd784cbd-a711-4e52-9913-c13b3e6374d7`, 표시 이름은
`e2e-20261030-direct-zephyr`다. `board_owned_observe*.stdout`에서 이 UUID의 등록·
원 작업 journal이 없고, Xen에는 기존 3개 guest만 있으며 CPU 정책 두 파일 해시와
기본 VM 배치가 최초 관측과 같음을 확인했다. 실패 요청의 새 key/UUID나 강제 수습은 하지 않았다.

### 원인 경계와 설치본 차이

`board_contention.stdout`에서 PID 1267이 platform.lock을 보유했고, 그 자식 Python
identity helper가 domid 1/2/3 순으로 바뀌었다. 관측한 helper는 R 상태·CPU 시간 증가였으며,
한 PID의 영구 syscall 대기를 확인한 것은 아니다. API 접근 기록에는 반복 list와 그중
일부 503이 있었다. 즉시 실패는 공유 `SerializedManager.call`의 대기이며, 반복 신원 조회
비용과 경합의 연관 근거다. 모든 대기 시간을 단일 helper에 귀속하거나 과거 장애까지 같은
원인으로 확정하지 않는다. 유효 busy 설정 30초를 전체 HTTP 예산으로 취급하지 않는다.

`RUNTIME_MATCH.json`의 설치 모듈 8개 중 현재 작업트리와 일치한 것은 Python identity 모듈
1개였다. 나머지는 Git의 이전 모듈 바이트와 일치했다. 개별 모듈 일치로 설치 전체 HEAD를
추정하지 않는다. 설치 OpenAPI에는 key stop 200/202와 control-operation 조회가 없었다.
FastAPI·Worker·Spring 제품 소스·배포 설정은 이 대조를 위해 수정하지 않았다.

### 기존 native helper의 ARM·실제 Xen 대조

[기존 인계 보고서](display-253-board-handoff-20261029.md)는 native helper를 비교한 뒤
보드 인계를 위해 Python wrapper로 복원했다고 기록한다. 현재 소스의 같은 C와 wrapper를
재사용했다. 기존 고정 Yocto compiler/sysroot로 helper 한 개만 ARM 부분 빌드하고 ELF와
loader를 확인했다. 소유 출력 경로의 권한 때문에 실패한 첫 준비 시도도 보존했으며, 기존
빌드·캐시 권한을 바꾸지 않고 별도 사용자 소유 경로로 출력했다.

`NATIVE_IDENTITY.json`의 ELF SHA-256은
`72c27e497df7f8738ce930dcd5c609e2732b46a3de7d6f867b556bc2f8cc8aab`다.
`board_identity_memfd.stdout`에서 익명 메모리 FD로만 실행하여 2회 × domid 1/2/3,
6쌍 모두 기존 신원 JSON과 같았다. 관측 시간은 Python 0.711~0.988초, native
0.0083~0.0119초였다. 순차 소수 표본이며 전체 관리 작업 개선·최악 지연 보장은 아니다.
새 보드 파일·의존성·VM·서비스를 만들지 않았고 FD는 닫았다.

`identity-runtime-candidate.tar.gz`에는 ELF와 정규 wrapper 두 대상 및 manifest만 담았다.
실제 계획 모드에서 기존 wrapper 0755/root:root·해시, native/백업 부재를 확인했다.
적용·원복 도구의 소유 WSL 임시 루트 6개 사례는 `IDENTITY_ROLLOUT_TOOL_TEST.json`에 둔다.
**공용 helper 설치는 별도 확인 전이며 이 결과만으로 기본 관리가 정상화됐다고 마감하지 않는다.**

누적 담당자 요구는 [기존 HTTP 요청](../requests/20261029-request-http-spring-ui.md#설치본에서-재현한-직렬화-경합과-배포-대조)에
연결한다. EC2의 파일·컨테이너·DB·env와 실보드의 서비스·CPU·네트워크는 변경하지 않았다.

## 생성 전 용량 검사와 단계 내 해시 재사용

아래는 앞선 설치본 관측 이후, 사용자가 요청한 기본 관리 개선 2번의 별도 수정·적용 결과다.
기존 관측의 미수행/승인 대기 표시는 당시 결과로 보존한다. 이 수정으로 큰 이미지 준비의
전역 잠금, create 전체 deadline, HTTP·Spring·화면 종단이 마감됐다고 해석하지 않는다.

### 변경과 검증 순서

`xenova_management.Manager._selection`에 내부 선택 인자 `full`을 추가했다. 신규 create는
metadata·manifest·자원·주소 검증 후 기존 용량 계산으로 사전 검사하고, 전체 payload 검증과
기존 최종 용량 검사를 이어서 수행한다. 사전 검사는 예약이 아니며, 전역 잠금과 operation
기록·부분 작업의 소유·재전달 경로는 변경하지 않았다. 손상 payload와 용량 부족이 동시에
존재하면 payload 해시 오류보다 용량 부족이 먼저 나올 수 있지만, 손상 metadata는 여전히
사전 검사 전에 거절한다. 용량 부족을 이유로 정상 게시하거나 검증을 생략하는 경로는 없다.

`xenova_catalog.describe(full=True)`는 `root.raw`를 한 번 해시해 manifest의 두 hash 필드와
각각 대조한다. 이전에는 같은 호출에서 두 번 읽었다. 모든 payload의 전체 검증과
`Manager._prepare` 및 `prepare_instance`의 독립된 준비·복사 전후 검증은 유지했다.

시험은 `platform/tests/test_xenova_template_preflight.py`에 추가했다. 작은 실제 파일과
실제 Manager/catalog/용량 계산을 사용하고 Xen·게스트 준비만 기존 fixture로 대체한다.

| 근거 | 실제 결과·assertion |
|---|---|
| `template-focused-baseline.log` | 수정 전 새 기준 5 failed/5 passed. payload hash 두 번 뒤 용량 검사, 용량 부족에서도 payload 읽기 확인. 새 사전 검사 뒤 재확인 조건은 수정 전 존재하지 않음 |
| `template-focused-candidate.log` | 10 passed. 용량 부족 시 payload hash 0·journal/복사/Xen create 0, 전체 검증의 payload별 hash 1, 두 root hash 필드 검증 유지 |
| 같은 집중 시험 | 검사 순서 capacity→root hash→capacity, 검증 중 여유 감소·원본 변경 거절, 손상 metadata 우선 거절, 완료 replay의 재복사·재검증 없음 |
| `template-regression-candidate.log` | 383 passed, 93.02초. 위 신규 10개 포함. 기존 management/review/boundary/budget/platform/first-install/Zephyr/lifecycle/recovery/reconciliation 관련 suite |
| `template-installed-candidate.log` | 보드 기존 Manager에 동일 함수 수정만 이식한 후보로 10 passed. 이 시험도 로컬 Linux 격리 실행임 |

명령·고정 입력·파일별 해시는 PC 증거 경로
`C:/Users/SSAFY/Documents/Codex/2026-10-30/xenova-vm-e2e-acceptance/`의
`TEMPLATE_BASELINE.json`, `TEMPLATE_CANDIDATE.json`과 각 시험의 `.json`에 보존했다.
기준 HEAD는 `5009cc4`이며 미커밋 제품 diff와 새 시험이 후보의 일부다. HEAD만으로 후보를
식별하지 않는다. 별도 도구·의존성·firmware·커널 수정은 없다.

### 부분 설치 묶음과 실보드 적용

두 모듈은 기존 `xenova-platform_1.0.bb`의 SRC_URI·0644 설치 목록에 이미 포함된다.
정규 소스용/기존 설치본 이식용 두 변형을 `xenova-template-preflight-modules-20261030.tar`에
각각 넣고 꺼낸 바이트를 대조했다. `TEMPLATE_STAGING.json`에 해시를 기록했다.
이는 소유 파일의 설치 묶음 검증이며 BitBake RPM 생성·전체 이미지·SD 검사는 수행하지 않았다.

보드 `192.168.30.96`의 boot ID `3acf2435-a597-460e-a2ae-5d39f2cb7678`에서
두 대상 모듈을 root 전용 `/root/xenova-template-preflight-20261030`에 백업한 뒤 적용했다.
기존 catalog 전체와 Manager 수정 대상 함수는 수정 전 소스와 일치했다. Manager의 다른
설치 버전 차이는 그대로 보존했으며, API·Worker 제품 소스/unit/env는 교체하지 않았다.

| 모듈 | 정규 수정 소스 SHA-256 | 실제 보드 적용 SHA-256 |
|---|---|---|
| `xenova_catalog.py` | `2acd0e8efc4a7b40e5531b6aff0c6d596e0c7405012acb09949cdd74c6c9f804` | 동일 |
| `xenova_management.py` | `13a03ed66f4dbd33d79107730365bf4c0cc8cecb69f991940c148cd7335cad55` | `78321ce280646727369ac961883b44caa2dc5e75638c522d07628e6522ca667c` |

실제 unit의 종료 hook/중지 전파가 없고 API cgroup에 API PID 한 개만 있음을 확인했다.
기본 VM backend·xenstored·xenconsoled는 다른 unit 소유였다. 기존 operation→admission
순서로 잠금을 얻어 진행 중인 물리 작업이 없는 구간에서 파일을 원자 교체하고,
메모리에 로드된 모듈 갱신을 위해 `xenova-dom0-manager.service`만 정상 재시작했다.
API PID 1254→27273, health 200을 확인했다. timeout/KillMode 변경이나 force는 없다.

`board_template_apply-r2.stdout`, `TEMPLATE_BOARD_FINAL.json`에서 두 파일의 실제 해시·
0644/root:root, boot ID, 기존 Debian/Zephyr A/B의 domid·메모리·vCPU 유지와 CPU 정책
두 파일 해시 동일을 확인했다. 인증 파일 바이트도 전후 동일했다. Worker·MQTT·Tailscale
PID는 유지됐고 SSH·네트워크 설정·host는 변경하지 않았다. 백업의 `manifest.json`과
`rollback.py`는 소유한 두 파일만 복원하는 경로이며 새 부팅/후속 파일 변경은 재검토한다.

새 VM 생성과 전체 실보드 시험은 재개하지 않았다. 실제 용량 실패 재시험이나 대용량
Linux 생성 시간 개선도 측정하지 않았다. 약 6분 47초는 이전 요청부터 용량 부족 반환까지의
시간이며, 이 변경 전후 해시 함수만의 정밀 비교 수치로 사용하지 않는다. 마지막 읽기 전용
파일·보호 상태 확인을 마친 뒤 보드 사용을 종료했다.

## Linux 준비 검증과 복사 I/O 결합

사용자의 후속 요청에 따라 기본 관리 개선 2번의 남은 Linux 검증·복사 경로를 보완했다.
앞 절의 보드 적용 이후 수행한 로컬 변경이며 보드에는 적용하지 않았다. 전역 잠금 분리,
create 전체 deadline, 상위 제품 연결과 실제 Linux/화면 종단 수락은 여전히 별도다.
작업 설계와 수정 전 입력은 PC의 `2026-10-30/xenova-template-io-design/`에 보존했다.

### 구현과 유지한 경계

- `xenova_catalog.describe`: root의 두 기대 해시와 boot 참조·hash/size 형식을 payload
  읽기 전에 대조한다. metadata로 확정할 수 있는 오류는 전체 디스크 읽기로 넘기지 않는다.
- `Manager._prepare`: Linux의 manifest/metadata 재검증은 유지하고 준비 단계 전체
  payload 검증을 실제 복사 스트림에 결합한다. 생성 선택 단계의 전체 검증과 Zephyr의
  기존 전체 bundle 검사는 유지한다. 공개 action/spec/envelope·원 operation은 변경하지 않는다.
- `prepare_instance.make_instance`: 제공된 기대 manifest hash를 현재 값으로 덮어쓰지
  않고 대조한다. 같은 metadata 바이트에서 JSON과 digest를 얻고, root·부팅 자산 전체의
  복사 바이트를 검증한 뒤 tune2fs·guest 설정을 수행한다. 신규 일반 파일은 0600/O_EXCL로
  만들고 reflink하지 않는다. 짧은 read/write를 처리하며 조기 EOF·진행 없는 write·
  ENOSPC/EIO/fsync·hash 실패는 게시 성공이 아니다.
- sparse hole은 논리적인 0으로 hash하고 출력의 크기·hole을 유지한다. 비지원 파일시스템은
  전체 스트림 검증으로 처리한다. 관련 표준 API는 [Python os 문서](https://docs.python.org/3/library/os.html#os.SEEK_DATA)를 따랐다.
- 복사 전후와 게시 직전 dev/inode·크기·mtime/ctime·mode·소유권 등으로 원본 변경을
  거절한다. atime은 정상 읽기로 갱신되므로 비교하지 않는다. 이 guard는 같은 준비 작업의
  연결 근거이며 영구적인 검증 cache나 root 외부 변경의 원자적 통제를 뜻하지 않는다.
- 실패한 stage/workspace와 원 journal을 보존한다. 복사 대상의 물리 매체 재읽기 검증이나
  전원 상실 수락은 아니다. 기존 `validate_record`, guest/SSH 검증·필수 저장·게시도 유지한다.

### 실제 경로의 처리 횟수와 회귀

동일한 작은 실제 파일과 실제 Manager→make_instance 경로에서 외부 Xen·mount·chroot만
대체했다. 별도 `sha256sum` 호출과 catalog 해시, 복사를 각각 계측했다.

| root 전체 입력 처리 | 앞 절 수정 뒤 기준선 | 이번 최종 후보 |
|---|---:|---:|
| catalog 전체 검증 | 2 | 1 |
| 준비 함수의 별도 원본 해시 | 2 | 0 |
| 외부 cp | 1 | 0 |
| 전체 검증 결합 복사 | 0 | 1 |
| 합계 | 5 | 2 |

`cost-baseline.json`과 `cost-candidate-final.json`의 결과다. 논리적인 전체 입력 처리
횟수이며 물리 SD 읽기량이나 실보드 지연 개선율이 아니다. 이 작은 fixture의 실행 시간도
성능 개선 근거로 사용하지 않는다. 생성 전 전체 검증이 여전히 남으므로 장시간 전역 잠금이
해결됐다는 뜻도 아니다.

`test_xenova_verified_copy.py`는 실제 파일의 dense/sparse/trailing-hole, 짧은 read/write,
비지원 fallback, 입력 symlink/hardlink/FIFO, 동일 inode 갱신·mtime 복원, 경로 교체,
hash/size 불일치, I/O/fsync 실패, 기존 출력 보존을 확인한다. 실제 준비 경로에서는
manifest 결합·수락 후 payload 변경 거절·게스트 설정 중 원본 변경·정상 생성·구형 manifest·
factory seed의 pending 신원과 전용 어댑터 사용을 확인한다. 복사 중 소유 자식을 SIGKILL한
뒤 새 Manager로 같은 요청을 호출해 원 operation·부분 디스크 inode/내용 보존과 재복사·
자동 초기화가 없음을 확인한다. 이는 SD 전원 상실이나 Xen 실기동 시험이 아니다.

집중 시험은 `focused-candidate-final.log`에 47 passed로 기록했다. 앞선 후보의 전체 관련
회귀는 `regression-candidate.log`에 436 passed이며, seed·구형 manifest·수락 후 변경 시험을
보완한 최종 후보는 `regression-candidate-final.log`에 **439 passed, 97.94초**로 기록했다.
skip·실패는 없었다. 명령·반환 코드는 같은 이름의 `.json`에 보존했다.

### 부분 설치 묶음과 미수행 범위

기존 recipe의 `prepare_instance.py`, `xenova_catalog.py`, `xenova_management.py` 세 파일을
정규 설치 경로·0644로 staging하고 후보/출력 바이트를 비교했다. `SUBSET_PACKAGE.json`과
`xenova-template-verified-copy-files-20261030.tar`에 보존했다. 새 의존성·recipe·버전은 없다.
BitBake RPM·전체 이미지·ARM 실행 검증을 수행한 것으로 확대하지 않는다.
세 파일은 같은 후보로 배포해야 하며 Manager만 별도 교체해 검증 결합 복사가 반영됐다고
수락하지 않는다. 실제 설치가 필요한 시점에는 후보·영향·원복을 다시 대조한다.

| 최종 실행 소스 | SHA-256 |
|---|---|
| `prepare_instance.py` | `6dbb9134018913df72ad0df6d9bc523aa22055f6622b67a4aa48497d82c04a63` |
| `xenova_catalog.py` | `2987e3cd3829f0eaf8eb3b1a34710963c4128a55f4458b75d041d6ef95cbb10f` |
| `xenova_management.py` | `d6d30df3350ef88e57e8cb0c003c6b39ff6f33c9c644306acb232c974f511ff5` |

설치 묶음 SHA-256은 `f857dc8f54b143070486be000e0f3da09ff08ec55c73efc0eaaa4af4b5aa4380`이다.
`CANDIDATE-FINAL_SOURCE.json`의 고정 입력과 작업트리의 관련 실행 소스·시험을 대조했다.

소유한 sparse fixture는 논리 크기 33,554,449바이트를 8,192바이트의 실제 data 읽기로
복사하고 논리 해시·크기와 8,192바이트 할당을 유지했다. dense fixture는 4,194,304바이트를
같은 양의 data 읽기로 복사해 해시가 일치했다. 두 경우 모두 별도 inode다. 이 수치는
해당 로컬 파일시스템의 fixture 결과이며 실제 템플릿/SD의 할당·처리량 보장은 아니다.

이번 추가 후보는 실보드·EC2·서비스·VM·네트워크·실제 broker에 적용하지 않았다.
보드에는 앞 절의 용량 사전 검사·단계 내 중복 해시 제거만 적용된 상태로 인계했다.
최종 소스는 미커밋 diff와 새 시험을 포함하므로 HEAD만으로 식별하지 않는다.

## 검증 결합 복사의 승인 후 실보드 측정

앞 절의 로컬 후보에 대한 후속 승인으로 세 모듈을 보드에 적용하고 신규 Debian 한 건을
기존 FastAPI로 생성했다. 이전의 보드 미적용 표시는 당시 단계이며, 이번에 승인·실행한
범위는 아래와 같다. EC2·Spring·프론트 제품 경로는 호출하거나 변경하지 않았다.

### 적용 전 발견과 기본 VM 복원

동일 boot ID의 API PID 34258 cgroup에 기본 Debian `debian-dev`의 qemu PID 37757
(`-xen-domid 9 -name debian-dev`)와 xl PID 37824가 있었다. KillMode=control-group이므로
API를 바로 재시작하면 backend도 영향을 받는다. 기존 VM 보존 승인으로 확대하지 않고,
사용자에게 기본 Debian 한 대의 정상 정지→적용/API 재시작→같은 UUID/자산/CPU로 복원을
추가 승인받았다. Zephyr A/B는 정지하지 않았다. console drop-in도 그대로 보존했다.

기본 Debian의 정상 stop은 원 operation `cb0385b0-a702-4ff3-84d7-ad291aaf1d15`로 성공했다.
소유 확인 도구가 빈 list에 `is not None`을 사용해 잘못된 종료 코드 3을 낸 기록도 보존했다.
도구만 bool 판정으로 고쳤고 stop을 반복하지 않았다. 적용 도구에서 Xen 부재·API cgroup의
backend 종료를 다시 확인한 뒤 진행했다. 제품 stop 실패나 force 사용으로 분류하지 않는다.

원본 세 파일·manifest·원복 도구는 root 전용
`/root/xenova-template-verified-copy-20261030`에 보관했다. 실제 설치 Manager는 다른 함수의
버전 차이를 유지하고 `_prepare`만 이식했다. 이식본은 세 모듈을 명시적으로 먼저 import한
경로/해시까지 기록해 로컬 집중 회귀 47 passed를 확인했다(`board-candidate-final.log`).
이전 이식 시험의 경로 우선순위 추정에 의존하지 않는다.

보드 적용 hash는 prepare_instance `6dbb9134018913df72ad0df6d9bc523aa22055f6622b67a4aa48497d82c04a63`,
catalog `2987e3cd3829f0eaf8eb3b1a34710963c4128a55f4458b75d041d6ef95cbb10f`,
Manager `8d601b4a6aa5a9c9743373ada8007d69dbd05bff6a4d5bd558fe6546ea4006df`다.
API PID 34258→48046, health 200·인증 파일 바이트·CPU 정책 동일을 확인했다.
기본 Debian 복원은 기존 FastAPI POST 201, 약 11.74초였다. instance.json 해시
`08c0d5563c6628baa851816684c291488cc8467bee1189d4b3e4d1cd1405a29c`,
디스크 inode 524292와 논리 크기 8GiB가 정지 전후 같았다. 등록 UUID·MAC·IP·자원은 유지했고
실행은 정상 정지/기동으로 교체됐다. 이후 관측 domid는 12였다.

### 실제 생성 요청 한 건

- UUID `53c7cf07-1529-49fe-a516-5e992b47bc43`, 표시 이름 `io-verified-copy-debian-20261030`.
- `debian-13-arm64` 1.0.2, vCPU 1, 512MiB. 원본 root 논리 크기 8GiB.
- 원 create operation `2a3c1c67-4498-4614-9c1c-691c80217e5f`, key `create:53c7cf07-1529-49fe-a516-5e992b47bc43`.
- POST 한 번, **HTTP 201 / 477.203초(7분 57초)**. 등록 journal SUCCEEDED/complete,
  execution `4555a1e6-5174-4a2b-8977-6817963c91a2`, domid 13을 확인했다.
- 할당 IP `192.168.77.17`의 TCP 22 연결도 확인했다. SSH 인증·OS 전체 준비·Agent·
  Spring/화면 종단을 수락한 결과는 아니다. 시험 요청의 긴 HTTP 대기는 제품 timeout 변경이 아니다.

실제 설치 `PlatformProvisioner.ensure_instance`는 create execute=True 한 번을 호출했다.
계획 호출 중복으로 추정하지 않는다. 적용 전 소스 경로는 전체 root 해시 4회+복사 1회였고,
새 후보는 선택 단계 전체 해시와 검증 결합 복사로 나뉜다. 약 2.31초 간격의 외부 관측에서
첫 단계와 복사 단계의 API 원본 FD를 확인했고, 이후 별도 전체 root 읽기는 관측되지 않았다.

| 요청 진입 이후 관측 경계 | 시간 |
|---|---:|
| 첫 preparing 기록 | 222.762초 |
| 첫 published 기록 | 456.473초 |
| 첫 등록 complete 기록 | 458.707초 |
| HTTP 201 반환 | 477.203초 |

원본 root FD는 사전 검증의 4.32~218.05초, 검증 결합 복사의 222.76~435.70초에 관측됐다.
약 3분 40초씩 두 구간에 시간이 남아 있다. 이것은 함수의 정확한 시작/끝 계측이 아니며,
관측 주기와 읽기 전용 관측 부하가 포함된다. 사용자 관측의 이전 6분/12분과 동일 조건의
A/B를 다시 실행한 결과가 아니므로 개선율·보장 상한·최적 후보 순위를 확정하지 않는다.
긴 전역 잠금·UI 대기·상위 timeout이 해결됐다는 의미도 아니다.

원자료 `board_io_measure.jsonl`은 PC의 `2026-10-30/xenova-template-io-design/`에 보존했다.
SHA-256은 `742f1e4adc8bb675c1e6eeb414d4fcced9e548b33c008899b96cfeb96ac761ab`이며,
`BOARD_IO_RESULT.json`에 분석·제약·최종 확인을 연결했다. 타 장치 단조 시각을 빼지 않았다.

### 승인한 시험 자산만 정리

위 신규 UUID 하나만 정상 stop→retire→동일 UUID 확인 purge했다. 모든 단계가 첫 호출에
성공했고 force는 없었다. lifecycle PURGED, 등록/디스크와 비어 있는 소유 workspace 제거를
확인했다. 실제 반환 여유는 1,273,544,704바이트(약 1.19GiB)였다. 원 create journal과
정상 수명주기 기록·측정 원자료·원복 백업은 보존했다. 다른 기존 시험 VM은 삭제하지 않았다.

마지막 확인에서 기본 3개 VM 실행·구성, CPU 정책 두 파일 hash, API health 200과 기본
Debian TCP 22를 확인하고 보드 사용을 종료했다. 최적화 구현은 한 세트이며 과거 버전은
원복 백업으로만 남겼다. backend의 API cgroup 귀속 문제는 이 세 파일 변경으로 고치지 않았다.
이 측정 후보에는 사전 전체 검증이 남아 있었다. 아래 후속 구현은 수락·예약·실패 기록
경계를 함께 조정해 검증 결합 복사로 이동한다. 이 실보드 측정 결과를 덮어쓰지 않는다.

## Linux 사전 전체 해시 제거와 원 작업 보존

사용자가 복사 중 전체 검증 유지와 사전 전체 해시 제거를 승인한 뒤 구현한 로컬 후속
후보다. HEAD `5009cc4c026d2e9e94c4374a9d1f482405fae08f`에 기존 미커밋 I/O 후보와 이번
diff·새 시험을 합친 상태다. 이전 439개 회귀와 477.203초의 실보드 결과는 앞선 두 단계
후보의 결과로 보존한다. **이번 단일 검증 복사 후보는 보드에 적용하지 않았다.**

원자료는 PC `C:/Users/SSAFY/Documents/Codex/2026-10-30/xenova-template-single-pass-20261030/`,
시험 소스는 `/home/ssafy/xenova-template-single-pass-20261030/candidate-final/`이다.
`BASELINE.json`·`baseline-*`·`preexisting.diff`에 앞선 미커밋 상태를 보존했고,
`CANDIDATE-FINAL_SOURCE.json`에 시험 입력 파일 해시를 봉인했다. 제품·시험 코드와 이후
정리한 문서의 최종 바이트는 `FINAL_CANDIDATE.json`과 `final.diff`로 구분한다.

### 변경 책임과 판정

`Manager._mutate`의 Linux 선택은 metadata·용량까지만 검사한다. 원 operation·명세·
manifest·UUID/MAC/IP·workspace를 먼저 저장하고 `_create`→`_prepare`→실제
`make_instance`의 검증 결합 복사로 진행한다. 게시 도구의 전체 검증과 Zephyr bundle
검증은 유지했다. 실제 source guard·복사 함수는 앞선 후보와 동일 바이트다.

`_create`는 수락 후 Linux 준비 오류를 기존 `RECOVERY_REQUIRED`로 분류하고 검증 이유·
I/O errno를 기존 error에 남긴다. 실패가 기록된 부분 workspace의 재전달은 `_mutate`에서
차단하여 원 error를 덮지 않는다. `_call`의 실패 기록 저장 오류도 숨기지 않고 응답에
내구 저장 미확정을 표시한다. schema·status·원 키·공개 action은 추가하지 않았다.

| 확인된 상태 | 현재 처리 |
|---|---|
| metadata·인자·명백한 용량 부족 | 비싼 복사 전 거절. 신규 operation/디스크 없음 |
| Linux 계획 성공 | metadata·용량·소유 검사의 결과. payload 무결성·생성 완료 아님 |
| 복사 hash 불일치·ENOSPC/EIO | 원 operation에 RECOVERY_REQUIRED/error 기록. 부분 파일 보존, 게시·guest 설정 진행 금지 |
| 실패 기록 저장도 ENOSPC/EIO | 응답에 저장 미확정. 기존 preparing 원장·부분 경로로 재시작 뒤 재복사 차단 |
| 기록된 실패 + 부분 workspace | 원 key 조회/계획은 읽기 전용. 실행 재전달도 최초 오류·부분 inode/내용 유지 |
| workspace 생성 전 실패 | 원 manifest를 다시 확인하고 같은 operation·예약으로만 재개 |
| 복사 도중 프로세스 중단 | 실제 남은 부분 경로로 차단. 새 UUID/디스크/원 작업 발급 없음 |
| 게시 후 결과 기록 전 중단 | 같은 operation·자산 소유권을 재확인해 후속 등록. 재복사 없음 |

### 검증과 대체 경계

실제 Manager/catalog/make_instance/파일 복사를 사용했다. 기존 fixture가 외부 Xen·
mount/chroot·게스트 설정·DHCP 경계를 대체한다. 파일 쓰기·동기화 오류는 명시적으로
주입하며 부분 실패 재확인은 별도 Python 프로세스에서 실제 원장을 읽는다.

| 명령·자료 | 결과와 assertion |
|---|---|
| `io_checks.py cost baseline` / `cost candidate-final` | 같은 작은 Linux 생성 fixture에서 root 논리 전체 처리 2회→1회. 최종 catalog root hash 0, 독립 digest/cp 0, 검증 복사 1. SD 물리 읽기 횟수·실보드 성능 측정 아님 |
| `io_checks.py focused candidate-final` | `focused-candidate-final.log`: **57 passed**, 7.53초. 용량·metadata·계획, 검증 복사·원본 변경·SIGKILL, 실제 생성·부분 실패·새 프로세스 재확인 |
| `io_checks.py regression candidate-final` | `regression-candidate-final.log`: **449 passed**, 101.13초. 관리·예산·기존 lifecycle/복구·first-install·Zephyr bundle·템플릿·SSH 관련 17개 suite. skip 없음 |
| `test_xenova_creation_failure.py` | 손상 root/Image, 원 실패 보존, 다른 명세 충돌, workspace 전 실패의 동일 예약 재개, 부분 쓰기 ENOSPC/EIO, 실패 원장 저장 ENOSPC/EIO, 실패 저장 직후 중단, 게시 후 재전달 |
| `package_probe.py` | 기존 recipe의 세 모듈 포함 확인·0644 설치·소스/설치/tar 바이트 동일. sparse/dense 사본 SHA·독립 inode 확인 |

계획이 내용을 읽지 않도록 바뀌었으므로 이전의 ‘손상 payload를 수락 전 거절’ assertion을
그대로 유지하지 않았다. metadata 손상은 계속 수락 전 거절하고, 실제 payload 손상과
수락 후 변경은 원 작업이 남는 실패로 옮겨 검증했다. 전체 검증 중 여유 공간 소진·
원본 변경을 가정하던 두 사전 검사 전용 시험은 제거하고 실제 복사 실패/중단·원 기록
재확인으로 대체했다. 실패 기록을 성공으로 바꾸거나 검증을 생략한 결과가 아니다.

### 부분 설치 바이트와 남은 수락

`SUBSET_PACKAGE.json`과 `xenova-template-single-pass-files-20261030.tar`를 보존했다.

- `prepare_instance.py`: `6dbb9134018913df72ad0df6d9bc523aa22055f6622b67a4aa48497d82c04a63`.
- `xenova_catalog.py`: `2987e3cd3829f0eaf8eb3b1a34710963c4128a55f4458b75d041d6ef95cbb10f`.
- `xenova_management.py`: `f49836aef185ab45ef509d717456ec86dfd5a70037f8c50080b6150d50a536c7`.
- 묶음 SHA-256: `33c8334e0d27fb2d6c616144f8b414bc11b4cade419392a2320252e8deb5475b`.

이는 recipe 소유 Python 파일의 부분 설치 스테이징이다. BitBake/RPM·전체 이미지·ARM
실행·보드 설치 수락은 아니다. 게스트 C를 바꾸지 않아 Zephyr ARM 재빌드는 하지 않았다.
원본 선택과 복사 사이의 전체 root 해시를 제거했으나 저장장치/CPU 간섭·전역 잠금·
create deadline·상위 HTTP 후속 책임을 마감한 것은 아니다. 대용량 보드 생성 시간과
전원 상실은 미검증이며, 위 477.203초에서 특정 시간을 뺀 예상치를 실측으로 쓰지 않는다.

현재 사용법은 [CLI 생성 계약](../platform/cli-manager-spec.md#creation-contract),
상위 연동 미완료 항목은 [HTTP·Spring·화면 요청서](../requests/20261029-request-http-spring-ui.md#linux-생성의-복사-검증-실패와-원-작업-회수)에 반영했다.
보드·실제 상태·다른 담당자 제품·진행 중 이미지 빌드·commit/push는 변경하지 않았다.

## Debian·Ubuntu 공통 적용 근거와 실패 수습의 후속 책임

`a15bc3f`/`5f3dba6` 이후의 보완이다. 제품 Python 세 모듈은 수정하지 않았다. 과거 최종
비용 fixture는 `test_xenova_management.ManagementTest`가 만든 Ubuntu 출처/1.0.0 자료를
사용했다. Debian의 실보드 결과는 이전 두 단계 후보이므로, 두 OS의 최종 경로를 직접
구분하는 네 시험을 `test_xenova_linux_template_paths.py`에 추가했다.

새 시험은 `platform/guests/default-templates.lock.json`의 공개 ID·source ID·1.0.2 버전과
`sources.lock.json`의 distribution·출처·kernel release를 읽는다. 실제 조립 함수
`assemble.copy_management_catalog`로 카탈로그를 게시한 뒤 실제 Manager/catalog/
`make_instance`/검증 복사를 수행한다. payload는 작은 ext4 표식 fixture이고 manifest
해시도 그 시험 바이트에서 계산한다. 운영 lock의 대용량 payload 해시·실제 OS 내용까지
검증한 것으로 확대하지 않는다. mount/chroot·guest 설정·Xen/DHCP는 기존 대역이다.

| 입력·시험 이름 | assertion |
|---|---|
| `debian-13-arm64` / `debian-13-20261015` 1.0.2, `test_debian_default_catalog_uses_one_verified_copy_without_preflight_hash` | 조립 시 게시 전체 검증은 유지. 이후 계획/실행의 catalog root hash 0·독립 digest/cp 0·검증 root 복사 1. 다섯 payload 각각 한 번·별도 inode·바이트 동일, 선택/등록 결과의 ID·버전·출처 일치 |
| `ubuntu-24.04-arm64` / `ubuntu-24.04-20260926` 1.0.2, `test_ubuntu_default_catalog_uses_one_verified_copy_without_preflight_hash` | 위와 같은 생성 경로·횟수·출처·독립 사본 검증 |
| `test_debian_failed_copy_preserves_origin_without_an_unpublished_asset_cleanup_api` / `test_ubuntu_failed_copy_preserves_origin_without_an_unpublished_asset_cleanup_api` | 각 OS의 root 손상은 공통 RECOVERY_REQUIRED. guest 설정·게시 없음, 원 key 조회·최초 오류·부분 inode/내용 보존, 재복사 0. 미게시 get/retire/purge는 VM_NOT_FOUND이며 종결 기능이 아님 |
| 기존 `ManagementTest.test_zephyr_real_independent_creation_has_no_ip_disk_and_fixed_profile`와 `test_xenova_zephyr.py` | 실제 별도 bundle 생성/검증 경로 유지. Linux 복사를 적용하지 않음 |

PC 원자료는 `C:/Users/SSAFY/Documents/Codex/2026-10-30/xenova-linux-common-closure-20261030/`에
둔다. `CANDIDATE_SOURCE.json`의 로컬 입력으로 `io_checks.py focused candidate`를 실행해
**61 passed / 8.07초**, `compatibility candidate`에서 **54 passed / 7.74초**를 확인했다.
앞선 57개와 관리/Zephyr 시험이 포함·중복되므로 이를 449개에 더한 새로운 전체 suite
통과 수로 보고하지 않는다. 기존 449개 근거는 같은 제품 바이트의 선행 결과로 재사용한다.
제품이 바뀌지 않아 ARM·부분 패키지·전체 이미지를 다시 빌드하지 않았다.

미게시 실패의 조회는 `xenova-vm operation --idempotency-key "create:VM_UUID" --json`이다.
부분 파일을 보존한 채 차단하는 기능과 운영자가 그 실패를 종결·정리하는 기능은 다르다.
현재 후자는 제공되지 않으며, [CLI 생성 계약](../platform/cli-manager-spec.md#creation-contract)과
[기존 상위 요청](../requests/20261029-request-http-spring-ui.md#linux-생성의-복사-검증-실패와-원-작업-회수)에
후속 5번의 하위 플랫폼 책임·상위 표시 책임·수락 조건을 연결했다. 원 요청·신원·자산·
실행자 잔존과 사용 여부를 확인하기 전 해제하지 않고, 종결 뒤 재개/새 요청의 조건까지
확정해야 한다. 원 파일 삭제·새 UUID 우회를 정상 운영 절차로 안내하지 않는다.

3번의 수정 전 로컬 반례는 `concurrency_baseline.py`와 `CONCURRENCY_BASELINE.json`이다.
실제 Manager를 별도 프로세스에서 preparing에 멈춰 두면 다른 VM의 list/get과 원 create
operation 조회가 OPERATION_IN_PROGRESS였다. key stop은 원 의도·operation을 수락했지만
실제 이행은 pending이었다. 준비를 풀어 준 뒤 생성은 같은 원 작업으로 성공했다.
소유 pipe로 대기한 표본 1개이며 실제 복사 시간·FastAPI/Spring·보드 성능 결과가 아니다.
이 반례는 **3번 수정 전 잠금 분리가 미완료였다는 근거**다. 감사 실행 성공을 제품의
진행성 통과로 처리하지 않는다. 아래 수정 후 barrier 수락과 구분하고 API cgroup/backend
문제는 별도 미완료로 유지한다.

## 장기 Linux 준비와 공통 잠금 분리

### 1. 기준선 대비 동작과 중심 수락

기준 HEAD는 `b3aad9e`(`b3aad9e` 이전의 2번 제품 변경 `a15bc3f` 포함), 브랜치는
`feat/platform/recovery-scenario-closure`다. 기준 커밋으로 되돌리지 않고 기존 2번 근거를
보존했다. 이번 후보는 이 HEAD와 diff·추가 파일의 조합이다. 원자료는 PC
`C:/Users/SSAFY/Documents/Codex/2026-10-30/xenova-stage3-20261030/`, 소유 시험 사본은
`/home/ssafy/xenova-stage3-20261030/candidate-validated/`에 둔다.

수정 전 `CONCURRENCY_BASELINE.json`에서는 preparing을 막은 동안 다른 VM list/get·
원 create operation 조회가 OPERATION_IN_PROGRESS였고 key stop은 수락 후 pending이었다.
수정 후 `test_xenova_preparation_concurrency.py`의 S3-T01은 실제 root 사본 첫 쓰기 안에서
A만 barrier로 멈춘다. A를 해제하기 전에 아래 순서를 assertion으로 확인한다.

1. 원 operation·UUID/MAC/IP·workspace가 기록되고 root 사본이 존재한다.
2. 다른 등록 VM B의 list/get과 A의 원 key operation 조회가 성공한다.
3. B key stop이 실제 하위 control→stop_many→`xl shutdown` 외부 경계에 도달한다.
   현재 domain 부재와 원 control phase=completed까지 확인한다.
4. 이때 A release=false·준비 thread alive이고 원장/부분 사본 inode·내용은 동일하다.
5. 그 뒤에만 A를 해제하고 같은 operation으로 생성 완료·SUCCEEDED를 확인한다.

| 입력·표본 | A 원 operation | A 해제 전 B 완료 | A 해제 후 |
|---|---|---|---|
| Debian 기본 선택, 로컬 1건 | `38e87fb2-7ff6-4123-b37e-2a1cd2df57be` | list/get/원 조회 성공·RUNNING, control `cf409ffd-3438-42d2-8348-a8c77e7a73ec` completed, shutdown 호출 1회 | 같은 원 operation 성공 |
| Ubuntu 기본 선택, 로컬 1건 | `9f630951-a29e-4d70-8004-37363d933eed` | list/get/원 조회 성공·RUNNING, control `bd10816c-62c9-4e36-89da-1ff79ea4c23b` completed, shutdown 호출 1회 | 같은 원 operation 성공 |

`assertions-regression-candidate-validated/S3-T01-*-before-release.json`과
`*-after-release.json`에 원 envelope·바리어 상태·물리 경계 호출을 기록했다.
`CORE_ACCEPTANCE.json`은 네 원자료의 해시와 순서 검증을 연결한다. 실제 Manager·파일·원장·
flock·검증 복사이며 Xen/mount/chroot·게스트 설정·DHCP의 외부 경계는 기존 대역이다.
같은 호스트의 순서 검증으로, 실제 SD 처리량·종료 지연·HTTP/보드 성능 수락이 아니다.

### 2. 소유·잠금·게시와 출력

| 실제 변경 | 책임·보존 |
|---|---|
| `xenova_management.py:_dispatch_locked/_mutate/_create/_complete_private` | 신규 슬롯 확보는 공통 잠금 밖. 수락 전 원 key·현재 자원/주소·gate 재검사. private 준비 뒤 현재 원장·등록부·원 manifest·결과의 UUID/MAC/IP/target/workspace를 확인하고 공통→admission 잠금 안에서 source guard·게시. 다른 VM의 최신 변경을 재사용 전 사본으로 덮지 않음 |
| `prepare_instance.py:make_instance/PreparedInstance.publish` | 복사·guest 설정·자원 정리까지 private 결과로 반환. helper의 조기 public 배치 제거. 독립 seed는 기존 경로를 유지하고 초기화된 live 저장소의 직접 instance helper 우회는 명시적으로 거절 |
| `xenova_preparation.py:LinuxSlot/owner_state` | Linux 준비 1개, 신규 경합은 확인된 미수락. 전용 원 실행 잠금의 원 operation/spec/manifest/VM/workspace와 host·프로세스 시작값·실제 보유 FD/inode를 대조. PID나 임의 잠금 busy만으로 활성 판정하지 않음 |
| `request_output/run`, `xenova_vm.py`·`prepare_rootfs.py` 출력 연결 | 프로세스 전역 FD/stdout/cwd/env 변경 제거. 기존 `_thread._local`과 같은 요청별 sink·명령 스트림 각 64KiB 제한. 파이프는 비우고 누락량을 남기며 데이터 stdout 잘림은 오류. 같은 프로세스 다중 thread의 요청별 fork와 예산/trace를 시험 |
| `Workspace` 자원 기록, `ensure_quiet/drain`·VM 종료 진입점 | mount/loop 할당·정리 미확정 기록과 자식에게 전달한 슬롯/원 실행 FD 유지. 부모 사망·슬롯 부재를 자원 정리로 취급하지 않음. quiesce 뒤 공통 잠금 밖에서 제한 drain, 불확실 자원의 저장소 해제 거절 |
| `xenova-platform_1.0.bb`, `assemble.platform_root_ok` | 새 모듈 SRC_URI·0644 설치·필수 설치본 바이트 대조에 포함. 코드만 PYTHONPATH에 추가한 후보로 패키징 완료를 주장하지 않음 |

구체적 소유/락 표와 사용 흐름은 [관리 구조](../platform/management-boundaries.md#linux-준비의-소유권과-게시),
종료는 [부팅·종료 계약](../platform/management-boot-boundaries.md#종료와-재시작)에 반영했다.
admission unit의 기존 상한 10초를 늘리지 않고 내부 drain 9초로 반환 여유를 남겼다.
platform shutdown-all/poweroff/reboot-board의 준비 drain과 VM 종료는 기존 90초를 공유한다.
fsync/커널 I/O를 선점하는 엄밀한 반환 상한이나 systemd 강제 종료 뒤 자원 보존 보장은 아니다.

### 3. 공개 의미와 소비자 영향

wire schema·원 ID/key·spec·예약·원 기한은 유지한다. 확인한 같은 Linux 준비의 관측에
`effective_status=RUNNING`을 제공하며, raw operation.status를 조회 때문에 다시 쓰지 않는다.
workspace 전 실패를 같은 요청으로 명시 재개할 때만 실행 전이를 RUNNING으로 기록하고
원 error·신원·예약을 보존한다. 부분 workspace 실패는 계속 재복사 차단 대상이다.

SUCCEEDED는 원 성공으로 유지한다. 부모가 사망한 잔존 자식/미완료 효과는
RECOVERY_REQUIRED, 소유권 관측 오류는 OBSERVATION_UNAVAILABLE이다. 예산 소진으로
검색을 끝내지 못하면 DEADLINE_EXCEEDED이며, 원장 읽기·JSON 형식 실패는 ARTIFACT_INVALID,
무결성 불일치는 RECOVERY_REQUIRED, 중복 key는 RECONCILE_REQUIRED로 구분한다.
어느 오류도 정상 부재나 유일한 원 작업으로 확정하지 않는다. 신규 슬롯 미수락에만 accepted=false·operation_id=null을
사용한다. 일반 503/timeout으로 이 값을 추정하지 않는다. asset operation과 control-operation은
별개 원장이며 조회가 실행/청소를 하지 않는다.

정식 필드는 [CLI·Manager 원장 계약](../platform/cli-manager-spec.md#operation-fields),
담당자 수정 위치·조건부 직렬화 개정·실제 예시는 [기존 HTTP 요청서의 3번 절](../requests/20261029-request-http-spring-ui.md#linux-준비-중-조회제어의-동시-연결)에
누적했다. 기존 상세 인계의 ‘get/list는 긴 직렬화 유지’는 새 단일 하위 후보 확인 시의
요청별 fork 지원으로 조건부 개정했다. 현재 FastAPI ensure_instance는 활성 create 응답을
VMView로 다루지 않도록 연결이 필요하다. 상위 제품 소스는 수정하지 않았다.

### 4. 검증·실패 이력·부분 설치

`io_checks.py regression candidate-validated`의 실제 argv와 rc는 JSON, 전체 출력은
`regression-candidate-validated.log`에 보존했다. **19개 관련 suite, 481 passed / 113.60초,
skip 없음**이다. 이 한 명령의 수이며 기존 449·61·54·별도 3번 시험 개수를 더한 숫자가 아니다.
기존 격리 Python/pytest·고정 systemd 검사기를 재사용했으며 새 도구를 설치하지 않았다.

| 수락 ID | 실제 assertion·근거 |
|---|---|
| S3-T01 | 양 OS의 위 중심 시험. A 실제 사본 쓰기를 계속 막은 상태에서 B 실제 stop·원 완료 확인 |
| S3-T02 | 같은 key/spec의 별도 thread/새 Python 프로세스 활성 재전달, 두 최초 요청이 수락 전 검사를 통과하는 경합의 재검사. 원 operation/예약/복사 1개 |
| S3-T03 | 다른 신규 Linux 슬롯 경합 accepted=false·신규 원장/예약/workspace 0. 완료 replay는 슬롯을 기다리지 않음. 슬롯 반환 후 명시 제출은 정상 성공 |
| S3-T04 | 다른 spec·동일 미게시 VM의 start/stop/retire는 거절. 원장·부분 사본 불변, 중복 물리 효과 0 |
| S3-T05 | A 준비 중 B autostart 구성 갱신과 새 Zephyr 등록을 실제 수행. A 채택 뒤 두 변경 보존, 원 IP 예약의 재배정 거절 |
| S3-T06 | 원 성공 기록 후 소유 FD 해제를 멈춰도 SUCCEEDED 우선. 완료 replay의 현재 관측 실패는 원 결과와 null/진단을 보존 |
| S3-T07 | owner 파일 읽기 실패와 조회 예산 소진에서 부재/활성 추정·원장 변경 0 |
| S3-T08 | 기존 recorded/복사 SIGKILL/published 중단 회귀와 새 private-ready·before-publish 중단, 잘못된 private target 거절. workspace 전 실패의 같은 원 요청 재개가 실제 RUNNING을 거쳐 성공 |
| S3-T09 | 소유 부모 SIGKILL 뒤 실제 자식이 FD 보유. 잔존 소유로 재실행·저장소 unmount 차단. 자식 종료 후 슬롯이 없어도 자원 기록 미확정이면 해제 계속 거절 |
| S3-T10 | 기존 실제 복사 ENOSPC/EIO·fsync·실패 원장 저장 ENOSPC/EIO 및 새 프로세스 재확인 suite 재실행. 원 오류·부분 파일·저장 미확정 보존 |
| S3-T11 | 복사 중 quiesce와 공통 잠금 밖 정상 drain. 늦은 게시 0. gate를 닫았다 다시 열어도 이전 준비 문맥 채택 거절 |
| S3-T12 | 같은 프로세스 thread의 stdout/FD/예산/trace 분리. 실제 자식 출력 폭주를 제한 메모리로 회수, 데이터 잘림·명령 실패를 정상값으로 반환하지 않음 |
| S3-T13 | 두 OS prehash 0/검증 복사 1, 원 manifest·source guard·sparse·seed·Zephyr bundle 회귀. 정규 recipe/조립기의 새 필수 모듈 포함·독립 import |
| S3-T14 | CLI의 활성 create 원 결과 replay와 legacy stop이 A 해제 전 완료. 초기화된 live 저장소의 원 실행 소유권 없는 직접 helper 우회는 안전 거절. 기존 mutation/복구/CPU guard 관련 회귀 유지 |

실패 이력도 보존했다. `stage3-candidate-3.log`의 최초 동시 수락 시험은 비차단 공통
잠금에서 한 thread가 먼저 거절되어 의도한 barrier에 도달하지 못한 시험 순서 문제였다.
sleep/timeout 확대 대신 첫 사전 검사 종료를 event로 확인한 뒤 두 번째 요청을 시작했다.
`regression-candidate-3.log`는 8 failed/445 passed: 새 필수 모듈이 빠진 설치 대역,
조기 public 배치/None을 반환하던 creator 대역, 전역 stdout 변경에 기대던 noisy 대역이었다.
설치 대역에 필수 모듈을 추가하고 creator는 실제 private make_instance를 실행하며
noisy 도구는 명시 sink/실제 캡처 명령을 사용하도록 고쳤다. JSON 단일 응답 assertion은 유지했다.

`resume-baseline-2-candidate-4.log`에서는 실제 재개 실행인데 RECOVERY_REQUIRED로 보이던
상태 전이 결함을 재현했다. 실제 재개 시 RUNNING 전이를 내구 기록해 수정했다. 첫
`resume-baseline-candidate-4.log`는 시험 데이터가 provenance까지 바꾼 잘못된 주입이었으므로
제품 결함 재현으로 확대하지 않는다. ContextVar 후보는 기능 회귀를 통과했으나 고정
Python manifest상 추가 패키지가 필요해 채택하지 않았다. 기존 trace와 같은 thread-local로
바꾸고 최종 481개를 재실행했다. 이 기록을 성공으로 덮어쓰지 않았다.

`SUBSET_PACKAGE.json`·`xenova-stage3-runtime-files-20261030.tar`는 recipe 소유 Python
라이브러리 29개를 0644로 실제 스테이징하고 source/staging/archive를 대조한 결과다.
변경한 다섯 모듈은 Python `-I` 독립 import에서 모두 설치 경로로 로드됐다. 원본 recipe의
RDEPENDS·layers.lock은 유지했다. 고정 OE commit `0f03c732a0a2d0506b41b1abe60b3f368b2300dc`의
Python manifest와 기존 전이 의존성을 `STDLIB_DEPENDENCIES.json`/설치 검사에 연결했다.

| 변경 runtime | SHA-256 |
|---|---|
| xenova_management.py | `606a9693078c462f3db248d5f0cff474e15cc1de4ca9587df12e5cbf6fd190cc` |
| xenova_vm.py | `b2773efa4d350489b76926ab53e99d839dcd7a78f8ec8267a2f3164e450378d7` |
| xenova_preparation.py | `73468b6cc7b6b7c80c43d1bb6aa15a3c69b55363405d5066a09613eddb051854` |
| prepare_instance.py | `a3f72845a365af8043ca5a9209a57b044b797e0591830b0be8bf62b987cb91f6` |
| prepare_rootfs.py | `2a671c0d4e59391786cc0a412717d59e87f8b8310c28457874e4f66e59ebc5f6` |

묶음 SHA-256은 `08e7104f225b71d76be7bc35aaa4e359ea385ecfd5d9a41de700880f35da5874`다.
`CANDIDATE-VALIDATED_SOURCE.json`은 실행한 소스, `FINAL_CANDIDATE.json`/`final.diff`는
최종 문서·추가 파일까지 구분한다. 위 스테이징은 BitBake/RPM·전체 이미지·ARM 실행·실제
설치본이 아니다. 게스트 코드는 바꾸지 않아 Zephyr ARM 재빌드는 수행하지 않았다.

### 5. 남은 범위

4번의 전체 polling/공정성 최적화, 5번의 create 전체 deadline 및 미게시 실패 자산의
정식 종결·정리·예약 해제는 그대로 미완료다. 소유 잠금/자원 기록을 삭제하거나 원 ID를
재발급하는 우회·새 자동 작업 시스템·force는 만들지 않았다. FastAPI/Spring/화면의
실제 연결·HTTP 동시성 수락, 정규 후보 설치·보드 저장장치 부하/시간 개선은 미수행이다.
API cgroup의 guest backend 귀속 위험도 별도 미완료다. 단일 설치 후보의 모든 관련
모듈·import 경로를 맞추기 전 임의 부분 배포로 제품 종단 수락을 닫지 않는다.
이번 작업에서 보드·운영 상태·다른 담당자 제품·진행 중 이미지 빌드·commit/push는 변경하지 않았다.

### 6. 3번 마감의 교차 경계 확인

후속 검토의 두 항목은 `dc3861c`와 이번 시험·문서 diff를 기준으로 좁게 확인했다.
3번 잠금 구조나 제품 코드를 다시 변경하지 않았다. 기존 481개 결과와 부분 설치 원자료는
그대로 보존하고, 다음 assertion을 기존 시험 파일에 추가했다.

| 확인 | 시험·실제 결과 |
|---|---|
| 같은 VM의 준비 중 복구 진입 | `test_S3_T04_debian_recovery_entries_do_not_reclaim_live_unpublished_preparation`, Ubuntu 대칭 시험. 실제 root 복사 첫 쓰기를 멈춘 동안 `recover_once` 계획/실행, `recovery-reconcile` 진단/실행과 `recovery-operation`을 호출. 미게시 VM은 아직 등록 실행/복구 attempt가 없으므로 VM_NOT_FOUND. 원 create를 중단 작업으로 수습하거나 대신 실행하지 않음 |
| 원 작업과 자산 불변·정상 진행 | 위 호출 중 원 operation·owner 파일 및 workspace 파일의 inode/내용·집합 불변, 새 recovery attempt/control/public 자산·추가 Xen 호출 0. A는 release=false·thread alive·root 쓰기 1회, 원 key 조회는 RUNNING. 그 뒤 barrier를 해제하면 같은 operation으로 정상 생성 완료 |
| 검색 실패 원인 구분 | `test_S3_T07_asset_scan_read_format_integrity_and_duplicate_errors_are_distinct`. 원장 읽기/부분 directory 열람·JSON 형식 실패는 ARTIFACT_INVALID, 무결성 불일치는 RECOVERY_REQUIRED, 중복 key는 RECONCILE_REQUIRED. 모두 data=null 오류이며 정상 부재나 유일한 원 작업으로 반환하지 않음. 주입을 해제하면 같은 원 준비는 RUNNING |
| 예산·소유권 관측 실패 | 기존 `test_S3_T07_owner_read_failure_and_expired_lookup_do_not_change_the_origin`을 재실행. 예산 소진은 DEADLINE_EXCEEDED, 소유권 읽기 실패는 OBSERVATION_UNAVAILABLE. 원 작업 변경 0 |

이 복구 교차 시험은 **신규 미게시 VM에 원 복구 attempt가 없는 경우**를 다룬다. 이를
등록 VM의 전체 Recovery/Worker 종단이나 기존 uncertain attempt의 새로운 수습 기능으로
확대하지 않는다. `Recovery.conditions`의 등록 수명·의도·미완료 자산 소유 확인과 기존
복구 회귀는 유지한다. 추가 교차 시험에서 제품 결함은 확인되지 않았다.

같은 PC 원자료 경로의 `io_checks.py stage3 candidate-closure`를 실행해 **31 passed /
15.39초, skip 없음**을 확인했다. 기존 28개에 추가 3개를 포함한 한 suite 결과이며 선행
481개에 합산하지 않는다. `CANDIDATE-CLOSURE_SOURCE.json`에 실행한 사본의 해시,
`stage3-candidate-closure.json/.log`에 실제 명령·반환·출력, `STAGE3_CLOSURE.json`과
`closure.diff`에 최종 추가 후보와 근거 대응을 보존한다. 두 OS의 복구 전 raw envelope와
조회 오류는 `assertions-stage3-candidate-closure/S3-T04-*-recovery-before-release.json` 및
`S3-T07-asset-lookup-errors.json`에 있다.

정식 [자산 작업 조회 오류 설명](../platform/cli-manager-spec.md#cmd-operation)도 위 실제
분기로 좁혔다. 기존 담당자 요청서의 key 조회 오류 구분은 유지하며 새 오류/응답 필드를
추가하지 않았다. recipe 소유 29개 소스는 기존 부분 설치 manifest와 해시가 같아 패키지·
ARM·전체 이미지를 다시 빌드하지 않았다. 문서 링크와 diff 공백 검사도 확인했다.

3번은 **하위 구현·로컬 검증 마감**이다. 4번의 반복 polling 비용·공정성, 5번의 전체
deadline·실패 자산 수습, 상위 연결·실제 설치본/보드·API cgroup 수락은 별도 미완료로
유지한다. 이 후속 확인에서 보드 적용·다른 담당자 제품 수정·commit/push는 하지 않았다.

## 반복 조회의 비용·일관성과 제어 진행성

### 기준선·후보와 범위

4번은 3번 후속 보완까지 커밋된 `72868ebe7d15a493279faf6b49a5b242170c19b4`,
`feat/platform/recovery-scenario-closure`의 깨끗한 작업트리에서 시작했다. 과거 HEAD로
되돌리지 않았다. 실제 실행 후보는 이 HEAD와 제품/시험 diff·새 시험 파일의 조합이다.
PC 원자료는 `C:/Users/SSAFY/Documents/Codex/2026-10-30/xenova-stage4-20261030/`,
소유 실행 사본은 `/home/ssafy/xenova-stage4-20261030/candidate-proof/`다.
`BASELINE_SOURCE.json`·`CANDIDATE-PROOF_SOURCE.json`·최종 `FINAL_CANDIDATE.json`과
`final.diff`로 입력·실행 소스·최종 문서를 구분한다.

직접 수정한 제품은 하위 `xenova_management.py`, `xenova_execution.py`, `xenova_vm.py`,
`xenova_runtime.py`, `xenova_preparation.py`다. FastAPI/Worker/Agent/Spring/프론트 제품,
두 기준 설계서, 보드·운영 서비스/VM/상태·네트워크/CPU·진행 중 이미지 빌드는 변경하지 않았다.
새 TTL cache·daemon·DB·공용 영구 작업 큐나 새 공개 action/필드는 추가하지 않았다.

### 4-A·4-B: 같은 조건의 비용과 유지한 검증

두 등록 Linux VM·두 원 자산 기록·두 시작 제어 기록, 같은 외부 FakeXen runner와
list/get/asset operation/control-operation 호출을 사용했다. 전체 UUID/name/state 목록의
전후 대조와 대상 incarnation 두 확인은 서로 다른 검증 경계여서 유지했다. 표시 조회에
불필요한 상세 디스크 설정 목록·짧은 목록을 제거하고, 검증된 등록부를 UUID로 나눠
builder가 VM마다 전체 dict를 재검색하지 않게 했다. get은 다른 VM의 identity를 호출하지 않는다.
asset 원장 조회는 전 VM 등록부를 읽지 않으며 control 원장은 별도 원본/의미를 유지한다.

`paired_cost.py candidate-proof`는 자체 회귀/스테이징 종료 후 기준선/후보를 번갈아 3쌍
순차 실행했다. `PAIRED_COST-FINAL.json`, `metrics-finalpair*-*-facts.json`에 입력·응답·
명령 계수·텍스트 읽기 바이트/JSON 파싱·trace·짧은 잠금 대기/보유·FD를 기록했다.
시계는 이 비교에서 두 후보 모두 같은 호스트의 실제 단조 시계다. 다른 이미지 빌드·OS cache는
제어하지 않았고 하드웨어 Xen/SD 성능 시험이 아니다.

| action | 외부 명령: 기준선 → 후보 | 텍스트 파일 읽기 / JSON parse | 읽은 바이트: 기준선 → 후보 | 응답 중앙값 ms, n=3 |
|---|---|---|---|---|
| list, 대상 2개 | verbose 목록 2·상세 1·짧은 목록 1·identity 4 → verbose 2·identity 4 | 12/17 → 28/32 | 10,202 → 31,762 | 31.122 → 33.805 |
| get, 대상 1개 | verbose 2·상세 1·짧은 목록 1·identity 2 → verbose 2·identity 2 | 10/13 → 14/16 | 10,625 → 15,841 | 19.636 → 17.278 |
| asset operation | 양쪽 Xen/helper 0 | 4/4 → 4/4 | 10,678 → 17,560 | 2.162 → 1.579 |
| control-operation | 양쪽 Xen/helper 0 | 2/2 → 4/4 | 8,509 → 17,018 | 1.590 → 1.730 |

파일 바이트는 이 임시 fixture의 텍스트 입력이며 이미지 payload·pipe·/proc 읽기를 합친
물리 I/O량이 아니다. 잠금 해제 뒤 현재 구성/실행과 검색 완전성을 다시 확인하므로 메타데이터
읽기·파싱은 증가했다. 작은 fixture의 list/control 원장 응답시간도 줄었다고 주장하지 않는다.
전체 응답시간 개선률·p99·최악 지연 보장은 제시하지 않는다. 확정한 효과는 불필요한 외부
목록/반복 builder 검색 제거와, 아래 보호를 유지하면서 공통 대기를 끊은 것이다.

| 보호 구간 | 기준선 | 후보·writer와의 관계 |
|---|---|---|
| `platform.lock` | list/get의 외부 관측·응답 및 asset 원장 검색 동안 유지 | 네 일반 조회는 획득하지 않음. cost 세 표본 모두 hold=0. 실제 변경·예약/게시와 제출 guard는 기존 실행 잠금 유지 |
| admission | control 원장의 전체 검색 동안 유지 | 원장 검색 밖으로 분리. 원자 기록을 완전 검색·대조하며 첫 일치에서 유일성을 확정하지 않음. 실제 stop 수락/제출의 짧은 writer 잠금은 유지 |
| observation-VM | 기존 파생 snapshot 저장 | 해당 VM의 현재 구성/수명·실행 근거를 대조하고 필요한 snapshot만 게시. 전체 등록부/Xen/helper 대기는 이 잠금 밖 |
| 요청 객체 | 가변 예산/진단을 공유하면 안전한 동시 사용 계약 아님 | 객체별 무대기 guard. 실제 병행 호출은 fork. 기존 lock-free snapshot은 내부 fork로 격리해 보존 |
| `observation-slot-0..3` | 없음 | list/get의 외부 관측 네 개, 공통/admission 밖 무대기. helper FD 상속·실제 종료 확인. 원장 조회/실제 제어·Linux 준비와 다른 보호이며 전체 API 동시수/QPS 보장은 아님 |

원자 JSON과 private/public 게시 규칙, 단조 수명/실행 신원을 사용해 현재 대상과 다시
결합한다. 모든 파일/Xen을 같은 시각의 단일 트랜잭션으로 관측했다는 보장은 아니다.
새 reader 잠금만 붙이고 writer를 보호했다고 주장하지 않으며 외부 root/xl 변경을
원자적으로 차단하지 않는다. 자세한 읽기/쓰기 대응은 [관리 구조](../platform/management-boundaries.md#일반-조회와-변경-작업의-진행)를 따른다.

### 4-C·4-D: 두 중심 수락과 오류·게시 순서

수정 전 `slow-baseline-facts.json`은 지정된 identity 조회가 계속 막힌 동안 다른 VM의
asset operation이 OPERATION_IN_PROGRESS, stop이 내구 수락 후 pending이며 shutdown
경계 호출은 0이었다. `poll-before-baseline.log`의 양 OS·1/2 폴러 네 건은 polling을
유지한 상태의 stop 완료 assertion에서 실패했다. 4 폴러 두 건은 그 전 단계에서 정상
operation 조회가 한 번도 완료되지 않아 실패했고 stop까지 도달하지 않았다. 같은
barrier/호출 mix에서 확인한 진행성 반례이며
과거 보드 helper 지연이나 SD 시간을 현재 소스의 원인으로 대입하지 않았다.

후보의 `S4-T02-get/list.json`은 느린 조회 release=false·thread alive인 동안 다른 VM의
asset 원 조회 성공, key stop의 실제 하위 shutdown 명령 경계와 원 control phase=completed를
확인한다. list에 포함된 B가 그 사이 종료되면 오래된 목록은 명시 관측 오류로 끝날 수 있다.
관측 해제 뒤 정상 list도 성공한다. get의 무관한 대상 조회는 그대로 성공한다.

S4-T03은 A의 실제 검증 root 사본 첫 쓰기를 막아 둔 상태에서 폴러를 시작한다. 각 폴러가
list/get/A operation을 실제 호출하고, B 정지 후에도 polling 완료 수가 더 증가한 것을
event/condition으로 확인한 뒤에만 폴러와 A를 해제한다. 다음은 원 완료 확인 시점의
완료된 정상 조회 수다. 전체 실행의 총 호출 수나 운영 QPS가 아니다.

| A 입력 | 폴러 | 정상 list/get/operation | 오류 | B 정지·원 완료 / A 상태 |
|---|---|---|---|---|
| Debian | 1 | 8/8/8 | 0 | completed, polling alive·A blocked |
| Debian | 2 | 11/11/11 | 0 | 동일 |
| Debian | 4 | 21/18/18 | 0 | 동일 |
| Ubuntu | 1 | 10/10/10 | 0 | 동일 |
| Ubuntu | 2 | 13/11/13 | TARGET_CHANGED 2 | 동일; B 전환 중 명시 오류와 정상 읽기 동시 존재 |
| Ubuntu | 4 | 17/16/15 | 0 | 동일 |

각 조합에서 shutdown 경계 호출은 하나이며 A root 쓰기는 1회다. 그 뒤 A도 같은 원
operation으로 성공함을 실제 create fixture의 종료 assertion으로 확인했다. 원자료는
`assertions-regression-candidate-proof/S4-T02-*.json`, `S4-T03-*.json`과 같은 소스의
시험 assertion이다. 10ms는 로컬 폴러의 회차 간 시험 간격이며 제품 설정이 아니다.
Xen·mount/chroot·guest 설정·DHCP는 외부 대역이고 Manager/원장/복사/잠금/runner/
thread/프로세스/FD는 실제다. 이 결과는 HTTP·화면·실제 Xen 종료 수락이 아니다.

추가로 확인한 경계와 고친 실패 이력은 다음과 같다. 모든 실패 로그를 보존했다.

| 근거 | 수정·수락 |
|---|---|
| `fault-before-candidate-3.log` | selector I/O 실패 뒤 소유 read 자식이 남는 반례. timeout/예외 때 해당 직접 자식을 제한 회수하고 원 오류는 유지. guest backend나 다른 PID를 정리하지 않음. FD/자식 누수와 정상 조회 회복 시험 |
| `deadline-before-candidate-closed.log` | 실제 helper가 원 읽기 예산을 소진했는데 일반 관측 오류로 반환. 읽기 문맥의 만료는 DEADLINE_EXCEEDED로 보존. 빈 원장/VM 검색의 만료도 정상 부재로 바꾸지 않음 |
| `timestamp-before-candidate-final-check.log` | 늦은 성공이 게시 시각을 새 표본 시각으로 쓰고, 늦은 실패가 새 성공 cache를 unknown으로 덮는 두 반례. 자기 원 관측 시각 유지·더 새 성공/오류의 파생 기록 보호 |
| `per-vm-before-candidate-audit-final.log`, `absence-clock-before-candidate-final-verified.log` | 뒤 VM의 대기가 앞 VM/부재의 시각까지 밀어 올리는 반례. 존재는 대상의 마지막 incarnation 검증 시각, 부재는 두 번째 완전 목록 시각을 보존 |
| `focused-candidate-1.log` | 변경 중인 목록의 명시 오류를 허용하지 않은 기대값과 FakeXen의 중간 ID 제거 뒤 중복 할당을 구분. 실제 source stop/restart를 유지하고 외부 대역의 빈 domid 재사용만 수정. 정상 읽기 성공은 별도로 강제 |
| `regression-candidate-3.log`, `regression-candidate-validated.log` | 목록 표시 순서·공유 요청 객체 보호·기존 동시 snapshot 호환성 회귀를 수정. 옛 상세 목록/긴 잠금에 묶인 비용 assertion은 현재의 두 관측/집합 재확인에 대응시키되 원 예산/제어 guard assertion은 유지 |

### 4-E: 최종 검증·계약·부분 설치

`run_checks.py regression candidate-proof`는 **22개 관련 suite, 541 passed / 224.37초,
skip 없음**이다. 실제 argv/rc/출력은 `regression-candidate-proof.json/.log`다. 같은 후보의
집중 시험 29개는 그 안에 포함되므로 기존 481/31개나 집중 결과와 합산하지 않는다.

| 수락 ID | 연결한 실제 시험·경계 |
|---|---|
| S4-T01 | 2-VM 비용/명령/JSON 계수·builder의 단건 입력. 완전 목록 2·identity 2/대상은 유지. 대상별·부재의 고정 시각과 원 게시 시각 보존 |
| S4-T02/03 | 위 두 중심 수락·양 OS·폴러 1/2/4, 정상 읽기와 실제 stop 경계/원 완료·A 원 작업 성공 |
| S4-T04/05 | domid 재사용을 포함한 새 실행, 대상 구성 변경의 최대 2회 관측, 계속 변경 시 TARGET_CHANGED·회복. 다른 VM autostart 변화는 get을 다시 시작하지 않음. 새 등록을 목록에서 조용히 누락하지 않음 |
| S4-T06/07 | registry/legacy 열람 실패·뒤늦은 중복 key와 기존 S3-T06/T07/T09 재실행. 실패를 empty/absence/unique로 바꾸지 않고 성공/활성/잔존 자식/미확인을 구별 |
| S4-T08/09 | 빈 검색·registry/원장/외부 helper의 잔여 예산, 실제 자식 EOF·selector 오류 후 회수. 화면 관측 뒤 실제 stop의 상세 자산/신원 제출 guard가 새로 수행됨 |
| S4-T10/11 | 설정 IP·unknown 앱 준비 보존, fork에 memo 상속 0·예산/trace/출력 분리. 같은 객체의 동시 호출 거절·동시 snapshot 호환, 늦은 성공/실패/시각의 최신 파생 기록 침범 0 |
| S4-T12/13 | 네 read 슬롯의 초과 거절 및 다른 프로세스 참여, 슬롯 점유 중 원 조회/stop 진행·해소 후 읽기 성공. 잘린/잘못된 identity의 정상값 fallback 0·기존 native/Python 64-bit/오류/timeout ABI 회귀 |
| S4-T14/15 | 원 완료 replay/현재 관측 실패 분리와 모든 기존 2·3번·복구/종료/trace 회귀. 두 OS prehash 0·검증 root 복사 1, 원 manifest·부분 파일/오류·private 게시·소유권·quiesce 보호 유지 |
| S4-T16 | 아래 정규 recipe 소유 source/staging/archive와 실제 설치 경로 import. 상위 호출/권한/오류·버전 인수 요청과 연결 |

부분 설치는 `SUBSET_PACKAGE-CANDIDATE-PROOF.json`과
`xenova-stage4-runtime-files-20261030-candidate-proof.tar`다. recipe의 기존 Python 29개를
0644로 스테이징하고 source/staging/archive를 바이트 대조했다. 변경 5개 모듈과 준비
모듈의 총 6개가 Python `-I`에서 설치 경로로 import됐다. recipe/RDEPENDS·고정 표준
모듈 의존성은 유지하며 새 잠금은 기존 core builtin `_thread.allocate_lock`이다.
native wrapper·Python/native 원본은 바뀌지 않았다. wrapper는 실행 가능한 기존 native를
exec하고 그 실패를 약한 Python 성공으로 fallback하지 않는다. native 부재 때의 기존
엄격한 Python 경로도 유지한다. 무관한 Zephyr/ARM 재빌드는 하지 않았다.

| runtime source | SHA-256 |
|---|---|
| xenova_management.py | `cfdc49c69ee6885dffed43c916b7a8b645e263ecb48a999ba8bea707ab4e6067` |
| xenova_execution.py | `6b978b786c621f965b501d65482bd53c8b47d05101b1b605b4805d17bccbe245` |
| xenova_vm.py | `40708ac8fedceb86642a848fc84c32a8a85f3f612f8910c2d0207e9422b7b810` |
| xenova_runtime.py | `43dee327af2ad13ad7d10b2a59449c67e7a2af33290f99f9523cc3995807d99d` |
| xenova_preparation.py | `1f039585daca5b4c22a9133b241b0383dc9b8c9dad8b504e7168d80020be8d3f` |

묶음 SHA-256은 `9113c1553798405ddccd4a9b58002db70d997b45ec9d128009f1935d50a8c6f4`다.
이는 Python 라이브러리 부분 설치/묶음과 host ABI 검증이며 BitBake/RPM·전체 이미지·실제
ARM/Xen/SD 설치본 수락이 아니다. 정규 이미지/설치 때 같은 후보의 전체 관련 모듈·helper
실제 경로·서비스 연결을 대조해야 한다. 일부 모듈 혼재 상태의 동시 호출은 지원으로 간주하지 않는다.

정식 의미는 [일반 조회 계약](../platform/cli-manager-spec.md#read-concurrency), 담당자
수정 위치·fork/예산·threadpool/선행 get·실제 polling/응답 역전·권한/허용 details는
[기존 HTTP·Spring·화면 요청서](../requests/20261029-request-http-spring-ui.md#반복-조회와-제어-진행성-연결)에 누적했다.
Worker의 관측 오류/옛 intent를 부재·새 복구 허가로 바꾸지 않는 연결은
[기존 Worker 요청](../requests/20261029-request-recovery-worker.md#일반-조회-변경의-소비-연결)에 추가했다.
D01/D03·D02/D08의 담당자 미완료를 대신 닫지 않는다.

4번의 **하위 구현·로컬 수락·부분 패키징**을 마감한다. 모든 실행 스케줄의 무기아나
저장장치/커널 I/O 선점 상한을 보장하지 않는다. 남는 정당한 대기는 실제 변경의 실행
소유권, 해당 VM의 짧은 관측 게시/필수 저장과 Xen/Store 자체의 응답이다. 5번의 create
전체 deadline·미게시 실패 자산 종결/예약 해제, 상위/Worker 연결·실제 HTTP/화면/보드,
API cgroup/backend 귀속 수락은 별도다. 다음 단계·보드 적용·commit/push로 자동 진행하지 않았다.

## 4번 후속: 재검증 상한·슬롯 중단과 반환

### 1번 기준선·비용 복원의 기능별 대응

1번은 2~4번에서 주요 기준선·비용·설치본 차이를 확인한 상태다. 아래는 그 자료와 현재
진입점을 묶은 표이며 시작/삭제의 실제 보드 비용이나 제품 종단까지 완료했다는 뜻은 아니다.
이전 설치 모듈 8개 중 1개 일치는 위 초기 `RUNTIME_MATCH.json` 당시 결과이고, 이번에
보드 설치 상태를 다시 조회하지 않았다.

| 기능·현재 진입점 | 잠금·원장·외부 효과/저장 위치 | 기존 근거와 이번 확인 | 미확인·후속 |
|---|---|---|---|
| 생성 `Manager._mutate/_create/_complete_private` → `_prepare/make_instance` | 공통 수락/예약·게시와 잠금 밖 단일 Linux 준비. `operations`, `.operation-ID` workspace·public instance·tombstone·control 초기화·DHCP | 2번 양 OS prehash 0/검증 복사 1, 3번 원 실행·private/public·중단, 4번 polling 진행성 | 현재 단일 복사/동시성 후보의 대용량 보드 성능·원 create 기한·미게시 실패 종결은 5·7번 |
| 조회 `_query/ReadContext/_read_states` → `Execution.observe_many` | 네 일반 조회는 공통 실행 잠금 없음. 외부 관측 4슬롯, VM별 snapshot 게시. 원장 별도 완전 검색 | 위 같은 조건의 외부 명령/메타데이터/잠금 전후 표·S4-T02/03, 아래 비용 목적/상한 | 설치 VM/원장 수·helper·SD의 실제 비용은 7번 |
| 시작 `_dispatch_locked` → `request_control/Execution.control` → `Platform.start` | 기존 공통 실행·admission·원 `control-requests/control/runs`·launch 기록. Xen create/scope·CPU guard·DHCP | management_budget의 원 key/기한과 replay, preparation_concurrency의 같은 VM 충돌, recovery/reconciliation의 launch/scope/receipt 회귀·현재 source 경로 | 보드의 시작 단계별 비용과 API 재시작 backend 보존·Agent/화면은 7번 |
| 정지 `_accept_stop/_dispatch_locked` → `Execution.control/Platform.stop_many` | 짧은 의도 수락 뒤 실행 잠금·현재 자산/신원 재검증·shutdown·부재 확인·원 control 결과 | 3·4번 A/느린 조회 미해제 중 B 실제 하위 종료 명령/원 completed, 예산·의도·미확정 회귀 | 로컬 Xen 대역. 실제 Xen 종료 및 상위 원 key/후속 이행 수락은 6·7번 |
| 삭제/복원 `_transition/_purge` → `_stopped/_usage/_ownership` | 공통 실행 잠금. retire 숨김 보관, restore 현재 IP/용량·Linux e2fsck, retired만 purge. 원 operation/tombstone·inode inventory·unlink/fsync·DHCP | management의 이동 중단·partial purge 재개·EIO·링크/Xen/loop 보호 시험과 현재 함수 대조. 이번에 삭제/이동을 실행하지 않음 | 미게시 workspace 수습은 기존 retire/purge가 아님: 5번. lifecycle 비용·화면 용어/공간은 6·7번 |

### 추가 읽기의 목적과 상한

관측 정합성에 필요한 다른 경계의 확인을 동일 단계 중복으로 합치지 않는다. 위 비용 표의
증가한 JSON 읽기는 아래 목적에 연결된다. 함수/회차의 상한이지 전체 VM/원장 수나 커널 I/O
시간에 무관한 고정 비용·최악 지연 보장이 아니다. 파일별 추가 호출은 trace로 먼저 대조한다.

| 구분·함수 | 재사용/재검증의 목적 | 현재 상한·범위 |
|---|---|---|
| 같은 회차의 외부 사실: `_observe_many/_view` | 완전 목록을 모든 대상에 공유, UUID별 builder 입력·사전 읽은 intent/run을 응답 조립에 재사용 | 비어 있지 않은 한 회차에 `xl list -v` 2회·존재 대상별 identity 2회. builder의 재호출/상세·짧은 목록 0 |
| 잠금 밖 조회 후 등록/수명 대조: `ReadContext.validate/validate_one` | 최초 구성/수명과 현재 대상 집합·record/tomb·실행/의도를 결합. 오래된 구성+새 실행 혼합 방지 | get 등록부 1회, list 최초+집합 재확인 2회/회차. 대상별 채택 직전 metadata 대조 1회, 외부 helper/전체 등록부 재검색 없음 |
| 원 key 검색 완전성: `_query.search/_operation_view` | 첫 일치가 유일함을 추정하지 않고 전체 검색 결과·원 실행 소유 변화 확인 | asset/control 각각 완전 검색 2회/회차. asset 원 결과/소유 대조 최대 2회/회차. Xen/VM 등록부 enrichment 0 |
| 문맥 변경의 재시도: `_query` | 계속 바뀌는 입력을 무한 재관측하지 않음 | ReadChanged에만 새 회차 최대 1회, 합계 최대 2회. 최대 외부 목록 4회·identity는 회차별 2×존재 대상 수의 합, 원장 완전 검색 4회. 같은 잔여 예산, 기한 연장 없음 |
| 효과 제출의 별도 guard | 표시 근거를 start/stop/삭제/복구 허가로 사용하지 않음 | 실제 제어의 상세 자산/신원 검사는 별도 기존 경계. 제거할 같은 회차 중복으로 취급하지 않음 |

호출 간 cache/새 DB/helper는 만들지 않았다. 4번 최초 비용 비교의 세 표본은
`candidate-proof` 당시 값으로 보존하며 아래 파일 모드 검사 보완 후보의 새 지연 실측으로
바꿔 표기하지 않는다. 관측/검색 구조와 계수 assertion은 아래 최종 회귀에서도 유지했다.
실제 설치 자료로 단건/전체 비용을 판정하는 것은 7번이다.

### 슬롯 소유자 중단·파일 보호의 직접 시험

`test_S4_T12_owner_exit_keeps_helper_slot_until_actual_exit_then_recovers`는 세 슬롯을
계속 보유하고 나머지 하나에 실제 Manager get과 Python helper를 진입시킨다. Xen 출력만
기존 대역이며 실제 runner·fork·pipe·flock·상속 FD·부모 SIGKILL/waitpid·자식 pidfd를 사용한다.
부모 중단 뒤 helper 생존과 슬롯 FD 하나를 확인한 채 신규 list는 미수락 경합,
원 operation 조회는 성공한다. helper를 해제해 실제 종료를 확인한 뒤 **동일 inode의 슬롯**을
획득하고, 다른 세 슬롯을 해제하기 전 정상 get도 성공한다. FD 누수는 없다.
Linux 준비의 부모/자식 시험을 이 새 관측 슬롯의 근거로 대체하지 않았다.

`test_S4_T12_slot_files_reject_unsafe_paths_modes_and_owners_without_repair`는 symlink·
hardlink·directory·FIFO·0666 모드·다른 uid·연결된 runtime/locks 경로를 각각 거절한다.
uid만 fstat 경계에서 대역으로 바꾸고 파일/링크/모드와 FD는 실제다. inode/mode/외부 원문
보존과 정상 0600 파일 획득, FD 회수를 확인했다. 실제 타 uid 침입·적대적 root의 경합까지
시험한 보안 감사라고 확대하지 않는다.

확인한 실제 차이는 관측 슬롯이 0666 기존 파일을 수락하던 한 곳이다.
`xenova_runtime.observation_slot`에 기존 Linux 준비 `_plain`과 같은 0600 검사를 추가했다.
잘못된 파일을 삭제/chmod하거나 다른 슬롯으로 건너뛰지 않는다. 초기 실패
`focused-followup-before.log` 2건은 vm.run에 없는 pass_fds 인자를 준 시험과 runtime 부모
디렉터리 누락이라는 fixture 오류였다. 이를 고친 `focused-followup-repro.log`는
**1 failed/30 passed**, 실제 permissive-mode 수락만 재현했다. 두 실패 이력 모두 보존한다.

### 후속 후보·회귀·부분 설치와 인계

후속 후보는 같은 HEAD에 기존 diff와 runtime 검사/새 시험/문서를 더한 조합이다.
원자료 폴더는 기존 `C:/Users/SSAFY/Documents/Codex/2026-10-30/xenova-stage4-20261030/`,
실행 소스는 `/home/ssafy/xenova-stage4-20261030/followup-final/`, 소스 봉인은
`FOLLOWUP-FINAL_SOURCE.json`이다. 기존 `FINAL_CANDIDATE.json/final.diff`는 앞선 후보의
자료로 보존하고 후속은 `FOLLOWUP_CANDIDATE.json/followup.diff`로 구분한다.

`run_checks.py regression followup-final`: **22 suite, 543 passed / 135.09초, skip 없음**.
기존 541개와 추가 집중 결과를 합산하지 않는다. 같은 후보의 두 중심 barrier·양 OS
polling·2/3번 비용/소유권/게시/종료·신원/예산/native/seed/Zephyr 회귀가 포함됐다.
신규 원자료는 `assertions-regression-followup-final/S4-T12-owner-exit.json`과
`S4-T12-slot-files.json`, 실제 argv/rc는 `regression-followup-final.json/.log`다.

`package_check.py followup-final`: recipe 소유 Python 29개 source/staging/archive의 바이트·
0644 모드와 6개 Python `-I` import를 재확인했다. recipe/의존성/native/게스트는 무변경이다.
`SUBSET_PACKAGE-FOLLOWUP-FINAL.json`과 `xenova-stage4-runtime-files-20261030-followup-final.tar`,
묶음 SHA-256 `dbe68291756460538993b4580d519e8bd947546cb2f4ec818360e07a78421b0c`로 연결한다.
이것은 부분 설치 묶음이며 정규 RPM/이미지/ARM/Xen/SD 설치 수락이 아니다. 무관한 ARM 재빌드는 안 했다.

정식 조회 계약/관리 구조에 슬롯의 파일 조건과 부모 중단 의미를 반영했다. 기존 HTTP 요청서에
다른 VM stop·원 key/ID 조회의 권한 거절, 내부 경로/비밀 오류 노출 차단, 실제 threadpool 포화 중
정상 읽기+제어 진행의 음성 수락을 명시했다. Worker 요청에는 이 입력을 사망/새 복구 허가로
바꾸지 않도록 누적했다. 두 요청서의 제품 수락은 계속 대기이며 타 담당자 코드는 변경하지 않았다.

5번 제안은 같은 작업 폴더의 `STAGE5_IMPLEMENTATION_PROPOSAL.md`에 둔다. 원 create 기한·
기존 원 결과 회수·명시적 미게시 수습·예약 반환·schema/권한/삭제의 미확정 결정을 구분한다.
큰 설계서에 일시 작업 계획을 추가하지 않았다. 5번 구현·실제 자산 정리·보드·commit/push는 하지 않았다.

## 원 create 기한·실패 종결과 예약 반환

### 기준선과 변경 책임

2026-10-31, 기준 HEAD `69387c119f3ca66b59a2b5b450503721a74bad7b`, 브랜치
`feat/platform/recovery-scenario-closure`의 작업트리 후보다. 위 4번의 543개는 선행 근거이며
이번 결과와 합산하지 않는다. 착수 시 tracked/untracked 변경은 없었다. 과거 후보로 되돌리지 않았다.
원 입력 계획과 원자료는 저장소 밖 `C:/Users/SSAFY/Documents/Codex/2026-10-31/xenova-stage5/`
(이하 P5)에 보존한다. `BASELINE_SOURCE.json`, `CANDIDATE-FINAL_SOURCE.json`,
`FINAL_CANDIDATE.json`의 HEAD·diff·새 파일·해시로 후보를 식별한다.
시험 snapshot 뒤의 문서 수정은 최종 봉인에 따로 포함하며 시험/패키지의 제품 바이트와 대조한다.

| 변경 위치 | 책임과 유지한 경계 |
|---|---|
| `xenova_creation.py`(새 내부 모듈) | 원 생성의 유한 정책/host/기한, 버전형 수습 검증, private inventory·현재 사용 검사, 명시 삭제 계획/재개, 공통 예약 반환 판정. 새 원장·DB·자동 실행기를 만들지 않음 |
| `xenova_management.py` | 수락 저장 전후 기한, private 준비/게시와 원 결과 정산, 원 조회의 RUNNING/RECONCILING/FAILED, 같은 create key의 실패 replay. 원 UUID/spec/할당/의도·단일 Linux 슬롯을 유지 |
| `xenova_preparation.py`, `guests/common/prepare_instance.py` | 요청별 실행/정리 예산, 실제 sparse 읽기·부분 write·guest 명령/반환·게시 전 검사, 자기 자식/FD·Workspace 역순 종료. 전역 stdout/cwd/env 변경·추가 prehash 없음 |
| `xenova_execution.py`, `xenova_cpu.py`, `xenova_resources.py`, `xenova_recovery.py`, `xenova_zephyr_control.py` | 기존 미완료 자산 guard가 같은 `creation.holds`를 소비. 단순 status != SUCCEEDED로 수습 완료를 영구 차단하던 판단을 교체. 복구 정책/CPU 배치/사용자 의도/실행 신원은 변경하지 않음 |
| `xenova_vm.py`, `xenova_management_cli.py`, recipe | 하위 `operation-reconcile` 진입, create 한정 호출 상한과 설정 경로. 새 모듈 포함과 비활성 예시 설치 |
| 관련 platform 시험·bridge 및 dom0-manager `tests/test_platform_integration.py` | 명시 유한 시험 정책과 실제 private 준비 결과를 fixture에 제공. dom0-manager/FastAPI/Worker/Agent/Spring/프론트 **제품 소스는 변경하지 않음** |

두 기준 설계 문서의 설계나 MQTT 공개 계약은 변경하지 않았다. 새 runtime 의존성·SQLite·
daemon·청소 스케줄러·영구 큐는 없다. 실제 정리는 이번 시험이 만든 격리 fixture만 대상으로 했다.

### 5-A: 시간·호환·공개 의미

정식 사용법은 [operation-reconcile](../platform/cli-manager-spec.md#cmd-operation-reconcile)와
[생성 기한](../platform/cli-manager-spec.md#creation-deadline),
[관리 경계](../platform/management-boundaries.md#원-생성-실패와-예약-반환)에 둔다.
새 호출을 HTTP에 자동 노출하지 않았다. root 하위 CLI/Python과 사용자 HTTP 권한은 별개다.

| 계약 | 이번 확정 |
|---|---|
| 정책 | `/etc/xenova/creation-policy.json`: schema 1.0, 명시 양수 call_ms/execution_ms/cleanup_ms. call/실행 입력 상한 86460000ms, cleanup 상한 60000ms는 직렬화 한계이며 운영 기본값이 아님. 누락/무효 정책은 새 Linux create 수락 전 거절 |
| 세 시간 | 새 수락에서 `D_create=min(t_accept+execution_ms,D_call-cleanup_ms)`. 호출 대기는 D_call을 소비. 같은 원 요청·새 caller·재시작·조회가 D_create를 연장하지 않음. 종료 여유는 원 실행의 여러 단계에서 한 번 시작한 동일 예산을 공유 |
| 저장 | 새 Linux create는 asset journal schema 1.1의 `creation.version=1`에 host boot·수락 시각·원 기한·정책을 저장. spec_hash에 timeout을 넣지 않음. 수습도 원 JSON의 reconciliation.version=1에 결합 |
| 호환 | 완료된 1.0 결과 replay 유지. 원 기한 불명/다른 boot의 미완료 생성은 자동 재개 금지, 현재 host의 명시 private 수습은 가능. 구형 writer와 혼합 배포는 지원하지 않음 |
| 진단/명시 적용 | 원 operation ID 또는 key·VM UUID·원 spec hash·discard-private. execute에는 진단 context hash와 안정 reconciliation_id 필요. 임의 경로/PID/device/force 입력 없음 |
| 결과 | 원 생성 실패와 수습 complete/release를 분리. 원장 status/phase와 관측 effective_status는 별개. 같은 create key는 원 실패+수습 기록, 새 복사/신원/기한 0. 별도 신규 생성은 새 UUID/current 자원 검사를 거침 |

recipe는 값이 null인 `creation-policy.json.example`만 `/usr/share/xenova`에 넣는다.
**운영 정책은 이번에 활성화하지 않았다.** 동일 후보를 설치하더라도 승인된 유한 정책을
공급하기 전에는 새 Linux create가 거절된다. 보드 실측 없는 숫자를 제품 기본값으로 정하지 않았다.
수습의 기본 15초/호출 상한 105000ms는 생성 운영 기한이 아니다.

### 5-B·5-C: 원 기한과 정상 종료·원 결과 회수

같은 조건의 수정 전후 반례는 `run_checks.py audit-before baseline`과
`audit-sealed candidate-final`이다. 실제 Manager/검증 복사/원장을 실행하고 첫 root write 뒤
제어 시계를 10초에서 100초로 이동했다. 시험 정책은 call 60000/실행 10000/정리 1000ms로 동일하다.

| 관측 | 기준선 | 최종 후보 |
|---|---|---|
| 원 host/실행 기한 기록 | 없음 | 최초 수락의 원 기한 20000ms 보존 |
| 동일한 원 root write 뒤 시계 이동 | 계속 진행·public 게시·ok=true | DEADLINE_EXCEEDED·public 없음·원 operation/부분 파일/오류 보존 |

두 감사 도구의 rc=0은 각각 반례/수정 후 assertion 확인이다. 기준선의 제품 성공을 수락한
결과가 아니다. `audit-before-facts.json`/`audit-sealed-facts.json`에 원 operation과 응답을 남겼다.
로컬 제어 시계 분기이며 실제 SD 생성 시간/최악 지연/전원 상실 결과가 아니다.

수락 저장 직후, 복사 반복문, guest 반환, 게시 직전 만료를 직접 주입했다. 기한 뒤 새
준비/public 게시는 없다. 정상 실패는 자기 실행 자원을 제한적으로 정리하며 private 이미지를
자동 폐기하지 않는다. 실제 Workspace.close의 역순 umount/loop detach와 자식 종료 미확정 시
보류를 시험했다. mount/loop 명령은 대역이며 실제 커널 해제 수락은 아니다.

자식 timeout의 추가 반례(`cleanup-before-candidate-8.log`)에서 wait 인자는
`[1, 약 0.05]`였다. 정리 여유와 별개의 1초 wait를 없애 공통 소유 자식 종료 경로에서
남은 정리 예산만 쓴다. 이 수정은 실제 소유 subprocess와 wait 인자를 검사하며 통과했다.
이후 시간 검사는 협조적이며 fsync/커널 I/O의 강제 선점이나 실제 종료 상한을 보장하지 않는다.

public 이후는 원 자산 검증/등록 정산과 새 외부 효과를 나눈다. **dhcp-applied가 이미
확인된 원 작업**은 원 기한 뒤에도 현재 호출 예산으로 결과를 마감하고 최신 stopped 의도를
보존했다. DHCP 재제출/launch는 0이다. 확인되지 않은 DHCP는 만료 뒤 새 효과를 허가하지 않고
미확정으로 남긴다. 이 시험은 미완료 create에 대한 HTTP stop 지원을 가정하지 않으며 실제
짧은 Execution.accept 원시 연산으로 선행 수락된 stopped 의도를 구성한다.

### 5-D: 수습·잠금·예약 판정

| 현재 근거 | 수습/반환 판단 |
|---|---|
| 확인된 active 원 준비 또는 다른 cleanup | 원 실행/원 조회로 연결, 탈취·삭제·예약 반환 없음 |
| 부모 부재여도 상속 FD·private inode·mount/loop 등 사용 잔존 | 원 실행 종료/미사용 확인 전 차단. 슬롯 파일 부재/PID/시간으로 대체하지 않음 |
| 관측 실패·불완전 열람·원장/schema 손상 | 없음/정상/반환으로 추정하지 않고 해당 오류로 종료 |
| workspace 전 실패·소유 효과 없음 | 명시 수습에서 삭제 0회로 원 실패와 예약 종결 가능 |
| 부분/완성 private·소유 확인·현재 미사용 | 원 계획 내구 저장 뒤 고정 항목만 폐기. source 손상/부재를 재해시하며 폐기를 막지 않음 |
| public/등록 충돌·원인 모를 부재·교체 inode·링크/특수 파일/권한 불일치 | 삭제/성공/예약 반환 금지. 진단 이후 변경도 실행 시 재확인 |
| 계획 저장 실패 | 삭제 0. 최초 create 오류와 수습 저장 오류는 별도 |
| unlink/부모 fsync/최종 저장 뒤 중단 | 저장한 deleting/root_pending과 남은 항목으로 동일 수습 재개. 자기 진행 revision은 새 요청으로 보지 않음 |
| complete JSON만 보이고 동기화 미확정 | 조회는 기록 내용을 표시. 실제 예약 소비 writer의 파일/부모 fsync와 동일 원장 재확정 실패 시 자원 소비 금지 |
| 현재 미사용·자산 부재·내구 완료 확인 | 해제 가능한 활성 예약 제외. 원 실패/key/spec/UUID 이력과 stable lock은 보존. 별도 정상 생성 허용 |

순서는 기존 Linux 슬롯(공통 잠금 밖) → 원 작업 확인/계획 저장(짧은 공통 잠금) →
열거·현재 사용 확인(잠금 밖) → 각 삭제 marker 저장 → inode/gate 확인 후 개별 unlink/부모
fsync(짧은 admission) → 진행 저장 → 잔여 사용/충돌 검사 → 내구 완료다. 긴 정리 전체로
공통 잠금을 점유하지 않으며 quiesce 뒤 다음 파괴적 단계를 차단한다. 재개는 같은 명시
수습의 새 호출이고 옛 생성의 게시 허가를 부활시키지 않는다.

`_reserve_space/_ip_inventory/_mutate/_control_view`, Execution.control, resource guards,
Recovery.conditions, CPU.guard_stopped, Zephyr reboot가 동일 holds 판정을 사용한다.
원 UUID 이력 충돌은 계속 막고 IP/활성 용량 예약은 완료 후 반환한다. CPU의 전역 guard가
수습 전에는 차단하고 수습 뒤에는 통과하는 직접 시험도 포함한다. sparse 실제 블록 차감은
기존 식을 유지했고 기존 용량/IP/lease·복구/자원 suite를 함께 재실행했다. 모든 action의
운영 조합/실제 DHCP·CPU 변경을 이번에 수락한 것은 아니다.

기존 2GiB 증가분 여유와 128KiB 수습 기록 상한은 유지하나 별도 물리 공간을 보장하지
않는다. 저장소 ENOSPC/EIO에서는 계획/완료를 확정하지 않고 호출을 끝낸다. 원장/다른 VM/
로그 삭제나 reserve-file로 우회하지 않았다. 저장소 회복 후 동일 수습·새 생성 성공까지 시험했다.

### 5-E: 수락 대응과 원자료

새 시험은 `platform/tests/test_xenova_creation_reconcile.py`, 기존 근거는 같은 최종 후보의
preparation_concurrency/read_progress/verified_copy/creation_failure 및 관리/복구 suite다.

| 요구 | 실제 assertion·근거 |
|---|---|
| S5-T01 | 정책 부재에서 원장/준비 0·정상 읽기, 수락 저장 직후 만료에서 원 기한/ID 보존·준비 0. 기존 management_budget/atomic_write/failure_record 시험으로 미수락/저장 미확정 구분 |
| S5-T02 | 양 OS 실제 검증 복사 내 만료, Ubuntu guest 반환·Debian 게시 직전 만료, 공유 cleanup margin·소유 child wait 한도·실제 Workspace 역순 종료. sparse/부분 write/source guard는 기존 verified_copy/두 OS 경로 시험 |
| S5-T03 | 새 caller/동일 boot 원 기한 보존, legacy·다른 boot 재개 금지/명시 폐기 성공. 기존 완료 replay 회귀 유지 |
| S5-T04 | 실제 열린/unlinked FD, loop 사용·관측 EIO에서 삭제 0; 해소 후 같은 수습 성공. 자식 미확정 시 close 보류. 상속 FD 부모 중단·host unmount 차단은 기존 S3/S4 프로세스 시험 재실행 |
| S5-T05 | workspace 전 실패 삭제 0 종결, private 만료 게시 0, dhcp-applied 이후 원 결과 정산·stopped 의도 유지. 기존 recorded/private/public 중단 회귀 |
| S5-T06 | 진단 뒤 다른 inode·원인 모를 다른 항목 부재·public broken symlink·hardlink/FIFO/모드·열린 파일 거절. 자기 private 폐기와 정상 후속 생성도 성공 |
| S5-T07 | 계획 ENOSPC 삭제 0, 완료 부모 fsync EIO에서 보이는 complete만으로 예약 소비 0, unlink/루트 제거 뒤 실제 SIGKILL 및 새 프로세스 회수. 같은 원 오류·수습 ID 보존 |
| S5-T08·T10 | 동일 수습 병행은 슬롯 미수락, 완료 응답 유실 replay/다른 수습 ID 거절. 원 create는 원 실패+수습 완료·재복사 0. 완료된 원 성공/현재 관측 실패 분리는 S3/S4 회귀 |
| S5-T09 | 양 OS 수습 barrier를 해제하지 않은 채 정상 list/get/원 조회·B 실제 하위 graceful stop/원 completed. polling 계속 성공. 준비 중 polling은 기존 S4-T03 양 OS·1/2/4 폴러 회귀. quiesce 뒤 삭제 차단/명시 재개 |
| S5-T11 | 아래 29 suite와 별도 dom0-manager 전체 회귀. prehash 0/검증 복사 1·seed/Zephyr·S01/S02·receipt/복구/CPU/의도 유지 |
| S5-T12 | 하위 strict CLI 진단/임의 path 거절만 확인. 실제 HTTP/Spring/화면 권한·DTO·명시 확인·사용성은 담당자 구현/종단 수락 대기 |
| S5-T13~16 | 실패 후 공통 잠금 반환, workspace 0개 종결, source 손상/부재 폐기, 저장소 회복 뒤 같은 수습 완료, 공통 예약 guard/원 UUID 이력·반환 IP 재사용과 별도 정상 생성 |

중심 양 OS 흐름은 실제 원 root write EIO → 부분 파일/원 오류 → cleanup unlink 뒤 중단 →
새 Python 프로세스에서 동일 수습 완료 → 같은 create key 원 실패 replay/복사 0 → 새 UUID
생성 성공·반환 IP 재사용이다. `assertions-regression-candidate-final/S5-T08-*`에 원/새
operation·오류·수습·동일 key 응답을 연결했다. `S5-T07-kill-*`은 실제 SIGKILL 두 지점,
`S5-T09-cleanup-barrier-*`는 cleanup_alive/released=false·polling_alive·정상 조회 수와
B의 physical_result/원 control operation completed를 남긴다. 전체 기록에 비슷한 상태가
있었다는 추정 대신 해당 barrier 안에서의 원 요청·응답 인과를 assertion으로 검사한다.

최종 실행은 기존 고정 WSL 도구·격리 경로에서 수행했다. `run_checks.py`의 각 JSON에 전체
pytest argv/rc/실행 시간을, 같은 이름의 log에 원 출력을 보존했다.

| 명령(모두 P5의 run_checks.py) | 최종 결과 |
|---|---|
| `focused candidate-final` | 새 수습+S4 집중 66 passed, 32.00초, rc=0 |
| `regression candidate-final` | 관련 29 suite, 696 passed, 197.70초(pytest)/198.10초(실행기), rc=0, skip 없음 |
| `manager candidate-final` | dom0-manager 전체 576 passed, 58.85초/59.65초, rc=0, skip 없음. 기존 Starlette/httpx 사용 deprecation warning 1개 |
| `audit-sealed candidate-final` | 원 기한 뒤 public 없음·원 기한/부분 파일 보존 assertion 통과 |
| `check_docs.py`, `git diff --check` | 6개 소유 문서의 로컬 링크/앵커 289개 오류 0, diff 공백 오류 0 |

집중 시험은 관련 회귀에 포함되므로 수를 합치지 않는다. 새 dom0-manager 의존성이나
httpx 버전을 설치/변경하여 warning을 숨기지 않았다. 기존 S01/S02의 손상 차단·동일 사건 ID·
pending 원자 저장 시험은 이 별도 suite에서 재실행했다.

시험은 실제 하위 Manager/원장/파일/검증 복사/flock/요청별 runner/subprocess를 사용한다.
Xen/Store·mount/loop·chroot/게스트 설정과 storage_check는 명시 외부 대역이다. 새 수습
시험의 전역 `/proc` 열람은 자기 프로세스(새 프로세스 시험은 자기/부모)의 실제 FD로 제한했다.
따라서 전체 root 프로세스 접근·실제 Xen/SD/게스트 backend·권한/전원 상실 수락은 아니다.
테스트의 짧은 timeout·제어 시계/축소 payload를 운영 시간/보장 상한으로 옮기지 않는다.

초기 실패 원자료를 삭제하지 않았다. candidate-1의 evidence 파일명 충돌, candidate-2의
추가 fixture 정책 누락, candidate-3의 해제된 inode가 새 소유 파일에 재사용된 오탐을
구분했다. inode 오탐은 현재 FD 경로/nlink까지 확인해 수정했다. candidate-4의 HTTP stop
가정·candidate-5의 임의 key 가정은 실제 기존 계약에 맞춰 시험만 고쳤다. candidate-6의
bridge public/None 대역은 기준선에서도 재현(`bridge-before-baseline.log`)되어 실제 private
결과 fixture로 교정했다. dom0-manager 7개 실패도 정책/private 시험 경계만 보완했다.
최종 후보의 제품 통과를 이 초기 실패 로그로 대체하지 않는다. 최초 독립 cleanup-wait
명령의 pytest 경로 누락은 환경 실패이며, 고정 도구를 사용한 재실행에서 wait 반례를 재현했다.

### 부분 패키지·인계와 남은 수락

`package_check.py candidate-final`은 recipe의 실제 install 목적지/모드를 따라 Python
**33개와 비활성 설정 예시 1개**를 소유 staging/archive로 구성했다. 이전 29개 subset에
없던 기존 하이픈 이름 Python 도구도 실제 목적지로 포함했으므로 파일 개수를 직접 비교하지
않는다. source/staging/archive 바이트·모드와 **11개 설치 경로의 `python -I -B` import**를
확인했다. `SUBSET_PACKAGE-CANDIDATE-FINAL.json`에 각 파일 해시/모드/출처를 둔다.
archive SHA-256은 `cd7e9d164107d5e7708066c8754f684eec50d3f60ad707b70c28570521b59b0c`다.

RDEPENDS는 기준선과 동일하다. 새 내부 모듈의 contextlib/hashlib/json/os/pathlib/re/stat는
기존 설치 모듈에서 이미 쓰는 표준 모듈이다. 고정 OE `0f03c732a0a2d0506b41b1abe60b3f368b2300dc`의
기존 표준 패키지 closure/manifest 근거를 재사용했다. 새 표준/runtime 패키지는 추가하지 않았다.
recipe의 새 module/example 포함을 확인했으며 native identity/guest 소스는 그대로다.
이 결과는 **부분 설치 묶음**이며 BitBake/RPM/전체 이미지/실제 설치본 검증이 아니다.
호스트 Python만 바뀌어 무관한 Zephyr ARM 재빌드는 하지 않았다.

[HTTP·Spring·화면 요청](../requests/20261029-request-http-spring-ui.md#원-create-기한과-실패-자산-수습-연결)에
실제 `_BUDGET_ACTIONS/SerializedManager/PlatformDriver` 위치, 권한/명시 확인, 원 key·
context·수습 ID 보존, 허용 응답 필드와 같은 시험의 실제 HTTP 인수 조건을 누적했다.
[Worker 요청](../requests/20261029-request-recovery-worker.md#원-create-실패-종결의-소비)은
수습을 VM 사망/재시작·Standby 재합류·출력 허가로 소비하지 않도록 연결했다. 요청서 작성은
담당자 제품 구현 완료가 아니다.

5번의 하위 구현·로컬 검증·부분 패키징 범위는 여기서 마감한다. 운영 유한 시간값 공급/
실측, 동일 모듈 후보 설치, 실제 HTTP/Spring/화면 권한과 응답 유실/명시 삭제 수락,
Xen·SD/전원 상실·실제 mount/loop·API cgroup/backend 소유는 별도 미완료다.
이번에는 보드·서비스·운영 상태·전체 이미지·commit/push를 변경하지 않았으며 6·7번은 실행하지 않았다.
