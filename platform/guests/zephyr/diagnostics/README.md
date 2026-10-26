# Zephyr Diagnostics Core

Zephyr 펌웨어의 실행 상태를 통신과 독립적으로 관측하는 공통 모듈이다.
부팅 단계·오류 이력, 스레드·스택, kernel heap, 앱이 제공하는 네트워크 관측과
임의 주기 작업의 시작·완료를 고정 크기 메모리에 기록한다.
MQTT, 주행 알고리즘, 제어권 판단, VM Agent, 영구 crash 저장소를 구현하지 않는다.

## 연결과 조회

앱의 `ZEPHYR_EXTRA_MODULES`에 이 디렉터리를 넣고
`CONFIG_XENOVA_DIAGNOSTICS=y`로 빌드한다. `network-starter-app`이 실제 연결 예제다.
앱은 자신의 초기화 결과를 `xdiag_boot(stage, rc)`, 처리 가능한 오류를
`xdiag_error(stage, rc)`로 전달한다. `stage`는 종료 NUL 포함 32바이트 이내다.
오류 메시지에 자격증명을 넣지 않는다.

Network Starter의 기존 `xenova` 명령 아래에서 조회한다.

```text
xenova diag
xenova diag threads
xenova stats
```

`diag`는 로컬 snapshot JSON을 출력한다. `diag threads`만 스레드 목록과 스택을
분석한다. 기존 `stats`의 netfront 카운터와 `hook` 종료 시험 명령은 유지한다.
두 조회 모두 네트워크 시작·재연결·VM 제어를 수행하지 않는다.

C 소비자는 `xdiag_snapshot_get(&snapshot, include_threads)`를 호출한다.
큰 snapshot은 자동 변수보다 static 또는 명시적으로 예약한 메모리에 둔다.
전체 함수 실패와 각 section의 `rc`를 모두 확인한다. `rc != 0`인 section의
숫자는 유효하지 않다. 콘솔에서는 해당 값이 `null`이다.

| 상태 | 의미 |
|---|---|
| `ok`, rc=0 | 이번 조회에서 수집됨. 건강·앱 준비 완료 판정은 아님 |
| `unsupported`, -ENOTSUP | 해당 빌드/API에서 지원하지 않음 |
| `not_registered`, -ENOENT | 네트워크 provider 또는 관측할 루프가 등록되지 않음 |
| `not_requested`, -EAGAIN | 비싼 스레드 분석을 요청하지 않음 |
| `truncated`, -ENOSPC | 최대 스레드 수 초과. 반환된 행은 유효하며 `seen`은 전체 수 |
| `scan_limit`, -E2BIG | 개별 스택이 스캔 제한보다 커 사용량을 수집하지 않음 |
| `collection_failed`, 기타 errno | 실제 오류. 원래 `rc`를 보존하며 0으로 대체하지 않음 |

전체 호출은 잘못된 포인터에 `-EINVAL`, ISR 호출에 `-EWOULDBLOCK`,
진단 잠금 20ms 대기 실패에 `-EBUSY`를 반환한다. 콘솔 출력은 별도 잠금으로
직렬화하고 이미 출력 중이면 즉시 `-EBUSY`를 반환한다.
스레드 스캔 및 출력 전체에 실시간 deadline을 보장하지 않는다.

## 실제 관측 범위

- 부팅·오류: 초기화 결과와 호출자가 보고한 오류를 각각 최신 8개까지 보존한다.
  전체 발생 수와 남아 있는 이력 수는 다르다. 이력은 이번 펌웨어 실행의 RAM이며
  reboot 뒤에는 남지 않는다. U-Boot·Xen·Zephyr 초기 kernel 로그와 fatal/panic은
  기존 UART/PV 콘솔 및 dom0/PC 수집 경로로 확보한다. fatal handler를 교체하거나
  기록된 오류가 없다는 이유로 crash가 없었다고 판단하지 않는다.
  각 원 사건에는 발생 시점의 guest-boot-local `event_seq`를 부여한다. 실패한 boot 사건이
  두 ring에 들어가도 같은 ID이며, `history.event_sequence`는 중복 없는 원 발생 수다.
  sequence 소진은 -ERANGE로 거절하며 ID를 wrap하지 않는다.
- 시각: `uptime_ms`는 Zephyr uptime이다. 타이밍 API는 64비트 하드웨어 사이클을
  런타임 주파수로 변환한 local microsecond clock이며 epoch는 uptime/UTC와 다르다.
  `frequency_hz`는 `sys_clock_hw_cycles_per_sec()`의 실제 값이다.
  snapshot은 여러 provider의 수집 구간을 포함하며 원자적인 동시 관측이 아니다.
  `collection.elapsed_us`는 JSON 출력 시간을 제외한다.
- 스레드: 이름·상태 문자열·우선순위와 스택 크기·미사용 바이트를 조회한다.
  상태 문자열은 Zephyr의 표시용 문자열이며 실행 중인 CPU를 뜻하지 않는다.
  `state_flags`는 고정 Zephyr 버전의 raw flags이며 0일 때 상태 문자열은 비어 있을 수 있다.
  긴 이름은 `name_truncated=true`로 명시한다.
  스택 사용량은 fill pattern의 high-water 관측으로 현재 SP나 호출 스택이 아니다.
  Thread Analyzer와 같은 `k_thread_stack_space_get()`를 사용하되, 그 모듈이
  실패 시 `unused=0`으로 바꾸는 경로를 거치지 않아 오류를 보존한다.
  수명이 변하는 thread pointer를 잠금 밖에 보관하지 않고 `k_thread_foreach()`에서
  제한된 행을 수집한다. 이 동안 인터럽트 지연이 발생할 수 있으므로 성능 측정 중
  `diag threads`를 호출하지 않는다. 상세 stack 조회는 CPU 수집과 분리한다. 일반 주기 CPU 수집은 stack scan을 호출하지 않는다.
- heap: `k_malloc` 계열이 사용하는 **kernel system heap만** 관측한다.
  Zephyr 4.4의 kernel shell과 동일한 `_system_heap.heap`을 사용하고 allocator의
  spinlock 아래에서 `sys_heap_runtime_stats_get()`를 호출한다. 이 부분은 고정 버전의
  내부 구조에 의존하는 adapter다. Zephyr 변경 시 다시 검토·빌드·실행 시험한다.
  libc heap·static RAM·네트워크 packet pool을 전체 free RAM으로 합산하지 않는다.
- 네트워크: 모듈은 Xen을 알지 못한다. 앱이 한 번 등록한 bounded provider를 호출한다.
  Network Starter는 실제 `xnet_stats_try()`와 `net_if` up/carrier 상태를 제공한다.
  system workqueue를 packet lock에 대기시키지 않고 경합은 오류로 보존한다.
  초기화 실패 시 해당 errno를 반환한다. link up은 DHCP·IP·broker 준비와 다르다.
  원본 netfront의 32비트 카운터는 64비트 snapshot 필드로 전달되지만 wrap을 복원하지 않는다.

provider와 관측할 루프 객체는 static lifetime이어야 한다. 등록 후 임의 교체하지 않는다.
실제 Xen 실행 UUID·host_boot_id·execution_id·펌웨어 해시는 수집자가 실행 문맥과 함께
기록해야 한다. 이 모듈은 임의 UUID를 만들어 Xen의 신원인 것처럼 사용하지 않는다.

## 시스템·스레드 CPU와 메모리 영역

`resources.c`가 system workqueue에서200ms마다 짧은 계측을 소유한다. 조회와 Publisher는
cache를 복사할 뿐 차분 기준을 바꾸지 않는다. 원천 구간·원 누계·계측 비용은 내부 snapshot에
남기고 외부에는 [Telemetry 계약](../telemetry/contract/CONTRACT.md)의 결과만 제공한다.

CPU는 고정 Zephyr UP runtime statistics를 사용한다. 분모는 CPU API의 non-idle+idle,
시스템 분자는 non-idle이며 스레드별 분모도 같다. IRQ와 Xen 비실행 시간의 귀속은 guest
계측기의 한계다. pCPU 점유율 또는 계산의 순수 CPU 시간으로 해석하지 않는다.
원천32비트 clock의 half-wrap보다 긴 공백, reset/역전, 1초 이상 오래된 cache는
불연속/null이다. 첫 표본·분모0은 no_sample이다. 새 Publisher는 기존 guest 계측을 초기화하지 않는다.

생성 hook·thread monitor로 guest-boot-local 수명 ID를 유지한다. registry64개,
반환행32개이며 목록이 잘리면 전체 seen과 잘림을 남긴다. 이름·주소 재사용을 같은 수명으로
합치지 않고 raw pointer를 발행하지 않는다. 스레드 접근은 monitor 잠금 안에서 끝난다.

| 영역 | 소유/실제 API | 포함·중복 경계 |
|---|---|---|
| kernel system heap | `_system_heap`/sys_heap stats | 기존 used/free/peak. libc heap·전체 RAM 아님 |
| net_rx_data | 고정 rx_bufs/net_buf 가용 block | data payload만. descriptor·정렬/관리 비용 제외 |
| net_tx_data | 고정 tx_bufs/net_buf 가용 block | 같은 범위. static RAM 안의 예약이므로 RAM과 합산 금지 |
| thread stack | 명시적 `k_thread_stack_space_get` | 예약 크기와 high-water. 현재 SP/호출 stack 아님 |

`CONFIG_XENOVA_DIAGNOSTICS_CPU=n` 또는 pools 비활성 구성의 capability/표본 상태를
구별한다. 기능 off를 수집 성공·0%로 표시하지 않는다. provider의 현재 실패도 기능 미지원으로
바꾸지 않는다. [실행별 보존과 조회](../telemetry/README.md#내부-진단과-실행별-보존)는 dom0의
별도 책임이며 Core의 RAM 이력을 guest 비휘발 저장소로 바꾸지 않는다.

## 재사용 가능한 주기 작업 계측

```c
static struct xdiag_loop measured;

/* 실행 전 한 번: 원하는 주기와 완료 deadline. 20ms는 예시다. */
int rc = xdiag_loop_init(&measured, 20000, 20000);
if (rc == 0) {
    rc = xdiag_loop_watch(&measured);
}
/* rc를 확인한 뒤, 예정 주기에 실제 작업을 실행하는 곳에서: */
rc = xdiag_loop_begin(&measured);
if (rc == 0) {
    application_step();
    rc = xdiag_loop_end(&measured);
}
```

이 코드는 hook 사용 위치의 예시이며 스케줄러 자체가 아니다. 동일 루프에는 한 writer만
사용하고, 실패한 begin 뒤에 작업을 정상 계측한 것처럼 end를 호출하지 않는다.
측정 구간은 begin과 end가 읽는 clock 사이이며 hook 자체의 전체 비용과 다르다.
완료 hook은 sleep·의도적인 대기 전에 호출한다.

| 항목 | 정의 |
|---|---|
| `loop_seq` | 정상 end 호출로 확정한 계산 완료 횟수. 시작 수·heartbeat seq와 다름 |
| `in_flight` | 시작했지만 완료하지 않은 작업 존재 |
| `interval` | 연속한 실제 시작 시각 차이. 첫 작업에는 표본 없음 |
| `last_jitter_us` | 마지막 interval − period. 음수도 보존 |
| `absolute_jitter` | abs(interval − period). 양·음 지터 상쇄 방지 |
| `schedule_lateness` | 실제 시작 − 해당 작업의 예정 release |
| `step_elapsed` | 실제 end − begin. IRQ·선점·Xen descheduling도 포함 |
| `deadline_miss_count` | end > 예정 release + deadline인 완료 작업 수. 같은 작업은 한 번만 셈 |
| `skipped_release_count` | 완료보다 엄격히 앞선 미실행 예정 release 수 |
| `last_completed_age_us` | 마지막 완료부터 조회까지 경과. 완료 표본이 없으면 null |

예정 release는 init의 origin에 고정한다. 초과 후 `now + period`로 원점을 지우거나
밀린 주기를 한꺼번에 재실행하지 않는다. 예를 들어 T=D=20ms에서 20ms 예정 작업이
85ms에 끝나면 deadline miss 1개, 40/60/80ms 누락 3개를 기록하고 다음은 100ms다.
늦게 깨어난 작업은 원래 예정 release에 대한 lateness를 기록한다.
정확히 마감 시각에 끝난 작업은 miss가 아니다.

각 지표는 누적 count/min/max/sum과 32개 logarithmic histogram을 가진다.
`p95_upper_us`·`p99_upper_us`는 **버킷 상한 근사치**이며 정확한 분위수가 아니다.
버킷은 0, 1, 2..3, 4..7, …이고 마지막은 2^30µs 이상인 열린 구간이다.
열린 구간·포화 시 분위수는 null, 표본 없으면 value 전체가 null이다.
합계 포화 시 평균도 null이다. 1초 50개 표본의 p99를 장기 통계처럼 해석하거나,
구간별 p99를 평균해 전체 p99로 사용하지 않는다.

metric의 `count_saturated`, `sum_saturated`, `histogram_saturated`는 포화 원인을 구분한다.
기존 `saturated`는 그 합성 flag로 유지한다. 초기 origin은 `timing.origin_us`로 보존한다.

순수 C core는 allocation·I/O·로그·Zephyr 의존성이 없다. Zephyr hook은 짧은
spinlock으로 timestamp와 누적값을 보호한다. 스레드·heap·네트워크 수집과
문자열 직렬화는 이 hook에서 실행하지 않는다.

## 검증과 성능 비교

`platform/tests/test_xenova_diagnostics.py`는 production core를 cc와 ASan/UBSan으로
컴파일해 임의 주기, 양·음 지터, 완료/미완료, overrun·누락, 경계·오류 이력을 검사한다.
`tests/zephyr-app`은 실제 Zephyr native_sim에서 timer 변환, 스레드·heap 증감,
provider 실패와 콘솔 JSON을 검사한다. native_sim 수치는 실보드 지터가 아니다.

```bash
python3 -B -m unittest discover -s platform/tests -p test_xenova_diagnostics.py -v

# 기존 고정 Zephyr/호스트 빌드 도구 환경에서, 새 출력 경로를 사용한다.
# ZEPHYR_BASE와 기존 도구의 Python 경로는 빌드 가이드대로 지정한다.
cmake -S platform/guests/zephyr/diagnostics/tests/zephyr-app \
  -B /absolute/new-diagnostics-test -GNinja \
  -DBOARD=native_sim/native/64 -DZEPHYR_TOOLCHAIN_VARIANT=host
cmake --build /absolute/new-diagnostics-test -j 4
/absolute/new-diagnostics-test/zephyr/zephyr.exe
```

동일 디렉터리의 `testcase.yaml`에는 기본·수집 비활성·스레드/스택 제한의
세 native_sim 시나리오를 둔다. 비활성에서는 unsupported/null, 제한에서는
truncated/scan_limit을 검사한다. 새 도구 설치를 요구하지 않는다.

Network Starter의 정규 부분 빌드 도구에 다음 옵션을 제공한다.
기본 빌드의 주기 시험은 꺼져 있다. 기존 빌드 입력 인자는 빌드 가이드를 따른다.

| 옵션 | 결과 |
|---|---|
| `--diagnostics on` | 공통 모듈·로컬 조회 활성화. 기본값 |
| `--diagnostics off` | 진단 모듈 제거. 기존 network/shutdown 유지 |
| `--timing-exercise` | 동일한 20ms 고정 산술 작업을 추가. 주행 출력 없음 |

시험 빌드의 `xenova exercise`는 두 variant에 공통으로 남는 외부 counter observer다.
완료 수·interval 표본 수·최대 절대 지터·시작 지연·hook 포함 job 경과를 조회한다.
계측 off에서도 이 비교용 observer는 남으며, 그 비용은 양쪽에 공통이다.
`xenova diag`의 측정 구간과 job-with-hooks 구간은 다르다.
순수 hook 비용의 분리 시험은 별도의 paired probe로 수행한다.

각 빌드는 새 출력 디렉터리를 사용한다. 도구는 모듈·앱 입력과 옵션·실제 config·
ELF load segment·산출물 해시를 기록하고 frozen 16MiB/1-vCPU/GICv2 프로필을 검사한다.
현재 소스의 개발 빌드를 기존 불변 게시본에 덮어쓰지 않는다.

실보드에서는 먼저 카운터 주파수와 uptime 간 경과 일치, 계산 완료/중단의 구분,
counter sample 수, 계측 on/off RAM·스택·주기 지연·hook 비용을 확인한다.
스레드 분석과 콘솔 출력은 타이밍 측정 구간 밖에서 한다.
정확한 hook 비용은 호출 전후를 외부의 동일한 counter probe로 측정하며
`step_elapsed`를 hook overhead로 보고하지 않는다.

그 뒤 동일 바이너리·펌웨어·입력·주기·broker 설정에서 pinning 미적용/적용 ×
Debian 유휴/부하 네 조건을 비교한다. 실제 Xen hard affinity 관측도 함께 남긴다.
기능 수락과 주기·통신 성능 수락은 별개다. 이 모듈 도입만으로 실제 주행 성능,
MQTT 수신 지연, IRQ·메모리 격리 또는 실시간 기한 보장을 주장하지 않는다.

[Telemetry Publisher](../telemetry/README.md)는 이 Core를 사용하는 별도 adapter다.
Core에 MQTT 의존성을 넣거나 이 로컬 JSON을 wire 계약으로 간주하지 않는다.
`agent.heartbeat`, `app.heartbeat`, 루프 완료는 각각 다른 근거이며
통신 스레드만 살아 있다고 멈춘 계산을 정상 진행으로 보고해서는 안 된다.
