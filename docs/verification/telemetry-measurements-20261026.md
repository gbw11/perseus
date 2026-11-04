# Telemetry 실제 계측·출처·지원 계약 검증

## 공통 네트워크 지표 발행 경로 — 2026-11-03

기준은 `feat/platform/zephyr-network-telemetry`의 `05aa95c` 위 플랫폼 변경이다.
같은 Diagnostics 캐시와 MQTT 작업자에서 [v1 MQTT-03](../contracts/mqtt-contract-v1.md#31-zephyr의-공통-네트워크-지표-mqtt-03)으로
RX/TX 누적값을, [v2 2.1](../../platform/guests/zephyr/telemetry/contract/CONTRACT.md)로 수신률과
상세 자원 지표를 보낸다. VM마다 ring 세대와 관측 단조 시각을 보존한다.

| 검증 | 결과 |
|---|---|
| 제품 C 인코더·계약 | `python3 -B -m unittest discover -s platform/tests -p test_xenova_telemetry.py -v` 15개 통과. ASan/UBSan, uint64 상한, UTC 유무, ring 초기화, 만료·읽기 실패, v2에 RX/TX 누적값이 없는지 확인 |
| 계정·ACL | 격리 Linux에서 `test_xenova*broker*.py` 30개 통과. Telemetry 계정의 자기 VM v1 `/metrics` 권한과 기존 비밀번호 보존을 검사. `render-policy.py --check` 통과 |
| Zephyr native 진단 | `native_sim/native/64`와 `resources-network.conf`로 8개 통과. 바이트 변화·유효한 0·ring 세대 변경·읽기 실패 후 복귀를 확인 |
| 격리 MQTT 왕복 | `run_native_acceptance.py --execute --extended`로 실제 Zephyr 발행기와 Mosquitto 2.0.18 사용. VM 2대의 v1 표본 8개와 각 VM의 ring 세대 2개를 캐시와 대조. v2 표본 296개 수신, 교차 VM 발행 및 같은 VM heartbeat 발행 차단. Xen ring 입력은 native 시험 provider로 대체 |
| ARM64 | Zephyr 4.4.0 고정 도구로 `xenvm` 주행앱과 `warm-lattice-lab.conf` 빌드 성공. 문서 정합화 후 빌드 ID를 갱신한 `zephyr.bin` SHA-256 `44d3b05eb2a02a298c26e40ae004a40c5287b99438e6630f13abf1f21ece967f` |

Spring의 v1 저장과 화면, 실보드 설치·수신은 후속 수락이다. 기존 보드의 ACL 반영 절차는
[브로커 안내](../../platform/deploy/mosquitto/README.md#기존-계정과-업데이트)를 따른다.
아래 10월 기록의 지표 범위와 검증 결과는 각 당시 후보에 적용된다.

## 로컬 마감 보완 — 2026-10-27

기준은 Telemetry 5개 커밋이 포함된 `da9221f`, 브랜치는
`feat/platform/zephyr-telemetry-measurements`다. 아래 결과가 최종 후보에 적용된다.
기존 결과와 실패 기록은 뒤의 초기 후보 기록 및 PC 증거에 보존했다.
새 기능·MQTT stream·공개 지표·제품 Worker/Gateway 코드는 추가하거나 변경하지 않았다.

### 확인된 결함과 최소 수정

| 경계 | 확인한 사실·수정 | 실제 코드·시험 |
|---|---|---|
| 콘솔 부가 기록 | 기존 `buffer_append()` 안의 동기 file I/O가 같은 event loop의 PTY/다른 콘솔 처리를 지연시킬 수 있었다. 파일 open·fstat·회전·write를 작업자 하나로 분리 | `xenova_console_evidence_async.h`, Xen patch, `test_xenova_console_async.py` |
| 생산자 대기 | 최대64개 immutable 사본, `pthread_mutex_trylock`, nonblocking eventfd. 큐 공간·작업자 복구·drain/join을 기다리지 않으며 직접 파일 기록 fallback 없음 | 실제 patch를 적용한 pinned Xen의 `buffer_append`·`handle_tty_write`를 컴파일. 외부 PV ring/event channel만 대역, 실제 openpty 사용 |
| 원문과 귀속 | 버리는 것은 부가 사본. 두 PTY 원문 바이트는 유지. 작업자가 큐 잠금을 잡은 경우·I/O 정체·실패·종료·시작 불가 각 경우 통과. 작업자 종료 뒤에도 PTY 전달 확인 | 위 시험 5가지 조건 × queue/실제 Xen PTY 두 경로, ASan/UBSan·FORTIFY |
| 실행 교체 | 사본이 원래 BIND를 소유. BIND가 없는 사건은 저장하지 않음. 늦게 생성된 파일은 pending cursor로 수집하되 receipt와 일치한 BIND 이후만 귀속 | `test_async_creation_and_delayed_old_binding_never_relabel_new_execution`: old/new/old/new 자료 중 새 실행 사건 2·4만 저장 |
| 자원 신선도 | 새 collection이 오래된 CPU/pool cache의 수명을 연장할 수 있는 결함을 production C에서 재현. CPU window end·pool observed uptime에 2초 검사를 추가 | `model.c`, worker expiry, `emit.c`. 만료 거절 시 stream seq 불변. 미지원 CPU의 관측 시각도 실제 cache 시각으로 기록 |

작업자 stack은64KiB, 큐64개, FD cache32개다. 파일당256KiB 회전과 기존 archive
보존 정책을 유지한다. 큐 수락은 내구 저장이 아니며, 작업자 실패·daemon 종료·포화 때
남은 사본의 보존을 보장하지 않는다. source loss는 기존 `not_quantified` 경계다.
물리 PV ring 전달·PTY 성능/IRQ 지연·전원 상실 보존은 이 로컬 시험의 수락 대상이 아니다.

### CPU·UTC·지원 선언 대응

| 조건 | 근거와 결과 |
|---|---|
| 긴 CPU 공백 이후 복귀 | 실제 native Zephyr system workqueue를2,147,983ms의 가상 시간 동안 중단. cache stale → `counter_discontinuity` → 그 표본으로 기준 재확보 → 다음 두 정상 window·thread 값 복귀. `diag-net-run.log`, `test_long_gap_baseline_and_two_normal_windows` |
| registry/반환 한계 | 실제70개 thread 추가, 전체76개 관측·반환32·`-ENOSPC`, 반환 ID 중복 없음. 종료 뒤 registry 정상 복귀. `test_registry_and_return_limits_are_explicit` |
| CPU 페이지 | production C32행/8parts·seen73/truncated. sample·collection·window 일치, 누락 마지막 part를 완료로 보지 않음, lease 만료, 다른 window 충돌 거절. `test_xenova_telemetry.py` |
| 실제 Publisher | 독립 broker 수신310개, source73개·CPU44개 대조. CPU 페이지25개 group 모두 같은 표본·구간으로 완전 수신. 실제 transport에서의 부분 수신은 이번 run에 없으며 누락·만료는 위 production C/consumer 시험 근거 |
| UTC | 정식 정의는 [계약 §6.1](../../platform/guests/zephyr/telemetry/contract/CONTRACT.md#61-utc-품질의-인정갱신만료) 한곳. 실제 clock.c의 점프 거절·만료·재획득·uncertainty 올림, host adjtimex 정상/UNSYNC/CLOCKERR/TIME_ERROR 외부 경계 시험 |
| persistent_crash | 지원 구성 확인과 개별 저장 성공을 분리. binary profile·timer·root 전용 경로·receipt 확인, 파일 fsync 실패 시 cursor 미진행. 지원 선언을 모든 fatal 포착이나 내구 성공으로 해석하지 않음 |

가상 시간은 native_sim의 고정1MHz clock 그대로 빠르게 진행했다. timer frequency를
바꾸거나 실제 Xen 시간을 모사하지 않았다. 실제 Xen 계상과 architectural counter
진행률·시각 공급·IRQ 제외 지연은 기존 후속 실보드 수락 범위다.

### 최종 후보의 독립 회귀 근거

| 실제 실행한 suite | 결과·범위 |
|---|---|
| production C MQTT / drive controller | ctest 각각1/2개 통과. 계산·준비 어댑터와 공용 계약 |
| 실제 Zephyr v1 통신 owner |27조건 통과: 진행·readiness·F01 창/heartbeat·재연결·만료·in-flight·시작/종료 경합·join. `owner-*.json/log` |
| 실제 Zephyr 수명주기 |9조건 통과: 정상, 정상 I/O, 공통 deadline, shutdown/network/loop/Publisher 실패, 정리 실패, 초기화/종료 경합. `lifecycle-*.json/log` |
| 실제 두 Zephyr + MQTT + Gateway | 동일 native ELF로 A 시작·B 시작 각각13단계 통과. AUTO↔MANUAL, 반대 VM 승계, 새 실행 Standby 재합류, 재승계. 실제 장치 대신 mock serial이며 Recovery Engine 종단은 아래 별도 suite |
| MQTT Python / Gateway / dom0-manager |126통과·외부 broker4 skip /84통과 /380통과. Manager suite는 실제 Engine→Manager/Execution→provision/receipt→실행 binding→준비 연결을 포함. Xen/Store와 시험 경로만 외부 경계 |
| 전체 platform |912개, 실패0·기존 비root UID 검사 skip1,357.712초. 별도 suite 결과를 이 개수로 대체하지 않음 |
| 부분 패키징 | `bitbake -c package_qa xenova-platform xen-tools`:3071 tasks,3055 cached, 모두 통과. 기존 경고2개. ARM64 xenconsoled 및 설치 module 바이트 일치 확인 |

시험 경로 보완: Recovery fixture가 실제 `/run`을 읽던 부분을 해당 Manager의 임시
실행 기록으로 연결했다. pair 독립 수신기가2.1을 v1 parser로 보내던 조건은 v2 토픽
기준으로 정식 Telemetry validator에 연결했다. 제품 상위 코드는 바꾸지 않았다.
최초 잘못된 시험 인자·fixture 실패·Xen Werror 실패 로그도 삭제하지 않았다.

### ARM·패키지·봉인

고정 Zephyr `684c9e8f32e4373a21098559f748f06915f950c9`, 기존 SDK/GCC14.3 입력을 유지했다.
아래5종의 ELF/DTS·16MiB UP 프로필·bundle·전체 source receipt를 현재 소스와 대조했다.
drive/autonomous/manual의 direct netfront frame은48bytes·stack sentinel 유지다.

| 후보 | zephyr.bin SHA-256 | bytes |
|---|---|---:|
| drive | `45212e2af97660b4eeb9ccc9fec6bd022ab7107946fb4e77c949da03fce87845` | 404232 |
| autonomous | `33100c7b4c676e45e8da38350805af4a457a0ae992f7e0c4957a5211ead26add` | 404240 |
| manual | `e796827bf7904ee86ab487aa73620a447bc032ca7236e93a4e24d9bb288a41ed` | 400144 |
| starter | `38e8e0380e0f0e10b15b09fe81b64ca9a7429ceee1a29b76fcd67310c211cc99` | 367136 |
| starter-off | `418cfda6f5d97550abc5c15fbe29d79846a544ba6b9209ff31de9369d6932982` | 292816 |

Xenconsoled ELF SHA-256은 `6de9ba98cdc47c94274c11597b8b1aaa7b81bd6e35326ae0191e60d24eced002`이다.
`-pthread`로 DT_NEEDED에 `libpthread.so.0`이 추가되지만 기존 `glibc` package가 제공하며,
새 runtime package/외부 layer/lock 변경은 없다. 실제 pkgdata·RDEPENDS를
`FINAL_ARTIFACTS.json`에 기록했다. 기본 PV/PTY 소유자와 종료 unit은 유지했다.

최종 PC 증거 위치는
`C:/Users/SSAFY/Documents/Codex/2026-10-27/xenova-telemetry-closure/`다.
`ACCEPTANCE.json`, `FINAL_ARTIFACTS.json`, `SOURCE_MANIFEST_SEALED.json`,
`final-source-changes-sealed.zip`, `evidence-sealed.zip`이 source·산출물·개별 suite를 연결한다.
최종 archive는 공개 로그·결과만 포함하고 broker 인증정보/Bootstrap 파일은 포함하지 않는다.
이 변경으로 보드 접속·배포·VM/서비스 제어·이미지/SD 작업은 하지 않았다.

## 초기 후보 기록

### 기준과 판정

기준 HEAD는 `a2e61555b5218e8e98b3a499068f36d4c31eac52`, 작업 브랜치는
`feat/platform/drive-recovery-integration`이다. 기존 주행·준비·권한·복구 구현을 유지하고
미커밋 Telemetry 변경을 별도 후보로 검증했다. commit/push는 수행하지 않았다.

구현·로컬 시험·native 통합·ARM 후보·부분 패키지까지 확인했다.
전체 이미지 편입, 실제 Xen/보드, 제품 Spring/DB/화면 종단은 미수락이다.
native 시간 계측으로 실제 ARM 비용·주행 지터 효과를 수락하지 않는다.

정식 사용법은 [Publisher](../../platform/guests/zephyr/telemetry/README.md),
[Diagnostics](../../platform/guests/zephyr/diagnostics/README.md),
[2.1 계약](../../platform/guests/zephyr/telemetry/contract/CONTRACT.md)을 따른다.

## 변경 책임

| 묶음 | 파일과 동작 |
|---|---|
| 자원 계측 | diagnostics의 resources.h/resources_core.c/resources.c, Kconfig와 shell. 단일 200ms cache, UP CPU·thread lifetime·고정 RX/TX data pool |
| 직렬화·현재 상태 | telemetry model/adapter/worker/clock, schema/catalog/validator/fixture. 소수 JSON number, CPU 표본 페이지, memory region·현재 degraded/복귀 |
| 출처 | build_identity.py·CMake·build.c, firmware packager·Execution·bootstrap/provisioner. 입력 ID와 로딩 bin hash, 실행별 이름·UTC 주입 |
| 내부 네트워크 | netfront_ring/netfront와 앱 provider. 별도 64비트 byte·counter epoch, 비대기 조회와 실제 구간 rate. 새 MQTT byte/rate 지표 없음 |
| 보존 | xenconsoled 필터·bounded scratch writer, telemetry support/evidence 모듈·timer·recipe. 단일 PV reader와 기존 PTY 전달 유지 |
| 문서 | 현재 2.1 원문, 2.0 원문·체크섬 보존, CLI/빌드 안내와 이 검증 보고서 |

`execution_context.loaded_artifact`는 새 Zephyr 실행의 선택 기록이다. 로딩한
zephyr.bin·manifest·build ID와 당시 이름을 결합한다. 기존 Manager action/envelope,
실행 식별과 recovery completed의 의미는 바꾸지 않는다. 기존 실행의 빠진 receipt를
현재 디스크로 채우지 않는다. 이름 변경은 다음 실행별 주입 시점에 반영한다.

## 요구와 실제 시험 대응

| 요구 | 실행한 시험·근거 | 결과·경계 |
|---|---|---|
| 소수·큰 정수·null·비정상 수 | test_xenova_telemetry.py의 production C emit + ASan/UBSan; 문자열 percent·NaN/Infinity·범위·unknown field 음수 변형 | 통과. u64/i64 문자열과 0.001 percent half-up, clamp 없음 |
| CPU 계산·첫 표본·분모 | resources_core의 알려진 total/busy와 큰 분모, 첫 표본·0 분모·긴 공백 | 통과. guest-accounted UP 비율이며 Xen pCPU 사용률 아님 |
| 실제 CPU·thread 수명 | native diagnostics의 test_runtime_source_and_lifetime_reuse | 통과. idle 구간 busy=0, 통제 busy thread peak 23.810%, 같은 TCB/이름의 ID 12→13. 보장 상한 아님 |
| 표본 소유·공통 구간 | 동일 cache 연속 조회, 실제 CPU API flush 후 IRQ 제외 안의 monitor 열거, 원자료와 수신 number 독립 재계산 | 통과. reader/Publisher 재시작이 기준점을 재발급하지 않음 |
| CPU 연속성 | 반주기 제한·reset/역전의 portable 경계, native Publisher 재시작/새 guest boot | 로컬 확인. Xen descheduling·실제 counter wrap/장시간 정지는 실보드 미검증 |
| heap·pool | 실제 k_malloc/free, test_fixed_pool_actual_allocate_release | 통과. net_buf 점유 1 block 증감과 used+free=capacity. 전체 RAM 합산 없음 |
| stack | 기존 native 상세/미요청 시험, platform Diagnostics 회귀 | 통과. 200ms CPU collector는 stack scan을 부르지 않음 |
| 이름·build ID | UTF-8/제어 문자/길이·JSON escaping, 실제 native hostname/inventory; 격리 입력 동일/dirty/flags 변경 시험 | 통과. 동일 입력 ID 유지, 미커밋 소스·flags 변경 시 ID 변경 |
| 실행 산출물 | 기존 bundle·manifest/신원 회귀, source metadata의 실행 불일치·잘못된 hash·로딩 뒤 디스크 교체 시험 | 통과. 현재 실행의 loader record 사용. 새 ARM ELF/bin/hash 별도 검증 |
| UTC | 실제 clock.c의 정상·RTT/불확실성·만료·역전·시각 점프·재획득; host response context 검사 | 로컬 통과. 실제 dom0 adjtimex/ARM clock→Store→guest 공급은 보드 미검증 |
| capability/현재 상태 | CPU/pool off native 빌드·실수신 unsupported/null, 누계 실패→정상 복귀·stopping C 시험 | 통과. 기능 지원과 표본·Publisher 상태 구분 |
| 계약 이행 | 2.0 Golden 30 정상/30 거절/11 sequence, 별도 2.1 C fixture 4개, closed validator | 통과. 구형 소비자의 2.1 수락은 별도 구현 필요 |
| 실제 MQTT·재시작 | 실제 Core/Zephyr MQTT→격리 Mosquitto→독립 subscriber, 인증·ACL·retained·broker 단절·Publisher/guest process 교체 | 통과. 실제 netfront/Xen reboot 수락으로 확대하지 않음 |
| 네트워크 내부 | production ring byte 누적·epoch/reset/포화, native byte rate/실패/복귀 시험 | 통과. TX ring 수락·RX stack 수락의 frame bytes. 상대 수신 완료가 아님 |
| 오류/fatal | 실제 native k_oops 기본 fatal→production 콘솔 필터→영구 archive→다시 조회 | 통과. PV ring 전송·실보드 fatal·전원 상실 보존은 별도 |
| 보존 실패·회전 | test_xenova_telemetry_sources.py 11개: partial·fsync 실패·용량 회전·중단 계획 재개·동일 inode 재성장 | 통과. 삭제 요약과 미수집 불연속을 분리 |
| 비용·회귀 | 같은 12초 native 입력의 CPU/pool on/off 2 VM씩, 기존 내부 비용; 전체 platform 908개 | 로컬 기능·정적 증분 확인. native 시계 한계로 실제 실행 비용·주행 지터 효과는 미수락 |

CPU의 IRQ 시간은 pinned runtime statistics의 current thread 계정에 들어갈 수 있다.
직접 원시 차분은 32비트여서 반 wrap 이상 관측 공백을 무효 처리한다. 실제 Xen 정지 중
architecture counter 진행·귀속은 보드에서 확인해야 한다. thread registry 64/반환32,
CPU 페이지당 thread4, cache stale1s이며 SMP 합산을 지원한다고 선언하지 않는다.

## 실행 결과와 고정 도구

```sh
python3 -B -m unittest discover -s platform/tests -v
python3 -B -m unittest discover -s platform/tests -p test_xenova_telemetry.py -v
python3 -B -m unittest discover -s platform/tests -p test_xenova_telemetry_sources.py -v
bitbake -c package_qa xenova-platform xen-tools
```

전체 platform: 908개, 약5분, 실패0, 기존 skip1. skip은 비root 실행의 실제
consumer UID 검사이며 이 Telemetry 변경으로 추가한 skip이 아니다.
출처·회전 시험 11개와 Telemetry 14개도 각각 통과했다.
추가로 production C 비율 계산을 seed42의 10,000개 u64 분자/분모와 Python 정확 정수
half-up 식으로 대조했고 모두 일치했다. 문서 링크·anchor227개, 현재 계약 체크섬96개,
기존 bringup/최초 등록 unit 검사와 실제 Python wrapper `--help`도 통과했다.
초기 전체 회귀의 unit 설치 검사 오류는 timer/명시 install 문을 검사하도록 보완했다.
다음 실행의 로그 출력 디렉터리 미생성 오류는 시험 환경 준비로 수정했다.
실패 로그를 성공 로그로 덮어쓰지 않고 보존했다.

고정 도구는 Zephyr4.4 commit `684c9e8f32e4373a21098559f748f06915f950c9`,
SDK1.0/GCC14.3, 기존 추출 Mosquitto2.0.18·jsonschema·systemd259 검사 도구다.
새 target runtime 패키지나 외부 레이어·버전 변경은 없다.
격리 broker는 새 network/mount namespace에서만 실행하며 실제 네트워크/보드를 사용하지 않았다.

최종 native 통합은 310개 수신, 원천 값 대조 73건, CPU 원자료 독립 재계산 44건을 통과했다.

## ARM 후보와 부분 패키지

| 구성 | bin bytes | linker RAM | zephyr.bin SHA-256 |
|---|---:|---|---|
| drive | 404,232 | RAM:       3476 KB        16 MB     21.22% | `a2169922e77f27db19f284a067935e8f825684a295e14f50bc20a4dadfb8c81b` |
| autonomous | 404,240 | RAM:       3472 KB        16 MB     21.19% | `9ca7fdc3f92aa33ecad89a8d73e529a13a5b8404e6343fd482b5f1b2d943feac` |
| manual | 400,144 | RAM:       3468 KB        16 MB     21.17% | `595b74486ec634ad7bc610d2f87ce40e2f8105e0d8f0c84e61dc6f636487e499` |
| starter | 367,136 | RAM:        892 KB        16 MB      5.44% | `5a9fff7ced936685bd9139dbe24e2e44e820c2043cb9e9fc634e2e815178d0c7` |
| starter-off | 292,816 | RAM:        736 KB        16 MB      4.49% | `418cfda6f5d97550abc5c15fbe29d79846a544ba6b9209ff31de9369d6932982` |

활성 후보 모두 실제 config의 CPU/pool·64bit timer·tracing-user, UP/GICv2·16MiB ELF/DTS를
확인했다. 공통 drive의 최종 번들 하나를 A/B에 동일하게 사용하는 준비물이며, 기존
autonomous/manual은 지원 빌드 회귀용이다. 게시·factory 선택·이미지 조립은 하지 않았다.
기존 주행 netfront send 직접 frame 48bytes와 stack sentinel도 유지된다.
Starter off는 Diagnostics/Publisher가 모두 off이고 timing-exercise가 on인 지원 확인용이다.
892→736KiB 차이를 CPU 기능만의 절감으로 해석하지 않는다.

부분 QA: 3,071 tasks 중 3,060 cached, 전체 성공. 기존 sstate/hash-equivalence 경고와
kernel I/O 경고 2건은 남았으며 이 작업에서 빌드 환경을 임의 변경하지 않았다.
실제 packages-split의 support/evidence 모듈·wrapper·service/timer·xenconsoled drop-in을
확인했다. 설치 모듈 bytes가 최종 소스와 같고 postinstall의 두 timer enable 항목이 있다.
실제 xenconsoled는 AArch64 ELF이며 필터/회전 marker를 포함한다.
서비스 실행·콘솔 전달과 host 재부팅 수락은 설치 환경 시험으로 남긴다.

## 비용과 보존 한계

같은 native 앱·12초 실행·6초 후 계산 stall·1회 상세 snapshot·broker 설정으로
CPU/pool on/off를 각각 A/B 두 VM에서 1회 비교했다. 각 실행의 resource sample57,
주기 jitter 발췌30개, 발췌 최대/중앙값0µs였다. 수집/직렬화 비용도 native 가상 시계에서
0으로 나와 실제 0비용이라는 결론을 낼 수 없다. 송신 경과 합330,000µs도 pure CPU가 아니다.
전체 loop를 회수한 실제 ARM 지터 또는 성능 개선 결과로 사용하지 않는다.
동일 native ELF의 on-off 증분은 text4,794/data216/BSS5,682bytes였다. heap peak·실제
최대 stack과 IRQ 제외 구간의 실제 지연은 별도 실보드 계측이 필요하다.

영구 archive는 전체64MiB/4,096개, 실행별128개를 넘기기 전에 오래된 정상 기록을
삭제한다. 계획 선저장·hash 재확인·parent fsync와 최근128개 요약을 사용하며, 중단 후
삭제 확인을 잃으면 absent_after_plan으로 남긴다. 과대 신규 기록 때문에 기존 이력을
미리 지우지 않는다. 손상/작성 중 파일이나 영역 밖 파일은 삭제하지 않아 공간 확보가
불가능할 때는 여전히 저장 실패를 표시한다. 다른 사용자 데이터는 정리 대상이 아니다.

필터 임시 log는256KiB 회전 세대·현재 binding을 남긴다. 수집자는 동일 inode가 다시
커져도 rotation_gap을 기록하며 미회수 사건 수를 추측하지 않는다. 일반 PV/UART 원문은
삭제 대상이 아니다. bootstrap 전 fatal, 수집자 재시작·빠른 실행 교체·ring/line 손실,
host 전원 상실 전 미저장 구간의 포착은 보장하지 않는다. persistent_crash=available은
정의한 제한된 외부 수집 구성이 확인됐다는 뜻이며 매 사건의 저장 성공이 아니다.

## 실패에서 수정한 사항

- CPU Kconfig의 timer/tracing 순환과 tracing choice 강제 선택을 고정 Zephyr 규칙에 맞췄다.
- ARM minimal libc에 없는 ESTALE 대신 기존 EOVERFLOW 불연속 의미를 사용했다.
- 실제 hostname setter가 동작하도록 NET_HOSTNAME_DYNAMIC을 선택했다.
- Network Starter의 평탄 snapshot이 build ID 입력 경로를 깨뜨린 오류를 repository 상대
  구조·고정 tool lock 복사로 수정했다. tool/version/factory lock 자체는 바꾸지 않았다.
- xen-tools의 Python do_patch에 shell을 붙인 오류와 함수 선언 순서를 실제 부분 빌드로 수정했다.
- 임시 회전 시험의 입력이 필터 출력 형식이었던 시험 도구 오류를 실제 XDIAG_EVENT 입력으로 수정했다.
- 표시명은 기존 Manager의 63 Unicode 문자를 유지하도록 최대252 UTF-8 bytes(+NUL)로
  맞췄다. 63개의 4바이트 문자도 실제 C Bootstrap/Publisher/독립 수신을 통과했고,
  64문자는 입력 단계에서 거절한다. 이름·UUID·세션을 재발급하지 않는다.

## 후속 수락

1. 새 후보 배포·CPU/기본 VM 변경 없이 진행할 보드 대상·원복·보호 목록을 먼저 확정한다.
2. 실제 Xen 계상·counter 주파수/긴 공백·netfront bytes·시각 대응·host 품질 만료를 확인한다.
3. 실제 fatal/일반 오류·collector 단절·guest 재시작 뒤 과거 실행 조회를 확인한다.
4. host 재부팅/전원 상실·공간 압박 보존 시험은 영향과 수락 조건을 별도로 승인받는다.
5. 전체 이미지 포함·제품 소비자의 2.1 schema/catalog·소수/window/lifetime/UTC 저장·조회·화면을 따로 수락한다.

원자료·실행 명령·실패 로그·후보 영수증은 PC의
`C:/Users/SSAFY/Documents/Codex/2026-10-26/xenova-telemetry-measurements/`와 WSL의
`/home/ssafy/xenova-telemetry-measurements-20261026/`에 보존했다. 공개 요약은 ACCEPTANCE.json,
최종 입력 목록은 SOURCE_MANIFEST_SEALED.json을 사용한다. 인증정보 디렉터리는 별도 private 경로이며
보고서·Git에 포함하지 않는다. 실보드 접속·설치, 전체 이미지/SD, commit/push는 수행하지 않았다.
