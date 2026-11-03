# Zephyr 계산·통신·Store 대기 분리 검증

## 판정과 기준선

**기존 기준선/A/B/통합의 실제 Xen 비교는 각 1회 완료했고 기능·자료 정합성을 통과했다.**
각 구성에서 두 앱의 30초 준비·180초 창과 전체 계산/송신 원자료를 회수했다.
통합 후보의 격리 broker 단절·복귀와 협조형 guest reboot 후 새 실행도 확인했다.
시험 VM·broker·임시 인증정보·시험 폴더를 정리하고 기본 VM·CPU 정책·관리 연결을 보존했다.
실보드 사용을 종료한 뒤 사용자가 다른 작업자에게 환경을 인계했다.

수신 간격의 뭉침은 남아 있어 **종단 전달 성능 개선 완료로 판정하지 않는다.**
후속 오류 주입에서 추가한 F01 수정은 기존 실보드 후보와 별도로 식별했다.
F01은 로컬 실제 kernel/앱 시험·전체 회귀·ARM64 부분 빌드를 통과했으나 실보드에는 적용하지 않았다.
이후 v1 client의 `TCP_NODELAY=1`을 별도 커밋 `0af6c37`로 반영하고 로컬 socket 시험을
통과했다. 이 변경의 실보드 송수신·성능 효과는 아직 검증하지 않았다.
이동 전 실패와 이후 시험 도구 실패도 아래 이력과 원자료에 보존한다.

기준선은 `feat/platform/runtime-lifecycle-diagnostics@33eede5ef23a533c642e2fdee45af600ffe62615`이다.
이전 검토 커밋으로 돌아가지 않았다. Tailscale·부팅·Manager·복구·CPU 정책 변경은 보존했다.
구성 A는 패킷–Store 분리, B는 계산–v1 통신 분리다. 비교용 펌웨어는 기준선/A/B/통합
각 자율·수동 앱 2개이며 알고리즘 소스·Zephyr/SDK·RAM/GIC/vCPU 프로필은 동일하다.

## 확인한 대기와 수정

| 경계 | 기준선 사실 | 현재 구조 |
|---|---|---|
| 계산 → Bootstrap/MQTT | main이 500ms Bootstrap 준비, bounded v1 송수신·poll을 직접 수행 | 기존 계산 main과 별도 v1 owner. 교환 잠금 안에서는 사본 교환만 수행 |
| 패킷 → Store | 패킷 worker가 250ms마다 `backend_state(50)` 호출 | 상태 observer만 기존 공유 Store/watch를 기다림. 패킷 worker는 Store API를 호출하지 않음 |
| backend 관측 실패 | 관측 실패/종료 때 link down | 관측 불가·종료·신원/프로토콜 오류를 구분하되 전송 차단은 유지. 새로운 유예 없음 |
| 링 처리 | 총 64건 한도, 응답·ID·flags·메모리 게시 검증 | 한도·검증 유지. 남은 응답이 있으면 일반 10ms idle 대신 한 tick 뒤 재실행 |
| 주행 출력 | 계산 뒤 즉시 동기 발행 | 미송신 후보 1개와 in-flight 사본 1개. 제어 내용/송신자 실행/연결 문맥/나이를 송신 직전 검사 |
| 앱 보고 | main에서 생성 | 실제 계산으로 생성한 heartbeat 1개·사건 4개만 bounded 전달. worker는 진행 보고를 생성하지 않음 |

Store 지연 시험은 실제 제품 netfront와 Zephyr thread/lock을 사용하고 외부 Store만 지연했다.
요청한 지연은 35ms이며 native tick 반올림이 있어 정밀 35ms 지연의 증명이 아니다.
같은 관측 창에서 기준선 `30ms / 0 poll`,
후보 `30ms / 2 poll`을 기록했다. 표본은 각 1회다.
이 수치가 실제 Xen의 최악 지연이나 처리율을 보장하지 않는다.

입력은 본문·원래 수신 시각·송신자 boot/instance·seq·오류를 묶어 복사한다.
envelope decoder/socket은 v1 worker, payload parser/알고리즘은 계산 main 소유다.
입력 나이를 복사 시 초기화하지 않고 잘못된 새 payload도 과거 정상값으로 되돌리지 않는다.
같은 제어 내용을 새 seq로 받는 것만으로 문맥 revision을 바꾸지는 않는다.

후보 200ms 한도는 잠정 로컬 큐 상한이며 20ms 주기·250ms I/O 예산·Gateway 기한과 별개다.
이동 출력은 원 입력의 200/300ms 한도도 함께 적용한다. frame 송신에는 남은 후보 기한을
전달한다. 부분 송신 실패는 byte stream을 닫고 해당 후보를 재전송하지 않는다.
최종 Gateway의 제어권/generation 검증을 대체하지 않는다.

## 오류 재현과 후속 보완

| 시험 | 수정 전 관측 | 수정 후 관측 |
|---|---|---|
| 외부 50ms 종료와 진행 중 Bootstrap | main이 새 예산으로 기다려 240ms 뒤 성공 반환 | 원 기한을 stop gate와 함께 게시. 남은 40ms 뒤 `-ETIMEDOUT`, 미종료 owner 보존 후 명시적 join 확인 |
| 출력함 enqueue와 socket idle | 새 후보가 poll idle 만료를 기다려 wake 시험 실패 | socket 비대기 readiness 조회 + 출력함 신호로 깨는 idle. 같은 native tick 안에 처리한 시험 통과 |
| 동일 제어 내용 재수신 | 변경 없는 seq 갱신에도 이전 후보 폐기 | 내용·유효성·송신자 실행 변경만 문맥 변경. 실제 변경/재연결 거절 유지 |
| ARM minimal libc | 신규 코드의 `ESTALE` 미제공으로 빌드 실패 | 기존 제공 `EXDEV`로 문맥 불일치 표현. 동일 ARM 빌드 통과 |

wake 시험의 같은 tick은 비용 0µs를 뜻하지 않는다. 기존 connect/frame I/O의 250ms 한도,
주행 20ms origin, 놓친 release를 몰아 재실행하지 않는 규칙은 바꾸지 않았다.
기한 만료 뒤 join은 종료 상태의 재확인이며 새 연결·발행 권한이 아니다.

## 초기 구현의 로컬 검사

| 검사 | 결과 |
|---|---|
| 실제 native kernel 사례 70개 | netfront/종료 27, Store 비교·오류 6, v1 owner 21, 앱 수명주기 9, 실제 v1 frame 기한 1, Starter 5, Publisher 1 통과 |
| host 링·grant·공유 Store·종료 | 생산 C에 대한 기존 host·ASan·UBSan 회귀 통과. RX 포화 중 64건 한도와 pending 재실행 근거 추가 |
| 플랫폼 unittest | 894개 대상. 최초 전체 실행 통과(UID 1건 skip). 최종 실행의 로그 출력 폴더 누락 1건은 폴더 준비 후 같은 실제 systemd 259 검사 재시험 통과 |
| 실제 UID 소비자 검사 | 격리된 root fixture에서 별도 실행 통과. 실제 Wi-Fi 설정/서비스 변경 없음 |
| 부팅·설정·패키징 회귀 | 기존 bringup 검사 통과. source lock·기본 자산·systemd unit·이미지 구조 변경 없음 |
| 자율/수동 ARM 후보 | 기준선/A/B/통합 8개 빌드, ELF/DTS/설정/firmware bundle 검증 통과 |
| Network Starter ARM | on/on, on/off, off/off, exercise 4개 빌드·bundle_info 검증 통과 |

최종 전체 실행의 실패는 제품 오류가 아니라 시험 로그 경로
`test-logs-final`을 미리 만들지 않은 외부 runner 설정 오류였다.
`full-platform-final.log`와 `loader-log-path-recheck.log`를 함께 보존했다.
기존 실패·중간 후보를 최종 성공 결과로 덮어쓰지 않았다.

## 실제 native 송수신

실제 `auto_step()`/`manual_step()`·v1 client·v2 Publisher와 인증된 Mosquitto를 사용했다.
제어 입력은 고정 20Hz, 계산은 50Hz, 시나리오는 기존 8초 4단계다.
각 조건·앱의 표본은 18초 1회이며 단절 조건은 소유 broker를 2초 중단 후 복귀시켰다.
격리된 network/mount namespace·TAP·별도 listener/ACL을 사용했고 PC 네트워크는 변경하지 않았다.

| 조건 | 앱 | 계산 기록 | 로컬 송신 성공 | 독립 수신 일치 | 성공 후 미수신 | 내용 오류 |
|---|---|---:|---:|---:|---:|---:|
| native-normal-wake | manual | 900 | 883 | 883 | 0 | 0 |
| native-normal-wake | autonomous | 900 | 890 | 890 | 0 | 0 |
| native-outage-wake | manual | 900 | 729 | 726 | 3 | 0 |
| native-outage-wake | autonomous | 900 | 732 | 726 | 6 | 0 |

정상 조건은 송신 성공과 독립 수신이 모두 일치해야 통과시켰다. 단절 조건의 QoS0 미수신은
위치를 임의로 단정하지 않는다. 출력함 교체/만료/문맥 거절을 네트워크 유실로 세지 않는다.
schema 2의 계산·출력함 기록과 실제 `XD_SEND`를 연결하고 legacy schema 1 분석도 유지한다.
정확한 해석은 [주행 runtime](../../platform/apps/common/xenova-drive-runtime/README.md#실제-계산과-계측)에 있다.

native cycle 주파수·stack 관측은 `XD_CLOCK` 원자료에 있다. 이 환경의 거친 tick에서 나온
주기 분위수나 0µs 구간을 RPi4 실시간 성능·CPU 시간·비용 없음으로 보고하지 않는다.
guest와 수신기 단조 시계를 빼서 편도 지연을 만들지 않았다.

### 동일 native 조건의 기준선 비교

수신 간격의 뭉침을 확인하기 위해 같은 runner·알고리즘·20Hz 입력·broker 설정에서
기준선과 통합 후보를 순서대로 실행했다. 각 앱·조건은 18초 1회다.
실행 전 WSL load average와 수신기 CPU affinity도 `NATIVE_COMPARISON.json`에 기록했다.
실제 Xen 비교나 반복 측정에 의한 성능 수락을 대신하지 않는다.

| 구성 | 앱 | 계산 기록 | 로컬 송신 성공 / 수신 일치 | 계산 간격 p99 / 최대 (µs) | 수신 간격 p99 / 최대 (µs) |
|---|---|---:|---:|---:|---:|
| 기준선 | manual | 894 | 891 / 891 | 30,000 / 150,000 | 99,493.540 / 104,656.918 |
| 기준선 | autonomous | 894 | 891 / 891 | 30,000 / 150,000 | 99,346.512 / 100,196.723 |
| 통합 후보 | manual | 900 | 883 / 883 | 20,000 / 30,000 | 99,568.985 / 103,614.167 |
| 통합 후보 | autonomous | 900 | 888 / 888 | 20,000 / 30,000 | 99,788.865 / 110,205.934 |

네 실행 모두 내용 오류·로컬 송신 성공 후 미수신은 0건이었다. 후보의 송신 전 거절은
manual 17건, autonomous 12건이며, 각각 출력함 미수락 9건과 제어 문맥 변경 8/3건으로
분리된다. 기준선은 각 3건의 로컬 송신 실패와 6회의 예정 계산 누락을 기록했다.
출력 후보 수의 차이를 네트워크 유실로 해석하지 않는다.

기준선과 후보 모두 수신 간격 p99가 약 100ms다. 이 시험만으로 뭉침의 원인이나 후보의
전달 지연 개선을 확정할 수 없다. 특히 autonomous의 관측 최대 수신 간격은 후보에서
더 컸으므로, 계산 간격의 개선을 종단 성능 수락으로 확대하지 않는다.
같은 ARM 바이너리·배치·입력 조건의 실보드 전달 간격·손실·스택 검증이 남아 있다.

## ARM 메모리·스택

고정 Zephyr `684c9e8f32e4373a21098559f748f06915f950c9`, SDK 1.0.0/AArch64 GCC 14.3.0을 사용했다.
기본 1 vCPU/16MiB/GICv2, 기존 TCP workqueue와 stack sentinel을 유지했다.
아래는 ELF size의 byte 값이며 실제 동적 peak나 전체 예약 RAM이 아니다.

| 구성 | 앱 | text | data | bss | 합계 |
|---|---|---:|---:|---:|---:|
| baseline | manual | 368,863 | 6,088 | 2,203,639 | 2,578,590 |
| baseline | autonomous | 368,979 | 5,968 | 2,203,639 | 2,578,586 |
| a-net | manual | 368,863 | 6,128 | 2,207,734 | 2,582,725 |
| a-net | autonomous | 373,075 | 6,008 | 2,207,734 | 2,586,817 |
| b-drive | manual | 374,293 | 4,856 | 3,108,851 | 3,488,000 |
| b-drive | autonomous | 378,505 | 4,736 | 3,108,851 | 3,492,092 |
| combined | manual | 378,389 | 4,896 | 3,112,946 | 3,496,231 |
| combined | autonomous | 378,505 | 4,776 | 3,112,946 | 3,496,227 |

추가 정적 RAM의 대부분은 계산과 송신을 따로 남기는 bounded 원자료 배열이다.
상태 observer stack은 4KiB, v1 worker stack은 8KiB다. 운영/실험 구성 분리는 후속 범위로 남긴다.
동일 ARM compiler의 `-fstack-usage`로 netfront/Store/Bootstrap/socket/주행 경로를 검사했다.
통합 `.send` 직접 frame은 48 byte로 기존 256 byte 검사 안이다. observer 직접 frame 48 byte,
`backend_state` 80, `xvif_refresh` 320, v1 owner 256, Xen bootstrap loader 2,176 byte다.
기존 `xs_write` 2,416 byte도 그대로 확인했다. 이들은 직접 frame이며 전체 호출 체인의
상한이나 실제 ARM high-water 보장이 아니다. 실보드 stack·CPU·전달 지연 수락은 남아 있다.

## 산출물·재현·보호

원자료/명령/영수증/소스 입력/전체 frame 표는 다음 실행 폴더에 보존한다.

- PC: `C:/Users/SSAFY/Documents/Codex/2026-10-26/xenova-wait-separation`
- WSL: `/home/ssafy/xenova-wait-separation-20261026`
- 최종 후보·source hash: `MATRIX_FINAL_INPUTS.json`, `ARM_MATRIX.json`, `RESULT.json`
- 추가 native 비교: `NATIVE_COMPARISON.json`, `native-controlled-baseline`, `native-controlled-current`
- 재현 runner: `work.py`, `final-proof.py`, `closing-native.py`, `matrix.py`, `compare-native.py`

| 구성 | 앱 | firmware SHA-256 |
|---|---|---|
| baseline | manual | `1038fcb25ba81ceb456f46906ffcc078f360ed549159e47ad74d92b9061e9caf` |
| baseline | autonomous | `d9ad044034496c8a417ac127d19865ed366200f4a43ada24578c4349647ef334` |
| a-net | manual | `c7a6942994df0d6b6b55879a2725090bb34625548d22d0e78c590517d6f93f73` |
| a-net | autonomous | `03dd107f79e3a7dfb0045c673791dc624fbb61360c48b998a8945402ed8df59b` |
| b-drive | manual | `679b79b85c4248d699a8ed25aebec5818a918007f1bab1f9d9f89f4b5280ad59` |
| b-drive | autonomous | `6bb0e4e01d6ba1bb07fe199e8cc4da2c875f5672adc97690ff1673d7214156fa` |
| combined | manual | `a9030a2f1009cc15706f5e8fbb024453ef0ba6ac6aef5620896ca76a0053f14a` |
| combined | autonomous | `1f219ea004696618f2482fbbe0f230fa71da4721380330cce6be1a0f88b43ae6` |

일반화한 구조·사용법은 [Xen runtime](../../platform/guests/zephyr/xen-runtime/README.md)과
[주행 runtime](../../platform/apps/common/xenova-drive-runtime/README.md)에 반영했다.
runtime 의존성 추가, 앱 알고리즘 변경, v1/v2 wire 계약 변경, 상위 제품 수정은 없다.
기본 VM 제어·CPU 정책/관리 네트워크 변경·host 재부팅 명령·이미지/SD 작업은 수행하지 않았다.
실보드 시험 범위는 PC의 `BOARD_EXECUTION_REVIEW.md`로 준비했으며 사용자가 승인했다.
Store/backend/grant의 실제 장애 주입은 영향 범위를 더 확인해야 하며 정상 송수신 수락과 구별한다.

## 실보드 진행 상태

### 접속과 새 부팅의 기준선

`192.168.123.116:22`에서 기존 호스트키로 인증했다. 최초 boot ID는
`c0f76b65-c4b4-47cf-baca-430cba9dd9f5`였다. SSH가 전송·조회 중 간헐적으로 끊겼으며,
회수한 로그와 TCP 재전송만으로 원인은 확정하지 못했다. 첫 전송은 재접속 후 시험 경로가
없음을 확인해 설치 미도달로 판정했다. 원 실패 영수증은 보존했다.

사용자가 재부팅한 뒤 COM3와 SSH가 다시 사용 가능해졌다. 새 boot ID
`36d5e83a-0464-4894-b7f0-f90fe43c1c39`에서 신원·설치 모듈·기본 자산·CPU 준비·host admission을
다시 조회했다. 기본 Debian·Zephyr A/B의 UUID는 유지됐고 CPU 정책 revision 1, Credit2,
dom0/Debian `{0,3}`, A `{1}`, B `{2}`가 일치했다. 이전 부팅의 실행·준비 근거는 재사용하지 않았다.

공개 payload 18,113,101 byte를 속도 제한·조각별 해시 확인으로 전송했다. 설치된
`xenova_zephyr.bundle_info()`로 펌웨어 8개와 공개 파일 168개를 검증했고, 임시 자격증명은
보드의 root 전용 시험 경로에만 생성했다. 설치 검사 도구의 잘못된 `validate_bundle` 호출은
외부 runner 오류로 보존하고, 실제 함수로 재검사했다. 제품 runtime을 교체한 것은 아니다.

### 이동 전 실행 범위와 당시 미완료 범위

| 대상 | 마지막으로 확인한 결과 |
|---|---|
| 소유 자율 시험 VM | UUID `d0000bce-1016-4a37-8f15-61e50716c949`, 기준선 firmware, domid 4, execution `0f19be64-b5f7-415a-88e1-d90d07ee71eb`로 실행 |
| 소유 수동 시험 VM | UUID `ff313e66-e69b-4cea-9dae-09042295f7ba`, 기준선 firmware, domid 5, execution `bb8b882e-add0-41af-8774-9753275b21d8`로 실행 |
| 격리 broker | 내부 `192.168.77.1:28883/28884`, VM별 인증·ACL, v1/v2 실행별 Bootstrap 주입 확인 |
| 보호 대상 | 기본 VM·설치 runtime·CPU 정책·관리 네트워크 변경 없음. 측정 전 CPU 조회에서 시험 VM의 공유 배치와 기본 VM의 배치 확인 |
| `baseline-n1` | 신원 조회용 시험 프로세스가 3초 기한을 넘겨 계측 시작 전 종료. 성능 표본에서 제외 |
| `baseline-n1-r2` | 30초 준비·180초 창 실행. 페이지 회수의 prompt 판별 오류로 완전한 원자료 검증 실패. 성능 표본으로 아직 수락하지 않음 |
| A/B/통합 후보 | 보드로 파일 전송·검증했으나 해당 firmware의 VM 실행·성능 비교는 미수행 |
| 단절·협조 stop/reboot | 이번 후보 비교의 실보드 수락 미수행 |

외부 관측기는 매 호출마다 CLI를 시작하는 방식에서, 설치본의 동일한 `identity()`를
시험 전용 프로세스에서 호출하는 방식으로 보완했다. 매번 실제 Xen 신원을 조회하며,
잠금 대기를 포함한 3초 기한을 유지한다. 별도 읽기 시험 1회에서 CLI 호출은 약 0.65초,
함수 호출은 약 1~4ms였으며 보장 상한으로 해석하지 않는다. 명령 echo와 최종 bare prompt도
구별하도록 보완했다. 이 보완은 저장소의 제품 runtime 변경에 포함되지 않는다.

`recover-sealed-baseline-01`은 capture를 다시 설정하지 않고 같은 실행·측정 창의 회수만
시도했으나 SSH가 종료 코드 255로 끝났다. 원격 완료 여부는 미확정이다. 기존 실패 기록과
원자료를 성공으로 덮어쓰지 않았다. `baseline-n1-r2`의 온도 가드는 실행됐지만 온도 시계열을 회수
전에 보관하지 못했으므로 해당 시계열도 미확보로 남긴다.

### 이동 당시 중단과 재개 조건

2026-10-26 사용자의 이동 요청에 따라 실보드 시험을 중단했다. 중단 후 새 보드 접속이나
VM·서비스 제어를 실행하지 않았다. 시험 VM·broker·시험 경로의 최종 정리 여부는 미확인이다.
실보드의 최종 상태를 마지막 조회와 같다고 가정하지 않는다.

재개 시 보드 신원·현재 boot ID, 두 소유 UUID의 실행·journal, broker PID/start time,
시험 경로와 원자료 회수 결과부터 읽기 전용으로 확인한다. 같은 실행이 남아 있으면 봉인된
측정 창의 회수 여부를 먼저 판정한다. 실행이 바뀌었으면 다른 실행의 자료와 합치지 않는다.
기본 VM·CPU 정책·관리 연결의 현재 상태도 다시 대조한 뒤 남은 비교·협조 종료·소유 자산 정리를
진행한다. 파일 전송 완료나 service active를 실보드 수락으로 대체하지 않는다.

### 이동 후 재개와 수집기 점검

사용자의 재개 지시에 따라 `192.168.30.96:22`에서 기존 host key와 보드 serial
`10000000903220b1`을 확인했다. 현재 host boot ID는
`1f27f03e-a4b7-4eb1-9579-7319a002658c`이다. 이전 부팅의 두 시험 실행은 없었고,
이전 `baseline-n1-r2`는 계산 파일 한 개가 0 byte, 다른 한 개가 없었다. 콘솔 파일에 남은
계산 행도 앱별 4행이어서 완전한 계산 원자료로 사용할 수 없었다. 영구 경로의 자료를
회수해 `PREVIOUS_SEALED_BASELINE_ASSESSMENT.json`에 판정했고, RAM 자료를 수신 자료로
대체하거나 과거 실패를 성공으로 바꾸지 않았다.

새 부팅에서 기본 자산·설치 모듈·CPU 정책 revision 1과 실제 affinity를 다시 확인했다.
기본 Debian·Zephyr A/B는 유지하고, 같은 두 소유 UUID만 사용한다. 소유 VM은 공유 집합
`{0,3}`에서 실행되며, 비교 중 CPU 정책과 도메인 수를 바꾸지 않는다. 8개 펌웨어도 설치본의
bundle 검증으로 다시 확인했다. Tailscale은 이 보드에 설치되어 있지 않으며 추가하지 않았다.

`resume30-console-probe`의 5초 준비·10초 창에서 두 앱의 계산 원자료를 각각 500행씩
완전히 회수했다. 앱별 로컬 송신 성공 500건과 독립 수신 500건이 일치했고 내용 오류와
성공 후 미수신은 0건이었다. 원자료 회수는 두 앱 합계 약 8.10초였다. 다만 구독을
provisioning 뒤에 시작해 비보존 부팅 이벤트를 놓쳤으므로 전체 수락은 실패로 남겼다.

후속 수집은 읽기 전용 v2 구독기의 SUBACK을 확인한 뒤 VM을 기동한다. 이때 아직 모르는
boot/publisher 신원은 임의로 만들지 않는다. 원래 unbound 수신 파일을 보존하고 공식
provisioning 영수증의 VM·boot·publisher 값과 정확히 일치하는 실제 부팅 이벤트만 사후에
연결한다. 본 측정의 heartbeat·진행 근거는 별도의 bound 구독기 자료로 판단한다.
파생 분석 디렉터리에는 원 수신 byte·시각·파일 해시·행 번호·결합 근거를 남긴다.
기존 분석기의 5개 v2 stream 및 정상 조건의 송신/수신 일치 기준은 유지한다.

첫 기준선의 기동 전 구독에서 두 실행의 부팅 이벤트를 각각 6건씩 확인했다.
정식 비교는 30초 준비·180초 창, 각 조건 1회이며 기준선 수락 뒤 A(netfront/Store 분리),
B(계산/v1 통신 분리), 통합 후보 순으로 진행한다. 이 절의 수집기 변경은 저장소 제품 코드가
아닌 시험 전용 외부 도구의 보완이다. 문서는 사용자 요청에 따라 커밋 대상에서 제외한다.

### 최종 실보드 비교: 기능·자료 정합성

표본은 구성별 1회, 앱별 계산 9,000행이다. 실제 정책 revision 1, Credit2, 전체 도메인 6개,
동일 입력·broker 설정·고정 8개 ARM 바이너리를 사용했다. 두 소유 VM은 `{0,3}` 공유 배치다.
각 창 전후 전체 도메인의 실행 신원과 affinity를 대조했다. A와 통합의 마지막 CPU 조회는
미수락 `OPERATION_IN_PROGRESS`로 끝났으나 측정·원자료는 완료되어 있었다. 물리 실행이나
측정을 반복하지 않고 별도 읽기로 같은 incarnation·배치·보호 상태를 재확인했다.
원 job 실패와 `after-reconciliation.json`을 함께 보존했다.

| 구성 | 앱 | 계산 | 로컬 송신 성공 / 독립 수신 | 송신 전 문맥 거절 | 절대 지터 p99 / 관측 최대 (µs) | 수신 간격 p99 / 관측 최대 (ms) |
|---|---|---:|---:|---:|---:|---:|
| 기준선 | manual | 9,000 | 9,000 / 9,000 | 0 | 1,665 / 6,424 | 76.416 / 101.892 |
| 기준선 | autonomous | 9,000 | 9,000 / 9,000 | 0 | 1,653 / 6,748 | 90.584 / 109.122 |
| A: 패킷/Store | manual | 9,000 | 9,000 / 9,000 | 0 | 1,553 / 8,154 | 73.871 / 98.132 |
| A: 패킷/Store | autonomous | 9,000 | 9,000 / 9,000 | 0 | 2,049 / 7,051 | 90.195 / 107.017 |
| B: 계산/v1 | manual | 9,000 | 8,989 / 8,989 | 11 | 1,333 / 6,831 | 79.301 / 105.911 |
| B: 계산/v1 | autonomous | 9,000 | 8,986 / 8,986 | 14 | 1,703 / 2,941 | 93.871 / 105.896 |
| 통합 | manual | 9,000 | 8,988 / 8,988 | 12 | 1,381 / 3,078 | 67.990 / 101.064 |
| 통합 | autonomous | 9,000 | 8,986 / 8,986 | 14 | 1,858 / 6,759 | 86.591 / 107.678 |

네 구성 모두 내용 오류, 로컬 송신 성공 후 미수신, 계산 deadline miss·skipped release는 0건이다.
B/통합의 송신 전 거절은 `XD_CONTEXT_CHANGED`이며 의도적 Mailbox 문맥 거절을 네트워크 유실로
세지 않았다. 분위수는 실제 원자료의 nearest-rank다. N=1이며 관측 최대는 보장 상한이 아니다.
특히 통합의 autonomous 절대 지터 p99는 기준선보다 커서 일괄적인 개선 결론을 내리지 않는다.

주행 수신기의 reader error는 네 정상 조건에서 0건이었다. 기준선의 진단 수신기에는
재접속 4회가 있었지만 형식 오류·큐 유실은 없었다. 초기에는 외부 분석 wrapper가 원래
분석기에는 없는 reader error 0 조건으로 진행을 막았다. 이 추가 조건은 수락 기준에서
제외하고 원 수치와 진단 전달 연속성의 한계를 결과에 남겼다. 재접속 원인은 확정하지 않았다.
`analyze.py`의 `accepted`는 기능·자료 정합성 수락이지 성능 개선 판정이 아니다.

원자료는 `board-live/resume30-{baseline,a-net,b-drive,combined}-n1`에, 실제 기동 전 이벤트를
연결한 파생 근거·원 파일 해시·분석 결과는 각각의 `-analysis` 디렉터리에 있다.
기존 분석기 소스를 보존하여 네 조건에 같은 기준을 적용했다.

### 수신 간격 뭉침의 위치와 설정 관측

`INTERVAL_BOUNDARIES_FINAL.json`은 계산 시작·후보 생성·출력함 수락·실제 MQTT 송신 시작과
호출 소요시간·독립 수신을 구분한다. 긴 간격 40,000µs 이상, 짧은 간격 5,000µs 이하라는
분석용 기준을 전 구성에 동일하게 적용해 연속 패턴도 셌다. 제품의 새로운 deadline/TTL은 아니다.
정상 네 구성의 계산·송신 시작 간격 p99는 대체로 21~22ms지만 수신 간격 p99는 68~94ms다.
통합의 Mailbox 수락 간격 p99는 manual 21.098ms, autonomous 21.388ms이고,
실제 송신 시작은 21.173/21.505ms였다. Mailbox 이전부터 같은 크기의 뭉침이 있었다고
해석할 근거는 없으며, API 송신 시작 이후 구간을 더 관측했다.

본 비교 뒤 별도 45초 TCP 메타데이터 관측을 실시했다. payload·인증정보를 저장하지 않고
dom0의 같은 단조 시계로 xenbr0 유입과 loopback 유출을 기록했다. 두 관측 소켓의 kernel drop은
0, 50,000행 한도 초과도 없었다. 연속 공통 host 구간은 약 24.66초다.

| 관측 | 100 byte 초과 TCP segment 수 | 해당 간격 p99 | 전체 payload 길이 p99 |
|---|---:|---:|---:|
| autonomous → broker, xenbr0 | 590 | 65.935ms | 1,460 byte |
| manual → broker, xenbr0 | 625 | 61.371ms | 1,164 byte |
| broker → loopback 데이터 flow | 983 | 49.101ms | 3,478 byte |

같은 host 구간의 MQTT 수신 간격 p99는 manual 78.626ms, autonomous 86.873ms였다.
**broker 유입 전에도 TCP segment 간격의 불균일이 관측된다.** 다만 segment 한 개를 MQTT
명령 한 개로 보지 않는다. 이 수치는 사용자 공간 `recvfrom` 직후 관측 시각이며 wire timestamp가
아니다. 수신기 시작 전에 읽은 PID 메타데이터에는 아직 socket endpoint가 없었으므로 flow를
수신기 PID와 직접 연결했다고 주장하지 않는다. guest와 dom0 시각의 차로 편도 지연도 계산하지 않았다.

설치 Mosquitto는 2.1.2다. 두 시험 설정 파일에 `set_tcp_nodelay` 명시는 없었고,
`pidfd_getfd`로 복제한 **실제 broker의 연결 소켓 TCP_NODELAY는 모두 0**이었다.
당시 실보드 후보의 고정 Zephyr 소스는 `tcp_nodelay=false`로 초기화하며,
공통 custom transport와 v1 client는 이를 설정하지 않았다.
guest의 살아 있는 socket 값은 직접 조회하지 않았으므로 소스 근거와 실측을 구분한다.
Nagle은 원인 후보지만 설정을 바꾸지 않아 인과 효과를 확정하지 않는다.

다음 실험안은 현재 결과와 별도다. 먼저 같은 펌웨어·입력에서 시험 broker의
`set_tcp_nodelay`만 바꾼 한 조건을 비교하고, 필요하면 guest socket 옵션만 바꾼 독립 후보를
추가한다. 각 변수는 분리하고 재연결된 실제 소켓 값·계산/송신/수신 분포·손실을 대조해야 한다.
이 실보드 비교에서는 설정 변경을 수행하지 않았다. 이후 v1 소스의 변경과 로컬 검증은
아래 TCP_NODELAY 절에 분리한다.

패킷 관측용 짧은 capture는 **수신 연속성 수락 실패**다. source 500행 중 초기 manual 41행,
autonomous 42행의 로컬 송신 성공이 수신기에 없었고 이후 마지막 행까지 일치했다.
dom0 시계로 첫 수신 기록은 arm 뒤 5.854초여서 5초 준비 뒤의 창 앞부분과 겹쳤다.
이를 정상 네 조건의 손실이나 Nagle의 결과로 단정하지 않았다. 실패 자료와
`ANCILLARY_PROBE_COVERAGE.json`을 보존하고 이후 짧은 시험에는 실제 SUBACK 확인을 선행했다.
기다리는 초 수만 늘리거나 실패 창을 정상 성능 표본으로 바꾸지 않았다.

### 단절·협조형 재부팅·인계

통합 후보의 최종 단절 시험은 `resume30-combined-outage-r3`다. 설정을 바꾸지 않고 소유
broker만 중단·재시작했으며 stop/start 처리까지 포함한 관측 공백은 약 2.267초였다.
계산은 앱별 500행, deadline miss/skipped 0회, 내용 오류 0건이었다.
로컬 송신 성공은 각 346건, 독립 일치는 각 344건이며 QoS0 미수신 2건씩은 그대로 남긴다.
이 경우 lossless 수락을 요구하지 않고 같은 실행의 복귀 후 실제 명령 수신을 확인했다.

앞선 단절 시도 두 번은 시험 도구 오류로 보존했다. 첫째는 이미 reaped된 PID의 존재 확인과
status 읽기 사이 경합, 둘째는 임시 포트 검사에서 TIME_WAIT을 live listener처럼 취급한 오류다.
외부 controller의 상태 읽기를 한 번으로 제한하고, 사전 bind 검사에 restartable listener의
SO_REUSEADDR를 사용했다. 다른 live listener 부재를 확인한 뒤 같은 소유 broker 설정으로
복원했다. 제품 runtime이나 TCP_NODELAY·broker 설정 파일을 수정한 것은 아니다.

`resume30-combined-reboot1`은 두 UUID에 force=false인 실제 협조형 reboot를 수행했다.
domid 12/13에서 14/15로 바뀌었고 execution/guest boot/app/Publisher 세션을 새로 확인했다.
UUID·configuration·펌웨어·CPU 정책은 유지했다. 새 실행의 `resume30-postreboot-probe`는
앱별 계산 500행, 송신/수신 manual 495/495·autonomous 497/497, 내용 오류·미수신·계산 누락
0건으로 통과했다. 송신 전 문맥 거절은 정상 송신 유실과 구분했다. host는 재부팅하지 않았다.

이후 두 소유 VM만 정상 stop → retire → UUID 확인 purge를 수행했다. tombstone과 제품 journal은
기존 계약대로 남긴다. 소유 broker를 종료하고, 기본 VM의 configuration·generation·실행·의도
revision과 실제 CPU 배치가 같으며 Wi-Fi가 COMPLETED임을 확인했다.
공개 자료를 PC에 회수·해시 대조한 뒤 승인된 시험 폴더의 919개 파일을 정리했다.
인증자료 68개는 내용 보관에서 제외하고 폐기했다. 새 공개 증거는
`BOARD_FINAL_PUBLIC_EVIDENCE.tar.gz` 147,500 byte이며 나머지는 기존 회수본으로 확인했다.
`BOARD_CLEANUP_ASSETS.json`, `BOARD_CLEANUP_MANIFEST.json`, `BOARD_RELEASED.json`에 근거가 있다.
SSH ControlMaster도 종료했으며 인계 뒤 보드에는 재접속하지 않았다.

### 후속 수정 F01: 기존 실보드 후보와 분리

| 점검 | 기존 제품 코드의 재현 | F01 변경·결과 |
|---|---|---|
| 창 종료 직전 수락 후보가 송신 중 | 실제 owner thread의 MQTT 완료만 지연하자 `capture_complete=true`인데 transmissions 조회가 성공 | 계산 완료와 창별 수락 후보의 terminal 확정을 분리. 미확정 페이지/재설정 `-EBUSY`, 확정 후 실제 shell 페이지 total 유지 |
| 지속적인 사건 유입 | 실제 owner에서 사건 큐를 계속 채우자 계산이 만든 heartbeat가 전혀 전송되지 않음 | 대기 heartbeat 앞에 최대 사건 한 개만 허용. worker가 새 heartbeat/진행을 생성하지 않으며 동일 시험 통과 |
| heartbeat 만료 | heartbeat 폐기가 `event_dropped`에 더해짐 | heartbeat_dropped·그중 heartbeat_expired와 heartbeat_replaced를 구분. 만료 시험 통과 |
| 재연결 후 입력만 먼저 도착 | 새 입력과 이전 control 캐시를 동시에 관측 | 원 seq·수신 시각 유지, 이전 연결 후보 거절 시험 통과. 캐시 삭제·새 control TTL·새 권한 정책은 추가하지 않음 |

마지막 항목의 `connected`는 CONNACK 관측이며 SUBACK/현재 연결의 control 재수신 확인이 아니다.
기존 계약은 변경 시 retained control과 최종 Gateway의 generation 검증을 사용한다.
새로 계산한 후보가 마지막 수신 제어 generation을 사용하는 동작을 새 제어 확인으로 보고하지 않는다.
재확인을 필수 admission으로 바꾸려면 별도 제어 계약 결정이 필요하다.

F01의 `transmissions_complete`는 로컬 계측 필드다. 명시적으로 false인 자료는 분석기가
거절하고, 기존 schema 2에서 이 필드가 없으면 원래의 수락 후보/terminal 일대일 대조를 유지한다.
기존 누락 거절·v1/v2 wire 계약·20ms origin·I/O/종료 기한·force 승인 규칙은 바꾸지 않았다.

검증은 이벤트 밀림/만료의 수정 전 실패와 수정 후 성공, 재연결 입력 선도착,
기존 문맥/교체/종료 등 실제 native I/O 20사례, 실제 recorder·shell 페이지 반복 회수,
분석기 14시험을 포함한다. 전체 플랫폼 회귀는 **895개 대상 중 894개 통과, 1개 skip**이며
311.466초였다. 기존 고정 systemd 도구와 bringup 패키징 검사도 통과했다.
실제 F01 자율/수동 앱의 native 정상·2초 단절 송수신/분석도 통과했다.

| F01 ARM 산출물 | text / data / bss (byte) | 합계 | netfront 직접 send frame |
|---|---:|---:|---:|
| manual | 378,609 / 4,672 / 3,112,948 | 3,496,229 | 48 byte |
| autonomous | 378,725 / 4,560 / 3,112,948 | 3,496,233 | 48 byte |

이는 ELF section 합계이며 전체 예약 RAM·동적 peak가 아니다. 기존 1-vCPU/16MiB/GICv2,
stack sentinel과 직접 frame 256 byte 검사를 통과했다. `FOLLOWUP_F01_INPUTS.json`,
`arm-followup-f01-{manual,autonomous}.json`에 고정 소스·도구·파일 해시·빌드 영수증을 보관했다.
기존 `ARM_MATRIX.json`과 8개 실보드 후보는 덮어쓰지 않았다. **F01의 실보드 적용·동적 스택/메모리·
성능 수락은 미수행**이며 로컬 수락을 실보드 수락으로 확대하지 않는다.

### v1 TCP_NODELAY 후속 변경과 로컬 검증

`0af6c37`은 v1 client의 TCP 연결·MQTT CONNECT 제출 뒤, 구독·앱 메시지 제출 전에
`TCP_NODELAY=1`을 설정한다. 일반 TCP와 deadline TCP 경로의 초기 연결·재연결에
적용하고, 옵션 설정 실패는 기존 close/backoff 경로로 전달한다. v2 Publisher와
Mosquitto 설정은 변경하지 않았다.

고정 Zephyr `684c9e8f32e4373a21098559f748f06915f950c9`의 native_sim에서
실제 TCP socket을 생성하고 `getsockopt`로 초기값 0과 적용 후 1을 확인했다.
MQTT 연결·peer 경계를 대역으로 둔 두 전송 경로에서 초기 연결·재연결·옵션 설정 실패 후 close와
backoff·연결 실패 시 옵션 미호출을 확인했다. 생산 socket 옵션 호출은 실제 Zephyr 구현을
사용하고, 실패 주입만 syscall 경계에서 수행했다.

최초 시험은 inline syscall을 잘못 치환해 호출 횟수 단언에 실패했다.
실제 구현 심볼인 `z_impl_zsock_setsockopt`에 시험 대역을 연결한 뒤 두 경로가 통과했다.
실패 기록은 `v1-nodelay-bounded-socket-test.json`, 재시험은
`v1-nodelay-{bounded,legacy}-r2-socket-test.json`에 보존한다.
기존 v1 frame deadline·순번 시험, 분석기 14시험, bringup 패키징 검사도 통과했다.
입력 해시는 `V1_NODELAY_INPUTS-r2.json`에 기록했다.

이후 ARM 부분 빌드·실보드 배포·송수신 간격 비교는 수행하지 않았다.
기존 네 조건의 실보드 결과와 F01 ARM 산출물에 이 옵션이 적용됐다고 해석하지 않는다.

설명 확인에는 Zephyr 공식 [thread](https://docs.zephyrproject.org/latest/kernel/services/threads/index.html),
[workqueue](https://docs.zephyrproject.org/latest/kernel/services/threads/workqueue.html),
[clock](https://docs.zephyrproject.org/latest/kernel/services/timing/clocks.html) 문서를 참고했다.
구체적인 ABI·오류·timeout 동작의 기준은 latest 문서가 아닌 고정 소스와 실제 빌드/시험이다.

## 2026-11-03 실보드 단절 후속과 develop 통합

### 기준과 관측

원격 develop의 `ec49da415d425e3d4daf263d0012da71ffaebcf2`를 11월 3일에 받아 분리
작업트리를 만들었다. 보드의 `lab.2` 입력 영수증에는 platform의 `6f4c640`과 `d0bce11`이
들어 있었고 develop에는 두 변경이 빠져 있었다. 두 변경을 후보에 가져와 기존 보드를
교체할 때 해당 보호가 사라지지 않게 했다. 주행앱 입력 26개, 주행 계약, 하드웨어,
provisioning 프로필 및 경로를 정규화한 Kconfig는 설치된 `lab.2`와 같았다.

수정 전 보드에서 A의 게스트 링크는 down, XenBus 양쪽은 Connected(4)였다. Dom0의
`vif2.0`에서 세 차례 Guest Rx stalled가 기록됐고 같은 브리지의 B와 Debian은 통신했다.
22초 브로커 관찰에서 A 표본은 0건, B는 heartbeat 11건과 metrics 131건이었다.
고정 Raspberry Pi 커널은 수신 요청 슬롯이 모자란 상태를 stall로 보고한다. 대기 프레임이
만료된 뒤에도 ready를 기록할 수 있어 실제 수신은 게스트와 MQTT에서 별도로 대조했다.
[고정 커널 RX 처리](https://github.com/raspberrypi/linux/blob/95b85bebbedcaedfa7ca79116ed38b7376fba412/drivers/net/xen-netback/rx.c#L481-L504)

### 정책과 코드

| 경계 | 재현 근거 및 변경 |
|---|---|
| netfront 관측과 packet lock | usable Store의 일시 timeout·busy는 기존 링을 유지하며 재관측한다. 일시 packet lock 대기는 다음 tick에 다시 시도한다. 실제 backend 종료, 손상된 Store와 링 프로토콜 실패는 종결한다. |
| XenStore 송신 | `xen-store/src/xenstore.c`는 링에 맞는 요청을 한 번에 게시한다. 기한이나 공간 부족으로 미게시하면 producer·알림을 그대로 두고 pending 슬롯을 반환한다. 완전 송신 후 늦은 응답은 원 요청 ID로 처리한다. 큰 요청의 스트리밍과 부분 송신 뒤 세션 차단은 유지한다. |
| 협조 종료 | `xen-runtime/src/shutdown.c`의 단일 watch 힌트가 읽기 일시 오류에 소실되던 문제를 수정했다. usable Store에서 일시 오류인 동안 100ms 간격으로 값을 재확인한다. Store의 poison/close가 확인되면 감시를 종료한다. 유효 요청을 확인한 후 기존 ACK·quiesce·네트워크 및 Store 정리·hypercall 순서와 단일 소유권을 적용한다. |
| 현장 진단 | `xen-runtime/src/netfront.c`와 공통 주행 runtime에 `xenova drive netfront`를 연결했다. 기존 카운터와 워커가 종료된 위치·errno를 게스트 콘솔에서 조회한다. 종결 경로의 기존 로그에도 위치가 남는다. |

### 로컬 검증과 후보

- 동일 Store fixture에서 이전 코드는 기한 만료 때 헤더 16바이트를 게시한 채 세션을
  차단했다. 후보는 게시 0바이트, poison 0, pending 0을 확인했고 다음 정상 read가 성공했다.
  요청 링에 공간이 한 바이트 모자라는 사례와 ring wrap도 확인했다. Store 67개 C 사례가
  일반 빌드와 ASan/UBSan에서 통과했다.
- 같은 단일 종료 watch 시험에서 이전 코드는 timeout 뒤 원 요청을 확보하지 못했다.
  수정본은 timeout·busy·again에서 각 한 번 진행했고, read/watch의 sticky poison에서는
  quiesce·hypercall을 제출하지 않았다. 종료·네트워크 소유권 native_sim 17개와 합친
  후보의 관련 native_sim 13개를 각각 실행했다. Store·Xen hypercall 경계를 대역으로 뒀다.
- `platform/scripts/build-xenova-drive-app.py --app drive --profile warm-lattice-lab`을
  고정 Zephyr 4.4.0·SDK로 실행했다. ARM 입력
  `3eb84a4a5e78d311c23643a3b46288e6705a1e41231f714024c7484dbaf6161e`,
  버전 `0.1.0-drive-3eb84a4a5e78`,
  manifest `878647a0d6fb9cf8154fada882a11e6adeeca5f64b87b32966bf46a6a82360b3`,
  바이너리 `b18ed2eeb133afd37e61dc2687b6a0912b89b79412420fd7034e896224aaf092`.
- 기존 `lab.2`에 진단만 추가한 후보를 A에서 먼저 실행했다. 첫 3분 동안 A heartbeat
  91건, metrics 1,093건, `app.lifecycle.WAITING` 3,660건을 받았다. B는 heartbeat
  92건과 metrics 1,096건을 받았고, Spring DB에서 두 VM의 새 Zephyr 표본을 확인했다.
  이 구간에는 기존 장애가 재발하지 않아 그 장애의 최초 errno는 확보되지 않았다.

### 최종 작업트리와 실보드 수락

현재 작업의 원본은 로컬 `chore/frontend/live-console-check` 작업트리다. 이 브랜치를
`ec49da4`까지 fast-forward한 뒤 별도 검증 사본의 코드·시험·문서와 ARM 산출물을 옮겼다.
platform의 `6f4c640`과 `d0bce11`을 출처를 남기는 `cherry-pick -x`로 가져왔다.
이 브랜치에서 대응하는 커밋은 각각 `d46a15c`와 `bf59702`다. ARM 영수증에 들어 있는 입력
309개의 바이트 크기와 SHA-256이 현재 작업트리와 모두 일치했다. 고정 Zephyr commit은
`684c9e8f32e4373a21098559f748f06915f950c9`, SDK는 1.0.0이다.
로컬 기존 변경은 stash와 diff로 보관했다. 프런트의 기존 로컬 콘솔·지표 수정과 develop의
Linux 메모리 차트를 합친 뒤 타입 검사, Vitest 28파일 204개, 겹쳤던 두 파일의 Prettier 검사를 통과했다.
원본 커밋을 가져온 뒤 XenStore, netfront, Store 관측 상태의 host 회귀 10개를 다시 실행했다.

차량 출력 Gateway가 비활성인 상태에서 A만 일반 종료하고 최종 ARM 번들로 교체했다.
A의 로드 manifest가 후보와 일치했고, 이어 강제 옵션 없이 `xenova-vm reboot`를 실행해
25.57초 후 정상 결과를 받았다. A domid는 5에서 6으로 바뀌고 같은 manifest를 다시 로드했다.
B는 재부팅 중 heartbeat 15건·metrics 181건을 보냈고 실행 ID와 Xen unique ID가 유지됐다.

재부팅 뒤 45초씩 네 구간을 관찰했다. A는 heartbeat 92건, metrics 1,099건,
`app.lifecycle.WAITING` 3,689건을 보냈다. B는 heartbeat 92건과 metrics 1,101건을 보냈다.
게스트 콘솔에서 A의 DHCP 주소, carrier, MQTT 연결을 확인했다. Store 일시 관측 실패
1건과 회복 1건이 남았고, worker 종결 오류는 0이었다. Spring DB의 읽기 전용 2분 조회에서도
A 617건과 B 622건의 새 Zephyr metrics를 확인했다. 최초 장애의 정확한 트리거는 확보하지
못했다. 실차 장치 연결과 출력 인가는 이 검증 범위에 포함되지 않는다.
