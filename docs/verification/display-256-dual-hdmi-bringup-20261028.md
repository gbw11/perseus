# 256 — Pi4 dual HDMI KMS 활성화 (2026-10-28)

작업 브랜치: `feat/platform/S15P31E110-256-dual-hdmi-bringup`, 기준 platform ef79127. 별도 worktree를 사용해 252번 미커밋 렌더러 변경을 보존했다. platform 자체는 변경하지 않았다.

## 결과

일회성 시험 부팅에서 실제 재부팅과 tryboot_flag=1을 확인했다. 다음 두 DRM 커넥터가 동시에 connected이며 서로 다른 256바이트 EDID를 제공한다.

| DRM 출력 | 상태 | EDID SHA-256 |
| --- | --- | --- |
| card0-HDMI-A-1 | connected | 92a52d12e80b125e5d1d8a90c1acc772e6303e03a79961f98cea844e44e643f6 |
| card0-HDMI-A-2 | connected | ce1acb6e187b736d79d8103859f765f9f970863ad45b38fad07f2be5cb4c0275 |

양쪽 지원 모드에 1920×1080과 1280×720이 포함된다. `/dev/dri/card0`가 생겼고 실행 DT에서 HDMI0/HDMI1은 okay다. 사용자가 두 물리 모니터 모두 로그인 화면까지 나왔다고 확인했다. 후속 조회에서 fb0는 vc4drmfb 1920×1080으로 확인됐다. 이것은 포트 인식·콘솔 표시 증거이며 서로 다른 두 웹 화면이 동시에 배치됐다는 뜻은 아니다.

## 원인과 준비

기존 config.txt에는 `dtoverlay=vc4-kms-v3d`가 켜져 있었지만 /boot/overlays 및 대응 dtbo가 없었다. 커널에는 vc4·drm·drm_kms_helper가 내장돼 있으나 실행 DT의 HDMI/HVS/pixelvalve가 disabled였다. fb0/fb1은 같은 물리 framebuffer였고 두 독립 출력의 증거가 아니었다.

프로젝트 layers.lock의 meta-raspberrypi 919588393f41b48cb6c5d832aa35e58a19b6762d에서 linux-raspberrypi_6.18.bb를 확인했다. LINUX_VERSION=6.18.33, SRCREV_machine=95b85bebbedcaedfa7ca79116ed38b7376fba412다. 실제 보드 release도 6.18.33-v8이다. 공식 Raspberry Pi Linux의 해당 커밋에서 Pi4 전용 overlay, CMA include, clock binding을 가져와 dtc로 빌드했다. 전체 커널/이미지 재빌드는 하지 않았다.

generic vc4-kms-v3d는 보드별 overlay_map에서 Pi4 전용 파일로 매핑된다. 이 보드에는 overlay_map도 없으므로 실제 Pi4용 `vc4-kms-v3d-pi4`를 명시했다. 기존 dom0 CMA는 64MiB였고 Pi4 overlay의 기본값은 더 크므로, `cma-64`를 명시해 기존 예약량을 유지했다.

## 오프라인 검증

공식 소스와 아래 명령으로 overlay를 생성했다. 경로는 준비 디렉터리 기준이다.

```sh
cpp -nostdinc -I include -undef -D__DTS__ -x assembler-with-cpp \
  vc4-kms-v3d-pi4-overlay.dts > vc4-kms-v3d-pi4.preprocessed.dts
dtc -@ -H epapr -I dts -O dtb -o vc4-kms-v3d-pi4.dtbo \
  vc4-kms-v3d-pi4.preprocessed.dts
```

생성물은 3913바이트, SHA-256 `155ec071f26a8d45c4524ee00c5037ad7ceb8cc81d221640b9d9d7177c416ddf`다. dtc는 upstream dormant fragment의 unit_address_vs_reg 경고를 출력했지만 빌드는 성공했다. 실제 부팅 DTB의 로컬 복사본에 fdtoverlay를 적용해 HDMI0/HDMI1/HVS=okay를 확인했다. CMA 파라미터 시험용 복사본에서는 size=0x04000000도 확인했다. 원본 DTB는 수정하지 않았다.

## 실제 보드에 적용한 변경

1. `/root/xenova-hdmi-backup/20261028-060513/`에 config.txt, boot.scr, bcm2711-rpi-4-b.dtb를 백업하고 SHA-256을 대조했다. 같은 원본을 PC에도 보관했다.
2. `/boot/overlays/vc4-kms-v3d-pi4.dtbo`를 추가했다.
3. 원본 config.txt의 복사본으로 `/boot/tryboot.txt`를 만들고 graphics 줄만 Pi4·CMA64로 바꿨다. 기본 설정은 그대로 둔 채 일회성 부팅으로 먼저 시험했다.
4. 최초 `systemctl reboot "0 tryboot"` 요청은 이 systemd의 명령 형식과 맞지 않아 실제 재부팅되지 않았다. boot_id 불변으로 확인했고 실패 기록을 보존했다. 올바른 `systemctl --reboot-argument="0 tryboot" reboot`는 dry-run 통과 후 실행했다. 정상 종료 경로를 썼고 force·inhibitor 우회는 하지 않았다.
5. 실제 boot_id 변경, tryboot_flag=1, 두 커넥터와 EDID를 확인했다. 초기에 SSH가 간헐적으로 시간 초과됐지만 이후 기존 주소로 접속해 상태를 확인했다. 네트워크 설정은 바꾸지 않았고 시간 초과 원인은 확정하지 않았다.
6. VM 3대와 Manager·기존 서비스 복귀를 확인한 뒤 기본 `/boot/config.txt`에서 아래 한 줄을 원자적으로 교체·동기화했다.

```diff
-dtoverlay=vc4-kms-v3d
+dtoverlay=vc4-kms-v3d-pi4,cma-64
```

최종 config.txt SHA-256은 `9f8dc2146b654152fc7f1a1388de6915a7f8d57257d9d1400c0686c99eb81557`이다. tryboot.txt는 시험 기록으로 남아 있다. 커널 Image, kernel8.img, Xen, boot.scr, 원본 DTB, 게스트 펌웨어는 교체하지 않았다. 설정 확정 후 추가 재부팅은 하지 않았으므로 **새 기본 설정을 통한 정상/콜드 부팅 반복 검증은 미수행**이다.

## 기존 기능 복귀

- Debian domid1, zephyr-drive-a domid2, zephyr-drive-b domid3 실행 확인. 기존 게스트 firmware 변경이나 주행 명령 주입은 없었다. 호스트 재부팅에 따른 게스트 재시작은 발생했다.
- systemctl --failed: 0개. xenova-dom0-manager와 recovery-worker는 active/running, xenova-vms·first-boot·bridge 등은 정상 완료 상태다. xendomains는 기존 masked 상태를 유지했다.
- CmaTotal=65536kB, CmaFree=59320kB, MemAvailable=636440kB를 후속 조회했다. 이는 순간 관측이며 장시간 성능 수락이 아니다.
- 부팅 dmesg에는 v3d 경로의 phandle 73을 찾지 못했다는 경고가 있었다. VC4 KMS 및 HDMI 두 포트의 binding/연결은 성공했지만 **V3D/3D 가속 정상 동작을 증명하지 않는다.** 해당 경고를 수정하기 위해 임의 커널/DT 변경은 추가하지 않았다.

## 남은 작업과 복원

HDMI 포트 인식·기본 KMS 활성화 단계까지 완료했다. 다음에는 기존 fbdev/fb1 전용 시험 실행부를 KMS modesetting 기반으로 바꾸고, 두 실제 커넥터의 EDID·좌표를 사용해 계기판/인포테인먼트를 동시에 배치해야 한다. 독립 전환·분리/재연결과 정상 부팅 반복, 실제 두 출력 캡처 성능도 별도 검증한다. 256 전체를 완료 처리하지 않았다.

기존 설정으로 되돌려야 한다면 관리자가 아래 원본을 복원하고 정상 재부팅한다. 이는 복원 절차이며 이번에는 실행하지 않았다. 부팅 자체가 불가능하면 SD 카드의 boot 파티션에서 같은 원본 config.txt를 복원해야 한다.

```sh
cp /root/xenova-hdmi-backup/20261028-060513/config.txt /boot/config.txt
sync
systemctl reboot
```

현재 변경은 실보드 boot 영역에 적용한 것이며 Yocto 이미지 패키징 규칙에는 아직 반영하지 않았다. 새 이미지로 재설치할 경우 overlay 포함과 설정의 재현이 필요하다. 커밋·푸시·지라 상태 변경은 하지 않았다.

증거: [최종 설정 반영](evidence/display-256-dual-hdmi-20261028/persisted-hdmi-config-20261028.json), [시험 재부팅 및 DRM](evidence/display-256-dual-hdmi-20261028/tryboot-retry-result-20261028.json), [VM·서비스 복귀](evidence/display-256-dual-hdmi-20261028/post-tryboot-health-20261028.json), [소스 manifest](evidence/display-256-dual-hdmi-20261028/source-manifest.json).

공식 근거: [고정 Yocto 커널 recipe](https://raw.githubusercontent.com/agherzan/meta-raspberrypi/919588393f41b48cb6c5d832aa35e58a19b6762d/recipes-kernel/linux/linux-raspberrypi_6.18.bb), [Pi4 overlay 소스](https://github.com/raspberrypi/linux/blob/95b85bebbedcaedfa7ca79116ed38b7376fba412/arch/arm/boot/dts/overlays/vc4-kms-v3d-pi4-overlay.dts), [tryboot 설명](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#fail-safe-os-updates-tryboot).

## 후속 동시 표시 시험

이후 실제 두 출력에 계기판·인포테인먼트를 동시에 배치하고 독립 새로고침·VM 선택을 검증했다. [후속 시험 결과와 남은 범위](display-256-dual-screen-test-20261028.md)를 참고한다. 위 남은 작업 중 동시 배치·독립 선택 검증은 이 후속 시험으로 수행했다.
