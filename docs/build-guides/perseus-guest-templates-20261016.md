# Ubuntu·Debian 원본 및 템플릿 생성 결과 — 2026-10-16

이번 단계는 원본 다운로드·체크섬 검증·새 템플릿 생성과 실측 용량 계획까지 완료했다. VM 인스턴스/seed 생성, 최종 이미지 조립, SD 마운트·기록·포맷·파티션 변경, BitBake 빌드, commit/push는 실행하지 않았다. 템플릿 검증 통과는 Xen 실부팅 성공을 의미하지 않는다.

## 기준과 저장 위치

- 저장소 `/home/ssafy/S15P31E110`, 브랜치 `feat/platform/yocto-build-setup`.
- 시작 HEAD `9f685e9fd1d6cce65aea5ae8a655fb2b7a357492`. 시작 시 `git status --short`는 비어 있었다. 기존 Layer 및 원본 버전 고정값은 변경하지 않았다.
- 원본·템플릿·실행 명령·로그: `/home/ssafy/perseus-guest-templates-20261016-150530/`.
- 템플릿은 위 경로의 `templates/ubuntu-24.04-20260926/`, `templates/debian-13-20261015/`. 각각 `root.raw`, `Image`, `initrd`, `kernel.config`, `template.json`, `boot.json` 여섯 개 일반 파일이며 모두 mode 0444다.

## 원본과 검증

| 원본 | 다운로드 크기 | 고정 커널 |
| --- | ---: | --- |
| Ubuntu 24.04 ARM64, release-20260926 | 620,224,512 bytes | 6.8.0-142-generic (6.8.0-142.142) |
| Debian 13 ARM64, 20261015-2632 | 432,209,920 bytes | 6.12.111+deb13-arm64 (6.12.111-1) |

공식 체크섬 문서, 저장소 `platform/guests/sources.lock.json`, 실제 다운로드 파일의 digest가 모두 일치했다. Ubuntu는 SHA-256, Debian은 SHA-512이며 전체 값·URL·리다이렉트 출처는 `downloads-verified.json`과 보존한 공식 체크섬 문서에 있다. Debian의 공식 다운로드 서버가 지정한 HTTPS 미러로 이동했으며 고정 digest를 그대로 검증했다. 최초 리다이렉트 거부 로그와 재시도 성공 로그를 모두 보존했다.

## 필요한 준비 도구 수정

Ubuntu 원본은 root와 `/boot`가 분리되어 있다. 기존 도구가 root만 복사해 `/boot/config-6.8.0-142-generic`이 없는 상태로 실패했다. 원본 fstab과 GPT에서 별도 ext4 boot 파티션을 확인했다(`ubuntu-original-boot-layout.json`, `ubuntu-source-partitions.json`).

`scripts/prepare-perseus-guests.py`가 원본 fstab의 `/boot`를 같은 전용 loop의 파티션 목록 안에서만 LABEL/UUID/PARTUUID로 찾고 읽기 전용으로 마운트하도록 수정했다. 물리 장치 전체 검색이나 EFI 파티션 복사는 하지 않는다. 누락·중복·외부 장치·지원하지 않는 파일시스템·root를 벗어나는 링크는 거부한다. `tests/platform/test_perseus_guest_boot_partitions.py`에 6개 회귀 검사를 추가했다. 이 수정은 호스트 준비 도구만 해당하며 현재 dom0 바이너리는 바꾸지 않았다. 변경은 미커밋 상태다.

QEMU host 도구는 공식 APT 인덱스 checksum과 대조한 deb를 이번 전용 작업 경로에 풀어 사용했다. 패키지 설치·런타임 의존성 추가는 하지 않았고 `/var/lib/dpkg/status` 해시가 유지됐다. 임시 ARM64 binfmt 등록과 직접 만든 loop/mount는 종료 시 해제했다. 실패한 scratch와 로그는 삭제하지 않았다.

## 실제 템플릿과 용량

| 항목 | Ubuntu | Debian |
| --- | ---: | ---: |
| root.raw 논리 크기 | 8,589,934,592 bytes (8GiB) | 8,589,934,592 bytes (8GiB) |
| root.raw 실제 할당량 (`st_blocks × 512`) | 2,054,352,896 bytes | 1,006,931,968 bytes |
| 전체 템플릿 번들 실제 점유량 (`du -B1`) | 2,143,076,352 bytes (약 2.00GiB) | 1,082,150,912 bytes (약 1.01GiB) |

합계 실제 번들 점유량은 **3,225,227,264 bytes (약 3.00GiB)**다. 이는 현재 WSL 파일시스템 측정값으로 SD ext4에 복사한 뒤의 실측값은 아니다. raw 논리 크기를 축소하거나 템플릿 원본을 생략하지 않았다.

실제 템플릿을 명시한 `assembly-plan-verified-templates.config.json`으로 plan만 실행했다.

| 계획 항목 | bytes |
| --- | ---: |
| 실제 연결 64GB 카드 (`Get-Disk`, 메타데이터 조회만) | 63,864,569,856 |
| 선택 dom0 이미지의 비압축 크기 | 4,428,345,344 |
| 템플릿 root 논리 용량 합계 | 17,179,869,184 |
| 기본 VM 두 개 최대 root 논리 용량 합계 | 17,179,869,184 |
| boot/catalog 자산 | 328,065,138 |
| 파일시스템 메타데이터 여유 | 2,004,877,312 |
| 운영 여유 | 2,147,483,648 |
| 최종 이미지 예상 크기 / 최소 카드 용량 | **43,272,634,368** |
| 현재 카드에서 남는 용량 | **20,591,935,488** |
| 후속 조립 WSL 임시 공간 상한 | 90,973,614,080 |

plan 종료 코드는 0이다. 네 개 raw 디스크의 최대 논리 용량 32GiB를 모두 예약한 계산이며 sparse 점유량만으로 과할당하지 않았다. VM 디스크는 아직 만들지 않았으므로 초기 VM 할당량 3,061,284,864 bytes는 실제 템플릿 raw 할당량을 바탕으로 한 추정이다. 남은 VM 성장 예산은 14,118,584,320 bytes다. 최종 ext4 실제 free-space gate와 64GB/128GB 최초 부팅 확장은 미수행이다. plan 출력/작업 디렉터리는 생성되지 않았다. 조회 당시 WSL backing C: 여유 공간은 474,752,950,272 bytes로 계획 scratch 상한보다 컸다.

## 수행한 검증과 로그

- `bash scripts/check-perseus-platform.sh`: **129 tests OK**, 기존 boot/init/network 회귀 **17개 PASS**, 실제 systemd loader에서 정상 의존성 exit 0 및 주입 cycle exit 1. `boot-partition-regression.log`, exit-code 0.
- 두 템플릿 생성 명령은 각 `<id>.template.command`, stdout/stderr는 `<id>.template.log`, 종료 코드는 둘 다 0. 첫 실패도 `.attempt1`으로 보존.
- `verify-templates.py`: 두 템플릿 PASS, exit 0. root 및 boot 파일의 전체 해시·출처·ARM64 Image·커널 설정·모듈·읽기 전용 속성을 검사했다. `e2fsck -fn`은 각각 exit 0. 각 파일의 hash/논리 크기/할당량은 `templates-verified.json`, 검증된 digest 목록은 `TEMPLATE-SHA256SUMS`.
- machine-id 비어 있음, 공유 SSH host key 없음, instance-id 없음, 초기 root/1234 hash 및 SSH include 정책 확인. 실제 게스트 sshd를 RO 템플릿과 임시 `/run`·`/dev` tmpfs에서 `-G -T -C user=root,...`로 실행해 `permitrootlogin yes`, `passwordauthentication yes`, `permitemptypasswords no`, `authenticationmethods any` 확인. `effective-ssh-verification-final.json` 및 각 최종 로그. 비밀번호를 입력하는 실로그인은 수행하지 않았다.
- initramfs 생성 및 필요한 Xen/ext4 드라이버 검사 성공. 원본 고정 커널·모듈을 사용하며 dom0 커널 재사용 없음.
- `assembly-plan.command` / `.json` / `.log` / `.exit-code`: 실제 입력 hash 검사 및 64GB 계획 성공, exit 0. 입력 metadata도 유지.
- 기존 원본 빌드 산출물 checksum 25개 OK (`original-platform-preserved.log`), 게시된 WIC/완료 기록/manifest 모두 OK (`published-platform-preserved.log`). 게시 WIC SHA-256은 `3dae535defe6e6da114dc48889b0ecf8dccf11b27a818f131f61729ecd6d0760` 그대로다.

## 남은 문제와 후속 조건

**현재 seed 생성 검증 경로는 실제 조립 전에 보완해야 한다.** `platform/guests/common/prepare_instance.py::verify_guest_ssh(seed=True)`의 현재 `sshd -G -C ...`는 두 배포판에서 exit 255와 `Config test connection parameter (-C) provided without test mode (-T)`로 실패했다. `-G -T -C` 조합은 실제 두 sshd에서 통과했다. Debian 검사에는 임시 chroot `/dev/null`도 필요했다. 기존 생성 경로는 이 검증용 가상 파일시스템 준비까지 점검해야 한다. 해당 공유 모듈은 dom0에도 설치되고 WIC capability 검사에서 현 소스와 byte 비교하므로, 후속 수정·회귀 검사 후 dom0 재빌드 및 새 완료 산출물 게시가 필요하다. 이번에는 기존 게시 WIC와 shared runtime 모듈을 수정하거나 seed 생성을 실행하지 않았다. 템플릿 자체가 이 오류 때문에 실패한 것은 아니다.

initramfs 로그에는 오프라인 `/dev/xvda` root filesystem의 fsck hook 종류를 확인하지 못했다는 경고가 있고, Ubuntu에는 MD subsystem 및 mdadm 임시 설정 경고도 있다. 생성 명령과 read-only fsck는 성공했지만 실제 게스트 부팅·fsck 경로는 미검증이다.

다음 승인 단계: 위 seed 검사 결함 보완/테스트와 필요한 dom0 재빌드 → 새 플랫폼 완료 게시 → 기존 템플릿 재사용 계획 → 실제 seed 및 공통 이미지 조립 → 최종 FAT/rootfs/데이터 및 여유 공간 검사 → 별도 승인 후 SD 기록·64GB/128GB 최초 부팅과 작업/비밀번호 보존 시험. 커널 실부팅, Xen VM, DHCP/SSH 로그인, 재부팅 보존은 이번 실행 결과에 포함하지 않는다.
