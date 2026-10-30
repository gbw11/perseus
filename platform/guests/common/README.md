# 공통 Linux 초기 설정과 Jira 27

## 현재 코드가 하는 일

초기 설정은 다음 두 경로다. 실행 중인 VM에 오프라인 준비 함수를 다시 호출하지 않는다.

| 단계 | 코드 | 동작 |
| --- | --- | --- |
| Linux 템플릿 준비 | `prepare_rootfs.prepare_template()` | DHCP용 networkd 설정, SSH 서비스 활성화, 공유 machine-id·SSH 키 제거 |
| 일반 VM 생성 | `prepare_instance.make_instance()` → `prepare_rootfs.prepare_instance()` | 독립 디스크에 호스트명, machine-id, UUID 기반 instance-id, 고유 SSH 호스트 키 주입 |
| 공통 SD용 seed 생성 | `prepare_rootfs.prepare_deployment_seed()` | 첫 부팅에 만들 식별자·키를 비운 상태로 identity 서비스 설치 |
| seed의 실제 첫 부팅 | `guest_identity.ensure()` | systemd가 생성한 machine-id 검사, SSH 키 생성·검증, instance-id와 identity 완료 표식 기록 |
| seed의 다음 부팅 | `guest_identity.ensure()` | 기존 식별자·키 검증. 암호·키를 초기화하지 않음 |

일반 VM의 설정은 **생성 시 오프라인 주입**, 네트워크 주소 획득은 **부팅 시 DHCP**다.
일반 VM에는 seed 전용 `identity-complete.json`이 없어도 정상이다.
seed의 해당 표식은 **식별자·SSH 키 준비 완료**만 뜻한다. 네트워크 연결이나 에이전트 준비 완료를 뜻하지 않는다.
`xenova_first_boot.py`는 dom0의 공통 SD 최초 등록 처리이며, domU 에이전트 설치기가 아니다.

## 인스턴스 디스크 준비와 검증

Linux 관리 경로는 템플릿 선택 때 metadata·manifest 신원·자산 크기·소유권·용량을 검사한다.
사전 전체 payload 해시 없이 원 작업과 예약을 기록한 뒤 준비한다. `make_instance()`는 선택된
`template_manifest_sha256`을 현재 manifest와 대조하고, 원본에서 신규 private 파일로
복사하는 동일 바이트 전체를 기대 SHA-256과 비교한다. `root.raw`·커널·initrd·boot.json·
kernel.config를 모두 검증한 뒤 파일시스템 UUID와 게스트 신원·SSH를 설정한다.

복사본은 원본과 다른 inode로 생성한다. sparse hole은 출력에서도 유지하고 해시에는
해당 길이의 0 바이트를 포함한다. hole 조회가 지원되지 않으면 전체 스트림을 검증하며
복사한다. 전역 검증 cache나 reflink를 사용하지 않는다.

원본의 dev/inode·크기·mtime/ctime·mode 등은 복사 전후와 게시 직전에 비교한다.
이 정보는 같은 준비 작업의 변경 guard이며 내용 검증은 전체 스트림 해시가 담당한다.
guest 설정으로 복사본이 바뀌는 것과 원본 변경은 구분한다. 원본 교체·해시 불일치·
I/O 오류·동기화 실패 시 게시하지 않고 작업 경로를 보존한다. 기존 출력은 덮어쓰지 않는다.
실행자 중단 뒤 부분 작업의 자동 초기화나 새 operation 발급도 하지 않는다.

관리 경로의 준비 실패는 기존 operation에 `RECOVERY_REQUIRED`와 원인을 기록한다.
실패 기록 저장이 미확정이면 응답에도 이를 남긴다. 같은 요청의 조회는 원 기록을 읽으며,
실패가 기록된 부분 workspace의 재실행은 최초 오류와 파일을 보존한 채 차단한다.
workspace 생성 전 실패와 이미 게시된 자산의 후속 등록은 같은 operation·예약으로
재확인한다. 정확한 상태·오류·재전달 계약은 [생성 계약](../../../docs/platform/cli-manager-spec.md#creation-contract)을 따른다.
Linux 계획 성공은 payload 무결성이나 생성 완료의 확인이 아니다.

기존 직접 준비 경로의 구형 Linux manifest가 `files`를 생략한 경우에는 기존
`root_sha256`와 boot manifest의 해시로 검증한다. 새 관리 카탈로그는 계속 완전한
`files` manifest를 요구한다. 준비 완료는 Xen 실행·OS 기동·Agent 연결 완료를 뜻하지 않는다.

## Linux Bash 프롬프트

새 Linux VM의 준비 레코드에 있는 `display_name`을 게스트의
`/etc/xenova/display-name`에 데이터로 전달한다. 값이 없는 구형 레코드는 기존
`name`을 사용한다. 일반 VM과 factory seed 모두 같은 Bash 초기화 코드를 설치한다.

```text
[주행 제어 A] root@vm-a13f0b2c:~#
```

`vm-a13f0b2c`는 **VM UUID 앞 8자리를 보여주는 프롬프트용 표기**다.
실제 `/etc/hostname`, Xen 도메인 이름, UUID·MAC·IP·SSH 키는 변경하지 않는다.
일반 VM의 `/etc/xenova/prompt-uuid`에는 이미 할당된 UUID를 기록하고,
factory seed는 부팅 후 읽을 수 있는 Xen UUID 또는 기존 `instance-id`를 사용한다.
seed의 임시 UUID를 표시하거나 표시를 위해 새 UUID를 만들지 않는다.
유효한 UUID를 읽지 못하면 기존 프롬프트를 유지한다.

`/etc/profile.d/xenova-prompt.sh`와 시스템 `bash.bashrc`의 source 연결로
로그인·비로그인 대화형 Bash의 기본 `promptvars` 설정에서 적용한다.
기존 `PROMPT_COMMAND`를 보존하고,
사용자 rc 파일의 기본 PS1 설정 뒤에 표시한다. 이름은 쉘 코드로 source하지 않으며
PS1의 고정 변수 참조를 통해 표시해 `$()`·백틱·따옴표·역슬래시를 실행하지 않는다.
비대화형 명령과 Bash 이외의 셸, Zephyr에는 적용하지 않는다.

이 설정은 새 인스턴스/seed 사본을 준비할 때 설치한다. 기존 VM에 준비 함수를
다시 실행해 적용하지 않는다. 현재 이름 변경 API나 실행 중인 게스트로의 이름
동기화 기능은 없으며, 향후 이름 변경 기능에서 반영 시점을 별도로 정한다.

## 현재 입력과 역할 분담

| 항목 | 현재 구현 | 후속 작업 |
| --- | --- | --- |
| VM ID | 관리 계층이 UUID를 생성하고 Xen UUID·instance-id로 연결 | 외부 API의 UUID 계약 유지 |
| 호스트명 | 현재 관리 계층은 `hostname = name`으로 설정 | 독립 hostname 입력이 필요하면 P3와 API 확장 합의 |
| IP·MAC | 관리 계층이 지정 IP·고유 MAC 등록, 게스트는 DHCP | P1의 브릿지·DHCP 예약과 일치 여부 검증 |
| role | 현재 Linux 생성 API에 독립 입력 없음 | P3와 ProvisioningSpec의 필드·전달 경로 합의 |
| 에이전트 | 이 공통 준비 코드에 설치·등록 경로 없음 | P3 산출물·설정·서비스 규약을 받아 P2 설치/활성화 연결. 99·105번과 연계 |
| 전체 준비 완료 | identity 전용 표식만 있음 | 호스트명·네트워크·에이전트 각 단계 성공/실패 및 재시도 상태 설계 |

현재 구현을 설명하는 표이며, 새 ProvisioningSpec을 확정한 계약은 아니다.
백엔드에는 `AgentEnvelope.java`의 `vmId`, `bootId`, `agentInstanceId`와 `AgentPayloads.java`의
OS·hostname·네트워크 inventory 수신 모델이 이미 있다.
이 수신 모델은 게스트에 설치할 에이전트 바이너리·설정 파일·systemd 서비스의 배포 규약을 대신하지 않는다.
P3 인계 시 게스트 `instance-id`에 들어가는 VM UUID와 메시지 `vmId`의 연결을 확인해야 한다.
현재 프로젝트의 VM1·VM2는 Zephyr, VM3는 Debian이다. 이 README의 Linux DHCP·systemd·SSH 절차를 Zephyr에 적용하지 않는다.
Ubuntu는 Linux 공통 코드 적용 대상이지만, 아래 QEMU 검증은 Debian만 수행한다. Alpine은 이번 범위에서 제외한다.

## QEMU 검증

검증 도구: `platform/tests/qemu_first_boot_probe.py`.
기존 QEMU lab(`/home/ssafy/xenova-qemu-28`)과 SSH 설정을 재사용한다. 일반 PC나 실보드에서 실행하는 설치 스크립트가 아니다.

```bash
# 기본 실행은 계획 출력만 한다.
python3 platform/tests/qemu_first_boot_probe.py
# WSL의 기존 QEMU lab에서 명시적으로 실행한다.
python3 platform/tests/qemu_first_boot_probe.py --execute
# 다른 미사용 DHCP 주소를 선택할 수도 있다.
python3 platform/tests/qemu_first_boot_probe.py --execute --address 192.168.77.31
```

실행 시 문서화된 개발용 게스트 암호를 터미널에서 입력한다. 파일에 저장하지 않는다.
새 Debian VM(512MiB, 1vCPU, 192.168.77.30)을 생성해 다음을 수집한다.

1. 입력한 호스트명·UUID 및 DHCP 주소와 SSH 서비스 상태.
2. 기존 Debian과 machine-id·instance-id·SSH 공개키 지문이 다른지.
3. 정상 재부팅으로 boot ID가 바뀌었는지.
4. 재부팅 후 식별자·SSH 키·검증용 파일이 보존되는지.
5. 검증 VM을 정상 종료·보관한 후 기존 세 VM의 등록 정보가 같은지.

각 실행은 새로운 이름을 사용하며 JSON 결과는 lab의 `logs/jira27-<token>.json`에 남는다.
실패 시 강제 종료·영구 삭제를 하지 않는다. `cleanup_error`가 있으면 남은 검증 VM을 확인해야 한다.
보관된 검증 디스크는 증거로 유지하므로 반복 실행 시 공간을 확인한다.
생성 요청은 느린 TCG 환경을 고려해 최대 1800초 기다린다.
클라이언트 시간 초과가 원격 작업 취소를 뜻하지 않는다. 먼저 작업 기록과 남은 프로세스를 확인한다.
실패 JSON이 있는 실행은 같은 주소와 `--resume-token <token>`으로 동일 멱등키를 재사용할 수 있다.
부분 디스크가 있으면 플랫폼이 `RECOVERY_REQUIRED`로 거부할 수 있으며, 도구는 이를 지우거나 초기화하지 않는다.
재부팅 명령이 오류를 반환했지만 이미 재부팅됐으면, 첫 부팅 스냅샷이 있는 실패 JSON을
`--observe-after-reboot <실패 JSON> --resume-token <token>`으로 지정해 재부팅을 재요청하지 않고 관찰·정리할 수 있다.
이때 설정 비교가 모두 통과해도 앞선 제어 API 오류를 보존하여 전체 `passed`는 실패로 유지한다.

이 검증은 일반 VM의 기존 오프라인 주입 경로를 검사한다.
공통 seed의 실제 첫 부팅, Ubuntu 실기동, 에이전트 설치·등록, Zephyr 애플리케이션 설정, Raspberry Pi 실보드 수락은 별도로 검증해야 한다.
QEMU의 실행 시간은 실보드 성능 측정값으로 사용하지 않는다.

## 27번을 마무리하려면

1. 기존 두 초기 설정 경로를 유지하고, Jira의 “첫 부팅 설정”에 생성 시 오프라인 주입을 포함하는지 수락 기준에 명시한다.
2. P3와 role·에이전트 설정을 포함한 입력 규약과 전달 방법을 확정한다. 현재 관리 API에 없는 필드를 임의로 넣지 않는다.
3. P3가 제공하는 에이전트 산출물과 서비스 규약을 설치·활성화 단계에 연결한다.
4. identity 표식과 구분되는 전체 provisioning 상태를 정의하고, 실패 단계·재시도·완료 후 재실행 보존을 검증한다.
5. 최신 산출물로 seed 실제 첫 부팅과 필요한 OS별 적용, 실보드 검증 결과를 남긴다.

에이전트가 없는 현재 상태를 전체 provisioning 성공으로 기록하거나 Jira 27 완료로 처리하지 않는다.
