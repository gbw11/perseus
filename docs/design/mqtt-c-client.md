# 주행 앱용 C MQTT 라이브러리 설계 (xenova_mqtt C판)

> 상태: 설계 초안 (2026-10-20, S15P31E110-236). 구현 전이며 §6·§9 는 정해지는 대로 고친다.

- **기준**: Python [`xenova_mqtt`](../../platform/services/vm-agent/mqtt-client/README.md) (topics·policy·envelope·client), [MQTT 계약 v1](../contracts/mqtt-contract-v1.md) §1·§2·§5
- **쓰는 곳**: Zephyr 자율주행·수동주행 앱. 나중에 C 로 짜는 dom0 게이트웨이도 같은 코어를 쓸 수 있다.

## 1. 목표

Python `xenova_mqtt` 와 **같은 토픽·같은 봉투·같은 검사·같은 QoS 규칙**을 C 로 낸다. 두 구현이 같은지는 공통 시험 벡터로 확인한다.
Zephyr 에서 동적 메모리 없이 50 Hz 주행 명령을 안정적으로 내는 것이 1순위다.

## 2. Python 과 1:1 대응

| Python | C (`xm_` 접두어) | 같게 맞출 것 | 다르게 할 것 (Zephyr 사정) |
| --- | --- | --- | --- |
| `topics.py` | `xm_topic.c` | `PREFIX`, 빌더(`vm`·`app_events`·`manual_input`·`vehicle_control/status`·`probe_*`), 세그먼트 검사(`/ + # 공백` 금지), `parse`, `matches` | 반환은 호출자 버퍼에 쓰고 길이·오류 코드 |
| `policy.py` | `xm_policy.c` | 표 그대로 (17개 type + DEFAULT QoS 1), `MAX_BYTES` 64 KB, 주행 1 KB | 정적 const 배열 |
| `envelope.py` `build` | `xm_envelope_encode()` | 필드 순서·이름, `schema_version "1.0"`, `vm_id`/`source_id` 정확히 하나, `app_id` 는 토픽에서 | `message_id` = 단조 증가 정수 문자열 (계약 §2 펌웨어 예외), `boot_id` = 부팅 때 난수 UUID |
| `envelope.py` `validate` | `xm_envelope_decode()` | 검사 순서와 **오류 코드 문자열** (`PAYLOAD_INVALID`, `SCHEMA_VERSION_UNSUPPORTED`, `VM_ID_MISMATCH`, `APP_ID_MISMATCH`), `agent.command` 예외 | JSON 은 토큰 파서(jsmn)로, 값은 원문 버퍼를 가리키는 span |
| `SeqTracker` | `xm_seq_tracker` | (토픽, 발행자) 키, (boot_id, agent_instance_id) 가 바뀌면 기준 재설정, 이하 seq 버림 | 고정 슬롯 N 개 (기본 8), 가득 차면 가장 오래 안 쓴 슬롯 교체 |
| `XenovaClient` | `xm_client` | clean session, MQTT 3.1.1, 연결될 때마다 재구독, type 별 QoS·retain, 크기 초과 발행 거부, 수신 검사 → seq → latest 갱신 → 핸들러 | 스레드 안전하지 않음: **한 스레드가 소유** (§5). 전송은 포트(§4)로 분리 |
| `Message.age_ms()` | `xm_latest()` 의 `age_ms` | 신선도는 받는 쪽 단조 시계 (계약 §1) | `k_uptime_get()` |

## 3. 구조

```text
platform/apps/common/xenova-mqtt-c/        (Zephyr 모듈 + 호스트 CMake 둘 다)
├── include/xenova/mqtt/
│   ├── xm.h            # 공개 API 한 곳
│   └── xm_port.h       # 포트(전송·시계·난수) 인터페이스
├── src/
│   ├── xm_topic.c      # 순수 로직 ┐
│   ├── xm_policy.c     #           │ OS 무관 — Linux 에서 단위 시험
│   ├── xm_envelope.c   #           │
│   ├── xm_seq.c        #           │
│   ├── xm_payload.c    # 주행 payload 인코드/디코드 ┘
│   └── xm_client.c     # 연결·재구독·발행·수신 흐름 (포트만 호출)
├── ports/
│   ├── zephyr/xm_port_zephyr.c   # Zephyr net/mqtt + 소켓, k_uptime, sys_rand
│   ├── posix/xm_port_posix.c     # libmosquitto, clock_gettime — 호스트 시험·dom0 C 프로그램용
│   └── vchan/                    # (예비) Zephyr 네트워크가 없을 때 vchan → dom0 중계
├── third_party/jsmn/   # MIT, 단일 헤더 JSON 토크나이저 (버전·해시 고정)
├── tests/
│   ├── vectors/*.json  # Python·C 공통 시험 벡터 (§7)
│   └── test_*.c        # 호스트(Linux)에서 도는 단위 시험
├── zephyr/module.yml, Kconfig, CMakeLists.txt
└── README.md
```

- 순수 로직(topic·policy·envelope·seq·payload)은 OS 를 모른다 → **Linux PC 에서 시험**하고 Zephyr 에는 같은 소스를 넣는다.
- 위치는 앱 영역(`platform/apps/`, part `app`). Zephyr 모듈 방식은 `platform/guests/zephyr/xen-store` 선례를 따른다 (`ZEPHYR_EXTRA_MODULES` + Kconfig).

## 4. 포트 인터페이스 (OS·전송 분리)

```c
struct xm_port {
    int  (*connect)(void *ctx, const struct xm_conn_cfg *cfg);          /* host, port, client_id, username, password, keepalive */
    int  (*publish)(void *ctx, const char *topic, const uint8_t *data, size_t len, uint8_t qos, bool retain);
    int  (*subscribe)(void *ctx, const char *filter, uint8_t qos);
    int  (*poll)(void *ctx, int32_t timeout_ms);                         /* 수신을 xm_client_on_receive() 로 넘긴다 */
    void (*disconnect)(void *ctx);
    int64_t (*now_ms)(void *ctx);                                        /* 단조 시계 */
    void (*random)(void *ctx, uint8_t *out, size_t len);                 /* boot_id·instance_id */
    int  (*utc_now)(void *ctx, char out[25]);                            /* sent_at. 벽시계 없으면 -ENOTSUP (§6 D2) */
};
```

| 포트 | 전송 | 쓰는 곳 |
| --- | --- | --- |
| zephyr | Zephyr 내장 `net/mqtt` (MQTT 3.1.1) + BSD 소켓 | 주행 앱 (Zephyr 네트워크가 되면) |
| posix | libmosquitto | 호스트 시험, 로컬 브로커([`platform/deploy/mosquitto`](../../platform/deploy/mosquitto/README.md))와 통합 시험, dom0 C 프로그램 |
| vchan | Xen vchan 프레임 → dom0 중계 프로세스가 대신 MQTT 발행 | Zephyr 네트워크가 끝내 안 될 때의 대안 (계약 §10) |

전송이 무엇이든 봉투·토픽·검사는 코어가 하므로 앱 코드는 바뀌지 않는다.

## 5. 공개 API (초안)

```c
/* 식별 — vm_id 또는 source_id 정확히 하나 (Python Identity 와 같은 규칙) */
int xm_identity_init(struct xm_identity *id, const char *vm_id, const char *source_id,
                     const char *app_id, const struct xm_port *port, void *port_ctx);

/* 토픽 */
int  xm_topic_app_events(char *out, size_t n, const char *app_id);     /* xenova/v1/apps/{app_id}/events */
int  xm_topic_vehicle_control(char *out, size_t n);
int  xm_topic_vehicle_status(char *out, size_t n);
int  xm_topic_manual_input(char *out, size_t n);
int  xm_topic_parse(const char *topic, struct xm_topic_info *info);
bool xm_topic_matches(const char *filter, const char *topic);

/* 정책 */
const struct xm_policy *xm_policy_for(const char *type);                /* 없으면 DEFAULT (QoS 1) */

/* 클라이언트 — 한 스레드가 소유한다 */
int  xm_client_init(struct xm_client *c, const struct xm_identity *id,
                    const struct xm_port *port, void *port_ctx, const struct xm_conn_cfg *cfg);
int  xm_client_poll(struct xm_client *c, int32_t timeout_ms);           /* 연결·재연결·재구독·수신 처리 */
int  xm_publish(struct xm_client *c, const char *topic, const char *type,
                const char *payload_json, const struct xm_pub_opts *opts);  /* seq·봉투·크기 검사 후 발행 */
int  xm_subscribe(struct xm_client *c, const char *filter, uint8_t qos,
                  xm_handler_t handler, void *user);                    /* handler NULL 이면 latest 로만 */
int  xm_latest(struct xm_client *c, const char *topic,
               const struct xm_message **msg, int64_t *age_ms);

/* 주행 payload (계약 §5.4) */
int xm_drive_command_json(char *out, size_t n, uint32_t generation, float v_mps, float steer_rad);
int xm_vehicle_control_parse(const struct xm_message *m, struct xm_vehicle_control *out);
int xm_perception_result_parse(const struct xm_message *m, struct xm_perception_result *out); /* obstacles 최대 XM_MAX_OBSTACLES */
int xm_drive_input_parse(const struct xm_message *m, struct xm_drive_input *out);           /* keys 최대 XM_MAX_KEYS */
```

- 반환값: 0 이상 성공(길이), 음수 errno (`-EINVAL` 형식, `-EMSGSIZE` 크기 초과, `-ENOTCONN` 미연결, `-ENOSPC` 버퍼 부족).
- 수신 검사 실패는 `xm_client` 가 로그(오류 코드 문자열 그대로)만 남기고 버린다 — Python 과 같다.
- 메모리: 모두 정적. 크기는 Kconfig (`XM_RX_BUF` 1 KB 주행 / 4 KB 기본, `XM_MAX_SUBS` 8, `XM_LATEST_SLOTS` 4, `XM_SEQ_SLOTS` 8).

### 주행 앱이 쓰는 모양 (자율주행)

```c
/* 하나의 주행 스레드가 client 를 소유: 20 ms 마다 계산·발행, 남는 시간에 poll */
xm_subscribe(&c, perception_topic, 0, NULL, NULL);        /* latest-only */
xm_subscribe(&c, control_topic,    1, on_control, &state); /* retained 세대 */
for (;;) {
    int64_t next = tick_start + 20;
    const struct xm_message *p; int64_t age;
    if (xm_latest(&c, perception_topic, &p, &age) == 0 && age < PERCEPTION_STALE_MS) {
        plan(p, &v, &steer);
    } else {
        v = 0; steer = 0;                                   /* 인지 끊김 → 정지 (RC카 시나리오 §8 D5) */
    }
    xm_drive_command_json(buf, sizeof buf, state.generation, v, steer);
    xm_publish(&c, my_events_topic, "drive.command", buf, NULL);
    xm_client_poll(&c, next - port_now_ms());
}
```

## 6. Zephyr 라서 정할 것

| # | 항목 | 제안 | 이유 |
| --- | --- | --- | --- |
| D1 | JSON | **jsmn** (토큰만, malloc 없음) + 직접 `snprintf` 인코드 | Linux·Zephyr 같은 소스로 시험. Zephyr `json.h` 는 descriptor 방식이라 봉투의 임의 payload 를 다루기 불편 |
| D2 | `sent_at` (벽시계 없음) | SNTP 로 맞추면 실제 UTC, 안 되면 `1970-01-01T00:00:00.000Z` + 업타임 | 계약 §1: 보드 간 시각으로 판정하지 않는다. 백엔드는 비어 있지 않은지만 본다 |
| D3 | `message_id` | `"<seq 와 별개인 단조 카운터>"` 10진 문자열 | 계약 §2 펌웨어 예외. UUID 생성 비용 없음 |
| D4 | `boot_id`·`agent_instance_id` | 부팅 때 `sys_rand` 로 UUID v4 하나 만들어 둘 다 사용 | Zephyr 앱은 부팅 = 프로세스 시작 |
| D5 | 내 `vm_id` 얻기 | XenStore(G1 모듈)에서 자기 도메인의 vm UUID 읽기, 실패하면 Kconfig 값 | 템플릿마다 다시 빌드하지 않게 |
| D6 | 실수 출력 | `CONFIG_CBPRINTF_FP_SUPPORT` 켜기 또는 소수 4자리 고정 정수 변환 | Zephyr printf 기본은 float 미지원 |
| D7 | 계정 | username = app_id (`autonomous`·`manual`), 비밀번호는 템플릿 준비 시 주입 | 로컬 브로커 ACL 규칙과 같다 |
| D8 | MQTT 버전 | 3.1.1 | Python·백엔드와 같다. 권한 없는 발행은 브로커가 조용히 버림 |

## 7. Python 과 같은지 확인하는 방법

1. `tests/vectors/` 에 JSON 시험 벡터를 둔다.
   - `topics.json`: 빌더 입력 → 기대 토픽, 잘못된 세그먼트 → 오류
   - `policy.json`: type → (qos, retain, max_bytes)
   - `validate.json`: (topic, envelope) → 통과 또는 오류 코드
   - `seq.json`: 수신 순서 → 받음/버림
2. C 단위 시험(호스트)과 Python 시험(`mqtt-client/tests/test_contract.py` 에 벡터 읽기 추가)이 **같은 파일**을 돌린다.
3. 교차 시험: 로컬 브로커에서 C(posix 포트)가 낸 `drive.command` 를 Python `XenovaClient` 가 검사 통과로 받고, 반대 방향도 확인.
4. 크기: C 가 만든 `drive.command` 봉투가 1 KB 미만인지 (계약 §1).

## 8. 단계

| 단계 | 내용 | 확인 |
| --- | --- | --- |
| S1 | 순수 로직 (topic·policy·envelope·seq·payload) + 시험 벡터 | 호스트 단위 시험, Python 도 같은 벡터 통과 |
| S2 | posix 포트 + `xm_client` | 로컬 브로커에서 C ↔ Python 교차 시험 |
| S3 | Zephyr 모듈화 + zephyr 포트 | QEMU/실보드 Zephyr 에서 브로커 접속 (Zephyr 네트워크 상태에 따라 S3' vchan) |
| S4 | 자율·수동주행 앱이 사용 | 50 Hz 주기·지터 측정 (시나리오 지표) |

## 9. 열린 질문

- Zephyr PV 네트워크(패킷 송수신)가 언제 되는지 → S3 를 zephyr 포트로 갈지 vchan 으로 갈지 (박신영·주소정)
- Python 쪽 시험(`mqtt-client/tests`)에 공통 벡터 읽기를 더하는 작업 (S15P31E110-153)
