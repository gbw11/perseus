# Linux·Agent 건강 관측·로컬 복구 연결 요청

> 2026-11-01 6-A: 담당자의 현재 작업·수정 위치·선행조건·회신은 [현재 요청서](20261029-request-linux-agent.md)에서 읽는다. 아래 ID별 상세 정의와 당시 시험/요청 이력은 보존한다. 현재 하위 제공 범위와 설치 수락은 구분한다.

수신: Linux 엔지니어·Guest Agent 담당자. 실제 담당자 배정은 팀에서 확인한다.
**상태: D01-L01~L05 요청 작성·플랫폼 근거 제공 완료 / 담당자 구현·연동·실보드 수락 전.**
아래 ID는 기존 요청 ID를 유지한 문서 내 추적 ID이며 새 Jira 티켓이 아니다.

## 현재 할 일

| 순서·요청 ID | 구현할 일 | 완료를 확인할 근거 |
|---|---|---|
| 1 · [D01-L01](#d01-l01) | 기존 OS·process 자원의 실제 표본 구간·유효성·실행 수명을 연결 | 첫 표본·읽기 실패·PID 재사용·목록 잘림을 정상값으로 오인하지 않음 |
| 2 · [D01-L02](#d01-l02) | 필수 앱의 실제 작업 완료와 서비스 실행을 연결 | Agent 생존과 앱 진행을 구분하고 idle을 장애로 판정하지 않음 |
| 3 · [D01-L03](#d01-l03) | Agent 수집·송신·자체 생존의 실패를 구분 | collector 정체·Agent 종료·앱 종료·broker 단절의 원인과 행동을 구분 |
| 4 · [D01-L04](#d01-l04) | 기존 systemd 로컬 감독과 VM 복구의 경합 방지 | 같은 기능의 중복 물리 복구 0, 사용자 stop·원 작업 보호 |
| 5 · [D01-L05](#d01-l05) | 실제 설치 경로·의존성·신원과 소비 연결을 인수 | import/enable·전달·원천 계측·정책 행동을 각각 확인 |

## 함께 읽을 파일과 순서

같은 소스 후보를 기준으로 아래 순서로 읽는다. 파일을 따로 전달할 때도 이 상대 경로를 유지한다.

| 순서 | 파일·읽을 부분 | 필요한 이유 |
|---|---|---|
| 1 | 이 문서의 현재 할 일과 D01-L01~L05 | Linux·Agent의 수정 위치·입출력·판정·수락 assertion |
| 2 | [공통 건강 판정 계약](../contracts/health-evidence-v1.md) | 자원 경고·기능 실패·관측 불가와 행동 책임의 공통 기준 |
| 3 | [MQTT 계약](../contracts/mqtt-contract-v1.md) 2·3·6·9·11장 | 현재 봉투·Agent/앱 신호·ACL·복구 사건. 새 필드가 필요한 부분은 별도 합의 |
| 4 | [Worker 요청](2026-10-27-recovery-health-ctrl.md)의 W02~W04 | Linux 제공 근거를 소비하는 쪽의 유효성·로컬 감독 경합·회복 조건 |
| 참고 | [건강 판정 검증 보고](../verification/health-evidence-20261027.md) | 이미 제공한 플랫폼·Zephyr 근거와 남은 제품/실보드 수락 |

## 공통 원칙과 경계

**CPU·메모리·스레드 상태를 정식 건강 판정의 입력으로 사용해야 합니다. 다만 그 결과는 모두
‘VM 재시작’으로 이어지는 것이 아니라, 경고·준비 제한·기능 축소·승계·앱 재시작·VM 복구 중
적절한 행동으로 연결돼야 합니다.**

<a id="d01-l01"></a>
### D01-L01 — OS 자원·수집 유효성

- 대상: `platform/services/vm-agent/agent/xenova_agent/metrics.py`의
  `MetricsCollector.collect/_cpu_values/_memory_values`, `procfs.py`, `processes.py:ProcessSampler.sample`.
  기존 guest `/proc/stat` CPU는 할당 vCPU 전체 100%, process CPU는 한 코어 100%다.
- 요청: 원 boot/Agent instance, 실제 표본 구간·reset/역전·read/parse 실패·부분 process 목록을
  유효성과 함께 소비 가능하게 한다. process의 PID/starttime은 현재 내부에만 있으므로 이름/PID만으로
  앱 수명을 판단하지 않는다. top-N 밖의 서비스 부재를 서비스 종료로 해석하지 않는다.
- 권고: 기존 values/processes와 null/reason을 유지하고, 서비스용 근거는 등록된 필수 unit·PID/cgroup에
  한정한다. 지원되는 환경에서 PSI·OOM 원인·cgroup memory.events를 선택형 근거로 검토하되,
  미지원/권한 오류는 명시한다. 모든 thread 상시 발행이나 새 공개 지표를 선행조건으로 하지 않는다.
- 행동: CPU가 높아도 필수 작업이 정상 완료되면 정상 부하일 수 있다. 합의한 자원 여유·지속 조건
  위반은 경고/제한의 근거이며, 단일 높은 CPU·낮은 MemAvailable만으로 새 자동 경고 규칙을 만들지 않는다.
  확인된 할당 실패·OOM으로 필수 서비스가 실제 중단된 경우 해당 기능을 제한하고 D01-L04의
  감독/Worker에 원인을 넘긴다. VM 재시작기는 추가하지 않는다.
- 설정·선행: 현재 metrics 기본 2초·top process 10개, payload 상한 60KiB는 소스값이다.
  CPU 90%·메모리 90% 같은 새 건강 상수는 미합의다. 사용자 지정 앱의 필수 자원/완료 기한이 먼저 필요하다.
- 수락: 정상 고부하+정상 완료에 VM 복구 호출 0, 첫 표본/역전/read 실패는 null+reason,
  PID 재사용·top-N 잘림으로 이전 앱 진행 회복 0, 실제 OOM 종료는 단순 CPU 경고와 다른 이유를 남긴다.

<a id="d01-l02"></a>
### D01-L02 — 필수 앱 서비스·실제 진행

- 대상: Agent `__main__.py:main`, `reporter.py:Reporter._metrics_payload/tick`,
  실제 필수 앱의 systemd unit과 업무 완료 연결부. 현재 process 통계·Agent heartbeat는 있지만
  일반 앱 서비스의 의미 있는 완료/입력 부족 계약은 없다. 임의 unit 이름을 제품 설정으로 추가하지 않는다.
- 요청: 앱 담당자가 필수 작업, 입력 유무, warm-up, 실제 완료 순번/오류, 적용한 제어 문맥을
  제공하고 Linux 담당자가 unit/현재 MainPID·starttime·서비스 실행 수명과 결합한다.
  원 timestamp·완료 순번을 보존하며 Agent 송신이 앱 진행을 대신 생성하지 않는다.
- 권고: 기존 앱 연결부의 제한된 최신 상태를 우선 사용한다. 원격 앱 restart API·새 daemon·
  범용 플러그인 시스템을 먼저 만들지 않는다. 새 필드가 필요하면 v1 스키마/소비자와 최소 확장을 합의한다.
- 행동·회복: 입력 없음/정상 이벤트 대기는 기능의 준비 부족 또는 정상 idle이다. 유효 작업이 있는데
  기존 기한을 넘겨 완료가 없으면 해당 서비스 기능 실패다. 현재 실행의 새 완료와 준비 유지로 회복을 확인한다.
- 우리 제공물: Zephyr `xdh_work/xdh_assess`, 실제 progress/readiness 분리, 실제 실패/기한 fixture와
  Manager 실행 바인딩. Zephyr의 시간값을 Linux의 보편 정책으로 복사하지 않는다.
- 수락: Agent만 살아 있는 앱 정체를 정상으로 판정하지 않음, 일이 없는 앱은 deadlock 판정 0,
  서비스 재시작/같은 PID 재사용/지연 보고가 이전 완료를 현재 준비로 되살리는 횟수 0.

<a id="d01-l03"></a>
### D01-L03 — Agent 자체 건강·전달

- 대상: `heartbeat.py`의 송신 loop, `reporter.py:Reporter.tick`, `__main__.py`의 두 thread 종료,
  `deploy/xenova-agent.service`, 공용 MQTT의 실제 연결/구독 상태(제품 공용 코드 수정은 소유자 확인 후).
- 요청: 수집 진행, 원천 오류, 마지막 성공, 송신 실패/크기 초과/오프라인 폐기를 구별한다.
  Reporter가 멈춰도 별도 heartbeat가 계속될 수 있다는 현재 구조를 보존하고 명시한다.
  Agent 자신의 heartbeat로 자기 collector 전체를 보증하지 않는다.
- 권고·설정: 현재 heartbeat 5초, metrics 2초, `Restart=always`, `RestartSec=2`, stop 10초다.
  종료된 Agent는 기존 systemd가 감독하되 살아 있는 정체 검출/Watchdog 지원은 아직 연결되지 않았다.
  추가 watchdog을 채택하려면 무엇을 실제 진행으로 세는지·실패 종료 조건을 먼저 확정한다.
- 행동: broker 단절은 전달 오류다. Agent 단독 종료를 앱 사망/VM 확정 장애로 바꾸지 않는다.
  수신측이 Agent를 관측하지 못할 때 외부 감독 근거와 per-VM 전달 상태가 없으면 원인을 보류한다.
- 수락: collector만 정체/Agent만 종료/앱만 종료/broker 단절을 각각 주입해 다른 원인·행동이 나오고,
  재연결 때 오프라인 과거 표본·READY를 새 진행으로 재생하지 않는다.

<a id="d01-l04"></a>
### D01-L04 — 지원되는 로컬 복구·VM 복구 경합

- 대상: 실제 필수 서비스 unit의 `Restart/StartLimit*/Timeout*`와 Agent 감독,
  Worker `watcher.py`, `engine.py`의 소비 연결(Worker 제품 수정은 해당 담당자).
- 요청 입력: 서비스 실행 수명, 확정 종료 원인, 로컬 복구 시작/진행/성공/실패·소진,
  원 기능의 회복 근거. 아직 이런 공개 wire 필드는 없으므로 구현된 것처럼 샘플을 만들지 않는다.
- 권고: 지원되는 systemd 서비스만 로컬 감독이 소유한다. Worker는 로컬 복구 중인 동일 기능에
  VM 복구를 경쟁 제출하지 않되, 유예를 매 heartbeat로 무한 갱신하지 않는다. 유예 상한은 실제 unit의
  restart/backoff·시작 기한에서 산정하고 담당자가 확정한다. 임의 새 1회 재시작 기본값은 넣지 않는다.
- 금지: user stop·원 operation/attempt·기한/예산을 덮지 않음, 공유 자원 정리 없는 thread abort,
  자동 force, 모든 앱 공통 restart 순서, broker 장애로 반복 VM restart.
- 회복: unit active가 아니라 현재 실행의 실제 업무 완료·필수 자원/입력·준비 확인이다.
- 수락: local restart 진행/소진 중 Worker 경합에서 실행 소유자가 하나이고, user stop 이후 신규
  create 0, 정상 기능 회복 전 episode/시도 예산을 신호 한 번으로 초기화하지 않는다.

<a id="d01-l05"></a>
### D01-L05 — 배포·인수

- 대상: `agent/deploy/install_into_rootfs.py`, `xenova-agent.service`·identity drop-in,
  `platform/guests/common/prepare_instance.py`, `platform/scripts/prepare-xenova-guests.py`,
  `platform/first-install/prepare-xenova-deployment.py`의 factory seed 경로.
- 요청: 소스/버전·Paho·공용 xenova_mqtt·unit의 실제 설치 바이트와 일반 VM/factory 신원 조건을
  고정한다. 기존 UUID·hostname·MAC/IP·인증정보를 재발급하지 않는다.
- 선행·제공물: 현재 템플릿 Paho/Agent 설치 경로와 Manager execution-context, Bootstrap/receipt
  제공은 이미 존재한다. 이는 새 건강 소비가 설치·연결됐다는 뜻은 아니다. runtime import·enable,
  broker 전달·원천 계측·정책 행동을 별도 수락한다.
- 수락: 정상 고부하/정상 idle/앱 정체/앱 종료/Agent 종료/OOM/전달 단절의 동일 입력 시험에서
  단위·실행 수명·판정·허용 행동·회복 근거를 대조한다. 실제 board/새 템플릿 배포는 별도 승인/인수다.

## 요청 이력과 후속 책임

| 이력 | 보존한 의미·상태 |
|---|---|
| 2026-10-27 · D01-L01~L05 최초 요청 | 기존 `management-integration-request.md` 6절에 추가한 다섯 요청을 이 문서로 옮겼다. ID·수정 위치·조건·수락 기준을 유지한다 |
| 문서 분리 | 담당자 구현 완료나 Linux 배포 수락을 뜻하지 않는다. 과거 117번의 담당 이력을 현재 소유자로 추정하지 않는다 |
| D03 인수 | 고부하의 정상 가능성과 합의된 여유/지속 조건을 명확히 했다. 하위 receipt·recovery 수습은 Linux 필드나 supervisor 계약을 바꾸지 않으며, D01-L01~L05 구현·수락은 계속 대기다 |
| 관리 요청 연동 | D02·D08은 [HTTP·Spring·화면 요청서](../platform/management-integration-request.md)에서 별도로 추적한다. 하위 완료·상위 미구현 상태를 유지한다 |

서비스별 필수 작업·입력·시작 기한과 새 wire 필드가 필요한 경우는 해당 앱·Worker·계약 담당자와
확정한다. 이 요청이 제품에 연결되기 전에는 건강 판단·로컬 복구가 이미 지원된다고 표시하지 않는다.
각 요청 ID에 담당자 후보·시험 결과·설치/연동·실보드 수락 상태를 기록한다.
