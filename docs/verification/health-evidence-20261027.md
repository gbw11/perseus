# D01 건강 근거 연결과 D02·D08 하위 인수 보완

> 후속 위치 안내: 아래는 당시 후보·검증 결과를 보존한 보고다. 현재 담당자 요청은
> [Linux·Agent](../requests/2026-10-27-linux-health-agent.md),
> [Worker](../requests/2026-10-27-recovery-health-ctrl.md),
> [HTTP·Spring·화면](../platform/management-integration-request.md)으로 분리했다.
> D03 하위 수습과 별도 담당자 인수는 [후속 보고](reconciliation-closure-20261027.md)를 따른다.
> D01 담당자 구현 및 D02·D08 상위 미연결을 완료로 닫지 않는다.

## 판정·기준선

**플랫폼·Zephyr의 확정 가능한 건강 근거/준비 연결을 구현하고, 담당자별 요청을 누적했다.**
Worker의 기존 D01 전달 분류 수정과 Linux·제품 종단·설치본/실보드 수락은 남아 있다.
요청서 작성이나 native 통과를 그 담당자의 구현·실제 Xen 수락으로 보고하지 않는다.

기준은 `refactor/platform/recovery-contract-closure`의 HEAD
`bc89d2ab0e91505bcc040696873fedc1b618000e` + 현재 diff·새 파일이다. 착수 시 Windows·WSL은
같은 clean HEAD였고 D02·D08의 봉인한 8개 파일을 대조했다. 원격 읽기 대조는 platform
`3c7e844e8bad9e873da5c20c8100a3d5da80e642`, develop `3de2deeec6cb78a28b8026a5e415329cb3c29509`다.
과거 커밋 전환·브랜치 병합은 하지 않았다.

증거는 PC `C:/Users/SSAFY/Documents/Codex/2026-10-27/xenova-health-d01/`,
WSL `/home/ssafy/xenova-health-d01-20261027/`에 있다. `SOURCE_MANIFEST.json/candidate.patch`와
ARM receipt·`ARTIFACTS.json/FINAL_VERIFICATION.json`으로 HEAD 외의 후보를 식별한다.

## ① 플랫폼·Zephyr 실제 변경

| 위치·책임 | 구현·보존 |
|---|---|
| Diagnostics `health.h/health.c` | CPU·memory/thread 관측 경고와 필수 작업 실패·기한/시각 검증을 독립적으로 판정. no allocation/I/O/lock, 재시작·출력 권한 없음 |
| `resources.c`, `resources.h`, `diagnostics.h` | 기존 200ms collector에 실제 kernel heap cache 연결. `xdiag_resources_try_get`는 K_NO_WAIT이며 기존 20ms getter·단일 CPU baseline·thread lifetime·잘림 의미 유지 |
| drive `controller.c/h`, `main.c` | 실제 auto/manual 계산을 같은 기존 release deadline으로 확인. 모드별 실제 실패와 serial 전체 step의 miss를 준비 제한·0 후보에 연결. 비선택 AUTO의 단독 실패는 정상 MANUAL을 막지 않음 |
| 공통 runtime `runtime.c`, `drive_runtime.h` | 원 deadline/miss 조회, 제한된 최신 평가 복사와 `xenova drive health`. 원 완료 순번·시각을 보존하며 query/worker가 새 진행을 만들지 않음 |
| C/native 시험 | 실제 classifier·controller·두 기존 알고리즘, 실제 Zephyr heap/mutex·thread/collector. 시간/자원 창 합성은 경계 fixture로 구분 |
| 하위 Manager/Execution | D02·D08의 작은 추가 보완. 아래 별도 절 참조 |

F01의 창/송신 최종 확정·보고 공정성, TCP_NODELAY, 계산/통신 소유 분리, 20ms 고정 origin·
skip 정책, 원 입력 시각·in-flight 분리, join·공통 종료 deadline·netfront/Store/grant 의미는 유지한다.
주행 알고리즘·Gateway·공용 MQTT 제품 코드·v2 공개 지표·ACL은 변경하지 않았다.

처음 native heap 시험은 새 heap 필드를 CPU 수집 결과 대입이 덮는 결함으로 실패했다.
heap 수집을 그 대입 뒤로 옮기고 같은 assertion을 통과했다(`diag-run.log` 실패 보존,
`diag-run-2.log` 통과). free>0 assertion을 제거해 통과시키지 않았다.

## ② 공통 건강 계약·조건별 대응

공통 설계 원칙·원천/단위·유효성·판정·영향·허용 행동·회복 조건의 원문은
[건강 판정 계약](../contracts/health-evidence-v1.md)이다. MQTT v1 사유 목록은 유지하며
필수 계산 자원/기한 실패의 `CALCULATION_FAILED` 해석만 명확히 했다. 자세한 원 errno와
경고는 local 평가에 남기며 v2 전체 수신을 준비의 필수조건으로 삼지 않는다.

| 조건·보장 | 실행한 근거·assertion | 한계 |
|---|---|---|
| high CPU+on-time | `test_health.c`: 95% 정상, 100% idle 미관측 경고·restrict=false. 실제 controller의 유효 MANUAL 양수 명령 유지 | CPU 창은 합성 정책 fixture. 실제 pCPU 포화 실험 아님 |
| 실제 heap 실패 / 진단 실패 | native `test_health_uses_actual_heap_and_nonwaiting_cache`: 실제 k_malloc 실패, required 작업만 RESOURCE_FAILED·제한 | 현재 stateless 주행 계산 자체에 새 allocation을 넣지 않음 |
| 현재 자원 경고의 기능 범위 | controller의 AUTO -ENOMEM fault injection·정상 MANUAL READY/양수 후보; heap free=0 경고만으로 막지 않음 | fault는 어댑터 경계 주입이며 실제 앱 OOM 실보드 수락 아님 |
| 필수 task의 deadline | core의 end==deadline 정상/end>deadline 제한, CPU=0으로도 위반 상쇄 없음. 실제 controller 경로의 serial 지연·fresh 완료 회복 | 시각 입력은 controlled fixture. 같은 main이 영원히 막히면 스스로 새 판단을 내릴 수 없음 |
| 일 없음·미시작·종료 | core IDLE/NOT_STARTED/STOPPING에 지난 기한을 적용하지 않음 | thread 이름/낮은 CPU로 deadlock 판정하지 않음 |
| 보조 관측 경합·유실 | 실제 kernel mutex를 다른 thread가 보유할 때 try_get=-EBUSY, 20ms 대기 이전 반환. 정상 완료는 제한 안 함 | 실제 Xen의 collector/IRQ 지연 수락과 다름 |
| CPU·thread·pool 원천 | 기존 native runtime source/lifetime 재사용, registry64/반환32의 잘림, 실제 RX pool allocate/release·network error 시험 통과 | stack 스캔을 계산/주기 CPU 경로에 추가하지 않음 |
| local 출력 | `LOCAL_HEALTH.json`: 실제 공통 main 종료에서 같은 셸 handler의 AUTO/MANUAL 출력, 원 completed/step_end·stopping, 무효 CPU의 null 검사 | local 최신 RAM 평가이며 MQTT·Worker 수신 기능 아님 |
| 주행·복구 분리 | 같은 native binary의 두 실행, 두 방향 시나리오와 원 메시지→시리얼 제출 대응 검증 | 실제 Xen·차량 장치 적용·pinning 성능 결과 아님 |

기존 D01 전달 반례는 `delivery-before.json`에 별도로 보존했다. 대상 신호가 한 번도 없고 다른
VM만 수신했을 때 Linux는 HEARTBEAT_LOST, legacy Zephyr는 APP_HEARTBEAT_LOST를 만들고,
verified 현재 binding은 OBSERVATION_UNAVAILABLE·judgment 0을 유지했다. 이것은 **Worker
담당자의 남은 수정 근거**이며 이번 새 건강 확장을 과거 감사의 결함으로 확대하지 않는다.

## 로컬 회귀·ARM·부분 패키징

| 실행 | 결과·원자료 |
|---|---|
| 고정 CMake/Ninja `platform/apps/drive/tests` + CTest | sanitizer 적용 host classifier/controller 2개 통과, `host-test-2.log` |
| 고정 Zephyr native_sim Diagnostics | 8개 통과, `diag-run-2.log`; 실제 heap·잠금/CPU/thread/pool 검증 |
| CPU·pool 계측 off 선택 구성 | 5개 통과·skip0, `diag-off-run.log`. 실제 heap 실패·진단의 기능 영향 분리와 nonwaiting cache 확인 |
| native I/O owner | 27개 통과. readiness/HB expiry·공정성·context/reconnect·in-flight/교체·start/stop/join 실패. `io-*.json/log` |
| native drive lifecycle | 9개 통과. 초기화 단계·network/publisher/loop 실패·종료 경합·cleanup 오류·공유 deadline. `lifecycle-*.json/log` |
| 실제 native 공통 앱 + 격리 broker/Gateway | A 시작·B 시작 각각 통과, `pair-a/result.json`, `pair-b/result.json`. 기본 owner 자동 원복 금지·양방향 모드·재합류/재승계·과거 실행/재개 거절 |
| `unittest discover -s platform/tests -p 'test_*.py'` | 929개, 실패0·skip2, 328.568초. `platform.log/json` |
| 실제 dom0-manager pytest | 451개 통과, 기존 경고1. S01/S02·Engine·drive rejoin 포함. `manager.log/json` |
| Gateway pytest | 84개 통과, `gateway.log/json` |
| 공용 MQTT Python pytest | 126개 통과·4개 skip, `mqtt.log/json`. broker fixture 미설정 skip을 통과로 세지 않음. 격리 broker pair 시험은 별도 수행 |
| 고정 ARM 부분 빌드 | 최종 drive `arm-drive-3`, autonomous, manual, Network Starter 각각 통과. ELF/DTS·1 vCPU/16MiB·GIC·netfront send direct frame 검증은 기존 도구 사용 |
| 하위 `xenova-platform` | package_qa·package_write_rpm 통과. 두 모듈의 source/staging/RPM 바이트 일치. `ARTIFACTS.json` |
| 문서·후보 보존 | 링크/anchor 257개·diff 검사 통과. 31개 수정/새 파일의 Windows·WSL 일치, 서비스/상위/게스트 Linux/공용 MQTT 제품 소스 불변. `DOC_LINKS.json/SOURCE_MANIFEST.json` |

플랫폼 skip2는 고정 Xen console source 미지정 시험과 root 전용 Wi-Fi consumer 시험이다.
새 건강 변경을 그 경로의 별도 수락으로 확대하지 않는다. 각 명령·argv·환경의 선택값·시간과
실제 개수는 증거 JSON/log로 확인한다. 전체 platform 수가 native·broker·별도 suite를 대신하지 않는다.

drive의 같은 고정 입력 baseline ARM과 비교한 linker RAM 표시는 3476→3484KiB(+8KiB)다.
반영 코드·정렬·예약을 포함한 image RAM 점유이며 heap 실사용·최악 지연·성능 개선 수치가 아니다.
baseline receipt의 소스가 착수 해시와 일치함을 확인했다. compiler·Zephyr commit·SDK와 firmware
sha256는 `ARM_ARTIFACTS.json`에 고정한다. 후보는 게시 카탈로그·기존 번들을 덮지 않았다.

<a id="d02-d08"></a>
## D02·D08 하위 마감 추가 확인

원 [하위 마감 보고서](management-budget-adapter-20261027.md)의 후보·결과는 유지한다.
상위 HTTP 미구현을 이번 하위 작업 실패로 다시 등록하거나 HTTP 제품 코드를 수정하지 않았다.

| 보강 | 수정 전 근거 → 최소 보완 → 최종 assertion |
|---|---|
| active fork의 request 상태 | 실행 중 response_deadline을 clone이 복사하는 시험 실패(`lower-2.log`). fork에서 이 요청 전용 속성만 제거. 원 95000ms/다른 700ms 동시 호출·각 trace ID 분리, 종료 뒤 속성 정리, 같은 data/runtime/runner·flock/guard 보존, stale create 0 |
| 완전한 key 조회 | Path.glob의 열람 오류 은폐를 피하도록 strict scandir와 lstat·전체 항목 검증. 부분 iterator EIO/permission failure/나열 뒤 소실은 오류. 첫 matching 항목만으로 unique, 일부 목록으로 absence를 확정하지 않음 |
| 상태 구별 | 진짜 부재 VM_NOT_FOUND, 예산 만료 DEADLINE_EXCEEDED, 열람 미확인 OBSERVATION_UNAVAILABLE, 손상/중복 key RECONCILE_REQUIRED. malformed key metadata를 무시하지 않음 |
| 기존 원 작업 보호 | 관련 17개 시험 통과(`lower-3.log`), 같은 key 원 ID/spec/hash/revision/deadline, completed replay vm=null/warning·추가 물리 호출0·host 변경 시 시계 비교 null 유지 |

`Manager.fork`는 일반 모든 action의 무제한 동시 실행 API가 아니다. 새 저장 원장·SQLite·색인·
journal 삭제는 없다. 두 하위 모듈 수정 때문에 부분 RPM을 다시 확인했고 기존 RPM 3개와
새 RPM을 증거 경로에 보존했다. FastAPI·Worker·Spring·프론트·Gateway·Agent 소스는 불변이다.

## ③ 누적 요청서와 담당자 변경점

- [management-integration-request.md](../platform/management-integration-request.md): 기존 D02·D08
  1~5절·미구현 상태 보존. Linux D01-L01~L05와 fork/call/get/list/key 조회·budget/original D·
  응답 해석·권한/허용 details·원 key 회수·조건부 이행 책임을 누적했다.
- [기존 ctrl 요청서](../requests/2026-10-20-manual-app-gateway-ctrl.md#d01-worker): 원 155·237·124와
  Gateway 소유 이력을 보존하고 D01-W01~W05를 추가했다. 실제 함수·입력 유효성·금지 행동·
  설정 근거·우리 제공물·권고안/영향·회복과 호출 수 assertion을 적었다.
- [platform 요청 279](../requests/2026-10-22-recovery-worker-platform.md): capability/unit의 현재
  제공과 아직 없는 intents action을 구분하고 원 요청을 전체 처리 완료로 닫지 않았다.
  요청 색인은 위 두 기준 문서로 연결한다. 별도 Linux 요청서는 확인되지 않아 새 중복 문서를 만들지 않았다.

## ④ 남은 수락·보존 범위

| 담당·단계 | 남은 것 |
|---|---|
| Linux/Agent | OS·서비스/앱 진행·관측기 건강·지원 로컬 복구·배포의 L01~L05 구현/시험 |
| Worker | 기존 per-VM 전달 분류 D01-W01, 자원/기능 원천 소비·행동 정책·회복/예산·설명 연결 |
| Gateway 담당 | 상세 사유 소비·실제 입력/출력 경로와 건강 제한의 제품 인수. 모드/owner/권한을 Worker로 이동하지 않음 |
| HTTP/Spring/화면 | 기존 D02·D08 원 key/수락/조회·기한/후속 이행 요청. 현재 미구현 상태 유지 |
| 설치·실보드·장치 | 실제 Xen CPU 계상·IRQ/지연·실제 pool/stack 부족·정체·netfront/Store·소비 연결, 장치 적용·회복 확인 |

이 작업은 실제 `/run`·`/srv`·credential·운영 상태/DB·서비스/VM·broker/ACL·CPU/네트워크를
변경하지 않았다. 실보드 접속·배포·host reboot·force·전체 image/SD·commit/push는 하지 않았다.
D03 수습·새 복구 시스템·범용 메시지 저장으로 자동 진행하지 않는다.
