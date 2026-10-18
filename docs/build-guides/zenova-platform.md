# Zenova 플랫폼 실행·빌드·최초 설치

현재 소스의 공식 명칭은 Zenova다. 이 가이드의 명령은 새 Zenova 이미지용이며,
이름 변경 후의 WIC·펌웨어·실보드 성공을 주장하지 않는다.
기존 `perseus-*` 문서와 이전 `yocto-rpi4-xen-*` 문서는 당시 증거로 보존한다.

## 설치·실행 계약

| 대상 | 현재 값 |
| --- | --- |
| 프로젝트 레이어·collection | `platform/yocto/meta-zenova` · `zenova` |
| dom0 이미지 | `zenova-xen-bringup-image` |
| 진단 initramfs | `zenova-domu-initramfs` |
| 패키지 | `zenova-platform` · `zenova-bringup` · `zenova-domu-init` |
| 영구 데이터 LABEL·마운트 | `zenova-data` · `/srv/zenova` |
| 런타임·잠금·설정 | `/run/zenova` · `/etc/zenova` |
| Python·공유 자료 | `/usr/lib/zenova` · `/usr/share/zenova` |
| 관리·초기화 | `zenova-vm` · `zenova-first-boot` |
| 데이터 mount unit | `srv-zenova.mount` |
| VM 자동 시작·종료 | `zenova-vms.service` · `zenova-vm-shutdown.service` |
| 새 Starter ID | `zenova-zephyr-starter-1.0.0` |

기본 VM ID는 `ubuntu-dev`·`debian-dev`·`zephyr-dev`다. UUID/MAC 생성·검증,
Linux 독립 디스크·machine-id·SSH 키 보존, 정적 Zephyr 하드웨어 프로필은 유지한다.
등록 정보는 `/srv/zenova/instances/<id>/instance.json`, 불변 템플릿은
`templates/`·`rtos-templates/`에 둔다. 펌웨어 revision은 실제 manifest 해시다.
`guest_kind`와 Xen domain type, 등록 여부·Xen 상태·앱 준비 상태는 계속 구분한다.

파티션 탐색과 확장에는 `zenova-data`를 사용하며 `/run/zenova/data-device`를 통해
`srv-zenova.mount`로 연결한다. 기존 WIC는 부트/rootfs 2개 파티션이고,
최초 설치 조립기가 세 번째 data 파티션을 만든다. 별도 fstab/WKS 우회 경로를
추가하지 않는다. 부트 파일 `xen`·`Image`·`boot.scr`, 외부 Poky/Xen/Raspberry Pi
명칭과 Git 저장소 URL·브랜치는 유지한다. U-Boot 디버그 변수는 `zenova_debug`다.

## 실행 가능한 소스 검사

WSL Ubuntu에서 저장소 루트와 이미 준비된 분석기를 사용한다.
환경변수의 분석기 값은 JSON argv이며 dom0는 systemd 259 이상, guest는 257 이상이어야 한다.

```bash
cd ~/S15P31E110
export ZENOVA_DOM0_SYSTEMD_ANALYZE='["/absolute/systemd-analyze-259"]'
export ZENOVA_SYSTEMD_ANALYZE='["/absolute/systemd-analyze-257-or-later"]'
bash scripts/check-zenova-platform.sh
rc=$?; echo "regression rc=$rc"; [ "$rc" -eq 0 ] || exit 1
```

`--metadata`는 기존 고정 Layer를 읽고 /tmp 아래 새 구성에서 BitBake 파싱과
`-e`만 실행한다. `YOCTO_BUILD_DIR`에는 보존 대상 기존 build conf 경로를 지정한다.
원본 conf 해시를 전후 비교하며 이미지/패키지/커널을 빌드하지 않는다.
구버전 분석기로 의존성을 무시해 통과시키지 않는다. 과거 전체 dom0 fixture와
새 소스의 유닛 투영은 서로 구분하며 generator/PID1 실행은 이 검사의 범위 밖이다.

## 후속 승인 후 dom0 빌드

새 Linux 파일시스템 경로를 사용한다. 기존 빌드의 관리 블록·레이어 경로를 덮어쓰지 않는다.
외부 버전은 `platform/yocto/layers.lock` 그대로이며 신규 다운로드·버전 변경은 필요하지 않다.

```bash
cd ~/S15P31E110
export YOCTO_WORKSPACE="$HOME/yocto-workspace"
export YOCTO_BUILD_DIR="$YOCTO_WORKSPACE/build-rpi4-zenova"
bash scripts/setup-yocto.sh --verify
rc=$?; echo "setup rc=$rc"; [ "$rc" -eq 0 ] || exit 1
source "$YOCTO_WORKSPACE/layers/openembedded-core/oe-init-build-env" "$YOCTO_BUILD_DIR"
bitbake zenova-xen-bringup-image > zenova-build.log 2>&1
rc=$?; echo "build rc=$rc"; [ "$rc" -eq 0 ] || exit 1
```

새 WIC는 `tmp/deploy/images/raspberrypi4-64/zenova-xen-bringup-image-*.wic.bz2`다.
실제 rootfs의 모든 새 패키지·유닛·enable/preset·import와 SSH key gate를 검사하고,
실제 QEMU VNC 비활성·xenpv/backend 및 Xen parser는 실행 환경별로 구분한다.
기존 종료 관측·dom0 SSH·BCM2711 reset 수정도 새 산출물에서 확인한다.

## 템플릿·seed·펌웨어·최종 조립의 경계

Linux upstream ID·버전·digest는 `platform/guests/sources.lock.json`에 그대로다.
이전 준비 템플릿 안에는 `/etc/perseus`·identity unit·SSH Include가 있을 수 있다.
이 원본이나 기존 VM을 덮어쓰지 않는다. 새 Zenova 준비 템플릿은 이미 검증한
OS 입력에서 **새 경로에** 만들고 새 manifest 해시를 사용한다. 재다운로드가 필수는 아니다.
이전 marker의 root는 새 instance/seed 준비 함수에서 거부한다. 기존 VM의 신원을
Zenova 이름으로 자동 이전하는 기능이나 호환 CLI는 제공하지 않는다.

Starter는 안내문·앱 이름·카탈로그 ID·소스 해시가 바뀌었다. Zephyr 4.4.0,
SDK 1.0.0, 앱 1.0.0, 1 vCPU/16MiB/GICv2는 유지한다. 이전 펌웨어/manifest를
새 이름으로 다시 쓰지 않고, 후속 승인 후 새 빌드와 불변 번들을 준비한다.
현재 소스 provenance는 `starter.lock.json`, 실제 binary revision은 향후 새
`firmware.json`의 SHA-256이다. 자세한 명령은 Starter README를 따른다.

새 dom0 게시 도구는 `scripts/publish-zenova-platform.py`,
Linux 준비 도구는 `scripts/prepare-zenova-guests.py`,
펌웨어 게시 도구는 `scripts/package-zenova-firmware.py`다.
`platform/first-install/example.config.json`에는 새 dom0·준비 템플릿·Starter의
**실제 경로·해시**를 입력한다. 조립기 `scripts/assemble-zenova-first-install.py`는
`plan`·`assemble --execute`·`verify`를 제공하고 출력은
`zenova-first-install.img`·`zenova-first-install.manifest.json`이다.
이전 dom0는 소스 일치 gate에서 거부하며 검사 조건을 완화하지 않는다.

## 후속 보드 수락

모든 단계의 종료 코드를 확인하고 실패 시 중단한다. UART 전체 로그를 외부 PC에
보존한다. `zenova-vm status`와 `diagnose`, `findmnt /srv/zenova`,
`lsblk -f`로 label·mount·등록·시간 상태를 확인한다.
`systemctl --no-pager --full status sshdgenkeys.service sshd.service`,
`ss -lntp`와 실제 SSH 접속으로 dom0 SSH를 확인한다. failed unit 수만으로 통과시키지 않는다.

세 VM 실행 중 `zenova-vm reboot-board` 한 번으로 Linux graceful, 승인된
Zephyr forced, domain-removed, 저장소 정리, reset, 세 VM 자동 시작과
SSH 자동 준비를 각각 판정한다. 일시적 조회 변화는 단일 monotonic 마감시간 안에서
재관측하며 교체·미등록 도메인·조회 실패는 보드 요청을 막는다.
전역 `--force`는 Linux에도 적용되므로 일반 수락 시험에서 쓰지 않는다.
`autostart=on`은 강제 종료 승인이 아니며 앱 준비는 계속 unknown이다.

우선 5회 반복을 제안하며 회차별 결과와 UUID/MAC/키/시험 파일/펌웨어·정책 보존을
기록한다. SSH 수집 파일은 명령 성공 후에만 확정한다. poweroff는 별도로 시험한다.
직접 systemctl reboot/poweroff는 CLI 사전 검사 밖이며 ExecStop 실패로 OS 종료가
취소된다고 가정하지 않는다. 실제 xl parser·하드웨어 reset·네트워크·SSH·앱 동작은
소스 검사나 모의 Xen 성공과 구분한다.
