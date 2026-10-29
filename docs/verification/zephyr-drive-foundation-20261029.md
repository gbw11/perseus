# Zephyr 계산 독립 실행·원자료 회수·운영 구성 검증

이 문서의 기존 절은 최초 봉인 후보의 당시 결과다. 이후 명시적으로 승인한 warm 프로필·
앱별 재초기화·Gateway/Worker 연결 결과는 마지막 후속 절에서 구분한다. 이전 산출물과
실패 기록을 새 후보의 성공 근거로 덮어쓰지 않는다.

## 기준과 완료 범위

기준 HEAD는 `30889a544f9784302dbb74b8047ea0e6919d97d8`, 작업 브랜치는
`feat/platform/zephyr-drive-foundation`이다. 후보는 HEAD만이 아니라 미커밋 diff·새 파일과
ARM `BUILD_RECEIPT.json`의 입력별 SHA-256으로 식별한다. 과거 후보로 되돌리거나 원격을
병합하지 않았다. 조사 시 원격 develop은 `c4ff944`, platform은 `1ed0653`이었다.

`docs/platform/recovery-scenarios.md`는 작업 전부터 있던 untracked 입력이다.
이 파일과 `docs/platform/drive-pipeline-architecture.md`의 설계·본문은 변경하지 않았다.
새 준비 메시지·warm Standby 프로필·앱 재시작 정책을 임의로 추가하지 않았다.

| 묶음 | 이번 결과 | 별도 수락 |
|---|---|---|
| 준비·건강 근거 | 실제 계산 완료, 준비, 출력 허가, 내부 대기의 구분을 기존 목적 문서에 정리. 평가하지 않은 모드의 초기 0을 정상 건강값으로 게시하지 않음 | warm Standby의 대기 자격·활성화 확인·진행을 구분하는 소비자 공동 계약 |
| 원자료 회수 | 기존 페이지 출력만 사용한 제한 재시도·실행/창/내용 대조. 실제 native shell 출력과 분석기 연결 확인 | 실제 Xen PV ring·UART 정체·장시간 회수 |
| 운영/실험 구성 | recorder·요청형 상세 스택 스캔을 선택 구성으로 분리. 두 ARM 후보를 개별 부분 패키징 | 최종 이미지 포함·설치본 구성 |
| 독립 실행 기반 | 기존 main에 AUTO/MANUAL 계산 스레드 연결, 원 입력·기한·완료 보존, 부분 시작/잔존 처리 | 새 planner·차량 입력·앱 단독 재초기화·실제 Xen 지연/스택 |

실보드 접속·서비스/VM/CPU/네트워크 변경·전체 이미지 조립·commit/push는 수행하지 않았다.

## 실제 변경과 보존 경계

- `platform/apps/drive/zephyr/src/workers.{c,h}`: 계산별 실제 `k_thread`, 어댑터, 실행 중
  사본·최신 대기 사본·결과 칸을 소유한다. callback 동안 교환 잠금을 보유하지 않는다.
  AUTO 지연이 MANUAL callback이나 main의 완료 대기로 이어지지 않는다. 같은 주소 공간의
  메모리 손상 격리와 강제 thread 복구는 제공하지 않는다.
- `main.c`, `controller.{c,h}`: 기존 제어 검증을 분리해 재사용한다. 두 계산은 고정 20ms
  원점의 원 release/deadline을 전달받고, 지나간 release를 몰아서 수행하지 않는다.
  기존 `verified-drive-1` 호환으로 두 계산을 수행하며 warm Standby 계산 중단은 활성화하지 않는다.
- 공통 runtime/IO: 선택된 계산의 실제 시작·완료만 공통 `loop_seq`에 반영한다.
  SAFE_STOP에서는 실제 MANUAL 완료를 관측한다. 앱별 progress는 각 어댑터의 실제 근거다.
  이 계측 범위 변경은 사용자 승인 범위이며 이전 직렬 두 계산 합계와 같은 성능 표본이 아니다.
  원 입력 수신 시각, control/connection revision, 실제 생산 uptime-ms를 보존한다.
  readiness 300ms·heartbeat 1000ms의 기존 수명은 coordinator 확인 시점에 갱신하지 않는다.
- 종료는 admission을 먼저 닫고 실제 생성한 계산 스레드만 join한다. 계산·v1·Publisher·
  netfront/Store는 원 guest cleanup deadline을 공유한다. 늦은 callback은 오류·잔존으로 남기고
  abort·stack 재사용·새 기한·자동 force로 성공시키지 않는다.
- `xm_payload.c`: payload parser 세 개의 정적 JSON 작업 메모리를 호출별 제한된 stack으로
  옮겼다. 새 동시 호출에서 기존 공유 메모리 충돌을 실제로 재현한 수정이다. envelope decoder는
  기존 v1 I/O 단일 소유를 유지한다. MQTT 필드·토픽·ACL·QoS·알고리즘은 바꾸지 않았다.
- recorder는 고정 원자료 배열과 capture 명령을 실험 구성에서만 포함한다. 운영 구성의
  `capture/records/transmissions`는 소스상 `-ENOTSUP` 분기로 거절한다. 기본 진단·CPU 계상·
  heap/pool·실제 완료·오류·Publisher·협조 종료는 유지한다.

현재 구조와 사용법의 원문은 [공통 펌웨어](../../platform/apps/drive/README.md),
[runtime/회수 사용법](../../platform/apps/common/xenova-drive-runtime/README.md),
[건강 근거](../contracts/health-evidence-v1.md)이다.

## 회수 도구의 보장과 제한

`tools/collect_console.py`는 기존 `records OFFSET 4`, `transmissions OFFSET 4`만 조회한다.
매 페이지의 전후에 records-0의 VM/app/boot/instance/execution·측정 창을 대조하고 페이지를
두 번 읽어 내용·offset/next/total·행 수를 비교한다. 페이지 기본 3회, 전체 기본 300초이며
재시도는 같은 offset·같은 전체 기한을 사용한다. 미확정 송신·overflow·실행/창 교체·누락은
성공으로 바꾸지 않는다. 완전한 회수 후에도 기존 분석기의 후보별 최종 기록 대조가 필요하다.

구 프로토콜에 새 `XD_WINDOW`·페이지별 신원·해시를 추가하지 않았다. 페이지 자체에 실행 토큰이
없으므로 운영자가 독점한 raw PTY와 직렬 조회, 회수 중 외부 root가 창/바인딩을 바꾸지 않는다는
조건이 필요하다. 초기 prompt와 명령 echo·종료 prompt가 필요하며 응답 timeout 뒤 이전 prompt를
확인하기 전 새 명령을 보내지 않는다. collector의 flock은 협조하는 수집기끼리만 배제한다.
32KiB 응답 한도와 유한 기한을 넘는 환경은 실패로 남는다. 전체 12,288행을 어떤 연결에서도
300초 안에 회수한다는 보장은 없다. 전송 완전성과 기능·성능·장치 적용은 별개다.

## 실행한 검증

고정 도구는 기존 WSL의 Zephyr `684c9e8f32e4373a21098559f748f06915f950c9`,
Zephyr SDK 1.0.0의 AArch64 GCC 14.3.0과 기존 CMake/Ninja다. 새 의존성을 설치하지 않았다.
개별 로그에는 실제 argv·종료 코드·소요시간을 함께 보관했다.

| 시험/소스 | 결과와 assertion | 외부 대체 경계 |
|---|---|---|
| `platform/apps/drive/tests` CTest | 4/4 통과. 기존 controller/health와 실제 payload parser 6 thread·120,000회 동시 호출 | host 실행, Xen/스케줄링 보장 아님 |
| `PARSER_BEFORE.json` | HEAD의 정적 parser로 같은 probe가 assertion 실패(return -6). 수정 후 concurrent-payloads 통과 | 기존 코드를 소유 임시 경로에서만 재현 |
| `xenova-drive-workers` | 7/7: 정상, 독립 진행, 최신 대기 교체, 만료 작업의 가짜 완료 금지, 잔존·기한 갱신 거절, 두 번째 thread 생성 실패, 미시작 join | 실제 Zephyr thread/잠금/어댑터. 지연·thread 생성 실패만 주입 |
| `xenova-drive-main` | 2/2: AUTO 첫 계산 120ms 지연 중 MANUAL 실제 6회 완료; 제어 변경 후 옛 결과 거절; 미래 완료 시각/중복 완료가 loop_seq를 증가시키지 않음; 평가하지 않은 모드의 정상 건강 게시 금지; 소유자 join | 실제 main/runtime/알고리즘. transport·Publisher admission·지연만 대체 |
| `xenova-drive-io` | 29/29: F01 공정성·창/문맥·최신 후보/in-flight·재접속·부분 시작/종료 및 원 생산 시각의 readiness/heartbeat 만료 | 실제 I/O owner·잠금. Bootstrap·socket 경계 대체 |
| `xenova-drive-lifecycle` | 9/9: 초기화 단계 실패·종료 경합·v1/Publisher join·원 cleanup deadline | 실제 lifecycle, 외부 자원 경계 대체 |
| recorder/shell capture fixture | 창 종료 직전 수락된 송신을 지연: capture 완료와 송신 확정 분리, 미확정 페이지/재설정 거절, 확정 후 total 유지. 실제 shell 페이지를 collector→`analyze.source()`로 연결(1행) | 합성 Bootstrap, MQTT 완료 지연. 실제 PV/UART 아님 |
| `test_xenova_drive_collection.py` | 16/16: 부분 응답·같은 offset 재시도, 신원/창/내용 교체, 잘못된 자료형, 누락·overflow·기한 및 실제 소유 POSIX PTY의 prompt/echo/timeout | 페이지 오류는 fixture, PTY는 실제 OS 객체 |
| `test_xenova_drive_integration.py` | 16/16: 기존 누락 거절, 중복 완료 거절, SAFE_STOP 미제출 행의 기본 rc=0을 수락 후보로 오인하지 않음 | 분석용 합성 자료; 실제 발행 수락과 구별 |
| `test_xenova_zephyr_lifecycle.py` | 3/3: 후보 입력 고정·실제 packager 호출·기존 socket lifecycle | 파일/소켓 외부 경계 fixture |
| `test_xenova_network_zephyr.py` | 22/22: 기존 실행·복구·네트워크 프로필 회귀 | 작은 firmware/Xen 명령 fixture |
| `run_native_pair.py --initial a/b` | 각 13단계: 양방향 모드 전환·대칭 승계·새 세션 Standby 재합류·옛 실행 거절·독립 v1/v2 수신·실제 serial encoder 제출 | 같은 native 바이너리 두 개+실제 격리 TCP/broker/ACL/Gateway. Manager binding/receipt는 명시적 합성 fixture, serial은 file sink |

운영 구성 A/B와 실험 구성 A의 native pair는 마지막 main 건강 게시 보호를 포함한
후보로 각각 13단계를 통과했다. 정확한 바이너리 해시와 단계는 최종 증거 manifest와
각 `result.json`으로 구별한다. native pair가 직접 만든
execution/receipt는 실제 Recovery Engine→Manager→provisioning 수락을 대신하지 않는다.
이번에는 그 제품 연결부를 수정하지 않았다. 성능 개선·CPU pinning 효과는 주장하지 않는다.

## ARM 부분 빌드·패키지

두 구성 모두 실제 `.config`의 profile flag와 stack sentinel, ELF load 범위·고정 xenvm DTS
event IRQ를 검사하고 기존 firmware packager를 실행했다. 실행 코드·설정·DTS·ELF의 패키지
바이트를 build 출력과 대조했다. 기존 1-vCPU/16MiB·RAM/GIC/네트워크 하드웨어 구성을 유지했다.

| 구성 | 최종 출력 디렉터리(WSL 소유 경로 아래) | zephyr.bin bytes | ELF text/data/bss bytes |
|---|---|---:|---|
| operations | `arm-operations-r5/firmware` | 408,368 | 401,919 / 6,448 / 643,068 |
| experiment | `arm-experiment-r3/firmware` | 412,464 | 406,641 / 5,816 / 3,198,972 |

운영 ELF에는 `rows/deliveries` 배열이 없으며 실험 ELF의 배열 크기는 각각 1,671,168 /
884,736 bytes다. 두 구성의 BSS 차이는 2,555,904 bytes다. 계산 스택은 각각 8KiB,
실제 compiler 직접 frame은 worker `run` 2,448 bytes, payload parser 2,000~2,048 bytes다.
netfront send 직접 frame은 48 bytes(기존 직접 frame 한도256)다. 이것은 전체 call chain,
libc·IRQ 중첩·실보드 high-water의 보장이 아니다. 실제 Xen 스택/지연 수락은 남아 있다.

운영 `zephyr.bin` SHA-256:
`3e02b40dd9b633e52fec2c429b070838099d34033f8ff8929fcf387bee701e7c`

실험 `zephyr.bin` SHA-256:
`4144f58531616f5fe077277b2cb54c098287f76d5ffe3a269b7bff350df4b789`

profile·입력/설정/compiler/ELF/DTS/번들 전체 해시는 각 `BUILD_RECEIPT.json`에 있다.
ARM 부분 패키징은 Yocto 전체 이미지·seed 편입·A/B 설치 자산 갱신 수락이 아니다.

## 증거와 후속 범위

- PC: `C:/Users/SSAFY/Documents/Codex/2026-10-29/xenova-zephyr-foundation/`
- WSL: `/home/ssafy/xenova-zephyr-foundation-20261029/`
- 실행기 `verify.py`, 직접 출력/패키지 대조 `inspect_final.py`, `ARM_COMPARISON.json`,
  `FORMATTER_COLLECTION.json`, `operations/experiment-BUILD_RECEIPT.json`, `SOURCE_SEAL.json`.
  최종 성공뿐 아니라 이전 실패·중간 후보도 보존한다. 처음 ARM의 `ESTALE` 미지원은 pinned
  libc에서 지원되는 기존 문맥 오류 `EXDEV`로 고쳤고, 시험의 time0 unsigned underflow도
  시험 입력만 바로잡았다. 오류를 0으로 바꾸거나 안전 기한을 늘리지 않았다.
- 운영 사용은 operations, 원자료 시험은 experiment를 명시한다. 게시 불변 bundle과
  공용 캐시·별도 이미지 빌드 작업은 변경하지 않았다.
- 다음 공동 확정 대상은 설계 9.6절의 대기 자격/활성화/진행 계약, 앱 단독 재초기화와
  상태 있는 planner 어댑터다. 기준 문서의 설계를 바꾸려면 먼저 사용자 검토를 받는다.
- 실제 PV/UART 회수, Xen CPU 실행/지연·스택, 제품 입력/소비자·실제 장치 적용,
  최종 이미지·실보드 수락은 미수행이다. 로컬 독립 실행 기반의 완료와 혼동하지 않는다.

## 후속: 앱별 수명과 warm 준비 연결

후속 기준 HEAD는 `d488b84cc4d7d6650b8de791998b9827c2162056`이며 같은 브랜치의
미커밋 diff·새 파일을 포함한다. 사용자가 main·Gateway·최소 Worker 소비 연결을 승인한
범위다. 최초 봉인 후보와 바이너리·시험 결과를 혼합하지 않는다. 임시 구현 순서 21.3절은
요청대로 설계서에서 제거하고 PC 작업 폴더의 WORK_PLAN/IMPLEMENTATION_SEQUENCE에 보존했다.
두 기준 문서의 나머지 설계는 바꾸지 않았다.

### 변경과 수락 근거

| 보장 | 실제 구현 | 검증 경계 |
|---|---|---|
| 결과 철회와 자원 재사용 분리 | `workers.c` 앱별 lifetime·cancel·join_poll·restart, main 연결 | 실제 kernel thread의 AUTO 정리 중 MANUAL 진행, 원 기한 만료·잔존·늦은 종료 확인·생성 실패·동일 요청 추가 생성 0 |
| 대기·활성화·진행·허가 분리 | `adapter.qualify`, main supervisor, 명시적 `warm-drive-1`, Gateway `WarmDriveMachine` | 무업무 대기 계산 0, 상태 준비/qualifier 부재 거절, 실제 현재 계산 READY 전 출력 0. 활성화/실패 보고가 다음 정기 보고를 의도적으로 기다리지 않음 |
| 앱별 신뢰 신원 | root Bootstrap `drive_apps`, 같은 receipt의 CAS·앱별 요청 기록, C decoder·context owner·불변 송신 identity | 원 execution/boot/transport·다른 앱 보존, 같은 요청 UUID 재발급/추가 Store 쓰기 0, uncertain 재발급 거절 |
| 현재 실행의 seq 보호 | 공용 client의 seq/latest 이전 root binding guard, Gateway의 수신/제출 재검증 | 처음 보는 옛 세션도 거절. 옛 세션이 현재 seq 기준을 바꿔 중복 명령을 신선하게 만드는 경로 차단. 정상 새 instance 수락 |
| 부분 미확정의 영향 제한 | root 갱신 전 앱별 신원·대상 보존, `ProductionBootstrap.refresh`와 private `app_status` | AUTO Store 미확정에서 AUTO 옛/새 신원 모두 차단, 정상 MANUAL 바인딩과 출력 근거 보존. Worker 재시작도 같은 사본을 복원하며 추가 Store 쓰기 0. 초기 공급·근거 없는 기록은 예외 승인하지 않음 |
| 큐 대기와 재연결의 신선도 | Gateway와 rejoin은 원 수신 시각으로 보고 나이를 검사하고 현재 시각으로 현재 binding을 검증 | 늦게 처리한 보고를 새 계산/준비로 인정하지 않음. disconnect 이후 과거 큐의 보고가 대기 자격을 복원하지 않으며 정상 새 보고는 수락 |
| Worker 소비 | `rejoin/mqtt/heartbeats/observe/model/watcher`의 최소 연결 | 두 앱의 현재 감독 근거 각각 요구. 정상 대기/입력 제한을 VM 사망으로 오인하지 않음. Xen crash/absence와 기존 Engine 정책·원 시도·예산·S01/S02는 유지 |
| 실제 복구 후 연결 | 기존 Engine→실제 Manager/Execution→provisioning/receipt→binding | 외부 Xen/Store만 대역. platform COMPLETED와 앱 QUALIFIED/READY_OBSERVED·출력 허가를 구분 |
| 계산 건강·원자료 보존 | 실제 선택 완료만 local health에 기록, 미관측/철회 앱은 ENODATA. 기존 capture seal·분석기·collector 유지 | 미관측 AUTO를 초기 0의 정상값으로 출력하지 않음. 공통 loop_seq는 실제 선택 완료만 증가. warm SAFE_STOP은 증가 0 |

정식 필드·시간·거절 조건은 [MQTT 6.3절](../contracts/mqtt-contract-v1.md#63-대기활성화-보고-applifecycle),
사용/교체/빌드는 [주행 README](../../platform/apps/drive/README.md)에 둔다. 감독 100ms·lease
300ms·앱 정리 500ms는 기반 시험 프로필 설정이다. 20ms release·100ms 명령 신선도·기존
1초 제어 적용 기한·재무장/카운트다운을 늘리지 않았다. root의 새 명시적 요청과 같은 요청
재전달을 구별하며, 앱 장애를 자동 restart하는 정책은 추가하지 않았다.

### 실행한 로컬 검증

PC 증거: `C:/Users/SSAFY/Documents/Codex/2026-10-29/xenova-zephyr-lifecycle-implementation/`.
WSL 후보: `/home/ssafy/xenova-zephyr-lifecycle-20261029/`. verify/python_checks의 JSON은
실제 명령·출력·종료 코드·소요시간을 기록한다. SOURCE_SEAL은 HEAD·product diff·새 파일과
개별 바이트를 식별한다. DOCUMENT_LINKS는 현재 링크와 기존 링크 오류를 구별한다.

| 실행 | 결과·근거 |
|---|---|
| Gateway 전체 | 94 passed. `python-gateway-r8.log`; 기존 verified와 새 warm·앱별 미확정·정상 MANUAL 보존, 큐 대기·재연결 이전 보고 거절 포함. 실제 runner의 50ms 큐 지연 수락과 110ms 지연 거절을 구별 |
| 공용 Python MQTT | 145 passed, 4 skipped. `python-mqtt-r3.log`; seq 전 신원 guard·정상 새 신원·중복/retained 거절 포함. skip은 기존 opt-in 외부 broker 사례 |
| dom0-manager 전체 | 557 passed, 1 기존 의존성 경고. `python-worker-r70.log`; S01/S02 저장 중단·새 프로세스 복원·lock·원 요청 회귀 포함 |
| 마지막 rejoin/부분 미확정 보완 | 영향받는 실제 Engine/Manager/Bootstrap/Watcher 경로 9 passed. `python-warm-worker-r5.log`. 보고 수신과 소비 사이의 시각 차이, 미확정 앱 갱신 중 Worker 재시작의 Store 추가 쓰기 0 포함. 전체 557 이후 이 부분만 다시 검증했으며 별도 전체 suite 완료로 합산하지 않음 |
| 플랫폼 관련 8개 suite | 90 passed. `python-platform.log`: collector·분석·binding·초기화/종료·network·v2 모델/출처·Bootstrap. 최종 Bootstrap 대상 시험 5 passed(`python-bootstrap-r3.log`)로 CLI 옵션 충돌도 확인 |
| host C | ctest 4/4. 실제 계산/준비 어댑터, 상태 준비 미완료, C policy/payload, concurrent parser와 sanitizer |
| native kernel | worker 10, warm main/context 5, 기존 main 2, I/O 29, lifecycle 9, capture boundary 1 통과. 각 suite를 다른 suite의 개수로 대신하지 않음 |
| 실제 격리 MQTT/Gateway | 최종 `warm-pair-a-r7`·`warm-pair-b-r5` 각각 14단계 통과. 같은 native 파일의 A/B 대칭 연속 시나리오와 앱별 재초기화·옛 보고 거절. 독립 v1/v2 수신과 원 message VM/boot/instance/id→serial 제출 인과 대응. root execution/Store는 명시적인 native fixture |

초기 시험 실패도 보존했다. 출처 사본의 앱 instance 누락과 모든 보고 wake에 따른 이벤트
시험 정체는 제품 코드를 수정해 해당 회귀를 다시 통과했다. 첫 pair 시험의 READY/QUALIFIED
혼용 및 AUTO/수동 준비 유지 시점 차이는 시험기를 실제 on_resume 조건에 맞춰 수정했다.
제품 준비 시간·출력 조건을 낮춰 통과시키지 않았다. 시험 실행기의 subprocess 경로와
multiprocessing 재진입 문제는 실행기를 수정하고 소유 시험 프로세스만 중단한 뒤 재실행했다.
기존 저장 결함의 재발로 분류하거나 실패한 실행을 최종 성공 근거로 사용하지 않았다.

마지막 pair 재검증은 runner가 큐의 원 수신 시각으로 현재 binding을 다시 조회해 예외가
발생하는 경로도 재현했다(`warm-pair-a-r6`·`warm-pair-b-r4`). 현재 binding은 현재 시각으로,
명령 나이는 원 수신 시각으로 검사하도록 국소 수정했다. 조회 사이 binding이 사라지면
수락하지 않는다. 이후 Gateway 94개와 A/B 각 14단계를 통과했으며, 명령 수신 시각이나
100ms 신선도를 새로 발급하지 않았다. Python 소비자만 수정해 ARM C 후보는 같고,
최종 SOURCE_SEAL은 이 후속 소비자 diff까지 포함한다.

### ARM 부분 빌드와 번들

고정 Zephyr `684c9e8f32e4373a21098559f748f06915f950c9`·기존 SDK/compiler를 재사용했다.
각 BUILD_RECEIPT의 source_files와 현재 소스, 패키지 내부 바이트·설정·ELF/DTS·스택 직접
frame을 대조했다. netfront send 48 bytes·stack sentinel을 유지한다. 최대 직접 frame은
2,512 bytes이며 8KiB 계산 stack보다 작다. 전체 call-chain/IRQ peak·실보드 high-water 보장은 아니다.

| 프로필 | WSL 하위 폴더 | bin bytes | ELF PT_LOAD memory_bytes |
|---|---|---:|---:|
| 기존 verified 운영 | `arm-operations` | 412,464 | 1,060,864 |
| warm 운영 | `arm-warm-operations-r2` | 416,688 | 1,077,248 |
| warm 실험 | `arm-warm-experiment-r2` | 416,688 | 3,633,152 |

warm 운영의 로드 메모리 차이는 verified 운영 대비 16,384 bytes이며 실험 recorder/상세
스캔 구성은 warm 운영보다 2,555,904 bytes 크다. 링크/정적 로드 범위의 차이이며 실제 상주
메모리·CPU·지터 실측이 아니다. 바이너리 SHA-256은 각각
`c6f4d8ef6c5313ef4d96285c5701ecba0762990437bace2864d91a5699621c48`,
`bf9f5972e9cdded69ce2e716fa448d21ac1856ebc797bf908e9ea23d8c548ea4`,
`adadea344fd5b345a8019b7892d64960a004543bca914d7366114a5b98dee360`이다.
FINAL_BUNDLES와 세 BUILD_RECEIPT가 전체 파일·도구·구성 해시를 보존한다.

ARM firmware bundle 부분 패키징을 수행했다. Python 공용 recipe가 패키지 디렉터리의
모든 .py를 포함하고 기존 플랫폼 recipe가 provisioning 모듈을 설치하는 경로도 확인했다.
이번 후속에서 Yocto RPM·전체 이미지·seed/SD 편입을 수행한 것은 아니다.

### 남은 수락과 요청

- 실제 Xen Store 지연·callback 중단·PV 전달·CPU/stack high-water·실보드 협조 종료와
  A/B 설치·최종 이미지 포함·장치 적용은 별도 수락이다. 새로운 보드 접속/변경은 하지 않았다.
- 상태 있는 planner·지도·maneuver·상태 사본, 건강 상태에서 다른 generation으로 대상을
  미리 준비하는 계획 인계, 실제 MANUAL 세션·입력 공급과 ESP32 적용은 미완료다.
- Worker 최소 소비 연결은 D01 건강 정책/episode 회복·앱 수습 선택과 D03 전체 수습의
  완료가 아니다. FastAPI·Spring·화면·Linux/Agent 제품 구현은 대신 수정하지 않았다.
- 나머지는 `docs/requests/20261029-request-*`에 기존 ID를 유지해 누적한다. Gateway는
  박신영 직접 구현 범위다. 큰 설계서에 AI 작업 순서를 다시 넣지 않는다.
- commit/push·MR·브랜치 전환 없이 현재 작업트리에 보존한다. 기존 산출물·공용 캐시는 유지한다.

## 후속: 요청별 사전 준비와 조건부 권위 커밋

같은 HEAD `d488b84cc4d7d6650b8de791998b9827c2162056`의 기존 미커밋 후보를 보존하고
준비/커밋 상세 계약을 추가했다. 설계의 9.6·11.2·12.2·15.4·결정 목록은 현재 의미와
지원 경계를 보강했으며 작업 순서·PC 증거·시험 수치는 이 절과 PC 폴더에 둔다.
이전 후보의 성공/실패와 봉인은 덮어쓰지 않는다.

### 변경과 계약 대응

| 조건 | 구현·판정 |
|---|---|
| 같은 요청의 반복 준비 | `planning.py`, Authority `begin_transition/finish_preparation`. 원 UUID·예상 owner/generation·대상 실행/앱·모드·의도 revision·기한을 고정. 준비/조회는 출력 generation을 발급하지 않음 |
| 원자적 한 번의 커밋 | Authority `commit_transition`의 같은 잠금·JSON 교체에서 owner/generation·모드·대상과 원 요청 결과를 함께 확정. 반복 commit은 같은 결과와 추가 출력 action 0 |
| 취소/커밋 순서 | 같은 Authority 수정 경계에서 먼저 확정한 결과 유지. 다른 준비도 커밋 시 ABANDONED. 커밋 뒤 취소는 원 COMMITTED를 지우지 않으며 이후 정지는 별도 의도 |
| 커밋 실패·효과 미확정 | 미커밋 준비 실패는 유효 A 유지. 저장 효과 미확정은 출력 차단·원 결과 미확정으로 응답하고 이전 generation 복원 금지. 이미 확정된 다른 원 요청의 결과와 구분 |
| 프로세스 중단/재시작 | 실제 자식 프로세스의 준비/커밋 직후 `os._exit(37)` 뒤 다른 프로세스에서 로드. 미커밋 승인은 ABANDONED, 커밋 기록·spec·기한·generation 유지. 이전 진행/승인은 재무장에 사용하지 않음 |
| 새 실행의 정상 수락 | 새 신원의 대기 자격은 받을 수 있지만 이전 대상에 준 출력 허가는 상속하지 않음. 새 명시적 전환으로 새 generation을 확정할 수 있으며 이전 세대로 돌아가지 않음 |
| 상태 있는 앱의 준비 | C `preparation.h`·실제 adapter: 재구축/완전 사본·원 출처·수신 문맥·고정 기한·취소·현재 호환성 검사. boolean 하나나 qualifier 없는 계산 완료를 READY로 만들지 않음 |
| MANUAL 원 입력 | C/Python `manual-session-1` parser와 전환별 latch. 같은 MANUAL 승계의 현재 누른 키 수락, AUTO→MANUAL의 현재 중립 후 새 원 action만 이동 허가. 재발행/같은 sample은 신선도·조작을 갱신하지 않음 |
| 입력 만료/교체/무효 | 현재 명령/준비가 남아도 출력 보호. 기존 SAFE_STOP/재무장으로 종결하고 입력 부재 중 generation 반복 발급 0. VM 복구 정책으로 바꾸지 않음 |
| 실행·자원 보존 | v1/v2 소유·고정 release·최신/in-flight·F01·TCP_NODELAY·부분 초기화/협조 종료 유지. 새 VM·CPU 정책·알고리즘·broker 권한 변경 없음 |

실제 Gateway 연결은 warm actor의 제한된 로컬 queue다. queue 등록과 내구 PREPARING을
구별한다. HTTP/MQTT 운영 전환 API는 만들지 않았다. 현재 stateless 앱의 지속 자격을
원 요청에 채택하는 방식이며 게스트의 요청별 ACK로 보고하지 않는다. root 설정/바인딩의
`app_state_models`는 실제 고정 firmware와 일치해야 한다. 누락/stateful 선언을 일반
QUALIFIED로 우회하지 않는다. 상태 있는 앱은 로컬 검증 연결부를 제공했지만 실제 사본
전송·live 참조 교환·요청별 적용 보고는 후속 앱/생산자 구현이다.

Authority는 원 경로의 JSON에 최대 32개 전환·전체 64KiB를 보관하며 자동 삭제하지 않는다.
한도에서는 새 계획 요청을 거절하고 원 결과/정상 A를 유지한다. 원본 삭제나 초기화로
수습하는 CLI는 제공하지 않는다. 필수 fsync는 actor 비용이며 queue/tick 값만으로 최악
지연을 보장하지 않는다. 관측 cache guard와 외부 root/Xen 변경 전체가 원자적이라는
주장도 하지 않는다. MQTT/게스트/장치까지의 분산 원자성은 지원하지 않는다.

### 실행한 검증과 근거

PC: `C:/Users/SSAFY/Documents/Codex/2026-10-29/xenova-drive-transition-contract/`.
WSL: `/home/ssafy/xenova-drive-transition-20261029/`. verify/python_checks JSON에 실제
argv·rc·소요시간을 보관한다. 입력은 통제된 fixture이고 실제 C 계산·thread·Gateway·
MQTT·serial encoder를 사용한다. root 실행/Store 및 브라우저 입력 생산과 실제 장치는
각각 명시적인 대체 경계다.

| 검증 | 결과·연결 |
|---|---|
| Gateway 전체 | 122 passed, `python-gateway-r9.log`. 원 요청 재전달·취소/커밋 실제 thread 경합·저장 효과 미확정·실제 프로세스 종료/재시작·입력 만료/교체/무효·새 대상 거절/정상 수락 |
| 공용 Python MQTT | 149 passed, 4 기존 외부 broker opt-in skipped, `python-mqtt-r3.log`. C와 같은 12개 정규 벡터, 중립 latch·반복/역전·원 나이·같은 MANUAL 입력 |
| 플랫폼 관련 8개 suite | 91 passed, `python-platform.log`. 원자료 collector/분석·binding·Bootstrap·초기화/종료·네트워크·v2 계약/출처 |
| 실제 Engine/Manager/rejoin | 9 passed, 기존 의존성 경고 1, `python-warm-worker.log`. 추가 root 상태 모델 전달도 실제 provisioning/receipt 경로에서 확인. Engine 정책 변경 없음 |
| host C | CTest 5/5, `host-test-r4.log`. 실제 adapter·C/Python 벡터·상태 준비/취소·세션 입력·같은 세대의 정책 하향 거절·기존 concurrent parser/sanitizer |
| native warm main/context | 5/5, `warm-main-*-r3`. 원 release·계산/대기·AUTO 정리 중 MANUAL·잔존 재사용 금지·context owner 종료 |
| 실제 격리 pair, keys-only | A/B 각 28단계, `warm-pair-a-r2`, `warm-pair-b-r2`. 원 14단계에 계획 인계·중복 커밋·준비 중 기존 owner의 실제 출력 진행 확인. 마지막 stateful-only 계산 guard 전 C 후보이며 해당 guard는 host/세션 pair의 최종 후보로 보완 |
| 실제 격리 pair, 세션 입력 | A/B 각 42단계, `warm-session-pair-a-r2`, `warm-session-pair-b-r2`. 최종 동일 native 파일에서 계획 전환·중립 이전 0 출력·새 조작 뒤 이동·동일 MANUAL 승계의 키 재조작 0·기존 장애/재합류를 확인 |

최종 session pair 파일 SHA-256은
`b735e0a123093976daad8347d5d3c1ff25dbfbceb6b34016081767f1ce9bdd39`다.
생산자 fixture는 자기 통제 입력 상태를 실제로 확인하지만 제품 WS 세션을 입증하지 않는다.
시리얼 제출은 원 VM/실행/앱/메시지와 대조하며 실제 ESP32 적용은 아니다.
이전 전체 dom0-manager 557 통과는 선행 근거로 유지한다. 이번 변경에 전체 557을 다시
통과했다고 쓰지 않고 영향받는 9개 경로를 연결한다. I/O 29·lifecycle 9·capture/회수의
직접 변경 없는 보호는 앞선 봉인 근거와 이번 관련 suite를 재사용한다.

실패/중간 후보도 보존했다. C 시험 include 경로와 signedness 경고는 시험/형식을 수정했고,
준비 근거가 있어도 qualifier 부재가 실제 계산 READY를 만들 수 있는 경로는 adapter를
보완했다. 세션 pair 첫 실패는 추가 mode 전환 후 새 generation의 Standby 유지 시간을
기다리지 않은 시험 순서였으며 제품의 3초·100ms·1초를 늘리거나 낮추지 않았다.
입력 상실 시 기존 대기 자격만으로 SAFE_STOP→MANUAL을 반복 선택하는 경계도 별도
회귀로 막았다. 이 결과를 성능 개선이나 실제 차량 연속 운행으로 해석하지 않는다.

### ARM·패키지와 남은 수락

고정 Zephyr/SDK는 앞선 후보와 같다. `arm-operations-r3`, `arm-warm-operations-r3`,
`arm-warm-experiment-r3`의 BUILD_RECEIPT 입력·ELF/DTS/config·실제 번들 바이트를 현재 C
소스와 대조했다. bin 크기는 각각 412,464 / 420,784 / 420,784 bytes,
ELF PT_LOAD 메모리는 1,060,864 / 1,081,344 / 3,637,248 bytes다.
warm 운영의 앞선 봉인 후보 대비 정적 load 차이는 +4,096 bytes이며 실험 recorder 차이는
2,555,904 bytes를 유지한다. 최대 compiler 직접 frame 2,512 bytes·계산 stack 8KiB·
netfront send 48 bytes를 확인했다. 전체 call-chain·IRQ peak·실제 상주량/CPU·지터는 미수락이다.

Gateway/common MQTT/dom0-manager의 기존 setuptools wheel도 소스 사본에서 의존성 설치
없이 빌드해 13/11/48개 Python 모듈의 실제 archive 바이트와 원본을 대조했다.
PYTHON_PACKAGES·FINAL_BUNDLES·BUILD_RECEIPT·SOURCE_SEAL이 경로와 해시를 연결한다.
이는 Yocto RPM·Gateway 이미지 탑재·SD 설치 완료가 아니다. 패키지/배포 수락을 혼동하지 않는다.

Spring/UI의 실제 세션 확인·모드 독립 입력 공급, 상태 있는 planner/상태 전송·장치 ACK,
최종 이미지·실제 Xen/실보드·연속 운행/장시간 시험은 후속이다. 담당자 요청서에 구체
필드·수정 위치·수락 조건을 누적했으며 대신 제품 코드를 수정하지 않았다.
추가 의존성·실보드·VM/서비스/CPU/네트워크·전체 이미지·commit/push는 수행하지 않았다.
