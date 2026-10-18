# 공통 SD 최초 설치 이미지 — 소스 계약과 후속 실행 절차

이 문서는 WIC 수정 `70c85b90ef21e73b4d7b9daa97a65c0cacf3078c` 위의 후속 소스 구현이다. **소스·정적/모의 검증·후속 dom0 빌드·완료 이미지 게시와 Ubuntu/Debian·Zephyr 템플릿 생성을 완료했다. 인스턴스/seed 생성·최종 조립·SD 기록·실보드 부팅은 실행하지 않았다.** 파일 내부 검사 통과와 실보드 성공은 구분한다. 완료 산출물 인계 수정과 검증 결과는 [후속 보고](perseus-completed-artifacts-20261016.md)에, 실제 템플릿·64GB 실측 계획과 조립 전 보완할 seed SSH 검사 문제는 [Linux 템플릿 생성 보고](perseus-guest-templates-20261016.md)와 [Zephyr 추가 보고](perseus-zephyr-template-20261016.md)에 기록했다.

**2026-10-17 소스 갱신:** 공통 Zephyr 영구 관리·조기 잠금·Linux target 순서·QEMU VNC 옵션·로그/진단 변경을 포함한 dom0 재빌드가 필요하다. 과거 완료 WIC는 보존 기준이며 새 조립의 소스 일치 gate를 통과하지 않는다. 기존 세 템플릿은 변환 없이 재사용한다. 현재 CLI·펌웨어 배포·실보드 DTB 검사는 [영구 플랫폼 후속 계약](perseus-persistent-platform.md#2026-10-17-공통-관리펌웨어-배포-계약)을 따른다.

최종 사용자는 승인 후 만들어진 공통 `.img`를 선택 → 64GB/128GB SD에 기록 → 전원을 연결한다. 아래 절차는 그 이미지를 만드는 담당자용이다. SD 전체 재기록은 기존 데이터를 덮어쓰며, 데이터 보존 업데이트·백업·복원은 이 조립기 범위가 아니다.

## 확정한 저장소 정책

- 32GB 지원은 제외한다. 카드 상품 표기를 사용하지 않고 실제 장치 바이트 수로 판정한다.
- Ubuntu·Debian 템플릿의 **전체 root.raw 원본을 SD에 보관**한다. 기본 VM은 `ubuntu-dev`, `debian-dev`이고 각각 별도 8GiB raw 디스크를 사용한다. 기존 기본 메모리·vCPU·내부 IP 정책도 유지한다.
- 세 번째 템플릿은 Zephyr 4.4.0 Xen ARM64 콘솔 펌웨어다. Ubuntu·Debian과 불변 원본을 `rtos-templates/`에 보관하고 새 이미지에 독립 `zephyr-dev` seed를 함께 준비한다. 최초 부팅에 공통 registry로 등록하되 자동 시작은 false다. Linux 디스크·SSH·DHCP는 사용하지 않는다. [RTOS 계약](../../platform/guests/zephyr/README.md)을 따른다.
- 템플릿은 읽기 전용 sparse raw ext4를 선택했다. 압축/overlay 계층 없이 native ARM64에서 기존 생성 코드를 재사용할 수 있다. 2개 템플릿과 2개 VM의 논리 용량 32GiB에 시스템·메타데이터·운영 여유를 더해 64GB 범위를 검토한다. 압축 보관은 이번에 구현하지 않았다.
- 시작·정지·재부팅은 디스크를 복제하거나 초기화하지 않는다. 최초 생성은 별도 명령이다. 템플릿을 갱신해도 기존 VM은 덮어쓰지 않는다.

```text
파티션 1: 기존 FAT / U-Boot / Xen / Image / DTB
파티션 2: 기존 Yocto dom0 rootfs
파티션 3: ext4 LABEL=perseus-data → /srv/perseus
  templates/<template-id>/           불변 원본 6개 파일, root.raw 포함
  rtos-templates/<template-id>/      불변 Zephyr 펌웨어 원본
  seeds/{ubuntu-dev,debian-dev}/     최초 배포용 Linux 디스크·pending 설정
  seeds/zephyr-dev/                  독립 Starter 펌웨어·pending UUID·자동 시작 true·보드 종료 force-allowed
  instances/<id>/                    등록된 VM의 고유 설정·독립 디스크
  .perseus-install.json              공통 설치 자산 manifest
  .first-install-state.json          단계·계획된 고유 식별자·등록 진행
  .perseus-storage.json              해당 설치에 한 번 발급한 저장소 UUID
```

조립기는 기존 1·2번 파티션 위치·내용과 DOS disk identifier를 보존하고 새 3번 항목만 추가한다. 따라서 U-Boot의 `part uuid mmc ${devnum}:2`와 `root=PARTUUID=` 참조도 유지한다. GPT/확장 파티션/기존 3번이 있는 입력은 받지 않는다. 512-byte sector의 DOS/MBR, 최대 2TiB 범위만 지원한다.

## 입력과 계획

진입점은 `scripts/assemble-perseus-first-install.py`다. `plan`이 기본 동작이며 읽기만 한다. 명시적인 `assemble --execute`만 생성 경계를 넘는다. 실행 환경은 승인된 Linux root 준비 호스트이며, ARM64 게스트 chroot를 실행할 native ARM64 또는 QEMU/binfmt 환경이 필요하다. 필요한 도구가 없으면 실패하며 설치하지 않는다.

`platform/first-install/example.config.json`을 저장소 밖의 실제 설정으로 복사하고 모든 placeholder를 실제 값으로 바꾼다. 입력은 시각이 포함된 일반 파일의 절대 경로와 해시로 선택한다. latest 및 다른 symlink 경로는 받지 않는다.

플랫폼 입력은 **빌드 경로 밖에 원자적으로 게시한 완료 사본**이어야 한다. `platform-release.json`/해시와 같은 디렉터리의 이미지/해시·`BUILD-RESULT.json`/해시를 명시한다. 조립기 plan/assemble 모두 원래 빌드 경로와 `bitbake.lock`에 접근하지 않는다. 게시된 디렉터리와 파일은 읽기 전용이며 hardlink가 아닌 독립 일반 파일이어야 한다. 체크섬·출처·완료 상태가 다르거나 임시 게시 경로이면 거부한다.

게시 도구 `scripts/publish-perseus-platform.py`는 기존 완료 이미지의 복사만 담당하며 BitBake/게스트 준비/최종 SD 조립을 실행하지 않는다. 기본은 완료 기록과 예상 복사량을 읽는 계획이며 원본 이미지 bytes나 잠금은 열지 않는다. `--execute`에서만 BitBake 호환 exclusive flock을 잡고, 필요하면 실제 잠금 파일을 생성한다. 획득 inode가 현재 경로와 같은지 확인하고 원본 해시→독립 복사→사본 해시→읽기 전용 manifest/체크섬→덮어쓰기 없는 원자적 게시까지 잠금을 유지한다. 기존 잠금 파일은 보존하고 직접 생성한 동일 inode만 잠근 상태에서 정리한다. 잠금을 만들기 위한 수동 touch나 단순 파일 부재 무시는 사용하지 않는다.

원본 `BUILD-RESULT.json`에는 `build_exit_code: 0`, `finished_at`, `build_directory`, 40자리 `source_commit`, 선택 이미지의 `path`/`bytes`/`sha256` 객체(`wic` 또는 `rpi_sdimg`)가 있어야 한다. 원본 기록은 내용 변경 없이 복사한다. 실패/미완료 기록, 해시 불일치, 실행 중 빌드, 기존 출력은 거부한다. 중단된 전용 staging은 조사용으로 남지만 조립 입력으로 받지 않는다.

```bash
# 게시 계획만. 출력 디렉터리는 아직 없어야 하며 부모는 존재해야 한다.
python3 -B scripts/publish-perseus-platform.py \
  --build-record /absolute/build-logs/BUILD-RESULT.json \
  --build-record-sha256 <기록의_SHA256> \
  --image-kind wic --output-dir /absolute/completed-platform
# 실제 게시가 승인된 때에만 같은 명령에 --execute를 추가한다.
```

게시 결과의 `platform` 블록을 배포 설정에 복사한다. 원본 build 기록의 성공/해시를 임의로 꾸미지 않는다. 게시 후 원래 빌드가 없어지거나 다음 빌드가 시작되어도 조립기는 고정 사본만 읽는다. 사용 중 사본이 바뀌면 실패한다. 실행 중인 원본 빌드를 막는 책임은 게시 단계에 있고, 조립 단계는 그 빌드와 독립적이다.

각 게스트에는 검증된 `template_dir`과 `template.json`의 SHA-256을 지정할 수 있다. 조립기는 root/boot/config 해시·논리 크기·읽기 전용 속성·고정 `sources.lock.json`의 출처 일치를 검사한다. 이미 사용한 VM 디스크는 템플릿으로 받지 않는다.

새 템플릿 준비도 기존 `scripts/prepare-perseus-guests.py`를 호출한다. 이 경우 `template_dir` 대신 `input`, `size_gib: 8`, 선택적으로 `source_virtual_limit_bytes`(최대 64GiB)를 설정한다. input은 고정 QCOW2 원본이며 공식 해시와 비교한다. 원본이 없을 때는 `download: true`와 별도 승인된 `assemble --execute`가 모두 있어야만 고정 URL로 다운로드한다. 계획은 다운로드하지 않고 보수적 상한을 계산한다. 원본·기존 템플릿은 수정하지 않는다.

```bash
# 계획만 출력. 측정값은 모의 예시가 아닌 실제 대상 카드 용량으로 입력한다.
python3 -B scripts/assemble-perseus-first-install.py plan \
  --config /absolute/deployment.json \
  --output-dir /absolute/new-release \
  --work-dir /absolute/new-scratch \
  --card-bytes <실제로_측정한_바이트_수>
```

계획은 비압축 이미지 크기/최소 카드 바이트, VM·템플릿의 논리 크기와 현재 할당량, 기존 VM의 남은 성장 가능량, 메타데이터 여유(현재 보수적 5%+256MiB), 운영 여유(최소 2GiB), WSL 임시 공간 상한을 출력한다. Sparse의 작은 현재 점유량으로 VM 최대 용량을 과할당하지 않는다. 메타데이터 계수는 실측 보장이 아닌 초기 보수적 정책이며 실제 조립 후 여유 공간도 확인한다.

예를 들어 **기존** 플랫폼 3,347,054,592 bytes, 8GiB 디스크 네 개, bundle 부팅 자산 합계 상한 2GiB 조건의 모의 계산은 최소 **44,098,912,256 bytes**다. 원본 준비 scratch를 제외한 WSL 상한 예시는 91,544,879,104 bytes다. 이는 아직 빌드/준비하지 않은 최종 산출물의 실측값이나 현재 연결 SD의 측정값이 아니다. 32,000,000,000 bytes는 부족으로 실패하고, 모의 63,864,569,856/127,865,454,592 bytes는 통과했다. 실제 계획은 선택한 새 이미지와 템플릿 크기로 다시 계산한다.

## 실제 조립의 강제 gate와 출력

계획에서는 mount 없이 해시·완료 기록·레이아웃·용량을 검사하므로 `platform_features_verified: false`를 명시한다. 실제 조립 전에는 자기 작업 경로에 플랫폼 파일을 풀고 자기 readonly loop의 2번 rootfs를 `ro,noload`로 읽어 다음을 검사한다.

- 실제 FAT의 Xen·Image·U-Boot·DTB·부팅 스크립트, 스크립트 CRC와 Xen/FDT/PARTUUID 명령.
- 실제 rootfs의 capability 파일, 현재 소스와 일치하는 최초 부팅/공유 생성 모듈·unit·enable 링크.
- 확장·생성 도구, SSH key 생성 선행 drop-in, `machine-id=uninitialized`, 공유 SSH host key 부재.

**최초 설치 소스 이전의 성공 이미지는 기능 부족으로 거부한다.** 후속 빌드 `build-rpi4-xen-first-install-20261016-142155`는 종료 코드 0과 실제 WIC 최초 부팅 기능 47개 검사를 통과했다. 완료 이미지 게시는 수행했으며 실제 조립은 아직 실행하지 않았다. seed SSH/chroot 검증 경로는 [후속 보완](perseus-seed-ssh-fix-20261016.md)에 반영했다. 해당 소스를 포함한 새 dom0 재빌드가 필요하다. 조립기가 rootfs에 서비스나 실행 파일을 끼워 넣어 우회하지 않는다. 게스트 준비는 이 gate가 통과한 뒤에만 시작한다.

승인 후 계획 명령의 `plan`을 `assemble`로 바꾸고 `--execute`를 추가한다. 출력/작업 디렉터리는 기존 것이 없어야 하며 동일 filesystem에 두어야 한다. `/dev`, `/proc`, `/sys` 또는 symlink 경로를 출력으로 받지 않는다. 할당한 loop와 mount만 추적해 해제하고, 해제 실패 시 작업 경로를 보존하고 실패한다. 임시 경로도 임의 삭제하지 않는다.

새 데이터 ext4 파일에 세 템플릿과 Linux 두 seed·Zephyr 한 seed를 준비한다. 실제 free bytes가 VM의 남은 성장 가능량+운영 여유를 충족하는지 검사한다. 기존 FAT/root 파티션 해시 보존과 최종 파일/레이아웃/파티션 복사 검증 후에만 `renameat2(NOREPLACE)`로 새 출력 디렉터리를 게시한다. 중단된 작업은 완성 이름으로 공개하지 않고 기존 출력도 덮어쓰지 않는다.

```text
new-release/
  perseus-first-install.img
  perseus-first-install.manifest.json
  SHA256SUMS
```

manifest에는 선택 입력·고정 버전/해시·템플릿·seed root 해시·소스/배포 설정·파티션 레이아웃·최소 용량·실제 여유 공간·자동 시작 정책·수행 검증과 미수행 하드웨어 시험을 기록한다. 미커밋 소스 사용 여부와 조립기/준비 도구 소스 파일 해시도 기록하고, 준비 중 소스가 바뀌면 게시하지 않는다. 결과 재검사는 일반 파일만 읽는다.

```bash
python3 -B scripts/assemble-perseus-first-install.py verify \
  --manifest /absolute/new-release/perseus-first-install.manifest.json
```

## 최초 부팅·재실행·식별자

1. 이미지 제작 때 dom0 `/etc/machine-id`를 `uninitialized`로 만들고 dbus ID를 그 파일로 연결한다. 공통 host key/random seed도 넣지 않는다. **PID1이 부팅 초기에 ID를 확정하며 실행 중 ID를 바꾸지 않는다.** 기존 OpenSSH key generator를 실제 SSH listener보다 앞에 필수 의존성으로 둔다.
2. `perseus-data-prepare.service`가 현재 `/`의 major:minor와 sysfs를 통해 실제 root 2번·같은 부모의 data 3번을 찾는다. root PARTUUID와 부팅 인자를 대조한다. 다른 연결 매체의 동일 root PARTUUID가 발견되면 추측하지 않고 거부한다. label 첫 검색이나 고정 mmc 번호로 장치를 선택하지 않는다.
3. 실제 `blockdev --getsize64`까지 마지막 파티션만 늘리고(서명 wipe는 명시적으로 `never`) `partx --update --nr 3`로 커널 반영을 확인한 후 offline ext4 검사/확장을 수행한다. 기존 시작 위치·앞 파티션·filesystem UUID는 보존한다. 64/128GB 모두 같은 계산이며 이미 확장됐으면 크기 변경 명령을 실행하지 않는다. VM root.raw와 게스트 filesystem은 늘리지 않는다.
4. 검증한 실제 장치에 대한 `/run/perseus/data-device`를 통해 `srv-perseus.mount`를 수행한다. `perseus-first-boot.service`가 해당 mount와 실제 자산을 검증하고, VM별 UUID/MAC·저장소 UUID를 먼저 영구 journal에 저장한다. seed 설정 저장→디렉터리 rename→등록 단계 갱신으로 진행한다. 중단 후에는 같은 계획을 재사용한다. 기존 인스턴스/불일치/손상은 삭제·초기화하지 않고 실패한다.
5. 전체 registry 검증 후 완료 상태를 기록하고 기존 `perseus-vms.service`에 인계한다. 자동 시작 소유자는 여전히 이 서비스 하나이며 `xendomains`는 mask한다. Xen 준비·종료 보호 조건을 공통으로 요구하고 DHCP는 Linux 시작에만 요구한다. 불완전 설치는 CLI의 storage 검사와 서비스 의존성에서 VM 시작을 막는다.
6. 공통 seed의 guest machine-id는 systemd가 최초 부팅 때 생성한다. 일반 서비스 단계에서 guest identity service가 성공한 뒤 ssh.service가 시작된다. socket enable·RequiredBy 링크를 제거하고 SSH socket generator도 명시적으로 비활성화한다. 이후에는 ID/키 존재와 일치만 확인하며 암호·키·ID를 다시 발급하지 않는다. 완료 기록과 실제 자산이 불일치하면 SSH를 막고 진단한다.

진행 상태만 보고 skip하지 않는다. 매 부팅 root.raw의 일반 파일/독립성/논리 크기, boot/config 해시, registry/템플릿을 확인한다. 사용 중인 raw의 내용은 바뀌므로 최초 SHA로 매 부팅 덮어쓰거나 원복하지 않는다. 전체 raw 해시는 PC 조립 시와 신규 생성 직전 불변 템플릿 검증에 사용한다.

MAC은 보드별·인스턴스별 `02:`의 로컬 주소를 발급해 보존한다. 고유 범위는 물리 NIC가 편입되지 않는 각 `xenbr0`다. 기본 이름·hostname·IP `ubuntu-dev/.10`, `debian-dev/.20`는 유지한다. domid/VIF 이름은 영구 식별자로 저장하지 않는다. xl 제어 인자는 UUID/name 재검증 후의 현재 domid다. raw 내부 filesystem UUID와 부팅용 PARTUUID는 맹목적으로 재발급하지 않는다.

확장 도구는 각 최대 120초, 커널 반영 확인은 최대 4초, 장치 준비 대기는 최대 20초다. 확장 unit은 300초, mount job은 330초, 등록 unit은 300초, 게스트 SSH 전 키 준비 unit은 240초로 제한한다. 실패는 journald에 `[perseus-first-boot] ERROR`와 이유를 남긴다. filesystem check의 복구 가능한 exit 1만 성공으로 허용하며 포맷·VM 강제 제거는 하지 않는다.

## Pi 단독 신규 생성

로컬 template bundle에서 PC와 동일한 `prepare_instance.py`를 사용한다. native ARM64 chroot·고유 ID/SSH 키 준비·독립 raw 복사만 하며 다운로드·템플릿 생성·패키지 설치는 하지 않는다. 필요한 util-linux/e2fsprogs/coreutils는 고정 Layer의 RDEPENDS에 포함했다. 새 외부 Python 라이브러리는 없다.

```bash
perseus-vm templates
# 기본은 생성 계획. 사용하지 않는 예약 IP를 명시한다.
perseus-vm create ubuntu-team2 --template ubuntu-24.04-20260926 --ipv4 192.168.77.30
# 별도로 승인된 운영 시 실제 새 디스크/식별자 생성:
perseus-vm create ubuntu-team2 --template ubuntu-24.04-20260926 --ipv4 192.168.77.30 --execute
perseus-vm start ubuntu-team2
```

새 VM은 기본적으로 자동 시작 off다. `create --autostart` 또는 기존 `autostart-set <id> on`으로 다음 플랫폼 부팅 대상으로 등록한다. 생성 자체가 xl create나 OS/app 준비 완료를 의미하지 않는다. 같은 ID/경로/IP 중복은 거부한다. `cp --sparse=always --reflink=never`로 별도 파일을 만들며 hardlink하지 않는다. 실제 free bytes에서 기존 VM의 아직 할당되지 않은 논리 용량과 2GiB 여유를 뺀 뒤 신규 VM 전체 용량을 확보해야 한다.

초기 콘솔·SSH는 기존 `root / 1234`, 빈 암호 거부다. 사용자 변경 암호·앱·Agent·작업 파일은 일반 부팅/시작/정지에서 만지지 않는다. 신뢰하는 팀 개발망에서만 사용하고 공인 인터넷에 SSH를 노출하지 않는다.

## 재실행 검사와 별도 승인 단계

```bash
bash scripts/check-perseus-platform.sh
bash scripts/check-perseus-platform.sh --metadata
```

첫 명령은 일반 파일/명령 대역·8KiB 비 OS 조립 fixture와 실제 systemd loader의 순환 검사를 실행한다. 두 번째는 별도 `/tmp` 빌드 경로의 파싱/환경 조회만 추가한다. 실제 BitBake 빌드 태스크·다운로드·mount·SD·VM은 실행하지 않는다.

2026-10-16 당시 dom0/WIC 검사는 완료했지만, 2026-10-17 소스와 QEMU 옵션을 포함한 새 빌드·실제 패키징/조립 검사는 미수행이다. 별도 승인 후 순서는 완료 플랫폼 게시 → 공식 원본 검증/템플릿 준비 → 계획/실제 조립/verify → 64GB·128GB SD 기록 → 실보드 수락이다. 반드시 확인할 항목은 수동 명령 없는 Xen/dom0 부팅, 실제 용량 확장/재부팅 no-op, 각 VM 콘솔·SSH, 두 카드의 ID/키 차이, Pi 단독 신규 VM 생성, 중복/부족 용량 거부, 암호·파일·패키지 보존, 고유화/확장 도중 중단 후 복구, 정상 종료→저장소 해제다. 하드웨어/OS 호환·SD 속도·전원 차단 복구는 모의 시험으로 보증하지 않는다.

## 2026-10-18 수락 보완의 후속 반영

기본 RTOS 참조는 `perseus-zephyr-starter-1.0.0`이다. 기존 synchronization 진단 템플릿도 별도 불변 원본으로 계속 검증할 수 있으며, 이를 명시적으로 선택한 조립 설정은 기존 자동 시작 off·보드 종료 refuse 정책을 유지한다. 새 Starter seed만 자동 시작 on과 보드 종료 force-allowed를 갖는다. 기존 저장소의 정책을 defaults.json으로 덮어쓰지 않는다.

기존 Ubuntu/Debian 템플릿은 재다운로드/재생성 없이 재사용할 수 있다. 새 seed/인스턴스 사본을 만드는 공유 준비 경로가 SSH 서비스 정책을 반영한다. 기존 VM 내부는 dom0 재빌드만으로 바뀌지 않으며 [명시적 게스트 설정 갱신 절차](perseus-acceptance-20261018.md)를 따른다.
