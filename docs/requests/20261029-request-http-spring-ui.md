# HTTP·Spring·화면 종단 연결 요청

대상: FastAPI·Spring·프론트 담당자. **상태: 최신 하위/HTTP 제공 경로 대조 / Spring·화면 종단·새 입력 계약 수락 전.** 이번 최소 연결에서 이 제품 코드는 수정하지 않습니다. D02·D08과 원 key·deadline·후속 이행 책임을 유지합니다.

## 현재 할 일과 읽는 순서

1. [기존 관리 연동 요청](../platform/management-integration-request.md)의 실제 하위 호출·허용 details·원 key 검색 결과와 Spring 요청을 읽습니다.
2. 최신 `platform/services/dom0-manager/dom0_manager/api/vms.py`, `service.py`, `drivers/platform.py`를 대조합니다. 현재 소스에는 key 있는 stop의 200/202와 원 key 조회 경로가 있으므로 과거 문서의 HTTP 미구현 설명을 그대로 적용하지 않습니다. 배포·종단 수락은 별도입니다.
3. [관리 수명주기](../platform/management-boundaries.md), [복구 시나리오](../platform/recovery-scenarios.md)의 F1·F2·F4·FO1, 주행 설계 11~12·20절을 읽습니다.
4. Linux 준비 중 동시 조회·정지는 [아래 3번 연결 조건](#linux-준비-중-조회제어의-동시-연결)을 확인합니다. 현재 하위 제공과 실제 HTTP/제품 연결은 별개입니다.
5. 느린 조회·반복 polling과 제어의 연결은 [4번 후속 요청](#반복-조회와-제어-진행성-연결)을 읽습니다. 실제 threadpool/선행 get·현재 polling 주기와 공동 수락을 대조합니다.

## 담당자 작업

| 연결 ID | 실제 수정 위치 | 요구·금지 조건 | 수락 |
|---|---|---|---|
| D02·D08 | `backend/src/main/java/com/xenova/backend/vm/infrastructure/Dom0ManagerClient.java:stop`, `vm/application/VmService.java:stop`, VM port/DTO·controller | 최초 제출 전 안정 key를 보존하고 수락·pending·원 결과·현재 상태를 구분. 선행 find/직렬화로 짧은 stop 수락을 막지 않음 | 같은 VM의 긴 복구 중 stop 의도 내구 수락. 202를 정지 완료로 저장하지 않으며 completed replay의 vm=null/warning 보존 |
| D02·D08 | 위 client의 재시도/시간 예산·Spring 요청 추적 | 원 operation/host/deadline을 보존해 응답 유실·서버 재시작 후 key 조회와 조건부 동일 요청 이행을 연결 | 바깥 timeout을 취소/미실행으로 단정·새 key 우회 0. 검색 부재/읽기 실패/중복 key/host 변경 구분 |
| D02·D08 | `frontend/apps/admin-console/src/pages/VmDetailPage.tsx`, VM action query/상태 연결부 | 수락·진행·완료·미확정과 현재 VM 관측을 구분. 제한된 조회·사용자 재확인 경로 제공 | pending의 STOPPED 표시 0, 늦은 원 응답이 새 사용자 의도를 덮지 않음 |
| DEC-16·D01 표시 연결 | `backend/src/main/java/com/xenova/backend/agent/application/AgentMessageHandler.java`, 위 VM 화면의 앱 상태 소비 | 명시적으로 선택한 warm-drive-1의 app.lifecycle을 계약 6.3절과 대조해 소비. 대기 자격·실제 계산·출력 허가와 VM 건강 표시를 구분 | 계산 heartbeat가 없는 정상 Standby를 OFFLINE 앱으로 단정하지 않음. QUALIFIED를 READY/Active로 치환하지 않으며 구형 프로필을 묵시적으로 새 계약으로 수락하지 않음 |
| DEC-12·18·ARCH-T16·18 | `backend/src/main/java/com/xenova/backend/drive/application/ManualInputRelay.java`, 현재 입력 WebSocket·모드/복귀 요청 연결부 | 유효 세션의 현재 입력/중립을 필요한 모드·Standby에 공급. 동일 MANUAL 승계는 불필요한 재조작 없음, AUTO→MANUAL은 현재 중립 뒤 새 조작 | 연결 회복 시 옛 비중립 자동 복원 0. relay가 합성한 반복 중립으로 세션 단절을 숨기지 않음. 사용자 MANUAL 선택·현재 문맥의 명시적 AUTO 복귀 보존 |
| DEC-15·ARCH-T08 | 기존 영상 표시·수신 상태 연결부 | 실제 Jetson HTTP 경로·인증·프레임 진행/만료와 표시를 연결 | 마지막 화면을 실시간으로 오인하지 않으며 느린 화면이 mapper/주행을 막지 않음 |

## 제공물·선행조건

하위 Manager의 `fork/call/get/list/control-operation`, 원 key·intent revision·pending/uncertain 의미는 기존 정식 명세를 사용합니다. FastAPI의 현재 구현과 실제 배포값·Spring의 호출 예산을 각각 확인하고 xl subprocess timeout을 전체 Manager 예산으로 쓰지 않습니다. 새 입력 세션·운전 허용·복귀 요청의 공개 표현은 DEC-12·18과 Gateway 계약을 먼저 맞춥니다. 새 HTTP API·DTO를 이 문서의 예시만으로 임의 확정하지 않습니다.

## 세션 입력·계획 인계의 생산자 연결

DEC-12·18의 정책은 유지하며 정식 필드/상한은 [MQTT 7.1절](../contracts/mqtt-contract-v1.md#71-명시적-세션-입력-manual-session-1)을 따른다.
대상은 `drive/application/ManualInputRelay.java`, `drive/websocket/DriveInputWebSocketHandler.java`와
`frontend/apps/admin-console/src/api/drive.ts`의 입력 연결부다. 이 제품 코드는 플랫폼에서 대신 수정하지 않는다.

- 실제 인증/입력 세션에서 session_id를 발급한다. VM A/B·앱 instance·generation 교체만으로 운전자 세션을 교체하지 않는다.
- 브라우저의 실제 입력/생존 확인에 sample 순서를 부여하고 실제 조작 변경에만 action 순서를 부여한다. 현재 변경 시에만 보내는 WS 입력과 서버 50ms 재발행만으로는 지속적인 실제 확인을 입증할 수 없으므로 제한된 주기의 실제 클라이언트 확인 경로를 연결한다.
- relay는 원 sample·action·나이를 보존한다. 캐시 재발행을 새 sample/action으로 만들거나 세션 단절을 반복 중립으로 숨기지 않는다. AUTO·Standby에도 필요한 현재 입력을 공급하되 이동 허가는 Gateway에 둔다.
- 새 control에 결합한 세션 표본은 실제 현재 세션 확인으로 생산한다. 같은 MANUAL 승계는 누른 키를 이어 처리하고, AUTO→MANUAL은 해당 전환의 중립 이후 새 action이 오기 전 이동하지 않는다.
- 수락: 변경 없는 정상 키 유지의 새 확인, 확인 없는 재발행 만료, WS 단절/재접속의 옛 키 거절, 중립 표본이 최신 슬롯에서 덮인 뒤 새 조작 수락, generation만 바꾼 옛 입력의 새 조작 승인 0. 공통 C/Python 벡터와 실제 생산자 출력 의미를 대조한다.

Gateway의 로컬 `submit_transition` queue는 제품 HTTP API가 아니다. 관리 화면을 연결할 때는
원 요청 key/spec/deadline을 보존하고 큐 등록·내구 PREPARING·PREPARED·권위 COMMITTED·앱 적용·
장치 적용을 구분한다. 응답 timeout을 취소로 표시하거나 새 key로 우회하지 않는다.
현행 D02·D08의 Manager 원 요청/intent/deadline 계약과 이 주행 전환 원 요청을 혼동하지 않는다.

## 관리 요청 종단 수락의 확인 순서

1. `Dom0ManagerClient.stop`·`VmService.stop`의 선행 find/key 없는 호출을 실제 key 있는
   `request_stop` 경로에 연결한다. Driver/HTTP에 이미 있는 fork·예산·200/202를 다시 만들지 않는다.
2. 최초 전송 전 key를 보존하고 timeout·응답 유실·Spring 재시작 뒤 원 key 조회로 회수한다.
   조회 부재·예산 소진·읽기 실패·중복 key·host 변경을 구분한다. 하위는 순회 중에도 기존
   조회 예산을 검사하며 불완전한 결과로 부재/유일성을 확정하지 않는다.
3. accepted/pending이면 stopped 의도와 현재 실행을 별도로 표시하고, 남은 이행 주체를
   명시한다. 실제 실행은 원 요청·원 deadline에서만 허용하고 자동 새 key/기한을 부여하지 않는다.
4. 긴 복구/목록 조회 중 같은 FastAPI 프로세스에 별도 stop을 보내 내구 수락을 확인한다.
   최초 응답 유실·stop 수락 직후 프로세스 종료·완료 replay의 vm=null/warning·더 최신 사용자
   요청을 주입하여 UI·DB가 원 결과와 현재 상태를 혼합하지 않는지 수락한다.

큰 템플릿 생성의 자산 검증/복사 시간과 상위 응답 대기는 별도 항목이다. 이번 조회 순회
보완으로 8GiB 등록 비용이나 생성 중 전역 잠금이 해결됐다고 간주하지 않는다.

## 기본 VM 관리의 제품 종단 수락

아래는 기존 D02·D08과 Linux D01-L05를 제품 경로에서 확인할 조건이다. 새 API나
구현 완료 선언이 아니다. 하위 원문은 [VM 관리 API 계약](../contracts/vm-api-contract-v1.md),
Agent 설치·실계측은 [Linux·Agent 요청](20261029-request-linux-agent.md)을 따른다.
현재 소스의 기능과 실제 배포본의 기능은 각각 대조한다.

두 시작점은 **EC2의 Spring 공개 API**와 **보드의 FastAPI 직접 호출**이다. 같은 이름의
서로 다른 계층 응답을 그대로 비교하지 않는다. FastAPI는 현재 Xen 실행을, Spring은
자기 VM 명세와 실행·Agent 관측을 조합한다. 실제 HTTP 상태·원 오류·대상 UUID·원 요청을
대조해 어느 경계에서 실패했는지 남긴다. 보드에 연결되지 않는 상태는 생성 실패나
Agent 장애의 증거가 아니다.

| 확인 대상 | 기존 구현·담당 연결 위치 | 제품 수락 assertion |
|---|---|---|
| Debian·Ubuntu·Zephyr 생성 | `Dom0ManagerClient.launch`, `VmService.create`, FastAPI `api/vms.py:create_vm`·Provisioner | 실제 카탈로그 ID·버전·자원을 사용. 최초 UUID/명세 보존. 응답 유실 뒤 원 작업을 확인하기 전 새 UUID로 재생성하지 않음 |
| 목록·상세와 기본 정보 | Spring `VmService.list/get`, `VmResponse`, FastAPI `list_vms/get_vm` | 정지한 등록 VM도 Spring 목록에 유지. OS·템플릿·vCPU·메모리 출처와 Agent 보고 IP를 구분. 실제 실행 부재를 등록 부재로 치환하지 않음 |
| 시작·정지·삭제 | 기존 start/stop/delete와 프론트 `api/queries.ts:useVmAction` | 새 소유 VM만 제어. pending을 stopped로 표시하지 않음. 정지 확인 후 삭제·목록 제외. 하위 retire와 물리 자산 purge를 혼동하지 않음 |
| Linux 실시간 메트릭·프로세스 | `AgentMessageHandler` → `AgentDataService` → metrics/processes API, 프론트 `useMetrics/useProcesses` | 실제 해당 실행에서 온 CPU·메모리·네트워크 표본과 top 프로세스 확인. null/만료·누적값을 정상 0 또는 현재 사용률로 꾸미지 않음. mock 차트를 제품 계측으로 수락하지 않음 |
| Agent 상태·사건 | `AgentDataService.statusOf`, Agent 상태 관측기·사건 저장/조회 | 실제 heartbeat의 ONLINE→STALE→OFFLINE과 재수신 회복을 확인. Zephyr의 UNKNOWN을 Linux Agent 장애로 분류하지 않음. VM 생성·상태 변경·삭제와 Agent 사건의 UUID/시각 대조 |
| 화면 주기 갱신 | `frontend/apps/admin-console/src/api/queries.ts`, 목록·상세·메트릭·이벤트 화면 | 강제 새로고침 없이 변경·새 표본·새 사건을 반영. 소스의 VM/메트릭 5초·이벤트 3초 설정은 실제 배포 화면 관측과 별도로 확인. 오류를 빈 정상 목록으로 숨기지 않음 |
| 웹 콘솔 | 프론트 `api/console.ts`·`ConsolePanel`, Spring WebSocket relay, FastAPI 콘솔 경로 | 새 소유 VM에서 프론트 입력과 실제 guest 출력의 왕복 확인. 인증·독점 lease·종료 복귀 보존. 가짜 셸이나 접속 표시만으로 입출력 수락하지 않음 |

제품 시험을 위해 기존 서비스·DB·env·Agent 설치를 임의로 바꿔 통과시키지 않는다.
누락된 제공물이나 불일치는 원 요청 ID를 유지한 담당 작업으로 남긴다. 임시 도구·소유
시험 VM은 범위를 확인해 정리하되, 정상 생성된 제품 사건과 작업 기록을 지워 시험 흔적을
숨기지 않는다. 보드 직접 호출 통과와 Spring·화면 종단 통과는 별개로 기록한다.

### 설치본에서 재현한 직렬화 경합과 배포 대조

2026-10-30의 [실행 기록](../verification/recovery-scenario-closure-20261030.md#설치본-fastapiec2-직접-호출)에서
동일 소유 Zephyr UUID로 dom0 localhost와 EC2→FastAPI를 각각 한 번 호출했다.
두 요청 모두 `OPERATION_IN_PROGRESS`/503이며 각각 `create waited 30s for the manager`,
`get waited 30s for the manager`였다. 등록·작업 journal·도메인은 생성되지 않았다.
Spring 공개 API를 거친 호출은 아니므로 Spring 생성 실패를 재현한 것으로 확대하지 않는다.

즉시 실패 지점은 설치된 `drivers/platform.py:SerializedManager.call`의 차례 대기다.
전체 관측 시간 약 71초/59초에는 앞선 조회·대기도 포함되므로 `30초 = 전체 HTTP 예산`이
아니다. 설치본의 유효 `XENOVA_PLATFORM_BUSY_TIMEOUT`은 30이고, 최신 소스 기본 5와 다르다.
기본 VM 단건도 같은 잠금에서 503인 반면 `/api/v1/stats`는 200이었다.

담당자는 기존 D02·D08에 다음을 누적 수락한다.

- 현재 배포의 module hash·실제 설정과 정규 후보를 맞춘다. 설치 OpenAPI에는 원 key 조회와
  key stop의 200/202 계약이 없었다. 저장소에 구현이 있다는 사실을 설치 완료로 보지 않는다.
- `SerializedManager.call`·`VmService.create`의 선행 `has_domain/ensure_instance`와
  실제 반복 `list` 소비자를 함께 시험한다. 조회를 잠시 멈춘 우연한 성공이나 대기 숫자
  증가만으로 경합을 마감하지 않는다. 필수 현재 신원·CPU·자산 검증을 생략하지 않는다.
- 이미 구현된 native 신원 조회와 검증 목록 메타데이터 재사용의 배포 여부를 먼저 대조한다.
  공유 관측을 사용할 때는 기존 opt-in·유효성·만료 거절과 제어 직전의 새 guard를 보존한다.
  이 근거 없이 별도 cache나 raw Xen 우회 경로를 추가하지 않는다.
- Spring 공개 경로에서 생성과 사용자 stop의 원 UUID/key·전체 잔여 예산을 이어가고,
  실제 배포본의 6초 조회 대기와 하위 관측 비용을 대조한다. timeout을 미실행·취소로
  처리하거나 새 UUID/key를 발급하지 않는다. helper 단축만으로 이 종단 계약을 닫지 않는다.
- 기존 콘솔의 인증·독점 lease와 함께 실제 `XENOVA_CONSOLE_ENABLED` 배포값을 확인한다.
  이번 설치본은 해당 환경 설정이 없고 config 기본은 false였다. 라우터 코드 존재만으로
  프론트 콘솔 입출력이 가능하다고 보고하지 않는다. 활성화와 제품 왕복은 담당자 수락이다.

### Linux 생성의 복사 검증 실패와 원 작업 회수

하위 Linux create는 사전 전체 payload 해시 없이 metadata·용량을 확인하고 원 operation과
예약을 저장한 뒤, 실제 복사 바이트 전체를 검증한다. [정식 생성 계약](../platform/cli-manager-spec.md#creation-contract)을
따른다. 준비 실패는 기존 `RECOVERY_REQUIRED`이며 미게시 VM의 상세 조회가 부재여도
원 작업·부분 디스크가 남을 수 있다. 이 요청은 D02·D08의 상위 미구현 항목에 누적하며
HTTP·Spring·화면 제품 코드는 이번에 대신 수정하지 않는다.

- FastAPI `PlatformProvisioner.ensure_instance`와 `PlatformDriver.create`의 담당자는
  실제 하위 `operation_id/error`와 제출 전 VM UUID를 응답 유실·준비 실패 뒤에도 보존한다.
  Spring `Dom0ManagerClient.launch`·`VmService.create`는 상세 GET의 부재나 HTTP 오류만으로
  새 UUID를 발급하지 않는다. 실제 배포본의 Driver 클래스명·연결 경로부터 대조한다.
- 하위 조회 예시는 `manager.fork().call("operation", {"idempotency_key": "create:" + vm_id})`다.
  자산 operation 원장을 조회해야 하며 `control-operation(s)`의 부재로 생성 미수락을 확정하지
  않는다. 상위 담당자는 기존 인증·권한 검사 아래 이 자산 조회를 연결할 최소 HTTP 경로를
  정하고, 지원하지 않는 배포에서는 확인 불가로 표시한다. 이 문서의 Python 예시는 HTTP API가 아니다.
- 같은 UUID·정확한 명세의 조건부 재전달만 허용한다. 실패한 부분 workspace는 자동 삭제·
  덮어쓰기·재복사하지 않는다. 실패 저장 미확정, 원 기록 조회 실패, 정상 검색 부재를 구분한다.
  기존 후속 책임·예산·운영 수습 제한은 유지한다.
- 수락 assertion: 손상 payload/복사 ENOSPC·EIO 뒤 원 operation을 회수하고, 상위 재시작과
  응답 유실 뒤에도 UUID·key·할당을 유지한다. 미게시 상세 부재를 중복 생성으로 연결하지 않는다.
  부분 실패 재전달은 원 오류·파일을 유지하고, 게시 후 응답 유실은 재복사 없이 원 작업을
  회수한다. 로컬 하위 시험 통과를 실제 HTTP·Spring·화면 연결 완료로 처리하지 않는다.
- 이 절 작성 당시 미제공이던 미게시 종결/예약 반환은 아래
  [5번 연결](#원-create-기한과-실패-자산-수습-연결)을 따른다. retire/purge는 여전히
  미게시 정리 기능이 아니다. 원 기록 삭제·phase 변경·자동 재복사는 허용하지 않는다.
  같은 원 create는 원 실패/수습 결과만 회수하고 별도 신규 생성은 명시 새 요청으로 처리한다.
  HTTP·Spring 담당은 새 지원 버전의 권한·확인·조회/사용자 표시를 연결하되 파일 정리나
  새 UUID 우회를 대신 구현하지 않는다. 담당자 구현 전 제품 ‘정리 완료’ 표시는 미수락이다.
  수락은 종결/정리 중 중단·응답 유실에도 원 기록을 회수하고, 미종료 실행자·사용 중 자산·
  증거 불충분에서는 해제를 거절하며, 확인된 종결 뒤에만 후속 생성으로 진행하는 것이다.

## Linux 준비 중 조회·제어의 동시 연결

**D02·D08 후속 / 하위 3번 제공, FastAPI·Spring·화면 연결 및 설치 수락 전.**
지원 기준은 [3번 검증 후보](../verification/recovery-scenario-closure-20261030.md#장기-linux-준비와-공통-잠금-분리)의
모든 관련 모듈·실제 import 경로·recipe 포함 바이트가 일치하는 단일 설치본이다.
`fork()` 존재만으로 구형/혼합 설치본을 새 동시 호출 지원으로 판정하지 않는다.
현재 이 제품 코드는 플랫폼에서 대신 수정하지 않았다.

### FastAPI 담당

| 실제 위치 | 변경 요청·제공 근거 | 수락 assertion |
|---|---|---|
| `drivers/platform.py:SerializedManager.call/fork/load_manager`, `build_driver` | 기본 직렬화를 전부 삭제하지 않는다. 확인한 새 하위에서 Linux create와 get/list/asset operation 및 다른 VM stop을 요청별 fork 객체로 연결한다. 같은 inner를 여러 thread가 직접 공유하지 않는다. 하위의 동일 VM 충돌·실행 잠금·CPU/신원/의도 guard는 유지한다 | A의 실제 복사를 막아 둔 동안 B 조회와 실제 stop·완료 조회가 끝남. 상위 전역 mutex나 VM 공통 mutex가 하위의 새 진행성을 다시 막지 않음 |
| `drivers/platform.py:_BUDGET_ACTIONS/call_with_budget`·서비스/라우터의 요청 예산 | operation에 현재 남은 budget_ms를 전달하고 get/list timeout_ms도 같은 잔여값으로 제한. 만료 뒤 제출 금지. 3번 후보에는 create 기한이 없었으며, 5번 지원 버전의 유한 정책/호출 범위는 아래 절을 조건부 적용. timeout은 취소가 아님 | 원 작업 조회 만료·읽기 실패를 검색 부재로 바꾸지 않음. 조회 예산으로 원 실행 기한을 갱신하지 않음 |
| `PlatformProvisioner.ensure_instance/_registration/_paths`, `PlatformDriver.create`, `VmService.create` | 현재 ensure_instance는 create의 ok 응답 뒤 view/_registration에서 configuration.id를 찾는다. 활성 재전달의 data.operation/effective_status=RUNNING은 그 VMView가 아니다. 먼저 원 작업을 추적하고, SUCCEEDED의 등록 결과가 확인되기 전 `_paths`·start나 새 UUID 재생성을 하지 않는다. 선행 get의 직렬화도 함께 대조한다 | 같은 key 재전달을 잘못된 UUID/빈 경로 오류로 바꾸지 않음. 실제 등록 전 GET 부재를 중복 생성으로 연결하지 않음 |
| 원 자산 작업 조회의 API/DTO·`raise_for_envelope` | asset `operation`과 control-operation을 분리해 연결한다. 새 HTTP 경로·응답 DTO는 담당자가 기존 인증/VM 접근권한과 함께 확정한다. key 또는 operation ID로 얻은 원 record의 vm_id가 권한 대상과 일치하는지 확인한다. raw journal·workspace 경로·내부 소유 PID/FD·진단 원문을 그대로 외부에 반환하지 않는다 | 정상 검색 부재·읽기/소유권 관측 실패·기한 초과·중복 key를 구별. 다른 VM 원장을 볼 수 없음 |

하위 호출 예시는 다음과 같다. 첫 전송 전에 정한 동일 SPEC/UUID를 보존한다.

```python
create = manager.fork().call("create", SPEC, execute=True, request_id=CORRELATION_ID)
original = manager.fork().call(
    "operation", {"idempotency_key": "create:" + VM_ID}, budget_ms=REMAINING_MS)
current = manager.fork().call(
    "get", {"vm_id": OTHER_VM_ID, "timeout_ms": min(REMAINING_MS, 15000)},
    budget_ms=REMAINING_MS)
```

각 호출에는 그 시점에 다시 계산한 양수 잔여 ms를 사용한다. 위 예시의 동일 변수 표기는
연속 호출마다 전체 예산을 다시 지급한다는 뜻이 아니다. create의 외부 응답을 잃으면
새 key로 우회하지 않고 asset operation으로 원 작업을 확인한다. 준비를 실행하는 호출자는
동기 호출의 수명과 원 결과 회수 책임을 유지한다. 새 자동 이행기나 재시도 queue는 제공하지 않는다.

### Spring·화면 담당

`Dom0ManagerClient.launch`·`VmService.create`와 VM 작업 추적/DTO, 프론트 VM 생성·목록·
상세 action query에서 다음 의미를 보존한다. 정확한 필드 원문은
[자산 원장 계약](../platform/cli-manager-spec.md#operation-fields)에 둔다.

- `effective_status=RUNNING`은 원 Linux 준비의 활성 실행 관측이다. VM RUNNING·OS/Agent
  준비·생성 완료가 아니다. 아직 등록되지 않은 VM을 실제 실행으로 표시하거나 start하지 않는다.
- 신규 슬롯 경합의 `error.details.accepted=false`·operation_id=null은 확인된 미수락이다.
  일반 OPERATION_IN_PROGRESS·503·timeout에는 이 해석을 적용하지 않는다. 이미 수락된 원
  작업을 소급 취소하거나 모든 경합을 202/접수 완료로 변환하지 않는다.
- 원 기록의 RECOVERY_REQUIRED, 원장/소유권 조회 오류, 정상 검색 부재는 다르다.
  원 key·spec·UUID를 보존하고 읽기 예산으로 조회한다. 현재 표시값을 권한/실행 허가로 쓰지 않는다.
- SUCCEEDED 이후 현재 VM 관측만 실패한 재전달은 원 성공과 current=null/diagnostic_warnings를
  유지한다. 부분 실패의 정리 완료나 새 생성 허용은 위 5번 수습 기능 제공 전 표시하지 않는다.
- 기존 stop의 accepted/pending·원 물리 deadline·후속 이행 주체는 그대로 유지한다.
  하위의 Linux 준비 분리가 긴 Recovery Engine 작업 전체를 병렬화한 것은 아니다.

### 공동 제품 수락

S3-T01과 같은 순서를 **실제 FastAPI 직접 경로**, 이어서 **EC2/Spring·화면 경로**에서
각각 수행한다. Debian·Ubuntu A의 복사 진입을 통제한 상태에서 B 목록/상세·원 create 조회·
B key stop·실제 Xen 종료와 완료 조회가 A 해제 전에 끝나야 한다. 그 뒤 A를 해제하여
같은 원 operation·예약으로 생성 완료를 확인한다. 상위 handler를 mock으로 바꾸거나
먼저 A를 끝낸 결과는 제품 수락이 아니다. 동일 key 동시 요청·신규 슬롯 미수락·응답 유실·
관측 실패와 상태 역전도 포함한다. 보드 배포·임시 barrier 적용은 별도 승인을 받는다.

하위 로컬 통과와 위 제품 수락은 분리한다. 기존 기본 VM·CPU·네트워크·Tailscale을 보존하며,
서비스 재시작의 backend cgroup 문제는 아래의 독립 미완료 항목으로 유지한다.

## 반복 조회와 제어 진행성 연결

**하위 4번 제공 후보 / 제품 HTTP·Spring·화면 수락 전.** 기존 D02·D08·3번 요청 ID와
미완료 상태를 유지한다. 지원 근거는 [정식 조회 계약](../platform/cli-manager-spec.md#read-concurrency)과
[현재 검증 보고서](../verification/recovery-scenario-closure-20261030.md#반복-조회의-비용일관성과-제어-진행성)다.
단일 후보의 Manager/Execution/Platform/runtime/요청별 출력 모듈과 실제 import 경로를 맞춘 뒤
아래 네 일반 조회만 새 읽기 경로로 연결한다. `fork()`만 있는 구형/혼합 설치본은 기존 보호를 유지한다.

| 담당·실제 수정 위치 | 입력·출력·거절 조건과 권고 연결 | 수락 assertion |
|---|---|---|
| FastAPI `drivers/platform.py:SerializedManager.call/fork`, `PlatformDriver._views/list_domains`, `drivers/base.py:find` | list/get/asset operation/control-operation을 요청별 inner.fork 객체로 호출. 공유 inner 직접 동시 호출 금지. 긴 제어의 직렬화 전부 삭제 금지. stop 앞의 불필요한 전체 find/list와 전역 mutex 대기를 분리 | 느린 list/get 미해제 중 다른 VM stop 및 원 결과 조회 진입·완료. 같은 요청 객체를 잘못 공유하면 OPERATION_IN_PROGRESS이며 원 진행 요청의 예산/trace가 섞이지 않음 |
| FastAPI `api/vms.py`, `service.py`, request_budget·실행 threadpool | 네 외부 관측 슬롯은 하위 보호다. 상위의 동기 handler/threadpool도 유한하게 두고 느린 조회가 stop/원 조회 진입을 모두 점유하지 않게 실행량/대기를 분리. 기다린 만큼 잔여 예산을 줄이고 만료 뒤 제출 금지. 새 queue/daemon을 추가하지 않는 연결을 우선 | 하위 대신 mock 응답으로 통과시키지 않음. 실제 handler에서 정상 읽기와 제어 모두 진행. 초과 부하의 거절/예산 소진과 수락 후 pending을 구분 |
| FastAPI `_BUDGET_ACTIONS/call_with_budget`, `raise_for_envelope`·오류 응답 | asset operation의 하위 예산 지원을 포함. get/list/control-operation timeout_ms와 현재 budget_ms를 제한. TARGET_CHANGED·OBSERVATION_UNAVAILABLE·원장 오류·관측 슬롯 OPERATION_IN_PROGRESS를 빈 목록/404/STOPPED/accepted로 바꾸지 않음 | 정상 검색 부재·불완전 열람·예산/소유권 오류를 각각 보존. 원 key/기한 변경·원 journal 직접 쓰기 0 |
| 기존 `observations.py:verified_platform_batch/Recorder.refresh/Reader` | 이미 있는 선택형 파생 관측은 검증된 한 list의 시각/신원만 소비. VM별 get/snapshot 재호출을 덧붙이지 않음. 기존 실패/만료/foreign-host 정책 유지. 표시 snapshot을 control guard로 사용하지 않음 | 일부 항목 실패를 완전한 정상 목록으로 게시하지 않음. 이전 표본에 현재 시각을 붙이거나 다른 실행으로 바꾸지 않음 |
| Spring `VmStateWatcher.check`, `VmService.list/stop`, `Dom0ManagerClient` | 기존 상태 감시 fixedDelay 기본 5초와 사용자 조회의 실제 중첩을 계측. stop 선행 전체 조회를 제거할 때 VM 접근권한 검사는 별도로 유지. 동일 대상의 겹친 조회는 완료/취소를 추적하여 누적하지 않음. 일괄 polling/timeout 확대는 하지 않음 | 원 stop key·deadline·완료 추적 보존. 옛 조회 응답이 새 의도/원 결과를 덮지 않으며 연결 실패를 전체 VM 정지로 기록하지 않음 |
| 프론트 `api/queries.ts:useVms/useVm` 및 생성/정지 작업 화면 | 현재 목록 기본 5초·화면 override 1초, 상세 5초와 사용자 중복 화면을 구분. AbortSignal·동일 query key를 사용해 취소/최신 응답 순서를 연결. 로딩/관측 오류와 VM 부재, pending과 현재 실행을 별도로 표시 | 요청 단절/슬롯 거절이 삭제/빈 목록으로 보이지 않음. 늦은 목록·상세가 더 최신 사용자 stop/완료 상태를 덮지 않음 |

우리 쪽 제공물은 `test_xenova_read_progress.py`의 S4-T02(느린 조회)와 S4-T03(양 OS·
폴러 1/2/4개), 오류/재관측·현재 실행 guard·프로세스/FD 회수 및 기존 2·3번 회귀다.
10ms의 로컬 시험 polling은 운영 설정/QPS가 아니며 제품 기본값으로 설치하지 않는다.
네 관측 슬롯 상한은 초기 검증 구성이다. shared Xen/Store 자체의 무응답에서는 제어도
실패할 수 있으므로 모든 고장을 이 잠금 개선으로 수락했다고 하지 않는다.

인수 호출은 같은 Manager 인스턴스를 공유하지 않고 각각 `manager.fork().call(...)`을 사용한다.
list/get에 `budget_ms=remaining_ms`와 필요한 timeout_ms를, asset/control 원 조회에도 그 순간
남은 예산을 준다. query→stop 순서에서 query 결과를 guard 인자로 넘기지 않는다. stop은
원 key로 요청하고 control-operation으로 원 phase/result를 회수한다. 잔여 예산이 0이면 제출하지 않는다.
새 HTTP endpoint·공개 DTO/필드는 담당자가 기존 권한 검사와 확정하며 raw journal·private
경로·PID/FD·credential·진단 원문을 외부로 전달하지 않는다.

공동 수락은 같은 프로세스의 실제 FastAPI 직접 경로에서 먼저 수행하고, EC2/Spring/화면을
이어 확인한다. 느린 조회를 해제하기 전에 다른 VM stop과 원 조회 완료, 이어 두 OS 복사 및
실제 polling 유지 중 정상 조회 성공·B 실제 종료·원 완료가 필요하다. 종료/초기화와 부하 해소 후
읽기 회복도 확인한다. 하위 로컬 대역 통과는 이 제품/설치/보드 수락을 대신하지 않는다.

다음 음성 시험은 기존 요청의 제품 수락 조건이며 하위 통과로 완료 처리하지 않는다.

| 실제 경로 | 음성 assertion |
|---|---|
| `VmService.stop` 선행 조회 개정·FastAPI 권한 연결 | 불필요한 전체 목록 조회만 제거한다. 권한 없는 다른 VM의 stop은 기존 접근 정책대로 거절하며 의도 수락/실제 shutdown 0. 표시 snapshot으로 권한 검사를 대체하지 않음 |
| 원 key/operation 조회 handler·`Dom0ManagerClient` 결과 반환 | ID를 안다는 사실은 권한이 아니다. 원 기록의 VM과 요청자의 허용 대상을 대조한다. 다른 VM의 key/ID·불일치 vm_id 요청에서 원 spec/result·진행 참조가 노출되지 않음 |
| `raise_for_envelope`·Spring 오류 변환·화면 표시 | raw journal·workspace·PID/FD·credential·진단 문자열을 포함한 하위 오류를 주입. 기존 허용 코드/작업 참조만 응답하며 본문/상위 로그/화면에 비밀·내부 경로가 노출되지 않음 |
| handler/threadpool의 느린 관측 포화 | 느린 조회가 입장한 채로 허용 stop·원 조회가 진입/완료하고 정상 읽기도 성공. 전부 거절하거나 polling을 종료한 뒤 성공시키는 결과는 수락하지 않음 |

우리 쪽 슬롯 후속 시험은 실제 부모 중단/자식 FD/정상 반환과 비정상 경로·파일 거절을 제공한다.
다른 uid는 로컬 fstat 경계 대역으로 확인했으며 실제 HTTP 인증/권한·운영 파일 보안 감사는 아니다.
4번 당시 별도였던 create 기한/실패 종결은 아래 5번 제공 범위를 따른다.
실제 상위 연결·운영 시간값·설치/보드와 API 재시작 backend 귀속은 계속 별도 미완료다.

## 원 create 기한과 실패 자산 수습 연결

**5번 하위 제공 / FastAPI·Spring·화면 제품 구현과 종단 수락 대기.** 기존 요청 ID·
D02/D08의 원 key/응답 유실·pending/uncertain 후속 책임은 유지한다.
읽는 순서는 [원 생성/수습 명세](../platform/cli-manager-spec.md#cmd-operation-reconcile),
[관리 경계](../platform/management-boundaries.md#원-생성-실패와-예약-반환), 기존 원 key 절이다.

| 담당·실제 위치 | 권고 연결·하위 제공 | 수락 assertion |
|---|---|---|
| 플랫폼 설치 담당·FastAPI `drivers/platform.py`의 `SerializedManager/_BUDGET_ACTIONS/call_with_budget/PlatformDriver.create` | 동일 후보의 새 helper/관련 모듈·정식 import를 설치. 운영 승인된 `/etc/xenova/creation-policy.json` 공급 확인. create/operation-reconcile의 잔여 budget 전달, 요청별 fork 사용 | 예시 null 설정이나 정책 부재는 새 Linux create 수락 전 거절. 기존 완료 조회/수습을 정책 부재로 막지 않음. schema 1.1을 모르는 writer/혼합 모듈 운용 0 |
| FastAPI 요청 모델·`api/vms.py`·`service.py`, Spring `VmService.create/Dom0ManagerClient.launch` | VM spec과 호출 예산을 분리. 제출 전 UUID/spec·정규 create:UUID key·사용자 소유를 보존. create 등록과 이후 start는 별도 원 요청으로 추적 | timeout/연결 단절/GET 부재에서 새 UUID·복사 제출 0. 늦은 create 결과로 최신 stop 이후 자동 launch 0 |
| 원 작업 조회/수습의 HTTP 어댑터·Spring 작업 추적 | asset operation의 SUCCEEDED/RUNNING/RECONCILING/FAILED/RECOVERY_REQUIRED와 VM 실행 상태를 구별. 하위 진단과 명시 적용은 기존 조회의 자동 후속이 아닌 별도 인증/확인 경로 | FAILED+수습 완료를 create 성공/VM 실행/정지 완료로 표시하지 않음. 일반 polling이 수습·삭제를 시작하지 않음 |
| 미게시 대상의 접근권한·명시 확인·수습 요청 저장 | 일반 get이 없을 수 있으므로 원 생성 요청 소유/정책 주체로 권한 확인. 원 operation·VM·spec hash·목적·진단 context·안정 reconciliation_id를 저장한 뒤 명시 execute | ID를 아는 다른 사용자의 진단/조회/폐기는 기존 권한 정책대로 거절. 진단 뒤 inode/문맥 교체는 거절. 같은 수습 진행 revision을 새 요청으로 오인하지 않음 |
| 프론트 VM 생성/작업 상세·원 결과 재확인 | 원 생성 실패, 수습 진단 가능/진행/기록 완료, 예약 소비자의 내구 확인 및 새 생성 가능성을 구별. 계획 예상 항목 수·대상 확인 후 명시 폐기 | 같은 create key는 원 실패+수습 결과만 회수. 정리 응답 유실 뒤 같은 수습 ID 회수. 새 정상 생성은 별도 사용자 요청·UUID/current 자원 검사로만 시작 |

하위 사용 예시는 다음과 같다. `REMAINING_MS`는 각 호출 직전 다시 계산한 실제 잔여
예산이며 이 예시가 새 운영 시간값을 정하지 않는다. 원 VM 명세에 timeout을 추가하지 않는다.

```python
created = manager.fork().call("create", original_vm_spec, execute=True,
                              budget_ms=REMAINING_MS)
original = manager.fork().call("operation", {"idempotency_key": "create:" + VM_ID},
                               budget_ms=REMAINING_MS)
diagnostic_spec = {"operation_id": OP_ID, "vm_id": VM_ID,
                   "expected_spec_sha256": SPEC_HASH, "purpose": "discard-private"}
diagnostic = manager.fork().call("operation-reconcile", diagnostic_spec,
                                 budget_ms=REMAINING_MS)
# 인증·대상 확인·사용자의 명시 폐기를 받은 뒤 아래 요청을 안정적으로 보존한다.
discard_spec = {**diagnostic_spec, "expected_context_sha256": CONTEXT_FROM_DIAGNOSTIC,
                "reconciliation_id": SAVED_RECONCILIATION_UUID}
discarded = manager.fork().call("operation-reconcile", discard_spec, execute=True,
                                budget_ms=REMAINING_MS)
```

create의 원 기한은 최초 수락에서 host와 함께 저장되며 재전달/조회로 연장하지 않는다.
수습은 현재 호출의 별도 기한으로 실행하며 원 생성의 기한/신원을 고치지 않는다. 구버전
기한 불명·다른 host의 실패도 현재 사용/소유 확인 뒤 명시 폐기할 수 있다. 수습의 기본
timeout 15초와 상한 105000ms는 생성 운영 한도나 HTTP 대기시간이 아니다.

다음은 **하위 값의 외부 반환 허용안**이며 새 HTTP DTO를 이미 구현한 목록이 아니다.
기존 권한과 API 계약에 맞춰 담당자가 확정한다.

| 하위 원천 | 허용할 최소 의미 | 외부 전달 금지 |
|---|---|---|
| envelope/operation 관측 | 권한 확인된 operation_id·vm_id·effective_status, 기존 오류 code/원 작업 참조 | raw operation/request/template/할당·spec 원문 전체 |
| 진단 | purpose·context_sha256·spec_sha256·private_items/private_absent·명시 수습 필요 여부 | filesystem 경로·dev/inode·PID/FD·전체 진단 자료 |
| reconciliation | 권한 확인된 request.reconciliation_id·purpose·phase·남은 개수, 원 생성 실패와 기록된 release 의미 | plan/items/remaining 경로 목록, credential·최초 오류/cleanup 진단 문자열 |
| 시간 | 필요하고 권한이 확인된 원 host/기한과 이번 조회의 만료 판정 | 이를 자동 실행 승인·재시작 신호로 사용하는 동작 |

공동 인수는 Debian·Ubuntu 각각 실패/만료→진단→명시 수습→수습 중 단절→같은 수습
재확인→원 create 실패 replay→별도 새 생성으로 수행한다. 수습을 막은 상태에서도 다른
VM 정상 조회·실제 stop·원 제어 결과가 완료되어야 한다. 실제 HTTP threadpool·Spring·
화면으로 수행하며 하위 Python fixture 통과로 대체하지 않는다.
fsync/저장 오류에서 raw complete JSON을 보았다는 이유만으로 성공/예약 반환을 표시하지
않고 원 수습 결과를 재확인한다. 상위가 journal/lock 파일 삭제·phase 변경·자동 force로
대신 수습하는 fallback은 금지한다. API cgroup/backend 소유는 이 작업의 해결 범위가 아니다.

## 설치본 API 재시작과 VM backend 소유

기본 관리 시험에서 `xenova-dom0-manager.service`의 cgroup에 기본 Debian의 qemu
(`-xen-domid 9 -name debian-dev`)와 xl create monitor가 함께 남은 것을 확인했다.
unit의 KillMode는 control-group이었다. [실보드 기록](../verification/recovery-scenario-closure-20261030.md#검증-결합-복사의-승인-후-실보드-측정)에
근거와 승인된 정상 정지·복원 절차를 둔다. API가 active라는 사실이나 Xen domain만
남았다는 사실로 guest 저장소 backend 보존까지 수락하지 않는다.

기존 부팅/종료 계약은 `xenova-vm-<VM UUID>-<execution UUID>.scope`에 지속 프로세스를
귀속하고 실패 시 직접 xl로 우회하지 않는 것이다. 하위 runtime 배포 담당자는 실제
`xenova_vm.Platform`·`xenova_execution`·`xenova_guest_process`의 설치 버전과 호출 연결을
대조한다. HTTP 담당자는 `PlatformDriver.create`→하위 start의 직접 객체 호출에서도
같은 실행 소유 경계가 적용되는지 확인한다. 이번 I/O 후보 세 파일 교체는 이 설치본의
프로세스 소유권까지 갱신한 작업이 아니다. 현재 코드에 이미 있는 scope를 중복 구현하지 않는다.

권고 수락은 CLI/FastAPI의 새 Linux 실행 각각에서 monitor/backend PID·cgroup·실행
UUID를 대조하고, API 정상 재시작 때 기존 guest의 디스크 I/O·네트워크·실행이 유지되는지
격리 시험→승인된 실보드 순으로 확인하는 것이다. 실패 batch·개별 VM stop·host 종료의
정리 책임도 기존 부팅 계약과 연결한다. KillMode=process/none, 고아 프로세스 방치,
살아 있는 기본 VM backend의 무단 강제 이동으로 우회하지 않는다. 원 execution·의도·
CPU 정책은 보존한다. 이 항목은 미완료이며 D02·D08의 하위 수락을 다시 실패로 닫지 않는다.

## 5.5번 로컬 비용 보완의 소비 범위

하위 신규 create는 같은 공통 잠금 구간의 검증된 원장/반환 판정을 주소·용량 판정에
전달한다. 슬롯 대기/private 준비 뒤에는 다시 확인한다. 새 공개 필드·key·상태·기한이나
상위 cache 허가는 추가하지 않았다. 이 내부 사본을 HTTP 요청 간에 보관하거나 화면 조회로
제출 직전 guard를 대신하지 않는다. [5.5번 근거](../verification/recovery-scenario-closure-20261030.md#stage55)를 참조한다.

추가 반례로 확인한 중복 key 원장은 공통 create/retire/restore/purge 재전달에서도 기존
`RECONCILE_REQUIRED`로 거절한다. 이전처럼 첫 기록을 골라 성공 replay로 취급하지 않는다.
새 key 생성/원장 삭제로 우회하지 않고 원 기록의 모호성을 조사해야 한다. 새 응답 필드는 없다.

restore의 중복 fsck는 같은 파일/같은 잠금 구간에서 줄였지만 긴 lifecycle 작업의 공통
잠금은 남아 있다. barrier 중 다른 VM의 get/원 조회와 stop 의도 수락은 진행돼도 실제
종료는 기다릴 수 있다. 기존 D02/D08의 accepted/pending·원 key/기한·후속 이행 책임은
그대로다. 모든 busy를 accepted/완료로 바꾸거나 상위 timeout만 늘려 숨기지 않는다.
하위의 장기 lifecycle 소유권/잠금 분리 미완료는 플랫폼이 따로 해결해야 할 범위다.
이번 요청 갱신은 실제 FastAPI/Spring/화면 구현·설치 수락이 아니다.

## 진행 기록

- 2026-10-31 5.5 후속: 위 긴 restore 대기는 과거 P55의 반례다. 하위 후보는 대상 lease 아래
  긴 검사를 분리해 양 Linux restore/purge barrier 중 다른 VM의 실제 stop·원 결과 저장을
  확인했다. 같은 원 lifecycle의 활성 재전달은 원 operation과 effective_status=RUNNING을
  반환한다. 미수락 검사 경합과 구분하고 조회를 재실행기로 만들지 않는다.
- CPU 정책은 OS 공통 정적 pinning/해제로 확장했다. 정식 spec의 새 placements 및 policy/journal
  2.0·CPU 조회 policy_schema_version/버전별 reservations를 보존해야 한다. 기존 입력 exclusive도
  지원하나 원 key의 입력 형식은 유지한다. live repin·scheduler/pool 변경과 자동 정지는 없다.
  실제 FastAPI allowlist/Driver DTO·Spring/UI의 연결 위치와 기존 D02/D08 인수는 담당자 범위다.
  [후속 계약·시험](../verification/recovery-scenario-closure-20261030.md#stage55-followup)을 따른다.
  API cgroup/backend·운영 create 정책·동일 설치본의 HTTP 수락은 미완료로 유지한다.

- 2026-10-29: 현재 HTTP 소스의 key stop/조회 제공을 확인했습니다. Spring의 `stop`은 void 호출이며 종단 연결은 별도 과제입니다. 운영 API·DB·env·서비스를 변경하지 않았습니다.
