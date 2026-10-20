# dom0 관리 Wi-Fi와 SSH

WPA2-Personal(PSK), nl80211, systemd-networkd IPv4 DHCP를 지원하는 구성이다.
802.1X, 캡티브 포털, AP 모드, NAT, 포트 포워딩 규칙, Zephyr 네트워크는 제공하지 않는다.
무선 링크를 xenbr0에 연결하지 않는다. 내부 DHCP/주소 정책은 192.168.77.0/24 그대로다.

## Raspberry Pi 4 Xen 부팅 설정

프로젝트의 boot.cmd.xen.in은 Control FDT의 복사본에서
/soc/mmcnr@7e300000의 dmas·dma-names만 제거한다.
고정 BSP에서 무선 SDIO의 DMA 펌웨어 전송이 시간 초과한 경로 대신
커널의 기존 PIO 경로를 사용한다. SD 카드 호스트·DMA 컨트롤러는 변경하지 않는다.
DMA 실패의 내부 원인까지 확정한 수정은 아니다.

xenova-platform은 기존 커널의 kernel-module-brcmfmac-cyw를 함께 설치한다.
brcmfmac이 CYW vendor 모듈을 요청할 때 사용할 수 있어야 한다.
/etc/modprobe.d/xenova-wifi.conf의 feature_disable=0x2000은
펌웨어 인증 오프로딩(FWSUP)을 끄며 WPA2 키 교환은 wpa_supplicant가 처리한다.
configure가 저장하는 disable_scan_offload=1은 예약 스캔 오프로딩을 끈다.
커널·펌웨어·SDK 버전과 암호화 방식은 변경하지 않는다.

이 조합으로 한 부팅에서 AP 인증·DHCP·PC SSH가 확인됐다.
인증 옵션과 AP 선택을 독립적으로 비교한 결과는 아니므로 오프로딩만을 단독 원인으로
단정하지 않는다. WRONG_KEY도 초기 키 교환 중 단절을 근거로 한 추정 진단이다.

새 이미지에서 보드별 configure·activate를 한 번 실행하면 자격증명과
서비스 enable 링크가 영구 저장된다. 이후 부팅에서는 SDIO 장치 인식 → 모듈 로드 →
설정된 supplicant 시작 → networkd DHCP 순서로 연결한다.
공통 이미지에는 실제 SSID·PSK나 특정 AP의 주소를 포함하지 않는다.
수정 소스를 빌드한 이미지의 반복 재부팅·자동 연결은 후속 실보드 수락 항목이다.

## 보드별 설정

새 dom0에 실제 드라이버/펌웨어/도구가 포함된 뒤 UART의 root 셸에서 실행한다.
SSID와 PSK는 대화형 터미널에서 입력한 문자가 화면에 표시된다.
PSK도 UART 화면·터미널 녹화본에 남을 수 있으므로 입력 구간을 공유하지 않는다.
자격증명을 명령행 인자로 받지 않으며 결과 JSON과 상태·오류 출력에는 포함하지 않는다.
설정 파일은 /etc/xenova/wifi/<인터페이스>.conf, 디렉터리 0700/파일 0600이다.
PSK의 파생값도 접속 자격증명이므로 파일 내용·백업을 공유 로그나 검토 묶음에 넣지 않는다.
국가 코드는 실제 사용하는 국가로 지정한다. 인터페이스는 실제 무선 장치여야 한다.

    ip -brief link
    xenova-wifi configure --interface wlan0 --country KR
    echo $?

실패하면 중단하고 인터페이스·입력 규칙을 확인한다. 이 단계는 연결을 시작하지 않는다.
일반 암호는 8..63자의 ASCII, 이미 계산된 PSK는 64자리 hex만 허용한다.
SSID는 1..32 UTF-8 bytes로 검사하고 hex로 인코딩하므로 설정 문자열을 주입하지 않는다.

    xenova-wifi activate --interface wlan0
    echo $?
    xenova-wifi status --interface wlan0
    systemctl status xenova-wifi@wlan0.service --no-pager
    networkctl status wlan0 --no-pager
    ip -4 address show dev wlan0

설정은 dom0 시스템 영역에 남는다. 비밀 없는 25-xenova-wifi.network는 0644로
networkd가 읽을 수 있게 하고 PSK 파일 0600/부모 0700은 유지한다.
activate는 공통 Wi-Fi 잠금 안에서 소유한 xenova-wifi@<인터페이스>.service 상태를 읽는다.
inactive/failed면 enable 후 start, active면 enable 후 restart하여 새 SSID/PSK를 읽힌다.
activating/deactivating 또는 잘못된 상태는 중복 기동하지 않고 거부한다.
설정 적용 시 현재 Wi-Fi SSH 연결이 끊길 수 있으므로 UART 등 복구 경로를 준비한다.
이후 networkd reload와 해당 무선 인터페이스 reconfigure만 요청한다.
내부 DHCP·브릿지·VM 서비스를 재시작하지 않으며 wpa_supplicant를 수동 중복 실행하지 않는다.

activate JSON의 activation.action은 start/restart, result는 requested/failed다.
requested는 서비스·주소 설정 요청이 반환됐다는 뜻이며 AP 인증이나 IP·SSH 성공이 아니다.
실패하면 result=failed와 state-query/service-state/enable/supplicant/network-reload/
network-reconfigure 중 실패 단계를 반환하고 종료 코드 1을 사용한다.
저장된 자격증명은 유지하며 실패한 단계 이후의 명령이나 자동 재시도는 실행하지 않는다.
하위 도구의 원문 오류와 자격증명은 이 응답에 포함하지 않는다.
기존 배포판의 wpa_supplicant 범용 D-Bus 서비스는 기본 enable하지 않는다.
Wi-Fi service는 장치 대기 제한이 20초이며 VM 서비스에 Requires/After를 추가하지 않는다.
Wi-Fi 미설정/AP 부재/인증 실패/IP 미획득은 내부 VM 자동 시작을 막지 않는다.
이는 PID1/실보드 시험에서 별도로 확인해야 한다.

상태는 configured, wpa_state, IPv4, 내부망 중첩 여부만 노출한다. SSID/PSK를 출력하지 않는다.
SCANNING/DISCONNECTED 등만으로 AP 부재와 암호 오류를 확정하지 않는다.
필요하면 로컬 서비스 로그와 AP 상태를 대조하며 자격증명을 공유 로그에 복사하지 않는다.
관리망이 192.168.77.0/24와 겹치면 접속 진행을 멈추고 다른 관리망을 준비한다.
내부 IP를 임의로 바꾸거나 Wi-Fi를 브릿지에 합쳐 우회하지 않는다.

## 개발 PC에서 접속

실제 dom0 주소는 위 status/ip 출력에서 확인한다. 아래 MANAGEMENT_IP를 실제 주소로 바꾼다.
공유망에 노출하기 전에 UART에서 passwd로 기존 개발용 암호를 운영용으로 변경한다.
이번 소스는 기존 암호/키/machine-id를 재발급하지 않는다. 호스트 키 변경 경고를 무시하지 않는다.

    ssh root@MANAGEMENT_IP
    ssh -J root@MANAGEMENT_IP root@192.168.77.10
    ssh -J root@MANAGEMENT_IP root@192.168.77.20

첫 번째는 PC→dom0 관리 주소, 나머지는 PC→dom0→내부 Ubuntu/Debian SSH다.
PC에서 게스트 내부 주소로 직접 라우팅할 수 있다고 가정하지 않는다.
dom0의 AllowTcpForwarding local과 PermitOpen은 내부 .2..99의 TCP22만 허용한다.
OpenSSH는 먼저 읽은 설정을 사용하므로 기존 Xenova auth include를 앞에 둔다.
이 설정은 기존 호스트 키 준비 서비스→일반 sshd.service 순서를 변경하지 않는다.
후속 WIC에서 sshd -T와 실제 ProxyJump 연결을 모두 확인한다.

## 후속 타깃 검증

새 dom0 빌드/조립 승인 후 실제 WIC에서 다음을 확인한다.
1. brcmfmac·brcmfmac-cyw/cfg80211/SDIO 모듈과 Image의 kernel release/vermagic 일치,
   boot.scr의 무선 SDIO PIO 설정, modprobe.d 정책, board NVRAM/clm_blob/regdb와 서명.
2. wpa_supplicant/wpa_cli/iw/networkctl, Python ctypes/crypt/unixadmin와 모듈 import.
3. 설정 파일이 공통 이미지에는 없고 보드 입력 후 root 전용으로 영구 보존되는지.
4. AP 연결→IPv4→dom0 SSH 리스너→PC 로그인→ProxyJump 순서대로 확인한다.
5. Wi-Fi 미설정/AP 부재/인증 실패 상태에서도 세 VM 자동 시작이 진행되는지.
6. xenbr0/brif에 물리 무선 NIC가 없고 내부 DHCP만 동작하는지.
   configure/activate 이후 일반 재부팅과 전원 재인가에서 수동 모듈 재적재·스캔 명령 없이
   COMPLETED·DHCP·PC SSH가 준비되는지 반복 확인한다. 관리 주소는 DHCP로 달라질 수 있다.
7. key/machine-id/파일 보존과 혼합 종료·소프트웨어 재부팅을 반복하고 결과를 기록한다.

수집 명령의 종료 코드가 0일 때만 비교 파일을 확정한다.
실패한 ssh 출력으로 생긴 빈 파일을 보존 실패/데이터 손실로 판정하지 않는다.

근거 문서:
- https://w1.fi/wpa_supplicant/
- https://www.freedesktop.org/software/systemd/man/253/systemd.network.html
- https://man.openbsd.org/ssh_config#ProxyJump
- https://man.openbsd.org/sshd_config#AllowTcpForwarding


고정 BSP의 rpidistro 펌웨어 레시피는 synaptics-killswitch 제한으로 제외된다.
제한 승인을 추가하지 않고 고정 OE의 linux-firmware-bcm43455 패키지를 명시적으로 사용한다.
해당 레시피는 brcm/brcmfmac43455-sdio.*와 cypress/cyfmac43455-sdio.*를 소유한다.
실제 다음 WIC에서 bin/clm_blob/Pi4 NVRAM/regdb/p7s가 모두 있어야 조립 검사를 통과한다.
레시피 해석이나 upstream 파일 목록 확인은 타깃 파일 실물·RF 연결 검증을 대신하지 않는다.

regulatory.db/p7s의 실제 소유 패키지는 wireless-regdb-static이며 이 패키지를 설치한다.
