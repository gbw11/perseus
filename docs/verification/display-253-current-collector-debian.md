# 253: 최신 수집기와 실제 Debian Agent 수신 검증

2026-10-27 16:31 KST, 실보드 dom0 192.168.30.96. 대상 수집기 커밋 `527a9df`.

## 결과

기존 VM3 Debian Agent가 보낸 실제 heartbeat 3개(seq 4/5/6)를 모두 수락했다. VM UUID는 `055226af-b876-4707-8b5d-99e7f33ad3d7`이며 Xen에서도 실행 중이었다. 수신 중 Agent 소스는 fresh, 마지막 수신 경과 시간은 982ms였다. 임시 브로커 종료 후 disconnected로 바뀌었고 이전 보고를 정상 연결로 유지하지 않았다.

수집기 변경의 VM3 기존 Agent 경로 호환성은 실제 보고로 확인했다. VM1·VM2의 새 공통 앱 보고는 이 시험 범위에 포함하지 않는다.

## 방법과 제한

기존 배치 경로의 실행 의존성을 사용하고 현재 커밋의 config/app_reports/state/mqtt 모듈을 임시 Python 프로세스 메모리에 로드했다. 소스 모듈 해시는 증거 JSON에 기록했다. 시스템 수집기 파일을 덮어쓰거나 영구 서비스를 설치하지 않았다. 배포 설정은 기존 시험용 후보 설정이며 등록 UUID는 실제 VM3와 일치한다.

앞서 승인된 VM3 전용 임시 브로커를 loopback과 내부 게스트망에 약 19.6초 실행했다. OS timeout으로 브로커 최대 수명도 60초로 제한했다. ACL은 VM3 heartbeat/inventory/metrics 경로만 허용했다. 익명 접속을 사용하는 격리 시험이므로 정식 인증·ACL 배포 검증으로 해석하면 안 된다. 수집기는 MQTT 발행을 하지 않았고 게스트 Agent도 재시작하지 않았다.

inventory 1건은 heartbeat보다 먼저 도착해 거부됐다. 현재 정책에서 inventory는 새 boot를 시작할 수 없으므로 관측 OS는 null이었다. heartbeat 생존 수신 성공과 OS 근거 수집 완료는 구분한다. 앱 생존 보고도 없어 VM3 앱 상태는 unknown이다. 연결 복귀 시험과 inventory 선도착 처리는 이 결과로 통과를 주장하지 않는다.

## 정리 확인

- 임시 브로커·구독기 종료, 임시 디렉터리 제거 완료.
- loopback/내부 게스트망의 1883 포트 모두 닫힘.
- mosquitto 원래 서비스는 inactive/dead, PID 0 유지.
- VM·펌웨어·게스트 파일·자동 시작·시스템 서비스 변경 없음.

## 증거

- [수신 스냅샷·집계·모듈 해시](evidence/display-253-board-20261027/board-current-collector-debian-20261027.json)
- [별도 SSH 재조회로 정리 확인](evidence/display-253-board-20261027/board-current-collector-cleanup-20261027.json)
