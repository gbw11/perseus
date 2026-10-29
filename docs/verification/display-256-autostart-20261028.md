# 256 상시 배포·기본 부팅 자동 시작 검증 — 2026-10-28

## 결과

검증된 로컬 feed·전용 KMS 그래픽 세션·제품 Kiosk를 상시 서비스로 배치하고 `xenova-display.target`을 multi-user.target에 enable했다. **정상 재부팅 한 번 후 수동 start 없이 두 실제 HDMI 화면이 자동 표시됐다.** 사용자가 두 화면이 정상 크기로 자동 표시되는 것을 직접 확인했다.

- boot_id: `98d7d22b-7484-49a2-a5f7-d660303e85fb` → `a3fc3b67-2fcf-46c1-88dd-29255c169f21`.
- tryboot_flag=0: 앞선 일회성 시험 부팅을 벗어나 기본 config.txt로 부팅했다. config SHA-256은 기존 확정값 `9f8dc2146b654152fc7f1a1388de6915a7f8d57257d9d1400c0686c99eb81557`을 유지했다.
- 새 layout_epoch, 3초 미만 상태 시각, 두 출력 physical_verified/renderer_alive 및 applied_revision=1을 확인했다.
- HDMI-1 계기판 1280×720+0+0, HDMI-2 인포테인먼트 1280×720+1280+0. 부팅 후 실제 두 X11 영역을 캡처해 주소창 없는 서로 다른 화면을 직접 열어 확인했다.
- feed·graphics·kiosk active/running, NRestarts=0. 실패 systemd unit 0. 기존 Manager API active/running 및 loopback8081 유지.
- Debian/VM1/VM2 이름·UUID/역할을 배포 전 실제 Xen으로 대조했고, 재부팅 후 domid1/2/3 실행 복귀를 확인했다.
- 표시 API는 127.0.0.1:8082에만 listen. 미인증401·정식 로컬 토큰200을 확인했다. 기존8081 API 토큰·Spring 설정·네트워크 정책은 바꾸지 않았다.

## 배포 내용

저장소 [board-kms 실행 프로파일](../../platform/services/display/router/deploy/board-kms/README.md)에 세 Python 실행 파일과 네 개의 systemd unit, 실행 순서·복원 방법을 추가했다. 전용 Xorg는 READY=1로 준비 완료를 알린 뒤 Kiosk를 시작한다. 실제 제품 Kiosk의 브라우저/감독 프로세스 복귀·서비스 stop/start는 [선행 검증](display-256-kiosk-service-20261028.md)에서 확인했다.

상시 release는 `/opt/xenova-display/releases/20261028-ffed0f8`, 진입 symlink는 `/opt/xenova-display/current`다. 기존 격리 graphics rootfs와 ABI별 Python 의존성을 고정 실행 묶음으로 사용했다. 호스트 시스템 패키지나 커널·Xen·게스트 펌웨어는 교체하지 않았다. 이번 재부팅에 따라 VM과 API 재시작은 발생했다.

`/etc/xenova/display-256/` 아래 bindings와 feed/kiosk 환경 파일을 배치했다. 디렉터리0700·파일0600이고 토큰을 보드에서 생성했으며 Git·브라우저 URL·VITE 번들·증거에 저장하지 않았다. 실제 UUID·이름을 확인한 deployment bindings를 사용했다. 역할과 앱 ID는 기존 값을 유지했다. MQTT 계정·ACL·펌웨어를 새로 만들지 않았고, 미수신 앱/주행/센서 값은 대기·미확인으로 표시한다.

새 unit을 덮어쓰기 전에 기존 동일 이름이 없는지 확인했고 기존 boot config와 배포 전 상태 manifest를 root 전용 backup 경로에 남겼다. 정확한 위치는 evidence/autostart-backup.json에 있다. 기존 파일 전송을 보존하며 배포 스크립트의 경로 치환·재전송 오류를 수정했다. 보드에 systemd-analyze가 없어 해당 도구 검사는 수행하지 못했으며, 실제 systemd LoadState=loaded/LoadError 없음·수동 기동·정상 재부팅 결과로 검증했다.

마지막 이미지 수신을 위한 SSH 재접속은 두 번 시간 초과됐다. 이후 같은 주소의 ping/ARP 응답과 SSH 복귀를 확인해 캡처를 받았다. 네트워크나 표시 설정을 바꿔 해결한 것은 아니고 원인은 확정하지 않았다. 이 현상을 숨기거나 장시간 네트워크 안정성 통과로 계산하지 않는다.

## 현재 상태와 복원

두 화면은 상시 서비스로 켜 두었다. 앞으로 같은 이미지/실행 묶음에서 부팅하면 자동 표시한다. 자동 시작 해제·화면 중지 방법은 다음과 같고 VM·Manager API를 중지하지 않는다.

```sh
systemctl disable --now xenova-display.target
```

release와 원본 백업을 지우지 않았다. 새 이미지로 재설치하면 graphics rootfs·ABI별 의존성·서비스/설정/배포 bindings를 다시 배치해야 한다. **Yocto 이미지 자체에 포함하는 패키징은 이번 변경 범위에 없다.** 검증은 소프트웨어 정상 재부팅 1회이며 전원 분리 콜드 부팅 반복·장시간 부하는 아니다.

256의 수동 표시·두 모니터 독립성·상태 렌더링·HDMI 재연결·프로세스 복귀에 이어, 이번 상시 배포·부팅 자동 시작까지 확인했다. 실제 주행 데이터/앱 영상 연동은 253·255, 원격 전달 성능은257의 별도 범위다. 커밋·푸시·MR·지라 상태 변경은 아직 하지 않았다.

[시험 요약](evidence/display-256-autostart-20261028/summary.json) · [재부팅 후 상태](evidence/display-256-autostart-20261028/autostart-after-reboot.json) · [배포 해시·권한](evidence/display-256-autostart-20261028/autostart-captures-manifest.json). 정확한 배포/재부팅/조회 스크립트 gzip, 실제 캡처와 checksum도 같은 폴더에 있다.
