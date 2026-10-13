# Raspberry Pi 4 + Xen — 최소 Bring-up 전용 Yocto 이미지

> **용도:** 실보드 수령 후 ① Xen/dom0 부팅, ② dom0 기본 기능, ③ xl 툴스택, ④ 임시 domU 부팅, ⑤ 격리 브릿지·DHCP·MAC을 단계적으로 검증한다.  
> **현재 상태:** 2026-10-13에 작성한 **테스트 구성 초안**. 기존 `xen-image-minimal`의 실물 SD 이미지 생성은 완료되었지만 **이 신규 레시피의 BitBake 파싱·컴파일·실보드 검증은 아직 수행되지 않았다.**

## 원칙

- **기존 성공 이미지 `xen-image-minimal`을 보존**한다. 다른 타깃 `perseus-xen-bringup-image`를 추가하며 외부 Yocto 레이어, `layers.lock`, 기존 성공한 커널/부팅 스크립트 설정을 교체하지 않는다.
- `perseus-domu-initramfs`로 **디스크가 없는 테스트 domU**를 만든다. Debian·Ubuntu·Alpine의 최종 게스트 구성이 아니며, Linux Arm64 커널 `Image`는 기존 dom0 커널을 **테스트용으로 재사용한다**. 이를 지원할 커널 옵션이 없으면 빌드를 진행하지 말고 게스트 커널부터 조정한다.
- `xenbr0 = 192.168.77.1/24`는 **물리 NIC에 연결하지 않은 고립된 브릿지**다. `perseus-dhcp.service`는 `192.168.77.100~150` 주소를 제공하며 DNS는 비활성화한다. `eth0`를 브릿지에 추가하지 않는다.
- 테스트 서비스는 이미지에 설치되지만 **부팅 시 자동 시작하지 않는다**. `dnsmasq_%.bbappend`로 기본 `dnsmasq.service` 자동 기동도 비활성화하고, 분리된 `perseus-dhcp.service`만 수동 실행한다. 1~3단계 검증 후 운영자가 명령으로 시작한다.
- 이 실험 이미지만 **root 빈 비밀번호** 허용 설정을 갖고, `serial-getty@hvc0.service`를 활성화하려고 시도한다. 디버깅 목적이며 인터넷/공용망 연결 금지. 실제 배포 이미지에 사용하지 않는다. 특히 SSH 서비스가 제공되므로 신뢰할 수 없는 네트워크에 연결하지 않는다.

## 0. 파일 설치 (WSL2)

본 ZIP의 루트 경로는 GitLab `S15P31E110/` 기준이다. ZIP을 WSL 리눅스 파일시스템에 복사한 후 **프로젝트 루트에서** 압축을 푼다.

```bash
cd ~/S15P31E110
unzip -l /path/to/perseus-xen-bringup-kit.zip
unzip /path/to/perseus-xen-bringup-kit.zip -d .
chmod +x scripts/check-xen-bringup-prereqs.sh

git status --short
```

ZIP은 새 레시피와 문서·검사 스크립트만 포함한다. 기존 `scripts/setup-yocto.sh`나 `meta-perseus/conf/layer.conf`는 덮어쓰지 않는다. 원한다면 패키지 파일을 먼저 읽은 뒤 추가한다.

## 1. 빌드 전 커널 옵션 검사 — 반드시 먼저

**이미 전체 빌드에 성공한** `build-rpi4-xen/` 디렉터리를 사용해 `.config`를 검사한다.

```bash
source ~/yocto-workspace/layers/openembedded-core/oe-init-build-env \
  ~/yocto-workspace/build-rpi4-xen

cd ~/S15P31E110
bash scripts/check-xen-bringup-prereqs.sh
```

기본 요구:

| 심볼 | 필요값 | 이유 |
| --- | --- | --- |
| `CONFIG_XEN`, `CONFIG_XEN_DOM0` | `y` | Xen 기반 Linux |
| `CONFIG_XEN_NETDEV_BACKEND` | `y` 또는 `m` | dom0의 VIF 처리 |
| `CONFIG_XEN_NETDEV_FRONTEND` | **`y`** | **같은 Image**를 domU에 재사용하고 초기 initramfs에는 netfront 모듈을 따로 넣지 않음 |
| `CONFIG_HVC_XEN` | **`y`** | domU 가상 콘솔 hvc0 |
| `CONFIG_BRIDGE` | `y` 또는 `m` | xenbr0 생성. `m`이면 dom0에 bridge.ko 설치·적재 필요 |
| `CONFIG_BLK_DEV_INITRD`, `CONFIG_RD_GZIP`, `CONFIG_DEVTMPFS` | `y` | 게스트 initramfs 실행 |

`[BLOCKER]`가 하나라도 나오면 **빌드를 멈추고 해당 설정을 검토**한다. 검사 통과가 보드 부팅을 보증하는 것은 아니다. 특히 `BRIDGE=m`인 경우 생성한 이미지의 패키지 manifest에 `kernel-module-bridge`가 포함됐는지 나중에 확인한다.

## 2. 레시피 검증 (이미지 빌드 없음)

터미널에서 다시 Yocto 환경을 불러오거나 `bitbake`가 잡혀 있는지 확인한다.

```bash
source ~/yocto-workspace/layers/openembedded-core/oe-init-build-env \
  ~/yocto-workspace/build-rpi4-xen

bitbake -p
bitbake -e perseus-xen-bringup-image | grep -E '^(PN|MACHINE|IMAGE_INSTALL|IMAGE_FSTYPES)='
bitbake -e perseus-domu-initramfs | grep -E '^(PN|IMAGE_INSTALL|IMAGE_FSTYPES)='

bitbake -n perseus-xen-bringup-image > ~/perseus-bringup-dryrun.log 2>&1
tail -n 25 ~/perseus-bringup-dryrun.log
```

이 명령이 끝나기 전에는 실제 빌드를 시작하지 않는다. BitBake 파싱 오류나 레시피 이름 불일치가 나오면 즉시 수정한다.

## 3. Bring-up 전용 이미지 빌드

```bash
cd ~/yocto-workspace/build-rpi4-xen
bitbake perseus-xen-bringup-image
```

이미 수행한 `xen-image-minimal`의 빌드 작업은 BitBake 캐시가 가능한 범위에서 재사용된다. **새 테스트 게스트 initramfs와 rootfs 조립 등 추가 작업은 필요**하며, 작업량·빌드 성공은 현재 예측할 수 없다. `bitbake -c cleanall`, `rm -rf tmp`, `rm -rf sstate-cache`는 하지 않는다.

성공 후 **새로운 이미지 파일만** 확인한다.

```bash
cd ~/yocto-workspace/build-rpi4-xen
ls -lh tmp/deploy/images/raspberrypi4-64/*perseus-xen-bringup-image*.rpi-sdimg
ls -lh tmp/deploy/images/raspberrypi4-64/*perseus-xen-bringup-image*.manifest

# dom0의 Xen 도구와 진단 패키지 포함 여부
ls tmp/deploy/images/raspberrypi4-64/*perseus-xen-bringup-image*.manifest | head -n 1
```

manifest 확인은 `grep -E 'xen-tools|perseus-bringup|dnsmasq|iproute2|kernel-module-bridge' <선택한 .manifest 파일>` 형태로 수행한다. 설치 패키지 목록과 실제 파일 배치는 검증 결과로 기록한다.

실제 rootfs 안에 다음 자원이 있어야 한다.

```text
/etc/xen/perseus-domu.cfg
/etc/perseus/dnsmasq-bringup.conf
/opt/perseus/bringup/Image
/opt/perseus/bringup/domU-initramfs.cpio.gz
/usr/sbin/perseus-bringup-check
/usr/sbin/perseus-bridge-up
systemd: perseus-bridge.service / perseus-dhcp.service (기본 비활성)
```

**주의:** `setup-yocto.sh`는 여전히 이 이미지를 자동으로 빌드하지 않는다. 기존 스크립트의 역할은 환경 구성이다.

## 4. 실보드 준비와 UART

1. 새로운 `perseus-xen-bringup-image-...rpi-sdimg`를 PC의 별도 폴더로 복사한다. Raspberry Pi Imager에서 확장자를 인식하지 못하면 **복사본**의 확장자를 `.img`로 변경하여 'Use custom'으로 Flash한다. SD 카드의 기존 데이터는 덮어써진다.
2. **3.3V TTL USB-UART**를 사용한다. Pi 40핀 헤더의 물리적 **6번 GND ↔ 어댑터 GND**, **8번 GPIO14/TXD ↔ 어댑터 RX**, **10번 GPIO15/RXD ↔ 어댑터 TX**를 연결한다. **어댑터 VCC를 Pi에 연결하지 않는다.** 실제 제품의 핀 배치를 먼저 확인한다.
3. PC 시리얼 프로그램: **115200 baud, 8N1**, 흐름 제어 없음. Pi 전원을 넣기 **전부터** 로그를 캡처한다.
4. `U-Boot → Xen → dom0` 로그 경계를 구분하여 저장한다. 초기 Bring-up에서 HDMI 출력은 합격 조건이 아니다.

## 5. 실보드 단계별 검증 — 성공 판정은 사람이 확인

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

기대: `xl info`에서 Xen 4.21 계열 확인, `xl list`에 `Domain-0` 표시. `xl info` 실패 시 `systemctl list-unit-files | grep xen` 등으로 toolstack 및 `xenstored` 실행 상태를 조사한다. UART 로그가 보이지만 로그인 프롬프트가 없으면 `console=hvc0`, `serial-getty@hvc0` 생성 및 로그인 설정을 따로 확인한다.

### 4단계: 테스트 domU 생성

다음 명령으로 **격리 브릿지/DHCP 서비스를 먼저 시작**한다. 네트워크가 준비되지 않아도 순수 domU 부팅과 독립적으로 원인을 분리할 수 있도록 로그를 남긴다.

```bash
systemctl start perseus-bridge.service
systemctl start perseus-dhcp.service
ip -br address show xenbr0
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
- dom0의 `ip link`, `bridge link`, `xl network-list perseus-test`에서 VIF/브릿지 연결 확인

게스트 쉘은 테스트 종료 시 `xl destroy perseus-test`로 강제 제거할 수 있다. **이는 정상 종료(shutdown) 검증이 아니다.** 정상 종료는 이후 정식 init 환경을 갖춘 domU에서 따로 확인한다.

## 6. 합격 기준과 예외

| 검증 | 합격 조건 |
| --- | --- |
| 부트 체인 | Pi 펌웨어 / U-Boot / Xen / dom0 UART 로그 확보 |
| dom0 | 기본 Linux 도구, 저장장치, reboot 확인 |
| 도구 | `xl info`, `xl list`, `xl dmesg` 성공 |
| domU | 게스트 1개 생성, `/init` 실행, Xen 콘솔 접근 |
| 브릿지 | `xenbr0` 생성, VIF 부착, domU↔dom0 ping |
| DHCP/MAC | 지정 MAC 유지, domU 동적 IP 할당 |

미포함: Agent, MQTT, FastAPI 관리 서버, React 관리 UI, HDMI 키오스크, multi-node, 공유메모리, 정식 Debian/Ubuntu/Alpine 게스트.

## 7. 실패할 때 특히 먼저 볼 항목

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
