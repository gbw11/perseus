# Xenova 플랫폼 복구 기반 실환경 시험

새 dom0를 승인 후 빌드·배포한 다음 사용자가 실행한다. 이 문서는 시험 명령이며 측정 결과가 아니다.
차량 출력/제어권을 비활성화하고 사용자 지정 임시 VM만 사용한다.
기존 운영 VM·디스크·기본 Zephyr A/B·G1 시험 앱을 덮어쓰지 않는다.
VM 재시작과 앱 준비·동기화·운행 허가는 별도로 기록한다.

## 읽기 전용 사전 점검

아래 명령은 성공한 출력만 기준선으로 보관한다. 실패하면 해당 단계에서 멈춘다.

```sh
uname -a
xl info
xl list -v
systemctl status xenova-host-admission.service xenova-vm-shutdown.service --no-pager
systemctl cat xenova-host-admission.service --no-pager
cat /proc/sys/kernel/random/boot_id
cat /etc/xenova/execution-policy.json
```

기존 키/비밀번호/네트워크를 초기화하지 않는다. 데이터 mount도 확인한다.

```sh
findmnt /srv/xenova
xenova-vm list --json >before-vms.tmp
rc=$?; echo "list rc=$rc"; [ "$rc" -eq 0 ] || exit 1
mv before-vms.tmp before-vms.json
```

libxenmanage1·python3-ctypes·xenova-domain-identity와 새 모듈이 없으면 중단하고 dom0 패키징을 확인한다.
기존 rootfs에 파일을 수동 주입해 새 WIC 검증으로 대신하지 않는다.
기본 정책 두 항목은 null이다. 이 시험을 이유로 운영 선택을 임의로 승인하지 않는다.

## 임시 VM 준비와 실행 의도

VM_A/VM_B는 Spring 계약과 같은 서로 다른 UUID를 사용한다. 아래 입력은 사용자 값으로 치환한다.
create는 원래 정지 등록이며 정확한 카탈로그 버전을 지정한다.

```sh
VM_A=REPLACE_WITH_TEST_UUID_A
VM_B=REPLACE_WITH_TEST_UUID_B
xenova-vm templates --json
xenova-vm create --vm-id "$VM_A" --name '복구 시험 A'   --template-id debian-13-arm64 --template-version 1.0.0   --vcpus 1 --memory-mib 512 --auto-ip --json
rc=$?; echo "plan rc=$rc"; [ "$rc" -eq 0 ] || exit 1
```

계획과 공간·주소를 확인한 뒤 같은 명령에 --execute를 추가한다. B도 별도 UUID로 생성한다.
원본 템플릿과 다른 VM의 디스크를 공유하지 않는다. 생성 결과의 vm_id·IP·템플릿·의도를 보관한다.

```sh
xenova-vm start "$VM_A" --idempotency-key test-start-a --json >start-a.tmp
rc=$?; echo "start rc=$rc"; [ "$rc" -eq 0 ] || exit 1
mv start-a.tmp start-a.json
xenova-vm get "$VM_A" --json
xenova-vm execution-context "$VM_A" --json
```

B도 시작하고 execution_id·domid·xen_unique_id가 구별되는지 확인한다.
동일 실행 재조회와 동일 키 start 재전달에서 execution_id/revision이 바뀌지 않아야 한다.
새 키 또는 키 없는 새 start는 새 intent_revision을 수락하지만 이미 실행 중인 도메인을 복제하지 않는다.
기존 CLI 이름을 사용할 수도 있으나 기계용 snapshot/복구는 UUID를 사용한다.

## 한 번의 복구와 응답 소실 재전달

요청 파일은 같은 시도의 재전달 동안 보존한다. 다음 Python은 실제 Manager.get 결과를 사용한다.
현재 dom0에서 실행하므로 deadline도 같은 host의 monotonic clock이다.
90초 예시는 기존 종료 대기와 비교할 시험 예산이며 차량 합격 지연값이 아니다.

```sh
REQ=/absolute/new/test-recovery-a.json
PYTHONPATH=/usr/lib/xenova python3 -B - "$VM_A" "$REQ" <<'PY'
import json,sys,time
from xenova_management import Manager
from first_install_contract import uuid4
response=Manager().call("get",{"vm_id":sys.argv[1]})
if not response["ok"]: raise SystemExit(response["error"])
v=response["data"]["vm"]
if not v["execution_id"]: raise SystemExit("managed execution is unknown; stop and investigate")
request={"vm_id":v["vm_id"],"recovery_attempt_id":uuid4(),
 "expected_host_boot_id":v["host_boot_id"],"expected_lifecycle_generation":v["generation"],
 "expected_intent_revision":v["intent_revision"],"expected_execution":{"execution_id":v["execution_id"]},
 "mode":"graceful-restart","deadline_monotonic_ms":int((time.monotonic()+90)*1000)}
with open(sys.argv[2],"x") as stream: json.dump(request,stream,indent=2)
PY
rc=$?; echo "request rc=$rc"; [ "$rc" -eq 0 ] || exit 1
```

```sh
xenova-vm recover-once --spec-file "$REQ" --json
rc=$?; echo "recovery plan rc=$rc"; [ "$rc" -eq 0 ] || exit 1
xenova-vm recover-once --spec-file "$REQ" --execute --json >recovery-a.tmp
rc=$?; echo "recovery rc=$rc"; [ "$rc" -eq 0 ] || exit 1
mv recovery-a.tmp recovery-a.json
xenova-vm recover-once --spec-file "$REQ" --execute --json >replay-a.tmp
rc=$?; echo "replay rc=$rc"; [ "$rc" -eq 0 ] || exit 1
mv replay-a.tmp replay-a.json
```

최초 결과와 replayed=true를 비교한다. 새 execution_id는 하나이며 재전달로 다시 재시작하지 않는다.
original_result와 current_observation을 따로 확인한다. 앱 준비는 unknown이다.
이후 새 장애에는 새 관측·새 attempt ID의 파일을 만든다. 기존 deadline만 바꾸지 않는다.

B의 snapshot을 연속 소비하여 A의 긴 복구를 기다리지 않는지 관측한다.
max_age_ms는 시험자가 정하는 관측 freshness이며 차량 timeout이 아니다.

```sh
xenova-vm snapshot "$VM_B" --max-age-ms 1000 --json
rc=$?; echo "snapshot rc=$rc"; [ "$rc" -eq 0 ] || exit 1
```

snapshot의 ok=true는 조회 호출의 성공이다. last_success의 현재 유효성은
unknown/stale/last_error와 host/revision으로 판단한다. 담당 수집 서비스가 probe를 호출해 갱신한다.
새 감시 데몬이나 자동 복구 루프를 실행하지 않는다.

## 사용자 정지와 오래된 요청

```sh
xenova-vm stop "$VM_A" --idempotency-key test-user-stop-a --json
rc=$?; echo "stop rc=$rc"; [ "$rc" -eq 0 ] || exit 1
xenova-vm stop "$VM_A" --idempotency-key test-user-stop-a --json
xenova-vm get "$VM_A" --json
```

첫 유효 stop에서 stopped/revision이 확정되며 같은 키 재전달로 revision은 늘지 않는다.
이미 부재여도 유효 stop은 의도를 기록한다. 이전 미실행 복구 요청은 INTENT_MISMATCH 등으로 거절한다.
stop→start 후 다시 running이어도 과거 revision/execution 요청은 통과하지 않는다.
완료한 과거 attempt는 이력과 현재 관측만 반환하며 VM을 다시 시작하지 않는다.

start-if-absent는 시험 전용 VM의 외부 장애 주입으로 부재가 된 경우 별도로 시험한다.
사용자 stop/destroy는 stopped 의도를 기록하므로 장애 주입과 구분한다.
domid를 추측해 제거하지 말고 UUID/name/현재 unique_id를 재확인하여 지정한 시험 VM만 대상으로 한다.
그 뒤 probe의 absent_observation_revision을 기대값으로 쓴다. absent=true는 허용하지 않는다.

## Zephyr와 강제 종료

같은 Starter 템플릿에서 새 UUID의 시험 Zephyr를 준비한다. 기존 A/B와 G1은 변경하지 않는다.
graceful-restart는 거절한다. force-restart도 기본 거절이며 board force-allowed만으로 승인되지 않는다.
앱 강제 종료 안전성·차량 출력 정지를 확인하고 운영자가 승인한 경우만
/etc/xenova/execution-policy.json의 force_restart_vm_ids에 시험 UUID를 추가한다.
다른 설정·UUID는 바꾸지 않는다. 자동 승인 주입 도구는 없다.
그 후 force-restart 한 번의 새 실행만 관측한다. Linux graceful timeout의 자동 force 승격도 없어야 한다.

## 서비스와 host 재부팅

새 Python Manager 프로세스에서 get/probe해도 host_boot_id·execution_id·의도가 유지돼야 한다.
실 Worker 배포 후에는 담당자의 실제 서비스만 재시작한다. host coordinator restart로 대신하지 않는다.
xenova-host-admission.service는 정지 시 gate를 닫고 같은 host의 재시작만으로 열지 않는다.

정상 종료 경합 시험은 시험 전용 보드에서 운영 VM·차량 출력 영향을 먼저 확인한 뒤 실시한다.

```sh
xenova-vm reboot-board >board-reboot.log 2>&1
rc=$?; echo "reboot-board rc=$rc"; [ "$rc" -eq 0 ] || exit 1
```

진행 중 복구와 경합할 때 barrier가 먼저 게시되고 새 start/recovery가 차단돼야 한다.
이미 수락된 Xen 호출의 즉시 취소를 기대하지 않는다. 제거 후 재검사에서 새 생성을 막는지 본다.
실패하면 OS 종료를 요청하지 않고 barrier가 남아야 한다.
직접 systemctl reboot는 ExecStop 실패로 OS 종료를 취소하지 못하므로 실패 중단 시험을 대신할 수 없다.
조사 뒤 host-resume은 현재 barrier_id를 명시한다. 이는 차량 재무장이 아니다.
storage가 이미 해제됐다면 먼저 mount·상태를 조사한다. 대체 저장소는 만들지 않는다.

새 host에는 이전 admission·단조 시각·미완료 실행을 적용하지 않는다.
미승인 부팅 조합은 별도 기록한다. autostart=true/running과 false/stopped 조합도 함께 시험한다.
poweroff는 reboot와 별도로 시험한다.

## 로그와 회수

```sh
journalctl -b --no-pager -u xenova-host-admission.service   -u xenova-vms.service -u xenova-vm-shutdown.service >platform-journal.log
rc=$?; echo "journal rc=$rc"; [ "$rc" -eq 0 ] || exit 1
xl list -v >after-xl.tmp
rc=$?; echo "xl rc=$rc"; [ "$rc" -eq 0 ] || exit 1
mv after-xl.tmp after-xl.txt
```

/srv/xenova/control·control-requests·해당 recovery-attempts와,
/run/xenova/executions·observations·boot-admission·admission·execution-events·
last-stop.json/board-shutdown-result.json을 대상별로 수집한다.
Wi-Fi PSK·개인키·서비스 token은 포함하지 않는다. 명령별 종료 코드도 남긴다.

Linux 시험 파일·machine-id·SSH 공개 호스트키 비교는 성공한 SSH 수집만 확정한다.
실패로 생긴 빈 비교 파일을 데이터 소실로 판정하지 않는다. 호스트 키 검사를 끄지 않는다.
UUID·MAC/IP·디스크·펌웨어 내용도 전후 비교한다.

시험 후 지정한 임시 VM만 CLI stop으로 정지한다. Zephyr는 명시 --force가 필요하다.
자산 삭제는 이 시험에 필수가 아니다. 추가 보관/삭제는 기존 별도 계약으로 명시해 수행한다.
템플릿·다른 VM·G1·registry를 일괄 청소하지 않는다.
반복 횟수와 각 결과를 기록하고 코드/모델/FFI/실제 Xen/앱/차량 결과를 나눠 기록한다.
지연·지터·차량 정지 거리의 합격 수치를 이 절차에서 새로 정하지 않는다.


## 만료·진단 저장 오류·잠금 경합의 추가 시험

아래는 사용자 지정 시험 VM에서만 수행할 장애 주입 항목이다.
실제 Xen의 정상 재시작과 별도로 각 지점을 기록한다. 진단 event 저장 장애를 만들기 위해
/run 전체를 채우거나 운영 프로세스의 권한을 변경하지 않는다. 격리된 시험 주입 경계를 사용한다.

- stop intent commit/accepted/admission 게시 직전 중단 후 같은 host에서 autostart를 재호출한다.
  stopped/revision은 유지되고 추가 도메인 생성이 없어야 한다. 다음 host 정책 시험과 분리한다.
- control/recovery 완료·재전달 event 저장만 실패시킨다. journal은 completed이고
  원 실행 결과는 성공이며 diagnostic_warnings가 있어야 한다. 필수 journal 실패는 별도 재판정이다.
- accepted/removed 중단 뒤 원 deadline을 넘긴다. 아래처럼 원 파일을 그대로 재전달한다.

```sh
xenova-vm recover-once --spec-file "$EXPIRED_REQ" --execute --json >expired-result.json
rc=$?; echo "expiration reconciliation rc=$rc"
xenova-vm recovery-operation --recovery-attempt-id "$ATTEMPT_ID" --json
rc=$?; echo "history rc=$rc"; [ "$rc" -eq 0 ] || exit 1
```

원 재전달의 DEADLINE_EXCEEDED는 예상 오류다. history 조회의 성공과 attempt 성공은 다르다.
phase=failed 및 expired_before_launch=true가 확인된 경우만 새 관측·새 attempt 파일로 진행한다.
launching/uncertain/stopping이면 새 create/destroy를 실행하지 않는다. 다음을 수집한다.

```sh
xenova-vm recovery-operation --recovery-attempt-id "$ATTEMPT_ID" --json
xenova-vm execution-context "$VM_A" --json
xenova-vm probe "$VM_A" --timeout-ms 1000 --json
xl list -v
```

현재 실행·owner가 같은 attempt에 이미 결합된 경우 원 요청 재전달은 완료 기록만 복원할 수 있다.
결합 전이면 임의 PID 종료/잠금 파일 삭제/journal 편집으로 해제하지 않는다.
자식 xl·Xen 도메인·자산의 상태를 조사하고 별도 승인된 host 종료/새 부팅 경계에서 재평가한다.
새 부팅에는 새 host/관측/admission·attempt를 사용하며 과거 결과를 성공으로 고쳐 쓰지 않는다.

- Xen timeout/깨진 출력/operation busy와 observations 저장 실패를 조합한다.
  stdout은 항상 JSON envelope 하나이며 success/absent로 바뀌지 않아야 한다.
- 별도 시험 프로세스가 observation/admission 잠금을 보유하게 한다.
  probe는 기한 내 구조화된 실패로, 기본 host admission 변경은 busy로 반환해야 한다.
  늦게 잠금이 풀렸다는 이유로 이전 요청이 성공하거나 intent/barrier가 뒤늦게 바뀌면 안 된다.
