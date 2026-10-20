# xenova-mqtt-c — MQTT 계약 v1 C 라이브러리

Zephyr 자율·수동주행 앱이 쓰는 C판 `xenova_mqtt`입니다. Python [`xenova_mqtt`](../../../services/vm-agent/mqtt-client/README.md)와
**같은 토픽·정책·봉투·검사·seq 규칙**을 따르고, 두 구현이 같은지는 공통 시험 벡터(`tests/vectors/`)로 확인합니다.
설계: [docs/design/mqtt-c-client.md](../../../../docs/design/mqtt-c-client.md) · 계약: [MQTT 메시지 계약 v1](../../../../docs/contracts/mqtt-contract-v1.md)

## 지금 들어 있는 것 (설계 S1)

| 파일 | 내용 | Python 대응 |
| --- | --- | --- |
| `src/xm_topic.c` | 토픽 빌더·세그먼트 검사·`parse`·와일드카드 `matches` | `topics.py` |
| `src/xm_policy.c` | type별 QoS·retain·1 KB/64 KB 한도 (`app.heartbeat` 포함 18개 + DEFAULT) | `policy.py` |
| `src/xm_envelope.c` | 봉투 인코드(필드 순서 같음, 크기 한도), 검사(같은 순서·같은 오류 코드), UUID v4 | `envelope.py` |
| `src/xm_seq.c` | (토픽, 발행자)별 seq 중복·역전 버림, 재시작 시 기준 재설정. 고정 8슬롯 LRU | `SeqTracker` |
| `src/xm_payload.c` | `drive.command`·`app.heartbeat` 인코드, `vehicle.control`·`drive.input` 디코드 | 수동 앱 |
| `third_party/jsmn/jsmn.h` | JSON 토크나이저 jsmn v1.1.0 (MIT, sha256 `1ed6154d…c13b`) | `json` |

- 동적 메모리를 쓰지 않습니다. 실수는 고정소수점으로 씁니다 (Zephyr printf 실수 미지원 대비, 설계 §6 D6).
- 디코드 결과는 받은 버퍼를 가리키는 span입니다. 함수 안의 파서 상태가 정적이라 **한 스레드에서만** 부릅니다 (설계 §5).
- 아직 없는 것: 연결·재연결·재구독을 맡는 `xm_client`와 전송 포트(zephyr·posix·vchan) — 설계 S2·S3.

## 시험

```bash
cmake -S . -B build && cmake --build build && ctest --test-dir build --output-on-failure   # gcc, AddressSanitizer·UBSan
cd ../../../services/vm-agent/mqtt-client && python -m pytest tests/test_vectors.py         # 같은 벡터를 Python 에
```

Windows에서는 Docker로 돌립니다.

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$(cygpath -m "$PWD"):/src:ro" debian:trixie sh -c \
  'apt-get update -qq && apt-get install -y -qq gcc libc6-dev cmake make >/dev/null && cmake -S /src -B /tmp/b && cmake --build /tmp/b && /tmp/b/test_xm /src/tests/vectors'
```
