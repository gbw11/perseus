# HTTP·Spring·화면 종단 연결 요청

대상: FastAPI·Spring·프론트 담당자. **상태: 최신 하위/HTTP 제공 경로 대조 / Spring·화면 종단·새 입력 계약 수락 전.** 이번 최소 연결에서 이 제품 코드는 수정하지 않습니다. D02·D08과 원 key·deadline·후속 이행 책임을 유지합니다.

## 현재 할 일과 읽는 순서

1. [기존 관리 연동 요청](../platform/management-integration-request.md)의 실제 하위 호출·허용 details·원 key 검색 결과와 Spring 요청을 읽습니다.
2. 최신 `platform/services/dom0-manager/dom0_manager/api/vms.py`, `service.py`, `drivers/platform.py`를 대조합니다. 현재 소스에는 key 있는 stop의 200/202와 원 key 조회 경로가 있으므로 과거 문서의 HTTP 미구현 설명을 그대로 적용하지 않습니다. 배포·종단 수락은 별도입니다.
3. [관리 수명주기](../platform/management-boundaries.md), [복구 시나리오](../platform/recovery-scenarios.md)의 F1·F2·F4·FO1, 주행 설계 11~12·20절을 읽습니다.

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

## 진행 기록

- 2026-10-29: 현재 HTTP 소스의 key stop/조회 제공을 확인했습니다. Spring의 `stop`은 void 호출이며 종단 연결은 별도 과제입니다. 운영 API·DB·env·서비스를 변경하지 않았습니다.
