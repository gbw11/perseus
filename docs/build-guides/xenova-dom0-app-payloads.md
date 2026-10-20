# dom0 API·MQTT 배포 계약

이미지 타깃 xenova-xen-bringup-image는 xenova-dom0-manager와 xenova-mqtt-client를 설치한다.
전자는 기존 platform/services/dom0-manager를 FastAPI/Uvicorn과 함께 설치하고 systemd 일반 서비스로 시작한다.
후자는 기존 Python 공통 MQTT 라이브러리와 Paho를 설치한다. 새 Agent·브로커·Worker는 만들지 않는다.

## 실행·설정
unit은 python3 -m dom0_manager를 실행한다. XENOVA_DRIVER=platform,
XENOVA_PLATFORM_LIB=/usr/lib/xenova이며 기존 VM 신원·자산·IP·정책을 재초기화하지 않는다.
기본 바인드는 기존 계약의 127.0.0.1:8081이다. 네트워크·Tailscale을 바꾸거나 외부로 열지 않는다.
설정은 /etc/xenova/dom0-manager.env이다. 이미지에는 실제 서비스 토큰을 넣지 않는다.
보드에서 외부 바인드를 설정할 때 기존 계약대로 XENOVA_SERVICE_TOKEN을 준비한다.
UART에서 root 편집기로 설정하며 토큰을 명령행·셸 이력·로그에 넣지 않는다.
토큰 기록 전 파일을 root:root 0600으로 제한한다. 서비스 재시작은 운영자가 수행한다.
systemctl status xenova-dom0-manager.service --no-pager와 실제 인증된 요청을 따로 확인한다.
기존 shutdown gate를 재사용하며 새 복구 루프는 없다.

## MQTT와 템플릿
현재 공통 계약의 xenova_mqtt 모듈과 xenova/v1 토픽을 사용한다. 소스는 platform/services/vm-agent/mqtt-client에 있다.
Ubuntu/Debian 게스트에서 사용할 때는 Linux 템플릿 준비 단계에도 동일 소스·Paho 설치·검증이 필요하다.
현재 게스트 준비 경로에는 Agent 설치 호출이 연결돼 있지 않으며, 최종 조립이 이를 자동 설치하지 않는다.
factory seed 전용 identity 서비스와 일반 VM의 오프라인 신원 준비를 구분해 Agent unit 의존성을 검증해야 한다.
dom0 설치가 게스트 설치를 대신하지 않는다. Zephyr에 Python 라이브러리를 넣거나 PV 네트워크를 추가하지 않는다.
기존 Linux 템플릿·Starter는 보존하며 이번에는 seed·최종 이미지를 조립하지 않는다.

## 이미지·조립 검증
고정 Layer의 FastAPI/Uvicorn/Paho 버전을 사용하며 Layer·SDK를 업그레이드하지 않는다.
프로젝트 API/MQTT 소스에 별도 라이선스 선언이 없어 두 설치 레시피는 CLOSED로 표시한다.
제3자 패키지 라이선스·출처·해시는 각 Layer 레시피를 따른다.
Starlette가 import하는 shlex는 고정 Python manifest의 python3-shell 패키지로 설치한다.
조립 gate는 두 소스 트리·pyproject·unit·기본 설정의 바이트 일치,
필수 런타임 모듈과 enable 링크를 확인한다. 새 payload가 없는 이전 이미지는 거부한다.
실제 WIC에서는 패키지 소유권·ARM64 import·HTTP 진입점을 추가 검증한다.
서버 리스너와 Xen/실보드 실제 VM 제어는 별도 결과다.
