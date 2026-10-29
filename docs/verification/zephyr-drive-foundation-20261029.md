# Zephyr 계산 독립 실행·원자료 회수·운영 구성 검증

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
