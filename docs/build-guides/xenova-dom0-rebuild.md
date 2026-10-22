# dom0 재빌드 준비와 수락 경계

재빌드 타깃은 `xenova-xen-bringup-image`, 보드는 `raspberrypi4-64`다.
플랫폼 소스·설정·외부 layer revision을 고정하고 회귀 결과와 빌드 입력을 대조한다.
WSL Linux 파일시스템을 사용하고, 출력·로그·소스 snapshot은 Git 저장소 밖에 둔다.

## 빌드 전

1. 작업 브랜치·HEAD·미커밋 변경을 확인한다. 팀 변경을 임의로 덮어쓰지 않는다.
2. `bash platform/scripts/check-xenova-platform.sh`로 회귀·실제 systemd loader·recipe 설치 검사를 수행한다.
   systemd 259 이상이 필요한 검사는 해당 버전의 실제 실행 파일을 사용하고 낮은 버전으로 성공 처리하지 않는다.
3. `bash platform/scripts/setup-yocto.sh --check`로 `layers.lock`의 소스를 검사한다.
4. 새 외부 `YOCTO_BUILD_DIR`를 지정해 setup을 실행한다. 기존 local.conf/bblayers.conf를 덮어쓰지 않는다.
   기존 download/sstate cache는 경로·설정 일치를 확인해 재사용하며 소스 사본을 layer로 선택하지 않는다.
5. `bitbake -p`, 필요한 recipe의 `bitbake -e`/`bitbake-getvar`와
   `platform/tests/check_xenova_metadata.py`로 소스 경로·의존성·unit·WIC 의존성을 검사한다.
   환경 전체를 증거로 덤프하지 말고 비밀 없는 필수 변수만 보존한다.
6. 소스 파일·layers.lock·설정·기존 산출물의 SHA256과 실행 argv를 기록한다.
   실행 직전 다시 대조해 회귀 이후 입력 변경을 감지한다.

FastAPI의 upstream ptest만 제외하는 기존 승인 설정
`PTEST_ENABLED:pn-python3-fastapi = "0"`을 유지한다.
FastAPI/Pydantic Core/MQTT 및 필수 host 도구를 제거하는 설정이 아니다.
QEMU 개발 probe/lab은 운영 설치·자동 실행에 넣지 않으며 Xen backend는 유지한다.

## 빌드와 후속 검증

빌드 환경 진입 후 명시적으로 `bitbake xenova-xen-bringup-image`를 실행한다.
준비 단계의 parse/env/dry-run 성공은 이미지 빌드 성공을 뜻하지 않는다.

새 WIC에서는 실제 설치된 Manager/CLI·resources/transition/control/신원/복구 모듈,
정책·unit·libxenmanage, Linux cfg와 Wi-Fi PIO/CYW/scan 설정을 검사한다.
`xenova-vms.service`의 ExecStop과 공용 QEMU backend/guard 종료 순서를 실제 unit으로 확인한다.
FastAPI·MQTT 설치와 소스 일치도 확인한다. 새 웹 콘솔의 tty/termios 및 websockets도 실제 ARM64 Python에서 import한다.
없는 Worker/Agent/Gateway를 구현 완료로 표시하지 않는다.

최종 조립은 별도 실행 단계다. 기존 Ubuntu/Debian 템플릿을 보존하고 새 network Starter
1.1.0의 ELF/DTS/config/manifest·source lock을 검사해 별도 release로 게시한다.
기본 선택 lock의 SHA256이 게시본과 같아야 한다. factory 기본은 Debian 1대와 Zephyr A/B이며
per-card UUID·MAC·예약 IP·키 초기화·용량과 비밀 없는 공통 이미지 조건을 다시 검사한다.

새 SD의 최초 설치·부팅 정책·Wi-Fi 자동 연결·A/B 통신·mixed 종료는 실제 보드에서 수락한다.
기존 이미지에 부분 적용한 시험 결과로 새 이미지 수락을 대신하지 않는다.
[구현 계약과 실보드 확인 범위](../../platform/docs/resource-boot-zephyr.md)를 함께 본다.


## 통계·heartbeat 감시기 개발용 의존성

`112-xentop-stats`의 `xentop -b -i 1 -n -x -f`는 고정 Xen recipe의
`xen-tools-xenstat`가 제공한다. `124-heartbeat-watcher`의 공용 클라이언트는
`xenova-mqtt-client`와 그 `python3-paho-mqtt` 의존성이 제공한다.
Manager recipe에서 둘을 직접 요구하며 datetime·queue·signal 등은 기존 Python split 패키지로 충족한다.

이미지에는 `mosquitto`와 `mosquitto-clients`도 설치하지만 broker는 자동 기동하지 않는다.
담당자가 `platform/deploy/mosquitto/README.md`의 계정·ACL·리스너 배치를 완료한 뒤
기존 `mosquitto.service`를 활성화한다. 공통 이미지에 계정 비밀번호나 개인 접속점을 넣지 않는다.
Worker 코드·unit을 대신 설치하거나 enable하지 않는다.

의존성 준비는 기능 브랜치 병합과 별개다. 검토한 기준은
112 `1f9a9bb8ee6016863e61b0dbff69eba8f3af66d3`,
124 `3d532f4103567cce71a7ed2b926c46b8c349e786`이다.
124는 `XenovaClient.subscribed()`와 early SUBACK 처리를 사용하므로 현재 공용 클라이언트만으로
감시기 동작을 보장하지 않는다. 시험 소스를 옮길 때 해당 브랜치의
`platform/services/vm-agent/mqtt-client/xenova_mqtt`도 같은 revision으로 가져와야 한다.
단순 `paho-mqtt` 추가 설치로 이 API 차이를 해결할 수 없다.

새 WIC에서는 xentop·broker·pub/sub/rr/passwd 실행 파일과 동적 라이브러리,
Python `queue`·`signal`·`datetime`·Paho·공용 클라이언트를 실제 ARM64로 확인한다.
WSL의 broker 왕복 시험은 임시 loopback listener만 사용하며 실보드 Xen 통계·운영 MQTT ACL 수락과 구분한다.
