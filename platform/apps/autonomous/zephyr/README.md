# 자율주행 Zephyr 앱

`src/autonomous.c`의 기존 `auto_step()` 계산을 그대로 실행한다.
20ms 계산 루프·v1 주행 명령과 app heartbeat에 공통 Diagnostics/Telemetry 2.0,
검증된 Xen netfront·협조형 종료를 연결한다. 새 주행 알고리즘이나 Gateway 구현은 없다.

| 방향 | 토픽·type | 의미 |
|---|---|---|
| 입력 | `xenova/v1/vehicle/control` · `vehicle.control` | retained generation·active_app |
| 입력 | `xenova/v1/apps/perception/events` · `perception.result` | latest-only, 300ms 초과 시 기존 정지 계산 |
| 출력 | `xenova/v1/apps/autonomous/events` · `drive.command` | 실제 계산 후 최신 출력함 → worker의 QoS0 제출 |
| 출력 | 같은 events · `app.heartbeat` | 실제 계산 루프의 1초 heartbeat |
| 진단 | `xenova/v2/vms/<UUID>/telemetry/+` | 별도 Publisher·인증·세션, 기존 v2 계약 |

조향은 pure pursuit 다 (S15P31E110-360, 규칙은 `src/autonomous.h`). 인지 정의를 거꾸로 써서 카메라 0.6 m 앞 차로 중심
`y = −lane_offset_m + 0.6·tan(heading_err_rad)` 를 얻고, 후륜 축에서 그 점(카메라 0.20 m + 0.6 m 앞)을 지나는 원호로
`steer = atan(0.26·2y / (0.8² + y²)) − 1.0·lane_offset_m` 을 낸다. 차가 원호 위에 있으면 그 원호 조향을 내므로 급코너에서 안쪽으로
파고들지 않는다 (예전 `1.0·heading_err − 1.5·lane_offset` 은 정답 인지로도 0.24~0.26 m 파고들었다). 0.8 m 앞을 지나는 원호는
곡률 1.25(반경 0.8 m)를 넘지 못해 그만큼 급한 코너 진입에서 밀리므로 offset 항으로 당긴다. 축거·카메라 위치·0.6 m·이득은 `auto_cfg_default`
(시뮬레이터 차와 같은 값, 실차 측정 전). xenvm minimal libc 에 math.h 가 없어 tan·atan 은 다항 근사(오차 < 1e-4 rad)다.
Python 대역 `simulator/tools/closed_loop.py` 가 같은 식이다.

[공통 통합·시험 절차](../../common/xenova-drive-runtime/README.md)에 빌드·인증 bootstrap·명령·수신 분석·CPU 4조건 실험 절차가 있다.
계산 main은 고정 20ms 주기와 한 주기의 입력 사본을 사용하고, 별도 worker가
Bootstrap·v1 통신을 소유한다. 출력함 수락과 실제 송신 성공은 별개다.
브로커 자격증명과 VM UUID는 빌드 설정에 넣지 않는다.
Xen은 공유 Store의 자신의 VM 신원과 실행-bound bootstrap을 사용하며 익명/Kconfig fallback을 하지 않는다.
native_sim은 격리 TAP와 private runtime 파일을 사용하는 시험 전용 경로다.

## 계산 회귀

저장소 루트의 Linux/WSL 셸에서 실행한다.

```sh
cmake -S platform/apps/autonomous/zephyr/tests -B "$HOME/autonomous-logic-tests" -DCMAKE_EXE_LINKER_FLAGS=-no-pie
cmake --build "$HOME/autonomous-logic-tests"
ctest --test-dir "$HOME/autonomous-logic-tests" --output-on-failure
```

기존 계산·payload·XenStore UUID 경로 단위 시험과 공통 MQTT 벡터를 사용한다.
통합 수락은 M1 빌드, M2 실제 입력/생성/독립 수신, M3 동일 firmware CPU 비교를 구분한다.
시험 입력의 수락은 실차 동작·안전성 수락이 아니다.
