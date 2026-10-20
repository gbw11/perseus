# Xenova 플랫폼 실행 의도와 일회성 복구 계약

FastAPI 담당 Recovery Worker는 기존 xenova_management.Manager.call을 사용한다.
플랫폼은 유효한 실행 의도를 저장하고 현재 조건을 다시 검사해 한 번의 실행을 제공한다.
장애 판정·횟수/대기 정책·앱 준비·차량 모드/권한은 호출자 담당이다.

## 상태와 저장

| 정보 | 원본과 의미 |
|---|---|
| vm_id | 기존 영구 UUID. Xen UUID·자산·MAC/IP를 재발급하지 않음 |
| lifecycle / generation | 기존 자산 수명주기·세대. 재시작 횟수가 아님 |
| desired_state / intent_revision | 플랫폼이 수락한 running/stopped 의도·개정 번호 |
| host_boot_id | /proc/sys/kernel/random/boot_id. 서비스 재시작과 host 재부팅을 구분 |
| execution_id | 플랫폼이 준비하고 실제 Xen incarnation에 결합한 실행 UUID |
| recovery_attempt_id | 호출자가 발급하는 복구 시도 ID. 같은 시도의 재전달에는 같은 값 |
| app_readiness | unknown. 재시작 성공은 앱 준비·예비 합류·운행 허가가 아님 |

영구 저장소는 검증된 /srv/xenova mount만 사용한다.

- control/<vm_id>.json: 의도 원본. 인스턴스 보관 스냅샷 밖에 둔다.
- control-requests/<operation_id>.json: 사용자 제어 수락·물리 실행 결과.
- recovery-attempts/<recovery_attempt_id>.json: 일회성 복구 journal.
- 기존 instances / operations / tombstones는 기존 의미·키를 유지한다.

부팅 중 문맥은 /run/xenova의 executions/, observations/, boot-admission/, admission/,
execution-events/에 둔다. 디렉터리 0700·JSON 0600을 기본으로 root 관리 프로세스가 사용한다.
execution-context는 Manager의 read-only 조회다. 실행 ID는 인증/제어권 토큰이 아니다.
Agent의 guest_boot_id/app_session_id는 별도이며 여기서 생성하거나 추정하지 않는다.
Agent/Zephyr에 실제로 전달하는 통신은 담당자가 연결한다. G1 시험 노드는 사용하지 않는다.

새 일반 create는 stopped/revision 1, 새 factory 등록은 running/revision 1로 초기화한다.
기존 완료 설치에서 의도가 없으면 unknown/null로 읽으며 실행 상태나 autostart로 추론하지 않는다.
최초 등록 재실행은 이미 있는 의도·사용자 정책을 덮지 않는다.

## 공통 제어

UUID Manager와 이름 CLI start/stop/reboot/destroy는 Platform.request_control을 공유한다.
호출자는 기존 operation_lock·mount 검증 경계를 유지한다. Manager가 이미 그 잠금을 잡으므로
백엔드가 다시 operation_lock을 잡거나 Platform primitive를 직접 조합하지 않는다.

유효한 start는 running, stop/destroy는 stopped와 새 revision을 기록한다.
이미 꺼진 유효한 stop도 기록한다. 미지원 Zephyr stop, 잘못된 자산·신원·잠금 경합은 수락 전에 거절한다.
수락 뒤 Xen 실패·timeout은 의도를 되돌리지 않고 제어 journal에 별도로 남긴다.
사용자 reboot는 기존 실행 대상에 한해 running 의도로 수락하며 내부 정리는 사용자 stop이 아니다.
복구 내부 stop/start·보드 종료·autostart 설정 변경은 사용자 의도를 바꾸지 않는다.

제어의 idempotency_key는 선택 사항이다. 같은 키·동일 요청의 재전달은 revision을 증가시키지 않는다.
다른 요청으로 같은 키를 쓰면 IDEMPOTENCY_CONFLICT다. 키가 없으면 새 유효 요청이다.
request_id는 기존 호출 추적용이다. retire/restore/purge의 키나 수명주기 의미는 바꾸지 않는다.

수락 저장 순서:

1. base revision·다음 revision을 포함한 prepared journal fsync.
2. accepted_operation_id와 새 revision을 의도 원본에 게시. 이것이 수락 commit point다.
3. accepted journal·현재 host admission을 게시하고 executing을 기록한 뒤 Xen을 호출한다.

중간 종료 시 원본의 accepted_operation_id와 journal을 대조한다. prepared만 있으면 아직 수락하지
않은 것이며, 원본 게시 후면 같은 revision으로 journal을 정리한다. 파일별 rename을 전체
트랜잭션으로 가정하지 않는다. 저장 실패 때 물리 실행을 강행하지 않는다.
executing에서 끊겼으면 실제 관측으로 확인 가능한 결과만 확정하며 불명확하면 RECONCILE_REQUIRED다.
보관·복원 후 generation이 달라진 의도는 보존하되 intent_applicable=false다.
명시적 새 start/stop이 현재 세대의 의도를 만든다. '보관=stopped/복원=running' 규칙은 없다.

## 실행 신원

고정 Xen 1c72306b1f0b4dc533954f678361044f8083dccc의 tools/include/xenmanage.h와
tools/libs/manage/core.c에 정의된 xenmanage_get_domain_info를 사용한다.
기존 rootfs의 libxenmanage.so.1/libxenmanage1과 python3-ctypes를 재사용한다.

xenova-domain-identity는 읽기 전용 별도 프로세스다. 상위 Xen 관측의 남은 제한시간을 적용한다.
64-bit xen_unique_id는 JSON 정밀도 손실을 피하려고 10진 문자열로 반환한다.
host·영구 UUID/name·domid·unique_id·lifecycle generation과 준비한 launch 문맥을 대조한다.
UUID와 domid가 같아도 unique_id가 바뀌면 이전 실행으로 취급하지 않는다.

launching을 먼저 기록하고 xl create 후 실제 도메인과 결합해 observed로 기록한다.
중복 start나 재조회는 execution_id를 새로 만들지 않는다.
외부 xl·게스트 내부 reboot·toolstack 재생성으로 바뀐 실행은 자동으로 채택하지 않는다.
기존 Linux on_reboot=restart/on_crash=preserve 설정은 유지한다.
지원하는 내부 잠금과 직전 재조회가 외부 root의 xl까지 원자적으로 통제하지는 못한다.

## 부팅 정책 승인 경계

/etc/xenova/execution-policy.json은 다음 기본값으로 설치한다.

    {"schema_version":"1.0","autostart_stopped":null,"manual_running":null,"force_restart_vm_ids":[]}

| autostart / 저장 의도 | 현재 동작 |
|---|---|
| true / running, 현재 lifecycle | 부팅 대상으로 수락, 기존 autostart 경로 사용 |
| false / stopped | 부팅·복구 대상으로 수락하지 않음 |
| true / stopped | 정책 미확정. 현행 autostart 실행은 유지하되 복구 admission은 보류 |
| false / running | 정책 미확정. 현행 skip은 유지하되 복구 admission은 보류 |
| 기존 unknown 또는 이전 lifecycle의 의도 | 현행 autostart 동작 유지, 복구 admission은 보류 |

두 미확정 항목은 운영자가 합의 후 root 설정에서 start 또는 keep-stopped를 선택할 수 있다.
기본값 null은 어느 선택도 승인하지 않는다. 승인된 start가 stopped를 덮는 경우에만
source=boot-policy의 새 running revision을 기록한다. keep-stopped는 저장된 사용자 의도를
정지로 바꾸는 기능이 아니라 해당 부팅의 admission 거절이다.
같은 host의 명시적 사용자 stop admission은 서비스 재시작·autostart 재호출로 덮지 않는다.
이전 host의 admission은 사용하지 않는다.

force_restart_vm_ids는 별도 관리자 승인 UUID 목록이다. 기본은 빈 목록이다.
board_shutdown_policy=force-allowed나 autostart=true는 복구 강제 종료 승인이 아니다.
실제 팀원 앱을 목록에 넣기 전 차량 권한 무효화·정지 조율과 앱의 강제 종료 안전성을 별도로 판단한다.

## Manager 호출

기존 schema_version/ok/request_id/operation_id/replayed/data/error envelope를 유지한다.
하위 도구 출력은 stderr이며 JSON CLI stdout은 JSON 하나다. 기존 console은 바이트 스트림이다.

| action | spec | 의미 |
|---|---|---|
| get / list | 기존 입력 | 기존 VM 객체에 desired_state·intent_revision·intent_applicable·host/실행/admission 추가 |
| probe | vm_id, timeout_ms: 1..15000 | 기한이 있는 직접 관측·runtime snapshot 갱신 |
| snapshot | vm_id, max_age_ms: 1..120000 | operation_lock·registry·xl 없이 최근 관측 소비 |
| execution-context | vm_id | 현재 신원에 결합된 read-only 실행 문맥 |
| control-operation | operation_id | 사용자 제어 journal 조회 |
| recover_once | 아래 입력 | 기본 계획, execute=True에서만 복구 실행 |
| recovery-operation | recovery_attempt_id | 시도 이력 조회. 조회 성공과 시도 성공은 별개 |
| host-resume | barrier_id | 실패 조사 후 현 host admission의 명시적 재개. 차량 재무장이 아님 |

복구 요청 예시의 UUID·revision·시각은 실제 get/probe에서 얻어야 한다.

    {
      "vm_id":"550e8400-e29b-41d4-a716-446655440000",
      "recovery_attempt_id":"650e8400-e29b-41d4-a716-446655440000",
      "expected_host_boot_id":"750e8400-e29b-41d4-a716-446655440000",
      "expected_lifecycle_generation":0,
      "expected_intent_revision":2,
      "expected_execution":{"execution_id":"850e8400-e29b-41d4-a716-446655440000"},
      "mode":"graceful-restart",
      "deadline_monotonic_ms":123456789
    }

deadline_monotonic_ms는 같은 dom0 host의 monotonic clock 기준이며 최초 수락 시 남은 시간이
120초 이내여야 한다. 재전달 때 새 deadline으로 바꾸지 않는다. 원격 장치의 단조 시각은 사용하지 않는다.
mode는 start-if-absent / graceful-restart / force-restart다.
start-if-absent는 expected_execution에 execution_id 대신 absent_observation_revision을 사용한다.
부재 관측 revision에는 현재 등록 설정의 fingerprint도 포함되어 펌웨어·설정 교체 전 요청을 구분한다.
absent boolean·fenced=true·공개 origin 우회 필드는 받지 않는다.

복구는 현 lifecycle·적용 가능한 running revision·host admission·현재 실행/부재 revision·자산·
미완료 자산 작업·같은 시도를 재검사한다. graceful은 현재 Linux만 지원한다.
제거 확인 후 조건을 다시 검사하고 같은 자산으로 시작한다. Linux timeout을 destroy로 승격하지 않는다.
현재 host의 모든 Xen 조회/제어에는 같은 deadline의 남은 시간을 적용한다.
파일 fsync·kernel 호출을 선점하는 실시간 보장은 하지 않는다.

journal 단계는 accepted → stopping → removed → launching → observed → completed와 failed/uncertain이다.
removed에서 끊기면 새 조건 대조 후 재개할 수 있다. launching에서 끊기면 진행 중 xl이 있을 수 있다.
이미 결합된 실행의 owner가 같은 attempt이고 현재 unique_id가 맞으면 완료 기록만 복원한다.
결합 전이면 도메인이 없거나 보인다는 이유만으로 create/destroy를 반복하지 않는다.
이 상태에서 absent stop으로 정지 의도를 기록해도 미완료 launch는 해제하지 않는다.
자식 xl 종료와 실제 상태를 운영자가 재판정하기 전 새 launch는 거절하며, PID/기한만으로 해제하지 않는다.
같은 attempt 성공 재전달은 original_result와 current_observation을 따로 반환한다.
새 장애의 새 시도는 호출자가 새 attempt ID를 발급한다. 이전 host/lifecycle/intent 요청은 실행하지 않는다.

## 오류와 관측

기존 오류에 다음 코드를 추가한다. 호출자는 message 문자열을 파싱하지 않는다.

| 코드 | 의미 |
|---|---|
| HOST_QUIESCING | host가 새 시작·복구 수락을 닫음 |
| BOOT_ADMISSION_REQUIRED | 현재 host의 실행 정책이 미평가/미확정/거절 |
| INTENT_MISMATCH | 적용 가능한 running 의도 또는 revision 불일치 |
| TARGET_CHANGED | 이전 host/lifecycle/실행/부재 관측이 더 이상 대상이 아님 |
| RECOVERY_MODE_UNSUPPORTED | 협조형 종료 미지원 또는 per-VM force 승인 없음 |
| DEADLINE_EXCEEDED | 같은 host의 최초 deadline 만료·범위 초과 |
| OBSERVATION_UNAVAILABLE | Xen 접근·출력·incarnation을 확인하지 못함. 부재가 아님 |
| XEN_EXECUTION_FAILED | 수락 후 물리 제어 실패. accepted 의도는 유지 |
| RECONCILE_REQUIRED | 실행 또는 영구 기록의 결과를 안전하게 확정하지 못함 |
| OPERATION_IN_PROGRESS | 기존 잠금/미완료 작업 소유. 새 요청 수락 전 busy |

CLI exit는 성공 0, 입력/지원 검증 2, 충돌 3, 실행/불확실 결과 4다.
HOST_QUIESCING/BOOT_ADMISSION_REQUIRED/INTENT_MISMATCH는 충돌이며 RECONCILE_REQUIRED는 실행 오류다.
snapshot 조회는 성공해도 unknown/stale/last_error가 있을 수 있다.
last_success·최근 오류·host·observation_revision·관측 시각·age를 함께 본다.
running/blocked/paused/shutdown/crashed/dying/부재를 구별하며 blocked 자체를 장애로 판정하지 않는다.
snapshot은 mutation 허가가 아니다. recover_once는 항상 직접 재관측한다.

## 종료와 설치

xenova-host-admission.service는 실제 host-init/host-quiesce를 실행한다.
After=xenova-vms.service이므로 정상 종료 역순에서 vms/guest shutdown보다 먼저 barrier를 닫는다.
없는 Worker/Gateway unit을 만들거나 enable하지 않는다. 향후 실제 Worker unit은
After=xenova-vms.service, Before=xenova-host-admission.service 관계를 검토해 barrier 게시 뒤
Worker의 진행 작업 정리, 이후 기존 guest 종료가 되게 한다. Worker에 host coordinator를
PartOf로 묶지 않으며 Worker만 재시작할 때 host를 quiesce하지 않는다.

CLI reboot-board/poweroff/shutdown-all도 장기 잠금 전에 짧은 admission 잠금으로 barrier를 게시한다.
장기 operation_lock → 짧은 admission 잠금 순서이며, 종료는 짧은 잠금을 놓은 뒤 장기 잠금을 기다린다.
기존 drain과 guest 제거는 한 90초 deadline을 공유한다. 새 barrier가 실행 중 Xen을 즉시 취소하지 않는다.
이미 수락한 작업은 결과를 기록하며 복구는 제거 후 다시 gate를 확인해 새 실행을 막는다.
shutdown-requested의 기존 의미는 유지하고 실패 후 barrier를 finally에서 자동으로 열지 않는다.
직접 systemctl reboot의 ExecStop 실패로 OS 종료가 취소된다고 가정하지 않는다.
지원하는 사용자 종료 입구는 xenova-vm reboot-board/poweroff다.

새 모듈·entrypoint·root 정책·unit과 python3-ctypes는 recipe 설치에 연결한다.
Xen 라이브러리는 레시피 provider xen-tools-libxenmanage에 의존한다.
Debian SONAME 변환 후 실제 이미지의 패키지 이름이 libxenmanage1이다.
조립기는 파일/enablement/라이브러리 소스 일치 검사를 하며 이전 이미지가 새 기능을 가진 것으로
통과시키지 않는다. 이번 변경은 향후 dom0 재빌드가 필요하다.
Linux payload·Starter 펌웨어·기존 seed 내부 내용은 변경하지 않으므로 이 작업 때문에 재생성하지 않는다.
신규 factory running 의도는 새 dom0의 최초 등록 코드가 준비한다.

## 다른 담당자 인계

- Spring VmService.stop은 runtime이 없으면 vmControlPort.stop을 생략한다(현재 소스 L138~147).
  실제 도메인은 꺼졌지만 저장 의도는 running인 경우 웹 stop이 플랫폼에 도달하지 않을 수 있다.
  플랫폼의 absent stop 지원만으로 웹 RC-04가 완성되지 않는다.
- 현재 FastAPI service.stop → PlatformDriver._stop은 등록된 VM의 stop을 Manager까지 보낸다.
  등록 자체가 없을 때만 VM_NOT_FOUND를 멱등 성공으로 처리한다. Spring과 같은 생략으로 묶지 않는다.
- PlatformDriver.shutdown의 grace_s는 현재 Manager에 전달되지 않는다. 플랫폼 제거 대기 기본 90초와
  전후 관측 시간, HTTP/클라이언트 timeout 및 busy 대기 예산을 담당자가 함께 맞춰야 한다.
- 새 구조화 오류의 FastAPI 매핑, snapshot 소비와 recover_once 연결은 담당자 작업이다.
  기존 문자열 기반 timeout 분류만으로 새 오류를 정확하게 처리하지 못한다.
- 실행 ID ↔ guest/app session을 인증된 보고 경로에 묶고, 복구 뒤 동기화·준비·권한을 별도로 판단한다.
- Spring/FastAPI 운영 코드·G1 모듈/앱/lock·주행/Agent/Recovery Worker는 이 패치에서 수정하지 않는다.

실환경 절차는 xenova-recovery-foundation-board.md, 자동 시험은
platform/tests/test_xenova_recovery_foundation.py와 기존 전체 플랫폼 회귀를 사용한다.


## 중단·진단·잠금 경계

같은 host의 user admission을 재사용할 때 영구 intent의 VM/lifecycle/revision/의도와 대조한다.
intent가 이미 commit됐는데 admission이 뒤처졌다면 해당 control journal을 대조해 복원한다.
원본과 journal을 확인하지 못하면 INTENT_RECONCILIATION_REQUIRED로 start를 보류한다.
이 경계는 다음 host 부팅의 미승인 정책을 선택하지 않는다.

필수 intent/launch binding/journal 기록과 비핵심 execution-events를 구분한다.
completed journal 뒤 event 실패는 완료 결과를 되돌리지 않는다.
성공 응답은 data.diagnostic_warnings, 실패는 error.details.diagnostic_warnings에
DIAGNOSTIC_UNAVAILABLE·sink·event/cause_type을 덧붙일 수 있다. 원 envelope와 실행 결과는 유지한다.
필수 완료 journal이 실패하면 RECONCILE_REQUIRED이며 관측으로 확인하기 전 성공으로 처리하지 않는다.

만료한 accepted/removed attempt는 execute=True의 기존 소유 잠금 안에서 현재 기록·자산·Xen을
다시 대조한다. unresolved launch가 없고 removed면 실제 부재까지 확인한 경우 failed /
DEADLINE_EXCEEDED / expired_before_launch=true로 닫는다. 이는 추가 실행이 아니다.
같은 원 request의 deadline을 바꾸지 않는다. 이후 fresh 관측과 새 attempt를 사용한다.
계획·이력 조회는 만료 journal을 수정하지 않는다.
stopping/launching/uncertain 등 물리 동작이 미확정이면 retryable busy로 무기한 돌리지 않고
RECONCILE_REQUIRED·blocking_attempt_id·blocking_operation_id·phase·allowed_actions를 반환한다.

short_lock의 기본은 nonblocking이다. 충돌하면 OPERATION_IN_PROGRESS이며 intent나 barrier가
게시됐다고 보고하지 않는다. probe와 recovery admission은 호출자의 동일 deadline 안에서만
짧게 재시도하고, 기한 뒤 획득한 잠금으로 성공하지 않는다.
오류 snapshot 게시도 nonblocking이며 실패 시 원 JSON 오류와 진단 warning을 유지한다.
operation → short lock 순서는 유지한다. 파일 fsync/커널 호출의 hard real-time 보장은 아니다.
host-init/quiesce/resume의 잠금 경합도 실패로 반환하므로 barrier 게시 여부를 확인하고 재시도한다.
진행 중 systemctl 종료를 ExecStop 실패로 취소할 수 있다는 뜻은 아니다.
