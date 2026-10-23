# 근거와 신규 설계의 구분

## A. 사용자가 제공한 구현 자료 — 현재 구현 사실의 주 근거

1. `README.md` — Zephyr Diagnostics Core. 부팅·오류8개/RAM범위, status/rc, kernel system heap, thread stack high-water, raw32 netfront, loop 완료/지연/histogram 의미, local시간한계.
2. `RESULT.md` — 2026-10-23 실행 결과. `chore/platform/next-work`, HEAD `87c6269ffc5854e8f05cd7a049f6764f1a179bae`. local/native_sim/부분빌드 및 전체회귀 범위. MQTT·실제주행·실보드Publisher후속.

두 첨부의 exact SHA-256은 `source-inputs.json`에 보관했다. 첨부 파일의 구현주장을 독립 실보드 검증으로 바꾸지 않았다.

## B. 이번 응답에서 실제 읽은 연결 저장소 소스

- `backend`의 `backend/src/main/java/com/xenova/backend/agent/application/AgentMessageHandler.java`
  - blob SHA `563c68b2528518f05e6abcb03bdc4cfa331ebe5f`
  - v1 namespace/schema, percent/bytes 제한, sentAt 저장 경로 확인.
  - https://github.com/shin0-park/ssafy-autonomy-project-xenova/blob/backend/backend/src/main/java/com/xenova/backend/agent/application/AgentMessageHandler.java
- `vehicle`의 `platform/apps/common/xenova-mqtt-c/ports/zephyr/xm_zclient.h`
  - blob SHA `9cbd3527056bfbb716d90987bc17e8b255a8a5da`
  - 단일 client 소유 thread, 고정 출력버퍼, client별부팅/instance ID 생성설명확인.
  - https://github.com/shin0-park/ssafy-autonomy-project-xenova/blob/vehicle/platform/apps/common/xenova-mqtt-c/ports/zephyr/xm_zclient.h

원격 branch 전체를이번설계의정식통합/빌드기준으로선언하지않는다. 구현자가latest/미커밋/로컬Core차이를다시확인해야한다.

## C. 공식 문서 — 외부 프로토콜 근거

| 출처 | 확인한 사항 |
|---|---|
| OASIS MQTT 3.1.1 | CleanSession·retained 독립성, QoS/ACK, publish권한거절시행동. 특히 §3.3.5 [MQTT-3.3.5-2] |
| Eclipse Mosquitto config manual | username·topic ACL, 개별쓰기권한·TLS/패스워드보안 |
| Zephyr Kernel Timing | uptime·cycle주파수·단위변환의구분 |
| JSON Schema 2020-12 Validation | 구조검사/format검사와semantic검사의구분 |

공식 주소:

- https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/mqtt-v3.1.1.html
- https://mosquitto.org/man/mosquitto-conf-5.html
- https://docs.zephyrproject.org/latest/kernel/services/timing/clocks.html
- https://json-schema.org/draft/2020-12/json-schema-validation

공식latest문서는프로토콜개념확인에만사용했다. 고정Zephyr4.4/실제Mosquitto빌드의특정API가그대로있다는증거로사용하지않는다.

## D. 이번에 새로 정한 것

v2namespace·5streams·필드/자료형·metriccatalog·4KiB상한·주기/queue기본값·분할규칙·신원/시간표현·수신최소의무·fixture와validator는이번설계다. 기존구현이나외부표준이이프로젝트에자동으로요구하는값이라고주장하지않는다.

Golden의임의UUID/IP/MAC/카운터/시각/메모리/스레드/주파수는합성시험데이터다. 실제게시펌웨어의측정값이나성능보고서가아니다.
