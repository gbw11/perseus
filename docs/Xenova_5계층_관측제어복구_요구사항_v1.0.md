# Xenova 다섯 계층 관측·제어·복구 요구사항 명세서

**버전:** 1.0 검토안  
**작성일:** 2026-10-23  
**범위:** 실차 자율주행 / 대칭 Zephyr A/B / 일곱 사건  
**요구사항:** 112개 (현황·충족 판정 없음)

## 문서 안내
사용자 사건 시나리오를 기준으로 한 요구사항 검토안입니다. 필드 이름은 기존 계약과 매핑할 논리적 예시이며, 새 API가 구현됐다는 뜻이 아닙니다.

## 목차
1. 문서 목적과 적용 기준
2. 일곱 시나리오와 전환의 의미
3. 계층 경계와 결정권
4. 공통 관측·신원·검증 요구사항
5. PL — 플랫폼·Xen 관측 및 복구 실행
6. AG — Linux Agent·Zephyr Telemetry
7. AP — 주행 앱·인지·수동 입력·미러링
8. MQ — MQTT·수집·API/화면 투영
9. GW — 제어권 중재·ESP32 출력·안전
10. 데이터 계약 — 기존 형식 재사용과 필요한 확장
11. OS별 적용 범위와 누락 방지 표
12. 시나리오 재현·수락 요구
13. 시간·정책·미확정 상세 결정
14. 이전 관측 요구와의 추적성
15. Zephyr 참조 구현과 이후 작업의 경계
16. 자료 근거와 요구사항 제안의 구분

## 01. 문서 목적과 적용 기준

본 문서는 실차 자율주행 프로젝트 Xenova의 관측·판단·제어·복구 요구사항 기준이다. 사용자 제시 일곱 사건 시나리오를 다섯 계층의 책임과 검증 가능한 요구로 구체화한다. 현재 구현 충족 여부, PASS/FAIL, 담당자별 진척률은 평가하지 않는다.

기준 버전: v1.0 검토안 / 작성일: 2026-10-23. 사용자 시나리오는 고정 기준이며, 세부 필드·조건·검증 규칙은 이 문서가 제안하는 명세다. 실제 안전 시간, 동기화 대상과 허용 오차, 정책 선택은 13장의 결정 항목을 승인한 후 동결한다.

실차·Jetson·RPi4/Xen·Zephyr A/B·Gateway·ESP32를 본 수락 대상으로 한다. 시뮬레이터·Mock·QEMU는 분리된 개발/시험 환경이며 실차 종단 수락을 대신하지 않는다.

Zephyr A/B는 동일 기능의 AUTO/MANUAL 주행 앱을 실행하며 어느 쪽이든 활성 또는 예비가 될 수 있다. A=자율 전용, B=수동 전용으로 고정하지 않는다. 인지 장애에 따른 모드 전환과 VM 장애에 따른 제어권 이전을 다른 사건으로 다룬다.

관측만으로 인계·동기화·복구가 구현되는 것은 아니다. 미러링, 모드 적용, 제어권 차단, 차량 출력, 재합류 절차를 담당 계층의 동작 요구에 포함한다. 반대로 관측 데이터가 있다는 사실만으로 운행 허가를 만들지 않는다.

CPU pinning 구현은 이미 별도 작업이다. 이 명세는 그 결과인 희망/실제 배치·정책 revision·관측 오류를 참조할 뿐, 새로운 pinning 명령이나 정책을 설계하지 않는다.

기존 CLI 기능 명세와 계층별 연동 현황표는 별도 문서로 유지한다. 본 문서에는 연동 완료 여부 열을 만들지 않는다. 소스 수정·보드 명령·배포·재부팅·커밋/푸시는 본 문서 작성에 포함되지 않는다.

### 요구 수준과 해석

| 구분 | 해석 | 허용하지 않는 해석 |
| --- | --- | --- |
| 필수 | 해당 시나리오 또는 관리 범위를 지원한다고 선언하려면 충족해야 한다. 각 계층 표의 동작은 별도 표시가 없으면 필수다. | 미구현 값을 unsupported로 표시하는 것만으로 필수 기능이 충족되는 것은 아니다. |
| 조건부 | 디스크·센서·스케줄러 기능처럼 해당 대상에 실제 적용되는 경우 필수다. 비적용이면 범위와 사유를 명시한다. | Zephyr에 Linux PID/RSS를 꾸며 넣거나, 센서가 없는데 물리 정지 확인으로 표시하지 않는다. |
| 논리 필드명 | 이 문서의 이름은 의미를 설명하는 제안이다. 기존 필드와 의미가 같으면 재사용하고 매핑을 기록한다. | 기존 전송 필드를 일괄 개명하거나 같은 의미의 ID/API를 중복 생성하지 않는다. |
| 수락 기준 | 다음 작업에서 수행할 시험과 기대 증거다. 현재 결과가 아니다. | 시험 정의만으로 구현·실보드 성공을 선언하지 않는다. |

## 02. 일곱 시나리오와 전환의 의미

아래는 사용자 제시 시나리오를 유지하면서 실패 시 대안을 명시한 것이다. 모든 정상 인계에는 일시적 출력 억제 구간이 있을 수 있다. 모드 유지 요구를 무정지·무손실 주행 보장으로 확대하지 않는다.

### 사건별 요구

| ID | 사건 | 주행 모드 | 제어권·VM 복구 | 준비 조건·안전 대안 |
| --- | --- | --- | --- | --- |
| S1 | Jetson 인지 장애 | 현재 활성 VM에서 AUTO→MANUAL 준비. 이미 MANUAL이면 모드 유지. | 기존 활성 VM 유지. VM 재시작 없음. | 인지의 생성/수신/처리 정체를 구분한다. 수동 입력·앱 준비·운영 정책 확인 후 모드를 적용한다. 준비 중 또는 수동 입력 불가이면 안전 정지. |
| S2 | 활성 Zephyr 장애 | 같은 모드의 유효성·입력·동기화 조건이 충족되면 유지. | 준비된 상대 VM의 새 실행에 제어권 이전. 고장 VM 복구. | 옛 실행 출력 차단 후 새 세대를 수락한다. 같은 모드 인계가 불가하면 별도 모드 전환 조건을 평가하고, 그마저 불가하면 안전 정지. |
| S3 | Zephyr 앱만 응답 중단 | 현재 모드와 입력의 유효성을 재검증. | 예비로 이전 검토. 앱 또는 VM 복구. | Agent 생존·Xen 실행과 계산 진행 정체를 구분한다. 앱 독립 재시작이 불가능한 펌웨어는 VM 복구를 사용하며 지원하지 않는 앱 복구를 성공 처리하지 않는다. |
| S4 | 인지 장애와 활성 VM 장애 동시 발생 | AUTO 유지 불가. 준비된 새 활성 VM에서 MANUAL 가능성 평가. | 동기화·MANUAL 준비가 유효한 예비로 이전 검토. 고장 VM 복구. | 두 사건을 결합해 AUTO 승격을 차단한다. 인계·수동 준비 중에는 안전 정지. 어느 준비 조건이든 미충족이면 운행하지 않는다. |
| S5 | Jetson 기능 회복 | 입력 안정성·앱 계산·복귀 정책이 충족되면 AUTO 복귀. | 현재 활성 VM 유지. VM 재시작 없음. | 한 개의 새 프레임만으로 복귀하지 않는다. 설정된 안정 구간과 승인 정책을 충족하고 모드 적용 결과를 확인한다. |
| S6 | 고장 VM 복구 완료 | 현재 모드 유지. | 현재 활성 VM 유지. 복구 VM은 초기화·동기화 후 STANDBY. | 새 실행은 출력 권한 없이 합류한다. 부팅·ping·heartbeat·앱 준비·동기화·예비 적격성을 각각 확인한다. 자동 원복/재탈취 금지. |
| S7 | 양쪽 Zephyr 사용 불가 | 운행 불가. AUTO/MANUAL 표시와 별개로 안전 정지. | 유효한 제어권 없음. 허용된 복구 시도. | Gateway 정지 요청과 ESP32 독립 watchdog/E-stop으로 안전 상태를 만든다. 둘 다 회복돼도 초기 권한·준비 확인 전 자동 출발 금지. |

### 독립적으로 관리할 상태 축

| 상태 축 | 의미/대표값 | 원본·판정 책임 | 다른 상태로 대체하면 안 되는 것 |
| --- | --- | --- | --- |
| VM 의도 | desired_state, autostart, intent_revision | PL: 수락된 사용자 의도·부팅 정책 | running 의도 ≠ 실제 도메인 존재 |
| Xen 실행 | presence, xen_state, execution_id | PL: Xen 관측과 실행 대응 | blocked ≠ 앱 장애, paused ≠ 실행 중 STANDBY [T1] |
| Guest/Agent 생존 | boot/session, heartbeat 진행 | AG 생산, MQ 수신 검증 | Agent 생존 ≠ 계산 진행 |
| 앱 준비/진행 | init, loop 진행, 입력 처리, ready_by_mode | AP 생산, 판정자가 조건 검증 | 앱 실행 ≠ AUTO 또는 MANUAL 준비 |
| 주행 모드 | 요청 모드·적용 모드·mode_revision | AP 판단/적용 보고, GW 최종 적용 통제 | AUTO/MANUAL ≠ A/B 제어권 |
| 제어권 | owner VM+실행+앱 세션, authority epoch | GW 단일 중재, ECU 최종 수락 | 앱의 ACTIVE 주장 ≠ 실제 출력 권한 |
| 동기화/예비 적격 | 상태 세대·반영 버전·모드별 인계 가능 | AP 근거 생산, GW 승격 판단 | 최신 heartbeat ≠ 미러링 완료 |
| 안전 출력 | 허용·차단·정지 요청·적용 확인·물리 정지 확인 | GW/ESP32 및 실제 센서 | 정지 프레임 전송 ≠ 차량 실제 정지 |

## 03. 계층 경계와 결정권

같은 관측값의 생산·전달·소비는 여러 계층에 걸칠 수 있지만, 동일 사실의 원본과 최종 결정권은 중복시키지 않는다.

### 다섯 계층

| 계층 | 포함 범위 | 직접 책임 | 직접 판단하면 안 되는 것 |
| --- | --- | --- | --- |
| PL · 플랫폼/Xen | Xenova Manager, Xen driver, 수명주기·복구 실행 서비스 | 등록/의도/실행 신원, Xen 상태, 배포·자원, 작업 결과. 복구 Worker는 그 사실을 이용하는 정책 호출자. | CPU·ping만으로 앱 준비, 동기화, 차량 출력 권한을 판정하지 않음. |
| AG · Guest Agent | Linux Agent와 Zephyr 경량 Telemetry | OS 내부 inventory/metrics, 생존, 수집기 건강, 앱 계측의 사실 전달. | Agent가 주행 모드나 제어권을 독자 변경하거나 앱 진행을 대신 생성하지 않음. |
| AP · 주행 앱/입력 생산자 | 동일 A/B 앱, Jetson 인지, 수동 입력 생산자 | 계산 진행, 입력 생성/검증/처리, 모드 적용, 상태 동기화·준비 근거, quiesce. | 앱이 스스로 최종 제어권을 발급하거나 상대 VM 재시작을 독점 실행하지 않음. |
| MQ · MQTT/수집·조회 | 브로커, 클라이언트, 수집기, API·화면으로의 관측 투영 | 스키마·인증·전달/구독/시퀀스·신선도·보관·호환성. | 전달 성공을 계산/ECU 적용 성공으로 바꾸거나 관측 손실을 VM crash로 단정하지 않음. |
| GW · Gateway/ESP32 | 제어권 중재기와 물리 출력 적용 지점 | 승격·모드 commit·옛 출력 차단, 명령 수락·적용, watchdog/E-stop, 독립 안전 정지. | VM 재생성 자체는 PL을 통해 수행. Agent·MQTT 상태로 물리 정지 증거를 대체하지 않음. |

### 결정권의 연결

| 결정 | 근거 생산 | 최종 승인·실행 | 필수 경계 |
| --- | --- | --- | --- |
| AUTO/MANUAL 변경 | AP: 입력·모드별 준비, 사용자/자동 정책 요청 | GW: 승인된 정책으로 commit, AP 적용 결과 확인 | owner를 유지할 수 있고 mode revision만 바뀔 수 있음. |
| A/B 제어권 이전 | PL: 실행, AP: 모드별 동기화/준비, MQ: 유효성 | GW: 옛 권한 차단·새 권한 발급, ESP32: 수락 | 앱·heartbeat·MQTT 계정 이름만으로 승격하지 않음. |
| 앱/VM 복구 | PL/MQ/AP: 장애 증거·정지 의도·정책 | 복구 서비스가 PL의 기존 관리 계약을 호출 | 주행 인계는 VM 복구 완료를 기다리지 않음. |
| 물리 안전 정지 | GW 명령 또는 ECU의 자체 감시 | ESP32와 하드웨어 E-stop | dom0·브로커·두 VM이 없어도 가능한 경로. |

## 04. 공통 관측·신원·검증 요구사항

논리 데이터 필드는 기존 Manager envelope와 MQTT 봉투를 대체하는 새 단일 API가 아니다. 필요한 생산자가 자기 범위의 값을 만들고, 수신·집계 측이 근거와 함께 결합한다. [S1][S4]

### 요구사항

| ID / 항목 | 필수 동작·책임 | 제공 정보·입력·소비자 | 향후 수락 기준·실패 처리 |
| --- | --- | --- | --- |
| CO-01<br>사실과 판정 분리 | 실제 관측값, 생산자 자기 보고, 집계 판정, 정책 결정, 미관측을 구별해야 한다. | source, value, validity, reason, evidence_refs, decision_policy_revision | 동일한 유효 관측이 fault 상태를 보고할 수 있다. valid를 healthy로 치환하지 않는다. |
| CO-02<br>영구/실행 신원 | vm_id를 영구 신원으로 유지하고 host·VM 실행·guest 부팅·Agent/앱 실행을 서로 구분한다. | vm_id, host_boot_id, execution_id, domid, xen_unique_id, boot_id, agent_instance_id/app_session | 재시작 후 옛 실행의 보고·작업·명령이 새 실행에 적용되지 않아야 한다. ID 값 자체는 인증 토큰이 아니다. |
| CO-03<br>신원 연결 | 게스트 보고와 현재 PL 실행의 대응은 신뢰된 프로비저닝/채널 및 실행 확인을 통해 결합한다. | binding 상태와 근거, 보고자의 권한, 유효 실행 구간 | 불명확한 실행 대응을 UUID 문자열 일치만으로 승인하지 않는다. 임의 fallback UUID나 복제된 공통 세션 금지. |
| CO-04<br>값의 유효성 | VALID/STALE/UNKNOWN/ERROR/UNSUPPORTED/NOT_APPLICABLE에 준하는 구별과 사유를 제공한다. | value=null 및 사유, last_success, last_attempt, stale 기준 | 실패를 0·빈 배열·정상으로 바꾸지 않는다. last_success는 과거 값으로만 표시한다. |
| CO-05<br>시계 도메인 | UTC와 단조 시계를 구분하고 단조 시계는 해당 host/boot/session 범위에서만 계산한다. | clock_domain, producer_uptime, received_monotonic, time_quality, UTC 동기화/오차 정보 | 동기화되지 않은 장치 시각을 직접 빼서 지연을 만들지 않는다. uptime를 가짜 UTC로 저장하지 않는다. |
| CO-06<br>세션·순서·지연 | 시퀀스의 범위를 발행자·실행 세션·논리 스트림으로 정의하고 지연/역전/중복을 배제한다. | stream, seq, message_id, session binding, retired-session 정보 | 새 세션 ID라는 이유만으로 옛 세션을 재활성화하지 않는다. 재수신은 원 측정의 신선도를 갱신하지 않는다. |
| CO-07<br>단위와 측정 범위 | 각 값의 단위·누적/구간/순간 유형·측정 구간·재설정 조건·분모를 명시한다. | unit, metric_kind, window, counter_reset, sample_count | CPU·스택·메모리 수치를 다른 의미로 합산하지 않는다. wrap/reset 후 거대한 가짜 rate를 만들지 않는다. |
| CO-08<br>원본·변경·스냅샷 | 변경 이벤트와 주기 스냅샷, 유효기간 경과를 함께 지원해야 한다. | event revision, snapshot revision, observed age, missed-event/gap 표시 | 변경이 없다고 영구 정상으로 남지 않는다. 유실 후 전체 스냅샷으로 재동기화 가능해야 한다. |
| CO-09<br>기능 선언 | OS/앱/펌웨어별 capability와 실제 가용 상태를 분리한다. | 지원 metric/command, schema versions, runtime availability | 필수 인계 신호를 제공하지 않는 VM은 준비 완료로 선언하지 않는다. unsupported는 필수 요구 면제가 아니다. |
| CO-10<br>상관관계 | 원인 사건·명령·모드 전환·권한 이전·복구 시도·새 실행을 연결하되 각각의 ID를 보존한다. | incident_id, operation_id, recovery_attempt_id, transition_id, causation/correlation | 중복 보고가 중복 복구나 중복 승격을 만들지 않아야 한다. 실시간 명령마다 영구 기록을 강제하지 않는다. |
| CO-11<br>경합과 중단 | 판정부터 실행 사이에 의도·실행·역할·준비 조건이 바뀌면 재검증한다. | expected revision/identity, admitted context, deadline, result | 동시 stop·복구·인계·호스트 종료에서 오래된 요청이 최신 의도를 덮지 않는다. |
| CO-12<br>관측의 비간섭 | 계측·로그·문자열 생성·통신 큐의 CPU/RAM/시간 예산을 제한한다. | sampling cost, queue depth, drop count, log loss, overhead budget | 계측 실패가 주행 루프·종료·안전 출력 경로를 무기한 대기시키지 않는다. 계측 전후 부하를 비교한다. |
| CO-13<br>일곱 시나리오 불변식 | 모드·owner·안전 출력은 별도 상태로 관리하고 실제 명령 수락 지점에서 권한 단일성을 유지한다. | committed mode, mode_revision, owner tuple, authority epoch, safety state | 어떤 사건 순서에도 두 실행의 일반 주행 출력이 동시에 유효해지지 않는다. |
| CO-14<br>검증 근거 | 시험 환경·입력·고장 주입·정책·소스/펌웨어 해시·측정 도구·누락 구간을 보존한다. | test_id, run_id, environment, revision, artifacts, observation gaps | Mock/QEMU/실보드/실차 증거를 구분한다. 관측되지 않은 결과는 성공으로 소급하지 않는다. |
| CO-15<br>시간 정책 분리 | 표시용 생존, 즉시 주행 보호, 복구 발동, 준비 대기, 정상 종료의 기한을 별도로 정의한다. | 13장의 timing profile와 선택 이유 | 느린 Agent heartbeat를 차량 정지 기한으로 사용하지 않는다. 기한 미승인 항목은 시간 수락 불가로 남긴다. |

## 05. PL — 플랫폼·Xen 관측 및 복구 실행

적용: dom0와 등록된 Zephyr·Debian·Ubuntu 등 관리 VM. 외부 실행 사실은 Xen 관측을 원본으로 하며, guest 내부 상태를 추측하지 않는다. CLI/Manager 기능과 HTTP 노출 여부의 현황은 별도 문서에서 평가한다. [S4][T1]

### 요구사항

| ID / 항목 | 필수 동작·책임 | 제공 정보·입력·소비자 | 향후 수락 기준·실패 처리 |
| --- | --- | --- | --- |
| PL-01<br>등록과 존재 | 등록 수명주기와 실제 도메인 존재를 각각 조회해야 한다. | vm_id, display/internal name, lifecycle/generation, presence, registered | 도메인 부재·미등록·보관·삭제·조회 실패를 구별한다. 미등록 충돌도 진단 가능해야 한다. |
| PL-02<br>Xen 상태·실행 대응 | UUID/name/domid/incarnation과 관리 execution을 대조해 현재 실행 상태를 제공한다. | xen_state/raw flags, xen_unique_id, execution_id, managed/unmanaged | blocked를 fault로, paused를 STANDBY_READY로 표시하지 않는다. 관측 중 실행 변경은 재조회 또는 오류다. |
| PL-03<br>희망 상태와 정책 | 수락된 정지·실행 의도, autostart, 적용 가능 revision과 host admission을 제공한다. | desired_state, intent_revision, boot/host admission, shutdown_requested | 도메인 부재에서도 사용자 stop 의도를 반영한다. stopped VM을 장애 복구로 재시작하지 않는다. |
| PL-04<br>부팅 단계 기준선 | 실행 요청 수락·생성 관측 시점을 남기고 이후 guest 준비 제한시간의 기준을 제공한다. | launch requested/observed times, expected guest capabilities, execution_id | xl create 반환을 OS/Agent/앱 준비로 바꾸지 않는다. 첫 heartbeat가 아예 없는 경우도 감시 가능해야 한다. |
| PL-05<br>배포 원본 | 희망 템플릿·활성 펌웨어·빌드/설정 hash·Agent 설치 영수증을 조회 가능하게 한다. | template/firmware version, manifest hash, configuration revision | AG의 실제 버전과 비교할 원본을 제공한다. manifest를 실행 중 앱이 보고한 사실로 대신 표시하지 않는다. |
| PL-06<br>자원 설정과 적용 | 설정 vCPU·RAM과 Xen 실제 할당, 별도 CPU 정책의 실제 적용 결과를 구분한다. | configured/observed resources, scheduler/pool/affinity의 지원 범위, cpu policy revision | pinning 조회 계약을 재사용한다. 현재 실행 CPU를 affinity로 오인하지 않고, 배치 격리를 실시간 보장으로 표현하지 않는다. |
| PL-07<br>가상 I/O | 가상 NIC·블록 장치 연결과 Xen 측 누적 I/O를 OS 공통 관측으로 제공한다. | VIF/VBD 상태, MAC/예약 IP, rx/tx/blk counters, device reset | 예약 IP와 guest 실제 IP를 구분한다. 무디스크 Zephyr의 VBD는 비적용이며 0 사용률로 꾸미지 않는다. |
| PL-08<br>관측 유효성과 캐시 | 표본 시각·성공/실패·age·revision을 제공하고 제한된 비용으로 재조회/스냅샷을 제공한다. | snapshot, current, last_success/error, observed_monotonic, host_boot_id | stale·오류 표본을 현재 상태로 내지 않는다. 조회는 VM·정책을 변경하지 않고 잠금 대기에도 deadline을 적용한다. |
| PL-09<br>관리 작업 결과 | 생성·시작·정지·재부팅·보관·복원·삭제의 수락/진행/완료/실패/불확실을 추적한다. | operation_id, idempotency, phase, expected/run context, error/retryable | 요청 수락·명령 반환·실제 결과 확인을 분리한다. 재전달 시 중복 실행 금지. |
| PL-10<br>복구 경계 | 허용된 복구가 원 실행·의도·host 정책과 일치하는지 확인하고 새 실행을 결합한다. | recovery_attempt, old/new execution, trigger evidence, phase, attempts | 관측 불가를 강제 복구 이유로 바꾸지 않는다. 무한 재시작을 제한하고 자동 force 승격을 하지 않는다. |
| PL-11<br>인계와 복구 분리 | Gateway의 제어권 이전을 대신 수행하지 않으며, 정상 정리/복구와 출력 권한 경계를 연결한다. | fencing/transition context 참조, operation intent, recovered execution | 인계는 VM 재부팅 완료를 기다리지 않는다. 새 VM이 생겼다는 이유로 ACTIVE를 부여하지 않는다. |
| PL-12<br>협조 종료 | 게스트 종료 요청·수신·앱 quiesce·실제 제거를 구분하고 기한·거절·실패를 기록한다. | graceful capability/runtime readiness, shutdown stage, observed removal | 마지막 동기화 전 통신을 끊지 않도록 hook 계약을 지킨다. ACK를 도메인 제거로 간주하지 않는다. [S3] |
| PL-13<br>콘솔·오류 근거 | 부팅·panic·관리 오류 로그를 대상 실행과 연결해 접근·보관한다. | console source, execution, timestamps, truncation/rotation/loss, error code | 콘솔이 멈췄다는 이유만으로 VM crash 확정 금지. 로그 비밀 제거·용량 상한과 동시 접근 정책을 검증한다. |
| PL-14<br>호스트·공통 원인 | dom0 서비스·저장소·CPU/RAM·시계·관리망과 가능한 온도/전압/스로틀 상태를 제공한다. | host boot, service health, disk/mount, time quality, thermal/power availability | 호스트 원인을 여러 개별 VM 장애로만 표시하지 않는다. host 완전 장애는 호스트 외 관측/ECU로 확인한다. |
| PL-15<br>계측 제공 기반 | Agent·앱이 쓸 자기 신원·네트워크·빌드 문맥과 필요한 권한/의존성을 배포한다. | read-only identity/context, provisioning status, capability | 자격증명을 공통 이미지·manifest·콘솔에 포함하지 않는다. 제공 실패는 거짓 공통 UUID로 숨기지 않는다. |
| PL-16<br>실행/설정 변환 보존 | 재부팅·복구·프로필 변경 뒤 영구 UUID/허용 자산과 새로운 실행 문맥을 일관되게 유지한다. | MAC/IP reservation, current firmware, old/new execution linkage | 새 실행의 관측·통신 세션이 옛 실행과 섞이지 않아야 한다. 계획된 resource/CPU 변경 계약은 별도로 유지한다. |
| PL-17<br>장애 주입·격리 근거 | 격리된 시험에서 VM 부하·앱 중단·도메인 장애·재부팅을 식별 가능한 사건으로 기록한다. | injection_id, scoped target/execution, before/after, other-VM observations | 다른 VM의 loop/통신/콘솔을 함께 측정한다. 일반 관리 호출에 임의 장애 주입을 섞지 않는다. |

## 06. AG — Linux Agent·Zephyr Telemetry

적용: Debian·Ubuntu는 Linux 프로세스/시스템 의미를, Zephyr는 RTOS 스레드/heap/stack 의미를 제공한다. JSON 봉투·일반 inventory/metrics는 기존 계약을 우선 재사용하되, 서로 다른 OS 자원 모델을 같은 수치로 위장하지 않는다. [S1][S2][S5][T2][T3]

### 요구사항

| ID / 항목 | 필수 동작·책임 | 제공 정보·입력·소비자 | 향후 수락 기준·실패 처리 |
| --- | --- | --- | --- |
| AG-01<br>실제 inventory | 실행 중 OS·커널/펌웨어·앱·Agent 버전과 실제 인터페이스를 수집한다. | os.id/version, kernel_release, hostname, interfaces, app/agent build, capabilities | 설치 manifest 복사만으로 실제 런타임 확인을 대체하지 않는다. 최초 시작·정보 변경·재연결에 현재 inventory를 재확인한다. |
| AG-02<br>부팅·프로세스 식별 | guest boot와 Agent/앱 세션·uptime을 제공하고 PL 실행 대응을 지원한다. | vm_id, boot_id, agent_instance_id, uptime, execution binding | A/B 복제·앱 재시작·VM 재시작을 구분한다. 같은 부팅의 Agent 재시작을 guest 재부팅으로 표시하지 않는다. |
| AG-03<br>생존 보고 | Agent의 보고 기능이 실제 실행된 사실을 주기적으로 알리고 앱 생존과 구분한다. | heartbeat seq/producer session, report attempts/errors | 상태 판정은 수신 측이 한다. 끊긴 동안 누적한 옛 heartbeat를 재연결 때 생존 증거로 몰아 보내지 않는다. |
| AG-04<br>OS 준비 단계 | Agent 시작·기본 초기화·네트워크 준비·통신 준비의 실제 결과를 제공한다. | boot stage, stage reason, readiness prerequisites | 한 단계 실패가 모든 단계의 완료로 덮이지 않는다. stage 미관측·진행 중·실패를 구분한다. |
| AG-05<br>CPU 계측 | OS가 제공하는 CPU/스레드 실행시간을 수집하고 측정 분모와 구간을 명시한다. | guest CPU, thread runtime, sample window, vCPU count, validity | Xen CPU 시간과 guest 내부 스레드 비율을 구분한다. 첫 표본·reset·미지원에서 사용률을 추정하지 않는다. |
| AG-06<br>메모리 계측 | Linux 메모리와 Zephyr heap·stack의 범위를 명시해 사용/여유/peak를 수집한다. | used/available, heap allocated/free, stack capacity/unused, allocation failures | 16 MiB 등 VM 전체 할당량을 heap 사용량으로 표시하지 않는다. 서로 다른 allocator 통계를 중복 합산하지 않는다. |
| AG-07<br>프로세스/스레드 | Linux 프로세스와 Zephyr 스레드를 서로 다른 유형으로 보고한다. | pid 또는 thread id, name, state, priority, runtime, stack/RSS의 적용 범위 | Zephyr에 PID/RSS를 조작하지 않는다. 상위 N개 제한·sampling cost·권한 부족 이유를 제공한다. |
| AG-08<br>게스트 네트워크 | 실제 링크·주소·DHCP·내부 네트워크 오류와 지원되는 netfront 통계를 수집한다. | net_if state, assigned IP/MAC, tx/rx/drop, pool busy, protocol errors | PL 예약 주소와 불일치를 드러낸다. ping 성공만으로 MQTT·앱 준비 완료를 선언하지 않는다. |
| AG-09<br>Agent 통신 건강 | 연결·인증·구독·발행·재접속·큐 상태를 분리해 보고한다. | MQTT/transport state, last error, reconnect count, queue/drop | 보고 경로 자체가 끊기면 콘솔/제한된 로컬 진단에 근거를 보존한다. 송신 불가와 guest 정지 원인을 섞지 않는다. |
| AG-10<br>자체 수집 건강 | 수집기별 마지막 시도·성공·duration·오류·정체를 제공한다. | collector_id, last_success, elapsed, failure count, buffer overflow | Agent heartbeat가 계속되어도 수집 실패를 알 수 있어야 한다. 값 재사용 시 원 측정 시각을 유지한다. |
| AG-11<br>앱 계측 연결 | 앱이 갱신한 진행·준비·동기화 snapshot을 안전하게 읽어 전달한다. | AP의 snapshot revision, loop/input/output refs, reason | Agent가 loop_seq나 동기화 완료 번호를 대신 증가시키지 않는다. 찢어진 snapshot이면 불확실로 처리한다. |
| AG-12<br>동시성·비용 상한 | 계측은 주행 경로에 제한된 비용만 추가하고 공유 통신 객체의 소유권을 준수한다. | sample period, max payload, queue capacity, execution budget | MQTT 단일 소유 스레드 객체를 다른 스레드가 직접 호출하지 않는다. 메모리 부족 때 주행을 막는 무한 대기 금지. |
| AG-13<br>벽시계 부재 | Zephyr를 포함해 UTC를 모를 때 시간 품질과 uptime을 사실대로 보고한다. | clock valid flag, uptime, sample/receive time mapping | 업타임을 1970년 날짜로 꾸며 현재 메트릭으로 저장하지 않는다. 기존 필수 UTC 필드와의 호환 변경은 MQ 계약으로 승인한다. |
| AG-14<br>관측 capability | 지원·설정 필요·권한 부족·하드웨어 부재를 metric 단위로 구분한다. | metric capability, unit, unavailable_reason, OS type | 미지원은 0이 아니다. 필수 인계 계측 부족은 ready 조건을 만족시키지 못한다. |
| AG-15<br>재시작·오프라인 | 재시작·세션 변경 시 카운터/표본 기준을 재설정하고 stale 값을 새 보고로 재포장하지 않는다. | session changed, reset markers, sequence scope, lost sample count | 끊김 복구 직후 현재 값을 재수집한다. 옛 inventory를 최신 실행의 attestation처럼 사용하지 않는다. |
| AG-16<br>종료 연동 | 앱 quiesce/통신 종료 전후의 Agent 상태를 보고하되 종료 완료를 독자 선언하지 않는다. | quiescing, last telemetry, publish result, shutdown correlation | Telemetry 마무리 실패가 안전 정지를 무한정 지연시키지 않는다. guest 종료 최종 사실은 PL 제거 관측과 연결한다. |
| AG-17<br>참조 구현 범위 | Zephyr 참조 구현은 OS 계측·실제 루프 hook·메시지 생성·실제 수신 증거를 제공한다. | 공통 payload examples, OS별 mapping, contract vectors, unavailable cases | Linux Agent가 재사용할 것은 의미/계약/시험 벡터이며 Zephyr 수집 코드를 그대로 이식하지 않는다. 다른 계층 완료를 대신 주장하지 않는다. |

## 07. AP — 주행 앱·인지·수동 입력·미러링

적용: A/B 양쪽에 동일 기능을 제공한다. 아래 상태는 앱이 직접 갱신하는 사실이며, Agent/MQTT가 외부에서 추정하여 생성하지 않는다. 인지·수동 입력 생산자의 생성 신호도 포함한다. 제어권의 최종 승인자는 GW다.

### 요구사항

| ID / 항목 | 필수 동작·책임 | 제공 정보·입력·소비자 | 향후 수락 기준·실패 처리 |
| --- | --- | --- | --- |
| AP-01<br>대칭 A/B 기능 | 양쪽 앱이 AUTO와 MANUAL을 모두 수행하고, 역할은 실행 중 승인된 제어권을 따른다. | supported_modes, app build, vm/execution/session, role_observed | B에서도 AUTO 인계, A에서도 MANUAL 전환을 시험한다. 물리 코어·템플릿·표시 이름에 주행 역할을 고정하지 않는다. |
| AP-02<br>모드별 앱 준비 | 초기화, 필수 연결, 유효 입력, 계산 가능, 동기화 필요 여부를 분리해 ready_by_mode를 산출한다. | ready_auto, ready_manual, prerequisites, reason, policy/version | 하나의 ready=true로 두 모드를 대체하지 않는다. 준비 신호가 처음부터 오지 않는 경우도 검출 가능해야 한다. |
| AP-03<br>실제 계산 진행 | 실제 주행 계산 완료 지점에서 진행 번호와 마지막 정상 처리 시각을 갱신한다. | loop_started/completed_seq, last_complete_uptime, computation status | 주행 계산만 정지시켜 Agent/통신은 살아 있는 고장을 구별한다. heartbeat 송신만으로 loop_seq를 증가시키지 않는다. |
| AP-04<br>주기·실행시간 | 주기, 실행시간, 기한 초과, 건너뛴 tick과 통신 대기를 구별해 계측한다. | period/jitter, compute duration, scheduling lag, deadline_miss, sample_count | 관측 최대값을 이론 WCET로 표기하지 않는다. 누락 구간·계측 오버헤드·단위를 기록한다. |
| AP-05<br>인지 원천 진행 | Jetson은 캡처·추론·결과 발행의 실제 진행과 결과 유효성을 보고한다. | sensor/session, capture_seq, processed frame, inference status, result validity | 같은 오래된 프레임을 새 MQTT seq로 보내도 새 인지로 인정하지 않는다. 카메라 정지와 Jetson 프로세스 정지를 분리한다. |
| AP-06<br>인지 수신·처리 | A/B는 인지의 수신·검증·실제 처리 진행과 age를 각각 기록한다. | source session/frame, received/validated/processed_seq, rejection reason | 소스는 정상인데 경로가 끊긴 경우 원인을 불명확으로 남기되 AUTO 안전 조건은 해제한다. |
| AP-07<br>수동 입력 권한·신선도 | 허가된 조작 세션의 입력 생성·수신·처리와 연속성을 확인한다. | operator/session, input seq, deadman/connected, input age, neutral/brake | 브라우저 단절·손 놓음·오래된 조작을 구분한다. 새 입력이 없을 때 이전 가속 명령을 유지하지 않는다. |
| AP-08<br>인지 장애의 모드 요청 | 인지가 무효하면 현재 활성 VM에서 MANUAL 준비를 요청하고 출력 안전 조건을 적용한다. | perception fault reason, manual prerequisites, mode request | S1에서 owner/execution은 유지한다. 수동이 준비되지 않으면 정지하며 Zephyr 재부팅을 자동 해결책으로 쓰지 않는다. |
| AP-09<br>모드 적용 결과 | 요청된 모드·승인된 모드·실제 계산에 적용한 모드와 revision을 구분한다. | requested/committed/applied mode, mode_revision, apply result | 전환 중 지연된 AUTO 명령이 새 MANUAL 정책에 적용되지 않는다. 요청 ACK와 실제 적용 완료를 구분한다. |
| AP-10<br>명령 생성·발행 | 명령을 만든 계산·입력·모드·권한 문맥과 publish 시도/결과를 연결한다. | command_id/seq, input refs, mode revision, authority epoch, tx result | publish 성공을 ECU 적용으로 기록하지 않는다. 계산 완료와 발행 실패가 각각 보인다. |
| AP-11<br>예비 실행 | STANDBY도 입력 처리·동기화·준비 계산을 계속 수행하되 실제 차량 출력은 허가받지 않는다. | role_observed, shadow progress, output_inhibited, ready_by_mode | paused VM을 예비로 인정하지 않는다. 예비의 shadow 결과가 Gateway에서 실제 출력으로 통과하지 않는다. |
| AP-12<br>동기화 대상 정의 | 인계에 필요한 논리 상태와 복제하지 않을 실행별 상태를 명시한다. | state schema/version, control state, route/progress, selected inputs, excluded fields | 전체 RAM 복제를 요구하지 않는다. socket·lock·boot-local timestamp·옛 출력 권한은 복제하지 않는다. 목록은 D-02에서 확정한다. |
| AP-13<br>일관된 상태 복제 | 일관된 checkpoint/delta와 source 실행·상태 세대·버전을 연결한다. | replication stream/epoch, base/version, digest 또는 검증 근거, source execution | 서로 다른 계산 시점의 상태를 섞지 않는다. delta gap·순서 역전·버전 불일치는 전체 재동기화 또는 미준비로 처리한다. |
| AP-14<br>동기화 반영 증거 | 예비는 수신·검증·적용 완료를 따로 보고하고 마지막 일관된 버전과 지연을 제공한다. | received/applied/acked version, lag, pending count, sync validity | 상태 메시지 수신·PUBACK만으로 동기화 완료를 선언하지 않는다. 적용 ACK는 명시된 검증 완료 뒤에만 낸다. |
| AP-15<br>인계 적격성 | 모드별 동기화 허용 범위·현재 입력·앱 진행·호환 버전으로 승격 가능 근거를 제공한다. | takeover_ready_by_mode, evidence versions, age, reason, validity window | 정상 종료 전 마지막 ACK가 없어도 이미 확보한 일관 상태로 평가한다. 허용 손실 범위를 넘거나 근거가 불명확하면 승격 불가. |
| AP-16<br>인계 순간 재검증 | 승격 요청 시 현재 state/input/mode 문맥을 확인하고 예상 출력 불연속을 제한한다. | expected state/mode revision, current inputs, handoff result | 옛 위치·속도·제어기 누적값을 무조건 재사용하지 않는다. 인계 간 상태 변화가 크면 재계산 또는 안전 정지한다. |
| AP-17<br>고장 후 재합류 | 복구 앱은 새 세션·출력 비활성 상태로 시작해 현재 활성 실행에서 상태를 다시 받는다. | joining/syncing/standby_ready, baseline source, new app session | S6에서 복구 VM이 제어권을 자동 탈취하지 않는다. stale 권한·cache를 이어받지 않는다. |
| AP-18<br>인지 회복과 AUTO 복귀 | 신뢰할 수 있는 연속 인지·계산 결과와 복귀 정책을 확인해 AUTO 복귀를 요청한다. | stable interval, fresh frame progress, auto-ready reason, resume request | S5에서 owner/execution을 유지하고 VM 재시작을 요구하지 않는다. 인지 플래핑 시 반복 전환을 억제한다. |
| AP-19<br>quiesce | 새 일반 작업·출력을 억제하고 필요한 최종 상태를 처리한 뒤 기한 내 결과를 반환한다. | quiesce stage, final state version/ACK, output inhibited, timeout/reject | 최종 전달 전에 통신을 끊지 않는다. 실패 후 임의로 출력/업무를 재무장하지 않는다. crash는 hook을 실행하지 못할 수 있다. |
| AP-20<br>앱 복구 capability | 앱 단독 재시작 가능 여부와 영향 범위를 선언한다. | restart_scope, shared runtime impact, app/session reset | 단일 펌웨어에서 앱만 안전하게 재시작할 수 없으면 VM 복구를 사용한다. 임의 스레드 재시작을 정상 앱 복구로 위장하지 않는다. |
| AP-21<br>입력·상태 오류 진단 | 무효 입력·설정·자원 고갈·동기화 실패의 근거를 제한된 비용으로 남긴다. | reason code, offending source/session, counters, last valid refs | 실패 시 명령 생성 중단/안전 대체 결과와 원인을 분리한다. 잘못된 입력을 조용히 정상 기본값으로 치환하지 않는다. |
| AP-22<br>양방향 역할 시험 | A/B 역할을 바꾸어 같은 모드·계산·미러링·재합류 절차가 성립하도록 한다. | S1–S7의 A-active/B-active 변형, both-mode cases | A→B만 성공하고 B→A는 불가한 구성을 대칭 미러링으로 수락하지 않는다. |

## 08. MQ — MQTT·수집·API/화면 투영

MQTT는 데이터의 의미나 실제 적용을 만들어 주지 않는다. 기존 전송 규약은 유지 가능한 범위에서 재사용하되, 동일 앱 A/B의 동시 발행과 새 관측값을 구분할 수 있는 계약을 명시한다. [S1][S2][T4]

### 요구사항

| ID / 항목 | 필수 동작·책임 | 제공 정보·입력·소비자 | 향후 수락 기준·실패 처리 |
| --- | --- | --- | --- |
| MQ-01<br>메시지 종류와 버전 | 기존 Agent 봉투·inventory·metrics·heartbeat를 재사용하고 새 의미는 명시적 확장으로 추가한다. | schema_version, message type, topic/field mapping, compatibility | 기존 heartbeat {}에 몰래 앱 상태를 삽입하지 않는다. 새 enum/단위/타입은 생산자·소비자가 함께 승인한다. |
| MQ-02<br>A/B 스트림 독립 | 동일 app_id를 가진 A/B도 각 VM·실행·세션의 최신값과 seq를 독립적으로 보관한다. | key=(publisher VM/execution/session, stream), app_id=기능 식별 | 한 토픽/앱 이름의 latest-only 값으로 상대 VM 보고를 덮지 않는다. 전송 topic 또는 수신 분리 규칙을 D-03으로 확정한다. |
| MQ-03<br>인증·권한 | 인증된 주체와 허용 VM/app/topic을 대응시키고 자기 데이터만 발행하도록 한다. | credential principal, ACL mapping, provisioning/revoke status | payload의 vm_id만 믿지 않는다. Zephyr Agent topic 추가를 위해 모든 VM 쓰기 권한을 열지 않는다. |
| MQ-04<br>연결과 구독 | 소켓·인증·CONNACK·필터별 SUBACK·실제 수신 상태를 구분한다. | connected, subscription granted/denied, last message, auth reason | connected=true만으로 전체 수신 경로 정상 판정 금지. 거절된 필터에서 무수신을 VM 장애로 해석하지 않는다. |
| MQ-05<br>전달 단계 | 로컬 enqueue/publish 반환, broker ACK, 소비자 수신·처리 ACK, ECU 적용을 구별한다. | stage, message/correlation id, ack type, error/timeout | PUBACK 또는 QoS 2 완료를 업무 처리/차량 적용 보증으로 사용하지 않는다. [T4] |
| MQ-06<br>신선도 판정 | 수신 시각뿐 아니라 원 생산/처리 진행·세션·재전달 여부를 검증한다. | source frame/seq, producer sample age, receive age, transport age bound | 오래 대기한 메시지에 새 수신 시각만 붙여 신선하다고 하지 않는다. 나이를 보장할 근거가 없으면 안전 판단에 유효 처리하지 않는다. |
| MQ-07<br>중복·역전·세션 역행 | 재전달은 멱등 처리하고 퇴역한 실행/세션의 재등장을 차단한다. | session binding, seq high-watermark, request/message id, reset markers | 수신기 재시작·브로커 재연결·메시지 순서 교란을 시험한다. 새 ID로 위장한 옛 정보의 무조건 채택 금지. |
| MQ-08<br>QoS·retain 정책 | 종류별 신뢰도·지연·retention 정책을 정의하고 시간 민감 명령은 오래된 큐/retain에서 실행하지 않는다. | QoS, retain, bounded queue, expiry/age decision, replay policy | inventory의 retained는 마지막 보고일 뿐 생존 증거가 아니다. 옛 control snapshot으로 운행을 재허가하지 않는다. |
| MQ-09<br>재연결 | 재연결마다 필요한 구독·현재 세션·현재 상태를 다시 확보한다. | retry/backoff, subscription state, resync progress, ready gates | 연결 복구 직후 저장된 주행 명령을 몰아 보내지 않는다. 현재 명령/권한을 다시 확인하기 전 출력 불가. |
| MQ-10<br>관측 경로 장애 | 브로커·클라이언트·수집기·API·화면 연결의 건강을 각각 표시한다. | observer session, broker connection, query health, last successful sample | 공통 경로 장애를 A/B·Linux의 동시 crash로 단정하지 않는다. 운행 보호는 GW/ECU의 독립 경로로 계속한다. |
| MQ-11<br>LWT 경계 | LWT를 채택하면 세션에 결합한 연결 손실 보조 신호로만 사용한다. | will session/owner, reason, observed time | 옛 연결의 늦은 LWT가 새 연결을 OFFLINE으로 덮지 않는다. LWT만으로 VM 재시작이나 제어권 이전을 승인하지 않는다. |
| MQ-12<br>포맷·크기·단위 | payload 크기·필수 필드·수치 범위·단위·타입을 검증하고 거절 사유를 노출한다. | schema errors, packet/buffer limits, metrics units, unavailable reason | percent/bytes 밖 카운터·시간 단위는 별도 확장 계약으로 처리한다. 큰 inventory로 주행 메시지 경로가 막히지 않는다. |
| MQ-13<br>클라이언트 동시성 | MQTT 객체·JSON parser·수신 버퍼의 소유권과 수명 규칙을 지킨다. | owner thread, snapshot copy, bounded request queue, max wait | 다른 스레드의 publish/parse로 경쟁·덮어쓰기를 만들지 않는다. 실제 루프 정체를 관측하려면 감독·전송 경로의 독립성도 검증한다. |
| MQ-14<br>관측 API 투영 | 기존 Manager·Agent 관측값을 출처·실행·유효성·오류를 유지해 조회에 연결한다. | detailed observation, operation/recovery refs, OS metrics, unsupported fields | FastAPI 성공/HTTP 200을 앱 건강으로 치환하지 않는다. 미연동 필드는 false/0 대신 미관측 상태로 표시한다. |
| MQ-15<br>보관·자격증명 보호 | 시계 품질·retention·용량·개인정보·비밀 제거 정책을 적용한다. | received/observed time, retention, redaction, loss count | 토큰·비밀번호·전체 환경변수·민감 raw 입력을 로그에 남기지 않는다. 로그 장애가 안전 제어를 막지 않는다. |
| MQ-16<br>타임라인·지연 | 관측·모드·권한·복구 사건을 연관시키고 측정 구간과 시계 오차를 표시한다. | incident/transition/operation ids, send/receive/apply spans, clock uncertainty | 로컬 동일 시계 측정과 보드 간 추정 지연을 구별한다. 데이터가 빠진 구간을 0 ms로 만들지 않는다. |
| MQ-17<br>화면 최신성 | 화면/모니터의 할당 대상·데이터 최신성·렌더 확인과 연결 상태를 제공한다. | display target VM/app/session, rendered revision, heartbeat, disconnected | 웹소켓 연결을 실제 물리 화면 표시로 단정하지 않는다. stale 시각/배지를 유지하고 대상 전환 결과를 확인한다. |
| MQ-18<br>명령형 메시지 | Agent 시험 명령·재개·모드 요청에는 대상 실행·권한·만료·중복 방지 규칙을 둔다. | command_id, expected execution/boot, expires/validity, operation ref | 만료·재전달 명령으로 부하/종료/재개를 반복 실행하지 않는다. 생존·측정 채널과 파괴적 명령을 분리한다. |
| MQ-19<br>능동 점검·경로 검증 | ICMP/TCP/MQTT/앱 health 또는 요청-응답 probe의 검사 범위를 구분하고 통신 장애를 재현한다. | probe id, target execution, request/response stage, RTT, ACL·구독·재시작·큐 장애 | ping을 앱 준비로 바꾸지 않는다. 실제 앱 응답과 브로커 echo를 구별한다. Mock 연결로 실제 ACL 수락을 대체하지 않는다. |
| MQ-20<br>기존 기능과 미연동 분리 | 사용 가능한 계약은 연결하고 없는 계약은 인터페이스 요구로 남긴다. | contract version, exact call/topic/payload, requested extension, acceptance vector | 미구현 API를 호출했다고 가정하거나 임의의 성공 응답을 넣지 않는다. 기능 명세와 연동 진척 문서는 분리한다. |
| MQ-21<br>배포 단위 호환 | 발행자·브로커 ACL·공용 라이브러리·수신기·조회 모델의 호환 버전을 함께 기록한다. | compatibility manifest, schema tests, supported/unsupported versions | 한 브랜치의 helper만 교체하여 계약이 맞는다고 보지 않는다. 실제 수신·저장·조회 경로의 샘플을 시험한다. |

## 09. GW — 제어권 중재·ESP32 출력·안전

적용: 실제 Gate­way와 섀시 ECU. 사용자 사건 시나리오의 모드 유지·owner 이전·안전 정지 여부를 최종 실행으로 연결한다. 정책 중재와 물리 출력 수락의 책임을 분리하고 증거로 묶는다. 단일 RPi4의 VM 이중화가 호스트 고장 내성을 뜻하지는 않는다.

### 요구사항

| ID / 항목 | 필수 동작·책임 | 제공 정보·입력·소비자 | 향후 수락 기준·실패 처리 |
| --- | --- | --- | --- |
| GW-01<br>단일 중재 | 승격·회수·mode commit은 하나의 유효 중재 세션이 담당한다. | arbiter session, policy revision, authority state | 앱·백엔드·복구 Worker가 별도 최종 owner를 정하지 않는다. 중재 충돌 시 안전 정지한다. |
| GW-02<br>권한 대상과 세대 | 권한은 VM UUID뿐 아니라 현재 실행·앱 세션·중재 세션과 세대에 결합한다. | owner tuple, authority epoch, role, validity/lease | 새 VM 실행은 옛 권한을 상속하지 않는다. seq 숫자만 크다는 이유로 다른 owner 명령을 수락하지 않는다. |
| GW-03<br>모드와 owner 독립 | 모드 revision과 owner/authority epoch의 의미를 구분하고 효과를 별도로 기록한다. | requested/committed/applied mode, mode revision, active VM/execution | S1/S5에서 owner를 유지하고, S2에서 조건이 맞으면 mode를 유지한다. 늦은 이전 모드 명령은 배제한다. |
| GW-04<br>승격 조건 | 후보의 현재 실행·계산 진행·모드별 입력·동기화·정지 의도·관측 유효성을 검증한다. | candidate evidence refs, ready reasons, current intent, policy | 신선한 heartbeat 또는 단순 running만으로 승격하지 않는다. 준비 미완료·UNKNOWN은 운행 가능으로 다루지 않는다. |
| GW-05<br>옛 출력 차단 | 새 owner 명령 수락 전 옛 권한/지연 명령을 무효화한다. | fencing result, authority transition, old/new owner, cutover marker | 고장 VM의 협조 ACK를 기다려야만 차단 가능한 구조로 만들지 않는다. 전환 중 중복 유효 출력 금지. |
| GW-06<br>인계 상태기계 | 감지·보호·후보 확인·권한 commit·새 명령 수락·적용 확인을 단계로 구분한다. | transition phase, deadline, failure, first new accepted/applied command | 중간 실패·앱 회복·의도 변경 시 재검증하거나 정지한다. 인계 timeout을 정상 전환으로 기록하지 않는다. |
| GW-07<br>인지 장애 처리 | 인지 무효를 VM crash와 구분해 같은 활성 VM의 MANUAL 준비/적용을 중재한다. | perception validity, manual readiness, requested mode, safety output | S1에 불필요한 VM 재시작/owner 이동이 없어야 한다. 준비되지 않은 MANUAL로 운행을 강행하지 않는다. |
| GW-08<br>복합 장애 | 인지와 활성 VM 장애를 하나의 현재 조건 집합으로 평가한다. | incident set, AUTO invalid reason, candidate MANUAL readiness | 사건 도착 순서가 바뀌어도 S4에서 AUTO가 재허가되지 않는다. 예비 MANUAL 불가 시 안전 정지. |
| GW-09<br>AUTO 복귀 | 인지 회복·안정 구간·현재 앱 준비·운영자/자동 정책 조건으로 복귀를 승인한다. | resume policy/version, readiness evidence, mode apply result | S5에서 현재 owner를 유지한다. 단일 회복 메시지·stale retained·플래핑으로 자동 출발/반복 전환 금지. |
| GW-10<br>복구 VM 비활성 합류 | 새 실행을 예비로 등록하고 current owner를 유지한다. | rejoined execution, sync state, standby eligibility | S6에서 복구 완료 이벤트만으로 원 owner에게 되돌아가지 않는다. 자동 failback은 별도 승인 정책 없이는 하지 않는다. |
| GW-11<br>명령 수락 검증 | 신원·현재 모드/권한·순서·기한·범위·무결성을 검증한 명령만 전달한다. | accepted/rejected command refs, reason, generation, mode revision | 지연·중복·옛 세션·비정상 수치 명령은 출력에 반영하지 않는다. 게이트웨이 수락과 ECU 수락은 별도 기록한다. |
| GW-12<br>ECU 적용 확인 | ECU가 수신·수락·실제로 적용한 명령을 세대·명령 식별자로 보고한다. | ECU session, accepted/applied counter, actuator output, result/reason | 시리얼 write 완료를 ECU 적용으로 인정하지 않는다. MQTT ID와 프레임 counter의 대응·wrap 규칙을 보존한다. |
| GW-13<br>독립 watchdog | ESP32는 유효 명령 부재·상위 장치 정지 시 자체 시간 기준으로 안전 출력을 실행한다. | watchdog armed/expired, last valid command, stop cause/time | S7뿐 아니라 dom0·Gateway·Broker 전체 중단에도 작동해야 한다. 무효/옛 명령이 watchdog을 갱신하지 않는다. |
| GW-14<br>E-stop 우선 | 물리 비상정지와 안전 차단은 일반 주행 명령보다 우선하며 명시적 해제 절차를 둔다. | estop input/latch, stop reason, reset authorization | VM 재부팅·MQTT 재접속·새 owner 승격이 E-stop을 해제하지 않는다. |
| GW-15<br>재시작 세션 재수립 | Gateway/Broker/ECU 재시작 후 옛 권한·세대·잔류 프레임을 재사용하지 않는다. | new arbiter/ECU session, handshake, authority reconciliation | 세대 카운터 소실이나 wrap으로 과거 명령을 새 명령으로 인정하지 않는다. 재수립 전 일반 출력은 차단한다. |
| GW-16<br>안전 정지와 실제 정지 | 정지 요청·프레임 전송·ECU 적용·물리 정지 확인을 다른 단계로 보고한다. | stop_request/accepted/applied, speed/encoder, sensor health, stop_confirmed | 센서 부재/무효이면 물리 정지는 UNKNOWN이다. 명령 0·PWM 0만으로 차체 정지를 주장하지 않는다. |
| GW-17<br>전원·물리 실패 | ECU 전원·모터 구동·조향·근접 보호 등 실제 안전 출력 조건을 정의한다. | power/sensor availability, safe actuator state, fault cause | 상위 전원 차단과 ECU 전원 차단을 구분한다. MCU가 꺼져도 출력이 안전 상태가 되는 하드웨어 조건을 확인한다. |
| GW-18<br>지연·정체 계측 | 명령 생성/수신/수락/적용·인계·정지의 시점을 식별해 측정한다. | local spans, incident/command refs, queue latency, lost sample, clock quality | 동일 시계 측정과 cross-device 추정을 구분한다. 중간 증거가 없으면 종단 지연을 완전 측정으로 표시하지 않는다. |
| GW-19<br>보호와 복구 분리 | 즉시 출력 보호는 느린 VM 관리 호출·HTTP·로그·복구 완료에 의존하지 않는다. | safety decision loop, bounded waits, recovery request/result | Manager 잠금·브로커 지연·재부팅 시간에도 ECU 보호가 유지되어야 한다. |
| GW-20<br>권한/전환 기록 | 정책과 근거, 전환 전후 mode/owner/execution, 예외·거절·정지 결과를 남긴다. | transition id, before/after, evidence refs, policy revision, operator request | 어느 근거로 운행을 허용/거부했는지 재구성 가능해야 한다. 로그 실패는 별도 진단하되 보호 동작은 계속한다. |

## 10. 데이터 계약 — 기존 형식 재사용과 필요한 확장

이 절은 최종 wire schema를 강제로 바꾸는 선언이 아니다. 기존 형식의 재사용 범위와 새 필드의 의미를 고정하고, 호환되지 않는 항목은 D-03~D-05의 명시적 계약 변경으로 처리한다. [S1][S2]

### 메시지/조회 묶음

| 묶음 | 생산자 → 소비자 | 필수 의미 | 형식·전송 원칙 |
| --- | --- | --- | --- |
| 플랫폼 실행·자원 | PL → Worker/조회 API/GW | 영구·실행 신원, 의도, Xen 상태, 자원, 관측 age/error | 기존 Manager envelope와 probe/snapshot/작업 조회 계약을 재사용. MQTT로 중복 원본을 만들지 않음. |
| agent.heartbeat | AG → 수집기/복구 판단 | 해당 Agent 실행의 생존·순서 | 기존 빈 payload와 봉투 의미를 보존. 앱 진행을 넣을 경우 다른 명시적 타입. |
| agent.inventory | AG → 수집/저장/조회 | 실제 OS·빌드·인터페이스·Agent/capability | 기존 os/kernel_release/hostname/interfaces/agent_version 재사용. retained는 과거 inventory. |
| agent.metrics | AG → 수집/저장/조회 | OS 자원 표본·window·단위·비가용 사유 | 기존 percent/bytes 범위 우선. thread/시간/count 확장은 수신기와 승인. processes와 threads를 혼동하지 않음. |
| 앱 진행/준비 | AP → AG/MQ/GW | loop·입력 처리·모드별 ready·명령 생성 | 새 논리 보고 묶음. 기존 app.heartbeat 의미 유지, 세션·원 측정 시각 보존. |
| 미러링 상태/ACK | AP ↔ AP, 근거 → GW | state epoch/version, consistent checkpoint, applied ACK, mode별 인계 근거 | 주행 명령과 별개. QoS나 retain만으로 일관성 보장 주장 금지. |
| 모드/제어권 | GW → AP/ECU/관제 | mode revision과 owner/execution/authority epoch | 권한 정보의 retained는 참고 snapshot. 현재 세션·유효성을 재확인한 뒤 사용. |
| ECU 적용/안전 | ECU → GW → 관제 | 수락·적용 명령, watchdog/E-stop, 실측 속도·안전 출력 | 실제 ECU가 생성한 값과 Gateway 추정을 구별. 직렬 프레임·MQTT 간 대응 보존. |
| 관측 경로/화면 | MQ/표시 서비스 → 관제 | 연결·구독·수집·렌더 최신성 및 실패 | 서비스별 자기 세션·마지막 성공을 갖고, 상세 source를 보존. |

### 공통 의미 사전

| 정보 | 범위/원본 | 갱신·reset 규칙 | 검증·비가용 의미 |
| --- | --- | --- | --- |
| vm_id | PL 영구 VM UUID | VM 재부팅으로 바꾸지 않음 | 권한 자체가 아님. app_id·domid와 구별. |
| host_boot_id / execution_id | PL host와 VM 실행 | host 또는 VM 새 실행 때 각 범위대로 변경 | guest가 임의 주장한 값은 신뢰된 binding 없이는 확정하지 않음. |
| boot_id / app·agent session | AG/AP의 guest·프로그램 실행 | 재부팅과 프로그램 재시작을 구분 | 프로세스·펌웨어별 의미를 선언. clone 동일값 금지. |
| mode_revision / authority epoch | GW 모드 적용과 제어권 | 모드 변경과 owner/session 변경을 구별 | 세대 wrap·재시작·되돌림 처리 필요. 같은 값이어도 세션이 다르면 다른 권한. |
| seq / observation_revision | 생산자/수집 스트림 | 원 스트림의 진행 또는 값 변경 | 서로 다른 의미의 카운터를 동일하게 해석하지 않음. 부동소수로 큰 정수를 잃지 않음. |
| state epoch/version | AP 미러링 계보 | 새 기준 snapshot과 delta 반영 | 서로 다른 source execution의 version 숫자를 단순 비교하지 않음. |
| observed/received time | 생산 측 측정, 수신 측 수신 | 재전달은 observed time 갱신 안 함 | 같은 clock domain만 직접 차분. UTC 품질 별도. |
| validity / reason | 각 생산·집계 계층 | age·오류·지원 범위 변화 | UNKNOWN/UNSUPPORTED/STALE/NOT_APPLICABLE은 서로 다름. |
| last received / processed / applied | 각 단계의 실제 주체 | 그 단계가 끝난 때만 갱신 | MQTT ACK와 AP/ECU 적용 ACK를 분리. |

## 11. OS별 적용 범위와 누락 방지 표

아래는 요구 적용 범위다. 구현 현황 표가 아니다. Debian·Ubuntu에 주행 앱이 없으면 주행 요구는 비적용이며, 같은 역할의 앱을 배포하면 AP 요구가 동일하게 적용된다.

### 플랫폼·Guest 공통 적용

| 요구 묶음 | Zephyr A/B | Debian·Ubuntu | 직접 생산 책임 |
| --- | --- | --- | --- |
| Xen 신원·상태·의도·작업 | 필수 | 필수 | PL |
| vCPU·할당 RAM·실제 affinity | 필수, 별도 CPU 계약 참조 | 필수, 별도 CPU 계약 참조 | PL/Xen driver |
| 실제 OS·빌드·MAC/IP | 필수: Zephyr+앱/펌웨어 | 필수: 배포판+커널+Agent | AG, PL과 비교 |
| Agent 생존/수집 건강 | 필수: 경량 Telemetry의 실제 실행 | 필수: Agent 프로세스 | AG/MQ |
| CPU·메모리 내부 사용 | RTOS가 계측한 영역·분모 | Linux 시스템·프로세스 의미 | AG |
| 프로세스/스레드 | threads, stack, runtime | processes; 필요 시 threads | AG |
| VIF·링크·DHCP·I/O | VIF·netfront·net_if 계층 | VIF·Linux NIC·블록 계층 | PL+AG |
| 앱 준비·실제 loop·입력 | 주행 앱에 필수 | 앱 역할에 맞는 progress 필요 | AP |
| AUTO/MANUAL·미러링 | 양쪽에 필수 | 주행 역할 배포 시 적용 | AP/GW |
| 협조 종료·재합류 | 앱 hook+PL 제거+예비 재합류 | OS/앱 종료+PL 제거; 주행 시 재합류 | AP/AG/PL |
| 디스크·파일시스템 | 무디스크면 비적용; 가짜 0 금지 | 디스크 사용·오류 수집 조건부 필수 | AG+PL |
| 센서·물리 안전 | 앱 입력으로 참조, 원본은 외부 | 역할에 따라 참조 | Jetson/GW/ESP32 |
| 콘솔·로그·화면 | PV 출력/실행 대응, 실제 표시 별도 | 콘솔/서비스/앱 출력 | PL+MQ/표시 서비스 |

### 앞선 표에서 반드시 명시해야 할 추가 정보

| 추가 관측 | 이유 | 해당 요구 |
| --- | --- | --- |
| 부팅 단계·첫 보고 미도착 | 네트워크/Agent 준비 이전과 고장을 구별 | PL-04, AG-04, AP-02, MQ-10 |
| 실제 버전과 배포 버전 차이 | 잘못된 펌웨어/Agent로 시험하지 않기 위해 | PL-05, AG-01, MQ-21 |
| 수집기 자체 오류·계측 손실 | heartbeat만 살아 있는 수집 실패 구별 | AG-10, CO-12, MQ-10 |
| 시계 품질·세션 결합·옛 세션 배제 | 재부팅·retained·재연결 오판 방지 | CO-02~07, MQ-06~09 |
| 부팅/관측/API/화면 연결의 다른 건강 | 중앙 관측 장애를 VM crash로 오인 방지 | MQ-04, MQ-10, MQ-14, MQ-17 |
| 주행 실제 loop와 source frame 진행 | 같은 데이터 재발행·계산 정체 검출 | AP-03~07, MQ-06 |
| 동기화 적용 ACK·허용 손실 | 미러링이 실제 인계 가능한지 판단 | AP-12~17 |
| 정지 요청/적용/물리 정지의 단계 | 명령과 차량 운동을 구별 | GW-12~17 |
| host 공통 원인·watchdog 독립성 | A/B가 함께 사라지는 상황에 대비 | PL-14, GW-13, GW-17, GW-19 |

## 12. 시나리오 재현·수락 요구

다음은 향후 시험 명세다. 각 시험은 정상 전제와 실패 대안을 모두 수행한다. 실차 시험은 저속·안전 구역·물리 E-stop·독립 관측을 확보하고, 고장 주입의 대상과 복원 절차를 사전에 승인한다.

### 일곱 사건 시험

| 시험 ID | 주입·변형 | 기대 동작 | 필수 증거·수락 조건 |
| --- | --- | --- | --- |
| SC-01 / S1 | Jetson 프로세스 중단, 센서 frame 정체, Jetson→VM 경로 단절을 각각 주입. A/B 활성 및 manual-ready/not-ready 변형. | 같은 활성 execution 유지. MANUAL 준비/적용 또는 안전 정지. VM 자동 재시작 없음. | 원천/수신/처리 진행, mode before/after, owner 불변, output applied/stop. 운행 재개는 유효 수동 입력 뒤. |
| SC-02 / S2 | 활성 도메인 crash/제거/행. AUTO와 MANUAL 각각, A→B/B→A. 예비 최신/지연/미준비 변형. | 적격 예비에 owner 이전. 같은 모드 적격이면 mode 유지. 불가하면 정책상 수동 전환 또는 정지. | old execution 차단, candidate sync evidence, new epoch, 새 명령 ECU 적용. 중복 소유 구간 없음. |
| SC-03 / S3 | 실제 계산 경로만 정지; 가능한 경우 Agent/MQTT는 계속 실행. 별도로 앱 전체/통신 경로 중단. | Xen running·Agent 생존·계산 정체를 구별. 예비 검토와 지원 범위의 앱/VM 복구. | loop_seq 정체, heartbeat 및 collector 상태, 판단 이유, recovery scope, 새 세션·재합류. |
| SC-04 / S4 | 인지와 활성 VM 장애의 동시·순차·순서 역전·지연 보고 조합. | AUTO 허가 없음. 예비 MANUAL 적격이면 준비 후 인계, 아니면 정지. | 하나의 incident 또는 연결된 incident들, combined conditions, no AUTO output, ECU 안전 상태. |
| SC-05 / S5 | 인지 복구 후 정상 연속 frame, 한 개 frame만 회복, 반복 플래핑, 오래된 retained/큐 replay. | 정해진 안정 구간·복귀 정책 후 같은 owner의 AUTO 복귀. 나머지는 MANUAL/정지 유지. | source progression, ready interval, authorization, mode apply ACK, owner/execution 불변. |
| SC-06 / S6 | 고장 VM 복구, 앱 단독 재시작, 동기화 지연/실패·버전 불일치. | 새 execution/session으로 출력 비활성 합류. 현재 owner 유지. 일관 상태 반영 후 STANDBY. | old/new identity, sync base/version/ACK, ready_by_mode, no authority theft, false-ready 없음. |
| SC-07 / S7 | 양쪽 VM 장애. 추가로 Broker/Gateway/dom0 중단과 ECU 전원 상태를 분리 시험. | 유효 owner 없음. ECU watchdog/E-stop 안전 출력. 복구는 허용 범위에서만, 자동 출발 없음. | ECU 자체 타이머·출력 적용·가능한 실제 속도. 상위 보고 부재 시 독립 장치/영상/센서로 확인. |

### 횡단 고장 시험

| 시험 ID | 시험 | 수락 기준 |
| --- | --- | --- |
| XT-01 | 옛 세션·중복·순서 역전·domid 재사용 | 새 실행의 진행·권한·메트릭으로 수용되지 않음. |
| XT-02 | 실제 UART/네트워크 연결은 유지되나 MQTT ACL/SUBACK 실패 | OBSERVATION_UNAVAILABLE과 VM fault를 구별, 안전 입력 없으면 정지. |
| XT-03 | 입력 생성 정지 후 같은 payload를 새 MQTT seq로 발행 | source progression 정체를 인지, fake freshness 차단. |
| XT-04 | 브로커·Gateway·ECU 재시작 및 세대 소실 | 재핸드셰이크 전 출력 차단, 옛 명령·LWT·retained의 권한 회복 금지. |
| XT-05 | 모드 변경/권한 이전/stop/복구/host 종료 경합 | 현재 의도·실행·owner를 재검증, 중복 복구·중복 출력 없음. |
| XT-06 | 계측 과부하·로그 디스크 가득 참·큐 포화 | 손실을 드러내고 비용 상한 유지. 주행/안전/정상 종료 무한 대기 없음. |
| XT-07 | 처음부터 Agent/APP_READY가 한 번도 오지 않음 | 단순 UNKNOWN 영구 방치가 아니라 정해진 시작 기한·사유로 판정. |
| XT-08 | 동기화 delta 유실·적용 실패·버전 불일치 | standby_ready 철회, 재동기화 전 승격 거절. |
| XT-09 | Debian/Ubuntu 부하 중 A/B 주행·미러링·재부팅 | 실제 loop deadline·통신·예비 준비 영향 측정. 배치 격리와 시간 성능을 분리 보고. |
| XT-10 | 정지 명령 전송 성공, ECU ACK 또는 센서 부재 | 전송/적용/물리 정지의 완료 표시를 구분. 미관측을 성공으로 채우지 않음. |
| XT-11 | 벽시계 불일치·시계 보정·uptime reset | 잘못된 음수 age·가짜 최신 표본·인계 승인 없음. |
| XT-12 | 관리 UI/백엔드·표시 화면 연결 중단 | 차량 안전은 UI에 의존하지 않음. 조작 입력 경로가 끊겼다면 manual input 무효 처리. |
| XT-13 | Python/C producer와 실제 수신기 계약 벡터 | inventory/metrics/units/time/session을 같은 의미로 파싱·저장·조회. |

### 전환 허용 조건의 최소 논리

| 판정 | 필수 조건 | 조건이 불명확하거나 실패할 때 |
| --- | --- | --- |
| 모드별 후보 적격 E(vm, mode) | 현재 실행/앱 세션 결합 ∧ running 의도/허용 정책 ∧ 실제 계산 진행 ∧ 해당 모드 입력 유효 ∧ 앱 준비 ∧ 필요한 동기화 적용/허용 지연 | UNKNOWN을 true로 간주하지 않는다. 필수 신호가 미지원이면 후보 적격으로 승인하지 않는다. |
| 동일 모드 인계 | E(예비, 현재 모드) ∧ 옛 출력 무효화 가능 ∧ GW/ECU 새 권한 수락 가능 | 자동으로 AUTO를 유지하지 않는다. 승인된 MANUAL 대안의 적격성을 따로 평가하거나 정지한다. |
| 같은 VM의 MANUAL 전환 | 현재 실행 유효 ∧ 수동 경로/조작 권한/입력 유효 ∧ 앱의 MANUAL 준비 ∧ 모드 적용 확인 | owner를 무조건 바꾸지 않는다. MANUAL 준비 전과 불가 시 안전 정지한다. |
| AUTO 복귀 | 현재 owner 유효 ∧ E(owner, AUTO) ∧ 인지 안정 구간 ∧ 승인된 복귀 정책 ∧ 모드 적용 확인 | 한 번의 인지 회복/heartbeat만으로 복귀하지 않는다. 현재 owner 유지 또는 안전 정지. |
| 복구 예비 합류 | 새 실행/앱 세션 ∧ 출력 권한 없음 ∧ 현재 기준 상태 재동기화 ∧ 모드별 준비 확인 | 복구 완료와 standby_ready를 분리한다. 정상화된 VM의 자동 권한 재탈취 금지. |

## 13. 시간·정책·미확정 상세 결정

아래 값은 이 문서에서 임의의 숫자로 확정하지 않는다. 기존 계약의 Agent heartbeat 주기와 주행 안전 기한은 다른 계층의 값이다. 측정 환경·허용 위험·요구 지연을 근거로 값을 정하고 동일 정책 revision으로 시험한다.

### 시간 프로필

| 파라미터 묶음 | 정의 | 소유/관측 | 결정 조건 |
| --- | --- | --- | --- |
| P_control / D_loop | 계산 주기와 각 계산의 마감 | AP | 주행 제어 요구와 실측 jitter, CPU/네트워크 부하로 동결. |
| T_input_auto / T_input_manual | 인지/조작 입력의 유효기간 | AP/GW | 원 생성 진행·전송 지연 근거 포함. 수신 시각만으로 원 데이터 age를 보장하지 않음. |
| T_command_valid / T_output_guard | 명령 수락 유효기간과 상위 보호 기한 | GW | 전환 감지·판단·전달/ECU 적용의 누적 예산을 고려. |
| T_ecu_watchdog / T_safe_output | ECU 마지막 유효 명령 이후 안전 출력까지의 시간 | ECU | 호스트가 완전히 멈춘 시험에서 검증. 물리 제동 시간은 별도 측정. |
| P_heartbeat / T_stale / T_offline | Agent·앱·경로별 보고 주기와 표시/복구 판단 기한 | AG/MQ/복구 | 기존 호환값을 보존하되 앱 진행/즉시 제어와 구별. 종류별 별도 정책. |
| T_boot / T_app_ready | 실행 시작 후 첫 생존/준비 대기 | PL/AP/복구 | Linux와 Zephyr에 다른 값 가능. 첫 보고 없는 경우 포함. |
| T_sync_age / L_sync / T_rejoin | 동기화 지연·허용 상태 차이·재합류 기한 | AP/GW | 모드별 복제 상태의 안전한 허용 손실을 명시. 필수 상태에 근거 없으면 ready=false. |
| T_handover / T_auto_stable | 인계 완료 기한과 AUTO 복귀 안정 구간 | GW/AP | 준비·차단·commit·새 명령·적용을 모두 포함. 실패 시 정지. |
| T_quiesce / T_recovery / retry budget | 정상 종료, 복구 시도, 재시도 예산 | PL/AP/복구 | 즉시 차량 보호 경로보다 느려도 되지만 이를 보호 기한 대신 쓰지 않음. |
| P_metrics / B_telemetry / T_display_stale | 계측 주기·시간/메모리 예산·화면 최신성 | AG/MQ | 주행 지터와 각 수집 API 비용을 측정하여 제한. |

### 명세 승인 전에 확정할 상세 항목

| ID | 결정 대상 | 이미 고정한 요구 | 추가로 결정할 것 |
| --- | --- | --- | --- |
| D-01 | 모드 전환·AUTO 복귀 승인 | 동일 owner 유지 가능, 조건 미충족 시 정지 | 수동 준비 승인·자동/운영자 복귀·안정 구간. |
| D-02 | 미러링 상태와 허용 차이 | 동일 A/B, 적용 ACK와 모드별 인계 근거 필요 | 정확한 상태 필드·일관성 경계·checkpoint/delta·모드별 손실/지연 한도. |
| D-03 | A/B topic·식별 매핑 | 양쪽 publisher를 독립 추적, app_id와 owner 분리 | 기존 app topic 유지+복합키 또는 새 per-VM channel, schema version/전환 절차. |
| D-04 | 메트릭 확장 | 기존 inventory/metrics와 OS 의미 보존 | thread 모델, count/time 단위, payload 분할, 기존 수신기 연결 변경. |
| D-05 | 시간·세션 binding | 가짜 UTC 금지, 오래된 실행 배제 | 무벽시계 표현·수신 저장 시각, PL↔guest 읽기전용 실행 문맥 전달 방법. |
| D-06 | 제어권/ECU 세션 | 단일 유효 owner, 늦은 명령 차단 | epoch/lease·Gateway/ECU 재시작·카운터 wrap·프레임 매핑. |
| D-07 | 물리 안전 상태 | 상위 전체 장애에도 안전 출력, E-stop 우선 | 모터 제동/코스팅·조향 상태·전원 실패·실제 정지 확인 센서와 판정. |
| D-08 | 복구 범위·승인 | 의도적 stop 존중, 복구 VM은 STANDBY | 앱 단독 복구 가능 대상, force 승인·재시도 횟수·운영자 개입 기준. |
| D-09 | 시간 프로필 | 계층별 기한 분리, 시간 수락에 근거 필요 | 위 시간 값·불확실성·안전 여유·실차/부하 조건. |
| D-10 | API·표시 경계 | 하위 값 의미를 보존, 현황표 분리 | 노출 API와 capability, 역할별 조회권한·보관·화면 적용 확인. |

## 14. 이전 관측 요구와의 추적성

이 표는 요구 누락 방지용 대응표이며 구현 충족 결과가 아니다. R/M 원문은 사용자가 제공한 목록 [U2]를 따른다.

### R/M 대응

| 원 ID | 원 요구 | 연결된 본 명세 요구 |
| --- | --- | --- |
| R1 | 도메인 상태·실행 식별 | CO-02~03, PL-01~04, AG-02, MQ-07 |
| R2 | Guest/Agent 생존·앱 준비 | PL-04, AG-03~04, AG-10, AP-02~03, MQ-04/10 |
| R3 | 계산·명령 진행 | AP-03~04, AP-10, AG-11~12, GW-18 |
| R4 | 인지 최신성·유효성 | AP-05~06, AP-08/18, MQ-06, GW-07~09 |
| R5 | 수동 입력 가용성·최신성 | AP-07~09, MQ-06/18, GW-07/11 |
| R6 | 모드와 적용 결과 | CO-13, AP-01/09, GW-03/07~09 |
| R7 | 제어권과 세대 | CO-02/11/13, GW-01~06/11/15 |
| R8 | 동기화·인계 준비 | AP-11~17, AP-22, GW-04~06 |
| R9 | 명령 생성→적용 | AP-10, MQ-05/16, GW-11~12/18 |
| R10 | 안전 정지·차단 | GW-05/13~17/19 |
| R11 | 정지 의도·복구·재합류 | PL-03/09~12/16, AP-17/19~20, GW-10/19 |
| R12 | 관측 경로·유효성 | CO-01~09, AG-09~10, MQ-03~11/14~16 |
| M1 | OS·앱·네트워크 정체성 | PL-05/15, AG-01~02/08, MQ-21 |
| M2 | 자원·프로세스/스레드 | PL-06~07/14, AG-05~08/14, MQ-12 |
| M3 | 자원 설정·반영 | PL-06/16, CO-07 |
| M4 | 수명주기·콘솔 | PL-09~13, AG-16, MQ-14~15 |
| M5 | 장애·복구 타임라인 | CO-05/10/14, PL-09~12, MQ-16, GW-18/20 |
| M6 | 격리 | PL-17, AG-12, AP-04, GW-19, XT-09 |
| M7 | 메시지·화면 | MQ-04~09/16~17/19, GW-12/18, XT-12~13 |

## 15. Zephyr 참조 구현과 이후 작업의 경계

이 절은 다음 작업의 범위를 분해한 것이다. 이번 문서에서는 구현 여부를 판정하거나 코드를 변경하지 않는다.

### 참조 구현의 완료 범위

| 범위 | Zephyr가 보여줘야 할 결과 | 그것만으로 완료할 수 없는 것 |
| --- | --- | --- |
| 시스템 Telemetry | AG 요구에 맞는 실제 inventory·OS metric·수집기 상태·세션을 생산 | 다른 OS의 수집기 구현이나 FastAPI/화면 연결 완료 |
| 실제 루프 연결 | AP의 loop·입력 처리·모드 적용·생성/발행 결과를 실제 코드 지점에서 갱신 | Gateway 수락 또는 ECU 적용을 자기 보고로 대신 확인 |
| 미러링 참조 | 양쪽에서 상태 전송→검증→적용→ACK와 준비 철회를 재현 | 미러링 신호가 없는 상태에서 standby_ready를 임의 true로 설정 |
| MQTT 종단 | 기존 구현된 계약에 실제 발행·인증·수신·저장 검증, 비지원 계약은 정확한 요구로 인계 | 기존 계약에 없는 필드가 수신되었다고 가정하거나 성공 Mock으로 연결 완료 처리 |
| 복구 재합류 | 실제 새 실행·세션·네트워크 복귀·앱 준비·동기화·STANDBY를 각각 입증 | 상위 제어권 인계나 물리 안전 정지의 완료 선언 |
| 운영자 인계 | payload·단위·오류·CLI/Manager·MQTT 예제와 시험 벡터 제공 | 다른 팀 코드에 임의 정책을 덮어쓰거나 현황표를 기능 명세에 섞기 |

### 다음 구현/점검 작업의 산출물 요구

| 산출물 | 필수 내용 |
| --- | --- |
| 요구 추적표 | 이 문서의 ID별 구현 위치·시험·미연동·미결정 사항. 충족 여부는 여기서 처음 평가한다. |
| 공통 계약/시험 벡터 | 정상·없는 값·오류·stale·unsupported·재시작·A/B 동시 발행·재전달 예제. |
| Zephyr/ Linux 차이표 | 같은 의미의 수집 방식과 OS별 차이, 현재 API/빌드 옵션 지원 범위. |
| 인계 문서 | 기존 Manager/CLI와 신규 계측 호출·반환·오류·권한·timeout. 사용 가능 기능만 예제로 제시. |
| 단계별 시험 기록 | 단위→대체 환경→실보드→실차. 같은 성공 수치로 합치지 않음. |
| 미연동 요청서 | 구현되지 않은 MQTT/API마다 정확한 필요한 계약과 소비처·수락 기준·호환 영향. |

## 16. 자료 근거와 요구사항 제안의 구분

이 문서의 일곱 사건과 A/B 목표는 사용자 제공 내용 [U1]을 기준으로 한다. 기존 저장소의 서로 다른 배포/시나리오 서술을 자동으로 최종 목표로 채택하지 않았다. 아래 소스는 용어·이미 있는 인터페이스 모양·기술적 경계를 확인하는 근거이며, 요구사항 충족 판정의 근거로 사용하지 않았다.

### 근거 목록

| ID | 자료 | 참조 범위·버전 |
| --- | --- | --- |
| U1 | 사용자 제공: 본 대화의 일곱 사건 표 및 동일 AUTO/MANUAL A/B 미러링 목표 | 출처: 현재 사용자 메시지. 최상위 기능 요구. |
| U2 | 사용자 제공: R1~R12, M1~M7 관측 목록과 OS별 표 | 출처: 현재 대화에서 사용자가 다시 제시한 목록. |
| S1 | MQTT 메시지 계약 v1, §1~4 | develop / 333a4c5bd73793bf2039e90201be9ae1f6d09887. 봉투·Agent topic·단위·retain 형식 참조. 역할 고정/vchan 서술은 본 목표로 승계하지 않음. |
| S2 | AgentPayloads.java / AgentMessageHandler.java | backend / 7428797dc7573fc04acffffd8eedc2112f3915c8. inventory·metrics 수신 자료형 및 단위 검증 형식 참조. |
| S3 | Linux 자원·부팅 정책·Zephyr 네트워크/종료 계약 | platform / b6ad37379e8db02caa8024d13b69bc4aa8c82dad. 공용 Store·quiesce·종료/복구 책임 경계 참조. |
| S4 | xenova_execution.py, observe/snapshot/describe | platform / b6ad37379e8db02caa8024d13b69bc4aa8c82dad. 의도·실행 식별·관측 유효성 용어 참조. |
| S5 | Linux xenova-agent README | platform / b6ad37379e8db02caa8024d13b69bc4aa8c82dad. 기존 Agent heartbeat 책임·설치 형식 참조. Zephyr 비대상이라는 과거 설명을 새 요구의 제한으로 채택하지 않음. |
| S6 | 차량 제어 상세 명세 | vehicle / e0c18d125201372b07e18e71cb8e617fb62eb851. 기존 용어와 단일 중재·독립 안전장치 원칙 참조. 과거 OS·코어·수치 가정은 승계하지 않음. |
| T1 | Xen xl 공식 문서 | create 반환과 OS 준비 구분, blocked/paused, 관리 동작 의미. 프로젝트 고정 버전에서 실제 지원 확인 필요. |
| T2 | Zephyr Thread analyzer 공식 문서 | 스레드 runtime·stack 계측의 일반 기능 경계. 최신 문서를 고정 펌웨어의 사용 가능 API로 간주하지 않음. |
| T3 | Linux /proc 공식 문서 | Linux 시스템·프로세스 계측의 의미와 지원 범위. |
| T4 | OASIS MQTT 3.1.1, §3.1~3.3, §4.3 | retain·세션·LWT·QoS·PUBACK의 전달 의미. 차량 적용 의미는 프로젝트 계약으로 별도 정의. |

### 고정 참조 링크
- **[S1] MQTT 메시지 계약 v1, §1~4**: https://github.com/shin0-park/ssafy-autonomy-project-xenova/blob/333a4c5bd73793bf2039e90201be9ae1f6d09887/docs/contracts/mqtt-contract-v1.md
- **[S2] AgentPayloads.java / AgentMessageHandler.java**: https://github.com/shin0-park/ssafy-autonomy-project-xenova/blob/7428797dc7573fc04acffffd8eedc2112f3915c8/backend/src/main/java/com/xenova/backend/agent/application/AgentMessageHandler.java
- **[S3] Linux 자원·부팅 정책·Zephyr 네트워크/종료 계약**: https://github.com/shin0-park/ssafy-autonomy-project-xenova/blob/b6ad37379e8db02caa8024d13b69bc4aa8c82dad/platform/docs/resource-boot-zephyr.md
- **[S4] xenova_execution.py, observe/snapshot/describe**: https://github.com/shin0-park/ssafy-autonomy-project-xenova/blob/b6ad37379e8db02caa8024d13b69bc4aa8c82dad/platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_execution.py
- **[S5] Linux xenova-agent README**: https://github.com/shin0-park/ssafy-autonomy-project-xenova/blob/b6ad37379e8db02caa8024d13b69bc4aa8c82dad/platform/services/vm-agent/agent/README.md
- **[S6] 차량 제어 상세 명세**: https://github.com/shin0-park/ssafy-autonomy-project-xenova/blob/e0c18d125201372b07e18e71cb8e617fb62eb851/docs/design/vehicle-control-spec.md
- **[T1] Xen xl 공식 문서**: https://xenbits.xenproject.org/docs/4.21-testing/man/xl.1.html
- **[T2] Zephyr Thread analyzer 공식 문서**: https://docs.zephyrproject.org/latest/services/debugging/thread-analyzer.html
- **[T3] Linux /proc 공식 문서**: https://docs.kernel.org/filesystems/proc.html
- **[T4] OASIS MQTT 3.1.1, §3.1~3.3, §4.3**: https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/mqtt-v3.1.1.html

---
이 문서는 요구사항의 기준선이다. 구현 충족 여부와 담당 계층의 연동 현황은 별도 추적표에서 평가한다.
