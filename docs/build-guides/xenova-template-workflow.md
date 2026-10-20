# Xenova 기본 템플릿의 커스터마이징과 게시

공식 기본 참조는 Xenova Ubuntu·Debian·Starter 3종이다. 이전 Perseus 준비본과
Xenova 준비본을 두 개의 현행 기본 카탈로그로 유지하지 않는다. 과거에 게시된
불변 버전과 실제 수락 기록은 이력이며 새 이미지에 호환 목적으로 복사하지 않는다.

## 원본·작업 자산·게시본

| 단계 | 위치·계약 |
| --- | --- |
| 수정 가능한 Linux 원본 | `platform/guests/linux/<source-id>/recipe.json`와 `files/`. 공통 OS 준비 코드는 `platform/guests/common/` |
| 수정 가능한 Starter 원본 | `platform/guests/zephyr/starter-app/`와 의도한 앱 버전·해시의 `starter.lock.json` |
| 준비/빌드 작업 자산 | 새 전용 workspace. 중단 시 기존 게시본을 덮어쓰지 않고 조사용 작업 경로를 남김 |
| 버전별 게시본 | Linux `linux/<source-id>/<version>/`, Starter `zephyr/xenova-zephyr-starter-<app-version>/<template-version>/`. 파일 0444, 동일 출력 덮어쓰기 거부 |
| 현행 기본 선택 | `platform/guests/default-templates.lock.json`. 템플릿 ID별 한 버전·실제 manifest SHA를 선택 |
| VM 생성 | 선택된 게시본을 공통 instances 경로로 독립 복사. 기존 VM에 자동 적용하지 않음 |

Linux `source_id`는 OS 원본의 고정 출처이며 `version`은 프로젝트 준비 버전이다.
Ubuntu·Debian의 OS 이름·원본 digest·커널 버전을 준비 버전과 혼동하지 않는다.
현재 기본 배포 버전은 1.0.1이며 이전 1.0.0 게시본은 보존한다. 내용 변경 시 recipe의 버전을 정하고,
다른 버전 출력 경로를 사용해 준비→검증→게시→기본 참조 갱신을 수행한다.

## Linux 수정·준비·검증

기본 recipe의 MOTD가 Xenova 안내를 제공한다. 팀원은 `files/`의 앱 기본 파일과
recipe의 source/target/mode를 수정한다. 개별 root.raw를 수작업으로 패치하지 않는다.
overlay는 새 OS 작업 사본에만 적용한다. guest 신원·SSH·systemd·커널과 OS 식별 파일은
보호 대상이며, 해당 정책을 바꿀 때는 공통 준비 소스를 명시적으로 수정·회귀 검증한다.
임의 패키지 설치나 네트워크 다운로드 hook은 추가하지 않았다.

```bash
# 이미 검증한 캐시 OS 입력을 사용한다. 출력 부모를 먼저 새로 준비한다.
python3 -B platform/scripts/prepare-xenova-guests.py \
  --execute template \
  --source ubuntu-24.04-20260926 --input /absolute/cache/ubuntu-arm64.img \
  --recipe /absolute/repo/platform/guests/linux/ubuntu-24.04-20260926/recipe.json \
  --output /absolute/releases/linux/ubuntu-24.04-20260926/1.0.1
rc=$?; echo "prepare rc=$rc"; [ "$rc" -eq 0 ] || exit 1
sha256sum /absolute/releases/linux/ubuntu-24.04-20260926/1.0.1/template.json
python3 -B platform/scripts/verify-xenova-template.py \
  --template /absolute/releases/linux/ubuntu-24.04-20260926/1.0.1 --sha256 ACTUAL_MANIFEST_SHA256
rc=$?; echo "verify rc=$rc"; [ "$rc" -eq 0 ] || exit 1
```

준비는 승인된 Linux root·ARM64 chroot 환경에서 수행한다. 이번 실행은 기존 도구와
임시 ARM64 handler를 전용 mount namespace에서 사용하고 종료 후 해제했다.
새 버전은 root·Image·initrd·kernel.config·boot.json의 실제 bytes/SHA와 recipe/공유
준비 소스의 해시를 template.json에 기록한다. 입력이 중간에 달라지면 게시하지 않는다.
검증기는 전체 payload·e2fsck -fn·xenova-root·실제 guest 내부 경로·SSH Include·
서비스 enable 링크·미할당 machine-id/공유 SSH 키 부재를 읽기 전용으로 확인한다.
초기 비밀번호를 유지하는 crypt salt, 배포판 sudo 문서의 예제 호스트명과 udev의 외부 라디오 제품명은 플랫폼 명칭이 아니므로 원본과 대조해 유지한다.

## Starter 수정·실제 빌드·게시

Zephyr 4.4.0·SDK 1.0.0·1 vCPU/16MiB/GICv2는 유지한다.
Starter 코드 변경 시 의도한 앱 version/id와 실제 app_files/app_source_sha256을
lock에서 갱신한다. lock과 실제 소스가 다르면 빌드 도구는 거부한다.
원본 commit·SDK archive digest는 변경하지 않는다.

```bash
python3 -B platform/scripts/refresh-xenova-starter-lock.py \
  --version 1.1.0
# 계획을 확인한 뒤 --execute를 추가한다. 소스만 변경하고 버전이 같으면 거부한다.
python3 -B platform/scripts/refresh-xenova-starter-lock.py \
  --version 1.1.0 \
  --execute
```

이후 빌드·게시 명령의 --version/--template-id도 새 lock 값으로 맞추고,
검증 후 default-templates.lock.json과 defaults.json의 기본 template 참조를 함께 갱신한다.
아래 예시는 Starter 앱 1.0.0과 배포 템플릿 1.0.1을 구분한다. 앱 소스·Zephyr·SDK lock은 유지한다.


```bash
python3 -B platform/scripts/build-xenova-starter.py \
  --zephyr-source /absolute/pinned/zephyr \
  --toolchain-prefix /absolute/sdk/bin/aarch64-zephyr-elf- \
  --toolchain-archive /absolute/cache/fixed-toolchain.tar.xz \
  --python-tools /absolute/existing/python-tools \
  --build-dir /absolute/new-starter-build --execute
rc=$?; echo "build rc=$rc"; [ "$rc" -eq 0 ] || exit 1
python3 -B platform/scripts/package-xenova-firmware.py \
  --build-dir /absolute/new-starter-build/zephyr --license /absolute/pinned/zephyr/LICENSE \
  --app xenova-starter --version 1.0.0 --template-id xenova-zephyr-starter-1.0.0 \
  --template-version 1.0.1 \
  --source-revision sha256:ACTUAL_APP_SOURCE_SHA256 --build-id YOUR_BUILD_ID \
  --app-source /absolute/repo/platform/guests/zephyr/starter-app \
  --output /absolute/releases/zephyr/xenova-zephyr-starter-1.0.0/1.0.1 --execute
rc=$?; echo "publish rc=$rc"; [ "$rc" -eq 0 ] || exit 1
```

실제 새 빌드에만 xenova-build-receipt.json을 발급한다. 게시기는 소스·버전·receipt·
payload 해시와 Xenova 안내문을 확인한다. 이전 Perseus 바이너리나 receipt 이후
변경된 바이너리는 거부한다. 테스트용 성공 출력은 실제 펌웨어 빌드 증거가 아니다.
일회성 안내 후 무기한 sleep은 정상 종료 지원이 아니며 capabilities는 유지한다.

## 기본 선택과 다음 이미지

```bash
python3 -B platform/scripts/resolve-xenova-templates.py \
  --release-root /absolute/releases > references.tmp
rc=$?; echo "catalog rc=$rc"; [ "$rc" -eq 0 ] || exit 1
mv references.tmp references.json
```

이 명령은 current lock의 3종을 모두 실제 검증한 뒤 조립 config의 linux_templates/rtos_templates
부분을 출력한다. 이전 이름의 중복 엔트리·경로 이탈·manifest 불일치는 거부한다.
선택 버전만 새 이미지의 templates/rtos-templates에 포함한다. 버전 이력 전체를 싣지 않는다.
새 Linux VM에는 선택한 template_version/template_manifest_sha256도 생성 출처로 남긴다.
UUID·MAC/IP·독립 디스크·키 생성과 Linux/Zephyr 생명주기 계약은 유지한다.

seed·최종 이미지 조립은 별도 명시적 실행으로 수행한다. 기본 참조를 포함한 dom0를 빌드한 뒤
현재 소스와 일치하는 관리 모듈·SSH 구성을 확인하고, 이 default 참조로 새 seed와
최종 이미지를 조립한다. 기존 사용 중 VM은 이번 템플릿 변경으로 초기화하지 않는다.
실제 VM 부팅·SSH·앱·혼합 종료·reset·DTB/IRQ 수락은 별도다.


## 공개 관리 ID와 기존 게시본

VM API는 ubuntu-24.04-arm64, debian-13-arm64, zephyr-xenvm-starter-arm64와 명시적 버전을 사용한다.
기존 게시본의 날짜 기반 id는 source_template_id/출처로 보존한다.
새 catalog descriptor가 공개 ID+버전과 기존 불변 manifest 해시를 연결한다.
기존 root.raw/firmware bytes나 manifest를 바꾸어 새 ID로 가장하지 않는다.
새 게시 내용이 달라지면 새 배포 버전과 실제 해시를 선택한다.
관리 카탈로그 게시와 최초 설치 연결은 xenova-vm-management.md의 절차를 따른다.

최초 설치 배치와 템플릿 카탈로그는 별개다. `resolve-xenova-templates.py`는 Linux 2종을 `linux_templates`, Starter 1종을 `rtos_templates`로 반환한다. 이를 새 조립 config에 넣으면 Ubuntu는 불변 템플릿만 보관하고 Debian Interaction seed 1개와 독립된 Zephyr A/B seed 2개를 준비한다. 기존 템플릿 버전·payload·manifest 해시는 변경하지 않는다.

Agent 설치는 구현 소스와 installer가 제공될 때 별도 연결한다. 현재 준비 도구는 게스트에 Agent·Paho를 설치하지 않는다. dom0의 FastAPI 서버와 MQTT 클라이언트 패키징은 [dom0 앱 구성](xenova-dom0-app-payloads.md)을 따른다.
