# 표시 파이프라인 2단계 검증

2026-10-21 · 작업 브랜치 `docs/platform/S15P31E110-250-display-contract`.

## 구현 범위

- dom0 독립 상태 수집기: 실제 Xen 실행 상태 + 등록 UUID/역할 + MQTT Agent/앱 관측 정규화.
- 소스별 수신 지연/단절, 중복·역순 메시지와 이전 부팅/프로세스 세대 배제.
- 내부 서비스 토큰 REST/WS와 외부 Spring 사용자 JWT REST/WS. 읽기 전용이며 터미널 입력/주행 명령은 거부한다.
- 외부 서버 장애는 로컬 수집을 중지시키지 않는다. 외부 WS 수집 폴링은 전용 스레드로 분리해 Spring의 주행/이벤트 스케줄러를 막지 않는다.

외부 웹 페이지 라우팅, HDMI 두 모니터 라우팅·렌더링·캡처·원격 영상 전송은 이번 단계 범위가 아니다. 기존 콘솔은 MO-04를 재사용한다.

## 자동 테스트

| 시험 | 결과 |
| --- | --- |
| 새 수집기 Python 상태/인증/읽기 전용 WS | 14개 통과 |
| 기존 dom0-manager Python 회귀 | 156개 통과 |
| Spring DisplayIntegrationTest | 4개 통과 |
| Spring ConsoleWebSocketIntegrationTest | 8개 통과 |

Python 테스트 환경에 Starlette의 httpx TestClient 사용 관련 deprecation 경고가 있지만 실패는 없다. 전체 Spring 테스트 모음을 통과했다고 주장하지 않는다. 이번 변경의 신규 API/WS 및 기존 콘솔을 대상으로 검증했다.

## 실제 QEMU 경로 시험

재현 도구: `platform/services/display/router/tests/probe_qemu.py`.
결과 원본: [display-step2-qemu-evidence.json](display-step2-qemu-evidence.json).

실제 dom0의 임시 디렉터리에서 수집기를 실행했다. 실제 Debian 게스트는 `/etc/os-release`와 부팅 ID 등 실제 정보를 읽는 메모리상의 임시 보고기로 Agent heartbeat/inventory를 발행했다. 시험 브로커는 WSL loopback에서 실행하고 SSH 포워딩으로 연결했다. 따라서 앱이 상시 설치됐거나 제품의 dom0 Mosquitto 배포가 검증됐다는 의미는 아니다.

통과한 7개 항목:

1. 실제 Xen에서 VM1·VM2·VM3가 실행 중인 상태 수집.
2. 앱 보고/OS 근거가 없는 VM의 앱 정상 상태·관측 OS를 임의 생성하지 않음.
3. 실제 Debian의 MQTT heartbeat와 Debian OS inventory가 dom0 수집기에 도착.
4. Spring 사용자 JWT REST로 실제 dom0 스냅샷을 받고 서비스 토큰이 응답에 포함되지 않음.
5. Spring 사용자 JWT WebSocket으로 같은 실제 상태를 수신.
6. MQTT 브로커 종료 시 Agent 소스는 단절로 표시하고 실제 Xen 실행 상태는 유지.
7. 시험 전후 세 VM의 UUID와 domain ID 동일. VM 재시작 없음.

QEMU dom0의 UTC 시각은 시험 호스트보다 뒤처져 있다. 스냅샷 원본 시각은 수정하지 않았으며 freshness는 각 수신기의 단조 시계 기준이다.

## 남은 연결

- 두 Zephyr Starter에는 앱 heartbeat 송신이 없으므로 앱 상태는 unknown이다. 설정 파일의 새 app_id는 발행자 구현 완료를 의미하지 않는다.
- 기존 MQTT 문서의 자동/수동 VM 분리는 그대로 재사용하지 않는다. 새 역할은 설정된 UUID 기준이다. 실제 제어 상태 발행자와 새 주·백업 역할 계약의 합의/연결은 남아 있다.
- AI 상태·실제 복구 작업 상태·그래픽 소스는 아직 연결하지 않았다. 상태 피드에서 unknown/null로 드러낸다.
- 실행 domid와 게스트 부팅 ID의 완전한 결합을 증명하는 플랫폼 기능은 없어 OS `current_run_verified=false`다. 기대 OS와 관측 근거를 구분한다.
- 실보드 HDMI/영상 전송·실제 주행 승계는 검증하지 않았다.

Jira는 이번 단계에서 수정하지 않았다. platform 브랜치 변경, 커밋, 푸시, 병합을 수행하지 않았다.
