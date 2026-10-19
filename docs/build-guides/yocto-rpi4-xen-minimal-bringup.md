> **리브랜딩 이전 보존 문서입니다.** 아래 본문은 당시 Perseus 소스·산출물·검증 기록이며, 현재 명령과 경로는 [Zenova 플랫폼 가이드](zenova-platform.md)를 따릅니다.

# Raspberry Pi 4 + Xen — 커널·이미지·실보드 bring-up 검증

이 문서는 추가 커널 진단, 생성 이미지의 정적 검사, 실보드 Xen/dom0·최소 domU·격리 Bridge/DHCP 검증 절차를 다룬다. 환경 설치·Clone·업데이트·커널 설정 생성·필수 옵션 검사·이미지 빌드·domU 통합 계약은 [재구성 및 인계](yocto-rpi4-xen-reproduce.md)를 따른다.

**기존 이미지에서 수동 보정 후 Xen/dom0/BusyBox domU 부팅·DHCP·양방향 Ping에 성공했다는 사용자 보고가 있다.** [2026-10-16 1차 소스 감사](yocto-rpi4-xen-source-audit-20261016.md)에서 이를 소스에 반영했다. [2026-10-16 WIC 수정·빌드 검증](perseus-wic-xen-fix-20261016.md)에서 수정 이미지 빌드와 실제 산출물 검사를 완료했다. 수정 이미지의 실보드 자동 동작은 아직 재검증하지 않았다. 이전 정적 증거는 [2026-10-15 감사 기록](yocto-rpi4-xen-audit-20261015.md)에 보존하며, 아래 절차로 2차 빌드 후 자동 동작을 다시 확인한다.

## 원칙

현재 영구 플랫폼의 운영·인증·저장소·Ubuntu/Debian 수락 조건은 [영구 플랫폼 가이드](perseus-persistent-platform.md)를 따른다. 아래 BusyBox 게스트는 기본 자동 시작 VM을 대신하지 않는 회귀 시험이다.

- **기존 성공 이미지 `xen-image-minimal`을 보존**한다. 다른 타깃 `perseus-xen-bringup-image`를 추가하며 외부 Yocto 레이어·`layers.lock`·커널 설정은 유지한다. 확인된 부팅·네트워크 결함은 자체 레이어의 bbappend와 템플릿에서 수정한다.
- `perseus-domu-initramfs`로 **디스크가 없는 테스트 domU**를 만든다. Debian·Ubuntu·Alpine의 최종 게스트 구성이 아니며, Linux Arm64 커널 `Image`는 기존 dom0 커널을 **테스트용으로 재사용한다**. 이를 지원할 커널 옵션이 없으면 빌드를 진행하지 말고 게스트 커널부터 조정한다.
- `xenbr0 = 192.168.77.1/24`는 **물리 NIC에 연결하지 않은 고립된 브릿지**다. `perseus-dhcp.service`는 `192.168.77.100~150` 주소를 제공하며 DNS는 비활성화한다. `eth0`를 브릿지에 추가하지 않는다.
- 브릿지·DHCP는 **부팅 시 자동 시작한다**. `dnsmasq_%.bbappend`로 배포판 기본 `dnsmasq.service`는 비활성화하고, 격리된 `perseus-dhcp.service`만 사용한다. BusyBox 게스트 자체는 수동 회귀 시험이다.
- 이 이미지는 팀 개발망 최초 인증 **root / 1234**와 빈 암호 거부 설정을 갖고, `serial-getty@hvc0.service`를 활성화하려고 시도한다. 디버깅 목적이며 인터넷/공용망 연결 금지. 실제 배포 이미지에 사용하지 않는다. 특히 SSH 서비스가 제공되므로 신뢰할 수 없는 네트워크에 연결하지 않는다.

## 0. 입력과 실행 위치

재구성 문서에서 지정한 `PERSEUS_PROJECT`, `YOCTO_WORKSPACE`, `YOCTO_BUILD_DIR`를 사용한다. 호스트의 Ubuntu Bash와 실보드 dom0/게스트 콘솔에서 실행할 명령을 구분한다. 새 터미널에서는 자신의 실제 경로로 변수를 다시 지정한다. 최초 성공 빌드나 특정 사용자의 홈 경로는 필수 입력이 아니다.

| 단계 | 필요한 입력 |
| --- | --- |
| 커널 옵션 검사 | 현재 빌드의 커널 `.config`. 새 환경에는 아직 없을 수 있음 |
| 이미지 정적 검사 | 현재 빌드에서 생성된 bring-up 이미지·manifest·부트 자산 |
| 보드·VM 검증 | 정적 검사를 마친 SD 이미지와 Raspberry Pi 4 / UART |

`setup-yocto.sh --verify`만 완료한 환경에는 커널 `.config`나 이미지가 생성되어 있지 않아도 정상이다. 환경 준비만 하는 팀원은 이 문서의 이미지·보드 검증을 수행할 필요가 없다.

## 1. 호스트에서 추가 커널 진단

이 절은 최초 이미지 빌드의 필수 순서가 아니다. 커널 configure → 필수 옵션 검사 → 이미지 생성은 [재구성 문서 3절](yocto-rpi4-xen-reproduce.md#3-선택-실제-이미지-빌드와-산출물)에 모아 관리한다. 아래는 configure/빌드 후 dom0 문제를 조사할 때 선택적으로 실행하는 진단이다.

새 터미널에서는 0절의 경로 변수를 지정하고 현재 빌드 환경을 활성화한다.

```bash
source "$YOCTO_WORKSPACE/layers/openembedded-core/oe-init-build-env" "$YOCTO_BUILD_DIR"
```

추가 dom0 문제를 조사할 때는 해석된 커널 빌드 경로에서 blkback·grant·XenFS 등도 확인할 수 있다.

```bash
KERNEL_B="$(bitbake -e linux-raspberrypi | sed -n 's/^B="\([^"]*\)"$/\1/p')"
for symbol in XEN_BLKDEV_BACKEND XEN_GNTDEV XEN_GRANT_DEV_ALLOC XEN_WDT XENFS XEN_PRIVCMD; do
  grep -E "^(CONFIG_${symbol}=|# CONFIG_${symbol} is not set)" "$KERNEL_B/.config" ||
    printf '%s: not present\n' "$symbol"
done
```

## 2. 호스트에서 생성 이미지 정적 검사

이미지 생성은 [재구성 문서의 선택적 빌드 단계](yocto-rpi4-xen-reproduce.md#3-선택-실제-이미지-빌드와-산출물)를 따른다. 아래는 **생성된 이미지 파일을 읽는 검사**이며 원본 이미지·빌드 캐시를 삭제·수정하지 않는다.

### 산출물과 설치 패키지

안정된 이름의 심볼릭 링크가 가리키는 현재 이미지와 manifest를 선택한다. 서로 다른 빌드 시점의 파일을 섞지 않는다.

```bash
IMG_DIR="$YOCTO_BUILD_DIR/tmp/deploy/images/raspberrypi4-64"
IMAGE_PREFIX="$IMG_DIR/perseus-xen-bringup-image-raspberrypi4-64.rootfs"
SD_IMG="$IMAGE_PREFIX.rpi-sdimg"
BOOT_IMG="$IMAGE_PREFIX.vfat"
ROOTFS_IMG="$IMAGE_PREFIX.ext3"
MANIFEST="$IMAGE_PREFIX.manifest"

ls -lh "$SD_IMG" "$BOOT_IMG" "$ROOTFS_IMG" "$MANIFEST"
grep -E '^(xen-tools|perseus-bringup|dnsmasq|iproute2|kernel-module-bridge|kernel-module-xen-netback)' "$MANIFEST"
```

커널에서 bridge·netback을 `m`으로 설정했다면 대응 모듈 패키지가 manifest에 포함되어야 한다. `y`라면 별도 모듈 패키지는 필요하지 않다. 설치 목록과 실제 rootfs 파일 존재 여부를 함께 확인한다.

### FAT·SD 파티션과 부트 인자

`mdir`/`mtype`은 FAT 내용, `fdisk`/`sfdisk`는 파티션 정보, `debugfs`는 ext3 내부 파일 확인에 사용한다. 필요한 검사 도구가 없는 Ubuntu 호스트에서만 설치한다. 이 도구들은 이미지 검사에 사용하며 타깃 이미지의 런타임 의존성을 추가하지 않는다.

```bash
sudo apt install -y mtools fdisk e2fsprogs
```

```bash
mdir -i "$BOOT_IMG" ::/
strings "$IMG_DIR/boot.scr" | grep -E 'dom0_mem|fatload|root=PARTUUID'
fdisk -l "$SD_IMG"
```

FAT에서 `xen`, `Image`, `boot.scr`, `kernel8.img`, Pi 4 DTB 등을 확인한다. `boot.scr`의 dom0 메모리·rootfs 인자를 현재 프로젝트 설정과 대조하고, SD 첫 파티션이 FAT 부트 영역, 두 번째가 Linux rootfs인지 확인한다.

최종 SD 이미지 안쪽을 검사할 때는 과거 이미지의 고정 오프셋을 재사용하지 않고 **현재 이미지의 첫 파티션 시작 섹터 × 섹터 크기**로 계산한다.

```bash
BOOT_OFFSET="$(python3 - "$SD_IMG" <<'PYOFFSET'
import json
import subprocess
import sys

table = json.loads(subprocess.check_output(["sfdisk", "--json", sys.argv[1]]))["partitiontable"]
if table["unit"] != "sectors" or not table.get("partitions"):
    sys.exit("Cannot determine the boot partition offset")
print(table["partitions"][0]["start"] * table["sectorsize"])
PYOFFSET
)"
mdir -i "${SD_IMG}@@${BOOT_OFFSET}" ::/
mtype -i "${SD_IMG}@@${BOOT_OFFSET}" ::config.txt
mtype -i "${SD_IMG}@@${BOOT_OFFSET}" ::cmdline.txt
```

`config.txt`의 UART·GIC 설정과 부트 자산을 확인한다. `cmdline.txt`의 일반 Linux 인자와 U-Boot가 Device Tree에 설정하는 Xen/dom0 인자를 구분한다.

### WIC 내부 부팅 파일 검사

`rpi-sdimg`의 부트 파일 설정은 WIC에 자동 적용되지 않는다. 프로젝트 이미지 레시피는 WIC의 `IMAGE_BOOT_FILES`에도 `xen-${MACHINE};xen`을 지정하고 `xen:do_deploy`를 선행 조건으로 둔다. WIC를 배포하려면 해당 파일 자체를 검사한다.

```bash
python3 -B scripts/check-perseus-wic.py \
  --deploy-dir "$IMG_DIR" \
  --output "$HOME/perseus-wic-inspection-$(date +%Y%m%d-%H%M%S)"
```

이 검사는 `.wic.bz2`의 MBR에서 FAT 위치를 읽고 `xen`, `Image`, `boot.scr`, 펌웨어가 읽는 U-Boot 파일(`kernel8.img`)을 추출하여 동일 deploy 디렉터리의 원본과 SHA-256을 비교한다. 추출된 `boot.scr`의 CRC와 Xen/FDT 부팅 명령도 검사한다. `--output`은 새 디렉터리여야 하며 추출 파일·FAT 목록·`result.json`을 남긴다. Python 표준 라이브러리와 호스트 `mtools`만 사용하고 SD 장치·마운트는 사용하지 않는다. 이는 부팅 파일의 정적 검사이며 실보드 자동 부팅 성공을 뜻하지 않는다.

### dom0 내부 게스트 자산과 VM 설정

```bash
debugfs -R 'ls -l /opt/perseus/bringup' "$ROOTFS_IMG"
debugfs -R 'cat /etc/xen/perseus-domu.cfg' "$ROOTFS_IMG"
```

bring-up rootfs에는 다음 자원이 있어야 한다. 게스트 kernel/ramdisk 경로는 설치된 VM 설정과 일치해야 한다.

```text
/etc/xen/perseus-domu.cfg
/etc/perseus/dnsmasq-bringup.conf
/opt/perseus/bringup/Image
/opt/perseus/bringup/domU-initramfs.cpio.gz
/usr/sbin/perseus-bringup-check
/usr/sbin/perseus-bridge-up
systemd: perseus-bridge.service / perseus-dhcp.service (기본 활성)
```

## 3. 실보드 준비와 UART

1. 새로운 `perseus-xen-bringup-image-...rpi-sdimg`를 PC의 별도 폴더로 복사한다. Raspberry Pi Imager에서 확장자를 인식하지 못하면 **복사본**의 확장자를 `.img`로 변경하여 'Use custom'으로 Flash한다. SD 카드의 기존 데이터는 덮어써진다.
2. **3.3V TTL USB-UART**를 사용한다. Pi 40핀 헤더의 물리적 **6번 GND ↔ 어댑터 GND**, **8번 GPIO14/TXD ↔ 어댑터 RX**, **10번 GPIO15/RXD ↔ 어댑터 TX**를 연결한다. **어댑터 VCC를 Pi에 연결하지 않는다.** 실제 제품의 핀 배치를 먼저 확인한다.
3. PC 시리얼 프로그램: **115200 baud, 8N1**, 흐름 제어 없음. Pi 전원을 넣기 **전부터** 로그를 캡처한다.
4. `U-Boot → Xen → dom0` 로그 경계를 구분하여 저장한다. 초기 Bring-up에서 HDMI 출력은 합격 조건이 아니다.

## 4. 실보드 단계별 검증

### 1~3단계: Xen, dom0, xl

돔0 로그인 셸 진입 후:

```bash
uname -a
cat /proc/cmdline
cat /proc/xen/capabilities
free -h
lsblk
systemctl --failed
command -v xl
xl info
xl list
xl dmesg
perseus-bringup-check
```

기대: `xl info`에서 현재 고정 레시피의 Xen 버전을 확인하고, `xl list`에 `Domain-0`가 표시되어야 한다. `xl info` 실패 시 `systemctl list-unit-files | grep xen` 등으로 toolstack 및 `xenstored` 실행 상태를 조사한다. UART 로그가 보이지만 로그인 프롬프트가 없으면 `console=hvc0`, `serial-getty@hvc0` 생성 및 로그인 설정을 따로 확인한다.

### 4단계: 테스트 domU 생성

부팅 시 자동 준비된 **격리 브릿지/DHCP 상태부터 확인**한다. 아래 start는 재실행해도 기존 상태를 유지한다. 네트워크가 준비되지 않아도 순수 domU 부팅과 독립적으로 원인을 분리할 수 있도록 로그를 남긴다.

```bash
systemctl start perseus-bridge.service
systemctl start perseus-dhcp.service
ip -br address show xenbr0
ls -l /sys/class/net/xenbr0/brif/
networkctl status eth0 xenbr0
# eth0에 master xenbr0가 없어야 한다. xenbr0는 unmanaged여야 한다.
systemctl --no-pager status perseus-bridge.service perseus-dhcp.service

xl create -c /etc/xen/perseus-domu.cfg
```

`xl create -c`가 성공하면 테스트 게스트의 `hvc0` 콘솔에서 `[perseus-domu] /init reached`와 쉘을 확인한다. 게스트 이미지에는 실제 Ubuntu/Debian 배포판 대신 **메모리 기반 임시 rootfs**를 사용한다. 손상되어도 루트파일시스템 디스크가 없다.

### 5단계: dom0 ↔ domU, 고정 MAC, DHCP

게스트 콘솔 안:

```bash
ip -br link
ip -br addr
cat /sys/class/net/eth0/address
ping -c 2 192.168.77.1
```

검증할 값:

- domU MAC `02:16:3e:20:00:01` (설정 파일에 지정한 **고정 MAC**)
- domU IPv4 `192.168.77.100~150` 중 하나 (dom0의 **dnsmasq DHCP**)
- domU ↔ dom0 `192.168.77.1` Ping 성공
- dom0의 `ip link`, `ls -l /sys/class/net/xenbr0/brif/`, `xl network-list perseus-test`에서 VIF/브릿지 연결 확인. domid는 실행 시마다 달라질 수 있다.
- `networkctl status xenbr0 vif<domid>.0`에서 unmanaged 확인. networkd 재시작 후에도 `eth0` 분리·브릿지 고정 IP·VIF 부착 유지 확인
- dom0에서 `ping -c 2 <실제 domU DHCP 주소>`도 성공해야 양방향 Ping 합격이다.
- 게스트 `/proc/mounts`·`/sys/class/net/eth0/address` 접근, 수동 DHCP 없이 주소 획득 확인. NIC가 없으면 최대 20초 대기 후 진단 셸로 진행한다.

게스트 쉘은 테스트 종료 시 `xl destroy perseus-test`로 강제 제거할 수 있다. **이는 정상 종료(shutdown) 검증이 아니다.** 정상 종료는 이후 정식 init 환경을 갖춘 domU에서 따로 확인한다.

## 5. 합격 기준과 예외

| 검증 | 합격 조건 |
| --- | --- |
| 부트 체인 | Pi 펌웨어 / U-Boot / Xen / dom0 UART 로그 확보 |
| dom0 | 기본 Linux 도구, 저장장치, reboot 확인 |
| 도구 | `xl info`, `xl list`, `xl dmesg` 성공 |
| domU | 게스트 1개 생성, `/init` 실행, Xen 콘솔 접근 |
| 브릿지 | `xenbr0` 생성, VIF 부착, domU↔dom0 ping |
| DHCP/MAC | 지정 MAC 유지, domU 동적 IP 할당 |

미포함: Agent, MQTT, FastAPI 관리 서버, React 관리 UI, HDMI 키오스크, multi-node, 공유메모리, 정식 Debian/Ubuntu/Alpine 게스트.

## 6. 실패할 때 먼저 볼 항목

- **Pi가 부팅되지 않음:** Xen/U-Boot/DTB/펌웨어, SD 부트 파티션, UART log를 먼저 수집한다.
- **로그는 나오는데 dom0 로그인 불가:** `console=hvc0`와 `serial-getty@hvc0`, 사용자 인증 설정 점검. 도구/게스트 코드를 수정하기 전에 해결한다.
- **`xl` 없음/실패:** 이미지 manifest의 `xen-tools`, xenstored와 xenconsoled 서비스 상태, `/proc/xen/capabilities` 점검.
- **`xl create` 실패:** `/opt/perseus/bringup/`의 `Image`/`domU-initramfs.cpio.gz`, `xl.cfg` 문법, Xen ARM guest 지원, 로그를 확인.
- **domU에 `eth0` 없음:** 게스트 커널의 내장 `CONFIG_XEN_NETDEV_FRONTEND`, dom0 netback 모듈, Xenbus/ vif 생성 오류 점검.
- **브릿지 안 만들어짐:** `CONFIG_BRIDGE`, `bridge` 커널 모듈 설치, `ip link add ... type bridge` 에러 확인.
- **DHCP 실패:** `systemctl status perseus-dhcp.service`, `journalctl -u perseus-dhcp.service -b`, `xl network-list perseus-test`, `ip addr` 점검.
- **domU가 빠르게 종료됨:** `xl dmesg`, guest console log, initramfs의 `/init`과 `/dev/hvc0` 점검.

## 공식 근거

- Xen 4.21 `xl.cfg` 문법: https://xenbits.xenproject.org/docs/4.21-testing/man/xl.cfg.5.html
- Xen Project ARM64 guest Image 지원: https://github.com/xen-project/xen/blob/master/SUPPORT.md
- meta-virtualization `xen-image-minimal` 및 `xen-tools` 패키지: https://layers.openembedded.org/layerindex/branch/wrynose/layer/meta-virtualization/
- Yocto image 및 rootfs 작업 단계: https://docs.yoctoproject.org/6.0/ref-manual/tasks.html
