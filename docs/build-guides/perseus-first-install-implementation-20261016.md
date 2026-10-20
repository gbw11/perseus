# 공통 SD 최초 설치 구현 보고 — 2026-10-16

이 문서는 최초 소스 단계의 역사적 기록이다. 이후 dom0 빌드와 완료 산출물 인계 보완 상태는 [후속 보고](perseus-completed-artifacts-20261016.md)를 따른다.

## 기준과 작업 경계

- 브랜치: `feat/platform/yocto-build-setup`
- 실제 기준/현재 HEAD: `70c85b90ef21e73b4d7b9daa97a65c0cacf3078c`. WIC Xen 수정이 포함된 커밋 그대로다.
- 시작 작업 트리는 깨끗했고, 시작 시 관찰된 활성 BitBake 빌드 프로세스는 없었다. 이번 변경 이전의 미커밋 소스는 없었다.
- 기존 체크아웃에서 소스만 수정했다. 외부 Layer 버전·기존 빌드 설정·성공 산출물을 유지했다. HEAD와 Git 인덱스는 변경하지 않았다.
- 이번에 실제 OS 바이너리 다운로드, 템플릿/VM 디스크 생성, 이미지 빌드/실제 조립, SD 접근·기록·포맷, git add/commit/push는 하지 않았다.
- 8KiB 일반 파일을 사용하는 비 OS 모의 조립만 실행했다. 포맷·mount·게스트 준비는 명령 대역이며 실제 OS 이미지 생성이 아니다.

## 최종 설계와 근거

1. **64GB/128GB, Ubuntu·Debian 전체 템플릿 보관.** 사용자 최종 요구에 따라 32GB 지원은 제외했다. 각 8GiB 템플릿 두 개와 기본 VM 두 개의 논리 용량 32GiB를 모두 예약한다. Alpine은 확장 디렉터리/미지원 경계만 남겼다. 활성 원본 버전, adapter, 템플릿 생성 및 기본 VM은 추가하지 않았다.
2. **불변 sparse raw 원본과 독립 raw 인스턴스.** 기존 준비 코드를 PC/Pi가 공유한다. 복사 시 `--reflink=never`를 사용하고 템플릿을 보존한다. 압축 보관은 구현하지 않았다. 구조와 고장 진단을 단순하게 유지하며, sparse의 실제 점유량을 최대 필요 용량으로 착각하지 않는다.
3. **계획/실행/검증 분리.** 기본은 읽기 전용 계획이다. 명시적 `assemble --execute`만 실제 생성한다. 입력 일반 파일·해시·완료 빌드 기록·빌드 잠금·레이아웃을 확인한다. 실제 실행에서는 게스트 준비 전에 readonly 플랫폼 rootfs/FAT 기능 gate를 통과해야 한다. 기존 성공 이미지에 파일을 끼워 넣지 않는다.
4. **기존 파티션 1·2와 부팅 참조 보존.** DOS/MBR에 마지막 ext4 3번만 추가한다. 최초 부팅은 실제 root major:minor/sysfs와 PARTUUID로 부팅 매체를 확인한다. 같은 PARTUUID가 다른 매체에도 있으면 실패한다. 마지막 데이터 파티션과 ext4만 실제 장치 끝까지 확장한다. 파티션 시작 위치·앞 파티션·VM root.raw·게스트 filesystem은 변경하지 않는다.
5. **초기화 재실행은 진행 상태와 실제 자산을 함께 검사한다.** 식별자 계획을 먼저 영구 저장하고 seed 설정→디렉터리 이동→등록→전체 검증 순으로 완료한다. 중단 후 같은 계획을 재사용한다. 기존 디스크가 없거나 손상됐으면 새 디스크로 대체하지 않고 VM 시작을 막는다. 일반 부팅에서는 디스크 복제·비밀번호 초기화·식별자 재발급을 하지 않는다.
6. **고유화 시점 분리.** dom0/guest machine-id는 공통 이미지에서 `uninitialized`로 두고 PID1이 초기 부팅에 확정한다. 실행 중 dom0 ID는 바꾸지 않는다. SSH 키는 listener보다 먼저 준비하며, 완료 후에는 존재/유효성만 검사한다. VM UUID/MAC과 저장소 UUID는 최초 등록 계획에서 한 번 발급한다. 기본 이름·내부 IP는 유지한다.
7. **자동 시작 소유자 유지.** 데이터 준비→mount→최초 등록→기존 `perseus-vms.service`로 연결한다. 기존 Xen 검사·네트워크/DHCP·정상 종료 보호를 유지한다. 별도 xl 자동 실행자나 자동 복구 정책은 추가하지 않았다. Pi의 `perseus-vm create`는 별도 명시적 최초 생성이며 실행 시작과 분리된다.
8. **패키징과 필요한 도구는 Yocto 소스에 반영.** 고정 Layer의 util-linux/e2fsprogs/coreutils 도구와 공유 Python 모듈을 포함했다. 설치 unit 6개를 모두 패키지 소유로 두고, static unit과 직접 enable하는 unit 4개를 구분했다. `installed-vs-shipped` QA는 유지했다.

용량 모의 예시: 기존 플랫폼 3,347,054,592 bytes, 8GiB 디스크 네 개, 부팅 bundle 상한 2GiB, 임시 메타데이터 정책 5%+256MiB, 운영 여유 2GiB일 때 최소 비압축 이미지/카드는 **44,098,912,256 bytes**다. 원본 변환 scratch를 제외한 WSL 작업 공간 상한 예시는 **91,544,879,104 bytes**다. 모의 32,000,000,000 bytes는 실패하고 63,864,569,856/127,865,454,592 bytes는 통과했다. 현재 SD 측정값이나 아직 만들지 않은 최종 이미지의 실측값이 아니다. 선택한 실제 자산으로 계획과 조립 후 free-space 검증을 다시 해야 한다.

## 변경 파일

- `platform/first-install/{assemble.py,first_install_contract.py,example.config.json}`: 조립기, 레이아웃·용량 공통 계약, 명시적 입력 예시.
- `scripts/assemble-perseus-first-install.py`: 계획/조립/결과 검증 진입점.
- `platform/guests/common/prepare_instance.py`, `scripts/prepare-perseus-guests.py`: 기존 최초 생성 코드 공유, PC/Pi 경로와 factory seed 연결.
- `platform/guests/common/{prepare_rootfs.py,guest_identity.py}`: rootfs 경로 방어 유지, 배포 seed와 최초 SSH 고유화.
- `platform/guests/alpine/README.md`: 확장용 구조와 현재 미지원 범위.
- `platform/yocto/meta-perseus/recipes-core/perseus-platform/files/{perseus_first_boot.py,perseus-first-boot,perseus-data-prepare.service,perseus-first-boot.service,first-install-capabilities.json,perseus-sshd-keys.conf}`: 제한된 데이터 확장, 단계별 등록, SSH 선행 조건과 기능 식별.
- 같은 `files/`의 `{perseus_vm.py,srv-perseus.mount,perseus-vms.service,perseus-vm-shutdown.service}`: 준비 조건 통합, Pi 단독 신규 생성과 저장소 안전성.
- `platform/yocto/meta-perseus/recipes-core/perseus-platform/perseus-platform_1.0.bb`: 패키징, unit 소유권/enable, 도구 의존성.
- `platform/yocto/meta-perseus/recipes-core/images/perseus-xen-bringup-image.bb`: 공통 이미지 machine-id/키 정책. 기존 WIC Xen 규칙 유지.
- `tests/platform/{first_install_fixture.py,test_perseus_first_install.py,check_perseus_first_install_units.py,check_perseus_metadata.py}`, `scripts/check-perseus-platform.sh`: 모의·회귀·실제 systemd loader·메타데이터 검사.
- `tests/platform/results/first-install-20261016.json`: 이번 실행 결과, 실제 로그 위치/해시와 미수행 범위.
- `docs/build-guides/{perseus-first-install.md,perseus-persistent-platform.md,perseus-first-install-implementation-20261016.md}`: 사용 계약, 후속 절차와 본 보고서.

## 실행한 검사와 결과

원본 로그: `/home/ssafy/perseus-first-install-audit-20261016-131311/`.

| 검사 | 실제 결과 | 로그 |
|---|---|---|
| `bash scripts/check-perseus-platform.sh` | exit 0, unittest 97개(기존 52+신규 45), 기존 boot/init/network 회귀 17개 통과, shell syntax·diff 검사 통과 | `final-checks.log`, `final-checks.exit-code`, `final-checks.command` |
| 실제 `systemd-analyze verify` private fixture | 정상 그래프 exit 0, 의도적 순환 그래프 exit 1을 정확히 탐지. 실제 서비스 미시작 | `final-checks.log` |
| 격리된 BitBake `-p`, `-e`, `bitbake-getvar` | 네트워크 금지, 별도 /tmp/cache. 파싱 및 메타데이터 확보 성공. 빌드 태스크 미실행 | `metadata-logs/parse.log`, `metadata-logs/*.env` |
| 저장된 실제 메타데이터에 대한 최종 검사 | exit 0, 6개 unit 소유권/4개 enable/static 분리/QA/도구/공유 소스/machine-id/WIC Xen 의존성 확인 | `metadata-checks.log`, `.exit-code` |
| 기존 ARM64 target Python을 기존 qemu-user로 실행해 새 runtime 모듈 import | exit 0. 최초 부팅·인스턴스 생성 동작은 실행하지 않음 | `target-imports.log`, `.exit-code` |
| 기존 성공 빌드 rootfs에 새 기능 gate 적용 | 기대한 거부 확인, 검사 exit 0. dom0 재빌드 필요 사유 출력 | `old-platform-rejection.log`, `.exit-code` |
| 기존 성공 산출물 SHA-256 | 15개 모두 기존 기록과 일치 | `success-artifacts-preserved.log` |
| 기존 두 빌드 디렉터리 conf 보존 | 4개 파일 모두 이전 해시와 일치 | `build-configs-preserved.log` |

첫 `bash scripts/check-perseus-platform.sh --metadata` 드라이버는 exit 1이었다. 파싱 자체는 성공했지만 검사기가 공유 소스 FILESPATH의 `../..`를 정규화하지 않고 비교했다. 검사기를 `Path.resolve()` 비교로 보완한 뒤 같은 파싱 결과를 최종 검사해 exit 0을 확인했다. 실패 시도도 `platform-checks-final-attempt1.log/.exit-code`에 보존했다. 당시 전체 드라이버를 성공했다고 표시하지 않는다. 그 후 최종 일반 검사 97+17개는 별도로 통과했다.

모의 검사는 바이트 기준 용량/부족·해시·잘못된 입력·기존 출력·진행 중 빌드 거부, 생성 전 기능 gate, 1·2번 보존, 확장 중단·재시도·실패, 두 카드 ID 차이, 등록 중단 후 재개, 비밀번호/디스크/고유 ID 보존, 미완료/누락 시 VM 차단, 신규 생성 경로·용량/중복 방어를 다룬다. 실제 OS 부팅·드라이버·SD 전원 차단을 모의 통과로 대체하지 않는다.

재실행 진입점은 `bash scripts/check-perseus-platform.sh`와 `bash scripts/check-perseus-platform.sh --metadata`다. 저장된 파싱 결과만 재검사하려면 `python3 -B tests/platform/check_perseus_metadata.py /home/ssafy/perseus-first-install-audit-20261016-131311/metadata-logs`를 사용한다.

## 남은 확인과 별도 승인 단계

**새 최초 부팅 기능을 포함한 후속 dom0 재빌드가 필수다.** 기존 성공 이미지에는 이 기능이 없어 조립 gate가 거부한다. 이번에는 새 WIC를 만들거나 그 내부 기능 포함을 주장하지 않는다. 다음은 별도 승인 후 수행할 항목이다.

1. 동일 Layer 버전/캐시와 성공 산출물을 보존하며 dom0 빌드. 설치 패키지/6개 unit/SSH 선행 조건/machine-id 상태 및 실제 WIC FAT Xen·부팅 파일을 검사한다.
2. 고정 Ubuntu·Debian 원본을 다운로드·검증하고 전체 불변 템플릿과 독립 seed를 준비한다. ARM64 kernel/initramfs/modules/Xen driver/정상 종료/SSH 설정의 실제 호환성을 확인한다.
3. 실제 대상 카드 바이트와 WSL 여유를 입력해 계획→명시적 조립→manifest/파티션 해시/최소 용량/실제 free-space를 검증한다. 실제 조립 경로의 loop/mount/원자적 게시도 아직 미실행이다.
4. 64GB·128GB 각각 SD 기록 후 수동 U-Boot 명령 없이 Xen/dom0, 마지막 ext4 확장, 브릿지/DHCP, 두 VM 자동 시작을 확인한다. 재부팅 시 확장 no-op과 앞 파티션·VM root.raw 크기 불변을 확인한다.
5. 두 카드의 dom0/guest machine-id·SSH host key·VM UUID/MAC/저장소 UUID 차이와 동일 기본 이름/IP를 확인한다. 초기 root/1234 및 비밀번호 변경 후 보존을 확인한다.
6. 파일·패키지·서비스·앱 설정을 만든 뒤 VM 재시작과 보드 정상 종료/재부팅 보존, 정상 종료→쓰기 완료→저장소 해제를 확인한다. 일반 시작에서 템플릿 복사/포맷/암호 초기화가 없는지 확인한다.
7. PC 없이 Pi의 로컬 템플릿으로 신규 VM을 명시적으로 생성한다. 원본 불변, 독립 root.raw/고유 ID, 용량 부족/중복 거부, 자동 시작 소유자 하나를 확인한다.
8. 확장/고유화/등록 단계별 중단과 다음 부팅의 재개, 손상/저장소 누락/실패 진단 및 VM 시작 차단을 실보드에서 확인한다. 강제 전원 차단 내구성은 아직 보장하지 않는다.

도구별 제한시간과 초기 운영 여유/메타데이터 계수는 현재 방어적 정책이며 실제 SD 속도와 자산 크기에 맞는지 검증해야 한다. 기존 WIC 내부 파일 검증과 실제 하드웨어 자동 부팅은 별개이며 후자는 아직 미확인이다. SD 전체 재기록 후 작업 보존은 별도 백업/복원 없이 보장하지 않는다.
