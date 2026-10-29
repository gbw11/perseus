# 복구 시나리오 국소 마감: Authority 진단·출력 검증·조회 예산

## 후보와 수락 범위

기준은 `eca59e1ba272496b7c661bcd5088b73624ed0763`, 작업 브랜치는
`feat/platform/recovery-scenario-closure`다. 후보는 기준 HEAD에 미커밋 diff·새 파일·소스별
해시를 더해 식별한다. 앞선 Zephyr 독립 실행·준비/커밋 검증은
[선행 보고서](zephyr-drive-foundation-20261029.md)에 보존한다.
아래 국소 구현·검증은 기존 4개 커밋의 범위이며, 후속 검토 브랜치 병합과 제품 수락
준비 결과는 마지막 절에서 별도로 구분한다.

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
