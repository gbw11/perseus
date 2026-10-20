# Perseus 영구 플랫폼 — 2026-10-16 소스 구현·검증 결과

## 상태

설계·소스·재실행 가능한 검사·후속 수락 절차를 반영했다. **사용자가 승인한 python3-json 의존성을 포함해 최종 정적 검증을 완료했다.** 실보드에서 영구 Ubuntu/Debian이 부팅됐다는 뜻은 아니다.

2026-10-16 사용자 승인 후 `perseus-platform_1.0.bb`의 RDEPENDS에 기존 고정 OE-core의 `python3-json`을 추가했다. 외부 Python 라이브러리·레이어 버전·lockfile은 변경하지 않았다. 사용자가 확인한 보드 RAM은 8GB이며 기존 dom0 1024MiB·Ubuntu 768MiB·Debian 512MiB 배분은 유지했다.

## 핵심 결정과 변경

운영 계약 전체는 [영구 플랫폼 가이드](perseus-persistent-platform.md)에 있다.

- 1차 감사의 MMC 선택, Control FDT 복사, dnsmasq bindir, networkd 격리, BusyBox 초기화 수정은 보존했다.
- 기존 `perseus-xen-bringup-image`에 영구 플랫폼 레시피를 연결했다. 브릿지·DHCP 기본 활성화, 물리/예상 외 포트가 있으면 DHCP 시작 거부, UDP/67 준비 검사와 DHCP 예약 reload를 추가했다.
- `perseus_vm.py`는 저장소·Xen 제어 인터페이스·xl·VM 자산을 검사하고 실행·정상 종료·재부팅을 관리한다. start에는 생성/포맷/복사/암호 재설정이 없다. 중복 ID/MAC/IP/디스크 및 템플릿과 같은 inode 사용도 거부한다.
- `xendomains.service`를 플랫폼 이미지에서 마스킹하고 `perseus-vms.service`를 단일 자동 시작 소유자로 정했다. 시작 실패와 무관하게 종료 보호 서비스가 armed되도록 분리했다.
- 영구 영역은 `LABEL=perseus-data` ext4, `/srv/perseus`다. 사전 마운트·등록 표식 없이는 시작하지 않는다. 현재 SD의 파티션·파일시스템·기존 데이터는 그대로다.
- Ubuntu 24.04/20260926, Debian 13 generic/20261015-2632 ARM64의 원본 URL·체크섬·커널 패키지 버전을 고정했다. 같은 배포판 이미지의 rootfs/커널/모듈로 initramfs를 준비하고 dom0 커널은 재사용하지 않는다.
- 최초 템플릿/인스턴스 생성은 별도 호스트 도구이며 기본은 PLAN ONLY다. 기존 출력 경로를 거부한다. root / 1234, 고유 UUID/machine-id/호스트 키는 최초 준비에만 설정한다.
- 일반 stop은 90초 제한의 정상 종료이며 강제 destroy를 호출하지 않는다. 관리 보드 종료 명령은 게스트 종료·sync·저장소 해제 성공 뒤에만 전원 요청을 한다. systemd 자체의 이미 진행 중인 종료를 ExecStop 실패로 취소할 수 없다는 제한도 명시했다.
- 외부 LAN을 내부 브릿지에 편입하지 않는다. 팀원은 dom0를 통한 SSH jump/scp를 사용하고 게스트 패키지 다운로드는 오프라인 전달 또는 별도 승인한 SSH 프록시 정책으로 다룬다.

## 정적 검사 결과

| 검사 | 결과/한계 |
| --- | --- |
| `bash scripts/check-perseus-platform.sh --metadata` | 종료 코드 0. 계약 unittest 34개 + 기존 bring-up 회귀 17개, 셸 문법·diff 검사 통과 |
| 분리된 `bitbake -p` | 3213개 레시피, 오류 0. WSL VHDX 권고 경고 1건 |
| `bitbake -e` 5개 레시피 | 부트/network 소스 우선순위, enable/mask, bindir, 비어 있지 않은 root 암호 설정·자동 시작 연결 확인 |
| `check_perseus_metadata.py` | 통과. 승인한 `python3-json`을 포함한 필수 런타임 패키지 선언 확인. 승인 전 발견한 의존성 누락은 소스에서 해결 |
| 기존 타깃 Python/QEMU | 원래 rootfs만 사용하면 `ModuleNotFoundError: json`. 기존 json 패키지를 임시 PYTHONPATH로만 참조하면 import/CLI help 통과 |
| 기존 타깃 OpenSSH/QEMU | 제안 초기 정책과 기존 config를 임시로 결합해 전역 파싱 확인: root yes / password yes / empty no / AuthenticationMethods any |
| root 암호 해시 | 1234 일치, 다른 값 불일치 확인. 실제 계정 파일을 변경하지 않음 |
| 기존 conf 2개·boot.scr·Image·Xen | 1차 감사의 SHA-256 보존 확인 |
| 고정 레이어·설정·Git HEAD | layers.lock/공통 fragment 불변, 외부 6개 checkout clean/고정 SHA 유지. HEAD 7152ce4, commit/push 없음 |

최종 파싱·레시피 값 로그는 `/tmp/perseus-platform-static.bBb8nB/logs`, 통합 실행 로그는 `/tmp/perseus-platform-approved-check-20261016.log`다. 재실행할 소스와 명령은 저장소의 scripts/tests에 남겼다. 이 임시 경로가 없어도 기본 회귀 검사를 다시 실행할 수 있다. 기존 빌드 conf를 수정하거나 빌드·캐시를 파싱 상태 저장소로 사용하지 않았다.

SSH per-connection `-T -C` 검사는 키 없는 템플릿 단계에서 실패할 수 있어, 인스턴스의 고유 키 생성 후 게시 전에 실행하도록 배치했다. 이번에는 실제 키·템플릿·인스턴스를 만들지 않아 그 단계는 미실행이다.

## 아직 실행하지 않은 검증과 다음 승인

1. 필요한 호스트 qemu-img/ARM64 chroot/binfmt 도구 준비 승인. 이번에 호스트 패키지를 설치하지 않았다.
2. dom0 이미지 빌드와 Ubuntu/Debian 템플릿·인스턴스 실제 생성 승인. kernel/initramfs/SSH full 검사와 최종 rootfs/unit/계정 상태 확인.
3. 데이터 매체 선택·label/마운트·필요한 파티션/포맷·SD 기록/배포 승인. 현재 카드의 남는 영역을 자동 사용하지 않음.
4. 실제 보드에서 자동 부팅, 두 VM SSH/콘솔, 파일·패키지·서비스·변경 암호 보존, 정상 종료·실패 처리 수락 시험.
5. 검토 후 commit/push 별도 승인.

## 변경 파일 목록

아래는 현재 작업 트리 전체 목록이다. 이 중 1차 감사부터 존재한 부팅/networkd/BusyBox 소스 및 감사 문서는 유지한 수정이며, 이번 단계의 변경과 함께 포함된다.

```text
docs/build-guides/perseus-persistent-platform.md
docs/build-guides/perseus-platform-implementation-20261016.md
docs/build-guides/yocto-rpi4-xen-build-versions.md
docs/build-guides/yocto-rpi4-xen-minimal-bringup.md
docs/build-guides/yocto-rpi4-xen-reproduce.md
docs/build-guides/yocto-rpi4-xen-source-audit-20261016.md
platform/guests/common/prepare_rootfs.py
platform/guests/sources.lock.json
platform/yocto/meta-perseus/recipes-bsp/xen-rpi-u-boot-scr/files/boot.cmd.xen.in
platform/yocto/meta-perseus/recipes-bsp/xen-rpi-u-boot-scr/xen-rpi-u-boot-scr.bbappend
platform/yocto/meta-perseus/recipes-core/images/perseus-xen-bringup-image.bb
platform/yocto/meta-perseus/recipes-core/perseus-bringup/files/dnsmasq-bringup.conf
platform/yocto/meta-perseus/recipes-core/perseus-bringup/files/perseus-bridge.service
platform/yocto/meta-perseus/recipes-core/perseus-bringup/files/perseus-dhcp.service
platform/yocto/meta-perseus/recipes-core/perseus-bringup/perseus-bringup_1.0.bb
platform/yocto/meta-perseus/recipes-core/perseus-domu-init/files/init
platform/yocto/meta-perseus/recipes-core/perseus-platform/files/defaults.json
platform/yocto/meta-perseus/recipes-core/perseus-platform/files/perseus-auth.conf
platform/yocto/meta-perseus/recipes-core/perseus-platform/files/perseus-vm
platform/yocto/meta-perseus/recipes-core/perseus-platform/files/perseus-vm-shutdown.service
platform/yocto/meta-perseus/recipes-core/perseus-platform/files/perseus-vms.service
platform/yocto/meta-perseus/recipes-core/perseus-platform/files/perseus-xen-ready.service
platform/yocto/meta-perseus/recipes-core/perseus-platform/files/perseus_vm.py
platform/yocto/meta-perseus/recipes-core/perseus-platform/files/srv-perseus.mount
platform/yocto/meta-perseus/recipes-core/perseus-platform/perseus-platform_1.0.bb
platform/yocto/meta-perseus/recipes-extended/xen/files/10-ether.network
platform/yocto/meta-perseus/recipes-extended/xen/files/10-xenbr0.network
platform/yocto/meta-perseus/recipes-extended/xen/xen-tools_%.bbappend
scripts/check-perseus-platform.sh
scripts/prepare-perseus-guests.py
tests/platform/check_perseus_bringup_regression.py
tests/platform/check_perseus_metadata.py
tests/platform/test_perseus_platform.py
```
