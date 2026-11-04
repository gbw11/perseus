# xenova-agent — VM 에이전트 (heartbeat·inventory·metrics·자체 상태·데모 명령)

Linux 게스트 안에서 돌며 [MQTT 계약 v1](../../../../docs/contracts/mqtt-contract-v1.md)의 heartbeat(MQTT-01)·inventory(MQTT-02)·metrics(MQTT-03)를 dom0 Mosquitto 로 보냅니다.
봉투·토픽·QoS·자동 재연결은 [mqtt-client](../mqtt-client/README.md)의 `xenova_mqtt`가 맡고, 이 패키지는 수집과 주기·수명을 정합니다.
heartbeat 는 메인 스레드, inventory·metrics 는 별도 스레드(Reporter)에서 돌아 수집이 느리거나 실패해도 heartbeat(복구 Worker 의 생존 판정 근거)가 밀리지 않습니다.
세 번째 스레드는 에이전트 자신의 상태(Reporter 가 돌고 있는지, 실패 누계)를 [v2 Telemetry](#자체-상태-v2-telemetry) 형식으로 보냅니다.
네 번째 스레드는 시연용 [데모 명령](#데모-명령-mqtt-0405)(MQTT-04)을 받아 처리하고 결과(MQTT-05)를 보냅니다. 기본은 꺼져 있습니다.

## heartbeat (MQTT-01)

| 항목 | 값 |
| --- | --- |
| 토픽 | `xenova/v1/vms/{vm_id}/heartbeat`, `type: agent.heartbeat`, QoS 1 |
| payload | `{}`. 봉투의 `seq`·`boot_id`·`agent_instance_id`·`sent_at`만 의미가 있음 |
| 주기 | 5초. 시작 직후 1회, 이후 단조 시계 기준 `start + n*5s` (발행 지연이 누적되지 않음) |
| 끊김 | 브로커와 끊긴 틱은 보내지 않고 건너뜀. 재연결은 클라이언트가 하고 연결되면 다음 틱부터 재개 |
| 발행 예외 | 봉투·토픽·라이브러리 사용 오류처럼 고정된 문제라 보고, 연속 3번이면 종료 코드 1로 끝남. systemd 가 로그에 남기고 2초 뒤 다시 띄움 |
| 연결 거부 | 잘못된 계정처럼 브로커가 연결을 거부하면 클라이언트가 경고만 남기고 재시도하므로 에이전트는 "연결 없음"으로만 본다. 영구 거부를 구분하려면 공용 mqtt-client 가 거부 사유를 드러내야 하며 별도 후속 작업이다 |
| 상태 | 에이전트는 상태를 선언하지 않음. 살아 있는지는 받는 쪽이 수신 시각으로 판정 |
| 권한 | root uid 지만 capability 전부 제거, `ProtectSystem=strict`·`PrivateTmp` 등으로 쓰기·권한 상승 차단. 전용 사용자는 instance-id(root 0600) 소유권을 플랫폼과 정한 뒤 |

앱·프로세스 상태는 heartbeat에 싣지 않고 metrics(MQTT-03)의 `processes[]`로 보냅니다. Zephyr 는 이 에이전트의 대상이 아닙니다.

## inventory (MQTT-02)

| 항목 | 값 |
| --- | --- |
| 토픽 | `xenova/v1/vms/{vm_id}/inventory`, `type: agent.inventory`, QoS 1, retained (브로커가 마지막 값을 보관) |
| 보내는 때 | 브로커에 연결될 때마다(시작·재연결) 한 번, 그 뒤 60초마다 다시 읽어 바뀌었을 때만 전체. 재연결은 클라이언트의 연결 성공 횟수로 알아채 두 틱 사이의 짧은 끊김(브로커 재시작)도 놓치지 않음 |
| 내용 | `os{id, version_id, pretty_name}`(/etc/os-release), `kernel_release`(/proc/sys/kernel/osrelease), `hostname`, `interfaces[{name, mac, addresses[{address, family}]}]`(/sys/class/net, lo 제외, IPv4·IPv6), `agent_version`, `capabilities{commands, metrics}` |
| 필수값 | Spring 이 `os.id`·`kernel_release`·`hostname` 이 비면 거부하므로 읽지 못해도 `linux`·`unknown` 으로 채운다 |

## metrics (MQTT-03)

| 항목 | 값 |
| --- | --- |
| 토픽 | `xenova/v1/vms/{vm_id}/metrics`, `type: agent.metrics`, QoS 0 |
| 주기 | 2초(`XENOVA_AGENT_METRICS_INTERVAL`). `window_ms` 는 설정값이 아니라 직전 표본과의 실제 간격, 첫 표본은 `null` |
| `cpu_percent_allocated` | /proc/stat 합계. 할당된 vCPU 전체를 100% 로 정규화. steal 은 사용으로 세지 않음. 첫 표본은 `null` + `first_sample`, 누적 카운터가 되돌아간 표본은 `null` + `counter_reset`(그 값이 새 기준이라 다음 표본부터 정상) |
| `memory_used_bytes` / `memory_available_bytes` | /proc/meminfo `MemTotal − MemAvailable`, `MemAvailable` |
| `disk_read_bytes_total` / `disk_write_bytes_total` | /proc/diskstats 섹터 × 512, `labels.device`. /sys/block 의 디스크 단위만(파티션·loop·ram 제외). /sys/block 을 못 읽으면 파티션과 겹쳐 셀 수 있어 `null` + `read_failed` |
| `net_rx_bytes_total` / `net_tx_bytes_total` | /proc/net/dev, `labels.iface`. lo 제외 |
| `processes[]` | CPU 기준 상위 10개(`XENOVA_AGENT_TOP_PROCESSES`, 0~50) `{pid, name, cpu_percent, rss_bytes}`. `cpu_percent` 는 top 과 같은 1코어 기준이라 100 을 넘을 수 있음. 첫 표본은 빈 목록. top-N 목록이라 목록에 없는 프로세스가 끝났다는 뜻이 아님 |
| `process_collection` | `processes[]` 가 어떻게 모였는지(MQTT 계약 v1.5 3장). 빈 목록의 뜻을 가른다: `first_sample` / `ok` / `partial`(일부 stat 을 권한·형식 때문에 못 읽음. 첫 표본이어도 실패가 있으면 partial) / `read_failed`(/proc 목록을 못 읽음. 원인 종류를 `PermissionError(EACCES)` 처럼 예외·errno 이름으로만 남김) / `disabled`(0개 설정, /proc 을 훑지 않음). `{status, discovered, read_ok, vanished, failed, truncated, error}` 로 발견한 PID 수와 읽기 성공·사라짐(읽는 사이 끝난 프로세스, 정상)·실패 수, 잘림(top-N·크기 상한)을 같이 보낸다. 에이전트 로그는 상태가 바뀔 때만(`partial`·`read_failed` 는 WARNING) |
| 측정 실패 | 0 이 아니라 `value: null` + `unavailable_reason`(`first_sample`·`counter_reset`·`not_supported`·`read_failed`·`parse_error`·`device_not_found`) |
| 단위 | `percent`·`bytes` 만(Spring 이 그 밖의 단위면 메시지를 거부). load average 는 허용 단위가 없어 보내지 않음 |
| 끊김 | 끊긴 동안에도 수집은 이어 가지만 발행하지 않고 버림(QoS 0 실시간 표본을 쌓았다가 몰아 보내지 않음). 재연결 뒤 새 표본부터 |
| 크기 | 직렬화한 payload 가 60KB 를 넘으면 `processes[]` 를 뒤에서부터 줄임(Spring 은 봉투 포함 64KB 초과를 거부). 다 줄여도 넘으면 그 표본은 보내지 않고 ERROR 로그 |
| 오류 | 수집·발행 실패는 그 틱만 버리고 계속 돎 |
| 비용 | 프로세스마다 `/proc/{pid}/stat` 하나만 바이트로 읽음. RPi4 dom0(프로세스 134개) 실측: 수집 한 번 약 0.04초, 에이전트 전체 1코어의 2.7% |

## 자체 상태 (v2 Telemetry)

v1 heartbeat 는 에이전트 프로세스가 살아 있다는 뜻일 뿐이고, payload 를 넣을 수 없어(Recovery Worker 가 빈 payload 만 받음) 수집·발행 루프가 멈춘 것은 드러내지 못합니다.
그래서 `xenova_agent.telemetry` 가 Zephyr Publisher 와 같은 [Telemetry 계약 2.1](../../../guests/zephyr/telemetry/contract/CONTRACT.md) 형식으로 에이전트 자신의 상태를 보냅니다. 받는 쪽은 Zephyr 와 Linux 를 같은 구독·검증기로 처리할 수 있습니다.

| 항목 | 값 |
| --- | --- |
| 토픽 | `xenova/v2/vms/{vm_id}/telemetry/{inventory\|heartbeat\|metrics}`. 봉투는 v1 과 다른 계약 2.1 봉투(`schema_version`, 스트림별 `seq`, `message_id`, `context`, `clock`) |
| inventory | QoS 1 retained. 연결될 때마다, 그 뒤 60초마다 다시 읽어 바뀌면. 보내지 못한 것(크기 초과·제출 실패)도 다음 60초 갱신이나 재연결 때 다시 시도하며 heartbeat 주기마다 되풀이하지 않음. v1 inventory 를 계약 8장 형식으로 옮긴 것: `os{id, version, kernel_release}`, `hostname`, `firmware.measurement_profile = linux_agent`, `publisher{name: xenova.agent, version, metric_catalog}`, `interfaces`(계약 상한 4개·주소 4개까지. 넘치면 IPv4 있는 것 → 주소 있는 것 순으로 남기고 나머지(dom0 의 vif 등)는 `interfaces_omitted` 로 셈), `capabilities`(Zephyr 항목은 `unsupported`/`not_registered` 와 사유, `publisher_counters` 만 `available`), `limits` |
| heartbeat | QoS 0, 2초. `publisher_state`: `running` / `degraded`(Reporter 스레드가 죽었거나 3주기(6초) 동안 틱이 없거나 마지막 metrics 수집이 실패) / `stopping`(종료 직전 한 번). 수집 실패는 예외만이 아니라 반환값으로도 판정: 필수 원천(`cpu_percent_allocated`·`memory_used_bytes`·`memory_available_bytes`)의 `read_failed`·`parse_error`, 프로세스 목록 `read_failed`. `first_sample`·`counter_reset`·`partial`·`disabled`·디스크/네트워크 사유는 정상이며 실패한 표본도 v1 로 나간다. `last_collection_success_uptime_ms` 는 마지막 metrics 수집 성공 시각(부팅 뒤 ms). `collection_failures_total`·`publish_submit_failures_total` 은 Reporter·v1 heartbeat·이 발행기의 실패 합계, `connection_attempts_total` 은 CONNACK 수(거부 포함). `app_health_asserted` 는 항상 `false` |
| metrics | QoS 0, 5초(heartbeat 와 따로 예정해 `limits.metrics_period_ms` 대로), family `publisher`: `publisher.collection_failures_total`·`publish_submit_failures_total`·`payload_oversize_total`. `events_dropped_total`·`puback_timeouts_total` 은 세지 않으므로 `unsupported` + 사유 |
| 시계 | `sent_uptime_ms`·`collection.*_uptime_ms` 는 CLOCK_BOOTTIME. UTC 는 계약의 dom0 검증을 거치지 않아 `utc_quality: unavailable`(`sent_at` null). `context.binding` 은 `unbound`(Linux 게스트에는 host 가 주입한 실행 receipt 가 없음) |
| 끊김·크기 | 끊긴 동안 보내지 않고 쌓지 않음. `seq` 는 실제로 보낸(또는 제출에 실패한) 메시지만 씀. 봉투 포함 4096 바이트를 넘으면 보내지 않고 `payload_oversize_total` 로 셈 |
| 받는 쪽 | 계약 6장 lease: heartbeat 6초 안에 못 받으면 STALE. Recovery Worker 는 v2 를 읽지 않으므로 VM 재시작 근거가 아님. 브로커 ACL 의 v2 쓰기 허용과 백엔드 구독은 별도 작업이며, ACL 이 막으면 조용히 버려질 뿐 v1 에는 영향이 없음 |

## 데모 명령 (MQTT-04·05)

격리·자동 복구 시연용입니다. 필수 제품 기능이 아니므로 `XENOVA_AGENT_DEMO_COMMANDS=1` 인 에이전트만 실행하고, 꺼져 있으면 모든 명령을 `DEMO_DISABLED` 로 거절합니다. 규칙은 [MQTT 계약 3.2절](../../../../docs/contracts/mqtt-contract-v1.md#32-데모-명령-mqtt-0405)이 기준입니다.

| 항목 | 값 |
| --- | --- |
| 구독·발행 | 자기 VM 의 `xenova/v1/vms/{vm_id}/command`(QoS 1)만 받고, 결과는 `.../command-result`(QoS 1) |
| 처리 위치 | 수신 스레드는 큐에 넣기만 하고 검사·실행은 명령 스레드가 함. 부하는 별도 프로세스라 heartbeat·metrics 가 밀리지 않음 |
| 검사 | `type` = `agent.command`(다른 type 은 버림), `operation_id` 는 문자열, `expected_boot_id` = 이 부팅의 boot_id, `sent_at`·`expires_at`(유효 기간 300초 이하, 보낸 시각이 에이전트 시계보다 30초 넘게 앞서면 `CLOCK_UNTRUSTED`), retained 명령 거절, 파라미터는 정수만(bool·실수 거절) |
| `stress` | `cpu_workers` 0~4(워커당 프로세스 하나, nice 19), `memory_mib` 0~512(페이지마다 써서 실제 RSS, `oom_score_adj` 1000, MemAvailable−64MiB 이하만), `duration_s` 1~120. 한 번에 하나(`BUSY`). `RUNNING` → 시간이 지나면 `SUCCEEDED`, 부하 프로세스가 일찍 죽으면 `FAILED`. 시간이 지나면 남은 프로세스를 반드시 끝냄 |
| `agent_exit` | 도는 stress 를 끝내고 `ACCEPTED` 를 보낸 뒤, 그 발행의 PUBACK 을 확인해야만 종료 코드 3 으로 끝남. 유닛의 `RestartPreventExitStatus=3` 이라 다시 뜨지 않고 heartbeat 가 끊긴 채로 남아 Recovery Worker 의 heartbeat 끊김 판정 대상이 됨. 종결 결과는 `ACCEPTED` 하나라 연결이 없거나 PUBACK 이 늦어도 실패 결과를 따로 내지 않고 기다리며(연결되면 제출), 그동안 새 명령은 `AGENT_EXITING`. 진행 단계(`exit_pending` → `exit_acked`)를 기록에 남겨, PUBACK 전에 에이전트가 다시 시작하면 같은 `ACCEPTED` 를 다시 제출하고 종료하며, `exit_acked` 를 기록하지 못하면 종료하지 않고 다시 시도하고, `exit_acked` 뒤 수동으로 다시 시작하면 종료하지 않음 |
| 중복 | 같은 `command_id`·같은 요청(action·parameters·expected_boot_id·operation_id)은 다시 실행하지 않고 마지막 결과를 다시 보냄. 요청이 다르면 `IDEMPOTENCY_CONFLICT`. 실행 전에 `/run/xenova-agent/commands.json`(RuntimeDirectory, 부팅마다 비워짐)에 남겨 같은 부팅 안의 에이전트 재시작 뒤에도 반복하지 않음. 다른 부팅의 명령은 `BOOT_MISMATCH` |
| 기록 실패 | 실행 전 기록을 남기지 못하면 실행하지 않고 `STATE_UNAVAILABLE`. 기록 파일을 읽지 못하거나 손상됐으면 덮어쓰지 않고 모든 명령을 `STATE_UNAVAILABLE` 로 거절(에이전트 로그 ERROR). 항목마다 지문(64자리 hex)·결과 필드 전체·상태별 필드 규칙·command_id 중복·종료 대기 1개 이하를 검사. 고치려면 파일을 확인하고 지운 뒤 에이전트를 다시 시작 |
| 정지 | SIGTERM 으로 멈추면 도는 stress 를 끝내고 `FAILED`(`AGENT_STOPPING`)를 보낸 뒤 종료. 도는 중에 에이전트가 죽어 다시 시작됐다면 그 명령의 기록을 `FAILED`(`AGENT_STOPPING`)로 바꿔 둠 |
| inventory | 켜져 있을 때만 `capabilities.commands = ["stress", "agent_exit"]` |

## 설정

| 환경변수 | 기본값 | 설명 |
| --- | --- | --- |
| `XENOVA_AGENT_VM_ID` | `/etc/xenova/instance-id` 내용 | 이 VM의 UUID. 플랫폼이 게스트 준비 때 파일로 기록한다 |
| `XENOVA_AGENT_BROKER_HOST` / `PORT` | `192.168.77.1` / `1883` | dom0 xenbr0 의 Mosquitto |
| `XENOVA_AGENT_BROKER_USERNAME` / `PASSWORD` | (없음) | 브로커 계정이 생기면 설정 (`deploy/`의 ACL) |
| `XENOVA_AGENT_METRICS_INTERVAL` | `2` | metrics 수집·발행 주기(초) |
| `XENOVA_AGENT_TOP_PROCESSES` | `10` | `processes[]` 개수(0~50). 0 이면 빈 목록이고 /proc 을 훑지 않는다 |
| `XENOVA_AGENT_TELEMETRY` | `1` | 자체 상태의 v2 Telemetry 발행(1/0, true/false, yes/no, on/off). v1 토픽에는 영향이 없다 |
| `XENOVA_AGENT_DEMO_COMMANDS` | `0` | MQTT-04 데모 명령(stress·agent_exit) 실행(1/0 등). 시연용 이미지에서만 켬 |
| `XENOVA_AGENT_LOG_LEVEL` | `INFO` | DEBUG/INFO/WARNING/ERROR/CRITICAL |

heartbeat 주기는 계약값 5초로 고정이며 환경변수로 바꿀 수 없습니다(`XENOVA_AGENT_HEARTBEAT_INTERVAL`이 설정돼 있으면 시작을 거부). 시험용 짧은 주기는 `AgentSettings(heartbeat_interval_s=…)` 생성자로만 줍니다. vm_id 는 소문자·하이픈 표준형으로 정규화해 토픽과 봉투에 씁니다(Spring·registry 문자열과 글자까지 같아야 함). UUID 가 아니거나 nil UUID(Domain-0)이거나, 포트·로그 레벨이 범위 밖이면 시작하지 않고 종료 코드 2 로 끝납니다.

## 실행

```bash
# 개발 PC (브로커 없이도 시작되고 연결을 재시도한다)
cd platform/services/vm-agent/agent
PYTHONPATH=.:../mqtt-client XENOVA_AGENT_VM_ID=11111111-1111-4111-8111-111111111111 XENOVA_AGENT_BROKER_HOST=127.0.0.1 python -m xenova_agent

# 게스트 rootfs 에 설치 (템플릿 준비 단계에서 플랫폼 파트가 부른다)
python3 platform/services/vm-agent/agent/deploy/install_into_rootfs.py <rootfs>
```

SIGTERM·SIGINT 를 받으면 현재 대기를 끊고 클라이언트를 닫은 뒤 종료합니다.

## 게스트 배포

새 Linux 템플릿은 플랫폼의 `platform/scripts/prepare-xenova-guests.py`에서
`guest_agent_template.prepare()`를 호출합니다. Ubuntu·Debian recipe의
`guest_agent.packages = ["python3-paho-mqtt"]`를 검사한 뒤 새 작업 rootfs 안에서만
apt update/install을 수행하고, `deploy/install_into_rootfs.py`로 두 패키지와 unit을 설치·enable합니다.
일반 VM 생성과 factory seed 생성에서는 apt를 실행하지 않습니다.

설치 결과는 `/etc/xenova/agent-install.json`과 템플릿 manifest에 기록합니다.
Python ≥3.11, Paho ≥1.6,<3, 두 패키지 import와 unit enable 검사가 실패하면 새 템플릿을 게시하지 않습니다.
이 영수증은 신원을 넣기 전 템플릿 설치 기록이며 Broker 연결 성공을 뜻하지 않습니다.
패키지 버전·다운로드된 deb 해시는 기록하지만 배포판 저장소의 미래 가용성을 보장하지 않습니다.

일반 VM은 생성 시 기록한 `/etc/xenova/instance-id`를 사용합니다.
기본 Agent unit에는 identity service의 무조건적인 `Requires=`가 없습니다.
Factory seed는 `prepare_deployment_seed()`가 identity service를 설치한 다음,
Agent가 있는 rootfs에만 `10-xenova-identity.conf`를 연결합니다.
공통 VM 생성 경로는 두 경우 모두 실제 import·enable·의존성을 검사하고 실패하면 게시를 중단합니다.
Agent가 없는 기존 게시 템플릿은 읽기/생성 호환을 유지하며 자동 업데이트하지 않습니다.
Zephyr에는 Python Agent를 설치하지 않습니다.

Broker 주소·인증정보·ACL과 종단 통신 수락은 별도 인계입니다. 실제 비밀은 소스·manifest·시험 로그에 넣지 않습니다.
실행 위치별 명령과 잔여 실보드 검증은
[Linux 게스트 Agent 패키징 가이드](../../../../docs/build-guides/xenova-linux-guest-agent.md)를 참고하세요.

## 테스트

```bash
cd platform/services/vm-agent/agent
python -m pytest
```

가짜 시계와 가짜 클라이언트로 주기·지연 보정·끊김 건너뜀·발행 실패·정지를 확인합니다. 실제 브로커 시험은 mqtt-client 의 `XENOVA_MQTT_TEST_BROKER` 방식을 따릅니다.
v2 Telemetry 메시지는 Zephyr 쪽 검증기 `platform/guests/zephyr/telemetry/contract/tools/validate_contract.py` 로 검사합니다(`jsonschema` 필요, `pip install -e .[test]`. 없으면 그 테스트만 건너뜁니다).
