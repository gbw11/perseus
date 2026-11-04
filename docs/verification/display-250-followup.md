# 250 계약 확정 후속 작업

> **2026-11-02 현재 상태:** 아래는 2026-10-22 당시 시험 이력이다. ‘보드 미준비·실보드 미수행’은 이후 252·256·257 시험으로 갱신됐다. 현재 구성·성능 근거·남은 Gateway 표시용 발행/ACL 확인은 [최신 결정 기록](../contracts/display-250-decisions.md)을 따른다. 과거 합성 시험을 실제 제어 상태 연동 성공으로 다시 분류하지 않는다.

2026-10-22. 기존 [5단계](display-step5.md) 시험 이후 사용자의 의사결정을 반영했다. 과거 evidence를 새 계약으로 다시 실행한 증거처럼 변경하지 않았다.

## 사용자 결정

- 보드는 아직 준비되지 않았다. 실제 dom0 SSH·그래픽 호환성 확인은 하지 않았다.
- 제어 담당 팀원과 별도 합의가 필요하다. 해당 팀원의 동의/발행 구현은 아직 확인하지 않았다.
- 표시용 상태는 기존 주행 제어와 별도 토픽으로 1초마다 보고한다.
- 1차 전송 목표는 720p/출력별 5 FPS, 지연 p95 ≤500ms, JPEG 합계 ≤8Mbps, 송신기+인코더 평균 ≤1코어다. 10 FPS는 후속 개선이다. 실제 보드 목표 달성 여부는 미검증이다.

## 반영과 검증

수신 토픽을 `xenova/v1/display/drive-state`, type을 `display.drive_state`로 분리했다. 기존 `vehicle/control` 구독을 제거하고 제어 발행자·주행 메시지·운영 ACL은 변경하지 않았다. 3초 freshness·역순/중복·퇴역 세대 거부와 모드/활성 VM의 분리를 유지한다. 활성 VM 필드가 빠진 보고도 거부한다.

Python 회귀 **58개 통과**. source/type/retained/VM 식별/비구동 VM/모드 누락·오류 거부, 1초 새 seq 보고와 3초 만료, 권위 프로세스 교체 후 과거 보고 거부, 기존 차량 제어 토픽 비구독을 포함한다. Starlette/httpx TestClient의 기존 deprecation 경고만 있다.

[새 토픽 실제 MQTT 시험 결과](display-250-control-evidence.json): 격리된 loopback amqtt 브로커·실제 Paho 구독·HTTP/WS 서버를 사용한 **6개 항목 통과**.

1. 구독 시 재생된 retained 초기값으로 현재 주행 상태를 정상화하지 않음.
2. 기존 차량 제어 토픽의 메시지를 표시 상태로 수락하지 않음.
3. 내용이 같아도 1초마다 증가하는 seq의 새 보고가 REST의 상태를 fresh로 유지.
4. 같은 보고가 인증된 WebSocket에 도착.
5. 중복 seq는 freshness를 연장하지 않고 3초 미수신 시 mode/active VM을 미확인으로 만료.
6. 신선한 VM2 보고 후에도 pinned는 VM1을 유지하고 명시적 follow_active에서만 상세가 VM2로 전환.

이 시험의 Xen/제어 발행자는 **합성**이며 실제 차량 제어 엔진·실보드·운영 계정 ACL의 통합 시험은 아니다. 브로커/서버/발행자는 시험 종료 시 정리한다.

재현(router 디렉터리, 기존 테스트 런타임의 amqtt/paho/httpx/websockets 필요):

```sh
PYTHONPATH=.:../../dom0-manager:../../vm-agent/mqtt-client \
python -B tests/probe_display_control.py --execute --output /tmp/display-control-evidence.json
```

[읽기 전용 보드 점검 도구](../../platform/services/display/router/tools/probe_board_environment.py)는 WSL에서 실행 가능함을 확인했으며 4개 자동 테스트로 누락/timeout/원격 DISPLAY 거부·조회 명령·토큰 미수집을 검증했다. WSL 실행 결과를 보드 검증 결과로 보관하지 않았다.

## 남은 완료 조건

[250 결정 기록](../contracts/display-250-decisions.md)의 3번 성능 목표 선택은 완료다. 1번은 제어 담당에게 [연결 규격](../contracts/display-drive-state-v1.md)을 검토받고 권위 발행자/실제 주기 보고/ACL 담당을 확정해야 한다. 2번은 보드가 준비된 뒤 실제 점검 결과와 X11·Chromium·FFmpeg 실행 가능성을 확인해야 한다.

Jira 완료 처리·팀원 메시지 전송·커밋·푸시·platform 브랜치 변경은 하지 않았다.
