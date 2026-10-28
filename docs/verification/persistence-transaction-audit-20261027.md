# 복구 상태 영속성·트랜잭션 경계 감사

후속 [S01·S02 파일 저장 마감](recovery-store-closure-20261027.md)은 이 보고서의 수정 전 반례를 보존하고, 별도 후보의 수정·재시작 회귀·패키징 결과를 기록한다. 아래 감사 당시 판정과 원자료를 성공으로 덮어쓰지 않는다.
receipt·Authority 파일 유지와 외부 효과의 별도 수습은 [D03 후속 결과](reconciliation-closure-20261027.md)에서 이어 확인한다. 이 후속도 SQLite 제품 도입·데이터 이관이 아니다.

## 판정

2026-10-27의 **저장 경로 감사·격리 반례·최소 SQLite 비교를 완료**했다. 제품의 SQLite 도입이나 실제 데이터 이관은 수행하지 않았다.

가장 먼저 해결할 것은 DB 전환이 아니라 **S01의 손상 격리 후 차단 상태 유실**이다. 실제 파일 저장 코드에서 재현했으며 파일 방식으로 국소 보완할 수 있다. SQLite 단순화의 우선 후보는 **Worker의 복구 상태와 기존 사건 로그**, 후순위 후보는 **Manager의 의도·원 operation 수락**이다. 작은 단일 Authority 상태, 실행별 receipt, 정적 설정과 자산은 현재 파일을 유지한다.

SQLite가 맡을 수 있는 것은 로컬 값의 공동 확정, 고정 ID 중복 방지와 일부 조회·보존이다. 장애 판단, 실행 직전 guard, Xen·Store 효과 대조, 재무장과 운영 수습은 그대로 남는다. 기존 감사의 **D01·D02·D03은 저장 엔진을 바꾸어도 해결되지 않는다.**

## 기준선과 증거

| 대상 | 확인한 내용 |
|---|---|
| 현재 소스 | `40ab68aaefa05fae032abb23944ba2927b78fab4`, `refactor/platform/recovery-contract-closure`. Windows/WSL의 기존 작업 보존 |
| 이전 감사와 차이 | [장애 대응 감사](recovery-architecture-audit-20261027.md)의 `cd86fd0652ac5b307b6c2735c25dc8363e75c801`와 **모든 추적 파일 바이트 일치**. HEAD는 target 병합 커밋이지만 제품 트리는 동일 |
| 원격 | fetch 후 `origin/platform=e44636da7ebd15e68ff9d67432e3fc5a9eda9534`. 관련 source는 현재 HEAD. 감사 중 브랜치 전환·병합 없음 |
| 기존 미추적 자료 | 이전 감사 보고서와 `audit_recovery_contract.py` 보존. 보고서에는 이번 보고서 링크만 추가 |
| 이전 원자료 | `BASELINE.json`, `AUDIT_PROBES.json`, `DEPLOYMENT.json`, `FINAL_VERIFICATION.json`과 archive를 실제 읽고 해시 대조. 이전 archive SHA-256 `20055aec71c6d1abaa3db55cc752cb7aa38fee017e9f19b9562222be27f76963` |
| Telemetry 완료 기준 | 이전 감사가 대조한 최종 source manifest 81개와 현재 소스 일치. [Telemetry 마감](telemetry-measurements-20261026.md#로컬-마감-보완--2026-10-27) 및 주행·종료·실행별 회귀의 동일 후보 근거 재사용 |
| 이번 자료 | PC `C:/Users/SSAFY/Documents/Codex/2026-10-27/xenova-persistence-audit/`, WSL `/home/ssafy/xenova-persistence-audit-20261027/`. `BASELINE`, `SOURCE_INDEX`, `FILE_PROBES`, `SQLITE_PROBES`, `TARGET_SQLITE`, `FINAL_VERIFICATION` JSON과 개별 실행 로그 |
| 변경 범위 | 이 보고서, 기존 보고서의 연결, [감사 도구](../../platform/tests/audit_persistence_contract.py). 제품 소스·설정·기존 시험·recipe·의존성·실제 상태 변경 없음. 보드 접속·commit/push 없음 |

프로젝트 전체가 파일만 사용하는 것은 아니다. [Spring 설정](../../backend/src/main/resources/application.yml)은 MySQL을 사용하며 테스트에도 MySQL 경로가 있다. 그러나 아래 하위 Manager·Worker·Gateway의 권위 상태에서 SQLite/ORM 사용은 확인되지 않았다. Spring 업무 DB는 이 상태들의 원본이 아니며 이번 전환 후보에 포함하지 않는다.

확실성은 **코드 확인 / 로컬 재현 / 기존 동일 후보 근거 / 설계 제안 / 미확인**, 성격은 **결함 / 단순화 후보 / 의도된 보호 / 범위 밖**으로 구분한다. 도구의 `audit_completed=true`와 종료 코드 0은 제품 보장 전체의 통과를 뜻하지 않는다. 결과에는 `all_product_guarantees_passed=false`를 유지했다.

## 저장 책임 지도

경로는 기본 설치 기준이다. 임시 fixture의 동일 이름 경로를 실제 `/srv`·`/run` 상태로 취급하지 않았다.

| 원본·형식 / 소유자 | 내용·생명주기·빈도·규모 | 일관성·잠금·내구성·보존 |
|---|---|---|
| `/srv/xenova/control/<UUID>.json`, `control-requests/<operation>.json` / 하위 Manager·Execution | 사용자 desired_state·revision·lifecycle, 원 요청/hash/key·operation·host·절대 deadline·phase·결과. 관리 요청/진행 단계별 쓰기. 요청 파일 전체를 읽는 key 검색, 완료 journal의 TTL/개수 정리 없음 | [Execution](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_execution.py)의 `requests` 622–636, `_accept_locked` 661–707. 짧은 admission flock과 긴 execution flock 분리. prepared journal→intent→accepted journal의 **재개 가능한 다중 파일 프로토콜**. 파일·부모 디렉터리 fsync |
| `/srv/xenova/recovery-attempts/<attempt>.json` / 하위 Recovery | 원 spec/hash·attempt/operation·CPU 문맥·기한·진행/결과. 시도·단계별 쓰기. 미완료 소유자 검색에 journal 스캔 | [Recovery](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_recovery.py) `attempts` 56–66, `conditions` 81–113, `_once` 202–333. 같은 attempt 재전달, 실행/의도/host 대조. 파일 원자 저장. `stopping`·불확실 상태는 삭제로 해제하지 않음 |
| `/srv/xenova/operations/*.json`, registry/`instances/*/instance.json`, tombstone·disk·bundle / Manager·asset 경로 | 등록 UUID·구성·자원 변경·retire/restore/purge 진행. 사용자 작업별. 펌웨어/디스크는 대량 파일 | 로컬 journal 외에 파일 이동·디스크·DHCP·Xen 효과와 조정. DB commit으로 외부 자산 publication/복구를 대체할 수 없음. 설정량과 CPU 정책은 별도 원본 |
| `/run/xenova/recovery/recoveries.json` schema 1 / Recovery Engine | recovery/VM·원 attempt/spec/deadline·시도 수·backoff·진행/결과·`pending_events`를 **한 JSON**에 저장. 단계별 전체 재작성. 열린 항목 + 최근 닫힌 항목 **20개**; pending 사건이 있는 닫힌 항목은 유지 | [RecoveryStore](../../platform/services/dom0-manager/dom0_manager/recovery/state.py) 180–253, [Engine](../../platform/services/dom0-manager/dom0_manager/recovery/engine.py) 521–577. Worker 단일 프로세스 flock + Engine RLock + 실행 executor 1개. tmp·file fsync·rename, 부모 fsync 없음. **Worker 재시작 보존, host 재부팅 시 초기화** |
| 같은 디렉터리 `recovery-events.jsonl` / Worker writer·HTTP reader | 기존 5종 복구 사건, 고정 event_id·cursor. 최근 **2,000개**, compaction 여유 200줄. 매 사건 append, 조회 시 파싱·검증·중복 제거. 명시적인 사건 바이트 상한은 없음 | [EventLog](../../platform/services/dom0-manager/dom0_manager/recovery/events.py) 54–171. 프로세스 내부 Lock; 파일 append/flush/fsync, compaction tmp/rename. 손상 위치 tombstone·gap 보존. 상태의 pending 사건에서 이동 후 HTTP 재보충 원천이 됨 |
| 같은 디렉터리 `armed.json` schema 1 / Watcher·ArmingStore | VM의 과거 app heartbeat 수신에 따른 감시 대상 정보와 history_lost. 신호 원문·신선도 아님. 대상 변화 때 쓰고 host 재부팅 시 재구축 | [ArmingStore](../../platform/services/dom0-manager/dom0_manager/recovery/arming.py) 62–103. tmp/rename, fsync 없음. capability 미선언·기록 손상은 관측 불가로 보호. S01의 손상 격리 실패 경계 공유 |
| `/srv/xenova/drive-control/control.json` + `initialized`, schema `1.0-drive-authority` / Gateway | generation·mode·owner·phase·pending host/deadline·재무장·소비한 요청 UUID. 저빈도 권한 전이/운영 요청 때 저장. 세대당 요청 **128개**, 초과 거절·조용한 eviction 없음 | [Authority](../../platform/services/gateway/xenova_gateway/authority.py) 29–138. private 디렉터리·실제 ext4/mount 검사, GatewayLease flock, RLock, 공용 atomic JSON의 file+directory fsync. marker 선저장 중단은 차단. 재시작은 출력 disarmed. 파일 오류 latch가 옛 메모리 덮어쓰기 차단 |
| `/srv/xenova/drive-rejoin/<VM>/<execution>/{telemetry,drive}.json`, schema `2.1-bootstrap` 및 `rejoin.json` / provisioning·Coordinator | 원 host/execution/domid/xen_unique_id·boot/app/Publisher instance·receipt ID·prepared/applied/uncertain와 build/artifact 메타데이터. 실행별 공급/단계 변경 때 저장. credential 원문은 이 메타데이터와 분리 | [Provision](../../platform/scripts/provision-xenova-telemetry.py) `save_receipt` 210–223, `provision` 226–301 및 [Rejoin](../../platform/services/dom0-manager/dom0_manager/recovery/rejoin.py). exclusive 생성·private 모드·file/dir fsync. 실행별 flock/VM slot은 Store 작업의 소유권도 보호. v1/v2 instance는 별개, 같은 guest boot namespace 연결 |
| `cpu-policy.json`, `cpu-policy-enabled.json`, `cpu-policy-operations/*.json` / 로컬 CPU 정책 경로 | revision·정책·명시적 변경 operation/key. 저빈도 설치/정지 조건의 정책 변경. 실제 affinity는 별도 관측 | [CPU 정책](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_cpu.py). prepared→활성화 anchor→정책→completed 재개. runtime `cpu-host.json`은 host+revision에 결합한 applying/partial/ready. Xen affinity 조치와 원복은 외부 효과 |
| `/run/xenova/executions`, observations, admission, CPU 준비, `drive-bindings.json`; Coordinator의 `_progress` / 현재 실행·관측 생산자 | host-scoped 실행/신원, 관측 및 수명 제한 사본. binding lease 기본 3초, pump 약 0.1초, Manager 관측 약 1초. readiness 진행은 메모리; 기록된 READY로 재시작 후 신선도를 복원하지 않음 | 현재 host/실행/의도 대조 후 재생성. [BindingCache](../../platform/services/vm-agent/mqtt-client/xenova_mqtt/drive_binding.py)에서 lease/실행 불일치 거절. DB 영구 원본 후보가 아님 |
| env·정책 기본값·manifest / 설정 경로 | Worker 기본 max_attempts 3, backoff 5/15/30초, attempt 110초·상한 120초, verify 150초는 설정. 실제 소비한 시도 수·원 spec/기한은 recovery 기록 | 설정 파일을 복사한다고 원 attempt 예산이 확정되는 것이 아님. host 단조 시각의 기한은 다른 host에서 이어 쓰지 않음. 정책 변경 후 적용 계약은 별도이며 DB가 자동 고정하지 않음 |
| credential/private key, MQTT·heartbeat·readiness 원문, 콘솔·crash·원자료·artifact | 비밀은 기존 private 경계. 연속·대량 자료는 기존 제한된 파일/회전, 필요한 수신 시각·진행은 메모리 | DB/BLOB·범용 영구 메시지 큐·자동 재생·상시 수집 확장 제외. 기존 Telemetry archive와 필터 임시 기록의 회전은 변경하지 않음 |

하위 원자 저장은 [runtime `atomic_write`](../../platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_runtime.py) 65–84를 재사용한다. root 소유 디렉터리/파일 0700/0600 검사와 Worker unit의 `UMask=0077`은 구분한다. Worker store 자체는 Authority와 같은 mount·owner 검증을 제공하지 않는다. persistent override를 지원하려면 unit의 마운트 의존성·권한·실패 처리까지 명시해야 하며, 이번에는 변경하지 않았다.

## 후보별 판정표

아래의 “제거”는 전환 설계가 실제 채택될 경우의 저장 책임이다. **이번 감사에서 제품 함수를 삭제하거나 SQL adapter를 완성한 것은 아니다.**

| 발견 ID | 기존 감사 D/A | 실제 파일·함수 | 보장·문제/유지비 | 현재 근거 | 파일 국소 보완안 | SQLite 대체안 | 제거할 코드 | DB로도 남는 책임 | 새 비용·위험 | 최종 권고·미검증 |
|---|---|---|---|---|---|---|---|---|---|---|
| S01 | D01/D03, A06/A15 | RecoveryStore·ArmingStore `_lost/load/save` | **결함**: corrupt 격리 뒤 차단 저장 실패→재시작 시 정상 빈 상태 | 코드 확인·`quarantine` 로컬 재현 | 격리 전 차단 상태 원자 확정 또는 본 파일 부재+격리 흔적 차단; 저장 실패 전파. `.corrupt`도 새 초기화로 오인하지 않음 | 기존 DB 손상/부재 거절을 동일하게 구현해야 함 | JSON별 격리·재로드 일부만; 설치/유실 구별은 유지 | 장애 판정·재시도 허가·원 시도 대조 | DB도 자동 빈 생성이면 동일 위험 | **파일 국소 보완 우선**. 실제 제품 수정·보드 미수행 |
| S02 | A06/A08 | EventLog `append`, Engine `_flush_pending` | **결함(보장 설명)**: False여도 이미 한 줄 쓰였을 수 있음. 동일 ID 재시도는 안전 | `event_append`: raw 2줄/visible 1개 | 실패를 효과 미확정으로 정의, 고정 ID 재시도·dedup 유지; 주석/시험 보완 | UNIQUE(event_id)와 상태+사건 트랜잭션 | append 후 줄 중복·파싱 dedup 일부 | commit 오류와 publish 결과 불확실성 | DB 오류 후 원 ID 조회 필요 | **국소 보완**. 현 호출자가 잘못된 새 ID를 발급한다는 증거는 없음 |
| S03 | D07/D09, A16/A25 | RecoveryStore `save`, ArmingStore `save`, unit | **조건부 내구 공백**: Worker 기본은 tmpfs, persistent override에는 parent sync/마운트 계약 부족 | `sync`: file only 대 file+directory | 공용 helper·명시적 persistent 모드·unit/mount 검증 | local ext4 DB + 명시적 sync·mount/초기화 검사 | 파일별 write/fsync/rename | host 초기화 정책·실장 저장장치 내구성 | 추가 패키지·fsync 비용·부가 파일 | 기본 `/run`을 결함으로 오인하지 않음. **기본 유지**, 영속 경로 변경은 별도 결정 |
| S04 | D03/D07/D08, A06/A08 | RecoveryStore, EventLog, Engine `_commit/_flush_pending/_prune` | **단순화 후보**: 전체 JSON 재작성·중복 열린 VM 검사·pending→JSONL 이행·compaction | 기존 129개 회귀·파일 중단·SQL 2개 모드 | 현 한 파일 원자성 인정, S01/S02 보완·저장 helper 통일 | Worker 소유 상태+기존 5종 사건만 같은 DB transaction | store 전체 재작성, 수작업 중복 검사, persistent pending 복사/이행, JSONL dedup/compaction | Engine 상태 전이·예산·원 요청·외부 결과/재시도·HTTP gap | schema/retention·busy·이관·사건 크기 합의 | **전환 검토 1순위**, 즉시 도입 확정 아님. 실제 adapter/target/전원 미수락 |
| S05 | D02/D03/D08, A05–A09 | Execution `_accept_locked/requests`, Recovery `attempts` | **단순화 후보**: prepared→intent→accepted와 journal 스캔. 기존 재개/stop 보장 있음 | 기존 stop 20·review 21, SQL admission/fixture 이관 | 현 프로토콜·공용 atomic helper 유지 | intent+원 operation 수락 동일 transaction; 하위 attempt는 같은 책임 안에서 후속 검토 | intent publication 복구 분기·key/미완료 검색 일부 | 긴 실행 소유·guard·물리 phase·미확정 종결·종료 신원·HTTP 전달 | Manager 전 경로 이관 범위 큼·writer 경합·key 보존 | **조건부 2순위**. SQL 모형은 전체 Manager 대체 수락 아님 |
| S06 | D03/D05, A15/A16/A21 | Authority `initialize/save/consume_request/reconcile` | **의도된 보호**: 작은 단일 상태 원자 저장·128 ID 제한. 소비 ID와 최종 결과는 다름 | 기존 5개 회귀·`authority` 중단/오래된 사본 | 현재 유지. 요청 결과 조회/복원은 별도 계약으로 검토 | 추후 결과 계약이 필요하면 상태+요청 결과만 공동 확정 | atomic JSON·marker publication 일부 | 단일 Gateway/장치 소유·retained 대조·최신 세대 증거·재무장 | DB 유실/오래된 백업·새 schema/운영 비용 | **파일 유지**. 결과 부재를 DB만으로 해결 불가 |
| S07 | D03/D09, A18/A19 | `save_receipt/provision`, Coordinator `_record` | **의도된 보호/외부 조정**: 실행별 소량 파일, prepared/applied/uncertain | 기존 uncertain retry·동일 실행/receipt 거절 근거 | 원 receipt·실행 보존, 지원 수습 경로 보완 | 필요 시 제어 메타데이터만 기존 소유 DB로; 현재 이득 작음 | 파일 저장 helper 정도 | Store commit·현재 실행 대조·VM slot·자격 공급 소유 | 비밀 혼입·파일/DB 이중 원본·이관 위험 | **파일 유지**. uncertain 해결은 SQL 수락 아님 |
| S08 | D01/D04/D06/D07, A01/A13/A25/A26 | CPU 정책, registry/assets, binding/cache, 설정·자료 | **범위 밖/일부 조건부 후보**: 정책 publication과 실제 적용은 별개 | 코드 확인·이전 동일 후보·binding invalidation | 현재 단일 원본과 host lease 유지 | CPU publication은 향후 Manager 저장 통합 때만 평가 | CPU 다중 파일 publication 일부; asset 외부 복구는 제외 | Xen affinity·자산 이동·신선도·입력/준비·원자료 | 책임 통합·잦은 cache DB 쓰기·부당 재생 | **파일/메모리 유지**, 이번 SQL 후보 확대 안 함 |
| S09 | D07/D08/D09, A06/A16 | target pkgdata/ELF, journal 보존·운영 | **도입 비용/의도된 보호**: DB 제약이 삭제한 key나 최신 generation을 기억해 주지 않음 | SQL missing/corrupt/busy/FULL·backup·unsafe prune | 원 journal 보존·지원 조회·통제된 백업 | target 명시 의존성·schema/백업/복원·key tombstone 정책 | 수작업 저장 관리 일부 | 최신성·원 기한·외부 효과·mount·재난 복원 | 신규 RDEP·WAL/checkpoint·복원 시험 | **조건 선행 후 결정**. 이미지/보드/실제 전원 미검증 |

## 확인된 결함과 유지할 보호

### S01 — 손상 격리 후 저장 실패가 다음 재시작의 차단을 잃게 함

실제 `RecoveryStore.load()`는 손상 JSON을 `_lost(preserve=True)`로 보낸다. `_lost`가 원 파일을 `.corrupt`로 이동한 뒤 `history_lost=true` 파일 저장을 시도한다. 여기서 ENOSPC를 주입하면 현 프로세스는 차단 상태이지만 본 파일이 없다. 다음 프로세스의 `load()`는 이를 최초의 빈 상태와 구별하지 못한다. 실제 Engine fixture에서 새 판단 후 **논리 recover_once 호출 1회**가 가능했다. 실제 Xen 재시작 중복을 증명한 시험은 아니다.

ArmingStore에서도 같은 순서로 history_lost가 유실됐다. “신호를 받은 적 없음”과 “이전 감시 대상을 잃음”의 구별이 사라지는 문제이며, 실제 VM 사망으로 분류해야 한다는 뜻은 아니다.

최소 후속 수정은 파일 방식에서 먼저 한다. 유효한 차단 원본을 교체 완료하기 전 기존 원본을 없애지 않거나, 본 파일 부재와 격리 흔적의 조합을 차단으로 읽고 저장 실패를 성공 초기화로 바꾸지 않는다. 필요한 회귀는 corrupt→격리→ENOSPC/EIO→프로세스 재시작, 기존 유효 파일의 중단, 최초 미설치의 정상 구별이다. 제품 수정은 이번 감사 범위 밖이다.

### S02·S03 — 실패의 효과와 내구 범위를 과장하지 않음

EventLog에서 append 후 fsync 오류를 주입하면 False이지만 줄은 존재했다. 같은 event_id 재시도 후 raw 2줄·조회 1건이었다. 따라서 “False면 아무것도 쓰이지 않음”이라는 설명은 틀리지만, 현재 고정 ID 재시도·dedup은 유지해야 하는 보호다.

RecoveryStore의 rename 전/후 SIGKILL은 각각 기존 상태+사건 0개와 새 상태+사건 1개를 반환했다. **상태와 pending_events는 이미 한 파일의 원자 교체 단위**다. 이 보장을 SQL 전환의 신규 성과로 세지 않는다.

현재 Worker 기본 `/run`은 서비스 재시작을 위한 tmpfs 보존이다. file fsync만 있고 부모 디렉터리 fsync가 없는 점을 일반 전원 재부팅 내구성 위반으로 단정하지 않는다. 반대로 persistent 경로로 환경값만 바꾸어 전원 상실 보장을 선언할 수도 없다. 이 경우 mount 실패·처음 설치·손상·경로 유실·공용 helper 적용을 함께 검증해야 한다.

## 최소 전환 경계

### Worker 상태·기존 복구 사건 — 우선 검토할 묶음

`기존 recovery 상태 → 새 상태/원 attempt·예산·고정 사건 ID를 같은 transaction으로 확정 → 외부 실행 또는 기존 사건 통지 → 확인된 결과/미확정 상태를 별도 transaction으로 확정`으로 한정한다.

SQL에 맡길 후보는 recovery ID/attempt ID 유일성, VM당 열린 recovery 하나, 상태와 최소 사건 공동 저장, event_id 유일성과 cursor 범위다. Engine의 `_commit`은 상태 전이 진입점으로 유지하되 `_save`의 전체 map 재작성과 `_flush_pending`의 **파일 간 복사·pop 후 재저장 부분**을 없앨 수 있다. `RecoveryRecord.validate`의 spec/phase 관계, `_task_replay`, `_build_spec`, `_on_result/_on_error`, backoff·종결·원 deadline 처리는 남는다. JSONL의 `_read` dedup·`_compact`는 SQL 조회/보존으로 대체하지만 gap 계약은 유지한다.

현재 사건은 `recovery.started`, `recovery.completed`, `recovery.recovered`, `recovery.cancelled`, `recovery.failed`뿐이다. [생산 호출부](../../platform/services/dom0-manager/dom0_manager/recovery/__main__.py)는 **EventLog 저장 후 QoS1/retain=false/wait_s=0**으로 제출하고, 게시 예외가 나도 HTTP 재보충 원천이 있으면 성공을 반환한다. 이는 PUBACK까지 보관하는 신뢰성 큐가 아니다. SQL의 통지 상태도 이 기존 의미를 따른다면 “제출 시도”이며 “소비자 반영”이 아니다. 제출 직후 기록 전 중단에서는 같은 event_id 중복이 남는다.

| 보존 항목 | 현재 계약과 전환 전 결정할 내용 |
|---|---|
| 크기 | 현재 error.message에 명시적인 바이트 최대가 없다. PoC의 사건 8KiB CHECK는 **시험용 제안**으로, 제품의 기존 사건을 자르거나 거절하는 확정 계약이 아니다. 실제 최대와 oversized 실패 반환은 별도 합의 필요 |
| 발생량 | 기본 max_attempts=3에서 각 시도는 started와 해당 시도 종결 사건을 만들고 장애 단위 후속 종결이 가능하다. 새 episode가 반복되므로 전역 영구 발생 상한은 없음. 모든 heartbeat 저장으로 확장하지 않음 |
| 미이행 | 아직 로컬 사건 원천에 반영하지 못한 상태+사건은 유지. 공간 부족이면 저장 실패·후속 실행 차단/재시도 의미를 보존하며 조용히 삭제하지 않음. 허용 공간·운영 알림·최종 수습은 도입 전 합의 |
| 완료 자료 | 현재 닫힌 recovery 20개와 사건 2,000개는 수명이 다름. 사건 row를 살아 있는 recovery row의 FK에 강제 결합해 사건을 지우거나 상태를 무제한 유지하지 않음. 기존 고정 ID·cursor/gap 의미를 유지 |
| 반복 통지 | DB에서 사건이 한 행이어도 게시 시도는 여러 번 가능. 원 event_id로 소비자 중복 처리. 통신 원문·주기 지표·입력·readiness는 저장하지 않음 |

이 묶음이 가장 많은 자체 저장 이행 코드를 줄이지만, 현재 상태 규모에서 성능 병목이라는 실측은 없다. **유지보수 단순화 후보**이지 성능 개선 확정이 아니다. 기본 저장 위치를 `/run`에서 `/srv`로 옮기는 결정은 SQLite 채택과 별개다. host 재부팅 후 attempt 예산을 이어갈지, 현재 host 근거를 다시 확보할지의 정책을 먼저 정해야 한다.

### Manager 의도·원 operation 수락 — 조건부 후순위

`현재 host/lifecycle/revision + 동일 key/spec 조회 → 새 stopped intent/revision + 원 operation/spec/deadline/accepted 결과 공동 확정 → 긴 execution 잠금과 실행 직전 guard → 원 물리 작업/결과 저장`이 대상이다.

`_accept_locked`의 prepared journal→intent→accepted 사이를 하나의 로컬 transaction으로 바꾸면 intent만 게시된 중단을 찾아 같은 revision으로 재개하는 분기를 줄일 수 있다. `requests()`의 전체 디렉터리 key 스캔과 `Recovery.attempts()`의 일부 유일성 검색도 인덱스로 대체 가능하다. 그러나 현재 모든 entrypoint가 사용하는 수락·journal writer를 같은 원본으로 전환해야 한다. JSON 원본을 그대로 두고 DB 색인만 덧붙이는 것은 이 단순화에 해당하지 않는다.

단순화하더라도 `boot_decision`의 승인 여부, execution 신원, CPU 준비·정책, stopped 우선순위, 종료 안전성, physical submitted/uncertain/stopping 단계는 없앨 수 없다. SQL transaction 밖에서 외부 작업하고 마지막 guard로 새 stop revision을 대조한다. 긴 execution 소유권을 DB write lock으로 대체하지 않는다. 하위 recovery attempt를 별도 DB로 쪼개면 intent와 함께 원자 저장할 수 없으므로 DB 개수는 책임 경계 결정 이후 정한다.

PoC는 intent와 operation의 수락 투영만 구현했다. 원 ID/revision/spec/hash/phase/deadline의 보존을 확인했지만 모든 error/details/events·CPU 문맥의 이관, 전체 Manager/Recovery의 SQL adapter, HTTP pending 전달, 실제 Xen 명령과의 통합은 구현·수락하지 않았다. 이 투영을 그대로 제품 schema나 완전한 이관기로 사용할 수 없다.

### 파일로 유지할 원본

Authority는 generation·선택·pending·소비 ID를 이미 작은 한 객체로 저장한다. marker-first 초기화 중단 차단, directory fsync 이후 불확실 latch, 128 ID 초과 거절이 실제 시험을 통과했다. 저장되는 것은 **소비한 요청 ID**이며 countdown의 최종 결과 조회를 제공하지는 않는다. 결과 보존이 필요하면 먼저 해당 공개 계약을 정해야 한다. SQL 자체가 재시작 후 자동 재무장, 과거 resume 재생, 최신 generation 증명을 허용하지 않는다.

receipt는 local prepared를 저장하고 Store 적용 후 current execution을 다시 확인해 applied를 기록한다. 그 사이 중단은 SQL이어도 uncertain이다. receipt lock/VM slot은 외부 공급의 중복 실행도 막으므로 짧은 DB transaction과 별개로 남는다. credential 원문을 receipt 메타데이터나 감사 DB에 복사하지 않는다. 파일을 유지한다면 파일이 원본이며 DB 사본을 추가하지 않는다.

CPU 정책은 드문 변경이고 자산은 외부 파일 효과가 크다. CPU publication 저장만 향후 Manager 저장 통합 때 재평가할 수 있다. affinity 실제 적용·부분 원복은 SQL 원자성에 포함되지 않는다. 현재 binding·LastSeen·readiness·연결 상태는 재구축 사본이며 영구 저장으로 신선도를 만들지 않는다.

## SQLite 도입 조건과 비용

로컬 transaction의 원자성은 DB 안의 변경에 한정되며 Xen/Store/MQTT와 공동 commit이 아니다. 외부 작업을 write transaction 안에 두어 stop 수락을 막으면 안 된다. SQLite는 WAL이어도 동시 writer가 하나다. [Transaction](https://www.sqlite.org/lang_transaction.html), [Atomic Commit](https://www.sqlite.org/atomiccommit.html), [WAL](https://www.sqlite.org/wal.html).

| 조건 | 이번 확인 / 도입 시 필요한 작업 |
|---|---|
| 내구 설정 | 임시 파일 DB에서 rollback `DELETE + synchronous=EXTRA`, `WAL + FULL` 비교. 기존 file+parent fsync와 대응하기 위해 DELETE/FULL만으로 동등하다고 하지 않음. NORMAL/OFF로 속도 비교 안 함. 실제 SD/ext4 전원 상실은 미검증. [PRAGMA](https://www.sqlite.org/pragma.html#pragma_synchronous) |
| 경합·기한 | BEGIN IMMEDIATE, 원 요청 잔여 예산으로 busy_timeout 제한, 획득 직후 만료 재검사. 30ms busy의 실제 관측은 DELETE 30.983ms/WAL 31.087ms(각 1회). busy_timeout은 전체 I/O/fsync 강제 상한이 아니며 새 deadline을 주지 않음 |
| 제약·자료형 | 매 연결 foreign_keys=ON 확인, key/attempt/event ID UNIQUE, VM당 열린 시도 partial unique. UUID·u64 문자열과 원 host/deadline 문맥 유지. `18446744073709551615`를 TEXT로 왕복 확인. generation u32 범위·상태 전이는 앱 검증 유지. [Foreign Keys](https://www.sqlite.org/foreignkeys.html) |
| 초기화·오류 | 정상 운용은 `mode=rw`, schema version 검사. missing·NOTADB·unknown schema·READONLY/FULL/BUSY를 빈 정상 DB로 만들지 않음. 실제 mount/설치/소유/권한 검사 후 별도 최초 생성. DB 실패 때 과거 JSON 자동 fallback 금지 |
| 권한·배치 | local filesystem, private directory/DB 및 journal/WAL/SHM 같은 소유 경계. 원 Worker/Gateway 프로세스 lease와 외부 실행 잠금 유지. 여러 host의 network filesystem WAL 공유 안 함 |
| WAL·백업 | checkpoint·긴 reader·최대 WAL 크기·공간 부족 정책 필요. 지원 Backup API 또는 통제된 offline 방식 사용; 실행 중 본체 파일만 복사하지 않음. backup integrity와 최신성은 별개. [Backup API](https://www.sqlite.org/backup.html) |
| 유지비 | schema version/지원 reader, 단일 원본 전환, key 보존·cursor/gap, 공간/손상 진단·지원 수습·백업/복원 검사가 새 책임. ORM/새 daemon/CLI 패키지는 현재 필요 근거 없음 |

**Target 확인:** 고정 build `build-rpi4-latest-image-20261026-121448/tmp`의 pkgdata에서 SQLite **3.51.3**, Python sqlite binding **3.14.7**을 확인했다. `libsqlite3.so.3.51.3`와 `_sqlite3.cpython-314-aarch64-linux-gnu.so`는 실제 **AArch64 ELF**이고 binding은 libsqlite3를 동적 참조한다. recipe는 threadsafe를 켠다. 실제 target `PRAGMA compile_options` 실행, import, runtime FK/sync 동작은 확인하지 않았다.

패키지 payload는 libsqlite3 **1,525,884B**, python3-sqlite3 **168,711B**, 선택 CLI sqlite3 **397,240B**다. Python 경로는 CLI 없이 binding·library가 필요하며 `python3-sqlite3`의 python core/datetime·glibc 의존성도 확인했다. 이 수치는 압축 WIC 증가량이 아니며 이미 포함된 패키지를 빼지 않은 크기다. 현재 이미지 설치 여부·최종 WIC·보드는 미확인이다. 도입 시 명시 RDEP와 패키징 승인이 필요하고 이번에는 추가하지 않았다.

호스트 비교 환경은 Python **3.12.3 / SQLite 3.45.1**, THREADSAFE=1이다. target과 버전이 다르다. 공식 문서는 WAL reset 관련 수정이 **3.51.3** 등에 반영됐다고 설명한다. 호스트 3.45.1의 배포판 backport 적용 여부와 동시 checkpoint 스트레스는 확인하지 않았으므로, 이번 WAL 성공을 target 버전·장시간 WAL 수락으로 확대하지 않는다. [WAL reset bug](https://www.sqlite.org/wal.html#walresetbug).

## 검증 대응

다음 명령은 WSL에서 기존 격리 시험 의존성을 PYTHONPATH로 연결해 실행했다. 새 패키지 설치 없음. 결과 파일 경로는 위 증거 디렉터리이며 fixture만 사용했다.

```sh
python3 -B platform/tests/audit_persistence_contract.py \
  --phase files --output "$EVIDENCE/FILE_PROBES.json"
python3 -B platform/tests/audit_persistence_contract.py \
  --phase sqlite --output "$EVIDENCE/SQLITE_PROBES.json"

cd platform/services/dom0-manager
python3 -B -m pytest -p no:cacheprovider \
  tests/test_recovery_engine.py tests/test_recovery_events.py \
  tests/test_recovery_watcher.py tests/test_recovery_lock.py
# 129 passed, 기존 Starlette 경고 1개

cd ../gateway
python3 -B -m pytest -p no:cacheprovider tests/test_verified.py \
  -k 'authority or restart or retained'
# 5 passed, 13 deselected
```

| 근거 ID·결과 | 실제 확인 / 대체 경계·한계 |
|---|---|
| files/quarantine — 보호 실패 재현 | 실제 Store·ArmingStore·Engine. corrupt 격리 후 ENOSPC와 새 인스턴스 load. 외부 recover_once만 FakePlatform. 실제 중복 Xen 효과는 미확인 |
| files/event_append — 설명 불일치, dedup 통과 | 실제 EventLog fsync EIO 주입. 재시도 raw 2/visible 1. 물리 매체 EIO 아님 |
| files/sync — fsync 위치 확인 | RecoveryStore file 1회, 공용 lower atomic/binding atomic file+directory 각 1회. ArmingStore는 코드상 fsync 없음 |
| files/file_crash — 기존 원자성 통과 | 실제 RecoveryStore의 rename 전/후 SIGKILL. 상태와 pending_events 반쪽 없음. 전원 상실 아님 |
| files/authority — 보호 통과 | 동일 ID 재시작 거절, 128개 한도와 overflow 거절, interrupted init 차단, 오래된 정상 사본과 높은 retained 충돌. 최신 세대 자동 복원·최종 요청 결과 지원 아님 |
| files/prior_boundaries — 동일 소스 재사용 | 이전 D02 stop intent 수락 뒤 HTTP 503 details 유실, D03 만료 stopping/Store uncertain 차단, binding invalidation 원자료 재사용. stopping에서 추가 create 0, receipt uncertain 재시도에서 새 ID/Store 재호출 없음 |
| SQL atomicity — DELETE/WAL 각각 통과 | intent 직전/commit 직전/commit 직후 SIGKILL; 상태+사건 중간/commit 경계. 양쪽 공동 확정 또는 rollback. 저장 후 재전달 원 operation/deadline 유지 |
| SQL concurrency — 두 모드 통과 | 프로세스 2개 동일 key/spec→같은 ID·1행. 상충 spec·FK 위반 거절. 실제 물리 실행 adapter는 없음 |
| SQL failure — 두 모드 통과 | BUSY·READONLY·페이지 한도 FULL·u64·원 예산 만료·missing/corrupt/schema 거절. FULL은 실제 디스크 부족 아님. SQLite VFS IOERR/실제 fsync 실패 미주입 |
| SQL external_boundary — 경계 유지 | SQL 연결/transaction 없는 별도 외부 대기 중 stop commit. 각 1회 15.391ms/17.435ms는 비교 성능·보장 상한 아님. 외부 효과를 나타낸 임시 파일 후 결과 미기록→executing 유지, 자동 재제출 0. Xen/Store 대역 모형 |
| SQL event_backup — 두 모드 통과 | 상태+사건 1행, 통지 후 완료 기록 유실 시 동일 ID 2회 제출 가능, periodic type 거절·VM당 open 1개. Backup API integrity=ok여도 과거 상태일 수 있음. MQTT/PUBACK/소비자 시험 아님 |
| SQL migration_retention — 두 모드 통과/위험 재현 | 실제 Manager fixture의 pending stop·원 ID/revision/phase/deadline과 실제 RecoveryStore fixture를 임시 SQL로 변환·재구성. 새 shutdown 0. pending 사건 보존. **의도적으로** 옛 완료 key 행을 지우면 같은 key가 새 작업으로 수락되는 모형 반례. 제품 정리 정책 변경 아님 |

총 파일 probe **6묶음**, SQL probe **12묶음(6종×2모드)**를 완료했다. 기존 Worker 저장/Engine/Watcher/lock **129개**, Authority 관련 **5개** 회귀를 새로 실행했다. 이전 동일 후보의 Manager 380, Gateway 84, stop 20, review 21 및 별도 주행·lifecycle·Telemetry 회귀는 원자료·소스 해시로 연결했으며 모두 다시 실행했다고 주장하지 않는다.

감사 도구 작성 중 import 경로 누락 1회, 새 event fixture의 원 recovery ID 불일치 1회는 **감사 도구 오류**였다. 도구만 수정하고 첫 실패 로그/이전 도구 해시도 보관했다. 기대값이나 제품 코드를 수정해 통과시키지 않았다.

## 이관·복원안과 후속 수락

전환을 선택한다면 **서비스별 유지보수 이관**으로 진행한다. DB 개수를 미리 하나로 정하거나 Manager·Worker·Gateway를 같은 권한 상태기계로 합치지 않는다.

1. 쓰기/새 실행을 통제한 상태에서 원 파일과 원 ID·phase·spec/hash·revision·host·deadline·예산·고정 사건 ID·미확정 소유권을 내보낸다. 비밀·원 통신·freshness는 제외한다. 원본은 증거 백업으로 유지한다.
2. 임시 DB에 제약·schema 검사를 적용하고 같은 논리 상태·중복 결과·cursor·보존 경계를 대조한다. 이번 fixture 예행연습은 이 단계의 작은 부분만 검증했다. uncertain을 completed로 바꾸지 않는다.
3. 명시적인 저장소 전환 표시로 새 writer/reader의 원본을 하나로 바꾼다. 기존 JSON은 읽기 전용 백업/내보내기이며 상시 이중 쓰기·자동 fallback을 하지 않는다. 실제 mount 확인 전 빈 DB를 생성하지 않는다.
4. rollback은 **DB 전환 뒤 새 변경이 없거나**, 새 ID/세대/진행·미확정 상태까지 역방향 내보내기를 검증한 경우에만 가능하다. 옛 JSON이나 오래된 일관된 DB 백업으로 조용히 복귀하지 않는다.
5. 완료 결과를 정리해도 과거 key가 새 실행이 되지 않아야 한다. 현재 Manager는 journal을 자동 TTL 정리하지 않는다. DB에서 정리하려면 원 key/spec/outcome의 필요한 tombstone 또는 명시적인 lifecycle 계약을 정한다. 공간 확보를 위해 미완료/uncertain 소유권을 삭제하지 않는다.

도입 전 수락에는 target Python import/PRAGMA·FK·실제 RDEP, private mount 실패, 여러 reader/짧은 writer 경쟁과 원 기한, process crash·실제 SD/ext4 전원 상실, WAL/checkpoint/공간 압박, 지원 백업·손상 복원, 원 요청 replay·미확정 외부 효과·stop 경쟁의 실제 adapter 시험이 필요하다. 이미지와 설치본, Xen/Store·장치 종단은 이번 로컬 감사와 구분한다.

**권고 순서:** S01 파일 국소 수정과 S02 실패 의미 정정 → Worker 상태·사건의 크기/보존·도입 비용 합의 후 작은 전환 검토 → 실제 제거량·target 실패 시험으로 이득 확인 → 필요할 때 Manager 수락 경계 재검토다. Authority·receipt·설정·cache·대량 자료는 파일/메모리를 유지한다. D01 장애 판단, D02 HTTP 정보 전달, D03 실제 결과 재확인과 지원 수습은 독립 후속 과제로 남긴다.
