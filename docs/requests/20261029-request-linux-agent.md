# Linux·Agent 후속 구현 요청

대상: Linux 엔지니어·Guest Agent 담당자. **상태: 요청 작성 / 담당자 구현·연동·설치본 수락 전.** 기존 D01-L01~L05의 ID와 상태를 유지합니다. 이번 Zephyr/Gateway 최소 연결에서 Linux·Agent 제품 코드는 수정하지 않습니다.

## 현재 할 일과 읽는 순서

1. [기존 Linux·Agent 요청](2026-10-27-linux-health-agent.md)의 D01-L01~L05를 읽습니다. 상세 정의와 원 수락 assertion은 이 원문을 따릅니다.
2. [건강 근거](../contracts/health-evidence-v1.md)와 [Worker 요청](20261029-request-recovery-worker.md)을 읽어 판정·행동 소비자를 확인합니다.
3. 아래 실제 소스·unit과 해당 guest 설치본을 대조한 뒤 변경·시험 결과를 연결합니다.

## 담당자 작업

| 연결 ID | 수정 위치·입출력 | 권고 구현·금지 효과 | 수락 |
|---|---|---|---|
| D01-L01 | `platform/services/vm-agent/agent/xenova_agent/metrics.py:MetricsCollector`, `procfs.py`, `processes.py:ProcessSampler` | 원 boot/instance·표본 구간·PID/starttime·목록 잘림과 오류를 보존. CPU/메모리의 분모·영역 의미를 유지 | 첫 표본·reset·읽기 실패·PID 재사용·top-N 탈락이 정상 0/앱 사망으로 바뀌지 않음 |
| D01-L02·L03 | `heartbeat.py`, 기존 Agent 수집/전송 경로, 필수 앱의 watchdog 연결부 | Agent 생존, 앱 실제 완료, collector 진행, 전송 상태를 구분. idle을 허용하는 앱 계약과 연결 | Agent만 정상/앱 정체·Agent 종료/앱 정상·broker 단절을 각각 주입해 올바른 근거와 행동 생성 |
| D01-L04 | `agent/deploy/xenova-agent.service`, 게스트 필수 앱 systemd unit | 지원되는 로컬 재시작/Watchdog와 Worker VM 복구의 중복 방지, 사용자 stop 유지 | 같은 기능의 경합 복구 0, 정상 앱 보존·유한한 실패 종결. 자원% 하나로 재시작하지 않음 |
| D01-L05 | 기존 Agent 패키징·entrypoint·설치 unit | 실제 의존성·신원·환경과 수집→보고→소비 연결 확인 | import·enable·서비스 생존·실제 계측·판정 행동을 각각 증명. 테스트 설치를 제품 설치 완료로 표시하지 않음 |

## 제공물과 남은 결정

플랫폼은 현재 execution·의도·CPU 배치 관측과 하위 수습 계약을 제공합니다. CPU·메모리·스레드 상태는 판정 입력이지만 행동은 경고·준비 제한·로컬 수습·VM 복구로 구분합니다. 앱별 idle/진행·필수 unit·복구 소유·실제 배포값은 Linux/Worker가 합의해야 합니다. 공개 필드가 추가로 필요하면 기존 계약에 자료형·유효성·크기·호환을 먼저 반영하고 임의로 발행하지 않습니다.

## 진행 기록

- 2026-10-29: 기존 미완료 요청을 이 시작점에 연결했습니다. 제품 수정·실보드 수락을 새로 수행하지 않았습니다.
