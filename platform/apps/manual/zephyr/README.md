# 수동주행 Zephyr 앱

`src/manual.c`의 기존 `manual_step()` 계산을 그대로 실행한다.
20ms 계산 루프·v1 주행 명령과 app heartbeat에 공통 Diagnostics/Telemetry 2.0,
검증된 Xen netfront·협조형 종료를 연결한다. 새 주행 알고리즘이나 Gateway 구현은 없다.

| 방향 | 토픽·type | 의미 |
|---|---|---|
| 입력 | `xenova/v1/vehicle/control` · `vehicle.control` | retained generation·active_app |
| 입력 | `xenova/v1/apps/manual/input` · `drive.input` | latest-only, 200ms 초과 시 기존 정지 계산 |
| 출력 | `xenova/v1/apps/manual/events` · `drive.command` | 실제 계산 후 QoS0 제출 |
| 출력 | 같은 events · `app.heartbeat` | 실제 계산 루프의 1초 heartbeat |
| 진단 | `xenova/v2/vms/<UUID>/telemetry/+` | 별도 Publisher·인증·세션, 기존 v2 계약 |

[공통 통합·시험 절차](../../common/xenova-drive-runtime/README.md)에 빌드·인증 bootstrap·명령·수신 분석·CPU 4조건 실험 절차가 있다.
브로커 자격증명과 VM UUID는 빌드 설정에 넣지 않는다.
Xen은 공유 Store의 자신의 VM 신원과 실행-bound bootstrap을 사용하며 익명/Kconfig fallback을 하지 않는다.
native_sim은 격리 TAP와 private runtime 파일을 사용하는 시험 전용 경로다.

## 계산 회귀

저장소 루트의 Linux/WSL 셸에서 실행한다.

```sh
cmake -S platform/apps/manual/zephyr/tests -B "$HOME/manual-logic-tests" -DCMAKE_EXE_LINKER_FLAGS=-no-pie
cmake --build "$HOME/manual-logic-tests"
ctest --test-dir "$HOME/manual-logic-tests" --output-on-failure
```

기존 계산·payload·XenStore UUID 경로 단위 시험과 공통 MQTT 벡터를 사용한다.
통합 수락은 M1 빌드, M2 실제 입력/생성/독립 수신, M3 동일 firmware CPU 비교를 구분한다.
시험 입력의 수락은 실차 동작·안전성 수락이 아니다.
