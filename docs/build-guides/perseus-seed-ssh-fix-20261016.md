# seed SSH 및 전용 chroot 런타임 보완 — 2026-10-16

이 기록은 dom0 후속 빌드 전에 완료한 소스 보완·회귀 검사 결과다. 원본/템플릿 및 이전 성공 WIC를 보존했다. 실제 seed/인스턴스 생성·최종 SD 이미지 조립·SD 접근/변경은 하지 않았다.

## 확인한 원인과 수정

`platform/guests/common/prepare_instance.py::verify_guest_ssh(seed=True)`는 `sshd -G -C ...`를 호출했다. 실제 Ubuntu/Debian sshd는 `-C`에 `-T`가 없어 exit 255로 실패했다. seed는 `-G -T -C`로 host key를 생성/공유하지 않고 root Match까지 검사하고, 일반 인스턴스는 `-T -C`로 기존 고유 host key도 읽게 했다. 60초 제한을 추가했다.

기존 인스턴스 생성 경로는 root만 마운트해 Debian chroot의 `/dev/null`과 SSH privsep 디렉터리가 준비되지 않았다. `guest_runtime(work, root)`가 SSH 검사 동안만 `/dev`와 `/run`을 전용 tmpfs로 덮고, 단일 null character device(1:3, 0666)와 `/run/sshd`(0755)를 준비한다. 호스트 `/dev` bind나 `/proc`/`/sys` 노출은 하지 않는다. 실제 두 배포판에서 이 최소 환경으로 검사에 성공했다.

mountpoint는 기존의 링크 없는 디렉터리여야 한다. 성공·sshd 실패·timeout·두 번째 mount 실패·mknod 실패 모두 직접 만든 mount만 역순 해제한다. 해제 실패는 오류로 전달하고 Workspace에 남겨 재정리하며 게시 성공으로 처리하지 않는다. rootfs의 기존 `/dev`·`/run` 내용과 비밀번호·machine-id·SSH 키를 덮어쓰지 않는다.

## 실행한 검사

- `bash scripts/check-perseus-platform.sh`: **149 tests OK**, boot/init/network **17개 PASS**. 실제 systemd loader 정상 exit 0, 주입 cycle exit 1.
- 신규 `tests/platform/test_perseus_seed_ssh.py` 9개: seed/normal 옵션과 timeout, 인증 정책 불일치/프로세스 오류, tmpfs와 권한, 성공/실패 정리, device/mount 실패, symlink 거부, unmount 실패 추적.
- 실제 Ubuntu `ubuntu-24.04-20260926`, Debian `debian-13-20261015`의 기존 8GiB 템플릿을 `ro,noload` loop로 읽고 production `guest_runtime`/`verify_guest_ssh`를 실행했다. 두 배포판에서 seed·일반 모드가 모두 통과했다. ARM64 실행은 이전에 검증한 private QEMU/binfmt를 사용했고 등록은 제거했다.
- seed 검사는 공유 host key 없이 수행했다. 일반 모드의 검증용 키 한 개는 별도 `/etc/ssh` tmpfs에만 생성해 검사 후 폐기했다. 기존 템플릿의 키·파일 metadata는 보존했다. VM 또는 새 seed 디스크를 만들지 않았다.
- 전체 로그, 실제 검사 스크립트, effective config, 명령/종료 코드는 `/home/ssafy/perseus-seed-ssh-build-20261016-160540/`에 보존했다. `regression.log`, `real-guest-ssh.log`, `real-guest-ssh-results.json`을 참조한다.

## 커밋·후속 빌드 경계

이전 단계의 Ubuntu 별도 `/boot` 보완, Zephyr 불변 펌웨어 템플릿/세 템플릿 용량·복사 규칙과 이번 SSH/chroot 보완을 함께 검토 가능한 소스 상태로 커밋·push한 뒤 `perseus-xen-bringup-image`를 별도 빌드 경로에서 재빌드한다. 외부 Layer HEAD와 기존 downloads/sstate-cache를 유지하고 기존 build/deploy 산출물을 덮어쓰지 않는다. clean/cleanall은 사용하지 않는다.

현재 이전 성공 WIC에는 이번 source-byte 변경이 없으므로 그 이미지에 파일을 끼워 넣지 않는다. 새 빌드 로그·종료 코드 및 WIC 내부 source-byte/부팅 파일/패키지 소유권 검사 결과는 위 전용 실행 경로의 후속 빌드 기록으로 보존한다. 이 소스 검증 기록 자체는 빌드 성공을 미리 주장하지 않는다.

최종 SD 이미지 조립·기록·확장·실보드 부팅·VM 정상 종료/보존 시험은 별도 단계다. Zephyr의 하드웨어 호환성/미지원 범위는 기존 RTOS 보고서대로 유지한다.
