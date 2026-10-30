# Tailscale 이미지·동일 보드 상태 복원 검증 — 2026-10-26

소스 기준은 `6930ea039e8a357f53283ba2b88c8488f9c21e03`와 이 문서에 기록한 미커밋 변경이다.
현재 branch는 `feat/platform/runtime-lifecycle-diagnostics`다. 확인한 원격 platform의
`66e1b3a` tree와 기준 HEAD는 같았다. 보드 설치·인증·재부팅·SD 기록·commit/push는 수행하지 않았다.

## 구현과 선택 근거

고정 meta-openembedded `47d7dbdd10890bbd429bc0cccbe14435c37e5af1`의 meta-networking에는
Tailscale 1.94.2 recipe가 있고 실제 부분 빌드도 통과했다. 다만
[TS-2026-013](https://tailscale.com/security-bulletins#ts-2026-013)의 인증서 차단 수정은
[1.104.2 / Go 1.27.3](https://tailscale.com/changelog#2026-10-21)에 포함된다.
기존 Go 1.26.8·외부 layer lock을 바꾸는 대신 공식 ARM64 정적 릴리스를 고정한
`tailscale_1.104.2.bb`를 사용한다. 기존 1.94.2 결과는 배포 후보가 아니다.

| 항목 | 구현 |
|---|---|
| 패키지 | `tailscale` CLI, `tailscaled` daemon·unit·저장소 helper. 공식 archive·LICENSE URL/SHA256/MD5, ARM64 고정 |
| image | CLI·daemon과 kernel TUN/IPv6 module 포함. 외부 layer·compiler·kernel 설정은 유지 |
| Python 의존성 | core/json/crypt/io/netclient/unixadmin. 고정 Python manifest에서 argparse·base64·hashlib·tempfile 등의 실제 제공자 대조 |
| 기동 | upstream unit + persistence drop-in, multi-user enable. network-online·Tailnet 인증 대기·VM 역의존성 없음 |
| 저장소 | `/srv/xenova/tailscale`, directory 0700/state 0600. 기존 검증 data-device와 실제 writable ext4 mount 대조 |
| 실패 경계 | mount 부재·다른 장치·중단 복원은 거절. rootfs state fallback·자동 로그인·자동 routing/SSH 기능 활성화 없음 |
| 백업·복원 | 정지한 daemon, 같은 RPi hardware serial, 제한 크기·경로·checksum 검사. 기존 상태 덮어쓰기 거절, fsync 후 게시 |
| 재설치 | 기존 SD 전체 초기화·factory seed/grow/UUID 정책 유지. Tailscale 백업은 공통 image·Git·공개 전달물에서 분리 |

`assemble.platform_root_ok()`의 변경은 payload·module·enablement 검사와 공통 state 배제뿐이다.
파티션 배치·조립·데이터 초기화 정책을 변경하지 않는다. 실제 kernel `.config`는
`CONFIG_TUN=m`, `CONFIG_IPV6=m`, IPv4/IPv6 multiple tables, netfilter를 제공했다.

## 실행한 검증

아래 집중 시험은 전체 회귀와 일부 겹친다. 개수를 합산한 별도 총계로 해석하지 않는다.

| 검증 | 결과와 증거 |
|---|---|
| 전체 platform unittest | 890개, 실패 0, UID 조건 1개 skip. 별도 root 실행의 Wi-Fi consumer 10개 PASS로 해당 사례 확인 |
| 최종 Tailscale 집중 시험 | 19개 PASS. 동일 보드 fresh target 복원, 다른 serial·경로 이탈·중복·변조·링크·크기 제한·fsync 실패·중단 staging·미정지 거절·보안 version 선택 |
| 최초 설치 회귀 | 48개 PASS. 실제 조립 구조 변경 없이 인증 state 포함·필수 module 부재·이미지 내부 절대 부모 symlink 뒤의 state 거절 포함 |
| bringup·diff 검사 | `python3 -B platform/tests/check_xenova_bringup_regression.py`, `git diff --check` PASS |
| BitBake 선택 | 3,216 recipes parse, 0 errors. meta-xenova 1.104.2 선택 및 공통 append 확인 |
| 최종 부분 패키징 | `bitbake tailscale`: 1,944 tasks 성공. package QA·LICENSE 포함·실제 pkgdata RDEPENDS 확인 |
| ARM64 실행 | 공식·실제 package의 daemon/CLI 1.104.2, Go 1.27.3 실행. 내장 build 설정 `GOARCH=arm64`, `GOARM64=v8.0`, `CGO_ENABLED=0` 대조 |
| native systemd·TUN | 별도 unit·PrivateNetwork, WSL kernel의 TUN과 ARM64 executable/qemu. `NeedsLogin`, `tailscale0`, daemon restart 확인 |
| 암호화·재설치 모사 | GPG AES256 round-trip·변조 거절. 소유 loop ext4를 다른 UUID의 새 ext4로 교체하고 같은 synthetic serial의 상태 bytes 복원 |
| 복원 중단·장애 격리 | 실제 자식 SIGKILL 후 staging이 새 identity 생성을 차단. 다른 serial·잘못된 data-device 거절, 별도 서비스 PID 유지, 소유 unit/loop 회수 오류 0 |
| 등록·상태 수명주기 독립 | 기존 legacy 최초 등록 fixture의 실제 register 코드. Tailscale 디렉터리가 있어도 등록 성공, 재실행·별도 state 변경 뒤 VM 신원·디스크·등록 기록과 opaque state 보존. block/mount는 대역, 실보드 아님 |
| 전체 dom0 WIC | **PASS**. 실제 FAT·rootfs 소스/enablement·ARM64 실행·helper import·systemd 259 graph·TUN/IPv6·공통 state 부재 등 33개 확인 |

최초 전체 회귀 실행의 실패는 systemd 259 검사기 환경 누락으로 WSL의 255를 사용한 결과였다.
고정 ARM64 259.5 검사기로 재실행해 통과했으며 실패 출력도 보존했다. 공식 binary recipe의
첫 패키징에서 LICENSE의 FILES 소유 누락을 QA가 검출해 보완한 뒤 재시험했다.
전체 회귀 뒤 추가한 보안 recipe 선택 사례는 19개 집중 시험에 포함해 확인했다.
후속 검사에서 공통 state 배제의 절대 부모 symlink 누락을 합성 fixture로 재현해 기존
rootfs 경로 해석기를 사용하도록 보완했다. host 조립 검사와 회귀만 변경했으며 image recipe의
payload 입력은 바뀌지 않았다. 빌드 당시 입력은 `IMAGE_BUILD_SOURCE_MANIFEST.json`,
최종 검사 입력과 차이는 `FINAL_SOURCE_MANIFEST.json`·`HOST_VALIDATION_ADJUSTMENT.json`에 구분한다.
초기 1.94.2·중간 image 빌드는 최종 후보 전환을 위해 명시적으로 중단했고 로그와
증분 object·기존 cache·이전 산출물을 보존했다. 최종 image 실행만 별도 resource config로
LLVM `-j8`을 사용하며 compiler·최적화·제품 설정은 변경하지 않는다.

## 증거와 재실행

PC 원자료는 `Documents/Codex/2026-10-25/xenova-tailscale/`에 있다.
`NATIVE_SECURE_PROBE.json`, `FINAL_SOURCE_MANIFEST.json`, `full-regression-r2.*`,
`final-secure-package.*`, `final-dom0-image.*`, `FINAL_WIC_VALIDATION.json`에 명령·종료 코드·출력·입력 해시를 보존한다.
인증 state·복호화 평문·암호는 보고서에 수집하지 않았다. 미인증 native probe의 private
fixture만 저장소 밖 root 전용 경로에 두며 실제 unit·mount·loop는 해제했다.

```bash
python3 -B -m unittest discover -s platform/tests -p test_xenova_tailscale.py -v
python3 -B -m unittest discover -s platform/tests -p test_xenova_first_install.py -v
# systemd 259 검사기 환경은 build-guide의 준비 절차를 적용한다.
python3 -B -m unittest discover -s platform/tests -p 'test_*.py' -v
bitbake tailscale
bitbake xenova-xen-bringup-image
```

## 최종 산출물

- dom0 WIC: `xenova-xen-bringup-image-raspberrypi4-64.rootfs-20261025152005.wic.bz2`
- 압축 WIC SHA256: `062a7b26310e4c76ba4624bab32a51774ced00ee15e448cc019dd9c5b488da82`
- 최종 package·source·입력/산출물 해시: PC `final-artifacts/`와 `FINAL_RESULT.json`.
- 검증한 WIC 후보이며 factory 최종 이미지 조립·SD 기록은 수행하지 않았다.

## 수락 경계와 남은 실제 시험

native 시험은 **미인증 상태와 synthetic board serial**을 사용했다. 물리 보드, 사용자
Tailnet, 인증 URL 승인, 다른 Tailnet 장치의 OpenSSH, 실제 host reboot, 실제 SD 재설치 뒤
같은 장치 ID/IP는 미검증이다. active·NeedsLogin·동일 파일 복원을 인증 성공으로 판정하지 않는다.
TPM state 보호를 별도로 사용하는 설치의 TPM 초기화·교체도 복원 수락에 포함하지 않았다.

추가 실보드 시험은 LAN/UART 관리 경로를 유지한 상태에서 후보 설치 → 최초 사용자 인증 →
다른 Tailnet 장치 SSH → daemon restart → 암호화 백업·무변경 검사 → 같은 보드의 state
보관/복원·장치 대조 순서다. host reboot와 SD 전체 기록은 별도 승인·사용자 작업이다.
기존 기본 VM·CPU 정책·Wi-Fi·sshd·UART와 VM 신원·자산은 변경 대상이 아니다.

현재 사용법은 [최초 인증](../platform/cli-manager-spec.md#cmd-tailscale),
[암호화 백업·동일 보드 복원·원복](../platform/cli-manager-spec.md#cmd-tailscale-backup),
[저장소·부팅 경계](../platform/management-boot-boundaries.md#tailscale의-저장소부팅-경계)를 따른다.
