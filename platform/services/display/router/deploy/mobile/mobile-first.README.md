# 2026-11-05 변경 안내
현재 351은 HDMI 기본·터미널 왕복 전환으로 변경됐다.
[새 전환 절차](display-mode.README.md)를 먼저 따른다.
아래 persistent marker 절차는 기존 설치의 이력/원복 참고용이다.

---

# 351 휴대폰 우선 실행 구성과 HDMI 대체 시연

## 목표와 제공 범위

**갤럭시 S24 유선 통합 화면이 1차 목표다.** 휴대폰 연결·표시가 시연 조건을 충족하지 못할 때
운영자가 기존 HDMI 계기판·인포테인먼트 두 화면을 대신 실행한다.
상시 모드 선택 UI·전환 API·USB 분리 시 자동 HDMI 전환은 만들지 않는다.

이 구성은 최신 Yocto **native HDMI** 배포를 대상으로 한다. `/opt/xenova-display/current`의
이전 board-kms graphics rootfs 구성에는 그대로 적용하지 않는다. 이전 구성은 feed가 HDMI
target의 PartOf이므로 target stop이 공유 수집기를 함께 중지할 수 있다. 먼저 배포 구성을 확인한다.

| 구성 요소 | 휴대폰 우선 실행 | HDMI 대체 시연 |
|---|---|---|
| 기존 display-feed 1개·공유 VM 관측·Gateway 읽기 | 유지 | 동일 인스턴스 유지 |
| 349 유선 IP·접속 인증·브라우저 세션 | 필요 | 휴대폰 사용과 분리 |
| `/display/mobile` 브라우저 렌더링 | 휴대폰에서 실행 | 필수 아님 |
| dom0 native Xorg·Openbox·Chromium | 실행하지 않음 | 기존 native HDMI 서비스 사용 |
| 캡처·MJPEG 인코더·원격 영상 송신 | 필요 없음 | HDMI 직접 표시에도 필수 아님 |
| VM·API·Gateway·MQTT·Tailscale | 변경/재시작하지 않음 | 변경/재시작하지 않음 |

새 수집기·MQTT 클라이언트·상주 진단 데몬을 추가하지 않는다. 이미지에 그래픽 패키지가
설치돼 있어도 휴대폰 표시를 위해 실행할 필요는 없다. 이미지의 패키지 경량화는 별도 범위다.

## 빌드 담당 전달 사항

- 350의 `/display/mobile`이 포함된 vm-screens를 이미지의 기존 web assets로 빌드한다.
- 기존 display recipe는 수정하지 않고 351의 bbappend로 native HDMI service drop-in과
  이 안내·비밀값 없는 환경 예시만 추가 설치한다. `mobile_preflight.py`는 기존 Python 소스 설치에 포함된다.
- HDMI의 기존 target·서비스·자동 시작 설정은 바꾸지 않는다. **이미지에 휴대폰 우선 marker를
  자동 생성하지 않는다.** 349가 준비된 실제 배포에 운영자가 아래 절차로 명시적으로 적용한다.
- USB 네트워크 드라이버/IP·접속 인증·세션 제공은 348·349의 선행 조건이다.
  이번 drop-in만 넣었다고 USB 접속이 가능해지지는 않는다.

설치 파일:

```text
/usr/lib/systemd/system/xenova-display-hdmi.service.d/50-mobile-first.conf
/usr/share/xenova/display/mobile/mobile-first.README.md
/usr/share/xenova/display/mobile/mobile-feed.env.example
# 기존 Python package 안의 xenova_display.mobile_preflight
```

`systemd_system_unitdir`에 따라 vendor unit 경로는 `/lib/systemd/system`일 수도 있다.
아래 명령은 실제 보드의 root shell에서 실행하는 운영 절차이며, 문서 작성 중 실행한 명령이 아니다.

## 1. 배포 전 확인과 기록

```sh
systemctl show xenova-display-feed.service -p FragmentPath -p ExecStart -p PartOf -p ActiveState -p UnitFileState
systemctl show xenova-display-hdmi.service -p FragmentPath -p DropInPaths -p ActiveState
systemctl is-enabled xenova-display.target
systemctl is-enabled xenova-display-feed.service
systemctl status xenova-display-graphics.service xenova-display-kiosk.service xenova-display-stream.service --no-pager
```

- native feed의 ExecStart는 loopback `127.0.0.1:8082`, 단일 worker의 기존 uvicorn이며
  `PartOf=xenova-display.target`이 없어야 한다. 다르면 이 절차를 중단하고 설치 담당과 실제 경로를 확인한다.
- 기존 `.env`·토큰·MQTT 읽기 계정·bindings·API·USB/Tailscale 설정은 교체하지 않는다.
  `mobile-feed.env.example`은 기존 환경 파일에 필요한 비밀값 없는 키만 비교하는 참고 자료다.
- 관리 서비스가 공유 VM 관측 파일을 제공하고 기존 feed도 같은 경로를 사용해야 한다.
  Gateway가 미배포·미수신이면 주행 표시가 미확인인 것이 정상이며 임의 서비스를 설치/기동해 숨기지 않는다.
- 별도 legacy graphics/kiosk/stream이 이미 실행 중이면 이 절차로 강제 종료하지 않는다.
  소유 배포와 사용 여부를 확인한다. 아래 점검기는 이런 잔여 실행도 실패로 보고한다.
- root 전용 환경 파일의 내용을 채팅·화면 캡처·운영 기록으로 덤프하지 않는다.

349의 승인된 접속 경로와 브라우저 세션이 준비되지 않았다면 **아직 실행 전환하지 않는다.**
feed를 `0.0.0.0`으로 공개하거나 서비스 토큰을 휴대폰 URL에 붙여 우회하지 않는다.

## 2. 휴대폰 우선 실행 적용

최신 native 구성과 349 준비를 확인한 운영자가 수행한다. 최초 적용 전 marker가 원래 있었는지,
HDMI 서비스/target·feed의 enabled/active 상태를 기록한다. marker가 이미 있다면 소유권과
이 배포에서 만든 빈 일반 파일인지 확인하며, 알 수 없는 파일을 덮어쓰지 않는다.

최초 적용에서 marker가 **존재하지 않을 때만** 생성한다:

```sh
test ! -e /etc/xenova/display-mobile-first
install -o root -g root -m 0644 /dev/null /etc/xenova/display-mobile-first
systemctl daemon-reload
systemctl stop xenova-display-hdmi.service
systemctl start xenova-display-feed.service
```

`test`가 실패하면 뒤의 install을 실행하지 않는다. 기존 파일을 자동 덮어쓰는 설치 스크립트가 아니다.
공유 feed는 이미 실행 중이면 그대로 유지되며 restart하지 않는다. HDMI 서비스만 정상 종료해
그 서비스 소유의 Xorg·Openbox·Chromium을 정리한다. 전체 target·VM·제어 서비스는 stop하지 않는다.

drop-in의 `ConditionPathExists=!/etc/xenova/display-mobile-first`는 marker가 있으면 native HDMI
서비스의 **다음 시작을 건너뛰는 조건**이다. 실행 중인 프로세스를 자동으로 멈추지 않으므로
위의 명시적 HDMI stop이 필요하다. 단순 케이블 분리에는 반응하지 않는다.

부팅 후 공유 feed를 자동 시작할 필요가 있고 기록한 상태가 disabled였다면:

```sh
systemctl enable xenova-display-feed.service
```

기존 feed enable 상태가 바뀐 경우 운영 기록에 남긴다. HDMI target은 기존 enable 상태를 유지해도
marker가 있는 동안 native HDMI는 시작되지 않는다. 이 때문에 target active만으로 화면 성공을
판정하지 않는다. 사용자 서비스로 별도 시작한 인코더 등은 이 조건의 적용 대상이 아니다.

## 3. 상태 확인과 휴대폰 접속

```sh
systemctl is-active xenova-display-feed.service
systemctl is-active xenova-display-hdmi.service
systemd-run --quiet --pipe --wait --collect \
  --property=RuntimeMaxSec=15s \
  --property=EnvironmentFile=/etc/xenova/display.env \
  /usr/bin/python3 -m xenova_display.mobile_preflight
```

두 번째 명령의 inactive/비정상 종료 코드는 HDMI 미실행을 확인하는 용도다.
점검기는 기존 환경 파일을 읽는 일회성 transient unit 안에서 실행되며 기존 토큰을 argv에 넣지 않는다.
해당 이미지에 `systemd-run`이 없으면 승인된 기존 비밀값 주입 환경으로 같은 Python 모듈을 실행한다.
환경 파일을 `source`하거나 `systemctl show -p Environment`로 출력하지 않는다.

점검기는 loopback의 **GET 두 번**과 systemctl show만 수행한다. HTTP POST·세션 생성·서비스 변경·
Xen 직접 조회·추가 MQTT 연결을 하지 않으며 비밀값·원시 응답·예외 메시지를 출력하지 않는다.
`local_runtime_ready`는 로컬 실행 구성 확인일 뿐 휴대폰 접속·유선·차량 구동 성공이 아니다.
출력의 `sources`가 stale/unknown이면 화면에서도 그 상태를 유지해야 한다.

휴대폰에서 349가 마련한 주소의 `/display/mobile`에 승인된 방법으로 접속한다.
349의 접속 주소·인증 방식이 미정이면 임의 포트·IP·로그인 절차를 만들어 제시하지 않는다.
실제 S24 가로·세로 가독성은 350의 남은 수락 확인이며, 유선 복귀·장시간 사용 시험은 352에서 기록한다.

## 4. 부팅·재접속·실패 점검 순서

1. 폰/케이블 전원·USB 인식·349의 IP와 인증 세션을 확인한다.
2. 기존 feed active와 `mobile_preflight` 결과를 확인한다. 인증 실패와 데이터 미수신을 구분한다.
3. 모바일 페이지를 새로 열고 신선한 상태가 돌아오는지 확인한다. 오래된 VM 상태·주행 모드를 정상이라고 간주하지 않는다.
4. 앱/단말의 주소창·화면 잠금·배터리 제한은 실제 지원 범위에서 확인한다. 화면 유지가 항상 지원된다고 가정하지 않는다.
5. 시연 조건을 충족하지 못하면 원인을 기록하고 운영자가 아래 HDMI 대체 절차를 선택한다.

부팅 후에는 feed만 자동 시작하고 휴대폰 브라우저는 사용자가 연다. 부팅·USB 재연결이
자동 차량 조작이나 VM 재시작·승계를 발생시키지 않는다.

## 5. 휴대폰 시연이 어려울 때 HDMI 대체 실행

운영자가 휴대폰 대신 HDMI 시연을 결정하고, 이 절차에서 생성한 root 소유 빈 marker임을
확인한 경우에만 아래 **정확한 파일 하나**를 제거한다. 다른 설정·데이터를 삭제하지 않는다.

```sh
rm -- /etc/xenova/display-mobile-first
systemctl start xenova-display.target
systemctl status xenova-display-hdmi.service --no-pager
```

기존 두 모니터·정확한 connector/EDID·Xorg·kiosk 환경이 준비돼 있어야 한다.
하나에는 계기판, 다른 하나에는 인포테인먼트가 뜨는지 직접 확인한다.
서비스 active와 DOM ACK만으로 물리 표시 성공이라고 기록하지 않는다.
HDMI target start는 기존 feed가 이미 실행 중이면 그 인스턴스를 재사용한다.
직접 HDMI 표시를 위해 원격 스트림/인코더를 새로 시작하지 않는다.

HDMI가 실패하면 `journalctl -u xenova-display-hdmi.service`와 기존 런타임 증거를 확인하며
VM·Gateway·MQTT·네트워크를 재설정하거나 임의 kill로 우회하지 않는다.

## 6. 시험 변경 복원·인계

- 최초 기록과 비교해 이 시험에서 만든 marker만 정리한다. 원래 있었던 marker는 임의 삭제하지 않는다.
- 이 시험에서만 feed enable 상태를 바꿨다면 원래 enable 상태를 복원한다. 공유 feed를 임의로 stop하지 않는다.
- HDMI active 상태는 원래 상태 또는 운영자가 채택한 시연 방식으로 맞추고 실제 화면 결과를 기록한다.
- 토큰·MQTT·bindings·Gateway·VM·외부 Spring·Tailscale·USB 네트워크 설정은 변경 대상이 아니다.
- 기록: 소스/이미지 커밋, 실제 unit 경로, marker의 최초/최종 상태, 서비스 최초/최종 active/enabled,
  접속 인증 결과, 주행/VM 데이터 freshness, 폰/HDMI 직접 확인 여부와 미수행 항목.

이번 351의 로컬 구현만으로 348·349·350 실물 확인·352를 완료 처리하지 않는다.
