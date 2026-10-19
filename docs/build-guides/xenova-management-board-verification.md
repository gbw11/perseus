# 후속 실보드 수락 절차

이 절차는 새 dom0 빌드·최종 조립·배포의 별도 승인 후 수행한다.
실제 기본 VM/기존 사용자 데이터 대신 명시적으로 만든 폐기 가능한 테스트 VM으로 삭제를 시험한다.
각 단계의 명령·종료 코드·로그를 별도로 보관한다. 실패하면 다음 변경 단계로 진행하지 않는다.
소스/모의 파일 검사, ARM64 바이너리 설정 파싱, 실제 VM 실행/앱 동작을 분리해 기록한다.

## 1. 이미지와 관리 접속

먼저 이전 문서 xenova-wifi-access.md의 UART 설정과 IP 확인을 따른다.
Wi-Fi 미설정/AP 부재/인증 실패에서도 내부 VM 자동 시작이 완료되는지 각각 확인한다.
재부팅 후 실제 무선 주소, sshd 리스너, PC→dom0 로그인, ProxyJump를 따로 확인한다.

    systemctl status sshdgenkeys.service sshd.service --no-pager
    ss -lntp
    xenova-wifi status --interface wlan0
    xenova-vm status

systemctl --failed=0만으로 SSH를 통과시키지 않는다. 실제 PC 로그인이 필요하다.
기존 machine-id/호스트 키/게스트 시험 파일을 이전의 성공한 수집 결과와 대조한다.
명령이 성공한 뒤에만 비교 파일을 확정한다. 실패로 생긴 빈 파일과 비교하지 않는다.

## 2. 정확한 템플릿 선택과 계획

    xenova-vm templates --json
    xenova-vm list --json

사용할 template_id/version/manifest 해시와 Spring에 먼저 저장한 UUID를 기록한다.
Linux IP는 플랫폼이 .2..99에서 선택하며 기본 .10/.20과 사용/보관/예약 주소를 제외한다.
표시 이름은 한글·공백을 사용할 수 있고 내부 경로·Xen 이름은 UUID에서 유도된다.

    vm_id="<Spring에 먼저 저장한 UUID>"
    xenova-vm create --vm-id "$vm_id" --name "팀 시험 Ubuntu" \
        --template-id ubuntu-24.04-arm64 --template-version 1.0.0 \
        --vcpus 1 --memory-mib 512 --auto-ip --json
    echo $?

계획 후 instances/operations에 새 자산·예약이 생기지 않는지 확인한다.
실제 생성은 같은 명령에 --execute를 붙인다. stdout JSON을 파일로 수집할 경우:
    <생성 명령과 --execute> > create.tmp.json
    rc=$?
    echo "$rc"
    test "$rc" -eq 0 || exit "$rc"
    mv create.tmp.json create.json

data.vm_id와 operation_id를 기록한다. 생성 직후 정지/등록 상태를 확인한다.
동일 UUID·명세를 재호출해 vm_id/operation_id/IP와 디스크 inode가 같고 replayed=true인지 확인한다.
같은 UUID·다른 memory_mib는 IDEMPOTENCY_CONFLICT여야 한다.
다른 UUID·같은 표시 이름은 독립 VM으로 등록되어야 한다. 폐기 가능한 두 번째 UUID로만 시험한다.
create/list/get의 template_id/template_version/guest_kind와 배정 IP를 서로 대조한다.
Zephyr의 별도 생성은 1 vCPU/16MiB, ipv4=null이고 disk/VIF가 없어야 한다.
이 검사는 실제 펌웨어 앱 실행/인터럽트 호환성 시험과 구분한다.
create가 반환됐다는 사실과 실제 guest OS/SSH 준비는 구분한다.

## 3. 실행·보존·보관·복원

    xenova-vm start <UUID> --json
    xenova-vm get <UUID> --json

실제 게스트에 접속해 시험 파일을 저장하고 수집 명령의 성공을 확인한다.
정상 종료를 요청하고 domain-removed를 확인한다.
    xenova-vm stop <UUID> --json
    xenova-vm retire <UUID> --idempotency-key board-retire-1 --execute --json
    xenova-vm list --lifecycle RETIRED --json
    xenova-vm restore <UUID> --idempotency-key board-restore-1 --execute --json

보관 중 같은 UUID의 create는 TARGET_CHANGED여야 하고 IP는 다른 VM에 재배정되지 않아야 한다.
복원 직후에는 실행되지 않아야 한다. UUID/MAC/IP/디스크/키/파일/자동 시작 정책을 대조한다.
다시 시작해 실제 SSH와 파일 보존을 확인한다.
과거 retire key를 재전송해 복원된 VM이 다시 보관되지 않는지 확인한다.
자원 현재 설정과 creation_spec이 구분되는지 확인한다.

## 4. 영구 삭제와 작업 기록

테스트 VM을 정지→새 key로 retire한 뒤 UUID를 다시 대조한다.
    xenova-vm purge <UUID> --confirm-vm-id <UUID> --idempotency-key board-purge-1 --json
    xenova-vm purge <UUID> --confirm-vm-id <UUID> --idempotency-key board-purge-1 --execute --json
    xenova-vm get <UUID> --json
    xenova-vm operation --idempotency-key board-purge-1 --json

논리 bytes/실제 할당 bytes/free_bytes_delta와 실제 df 여유 변화를 함께 기록한다.
tombstone과 작업 결과가 남아야 한다. 오래된 create를 재전송해 VM_PURGED이며 재생성되지 않아야 한다.
purge 이후 새 UUID의 생성에서 이전 IP는 현재 lease/다른 소유권이 없을 때만 다시 선택 가능하다.
오래된 성공 작업의 IP 기록만으로 영구 점유하지 않아야 한다.
다른 VM·원본 템플릿의 해시/파일은 변하지 않아야 한다.
실행 중 삭제, Xen 디스크 참조, 마운트/loop 사용 상태에서는 거부되어야 한다.

중단 주입은 폐기 가능한 환경에 한정하고 임의 SD 전원 차단으로 기존 데이터를 시험하지 않는다.
준비 중단은 RECOVERY_REQUIRED와 소유 작업 경로를 남겨야 한다.
게시 후 응답 소실은 같은 UUID로 복원돼야 한다. purge 중단은 원래 key로만 이어가고
부분 삭제 중 restore는 거부돼야 한다. 작업 조회의 ok와 작업 status를 따로 판정한다.

## 5. 기존 수락 회귀

세 기본 VM이 실행 중인 상태에서 일반 reboot-board를 한 번 실행한다.
Linux 정상 종료, 승인된 기본 Starter만 정책 강제 종료, 도메인 제거 확인,
저장소 정리, 하드웨어 리셋, 이후 자동 시작을 단계별로 기록한다.
재부팅 후 dom0/게스트 SSH 자동 활성화와 기존 식별자·키·시험 파일 보존을 확인한다.
간헐 경쟁조건은 반복 횟수와 각 결과를 기록하고 poweroff는 별도로 시험한다.
임의 새 Zephyr VM의 기본 refuse 정책을 autostart=on과 혼동하지 않는다.
직접 systemctl 종료는 이미 진행된 종료를 ExecStop 실패로 취소할 수 없다는 한계를 유지한다.
