# 253번 2단계: UUID 매핑과 수신 규칙 검증

검증일: 2026-10-22. 브랜치: `feat/platform/S15P31E110-253-display-source-collection`. 기준 HEAD: `e32455b67b1b9a8a772b070c12b79aea3dce7839`.

## 결과

2단계의 설정·수신 규칙 구현을 완료했다. [설정 계약](../contracts/display-source-bindings-v1.md)과 [후보 예제](../../platform/services/display/router/examples/integration-candidate-bindings.json)를 추가했다. 기존 앱 ID를 UUID 역할에 연결하고, 토픽별 구독 승인과 실제 보고 수신을 분리한다. Xen 상태가 과거 앱 정상 보고보다 우선하며 앱 오류와 수신 경로 오류를 구분한다.

| 검증 | 결과 | 범위 |
| --- | --- | --- |
| Python pytest | 87개 통과 | 기존 58개 + 소스 매핑/수신 회귀 29개 |
| 기존 frontend 표시 회귀 | 2개 파일, 8개 통과 | 추가 메타데이터와 표시 정책 소비 호환 |
| 실제 loopback MQTT·HTTP·WS | 7개 통과 | 합성 UUID/앱/Xen, 실제 브로커와 Paho 구독 |
| 기존 drive-state 전송 회귀 | 6개 통과 | retained 거부, 1초 갱신, 중복 만료, WS, 명시적 follow_active |

Python 시험은 host WSL의 별도 가상환경에서 실행했다. FastAPI TestClient의 httpx 관련 폐기 예정 경고 1건이 있었고 실패는 없었다. 브로커 거절/호출 오류는 단위 시험으로 확인했으며 운영 ACL을 시험한 것은 아니다.

```sh
# platform/services/display/router 에서, 필요한 패키지가 설치된 시험 환경
python -B -m pytest -q -p no:cacheprovider
PYTHONPATH=.:../../dom0-manager:../../vm-agent/mqtt-client python -B tests/probe_source_bindings.py --execute --output /path/to/evidence.json
PYTHONPATH=.:../../dom0-manager:../../vm-agent/mqtt-client python -B tests/probe_display_control.py --execute --output /path/to/control-evidence.json
# frontend 에서
npm test -- --run apps/vm-screens/src/display
```

전송 원본 결과: [소스 수신 7건](display-253-step2-mqtt-evidence.json), [제어 상태 회귀 6건](display-253-step2-control-evidence.json). 시험용 loopback 브로커·수집기와 합성 발행자를 사용하고 종료했다. 실제 게스트를 시작하거나 변경하지 않았다.

| 입력 | 확인된 presentation 정책 |
| --- | --- |
| running + 신선한 앱 error | app_error |
| paused + 이전 앱 error | paused |
| running 복귀 + 새로운 standby 보고 | status |

인증된 HTTP/WS에서 같은 UUID와 보고를 확인했다. 이 결과는 정책 JSON에 대한 증거이며 물리 모니터 화면 캡처나 실제 주행 상태의 증거가 아니다.

## 남은 범위

실제 Zephyr/Debian domU→dom0 보고, Ethernet의 TCP/MQTT 가용성, 설치된 Agent·앱과 브로커 계정/ACL, 최종 UUID/app 배치는 아직 미검증이다. Debian OS inventory는 추가하지 않았다. VM 시작/재시작·펌웨어 변경·설치는 수행하지 않았고 사용자 결정이 필요하다. 실제 OS 부팅과 발행 세대의 대응도 검증하지 않았다.

3단계에서는 준비된 환경과 허용 범위를 먼저 확인하고 실제 domU 보고를 연결한다. 이번 합성 시험을 실제 게스트 연결 완료로 사용하지 않는다. platform 변경·커밋·푸시·Jira 상태 변경은 하지 않았다.
