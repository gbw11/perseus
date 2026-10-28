# 256 실보드 키오스크 서비스 기동·장애 복귀 검증 — 2026-10-28

작업 브랜치 `feat/platform/S15P31E110-256-kiosk-recovery`, 기준 platform `ffed0f8`. 이전 256 MR과 252 MR이 반영된 기준에서 후속 검증했다. 제품 소스 변경 없이 기존 제품 Kiosk의 복귀 동작을 실제 보드에서 검증했다.

## 결과

| 시험 | 실제 확인 |
|---|---|
| systemctl start | 두 HDMI 물리 매핑 + renderer_alive + 적용 revision 확인 |
| 한쪽 Chromium SIGKILL | 해당 브라우저 PID 교체·두 출력 확인 응답 복귀, 반대 브라우저 PID 유지 |
| 감독 Python SIGKILL | systemd MainPID 13201→13776, NRestarts 0→1, 새 layout_epoch로 두 출력 확인 응답 복귀 |
| systemctl stop | inactive/dead, MainPID=0, ControlGroup 비어 있음 |
| 명시적 재시작 | MainPID 14322, 다시 다른 layout_epoch로 두 출력 확인 응답 복귀 |
| 마지막 stop·정리 | inactive/dead, MainPID=0, 시험 unit·토큰 환경 파일 제거 |

화면은 계기판 1280×720+0+0, 인포테인먼트 1280×720+1280+0으로 실제 두 HDMI에 출력했다. 복귀 후 두 실제 X11 영역 캡처를 열어 주소창 없는 서로 다른 차량 화면을 확인했다. 사용자 육안 확인을 별도로 받은 시험은 아니다. Xen 실행 상태는 조회했지만 앱/주행 보고·센서·미디어는 연결하지 않아 미확인/수신 대기를 유지했다.

## 이번에는 실제로 실행한 제품 경로

앞선 두 화면 시험기는 Chromium을 직접 실행했다. 이번에는 저장소 `xenova_display.kiosk.Kiosk`와 Playwright가 브라우저 두 개를 관리했다. 제품 `deploy/xenova-display-kiosk.service`의 Restart=on-failure, RestartSec=3, TimeoutStopSec=15, KillMode=control-group을 유지한 **runtime-only 시험 unit**으로 systemctl start/stop과 감독 프로세스 재시작을 실행했다.

보드에는 정식 키오스크와 Playwright가 없었다. 기존 격리 ARM64 Debian graphics rootfs의 `/opt/xenova-kiosk-runtime/20261028` 아래에 Python 3.13용 Playwright 1.64.1·pydantic 2.14.1 및 의존 wheel을 배치했다. 시스템 패키지 관리자나 호스트 Python을 바꾸지 않았다. wheel별 SHA-256과 import 확인을 보관했다. 브라우저는 기존 Chromium이며 sandbox를 유지했고 `--no-sandbox`를 쓰지 않았다.

시험 unit에는 RootDirectory/MountAPIVFS와 `/run`·`/tmp` bind, 격리 경로용 환경·ExecStart를 추가했다. 호스트에 UID1000 계정이 없어 root에서 chroot 내부 setpriv를 실행한 뒤 **제품 Python·브라우저는 UID/GID1000으로 권한을 낮췄다.** root 브라우저나 신규 호스트 계정은 만들지 않았다. 토큰은 root 전용 환경 파일로 systemd에 전달하고 브라우저 argv·URL·JS·증거에는 넣지 않았다. 정확한 unit 내용은 evidence/trial-kiosk.service에 있다.

Xorg·Openbox와 private feed는 시험 실행부에서 별도로 준비했다. **그래픽 세션을 포함한 부팅 자동 시작 전체, 제품 unit을 원래 경로 그대로 설치한 운영 배포, Yocto 이미지 편입의 검증은 아니다.** 시험 서비스는 /run에만 만들고 종료 후 제거했으며 enable하지 않았다. 정식 서비스의 동작 로직과 restart/stop 규칙은 검증했지만 이 범위를 자동 부팅 배포 완료로 확대하지 않는다.

## 실패 이력과 판정 보완

- 07:28:45 UTC: 호스트에 없는 UID1000을 systemd User로 지정해 217/USER로 기동 전 실패. unit 정리 후 chroot 안에서 setpriv를 사용하는 시험 프로파일로 변경했다.
- 07:31:09 UTC: MountAPIVFS 환경에서 /run 설정이 보이지 않아 FileNotFoundError. 해당 시험 runtime 경로를 bind하도록 바로잡았다.
- 07:32:46 UTC: 두 화면 기동·브라우저 복귀는 성공. 감독 재시작 후 오래된 상태 파일을 읽어 조기 성공 판정을 했고, 초기화 중 stop은 timeout으로 실패했다. 이 실행 전체를 통과로 취급하지 않는다.
- 07:36:43 UTC 최종: 감독 재시작 이후의 **다른 layout_epoch, 시작 경계 이후 updated_monotonic, 3초 미만 최신성, 두 출력 물리 매핑·렌더링 확인 응답**을 모두 요구했다. 이 조건으로 재시작 및 안정화 후 stop/start/stop을 통과했다. 앞선 실패를 제품 수정으로 고쳤다고 기록하지 않는다. 최종 시험은 기존 제품 소스로 통과했다.

상태 파일이나 프로세스 start 접수만으로 성공을 계산하지 않았다. DOM 확인 응답은 물리 프레임 지연의 측정값이 아니며 257의 500ms 목표 달성을 뜻하지 않는다.

## 종료 후 보드

VM3/VM1/VM2 domid 1/2/3 유지, 기존 listener SSH22·Manager8081 유지, systemd failed 0, graphics 프로세스·private mount 없음, VT1 복귀를 별도 SSH로 조회했다. 최종 framebuffer 복원 비교는 다른 바이트 0이다. VM·API 서비스·펌웨어·주행 명령·부팅 설정은 이번 시험에서 변경하지 않았다. 이전 KMS 설정과 boot_id를 유지하며 추가 재부팅하지 않았다. 의존성·stage 파일은 재현을 위해 격리된 /opt 시험 경로에 남아 있다.

## 256 완료 조건과 연결

- 두 물리 출력의 서로 다른 화면, 독립 선택·새로고침, 각 HDMI 케이블 분리/재연결: 앞선 [256 두 화면 시험](display-256-dual-screen-test-20261028.md).
- 정지·일시정지·작업 진행·미수신·정상 복귀의 렌더링: [252 두 출력 상태 시험](display-252-dual-state-20261028.md), 합성 입력 범위를 명시.
- 외부 웹·원격 스트리머가 없는 상태에서 loopback feed·로컬 HDMI를 기동했다. 실제 NIC 단절 중 시험은 하지 않았다.
- 모니터 상태와 브라우저/감독 장애 복귀, 적용 revision 구분: 앞선 EDID/hotplug 증거와 이번 실제 Kiosk runtime 상태로 확인.
- 실보드/두 HDMI 증거와 가상·합성 범위: 각 보고서에서 구별했다.

256의 수동 기동·표시·복귀 동작에 대한 증거를 보완했다. 부팅 자동 시작 운영 배포와 이미지 재설치 재현은 아직 남아 있고, 실제 주행/게스트 연동은 253·255, 원격 CPU·지연은 257에서 다룬다. 이 기록만으로 지라 상태를 자동 변경하지 않는다.

[시험 요약](evidence/display-256-kiosk-service-20261028/summary.json) · [종료 후 조회](evidence/display-256-kiosk-service-20261028/postflight.json). 같은 폴더에 네 실행의 원본 결과·정확한 원격 소스 gzip, 최종 helper, 실제 출력 이미지, 의존성 해시와 checksum 목록이 있다. 커밋·푸시·MR 생성·지라 변경은 아직 하지 않았다.

## 후속 상시 배포

이후 [상시 배포·기본 부팅 자동 시작 검증](display-256-autostart-20261028.md)을 수행했다. 위의 부팅 자동 시작 미수행은 당시 범위이며, 후속 정상 재부팅 1회에서 자동 표시와 사용자 확인을 받았다. Yocto 이미지 패키징은 여전히 범위 밖이다.
