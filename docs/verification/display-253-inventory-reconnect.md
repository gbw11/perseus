# 253: OS inventory 선도착 보완 및 실제 연결 복귀 검증

2026-10-27 16:40 KST. 작업 브랜치 `feat/platform/S15P31E110-253-display-source-collection`, 기준 커밋 `527a9df` 이후 미커밋 보완 소스를 시험했다. 모듈별 SHA256은 증거에 기록했다.

## 변경

이전 시험에서 실제 Debian Agent의 inventory가 heartbeat보다 먼저 도착해 버려졌다. 이제 OS ID를 임시 보관하고 같은 VM·boot·Agent 인스턴스의 신선한 heartbeat가 도착할 때 반영한다. heartbeat 전에는 OS를 표시하지 않으며 inventory만으로 VM 생존을 만들지 않는다.

보관은 VM별 최대 4개 세션·15초로 제한하고 필요한 OS ID와 출처 메타데이터만 유지한다. 이전 순번으로 대체하지 않으며 원래 inventory 수신 시각을 보존한다. MQTT 연결 세대 변경·Xen domid 변경·퇴역 세션·보관 만료·구독 거부에서는 승격하지 않는다. Agent 세션 교체 시 이전 inventory도 제거한다. `os.current_run_verified`는 계속 false이며 root 실행 바인딩 검증을 대신하지 않는다.

## 모의 검증

display router 전체 **138 passed**, 기존 Starlette TestClient/httpx deprecation warning 1건. 새 시험 11건은 선도착 보관과 동일 세션 확인, 타 boot/인스턴스, 15초 만료, MQTT 연결 교체, Xen 실행 교체, inventory 구독 거부, 순번·용량 제한, 퇴역 Agent, 재연결 신선도를 검증한다. 기존 retained inventory 규칙 시험도 통과했다.

## 실보드 검증

실제 VM3 Debian Agent는 실행 상태를 그대로 유지했다. 최신 수집기 모듈은 시험 프로세스 메모리에만 로드했다. 이전에 허용된 loopback/내부 게스트망의 VM3 전용 임시 브로커를 두 번 연결했으며 전체 시험은 17.89초였다. 외부 프로세스 timeout으로 임시 브로커 수명을 60초 이내로 제한했다.

| 단계 | 실제 관측 |
| --- | --- |
| 첫 연결 | inventory seq 3이 먼저 도착. 즉시 표시하지 않고 1건 보관 |
| 생존 확인 | heartbeat seq 7 도착 후 OS=debian, Agent/inventory 모두 fresh |
| 브로커 중단 | Agent/inventory 소스 모두 disconnected |
| 브로커 재가동 | MQTT 연결 세대 1→3, 새 heartbeat seq 8 및 inventory seq 4 수신 |
| 복귀 | OS=debian, Agent/inventory 모두 fresh. 같은 Agent 송신 세션 유지 |

12개 실보드 검증 조건 모두 통과했다. 재연결 때는 실제로 heartbeat가 inventory보다 먼저 왔으며, 양쪽 수신 순서를 확인했다. 이전 캐시만으로 fresh를 복원한 것이 아니다.

VM·펌웨어·게스트 Agent·시스템 서비스를 재시작/변경하지 않았다. 수집기는 MQTT 발행을 하지 않았다. 임시 브로커는 익명 접속과 VM3 전용 ACL을 사용했으므로 정식 인증 브로커 검증이 아니다. VM1·VM2의 새 앱 보고, HDMI 출력, 외부 Spring 경로는 이번 시험 대상이 아니다.

## 정리 및 증거

임시 브로커·구독기와 임시 디렉터리를 제거했다. 별도 SSH 재조회로 1883 두 리스너가 닫혔고 브로커 프로세스·시험 디렉터리가 없으며, 기존 mosquitto 서비스가 inactive/dead임을 확인했다.

- [수신 순서·스냅샷·검증 조건·모듈 해시](evidence/display-253-board-20261027/board-inventory-reconnect-20261027.json)
- [종료 후 독립 정리 확인](evidence/display-253-board-20261027/board-inventory-reconnect-cleanup-20261027.json)
