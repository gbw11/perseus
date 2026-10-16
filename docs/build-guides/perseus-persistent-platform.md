# Perseus 영구 Xen 플랫폼 계약과 후속 수락 시험

공통 SD 설치·64/128GB 확장·Pi 단독 최초 생성의 최신 계약은 [공통 최초 설치 이미지](perseus-first-install.md)를 따른다. 아래 문서의 초기 수동 저장소 준비 절차와 Alpine 계획은 당시 구현 기록이며, 현재 활성 템플릿은 Ubuntu·Debian이고 Alpine은 확장 구조만 유지한다.

## 현재 단계와 설계 결정

2026-10-16, WSL Ubuntu-24.04의 `~/S15P31E110`, `feat/platform/yocto-build-setup`에서 1차 소스 감사 수정본을 유지한 후 확장했다. 이번 단계는 소스·정적 검증이며 새 dom0 이미지, Ubuntu/Debian 템플릿, 인스턴스 디스크를 생성하지 않았다. 기존 VM·SD·빌드·캐시·외부 레이어 커밋도 변경하지 않는다. `root.raw` 테스트 fixture는 몇 바이트의 일반 파일이며 OS 이미지가 아니다.

| 계약 | 결정 |
| --- | --- |
| 기존 이미지 타깃 | `perseus-xen-bringup-image`에 영구 플랫폼 패키지 추가. BusyBox는 회귀 자산으로만 내장 |
| 부팅 | 기존 MMC 선택·Control FDT 복사 유지. Xen capability·privcmd·xl 검사 실패 시 VM 기동 거부 |
| 내부 네트워크 | Perseus가 `xenbr0=192.168.77.1/24`, Xen hotplug가 VIF 연결, networkd는 비관리 |
| 저장소 | 별도 ext4, `LABEL=perseus-data`, dom0 `/srv/perseus`. 마운트·등록 표식 없으면 실패 |
| 디스크 | 인스턴스별 단일 ext4 raw 파일을 `xvda`에 `qdisk` backend로 연결. 매 시작 복제 없음 |
| 실행 설정 원본 | `/srv/perseus/instances/<id>/instance.json`; `/run/perseus/<id>.cfg`는 실행용 파생 설정 |
| 자동 시작 | `perseus-vms.service` 하나만 시작 소유. 이미지에서 `xendomains.service` 마스킹 |
| 재시작 정책 | 사용자 정지·게스트 crash를 자동 복구하지 않음. 명시한 게스트 reboot만 Xen restart |
| 최초 로그인 | dom0·Ubuntu·Debian 콘솔/SSH `root / 1234`, 빈 암호 거부. 재부팅 때 암호 설정 안 함 |
| 준비 완료 | `xl create` 성공은 도메인 생성만 의미. SSH·OS·앱 준비 완료는 수락 시험에서 별도 확인 |

기본 VM은 Ubuntu 768MiB/1vCPU, Debian 512MiB/1vCPU, 기존 dom0 1024MiB다. **사용자가 보드 RAM 8GB임을 확인했다.** 기존 배분은 보수적인 초기값으로 유지하며 실보드 측정 전 성능 보장은 아니다. 초기 raw 디스크는 VM당 8GiB, 템플릿 2개와 인스턴스 2개 합계 논리 용량 32GiB다. 준비 중 원본 변환·작업 복사본에도 공간이 필요하므로 WSL 호스트에서 충분한 공간을 확보한다. sparse 파일의 겉보기 크기와 실제 사용량도 구분한다.

새 관리 프로그램은 기존 Python 코어에 더해 표준 JSON 모듈을 사용한다. 타깃 실행 검사에서 기존 이미지에는 `json`이 없음을 확인했다. `python3-json`은 고정 OE-core의 패키지이며 외부 Python 라이브러리/버전 변경이 아니다. 2026-10-16 사용자 승인에 따라 `perseus-platform_1.0.bb`의 RDEPENDS에 반영했다. 실제 패키지 설치는 이후 승인받을 이미지 빌드 단계에서 이루어진다.

## 영구 데이터와 고유 식별자

```text
/srv/perseus/                         # 별도 ext4 마운트. dom0 rootfs 폴더 대체 금지
  .perseus-storage.json               # 명시적 storage-init에서 한 번 만든 UUID 표식
  templates/<template-id>/            # 새 VM 생성용 원본. 기존 경로 덮어쓰기 거부
    root.raw, Image, initrd, kernel.config, boot.json, template.json
  instances/<id>/                     # 팀원이 사용하는 쓰기 가능한 VM
    root.raw                         # 정상 시작/종료/재부팅에서 재사용
    Image, initrd, kernel.config      # 해당 인스턴스가 고정한 부팅 세트
    boot.json                        # kernel/initrd/config 해시와 원본 출처
    instance.json                    # UUID/name/MAC/hostname/IP/자원/autostart
```

기본 인스턴스는 `ubuntu-dev`(192.168.77.10, MAC 02:16:3e:77:00:10), `debian-dev`(192.168.77.20, MAC 02:16:3e:77:00:20)다. 기본값은 `recipes-core/perseus-platform/files/defaults.json`에서 관리하고 최초 인스턴스 생성 시 복사한다. 그 뒤 설정 원본은 영구 `instance.json`이다. UUID는 최초 생성 시 새로 부여한다. domid·`vif1.0`은 실행 상태일 뿐 영구 식별자가 아니다.

registry 전체에서 id/UUID/name/hostname/MAC/IP 중복, raw 파일 symlink·hardlink 중복, 실행 중인 다른 도메인의 동일 디스크 사용을 검사한다. xl long-list가 설정을 누락한 경우 짧은 목록과 대조하여 시작을 거부한다. 운영 경로는 `perseus-vm`으로 통일한다. root가 직접 `xl`이나 디스크 파일을 조작해 이 경계를 우회하는 동작까지 방지하는 보안 격리는 아니다.

`start`, `stop`, `reboot`에 디스크 생성·복사·포맷·초기 암호 설정이 없다. 템플릿 업데이트를 게시해도 기존 인스턴스는 바뀌지 않는다. 삭제·초기화 명령은 제공하지 않으며 별도 백업/폐기 절차로 다룬다. 디스크가 누락되면 빈 디스크를 만들어 복구하지 않는다.

## Ubuntu·Debian 부팅 자산의 출처

단일 기준은 `platform/guests/sources.lock.json`이다. 원본 파일 다운로드는 이번에 수행하지 않았으며 공식 텍스트 메타데이터와 체크섬만 조회했다.

| 템플릿 | 공식 고정 입력 | 커널/모듈 세트 |
| --- | --- | --- |
| Ubuntu 24.04, 20260926 | Canonical ARM64 cloud QCOW2, SHA-256 고정 | `linux-image-6.8.0-142-generic`, `6.8.0-142.142` |
| Debian 13, 20261015-2632 | Debian ARM64 **generic** QCOW2, SHA-512 고정 | `linux-image-6.12.111+deb13-arm64`, `6.12.111-1` |

Ubuntu는 [고정 릴리스](https://cloud-images.ubuntu.com/releases/noble/release-20260926/), [체크섬](https://cloud-images.ubuntu.com/releases/noble/release-20260926/SHA256SUMS), [패키지 manifest](https://cloud-images.ubuntu.com/releases/noble/release-20260926/ubuntu-24.04-server-cloudimg-arm64.manifest)를 확인했다. Debian은 [고정 빌드](https://cloud.debian.org/images/cloud/trixie/20261015-2632/), [체크섬](https://cloud.debian.org/images/cloud/trixie/20261015-2632/SHA512SUMS), [공식 패키지 메타데이터](https://cloud.debian.org/images/cloud/trixie/20261015-2632/debian-13-generic-arm64-20261015-2632.json)를 HTTPS로 읽었다. Debian 브라우징 도구 조회는 실패하여 WSL HTTPS 요청으로 내용을 확인했다. `latest`는 생성 입력으로 쓰지 않는다.

Debian [Cloud 설명](https://wiki.debian.org/Cloud)의 일반 커널 포함 `generic`을 선택했다. [Xen cloud-image 안내](https://wiki.debian.org/Xen/InstallDebianGuestFromCloudImages)는 genericcloud의 드라이버 제한도 설명하지만 가이드 자체에 미검증 부분이 있으므로 ARM64 실보드 호환 보증으로 해석하지 않는다. Ubuntu 24.04와 Debian 13은 각 배포판의 공식 generic 커널을 선택하며 dom0 커널을 자동 재사용하지 않는다.

템플릿 준비 도구는 검증한 원본에서 rootfs·커널·모듈을 함께 가져오고 같은 rootfs에서 initramfs를 갱신한다. XEN/HVC_XEN/BLK_DEV_INITRD/DEVTMPFS 내장 여부와 Xen block/net frontend·ext4 지원을 검사한다. 모듈이면 initramfs에 들어 있는지도 검사하고 Arm64 Image magic·커널 패키지 버전을 확인한다. 새 자산의 해시는 생성 후 `boot.json`에 기록한다. 아직 생성하지 않은 자산의 해시를 미리 만들어 넣지 않았다.

root는 파티션 없는 `/dev/xvda`다. 새 영구 게스트는 `net.ifnames=0`으로 단일 VIF를 `eth0`로 유지해 초기 networkd 매칭과 일치시킨다. 클라우드 원본의 EFI/fstab/cloud-init 설정은 **새 템플릿 준비 때만** 교체·비활성화한다. Xen Linux shutdown control과 systemd의 정상 종료, hvc0 콘솔, SSH는 후속 하드웨어 시험 대상이다. 게스트의 커널 패키지는 초기 버전으로 hold한다. 앱 업데이트는 자유롭게 하되, 게스트 커널을 바꿀 때는 외부 Image/initrd와 디스크 내부 모듈을 함께 갱신하는 별도 오프라인 검증이 필요하다.

Alpine은 같은 bundle/인스턴스 계약으로 향후 추가할 수 있다. 현재 생성 도구의 게스트 설정은 Debian 계열 systemd/initramfs-tools 전용이며 Alpine rootfs를 그대로 넣어 실행할 수 있다는 뜻은 아니다. Alpine에는 별도 OS 준비 adapter와 커널 검증이 필요하다.

## 생성·게시 경계와 호스트 도구

`scripts/prepare-perseus-guests.py`는 기본이 **PLAN ONLY**다. `--execute`가 없으면 다운로드·마운트·이미지 생성·초기화 작업을 하지 않는다. 실제 실행은 별도 승인 후 **보드가 꺼져 있고 해당 데이터를 다른 호스트/VM이 사용하지 않는 상태**에서 수행한다. 동시 프로비저닝은 호스트 파일 잠금으로 막는다.

필요한 호스트 도구는 템플릿 준비용 `qemu-img`, `losetup`, `mount/umount`, `blkid`, `mkfs.ext4`, `cp`, `chroot`; 인스턴스 준비용 `tune2fs`, `ssh-keygen`, 최종 SSH 검사용 ARM64 chroot 등이다. 이번 WSL에서는 mount/losetup/mkfs.ext4/OpenSSL 등은 확인했지만 `qemu-img`와 `qemu-aarch64-static`은 PATH에 없었다. 설치하지 않았다. ARM64 네이티브 호스트 또는 ARM64 chroot를 실행할 QEMU/binfmt 환경이 필요하며, 없으면 생성 도구가 실패한다. 필요하면 생성 단계 전에 호스트 개발 도구 설치를 별도 승인받는다. zstd 압축 커널이면 `zstd`도 필요하다. dom0 런타임에는 이 생성 도구를 설치하지 않는다.

도구는 검증한 QCOW2를 명시적 포맷으로 private raw에 변환하고 readonly loop로 원본 root를 읽는다. 새 일반 파일에만 ext4를 만들며 물리 디스크 경로를 출력으로 받지 않는다. 작업 중에는 입력·기존 출력·기존 인스턴스를 갱신하지 않는다. 기존 출력 경로는 거부한다. 마운트와 loop를 해제한 뒤에만 완성 디렉터리를 게시한다. 해제 실패 시 작업 경로를 보존하고 오류를 낸다. 임시 작업 경로도 자동 삭제하지 않아 운영자가 확인할 수 있다.

아래는 **계획 출력 예시**다. 실제 실행 명령은 이번에 실행하지 않았다.

```bash
python3 -B scripts/prepare-perseus-guests.py template \
  --source ubuntu-24.04-20260926 --input /absolute/downloads/ubuntu-arm64.img \
  --output /absolute/staging/templates/ubuntu-24.04-20260926

python3 -B scripts/prepare-perseus-guests.py template \
  --source debian-13-20261015 --input /absolute/downloads/debian-arm64.qcow2 \
  --output /absolute/staging/templates/debian-13-20261015

python3 -B scripts/prepare-perseus-guests.py storage-init --data-root /mnt/perseus-data
python3 -B scripts/prepare-perseus-guests.py instance --data-root /mnt/perseus-data --id ubuntu-dev
python3 -B scripts/prepare-perseus-guests.py instance --data-root /mnt/perseus-data --id debian-dev
```

실제 게시 위치는 승인 후 마운트한 데이터 영역의 `templates/<id>`다. 템플릿 명령의 `--output`을 그 **새 경로**로 지정하면 생성·검증·게시가 이어진다. `storage-init`은 이미 마운트한 빈 ext4에 등록 표식과 디렉터리만 만들며 포맷·파티션·마운트는 수행하지 않는다. label 지정과 데이터 매체 준비는 별도 승인 작업이다. SD 남는 영역을 이번 소스 작업에서 자동 사용하지 않는다.

## 부팅·네트워크·인증 순서

1. U-Boot가 Control FDT 복사본으로 Xen → dom0를 부팅한다. 일반 Linux로 fallback한 경우도 Xen 준비 검사가 실패하여 VM은 시작하지 않는다.
2. 브릿지/DHCP 서비스는 기본 활성화되어 외부 LAN·인터넷·VM 데이터 저장소가 없어도 내부 네트워크를 준비한다. DHCP 전 검사에서 물리 NIC/예상치 못한 포트가 발견되면 시작을 거부한다.
3. dnsmasq는 `/usr/bin/dnsmasq`로 실행하고 프로세스가 UDP/67을 여는지 최대 5초 검사한다.
4. `/srv/perseus`가 준비되고 Xen gate가 통과하면 종료 보호 서비스가 먼저 armed 상태가 된다. DHCP 준비 후 등록 VM의 고정 MAC/IP 예약을 `/run/perseus/dhcp-hosts`로 쓰고 reload한 뒤 자동 시작한다.
5. 기본 두 VM이 아직 최초 생성되지 않았으면 성공으로 처리하지 않는다. 어느 VM이 실패해도 이름과 오류를 남기고, 이미 시작된 VM은 종료 보호 서비스가 관리한다.

초기 인증은 이미지/템플릿 최초 준비 시 설정한다. 인스턴스 생성은 고유 hostname·machine-id·SSH 호스트 키를 부여한다. 부팅 때 이를 다시 설정하는 서비스는 없다. dom0의 `/etc/shadow`, machine-id, `/etc/ssh` 키는 쓰기 가능한 기존 rootfs에서 유지된다. 사용자 암호 변경과 앱·Agent 설정은 다음 부팅에 덮어쓰지 않는다.

OpenSSH는 [처음 읽은 설정값을 사용](https://man.openbsd.org/sshd_config.5)하므로 `Include /etc/ssh/perseus-auth.conf`를 기존 include 앞에 둔다. `AuthenticationMethods any`로 공개키 필수 조건을 두지 않고 root 비밀번호 인증을 허용하며 빈 암호는 거부한다. 게스트 인스턴스 최초 생성에서는 고유 호스트 키를 만든 뒤 `sshd -T -C user=root,...`의 최종 값도 검사한다. 공유 키를 제거한 템플릿에 full 키 검사를 실행하지 않는다. 최종 dom0 이미지 역시 후속 시험에서 같은 방식으로 검사한다. 초기값은 팀 개발망 전용 `root / 1234`이며 공인 인터넷에 SSH를 노출하지 않는다.

노트북은 기존 외부 관리 LAN의 dom0 주소로 접속하고, 내부 VM에는 SSH jump를 사용한다.

```bash
ssh root@<dom0-management-ip>
ssh -J root@<dom0-management-ip> root@192.168.77.10
ssh -J root@<dom0-management-ip> root@192.168.77.20
scp -o ProxyJump=root@<dom0-management-ip> ./app.tar.gz root@192.168.77.10:/root/
```

물리 NIC를 xenbr0에 연결하거나 NAT/default route를 자동 추가하지 않는다. 외부 LAN이 없을 때에도 부팅·내부 VM 통신은 가능하며 UART 콘솔로 운영할 수 있다. 노트북과 dom0 사이의 직접 링크에 관리 IP를 부여하는 정책은 현장 LAN에 맞춰 별도로 구성한다. **내부 게스트의 직접 인터넷 다운로드는 기본 비활성**이다. 패키지는 jump SSH/scp로 오프라인 전달하거나, 관리자가 승인한 노트북의 패키지 프록시를 SSH reverse-forward한 뒤 해당 apt 명령에만 프록시를 지정한다. 기본 네트워크 정책을 바꾸지 않는다.

## 생명주기와 보드 종료

```bash
perseus-vm status
perseus-vm start ubuntu-dev
perseus-vm stop ubuntu-dev
perseus-vm reboot ubuntu-dev
perseus-vm autostart-set ubuntu-dev off
perseus-vm autostart-set ubuntu-dev on
perseus-vm reboot-board
perseus-vm poweroff
```

`stop`은 `xl shutdown UUID` 후 도메인 소멸을 최대 90초 기다린다. 자동 복구·destroy로 바꾸지 않는다. `reboot`은 `xl reboot UUID`를 요청하고 같은 UUID의 새 domid가 관측되는지 제한 시간 내 확인한다. OS/SSH 준비 완료는 별도다. 강제 제거는 `perseus-vm destroy <id> --force`로 분리했으며 정상 종료 성공으로 기록하지 않는다. 디스크 삭제/초기화는 수행하지 않는다.

보드의 정상 종료 경로는 `perseus-vm poweroff`/`reboot-board`다. 게스트 모두 정상 종료 → `sync` → VM 서비스 중지 → 저장소 unmount → 보드 종료 요청 순서다. 게스트 종료/저장소 해제 실패 시 종료 요청을 하지 않고 오류를 반환한다. 종료 표식이 생성된 뒤에는 새 시작을 거부한다.

일반 systemd 종료에도 독립된 `perseus-vm-shutdown.service`가 자동 시작 성공 여부와 무관하게 등록 게스트를 정리하도록 순서를 잡았다. 다만 **systemd의 ExecStop 실패 자체는 이미 진행 중인 호스트 shutdown을 취소하지 못한다**. 따라서 직접 `systemctl poweroff/reboot`, 전원 차단, 장치 강제 제거에는 위의 실패 시 중단 보장을 적용할 수 없다. 90초 제한/110초 서비스 제한을 넘으면 오류를 남기며 정상 종료라고 보고하지 않는다. 이 경계를 피하려고 무기한 종료를 막거나 강제 destroy를 자동 수행하지 않는다.

## 앱·Agent 배포 경계

팀원은 기존 인스턴스에 앱을 복사하고 배포판의 `/etc/systemd/system/`에 서비스를 등록할 수 있다. 작업 파일·패키지·서비스는 해당 `root.raw`에 저장된다. 앱 수정에 dom0 이미지/게스트 전체 재생성이 필요하지 않다. 이번에는 실제 Agent·앱·API·heartbeat를 구현하지 않았다. 새 SD 이미지로 전체 카드를 덮어쓰면 데이터 보존을 보장하지 않는다. 정상 재부팅 보존과 전체 매체 재기록 보존은 별개이며, 후자는 오프라인 백업·복원 또는 별도 데이터 매체를 포함한 업데이트 절차가 먼저 필요하다.

## 재실행 가능한 정적 검사

```bash
cd ~/S15P31E110
bash scripts/check-perseus-platform.sh
bash scripts/check-perseus-platform.sh --metadata
```

기본 명령은 일반 파일 fixture와 명령 대역으로 생명주기·보존·격리·최초 설정 계약, 기존 bring-up 회귀, 셸 문법을 검사한다. `--metadata`는 `/tmp/perseus-platform-static.*`에 설정/파싱 상태를 격리하고 `bitbake -p`, `bitbake -e`만 수행한다. 이미지/태스크·다운로드를 실행하지 않으며 기존 빌드·캐시에 쓰지 않는다. 이 절차는 저장소에 남고 `/tmp` 결과에 의존하지 않는다.

## 별도 승인 후 빌드·실보드 수락 시험

| 시험 | 절차와 성공 조건 |
| --- | --- |
| 최종 이미지 정적 검사 | 새 boot.scr·서비스 enable/mask·원본 boot/network 우선순위·dnsmasq 경로 확인. sshd 최종 값과 nonempty root hash 확인 |
| 게스트 자산 | 공식 입력 해시·커널 config/모듈·initramfs·ARM64 magic·게시 해시 통과. Ubuntu/Debian 둘 다 OS별 생성 검증 |
| 전원 인가 | 수동 U-Boot·bridge·DHCP 명령 없이 Xen/dom0/내부 네트워크 준비. 네트워크 케이블 없이도 플랫폼 부팅 |
| VM 자동 시작 | 두 기본 인스턴스가 각각 콘솔/SSH `root / 1234` 로그인. 각 UUID·이름·MAC·IP·machine-id·호스트 키 구분 |
| 작업 보존 | 각 VM에 파일·패키지·테스트 systemd 서비스 설치 → VM stop/start/reboot → 보드 정상 reboot 뒤 내용·설치·서비스 유지 |
| 인증 보존 | `passwd`로 변경 → VM·보드 재부팅 → 새 암호 유지, 1234로 되돌아가지 않음. dom0도 동일 시험 |
| 생명주기 | 같은 start 두 번에 도메인 하나. stop 후 즉시 부활 없음. autostart off 후 다음 보드 부팅에 해당 VM 미시작 |
| 네트워크 | 고정 예약 유지·dom0↔VM 및 VM↔VM 통신. networkd 재시작 후에도 NIC 분리·고정 IP·VIF 부착 유지 |
| 물리 NIC 방어 | 별도 통제 시험에서 잘못 연결된 포트가 있을 때 DHCP 시작 실패, 원인과 포트명 로그 확인. 정상 구성 복원 후 재시험 |
| 저장소 누락 | 종료 상태에서 데이터 매체 제거 후 부팅. 기본 VM 기동 실패가 명확하고 fallback raw가 생성되지 않음 |
| Xen 실패 | Xen 없이 Linux가 켜진 경우 gate 실패·VM 생성 없음·원인 로그 확인 |
| VM/DHCP 실패 | 누락/손상 자산, DHCP inactive/UDP67 미준비, 잘못된 게스트 기동을 각각 식별. 성공으로 기록하지 않음 |
| 정상 종료 한계 | 게스트 종료 지연/무응답에서 제한시간 초과 오류. 관리 보드 종료 명령이 전원 종료를 요청하지 않음. 강제 제거와 구분 |

확인 명령은 `systemctl --failed`, `journalctl -b -u perseus-vms -u perseus-vm-shutdown -u perseus-dhcp`, `perseus-vm status`, `xl list -l`, `bridge link`, `ip -br addr`, `sshd -T -C user=root,...`다. `xl list`만으로 게스트 앱이 정상이라고 판단하지 않는다.

JSON 런타임 패키지 추가는 승인·반영했다. 다음 단계의 승인 경계는 (1) 필요한 호스트 도구 설치, (2) dom0 BitBake 빌드 및 Ubuntu/Debian 템플릿 생성, (3) 선택한 데이터 매체 준비·게시·SD 배포, (4) 실제 VM/보드 수락 시험, (5) commit/push다. 이번에는 어느 생성·배포 명령도 실행하지 않는다.

## 소스 감사와 검증 기록

기존 고정 Xen 소스의 `tools/xl/xl_info.c:list_domains_details()` 및 `tools/libs/light/libxl_types.idl`에서 long-list의 `domid`, `config.c_info.uuid/name`, `config.disks[].pdev_path`를 확인했다. long-list가 설정 조회 실패 도메인을 생략하므로 관리기는 짧은 목록과 대조한다. 새 API/DTO를 가정해 만들지 않았다.

기존 `python3-core`만으로 실행한 타깃 QEMU 검사는 `ModuleNotFoundError: json`으로 실패했다. 기존 빌드의 `packages-split/python3-json`을 **임시 PYTHONPATH로만** 참조한 검사에서는 타깃 Python으로 CLI import/`--help`가 통과했다. 기존 rootfs에 패키지를 설치하거나 파일을 복사하지 않았다. 이 결과로 JSON 패키지 의존성을 확인했고 사용자 승인 후 레시피에 선언했다. 기존 rootfs에 패키지를 설치하거나 이미지를 새로 생성한 것은 아니다.
