# Zephyr Telemetry Publisher 2.0

Diagnostics Core의 실제 관측을 별도 v2 MQTT 연결로 발행하는 공통 모듈이다.
Core의 수집·주기 작업과 전송을 분리하며, 주행 계산·앱 건강 판정·복구 정책을 수행하지 않는다.
Network Starter가 Xen netfront → xenbr0 → 인증된 broker로 연결하는 참조 앱이다.
기존 `xm_zclient`, v1 주행/Agent 토픽과 제품 backend는 변경하지 않는다.

## 계약과 구성

- [첨부 원문 계약](contract/CONTRACT.md), [schema](contract/schemas/message.schema.json),
  [24개 metric catalog](contract/schemas/metric-catalog.json)가 wire 형식의 기준이다.
- `contract/`는 사용자 제공 ZIP의 원문이다. 그 안의 과거 HEAD·검증 보고서는 입력 자료이며
  현재 구현·실보드 시험 결과가 아니다. `SHA256SUMS`는 원본 파일을 검증한다.
- `src/model.c`, `src/writer.c`: allocation 없는 RTOS 독립 모델·4096-byte 직렬화.
- `src/adapter.c`: 고정 Zephyr 4.4 Diagnostics 구조체의 실제 필드 매핑.
- `src/worker.c`: MQTT client 한 개를 소유하는 단일 thread, cadence·큐·ACK·종료.
- `src/socket_transport.c`: 표준 Zephyr MQTT 프로토콜 + 전체 deadline을 유지하는 TCP I/O.
- `src/bootstrap.c`: 검증된 인스턴스 주입값의 제한된 decoder. 자체로 신원을 인증하지 않는다.
- `tools/reference_subscriber.py`: 제품 backend와 독립된 읽기 전용 수신·계약 검사 도구.

앱은 Diagnostics와 이 디렉터리를 `ZEPHYR_EXTRA_MODULES`에 넣고
`CONFIG_XENOVA_DIAGNOSTICS=y`, `CONFIG_NET_TCP=y`, `CONFIG_XENOVA_TELEMETRY=y`를 사용한다.
기존 Zephyr의 JSON/MQTT/socket 기능을 사용하며 외부 target runtime 의존성은 추가하지 않는다.
공유 firmware에는 VM UUID·broker 주소·password가 들어가지 않는다.

`xt_stop`은 worker의 socket을 다른 thread에서 닫지 않고 종료를 요청한 뒤 join한다.
stop/start의 TCB 재사용을 직렬화하며, timeout이면 아직 종료를 확인하지 못한 상태다.
불확정 socket close 오류는 보존하고 그 descriptor나 Publisher를 재사용하지 않는다.
기한·부분 초기화·공유 전송 정리의 계약은 [Zephyr runtime](../xen-runtime/README.md)을 따른다.

## Core → wire 대응

| 실제 원천 | v2 필드·의미 |
|---|---|
| own-domain Store `/vm/<UUID>` | `vm_id`; display name/domid/고정 Kconfig 값으로 대체하지 않음 |
| 실행별 bootstrap `boot_id` | guest 부팅 공용 namespace. TCP 재연결·Publisher 재시작에서는 유지 |
| bootstrap `publisher_id` | Publisher 실행 session. 재연결에서 유지, 재시작에는 새 host-issued token 필요 |
| bootstrap host/execution/receipt | `host_provisioned`의 세 ID. 없는 경우 모두 빈 주입값 → wire `unbound`/null |
| `k_uptime_get()` 전후 | collection start/end, 전송 직전 sent uptime. 다른 장치 clock과 빼지 않음 |
| `collection_elapsed_us`, `collection_rc` | Core 수집 경과. 직렬화/전송/hook 비용과 별개. 실패는 null·원 rc |
| heap allocated/free/peak | `memory.kernel_heap.{used_bytes,free_bytes,high_water_bytes}`, bytes/u64 문자열 |
| 제공되지 않는 CPU runtime | `cpu.system.percent`, unsupported/null. heap free를 전체 VM RAM으로 바꾸지 않음 |
| network tx/rx | `network.netfront.{tx_packets,rx_packets}`, packets/raw_counter. 원본 32bit 유지 |
| network tx_drop/rx_drop/pool_busy/protocol_errors | 같은 suffix의 `network.netfront.*`, count/raw_counter, 32bit |
| 실제 `net_if_is_up` / `net_if_is_carrier_ok` | 각각 `network.interface_up`, `network.carrier`, boolean. IP/MQTT ready가 아님 |
| 실제 Zephyr interface index/link address/IPv4 | inventory `if<index>`, MAC·주소. network label과 같은 identifier |
| timing loop_seq/in_flight | `loop.completed_total`, `loop.in_flight`. begin/publish가 완료 수를 늘리지 않음 |
| 조회 후 local clock − last_completion_us | `loop.last_completed_age_us`. 완료 없음은 no_sample/null |
| deadline_miss_count / skipped_release_count | `loop.deadline_miss_total`, `loop.skipped_release_total`, count |
| interval.last / last_jitter_us | `loop.last_interval_us` / signed `loop.last_jitter_us`. 첫 interval은 no_sample |
| interval/absolute_jitter/lateness/elapsed | `loop_timing` 분포 네 종류, task epoch 누적 32 buckets·count/min/max/sum |
| 분리한 count/sum/histogram 포화 flag | 해당 불확실성과 null을 보존. p95/p99는 bucket 상한이며 정확한 분위수가 아님 |
| Core 원 발생 event_seq/uptime/stage/code | `event_id=vm/boot/event_seq`. 같은 실패 boot 사건의 두 ring 저장을 dedup |
| Core thread rows / threads_seen / threads_rc | freeze한 페이지, snapshot-local row ID, 전체 원천 truncation·개별 stack rc 보존 |
| Publisher가 직접 센 실패·drop·ACK timeout | catalog의 `publisher.*` 5개 counter. 제출/PUBACK/독립 수신은 서로 다른 사실 |

모든 u64/i64는 십진 문자열이다. raw32 wrap을 64bit 누적으로 위장하지 않는다.
현재 xenvm adapter에는 신뢰 가능한 UTC source가 없어 `sent_at`·source·uncertainty는 null,
quality는 unavailable이다. 향후 동기화된 UTC provider 연결은 원 품질·오차 근거와 함께 검증한다.
없는 loop는 not_registered이며 CPU·thread runtime·영구 crash 이력은 추정하지 않는다.

한 Core task의 `task_epoch`는 guest boot namespace와 고정 init origin에서 정한다.
Publisher만 다시 시작한 경우 실행 중인 task를 새로 초기화한 것처럼 표시하지 않는다.
Core가 관측하는 task는 static lifetime 한 개이며, 재초기화는 writer/reader를 중지한 조건에서 한다.

## 인스턴스 주입과 신원

Network Starter는 기존 공용 XenStore session에서 자신의 VM link와 bootstrap을 한 transaction으로
읽는다. `/local/domain/<domid>/data/xenova-telemetry/bootstrap`은 dom0 소유, 해당 guest read-only다.
다른 VM UUID, 불완전 host binding, 자격증명 누락, 재사용 session token이면 송신을 보류한다.
xenvm의 timer test RNG는 신원·password 생성에 사용하지 않는다.

dom0의 [provision-xenova-telemetry.py](../../../scripts/provision-xenova-telemetry.py)는 기존
Manager의 `execution-context`와 실제 Xen unique ID를 전후 확인한다. broker·서비스·VM 생명주기는
변경하지 않는다. 기존 `libxenstore`를 메모리로 호출하므로 비밀을 `xenstore-write` argv에 싣지 않는다.
외부 root/xl 실행 전체와 원자적으로 묶인 변경이라고 주장하지 않으며, 전후 신원이 바뀌면
private receipt를 uncertain으로 남기고 성공으로 보고하지 않는다.

준비된 private 디렉터리의 credential JSON에는 아래 필드만 둔다. 실제 값은 Git·터미널·로그에
출력하지 않고 안전한 파일 입력으로 준비한다. username은 VM의 full UUID다.

```json
{
  "vm_id": "<actual VM UUID>",
  "broker_ipv4": "<trusted internal broker IPv4>",
  "port": 18883,
  "username": "<same actual VM UUID>",
  "password": "<instance-specific printable password>",
  "trusted_internal_plaintext": true
}
```

디렉터리는 root-owned 0700, credential/receipt는 0600이어야 한다. protocol decoder와 맞추기 위해
password는 따옴표·역슬래시·공백 없는 printable ASCII 1..128 bytes를 사용한다.
운영 broker 계정·ACL 발급은 별도 작업이다. untrusted/TLS-required 경로는 이 TCP adapter가
거절하며 익명·평문 fallback을 하지 않는다. 내부 평문 시험을 TLS 보안 수락으로 보고하지 않는다.

승인된 dom0 시험 창에서 실제 UUID와 조회한 실행 문맥을 선택한다.

```sh
read -r -p '시험 VM UUID: ' VM_ID
xenova-vm execution-context "$VM_ID" --json
read -r -p '조회 결과의 host_boot_id: ' HOST_BOOT_ID
read -r -p '조회 결과의 execution_id: ' EXECUTION_ID
read -r -p '0600 credential JSON 절대 경로: ' CREDENTIALS
read -r -p '0700 디렉터리의 새 receipt 절대 경로: ' RECEIPT
python3 /path/to/provision-xenova-telemetry.py \
  --vm-id "$VM_ID" --credentials-file "$CREDENTIALS" --receipt "$RECEIPT" \
  --expected-host-boot-id "$HOST_BOOT_ID" --expected-execution-id "$EXECUTION_ID"
# 계획의 실행 문맥 확인 후 같은 인자에 --execute를 붙인다.
```

같은 receipt 재시도는 같은 ID를 사용한다. 새 guest 실행에는 새 receipt를 사용한다.
같은 guest에서 Publisher만 재시작하면 `--previous-receipt`로 직전 applied receipt를 명시하고
새 receipt를 만든다. boot ID를 유지하고 Publisher ID만 교체한다. 옛 receipt를 새 host/실행에
재사용하거나 uncertain 상태를 새 키로 무조건 우회하지 않는다.

## Worker·전송·종료

| 항목 | 실제 구현 |
|---|---|
| MQTT | 3.1.1, CleanSession=1, keepalive 30s, 독립 client ID `xt2-<full UUID without hyphens>` |
| QoS/retain | inventory 1/true; heartbeat·metrics 0/false; events·snapshot 1/false |
| 주기 | heartbeat 2s; loop progress 1s; heap/net 2s; timing/publisher 5s. inventory의 metrics_period_ms는 heap/net 기본 2s |
| live 데이터 | offline queue 없음. 연결 후 새 수집, 2초 초과 표본 거절. family별 전송 due만 유지 |
| 사건 | RAM 32개, oldest eviction·drop 집계. 원 event ID/발생시각 유지 |
| 상세 snapshot | 명시 요청만, freeze 1개, threads 최대32 rows/8 parts·4 rows/part, history 8 rows/part, 30s 만료 |
| QoS1 | in-flight 1개, 원 bytes/ID 고정. 2s ACK timeout에서 연결 닫고 재연결; 새 연결의 사건은 원 event ID·새 envelope |
| I/O | nonblocking connect/poll/send, MQTT 호출 전체 250ms budget, 부분 send·0-byte send·iovec 전체 deadline 처리 |
| backoff | worker에서 1→2→4→8→10s. Core·시험 loop가 기다리지 않음 |
| 대역폭 | payload+topic+framing 예약 기준 16KiB/s, burst 8KiB |
| 종료 | 기존 quiesce가 `xt_stop(deadline)` 호출 후 netfront를 닫음. 이 client만 abort, 남은 큐 유실 기록 |

수신측 PUBACK은 ACL 허용·소비자 전달 증명이 아니다. 독립 subscriber와 늦은 retained inventory
수신을 별도로 확인한다. 현재 연결에서 ACK를 기다리는 bytes는 수정하지 않는다.
재접속 시 live 표본을 replay하지 않으며 사건만 bounded RAM 이력으로 다시 봉투화한다.

기존 namespace의 로컬 명령:

```text
xenova telemetry
xenova telemetry history
xenova telemetry threads
```

첫 명령은 비밀 없는 상태·제출·ACK·source gap·queue drop·비용 counter를 읽는다.
나머지는 발행을 요청하며 threads만 intrusive stack scan을 수행한다. MQTT 원격 명령 수신은 없다.
`source_missed`는 exporter cursor 사이의 source gap, coverage의 `missed_before_capture`는 해당
RAM history capture에 남지 않은 원 사건 수다. 어느 것도 실제 subscriber 손실 수와 같다고 가정하지 않는다.
`collect_us`/`serialize_us`/`send_us` 누적 경과는 구간이 다르고 pure CPU/hook 비용이 아니다.

## 검증

WSL 정식 소스에서:

```sh
python3 -B -m unittest discover -s platform/tests -p test_xenova_telemetry.py -v
bash platform/scripts/check-xenova-platform.sh
```

첫 명령은 원본 합성 Golden 30 정상·30 거절·11 sequence와 **production C**가 생성한
별도 bytes를 각각 검사한다. C 모델은 ASan/UBSan, 큰 정수·음수·raw32 wrap·ring/queue loss·
null·포화·페이지·실패 rc를 포함한다. Python bootstrap 시험은 신원 변경·멱등·비밀 제외·uncertain을 확인한다.

`tests/native-app`은 실제 Core·Publisher·Zephyr MQTT를 빌드한다. runtime-only bootstrap과
독립 instance-id를 시험 harness가 주입하며 netfront 대신 native TAP을 쓴다. netfront counter는
native에서 unsupported다. 고정 빌드 환경의 CMake/Python 경로는 [빌드 가이드](../../../../docs/platform/build-guide.md)를 따른다.

```sh
# 기존 고정 Zephyr/host 도구 환경에서 새 출력 디렉터리를 사용한다.
cmake -S platform/guests/zephyr/telemetry/tests/native-app -B /absolute/new-native-build \
  -GNinja -DBOARD=native_sim/native/64 -DZEPHYR_TOOLCHAIN_VARIANT=host
cmake --build /absolute/new-native-build -j 4
sudo unshare --net -- python3 -B \
  platform/guests/zephyr/telemetry/tests/run_native_acceptance.py \
  --binary /absolute/new-native-build/zephyr/zephyr.exe \
  --tools-root /absolute/private-test-mosquitto --output /absolute/new-results --execute --extended
```

tools-root에는 승인 후 저장소 밖에 추출한 Ubuntu Mosquitto 2.0.18의 `root/usr/{sbin,bin,lib}`를
사용한다. harness는 host 기본 network namespace에서 실행을 거절한다. A/B 실제 bytes·원천 대조,
retained history, reporter만 생존, 인증·ACL, broker 단절, native process/Publisher 재시작, 종료를 검사한다.
private credential 디렉터리는 결과의 sibling으로 분리되며 증거 archive에 포함하지 않는다.
native process replacement는 실제 Xen reboot 수락이 아니다.

독립 수신기 단독 실행은 `--credentials`에 private subscriber JSON (`username`, `password`)을
사용한다. `--bindings-file`은 검증된 VM/boot/Publisher ID 목록이며 broker 도착 순서로 만들지 않는다.
결과에는 실제 payload base64·topic·QoS·retained delivery·수신 단조 시각·계약 결과가 남는다.

ARM 부분 빌드는 정규 도구의 `--telemetry on|off`, `--diagnostics on|off`, `--timing-exercise`를
조합한다. Publisher on에는 Diagnostics가 필요하다. ELF/DTS·실제 config·옵션·전체 source hash를
영수증에 남긴다. 실보드 netfront 수신, 실제 앱 hook, pinning/부하 효과, 소비자 저장은 별도 층으로 검증한다.

## 소비자 인계

[원문 소비자 인계표](contract/docs/CONSUMER_HANDOFF.md)와 catalog를 함께 사용한다.
`xenova/v2/vms/+/telemetry/+`를 읽고 closed schema 2.0을 v1과 분리한다.
long composite ID, u64/i64 문자열, nullable UTC, metric+labels별 최신값, raw32 reset/wrap,
task epoch 누적 histogram과 근사 상한, section 실패/null, source truncation과 전송 page 누락을 보존한다.
Publisher heartbeat를 app heartbeat로 대체하거나 보고 생존을 복구/차량 정상으로 승격하지 않는다.
제품 backend·HTTP·화면·Recovery Worker의 저장·판정·표시는 해당 담당자의 변경과 수락 대상이다.
