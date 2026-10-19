# Xenova VM 관리 계약 1.0

공통 Python 진입점은 /usr/lib/xenova/xenova_management.py의 Manager.call이다.
CLI와 Python 호출자는 같은 함수·잠금·작업 JSON·기존 인스턴스 준비 함수를 사용한다.
서버, 인증, 큐, Agent, 자동 복구는 제공하지 않는다. root 권한의 로컬 관리 인터페이스다.

## 신원과 응답

vm_id는 instance.json의 uuid 및 Xen UUID와 같다. 일반 create는 Spring이 먼저 발급한
UUID를 필수로 받는다. 대문자·32자리 표기는 소문자 하이픈 표기로 정규화한다.
공개 name은 한글·공백을 포함하는 1..63자 표시 이름이며 중복을 허용한다.
instance.json의 display_name에 보관하고, 내부 id/name/hostname과 디렉터리는
vm-<UUID의 하이픈 없는 표기>를 사용한다. 표시 이름은 Xen cfg나 경로에 삽입하지 않는다.
domid는 매 실행 달라질 수 있는 Xen 번호다. UUID→domid 검증과 제어 직전 재조회는 유지한다.
기존 UUID, MAC, IP, machine-id, SSH 키를 마이그레이션하거나 다시 발급하지 않는다.

Manager.call(action, spec, execute=False, idempotency_key=None, request_id=None)는 다음을 반환한다.
schema_version=1.0, ok, request_id, operation_id(없으면 null), replayed, data, error.
error는 code, message, retryable, details다. Python 함수는 envelope를 반환하며 print하지 않는다.
CLI --json은 stdout에 JSON 하나만 출력한다. 준비 도구의 출력은 stderr로 보낸다.
console은 PV 바이트 스트림으로 기존 별도 명령이며 --json 관리 응답 대상이 아니다.

종료 코드: 성공 0, 입력/미존재/지원 범위 오류 2, 충돌/진행 중/실행 상태 충돌 3,
실행·저장소·복구 오류 4. HTTP 매핑은 호출자 책임이다.
작업 조회 ok=true는 조회 자체의 성공이다. operation.status와 recovery_required를 따로 확인한다.
운영 오류 코드는 소스의 VALIDATION, CONFLICTS와 ManagementError를 따른다.
실행 대상의 UUID/name/domid 불일치는 DomainIdentityError를 통해 TARGET_CHANGED로
반환한다. 종료 결과를 집계할 때도 이 유형을 보존한다.
Xen 조회 명령 실패·잘못된 자산을 문자열 패턴만으로 신원 변경으로 분류하지 않는다.
INVALID_SPEC / VM_NOT_FOUND / VM_NAME_CONFLICT / TEMPLATE_NOT_FOUND /
TEMPLATE_VERSION_NOT_FOUND / ARTIFACT_INVALID / STORAGE_UNAVAILABLE /
INSUFFICIENT_STORAGE / IP_POOL_EXHAUSTED / IP_CONFLICT / RESOURCE_UNSUPPORTED / IDEMPOTENCY_CONFLICT /
OPERATION_IN_PROGRESS / VM_NOT_STOPPED / TARGET_CHANGED / ASSET_IN_USE /
RESTORE_CONFLICT / VM_PURGED / CLEANUP_INCOMPLETE / RECOVERY_REQUIRED를 구분한다.

## 공통 입력 검사

spec은 JSON 객체(dict)이며 동작별 허용 필드만 받는다. get은 정규 UUID인 vm_id,
list는 lifecycle만 받는다. templates는 빈 객체만 받는다.
operation은 operation_id(UUID) 또는 idempotency_key 중 정확히 하나를 지정한다.
CLI가 선택하지 않은 selector를 null로 전달하는 것은 허용한다.
request_id와 재시도 키는 제어문자가 없는 1..200 UTF-8 bytes 문자열이다.
잘못된 타입·필드·selector는 INVALID_SPEC envelope와 CLI 종료 코드 2를 반환한다.
내부 AttributeError를 입력 오류로 포괄 변환하지 않는다.

## 템플릿과 생성

공개 template_id:
- ubuntu-24.04-arm64
- debian-13-arm64
- zephyr-xenvm-starter-arm64

최초 배포 버전은 각각 1.0.0이다. ID에는 준비 날짜나 앱 배포 버전을 넣지 않는다.
catalog/<template_id>/<template_version>.json은 원래 불변 번들의 상대 경로,
source_template_id, 실제 manifest_sha256을 고정한다. 동일 ID/버전 descriptor를 덮어쓰지 않는다.
source_template_id와 기존 instance.template은 과거 출처 식별자다. source lock 날짜·버전은 보존한다.
기존 인스턴스의 template_id/version을 모르면 추측해 채우지 않는다.

create spec은 vm_id, name, template_id, template_version, vcpus, memory_mib다.
guest_kind는 정확히 선택한 카탈로그에서 확정하며 호출자가 보내지 않는다.
Spring은 ipv4/idempotency_key를 보내지 않는다. Linux의 IP 생략은 자동 예약을 뜻한다.
CLI --auto-ip도 같은 경로다. 기존 CLI의 명시적 --ipv4는 충돌 검사 후 사용할 수 있다.
latest 해석과 사용자 지정 MAC/경로/bridge는 없다.
Linux 허용 범위는 1..4 vCPU, 128..8192MiB, 192.168.77.2..99다.
Zephyr는 1 vCPU/16MiB이고 ipv4를 받지 않는다. 물리 호스트의 여유 자원을 보장하는 예약기는 아니다.
새 일반 VM은 autostart=false이고 Zephyr board_shutdown_policy=refuse다.
factory 기본 VM 3개의 기존 정책은 별도 defaults.json을 따른다.
Spring에 연결되기 전 factory 준비만 기존 보드별 UUID 고유화를 예외로 유지한다.
공통 이미지에 고정 UUID를 넣지 않으며 일반 create로 이 예외를 사용할 수 없다.

예시(기본은 계획; --execute에서만 등록):
    xenova-vm templates --json
    xenova-vm create --vm-id <SPRING_UUID> --name "개발용 Ubuntu" --template-id ubuntu-24.04-arm64 --template-version 1.0.0 --vcpus 1 --memory-mib 512 --auto-ip --json
    xenova-vm create --vm-id <SPRING_UUID> --name "개발용 Ubuntu" --template-id ubuntu-24.04-arm64 --template-version 1.0.0 --vcpus 1 --memory-mib 512 --auto-ip --execute --json

생성 결과 data.vm_id로 후속 요청을 한다. 생성은 시작하지 않는다.
최초 creation_spec은 바꾸지 않는다. 현재 설정은 instance.json, 실행 관측은 get의 xen_state/domid다.
앱 준비 상태는 unknown이다. start가 반환됐다고 앱 준비 완료로 표시하지 않는다.

## 조회와 실행

    xenova-vm list --json
    xenova-vm list --lifecycle RETIRED --json
    xenova-vm list --lifecycle PURGED --json
    xenova-vm get <UUID> --json
    xenova-vm operation --operation-id <OPERATION_UUID> --json
    xenova-vm operation --idempotency-key create:<SPRING_UUID> --json
    xenova-vm start <UUID> --json

기존 이름 기반 start/stop/reboot/console/status는 유지한다. JSON start/stop/reboot는
같은 영구 UUID를 해석해 기존 domid 확인·제어 코드를 호출한다. console은 기존 이름 CLI를 쓴다.
자동 시작 결과 파일은 시작 시점 기록이며 현재 상태 모니터가 아니다.
--json을 붙인 status는 list의 등록 VM 응답과 같다.
기존 delete는 retire의 보관 별칭이다. 영구 삭제로 의미가 바뀌지 않는다.
create는 외부 UUID·명시 버전을 요구한다. delete(retire)는 여전히 호출자 재시도 키가 필요하다.
start/stop/reboot의 JSON 결과는 data.vm에 같은 VM 객체를 반환한다.
stop은 기본 최대 90초의 전체 단조 시계 마감시간 내 도메인 제거를 동기 확인한다.
--force는 명시적인 즉시 destroy이며 Linux에도 적용된다. 일반 stop은 시간 초과 시
자동 destroy로 바꾸지 않는다. Zephyr 개별 stop/reboot는 --force 없이 거부한다.

## 보관·복원·삭제

    xenova-vm retire <UUID> --idempotency-key archive-1 --execute --json
    xenova-vm restore <UUID> --idempotency-key restore-1 --execute --json
    xenova-vm purge <UUID> --confirm-vm-id <UUID> --idempotency-key purge-1 --execute --json

--execute를 빼면 계획만 조회한다. 등록/디스크/작업 기록/이름·IP 예약은 하지 않는다.
retire는 정지한 VM을 숨김 archive로 옮긴다. 공간 회수가 아니며 VM 자산·정책을 보존한다.
restore는 같은 자산/UUID를 검증해 재등록하며 즉시 시작하지 않는다. 이름/UUID/MAC/IP가
충돌하면 거부한다. 쓰기 디스크는 최초 템플릿 해시와 대조하지 않고 존재·ext4 구조·소유권을 검사한다.
복원 시 사용 여부를 확인한 뒤 읽기 전용 e2fsck -fn도 통과해야 한다.
restore의 계획·실행·중단 재시도는 복원 후 전체 등록 집합을 기준으로 기존 용량 정책을 적용한다.
필요 여유는 추가 복사량 + 등록 예정 Linux 디스크의 미할당 증가분 + 미완료 생성의
남은 약정량 + 2GiB다. restore의 추가 복사량은 0이며 보관 디스크의 이미 할당된 블록은
현재 free에 반영되어 있으므로 다시 더하지 않는다.
기록만 남은 생성은 준비할 payload를 예약하고, 복사된 payload는 파일별 실제 할당량만
차감한다. 이미 게시되어 등록 집합에 포함된 생성은 중복 예약하지 않는다.
공간 부족은 INSUFFICIENT_STORAGE이며 디스크·UUID·정책을 변경하지 않는다.
이동 후 중단은 게시 자산을 되돌리거나 삭제하지 않고 같은 작업으로 재확인한다.
완료된 작업의 부작용 없는 재전달은 현재 여유 공간 때문에 다시 실행하거나 차단하지 않는다.
커널/initrd와 활성 불변 펌웨어는 해당 해시를 검사한다. 누락 디스크를 새로 만들지 않는다.

purge는 RETIRED만 받으며 UUID 재확인과 --execute가 필요하다.
Xen 디스크 참조, mountinfo, loop backing, 프로세스 fd를 확인할 수 없으면 삭제하지 않는다.
심볼릭 링크, 외부 부모 링크, hardlink, 특수 파일, 중첩 mount를 거부한다.
작업 소유 inode 목록을 영구 기록하고 fd-relative unlink로 그 자산만 삭제한다.
purge는 신원·소유권을 확인하며, 손상된 부팅 파일을 고치거나 정상 부팅 가능해야 한다고 요구하지 않는다.
부분 삭제 시 inventory/remaining_assets를 남긴다. 원래 key로 재시도하고 restore는 거부한다.
논리 bytes, 할당 bytes, 파일시스템 free_bytes_delta를 구분한다. 보안 소거 기능이 아니다.
UUID와 내부 이름은 purge 후에도 예약되며 factory가 기본 VM을 재생성하지 않는다.

## 영구 기록과 중단

/srv/xenova/instances/<internal_name>/instance.json
/srv/xenova/operations/<operation_id>.json
/srv/xenova/tombstones/<vm_id>.json
/srv/xenova/instances/.retired-<internal_name>-<vm_id>-g<generation>/

모든 변경은 기존 /run/xenova 공통 flock을 사용한다. 올바른 ext4 마운트/초기화 표식을
확인하지 못하면 루트 파일시스템의 대체 저장소를 만들지 않는다.
재시도 키는 저장소 전체에서 유일하고 작업·대상 간 재사용은 충돌이다. 키를 경로로 쓰지 않는다.
명세 해시는 v1 canonical JSON(UTF-8, key 정렬, 공백 없음, ensure_ascii=false, NaN 금지)의 SHA-256이다.
일반 create의 키는 create:<정규 UUID>로 유도한다. 전달된 기존 create idempotency_key는
새 작업 선택에 사용하지 않는다. 같은 UUID의 최초 요청이 다르면 IDEMPOTENCY_CONFLICT다.
원래 입력(request), guest_kind/기본 정책을 적용한 최초 명세(initial_spec), 배정한
internal_name/MAC/ipv4(assigned)를 복사 전에 기록한다. 재호출은 당시 값을 재사용한다.
메타데이터는 파일 fsync→원자적 교체→부모 fsync, 새 인스턴스/이동은 renameat2 NOREPLACE를 쓴다.
여러 파일 전체가 하나의 트랜잭션은 아니다.

같은 key/같은 요청의 성공 재호출은 original_result와 current를 반환한다.
create의 최상위 VM 필드는 현재 설정을 반영하며 original_result는 최초 결과다.
현재 설정을 처음 값으로 되돌리지 않는다. RETIRED의 create는 TARGET_CHANGED,
PURGED의 create는 VM_PURGED다. 과거 성공 기록은 operation 조회로 확인한다.
retire/restore/purge는 각각 고유한 호출자 키와 operation_id·expected_generation을 유지한다.
같은 UUID를 모든 수명주기 작업의 키로 쓰지 않는다. 오래된 작업에 새 키를 자동 발급하면 안 된다.
retire→restore 후 오래된 retire/purge는 현재 세대의 자산을 다시 변경하지 않는다.
중단 후 잠금을 얻은 호출만 이전 동기 작업을 재확인한다. PID 숫자만 신뢰하지 않는다.
게시 완료·응답 소실은 기존 결과를 복원한다. 복사 도중 남은 작업 공간은 보존하고
RECOVERY_REQUIRED로 표시하며 자동 초기화/일괄 삭제하지 않는다.
기존 .deleted-name-uuid 보관 폴더는 UUID 조회·명시적 restore로 수용한다.
작업 기록/tombstone은 purge와 독립적으로 남고 TTL로 삭제하지 않는다.
복구 시 운영자는 작업 JSON의 소유 경로·mount/loop·inode를 대조해야 한다.
외부 root 프로세스의 xl/파일 변경까지 완전히 원자적으로 차단하지는 않는다.

shutdown-requested가 있으면 새 변경 작업, 중단 작업의 실제 재개,
JSON autostart-set/board-shutdown-set/config 쓰기를 거부한다.
조회·계획·완료 결과의 부작용 없는 재전달은 허용한다. 거부된 재개 요청이
기존 작업 JSON을 오류 상태로 덮어쓰지 않는다. 종료/정리 자체의 제어 경로는 유지한다.
Manager의 operation_id/replayed/오류 기록 대상은 호출별 지역 문맥이며,
같은 객체의 다른 호출이 잠금 충돌로 거부돼도 진행 중 응답을 바꾸지 않는다.

관리 조회·retired_instance·first-boot·autostart는 같은 정식 tombstone 판독을 쓴다.
UUID.json.tmp-<PID>-<12자리 hex>의 잔여 일반 파일은 JSON으로 채택하지 않고 보존한다.
정식 UUID.json의 손상·신원 불일치, 알려지지 않은 파일명·링크는 계속 거부한다.

## 다음 자산 준비·게시

기존 1.0.0 게시본은 수정하지 않고 새 descriptor로 공개 ID를 연결할 수 있다.
scripts/publish-xenova-template-catalog.py --data-root <DATA_ROOT> --descriptor <JSON> [--execute]
는 검증된 templates/ 또는 rtos-templates/ 내부 자산만 참조하고 payload는 쓰지 않는다.
계획도 실제 해시를 확인하며 같은 ID/버전의 다른 내용은 거부한다.
새 Linux 준비 코드/overlay로 내용이 달라지면 recipe의 새 버전(예:1.1.0)을 정하고
새 출력으로 준비·검증·게시한 뒤 default-templates.lock.json의 실제 해시·버전을 갱신한다.
Starter 앱 소스 변경은 기존 refresh/build/receipt 절차를 따른다. 앱 펌웨어 version과
템플릿 배포 template_version은 별개이며 packager --template-version으로 배포 버전을 지정한다.
synchronization 진단 번들은 Starter 공개 ID로 등록하지 않는다.
기존 보고서·출처·source lock은 고쳐 쓰지 않는다. 실제 실행 승인 전에는 자산을 재생성하지 않는다.


기존 Platform.create/delete Python 메서드도 Manager.call에 위임하고 envelope를 반환한다.
create의 기존 records 인자는 신뢰하거나 재사용하지 않는다. 기존 직접 호출자도
vm_id와 template_version을 전달하고 ok/error를 검사해야 한다. retire/delete에는 재시도 키도 필요하다.
공통 관리 API를 동시에 호출하는 프로그램도 기존 글로벌 잠금의 충돌 응답을 처리한다.

후속 실제 dom0에서 먼저 import를 확인한다:
    PYTHONPATH=/usr/lib/xenova python3 -B -c 'import xenova_management, xenova_management_cli, xenova_catalog, xenova_wifi, artifact_io'
    echo $?
WSL import 성공은 이 타깃 검사나 이미지 패키지 검사를 대신하지 않는다.

## Linux IP 소유권과 공통 VM 필드

/etc/xenova/defaults.json의 network.ipv4_pool은 192.168.77.2..99,
reserved_ipv4는 기본 .10/.20이다. .1/네트워크/브로드캐스트는 풀 밖이다.
선택·작업 기록·복사·게시·DHCP 반영은 기존 전역 잠금 안에서 실행한다.
등록 VM, 보관 VM, 미완료 생성 assigned, 정적 DHCP 예약, 현재 lease를 제외한다.
생성 중단/stop/start/retire는 주소를 유지하고 restore는 기존 주소 충돌을 다시 검사한다.
purge 완료 후 현재 소유/예약/lease가 없으면 주소를 다시 선택할 수 있다.
과거 성공 작업에 적힌 IP만으로 영구 점유하지 않는다. 정리 미완료 작업은 예약을 유지한다.
풀 고갈은 IP_POOL_EXHAUSTED(종료 코드 4)이고 잠금 경합 OPERATION_IN_PROGRESS와 다르다.
기존 정적 DHCP 예약은 보존한다. DHCP reload 실패는 dhcp-pending 단계에 남는다.
게시된 디스크를 없애거나 새 IP로 바꾸지 않고 같은 작업으로 반영을 재시도한다.
IP는 배정 주소이며 실제 게스트 DHCP 획득이나 SSH 준비 완료 증거가 아니다.
Zephyr는 주소를 예약하지 않고 ipv4=null을 반환한다. SSH ProxyJump 허용 범위 .2..99와 같다.

create의 data, list의 data.vms[], get의 data.vm은 다음 필드를 같은 위치에 제공한다:
vm_id, name, template_id, template_version, guest_kind, vcpus, memory_mib, ipv4.
configuration에는 내부 등록 설정, initial_spec에는 최초 생성 명세가 남는다.
기존 형식에 없는 공개 템플릿 출처는 null로 반환하며 추측해서 채우지 않는다.
factory 신규 등록은 defaults의 공개 ID/버전을 보존한다.
기존 이름 기반 인스턴스·작업 기록은 읽되 경로를 이동하지 않는다.
외부 UUID 없는 과거 create 요청의 자동 재실행은 제공하지 않는다.

## Spring 연결 검사와 후속 연동 경계

공통 HTTP 계약은 [VM API 통합 문서](../contracts/vm-api-contract-v1.md)와
[OpenAPI](../../contracts/dom0-manager-openapi.yaml)를 따른다.
HTTP와 이 가이드의 Manager/CLI는 별도 인터페이스다.

Spring의 Dom0LaunchRequest는 vm_id·template_id·template_version·vcpus·memory_mib의
5개 필드를 전송한다. 이 가이드의 Manager.create는 name을 포함한 6개 필드가 필수다.
tests/contracts/spring-create-request.json은 Manager용 6개 필드 시험 명세이며,
실제 Spring HTTP 요청의 직렬화 결과로 사용하지 않는다.
tests/contracts/spring_manager_bridge.py는 이 명세로 실제 파일/registry/journal/IP 예약을
검사하는 시험 연결부다. Xen·mount·OS 복사만 대역이며 배포 HTTP 어댑터나 Spring 응답 직렬화기가 아니다.

    python3 -B -m unittest discover -s tests/platform -p 'test_xenova_spring_contract.py' -v

검사는 현재 Spring DTO의 5개 필드와 Manager 필수 name의 차이를 명시적으로 대조한다.
Python 소스 대조는 Java 실행·HTTP 배포·실보드 검증을 대신하지 않는다.
services/dom0-manager에는 HTTP 서비스·Xen 조회 드라이버와 명령/Provisioner 인터페이스가 있다.
XenDriver의 create/shutdown/destroy와 UnavailableProvisioner의 변경 연산은 아직
NotImplementedError이므로 이들 명령을 공통 Manager로 연결해야 한다.

Spring VmService는 VmRecord에 UUID·명세를 저장하고 같은 이름·명세의 재요청에서 재사용한다.
Spring command-timeout은 기본 100초이고 플랫폼 stop 마감은 최대 90초다.
현재 HTTP create는 name을 받지 않고 생성·시작을 묶는 계약이므로, Manager의 필수 name 공급과
별도 start 호출을 연결해야 한다. 이 가이드는 HTTP 스키마나 플랫폼 입력을 임의 변경하지 않는다.
retire/restore/purge의 operation_id·원래 재시도 키·대상 세대 전달도 필요하며,
새 재시도마다 현재 세대의 작업을 새로 만드는 방식으로 연결하지 않는다.
플랫폼의 배정 ipv4와 Spring 공개 응답의 에이전트 보고 ipv4는 서로 다른 관측값이다.
Zephyr의 고정 1 vCPU/16MiB와 현재 HTTP/Spring 최소 메모리 128MiB 차이는
공통 계약의 연동 항목이며, 큰 메모리를 임의로 전달해 프로필 검사를 우회하지 않는다.

## factory 자체 IP와 Linux 공개 배포 버전

기본 .10/.20의 자동 할당 제외는 계속 유지한다. 기본 VM 복원 시에는
.first-install-state.json의 완료된 UUID·실제 MAC 계획, storage UUID,
.xenova-install.json의 factory ID·원래 주소를 실제 등록/보관 레코드와 대조한다.
defaults.json의 예시 MAC으로 보드별 MAC을 되돌리거나 예시 MAC 일치를 요구하지 않는다.
자체 기본 예약을 제외한 뒤에도 다른 UUID의 소유권과 다른 MAC의 DHCP 예약/lease는
계속 충돌로 처리한다. 소유권을 확인할 기록이 없으면 자체 예약으로 추정하지 않는다.

공개 배포 버전은 manifest.template_version을 우선하고 이 필드가 없을 때만
legacy manifest.version을 사용한다. 두 값이 다르더라도 명시적 template_version이
정확한 카탈로그 버전이면 이를 보존하며 원본 legacy 필드나 자산을 덮어쓰지 않는다.
명시적 필드가 null/잘못된 형식이면 legacy 값으로 우회하지 않고 준비 전에 거부한다.
Linux make_instance도 같은 판정 함수를 사용하며 이미 선택한 버전과 다르면
workspace/디스크 준비 전에 거부한다. 게시 후 Manager는 선택 ID·버전·kind 일치를
다시 검사하고 불일치 자산을 성공으로 반환하지 않는다. 게시된 디스크는 자동 삭제하지 않는다.
