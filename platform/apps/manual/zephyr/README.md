# 수동주행 Zephyr 앱

이 디렉터리의 단독 `main`은 기존 keys-only/manual-session-1 프로필의 호환 진입점이다.
현대 수동 운영은 [공통 A/B drive 펌웨어](../../drive/README.md)의 MANUAL 계산에서
같은 `src/manual.c`의 `manual_lease_step()`을 사용한다. 실제 Pi↔guest clock,
Vehicle, driver와 STOP gate는 그 계산 경계에서 검산하며 Jetson/World의 준비를
수동 조종의 조건으로 삼지 않는다. 이 단독 앱은 해당 실제 입력 owner가 없으므로
input2/3을 keys-only로 낮춰 이동시키지 않고 명시 command3 STOP만 발행한다.

기존 `manual_step()`은 명시 legacy 계산이다.
20ms 계산 루프·v1 주행 명령과 app heartbeat에 공통 Diagnostics/Telemetry 2.0,
검증된 Xen netfront·협조형 종료를 연결한다. 새 주행 알고리즘이나 Gateway 구현은 없다.

| 방향 | 토픽·type | 의미 |
|---|---|---|
| 입력 | `xenova/v1/vehicle/control` · `vehicle.control` | retained generation·active_app |
| 입력 | `xenova/v1/apps/manual/input` · `drive.input` | latest-only, 200ms 초과 시 기존 정지 계산 |
| 출력 | `xenova/v1/apps/manual/events` · `drive.command` | 실제 계산 후 최신 출력함 → worker의 QoS0 제출 |
| 출력 | 같은 events · `app.heartbeat` | 실제 계산 루프의 1초 heartbeat |
| 진단 | `xenova/v2/vms/<UUID>/telemetry/+` | 별도 Publisher·인증·세션, 기존 v2 계약 |

[공통 통합·시험 절차](../../common/xenova-drive-runtime/README.md)에 빌드·인증 bootstrap·명령·수신 분석·CPU 4조건 실험 절차가 있다.
계산 main은 고정 20ms 주기와 한 주기의 입력 사본을 사용하고, 별도 worker가
Bootstrap·v1 통신을 소유한다. 출력함 수락과 실제 송신 성공은 별개다.
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

`manual-lease-flow`는 input2/3 파싱, immutable 입력 수락, 만료·키 해제·A/D-only,
반전 STOP 전 press 폐기, 동일 SID CONTINUE_SESSION과 command2/3 직렬화를 연결한다.
command2는 A7의 exact 22필드를 보존한다. 실제 운영 command3는 원 confirmed clock
receipt의 `clock_probe_sequence`와 `next_direction`을 추가한 exact 24필드다.
STOP만 clock sequence 0을 허용하며 MANUAL의 next_direction은 항상 0이다.
합성 `manual_lease_facts`는 실물 STOP/APPLIED·무선 연결의 시험을 대체하지 않는다.
HELD의 새 표본도 이전 계산의 원 E를 바꾸지 않으며, 승계 때 원 입력의 generation을
현재 generation으로 고쳐 쓰지 않는다. generation 변화만으로 새 Enter를 요구하지도 않는다.
