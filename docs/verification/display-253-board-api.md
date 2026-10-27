# 253: 실보드 VM3 → 인증된 표시 REST·WebSocket 검증

2026-10-27 16:55 KST. 라즈베리파이 dom0 `192.168.30.96`, VM3 `055226af-b876-4707-8b5d-99e7f33ad3d7`. 작업 브랜치 `feat/platform/S15P31E110-253-display-source-collection`, 기준 커밋 `527a9df` 이후 inventory 선도착 보완을 포함한 미커밋 소스를 시험했다. 사용한 6개 모듈의 해시는 증거 JSON에 기록했다.

## 실제 시험 경로

```text
기존 Debian Agent → 내부망의 임시 MQTT 브로커 → 최신 수집기
                                           ├ 인증 REST /api/v1/display/state
                                           ├ 인증 WS   /ws/v1/display/state
                                           └ 인증 REST /api/v1/display/presentation
```

보드 loopback의 OS가 배정한 임시 TCP 포트에 실제 Uvicorn/FastAPI를 실행했다. HTTP 요청과 RFC 6455 WebSocket Upgrade·프레임을 TCP 소켓으로 읽었다. TestClient·합성 Agent 보고·가짜 MQTT 콜백을 사용한 시험이 아니다. 최신 모듈은 단기 Python 프로세스 메모리에만 로드하고 파일을 영구 배포하지 않았다. API Bearer 토큰은 실행 중 무작위 생성해 출력·저장하지 않았다.

MQTT 브로커는 loopback과 내부 게스트망에서 VM3 heartbeat/inventory/metrics만 ACL로 허용했다. 익명 접속의 격리 시험이므로 **정식 MQTT 계정 인증 검증은 아니다**. 보드 내부 표시 API의 인증과 MQTT 인증은 구분한다.

## 결과: 검증 조건 35개 모두 통과

| 구간 | REST / presentation | Agent·inventory 상태 | 동일 WS의 스냅샷 sequence |
| --- | --- | --- | --- |
| 최초 수신 | 모두 200 | fresh / fresh, OS=debian | 34 |
| MQTT 중단 | 모두 200 | disconnected / disconnected | 38 |
| MQTT 복귀 | 모두 200 | fresh / fresh, OS=debian | 51 |

REST는 MQTT 중단 때도 200으로 소스별 disconnected 상태를 제공한다. 수집기 HTTP 서버 자체가 종료된 시험과 다르다. OS는 마지막 관측값으로 남으며 inventory 소스가 disconnected임을 함께 표시한다.

WebSocket을 계속 유지한 상태로 MQTT의 중단과 복귀가 전달됐다. 세 단계의 source_epoch는 같고 sequence는 증가했다. 재연결 후 실제 heartbeat seq 9→10, inventory seq 5→6을 받아 복구했으며 이전 값만으로 신선도를 복원하지 않았다. 두 연결 모두 inventory가 먼저 와 잠시 보관한 후 heartbeat와 동일 Agent 세션이 확인돼 OS를 표시했다. Agent는 재시작하지 않았다.

인증 시험:

- REST에 토큰 없음/잘못된 토큰: 각각 401.
- WebSocket에 토큰 없음/잘못된 토큰: 각각 HTTP 403으로 Upgrade 거부. 연결 수락 전에 서버가 닫은 실제 네트워크 응답이다.
- 올바른 API 토큰: REST/presentation 200, WebSocket Upgrade 101.
- 상태 REST 응답은 세 단계 모두 Cache-Control: no-store.

전체 브로커 시험은 15.2초였고 각 브로커 프로세스에 전체 60초 이내로 끝나도록 외부 timeout을 적용했다. 표시된 OS는 보고 관측이며 current_run_verified는 false를 유지했다. 앱 상태·주행 모드·승계 권한을 만들지 않았다.

## 종료 확인

API 스레드·리스너, 구독기·브로커를 종료하고 임시 파일을 제거했다. 별도 SSH 재조회에서 열린 TCP 포트는 기존 SSH 22와 Manager loopback 8081뿐이었다. MQTT 1883 두 리스너는 닫혔고 브로커 프로세스·시험 디렉터리가 없었다. 기존 mosquitto 서비스는 inactive/dead를 유지했다.

VM·펌웨어·게스트 Agent·시스템 서비스 설정은 변경하지 않았다. 외부 Spring/Nginx/배포 웹, 브라우저 렌더링, HDMI, VM1·VM2 새 앱 펌웨어는 시험 범위에 포함하지 않는다.

## 증거

- [TCP API 응답·WS 스냅샷·검증 조건·모듈 해시](evidence/display-253-board-20261027/board-display-api-20261027.json)
- [시험 후 별도 SSH로 정리 확인](evidence/display-253-board-20261027/board-display-api-cleanup-20261027.json)
