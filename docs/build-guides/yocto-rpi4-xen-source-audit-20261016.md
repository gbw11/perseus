# Perseus Xen Bring-up — 2026-10-16 1차 소스 감사 및 수정

## 범위와 증거 수준

- 작업 위치: WSL `Ubuntu-24.04`, `/home/ssafy/S15P31E110`.
- 브랜치: `feat/platform/yocto-build-setup`, 시작 HEAD `7152ce47859779c888119e1bd15c8a0d85a4a3bc`. 시작 작업 트리는 clean이었다.
- 사용자 제공 실보드 결과: 수동으로 Control FDT를 복사하고 MMC 장치·네트워크·dnsmasq 경로·게스트 초기화를 보정한 후 Xen → Yocto dom0 → BusyBox domU 부팅, DHCP, 양방향 Ping 성공.
- 이 감사에서는 보드 콘솔을 직접 실행하거나 해당 성공 로그를 새로 수집하지 않았다. 사용자 보고, 고정 레이어 원본, 기존 빌드의 `.config`·rootfs·부트 환경, 수정 소스의 정적 검사 결과를 구분한다.
- 이미지·`boot.scr` 생성, 컴파일, BitBake 빌드 태스크, SD 기록, commit/push는 수행하지 않았다. 기존 빌드·캐시·deploy 경로에 쓰는 명령도 실행하지 않았다.

## 1. 원인과 근거

### U-Boot

원본은 `meta-virtualization/dynamic-layers/raspberrypi/recipes-bsp/xen-rpi-u-boot-scr/`의 레시피와 `files/boot.cmd.xen.in`이다. 프로젝트의 기존 bbappend는 `S`·`UNPACKDIR`만 보정하므로 외부 템플릿이 그대로 선택됐다.

- 원본 `part uuid mmc 1:2`, `fatload mmc 1:1`은 실보드에서 확인한 `mmc 0`과 불일치한다.
- 원본은 `${fdt_addr}`를 working FDT로 선택한 뒤 직접 `resize/set/rm`한다. Control FDT와 같은 메모리라면 U-Boot 내부 참조까지 영향을 받는다. 재시작은 사용자 보고이며, 이 감사에서 해당 런타임 주소의 동일성이나 재시작 원인을 직접 측정한 것은 아니다.
- 기존 `u-boot/2026.01/build/.config`에는 `CONFIG_BOOTSTD`, `CONFIG_BOOTMETH_SCRIPT`, `CONFIG_CMD_FDT`, `CONFIG_CMD_PART`, `CONFIG_CMD_FAT`, `CONFIG_CMD_MMC`, `CONFIG_CMD_BOOTI`가 활성화되어 있다.
- 같은 빌드의 `u-boot-initial-env`에서 `bootcmd=bootflow scan`, `fdt_addr_r=0x05600000`을 확인했다. 실제 소스 `boot/bootmeth_script.c:script_boot()`는 스크립트 실행 직전에 `devtype`, `devnum`, `distro_bootpart`를 제공한다. 따라서 자동 부팅에는 현재 MMC의 `devnum`을 사용하고, 변수가 없는 수동 `source`에만 확인된 `mmc 0`을 기본값으로 적용한다. USB 등 다른 부트 매체는 이번 범위에서 지원하지 않는다.
- `cmd/fdt.c`의 `fdt move`는 원본에서 새 주소로 `fdt_open_into()`를 호출하고 working FDT를 복사본으로 전환한다. 이 동작에 근거해 Control FDT를 `${fdt_addr_r}`로 복사하고 그 뒤에만 확장·수정한다. 원본 Control FDT를 제자리 확장하지 않는다.

### 네트워크 설정의 공급·설치 주체

```text
meta-virtualization/recipes-extended/xen/xen-tools_4.21.bb
  → xen-tools.inc: SRC_URI / VIRT_NETWORKING_FILES
  → classes/virt_networking.bbclass: do_install:append
  → /etc/systemd/network/10-ether.network
     /etc/systemd/network/10-xenbr0.netdev
     /etc/systemd/network/10-xenbr0.network
  → xen-tools-net-conf 패키지 (xen-tools의 런타임 의존성)
```

기존 bring-up rootfs에도 이 세 파일이 존재한다. `10-ether.network`의 `Type=ether`·`Bridge=xenbr0`는 물리 `eth0`를 매칭한다. 따라서 Perseus 스크립트가 직접 물리 NIC를 연결하지 않아도 networkd가 연결한다. `.netdev`는 부팅 중 브릿지를 자동 생성하고, `10-xenbr0.network`의 `DHCP=yes`는 수동 고정 IPv4 정책과 별개로 DHCP 클라이언트를 동작시킨다.

실제 `/etc/xen/scripts/vif-bridge`는 VM 설정의 bridge 이름으로 **해당 게스트 VIF**를 연결·해제한다. 물리 NIC 자동 편입의 근원은 이 스크립트가 아니라 위 networkd 규칙이다.

수정 후 책임:

| 주체 | 책임 |
| --- | --- |
| `perseus-bridge.service` / `perseus-bridge-up` | 수동 시작 시 `xenbr0` 생성, `192.168.77.1/24` 설정·UP |
| Xen `vif-bridge` | VM 생성·제거에 따른 `vif<domid>.0`의 브릿지 연결·해제 |
| systemd-networkd | `xenbr0`, `vif*`는 `[Link] Unmanaged=yes`. 물리 NIC는 기존 일반 정책 적용 |
| `perseus-dhcp.service` | `xenbr0`에만 DHCP 서버 제공. DNS·기본 라우터 제공 안 함 |

기존 rootfs의 `/usr/lib/systemd/network/80-wired.network`는 물리 Ethernet의 일반 DHCP 정책이다. 이를 수정하거나 networkd 전체를 비활성화하지 않았다. 브릿지·VIF의 비관리 규칙을 앞선 `10-*` 파일에 두어 일반 Ethernet 규칙이 VIF를 재설정하는 것도 막는다. 기존 파일명은 공급 패키지가 같은 경로를 대체하도록 유지했다.

### DHCP 실행 경로

`meta-openembedded/meta-networking/recipes-support/dnsmasq/dnsmasq_2.93.bb:do_install()`은 `BINDIR=${D}${bindir}`를 전달한다. 기존 rootfs에는 `/usr/bin/dnsmasq`가 있고 `/usr/sbin/dnsmasq`는 없다. 반면 자체 `perseus-dhcp.service`의 `@SBINDIR@`를 `${sbindir}`로 치환하므로 잘못된 경로를 실행한다. 이는 사용자 보고의 `203/EXEC`와 일치한다. unit을 `@BINDIR@`로, 레시피의 치환을 `${bindir}`로 수정했다.

### 테스트 domU 초기화

기존 `perseus-domu-initramfs/1.0/rootfs`에는 `/proc`, `/sys`, `/dev` 디렉터리가 없다. `/bin/mount`는 BusyBox applet으로 존재하므로 단순한 실행 파일 경로 오류가 아니다. 기존 `/init`은 대상 디렉터리를 만들기 전에 마운트를 시도하고, 실패를 `2>/dev/null || :`로 숨긴다.

기존에도 NIC 최대 20초 대기와 `udhcpc -n -q -t 5 -T 2`는 있었다. 그러나 sysfs가 마운트되지 않으면 `/sys/class/net/eth0`로 등록 여부를 볼 수 없으므로 대기 시간만 늘려서는 해결되지 않는다. `/proc/mounts`·MAC 주소 파일 접근 실패와도 부합한다. 실제 netfront 등록 지연의 길이는 직접 측정하지 않았다.

수정은 대상 디렉터리 생성 → proc/sysfs/devtmpfs 마운트 → devpts → NIC 최대 20초 대기 → 링크 활성화 성공 → foreground DHCP 순서다. 필수 마운트 또는 링크 활성화가 실패하면 오류를 출력하고 DHCP를 생략한다. Discover는 기존처럼 최대 5회, 간격 2초이며 `-n`으로 주소 획득 실패 시 종료, `-q`로 성공 시 종료한다. 전체 DHCP 프로토콜 교환에 대한 별도 wall-clock watchdog을 추가한 것은 아니다. 진단 셸 재시작 루프는 의도한 기존 동작으로 유지한다.

## 2. 변경 파일

아래 소스 경로는 `platform/yocto/meta-perseus/` 기준이다.

| 파일 | 변경 |
| --- | --- |
| `recipes-bsp/xen-rpi-u-boot-scr/xen-rpi-u-boot-scr.bbappend` | 자체 부트 템플릿 검색 경로 추가. 기존 unpack 보정 유지 |
| `recipes-bsp/xen-rpi-u-boot-scr/files/boot.cmd.xen.in` (신규) | 부트 MMC 선택, Control FDT 복사, 적재·PARTUUID 실패 시 중단, `${fdt_addr_r}`로 `booti` |
| `recipes-extended/xen/xen-tools_%.bbappend` (신규) | networkd 규칙 대체, 자동 생성용 `.netdev`를 입력·설치 목록에서 제외 |
| `recipes-extended/xen/files/10-ether.network` (신규) | 매칭을 `vif*`로 제한하고 비관리 설정 |
| `recipes-extended/xen/files/10-xenbr0.network` (신규) | DHCP 클라이언트 대신 비관리 설정 |
| `recipes-core/perseus-bringup/files/perseus-dhcp.service` | dnsmasq 실행 경로를 bindir 토큰으로 변경 |
| `recipes-core/perseus-bringup/perseus-bringup_1.0.bb` | DHCP unit의 `${bindir}` 치환 |
| `recipes-core/perseus-domu-init/files/init` | 마운트 대상 생성·실패 보고·NIC 대기 및 DHCP 실행 순서 보완 |

문서: 이 감사 기록 추가, `yocto-rpi4-xen-reproduce.md`, `yocto-rpi4-xen-minimal-bringup.md`, `yocto-rpi4-xen-build-versions.md` 갱신. `scripts/`는 변경하지 않았다.

부트 템플릿과 `xen-tools` bbappend는 meta-perseus를 사용하는 해당 Xen 이미지 전체에 적용된다. 이미 생성된 `xen-image-minimal`이나 기존 bring-up 이미지는 수정하지 않았다. Xen `0x00200000`, dom0 `0x00400000`, dom0 메모리 1024M, UART·PCI/USB workaround, VM 메모리·MAC·IP 범위, 수동 서비스 시작 정책, 커널·레이어 커밋은 유지한다.

## 3. 변경 전후

| 영역 | 변경 전 | 변경 후 소스의 의도 |
| --- | --- | --- |
| 부트 디스크 | `mmc 1` 고정 | bootflow가 선택한 MMC, 변수 없는 수동 호출은 `mmc 0` |
| FDT | 선택한 기존 FDT 제자리 수정 | Control FDT 복사본만 수정하고 전달 |
| 브릿지 | networkd 자동 생성·물리 Ethernet 편입·DHCP 클라이언트와 수동 설정 공존 | Perseus가 생성·고정 IP, Xen이 VIF 연결, networkd 간섭 제거 |
| DHCP 서비스 | 존재하지 않는 `/usr/sbin/dnsmasq` | 실제 설치 위치인 `/usr/bin/dnsmasq` |
| domU | 누락 디렉터리에 마운트 시도·오류 은폐 | 마운트 대상을 먼저 생성하고 준비 실패와 NIC 지연을 구분 |

이 표는 수정 소스의 동작 의도다. 수정본 이미지의 실제 동작을 검증했다는 뜻이 아니다.

## 4. 수행한 검증

정적 파싱 경로는 `/tmp/perseus-source-audit-20261016-6W9Y1r/build`, 로그는 같은 상위 디렉터리의 `logs/`다. 기존 conf를 임시 경로로 복사하고 `TMPDIR`, `CACHE`, `PERSISTENT_DIR`, `DL_DIR`, `SSTATE_DIR`를 모두 임시 경로로 격리했다. 네트워크는 `BB_NO_NETWORK=1`, 해시 서버는 비활성화하고 Python bytecode 생성을 막았다. 기존 build 환경에서 BitBake를 실행하지 않았다.

| 검사 | 결과 |
| --- | --- |
| `bash scripts/setup-yocto.sh --check` | 외부 6개 checkout URL·잠금 SHA·clean 상태 일치 |
| `sh -n` (init, DHCP hook, bridge helper), `bash -n` (기존 검사·setup 스크립트) | 통과 |
| 기존 QEMU `qemu-aarch64 -L <기존 guest rootfs> <기존 busybox.nosuid> sh -n <init/hook>` | 타깃 BusyBox ash 문법 통과. 스크립트 동작·마운트는 실행하지 않음 |
| 임시 경로의 `bitbake -p` | 3212개 `.bb` 파싱, 오류 0. 기존 WSL VHDX 권고 경고 1건 |
| `bitbake-layers show-appends` | 부트·xen-tools append 등록 확인 |
| `bitbake -e` 5개 타깃 | 부트 스크립트·xen-tools·perseus-bringup·domU·bring-up 이미지 변수 해석 통과 |
| FILESPATH·설치 목록 검사 | 프로젝트 boot/network 파일 우선 선택, `.netdev` 제거, bindir 경로·치환 확인 |
| 임시 명령 대역 검사 17개 | 부트 MMC 선택/기본값/실패 중단 8개, NIC 지연·부재·마운트/링크/DHCP 실패 분기 8개, networkd 매칭 1개 통과 |
| 기존 conf 2개·boot.scr·Image·Xen SHA-256 재검사 | 임시 파싱 전후 동일 |
| `git diff --check` | 통과 |

대역 검사는 `/tmp/perseus-source-audit-20261016-6W9Y1r/check_source_behavior.py`로 실행했다. FDT 셀 문법을 호스트 셸에 맞게 인용하고 부트·네트워크 명령을 대역으로 바꾸므로, 실제 U-Boot parser·메모리·커널·DHCP 응답을 검증하지 않는다. BitBake unpack/install/package/rootfs/image 태스크도 실행하지 않았으므로 패키징 및 최종 이미지 내용은 다음 단계에서 확인해야 한다.

## 5. 2차 빌드에서 반드시 확인할 항목

다음은 **별도 빌드 지시 후** 수행할 확인 목록이며 이번에는 실행하지 않았다.

1. `xen-rpi-u-boot-scr`가 자체 템플릿을 unpack하고 치환되지 않은 `@@...@@` 토큰 없이 새 `boot.scr`를 생성하는지 확인한다. FAT/최종 SD 내부의 스크립트까지 새 내용과 일치해야 한다.
2. `xen-tools-net-conf` 설치 결과에 비관리 `.network` 2개가 있고, `10-xenbr0.netdev`, `Bridge=xenbr0`, `xenbr0`의 `DHCP=yes`가 남지 않는지 검사한다. `/etc`·`/usr/lib`·`/run`의 다른 규칙과 drop-in도 대조한다.
3. 최종 DHCP unit의 `ExecStart=/usr/bin/dnsmasq ...`와 실행 파일 존재를 함께 검사한다. 수동 서비스 정책과 기본 dnsmasq unit 비활성화가 유지되어야 한다.
4. domU cpio의 `/init`·DHCP hook이 최신 소스이며, dom0 `/opt/perseus/bringup/`의 ramdisk가 해당 cpio와 같은지 확인한다. 새 init이 `/proc`·`/sys`·`/dev`를 실행 시 생성하는 것이므로 archive에 빈 디렉터리 존재를 요구할 필요는 없다.
5. 이미지 manifest에 bridge·xen-netback 모듈 등 기존 필수 패키지가 유지되는지, QA·install 충돌·미패키징 파일 오류가 없는지 확인한다.

## 6. 실제 하드웨어에서 재검증할 사항

- UART 수동 개입 없이 cold boot와 재부팅이 완료되는지, bootflow의 실제 `devnum`, Control/working FDT 주소와 복사 후 Xen 진입을 기록한다. 새 `fdt move` 경로의 재시작 방지 효과는 아직 미확인이다.
- 기존 PCI/USB workaround, UART, dom0 root PARTUUID, Xen 메모리·커널 적재 주소가 계속 유효한지 확인한다.
- 수동 서비스 시작 전 브릿지가 자동 생성되지 않는지, 시작 후 `xenbr0=192.168.77.1/24`이며 물리 `eth0`·`wlan0`가 포트에 포함되지 않는지 확인한다.
- 실제 domid에 해당하는 `vif<domid>.0`가 `xenbr0`에 연결되고, networkd 재시작·링크 이벤트 후에도 비관리 상태·고정 IP·VIF 연결이 유지되는지 확인한다. 오래된 보드의 수동 설정·systemd override가 새 원본을 가리지 않는지도 확인한다.
- 수동 NIC 활성화나 DHCP 재요청 없이 domU가 주소를 받고, `/proc/mounts`·MAC 주소 파일이 읽히며, 양방향 Ping이 성공하는지 확인한다.
- NIC 지연·부재 및 DHCP 미응답 상황에서 제한된 대기·실패 메시지·진단 셸 진입을 관찰한다. 20초 NIC 제한은 기존 값이며 실제 부팅 분포에 맞는지 재검증한다.

## 참고: 연결 SD카드 용량

Windows 읽기 전용 조회에서 USB 디스크는 63,864,569,856 bytes였다. 파티션은 FAT32 62,914,560 bytes(60MiB, D:)와 Linux 3,279,945,728 bytes(약 3.05GiB)다. D:의 남은 공간 9,448,448 bytes는 약 **9MiB**이며 카드 전체의 남은 공간이 아니다. 두 파티션 밖 공간은 약 56.4GiB다. Linux 파일시스템 내부 사용량은 조회하지 않았고, 카드의 mount·resize·format·쓰기 작업은 수행하지 않았다.
