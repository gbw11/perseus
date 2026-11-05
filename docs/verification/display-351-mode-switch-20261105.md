# 351 — HDMI 기본·터미널 표시 방식 왕복 전환

- 날짜: 2026-11-05
- 기준: `origin/platform` `fb192eb`
- 브랜치: `feat/platform/S15P31E110-351-display-mode-switch`
- 사용자 변경: 기본은 HDMI, 필요하면 휴대폰으로 바꾸고 다시 HDMI로 돌아온다.
  버튼 대신 터미널 명령이면 충분하다고 확정했다.
- 로컬 구현·검증 기록이다. 보드·휴대폰·외부 서버에는 접속하거나 배포하지 않았다.

## 제공 기능

```sh
sudo python3 -m xenova_display.display_mode status
sudo python3 -m xenova_display.display_mode mobile
sudo python3 -m xenova_display.display_mode hdmi
```

root 전용 일회성 CLI이며 새 관리 API·웹 버튼·상주 sampler·인코더는 만들지 않았다.
mobile은 기존 USB listener의 active 및 제한된 endpoint 정보를 기다린 뒤 HDMI를 중지한다.
Type=simple의 active와 실제 endpoint 파일 준비 사이 간격도 허용한다.
이는 실제 휴대폰 접속을 보장하는 검사가 아니다. 기존 349 연결번호 인증을 그대로 사용한다.

HDMI는 native service active 이후 기존 kiosk 상태를 읽어 두 출력의 mapped,
renderer_alive, physical_verified 및 3초 이내 timestamp를 기다린 뒤 mobile을 중지한다.
이 renderer 관측도 육안 화면 성공의 대체 근거로 쓰지 않는다.
휴대폰 listener 중지 후 다음 mobile 전환에서는 새 연결번호가 필요하다.

runtime marker와 추가 unit 조건으로 현재 선택을 적용한다. `/run`이 지워지는 재부팅에서는
기존 native target의 HDMI 기본 실행으로 돌아간다. unit enable 상태를 바꾸지 않는다.
기존 persistent mobile-first marker가 있으면 소유·이관을 확인하도록 거부하고 자동 삭제하지 않는다.

## 변경 경계와 실패 처리

- systemctl 변경 allowlist는 HDMI/mobile 두 unit의 start/stop뿐이다.
- 공유 feed는 실행 상태만 검사한다. VM·주행 제어·Gateway·API·MQTT·Tailscale·USB·인증
  설정을 바꾸지 않고 제어 상태를 추측/생성하지 않는다.
- flock으로 동시 명령을 거부하며 marker의 root 소유·일반 파일·권한·정확한 내용을 확인한다.
- 실패하면 기존 marker와 건드린 표시 서비스의 초기 active/inactive 상태로 복원을 시도한다.
  복원 실패는 별도 오류 코드다. 복원 성공이 실제 화면 복구나 세션 유지 성공이라는 뜻은 아니다.
- JSON 출력은 고정 오류 코드와 정리된 표시 상태만 제공한다. 원문 예외·ExecStart·비밀값을 출력하지 않는다.
- 별도 legacy/user stream은 소유하지 않는다. native 전환 전 운영자가 해당 실행을 정리해야 한다.
  임의 user manager 검색이나 다른 사용자의 프로세스 종료를 하지 않는다.

## 배포·기존 점검기 연계

351 bbappend에 `60-display-mode.conf`와 새 운영 안내를 추가했다.
기존 349/351 설치와 private 설정/서비스 자동 활성화 정책은 보존했다.
rootfs checker는 새 CLI·guard·운영 안내가 빠지면 실패하고 runtime marker를 이미지에 넣는 것도 거부한다.
mobile_preflight는 새 runtime 선택 및 해당 guard를 확인하며 기존 legacy 점검도 호환한다.

[운영 명령·전제·이관·복원 안내](../../platform/services/display/router/deploy/mobile/display-mode.README.md)를 따른다.

## 검증

다음 명령은 실제 서비스 변경 없이 로컬 WSL에서 수행했다.

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pytest \
  tests/test_display_mode.py tests/test_mobile_deployment_bundle.py \
  tests/test_mobile_runtime.py tests/test_mobile_access_deploy.py \
  tests/test_native_session.py -q -o addopts=
```

**최종 96 passed, 0 failed, 0 skipped, 1 warning (27.41초)**. 경고는 기존 Starlette/httpx 어댑터 안내다.
직전 전체 실행의 기존 신호 재발급 테스트 1건이 4초 subprocess 제한을 넘었지만,
제한 시간을 바꾸지 않은 단독 재실행(호출 3.08초)과 최종 전체 실행은 통과했다.
이력을 [로컬 검사 요약](evidence/display-351-mode-switch-20261105/local-test-summary.json)을 따른다.

- 제한된 fake service manager로 HDMI→mobile→HDMI 반복, 이미 active인 listener 재시작 방지,
  공유 feed 무변경, 준비 지연·timeout·실패 복원·부분 복원 실패를 검증했다.
- 오래된/오류 renderer, unsafe marker·symlink, 기존 persistent 정책·guard 미설치를 거부했다.
- 실제 flock의 겹친 실행 거부와 CLI root 권한·원문 예외 비노출을 검증했다.
- base/349/351 실제 설치 shell 조각을 임시 rootfs에서 실행해 새 CLI/guard/운영 문서가
  함께 설치되는지 검사하고 실제 systemd loader 및 condition 검사를 재사용했다.
- 실제 USB 기동 지연·ARM 실행·BitBake/패키지 QA·부팅 성공·물리 화면 성공을 수행한 것은 아니다.

## 남은 실장비 검증

새 이미지와 실제 설치 설정이 준비된 뒤 352에서 실제 S24 유선 인증→화면,
HDMI 왕복→재인증, 실패 시 이전 표시 복구, 재부팅 HDMI 기본 및 10분 안정성을 확인한다.
257의 그래픽 이미지 준비/원격 영상 시험과 251·253 완료 조건을 이 결과로 대체하지 않는다.
351 Jira 설명은 새 사용자 범위로 수정했고 상태는 진행 중으로 유지했다.
