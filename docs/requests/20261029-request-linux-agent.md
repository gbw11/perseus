# Linux·Agent 후속 구현 요청

담당: Linux 엔지니어·Guest Agent. 상태: **6-A 인계 준비 / 담당 제품·설치본 수락 전**.
2026-11-01 현재 지시다. D01-L01~L05와 [상세 정의](2026-10-27-linux-health-agent.md)를 유지한다.

## 현재 담당자가 할 일

수집·앱 진행·전송·로컬 감독을 구별하고 검증한 설치 제공물을 플랫폼에 넘긴다.
Linux VM의 host 실행·CPU 배치·backend는 플랫폼 책임이다. Agent가 affinity를 보정하거나 새 host 관리를 구현할 필요는 없다.
아래 위치는 `platform/services/vm-agent/` 기준이다.

| ID·상태 | 수정 위치·무엇을 왜 고치는가 | 정상 수락·실패 보호 |
|---|---|---|
| D01-L01 / 구현·수락 전 | `agent/xenova_agent/metrics.py:MetricsCollector`, `procfs.py`, `processes.py:ProcessSampler`. 표본 구간·분모·boot/instance·PID/starttime·reset·잘림·읽기 실패를 보존한다 | 정상 0과 첫 표본/미지원/실패를 구별. PID 재사용·top-N 탈락·부분 열람을 앱 사망이나 정상 0으로 변환하지 않음 |
| D01-L02·L03 / 서비스 계약·구현 전 | `heartbeat.py`, `reporter.py`와 실제 필수 앱의 진행 생산 지점. Agent 생존·collector 진척·실제 업무 완료·전송을 나눈다 | 정상 idle과 정체, Agent만 종료/앱 정상, Agent 정상/앱 정체, broker 단절을 각각 구분. 송신 횟수로 새 앱 진행 생성 0 |
| D01-L04 / 정책·공동 수락 전 | `agent/deploy/xenova-agent.service`와 실제 앱 unit. 지원되는 로컬 재시작/Watchdog와 Worker VM 복구의 소유·유예를 맞춘다 | 정상 앱 유지·유한 실패 종결, 사용자 stop 보존. 같은 기능 중복 복구·자원%만으로 restart·자동 force 0 |
| D01-L05 / 제공물·설치본 수락 전 | `agent/deploy/install_into_rootfs.py`, `xenova-agent-identity.conf`, Agent/MQTT pyproject와 entrypoint | 후보·의존성·unit·신원 공급을 고정하고 실제 수집→보고→소비 smoke 결과 제출. import/enable/서비스 생존을 기능 건강 완료로 확대하지 않음 |

## 플랫폼 제공물과 지원 조건

플랫폼은 Debian·Ubuntu의 선택 템플릿 준비·검증 복사·원 기한·실패 수습과 host 실행/관측,
OS 공통 정적 CPU 배치를 제공한다. [공개 관리 계약](../platform/cli-manager-spec.md)과
[현재 기준선](../verification/platform-management-baseline.md)이 원본이다.

일반 템플릿은 `platform/guests/common/guest_agent_template.py:prepare`과 기존 installer를 사용한다.
일반 VM은 `prepare_instance.py`→`prepare_rootfs.py:prepare_instance`, factory seed는
`prepare_deployment_seed`→identity unit/drop-in 연결을 따른다. 기존 Agent 설치 검증과
[배포 단위표](../platform/deployment-readiness.md#cohort)를 재사용하며 이미 게시한 template/hash를 제자리 수정하지 않는다.
패키지 갱신으로 guest UUID·MAC/IP·hostname·credential을 재발급하지 않는다.

## 오류·진단 비용의 담당 작업

`agent/xenova_agent/reporter.py`의 inventory/metrics 수집 예외와 `_loop`의
`log.exception("reporter tick failed")`를 실제 주기·handler로 계수한다.
이미 구별하는 collector·transport 오류를 합치지 않는다. 같은 cause의 반복 포장·stack·동기 sink 대기가 재현된 경계만 줄인다.
최초·서로 다른 내부 결함의 cause/stack, 표본 unavailable·누락·신원·구간과 필수 정리는 보존한다.
생성 설정의 기본값을 측정 결측·미지원·0·앱 준비에 적용하지 않는다.

collector 읽기 실패·transport 단절·다른 내부 결함·느린 sink를 각각 주입한다.
정상 수집/보고와 회복 후 실제 새 표본, 필수 종료, stack 횟수·큐·FD 잔존을 함께 제출한다.
모든 로그 비활성화나 오류 분류 축소를 개선으로 세지 않는다. 아직 실측하지 않은 비용은 개선 후보로 남긴다.

## 선행 제공물과 결정 담당

L05 회신에는 **소스 후보·hash/manifest, entrypoint, 실제 Python/외부 의존성, unit/identity drop-in,
환경 변수·파일과 권한, 일반 생성/seed별 smoke 명령·결과**를 넣는다. 비밀값 대신 공급 경로만 기록한다.
플랫폼은 검증된 제공물을 다음 template 빌드 입력·설치 receipt에 연결한다. Agent 내부 건강 판정 구현은 Linux/Agent 담당에게 남는다.
실제 앱별 필수 완료·idle·서비스 수명·로컬 복구 wire는 Linux/앱·Worker·계약 담당이 결정한다.
이 미정은 해당 서비스 건강 판정을 막으며 기본 Xen 조회·정지의 선행조건이 아니다.

## 수락·담당 회신

D01-L01~L05별로 후보(commit+diff), 변경 파일/기호, 정상·오류·회복 명령과 결과,
필요 제공물의 경로/hash·의존성·권한, 미수행 환경, 설치/enable/실계측/행동 수락 상태를 각각 회신한다.
Agent 미실행과 guest 협조 종료 무응답은 별개다. 전자는 host 관리가 가능해야 하고 후자는 기존 정지/권한 계약을 따른다.
[7번](../platform/deployment-readiness.md#stage7)의 동일 설치본·backend·실보드 수락은 이번에 실행하지 않는다.

## 변경 이력

아래는 6번 정리 전 원문이다. 당시 소스·실패·측정·요청을 보존하며 현재 실행 지시는 위 본문을 따른다.

<details>
<summary>2026-11-01 이전 요청·진행 기록 원문</summary>

# Linux·Agent 후속 구현 요청

대상: Linux 엔지니어·Guest Agent 담당자. **상태: 요청 작성 / 담당자 구현·연동·설치본 수락 전.** 기존 D01-L01~L05의 ID와 상태를 유지합니다. 이번 Zephyr/Gateway 최소 연결에서 Linux·Agent 제품 코드는 수정하지 않습니다.

## 현재 할 일과 읽는 순서

2026-11-01 진단 인계(D01-L01~L05 유지):
`agent/xenova_agent/reporter.py`의 inventory/metrics 수집 예외와 `_loop`의
`log.exception("reporter tick failed")`를 실제 주기·설정·handler에서 계수한다.
일부 수집/발행 오류는 이미 경고·unavailable로 구별하므로 이를 합치지 않는다.
지속 오류가 상위 tick까지 전파될 때 같은 cause의 중복 stack/형식화·동기 sink 대기가
발생하는지만 확인한다. 확인된 반복은 유한 요약/손실 계수로 제한하되 최초·서로 다른
내부 결함의 cause/stack, 원 표본의 오류/구간·신원은 보존한다. 신규 create의 설정 기본값을
계측 결측·미지원·0·앱 준비에 적용하지 않는다. Agent 제품은 이번에 수정하지 않았다.
수락은 collector 읽기 실패·transport 단절·서로 다른 내부 결함·느린 sink를 각각 주입하고,
collector/보고 진행·unavailable/누락 표시·필수 종료·회복 후 실제 새 표본과 stack 횟수/큐·FD
잔존을 확인하는 것이다. 실제 비용을 재지 않은 경로를 최적화 완료로 표시하지 않는다.

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

</details>
