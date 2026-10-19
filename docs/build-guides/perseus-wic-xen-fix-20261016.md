# WIC Xen 누락 수정 및 재빌드 — 2026-10-16

## 원인과 변경

직전 성공 빌드의 `.rpi-sdimg`에는 Xen이 있었지만 `.wic.bz2`의 FAT에는 없었다. `boot.scr`은 `xen`을 읽으므로 이 WIC로는 같은 Xen 부팅 흐름을 재현할 수 없었다. 새 `scripts/check-perseus-wic.py`로 이전 WIC를 읽어 `mcopy: File "::xen" not found` 실패(종료 코드 1)를 재현했다.

고정된 `meta-virtualization`의 `xen-raspberrypi4-64.inc`는 `DEPLOYPAYLOAD = "xen-${MACHINE}:xen"`을 사용하지만 이는 `rpi-sdimg`용이다. `meta-raspberrypi`의 WIC용 `IMAGE_BOOT_FILES`에는 Xen 매핑이 없었다. `xen-image-minimal.bb`의 기존 `xen:do_deploy` 의존성만으로는 FAT 복사 목록이 채워지지 않는다.

- 프로젝트 이미지 레시피에 `IMAGE_BOOT_FILES:append = " xen-${MACHINE};xen"`을 추가했다. WIC의 Xen 배포 의존성도 프로젝트 레시피에 명시했다. 부모 이미지에도 같은 의존성이 있어 기능상 중복이며 새로운 빌드 대상이나 버전을 추가하지 않는다.
- 메타데이터 검사는 확장된 매핑과 실제 `do_image_wic[depends]` 값을 검사한다.
- 새 WIC 검사 스크립트는 압축 이미지 자체에서 FAT 파일을 추출하고 deploy 원본 SHA-256, U-Boot 스크립트 CRC·명령, 펌웨어 설정을 검사한다. SD 장치와 마운트는 사용하지 않는다.
- [bring-up 가이드](yocto-rpi4-xen-minimal-bringup.md)에 재실행 명령을 추가했다.

## 실행과 결과

기준 HEAD는 `9902338ed4952e2828cf894b951c72e647a1e87d`이고 시작 시 작업 트리는 깨끗했다. 이 커밋 위의 WIC 수정본으로 빌드했다. 커널·Xen·외부 Layer 커밋을 변경하지 않았다.

```bash
bash scripts/check-perseus-platform.sh --metadata
# 별도 빌드 디렉터리에서 oe-init-build-env 적용 후:
bitbake perseus-xen-bringup-image
python3 -B scripts/check-perseus-wic.py --deploy-dir <아래 deploy 경로> --output <새 검사 디렉터리>
```

| 검증 | 실제 결과 |
| --- | --- |
| 기존 플랫폼 단위 테스트 | 52개 통과 |
| 부팅/init/network 회귀 검사 | 17개 통과 |
| BitBake 파싱·메타데이터 | 성공; Xen WIC 매핑·의존성 확인 |
| 이미지 빌드 | 종료 코드 0; 6602 태스크 중 6576개 재실행 불필요; sstate 99% 일치 |
| 실제 WIC FAT | 28개 검사 통과; `xen`, `Image`, `boot.scr`, `kernel8.img`가 deploy와 동일 |
| rootfs·rpi-sdimg·서비스·패키징 | 88개 검사 통과; readiness static unit 포함 4개 unit의 소유 패키지 확인 |
| 압축 WIC 전체 무결성 | `bzip2 -tv` 성공 |
| 이전 성공 산출물 보존 | 원래 deploy 파일 54개·직전 산출물 25개 해시, 링크 55개, 기존 설정 4개 일치 |
| 고정 Layer | 6개 checkout의 SHA·clean 상태 유지 |

빌드 경고 2개는 WSL VHDX 관리 안내와 호스트 `CONFIG_TASK_IO_ACCOUNTING` 미설정 안내였다. 실패한 빌드 태스크는 없다. 버전 대조 시 manifest에 없는 `kernel-image` 별칭을 조회한 일회성 도구 오류가 있었고, 실제 버전 포함 패키지명으로 재조회해 일치를 확인했다. 상세 설명은 로그 디렉터리의 `package-version-query-note.txt`에 남겼다.

## 산출물과 증거

기존 성공 이미지를 Yocto의 deploy 정리로부터 보존하기 위해 새 `TMPDIR`를 사용했다. 원래 빌드의 `downloads`, `sstate-cache`, hash-equivalence DB를 공유했고 삭제·clean 태스크를 실행하지 않았다.

- 빌드 경로: `/home/ssafy/yocto-workspace/build-rpi4-xen-wic-20261016-123602`
- 전체 로그·종료 코드·메타데이터·검사 결과·태스크 로그 3093개: `/home/ssafy/perseus-wic-build-20261016-123602`
- WIC: `/home/ssafy/yocto-workspace/build-rpi4-xen-wic-20261016-123602/tmp/deploy/images/raspberrypi4-64/perseus-xen-bringup-image-raspberrypi4-64.rootfs-20261016034040.wic.bz2`
- WIC 크기: `197418990` bytes; SHA-256: `d56dad2d92a160c5efda97d25d2573661c44ace5a0221a6276b1b67b2f35cf75`
- rpi-sdimg: `/home/ssafy/yocto-workspace/build-rpi4-xen-wic-20261016-123602/tmp/deploy/images/raspberrypi4-64/perseus-xen-bringup-image-raspberrypi4-64.rootfs-20261016034040.rpi-sdimg`
- rpi-sdimg SHA-256: `e8b8bc0d7a65bf6954a0fd967941457883a1bc21c53aba8c0cf5a0efadf25ee3`

`build.log`, `build.exit-code`, `static-checks.log`, `wic-checks.log`, `wic-boot-inspection/result.json`, `artifact-checks.log`, `artifacts.sha256`, `task-logs.tar.gz`를 보존했다. 임시 메타데이터 검사 출력도 `metadata-logs/`로 복사했다.

## 미검증 범위

SD에 기록하지 않았으며 실보드 자동 부팅, Xen 제어 인터페이스, DHCP·VM 실행·정상 종료는 재검증하지 않았다. Ubuntu·Debian 템플릿/인스턴스도 생성하지 않았다. 다음 하드웨어 검증에서는 WIC 기록을 별도로 승인한 뒤 수동 U-Boot 명령 없이 Xen→dom0 부팅, 브릿지 격리·DHCP 준비를 확인해야 한다.
