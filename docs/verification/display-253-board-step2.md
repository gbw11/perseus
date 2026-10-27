# 253번 실보드 보고 수신 확인 — 2026-10-27

## 결론

**VM3 Debian의 실제 기존 Agent → dom0 임시 브로커 → 표시 수집기 heartbeat 수신 경로 통과.** VM1·VM2 앱 보고 및 상시 브로커 배포 검증은 미완료다.

선행: [실보드 UUID·역할 설정·실행 상태 대조](display-253-board-step1.md). 이 문서 역시 253번의 검증 기록이며, 실제 시험에 사용한 보드 배치는 `70c3ed8` 기반이다. 기존 253번 구현 코드나 256·257번 브랜치 내용은 변경하지 않는다.

## 기존 상태를 먼저 확인

- dom0 mosquitto 프로그램은 설치됐으나 서비스 inactive/dead, 1883 미수신.
- 게스트 내부망 xenbr0는 `192.168.77.1/24`; Debian은 `192.168.77.20`.
- Debian SSH에서 `/sys/hypervisor/uuid`가 `055226af-b876-4707-8b5d-99e7f33ad3d7`인 것을 먼저 확인했다. 실제 `/etc/os-release`는 Debian 13.
- Agent 파일은 `/opt/xenova/agent`, MQTT 라이브러리는 `/opt/xenova/mqtt-client`에 있고, 해당 배포용 PYTHONPATH에서 모듈을 찾을 수 있다. 일반 Python 검색 경로의 모듈 미발견을 설치 실패로 판정하지 않는다.
- `systemctl show xenova-agent.service` 직접 조회는 active/running, PID 465. 단위 목록의 제한된 출력에서 항목이 안 보인 것과 실제 서비스 부재를 구분했다. Agent를 새로 시작하거나 재시작하지 않았다.
- Agent 기본 설정은 보드 내부망 `192.168.77.1:1883`, heartbeat 5초다. 기본값 조회를 실행 중 프로세스 환경의 증거로 간주하지 않았고, 이후 실제 broker 접속 로그로 경로를 확인했다.

## 사용자 승인한 임시 시험

최대 60초 동안만 내부 게스트망/loopback 리스너와 VM3 heartbeat·inventory·metrics 토픽 ACL을 허용했다. 실제 실행은 약 **17.3초**. 독립 timeout으로 브로커 수명을 제한했고 기존 systemd 서비스·자동 시작 설정은 변경하지 않았다.

브로커는 mosquitto 사용자로 실행했다. 이 시험은 제한된 내부망의 anonymous + 토픽 ACL이며 **운영 인증·자격 증명·ACL 검증을 대체하지 않는다.** 제어/주행 명령 토픽은 허용하지 않았다. 수집기는 구독만 했다.

실제 broker 로그에 `192.168.77.20`의 기존 Agent 클라이언트 접속을 확인했다.

| 항목 | 결과 |
| --- | --- |
| VM3 heartbeat | 실제 3건, 수집기 채택 3건 |
| UUID | 실제 Debian UUID와 보고 UUID 일치 |
| heartbeat seq | 1, 2, 3 |
| 표시 수집기 Agent 관측 | fresh, 마지막 관측 나이 526ms |
| Xen 상태 | running 유지 |
| 중계 종료 후 | disconnected / subscription unavailable, fresh로 유지하지 않음 |
| inventory | 실제 1건 도착, 수집기 채택 0건, observed OS는 null 유지 |

inventory가 heartbeat보다 먼저 도착한 순서와 현재 수집기의 “heartbeat로 부팅 세대를 먼저 확정”하는 검사 규칙을 확인했다. 다만 이번 기록은 거부 사유를 코드 경로별로 추적하지 않았으므로 정확한 거부 원인을 확정하지 않는다. OS 보고를 채택했다고 기록하지 않는다. 기존 합의의 heartbeat 우선 범위와 추가 inventory 수신 개선 필요를 분리한다.

## 시험 후 정리 확인

- `127.0.0.1:1883`, `192.168.77.1:1883` 모두 닫힘.
- mosquitto 시험 프로세스 없음, `/run/xenova-display-253-*` 디렉터리 없음.
- 기존 mosquitto 서비스는 inactive/dead로 유지.
- 패키지 설치·게스트 파일 변경·Agent 재시작·VM/펌웨어 변경 없음.

## VM1·VM2와 다음 의존성

배포 메타데이터는 각각 `zephyr-drive-a` / `.77.2`, `zephyr-drive-b` / `.77.3`, 둘 다 `zephyr-xenvm-starter-arm64` 1.1.0이다. 이 메타데이터에는 실제 app_id가 없었다. 템플릿/파일 존재만으로 autonomous/manual 송신 또는 주행 앱 준비를 주장하지 않는다.

이번 브로커는 VM3 상태 토픽만 허용했으므로 Zephyr 앱 미수신을 실패 증거로 사용하지 않는다. 실제 Zephyr 펌웨어의 app_id·broker 주소·인증·보고 지원을 담당자와 대조한 뒤, 해당 토픽 관측을 별도로 준비해야 한다. 이번 시험에서 Zephyr 펌웨어·콘솔·주행 제어에 접근하지 않았다.

기계 판독 근거:

- [최초 dom0 수신 준비 상태](evidence/display-253-board-20261027/board-reports-step2-20261027.json).
- [실제 Debian UUID·OS·Agent 상태](evidence/display-253-board-20261027/board-debian-agent-preflight-20261027.json).
- [실제 수신·단절 상태와 broker 로그](evidence/display-253-board-20261027/board-debian-report-probe-20261027.json).
- [실제 포트/프로세스/임시 파일 정리 및 Zephyr 메타데이터](evidence/display-253-board-20261027/board-report-cleanup-20261027.json).

원본 결과와 검사 스크립트는 로컬 outputs/display-253에도 보관했다. 검증 당시 Jira 상태나 Git 브랜치를 변경하지 않았으며, 이후 사용자 요청으로 이 검증 문서와 JSON 근거만 253번 브랜치에 커밋한다. 지라 완료 처리나 푸시는 포함하지 않는다.
