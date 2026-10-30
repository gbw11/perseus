# 복구 시나리오 국소 마감: Authority 진단·출력 검증·조회 예산

## 현재 상태 요약

| 구분 | 현재 판정 |
|---|---|
| 2번 최종 구현 | `a15bc3f`: Linux 사전 전체 payload 해시 0회·검증 결합 복사 1회·원 작업과 부분 실패 보존. 설명/기존 측정 기록은 `5f3dba6` |
| 로컬 근거 | 기존 최종 57개 집중·449개 관련 회귀와 세 모듈 부분 설치 바이트 대조. 이후 Debian·Ubuntu 명시 사례 4개를 추가해 집중 61개 통과, 관리·Zephyr 54개 재실행 통과 |
| 보드 상태 | 앞선 2회 처리 후보의 Debian 생성 477.203초만 측정. 최종 1회 후보는 미적용·성능 미측정 |
| 2번 공통 적용 | 기본 lock의 Debian·Ubuntu ID/버전·출처와 실제 카탈로그/준비 경로를 축소 payload로 검증. 실제 고정 대용량 산출물·Ubuntu 부팅 수락은 별도 |
| 실패 후 종결 | 부분 파일 보존·재복사 차단은 제공. 미게시 자산의 지원되는 종결·정리·예약 해제는 후속 5번 미완료 |
| 3번 로컬 마감 | 단일 Linux 준비 슬롯·원 실행 소유권·private 준비/게시 분리·요청별 출력. 최종 관련 회귀 481개 통과, 두 OS 모두 A 복사 해제 전 B 조회/실제 stop 완료 원자료 확인. 부분 설치 29개 Python 파일·독립 import 일치 |
| 남은 범위 | 4번 polling/공정성·5번 create 전체 deadline/실패 종결, 상위 제품 연결·정규 설치/보드 수락·API cgroup의 guest backend 문제는 미완료 |

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
RECOVERY_REQUIRED, 소유권 관측 오류는 OBSERVATION_UNAVAILABLE, 불완전 검색/예산 소진은
DEADLINE_EXCEEDED로 구분한다. 신규 슬롯 미수락에만 accepted=false·operation_id=null을
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
