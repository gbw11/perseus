# 351 휴대폰 우선 실행 구성 — 로컬 구현·검증

- 기준: `origin/platform` eb0f009. 350의 통합 UI가 이미 반영된 시점이다.
- 작업 브랜치: `feat/platform/S15P31E110-351-mobile-first-runtime`.
- 실제 보드 접속·서비스 변경·VM 조작·프론트 변경은 수행하지 않았다.
- 기존 추적 파일은 수정하지 않았다. 실행 지원·점검·검증용 새 파일만 추가했다.

## 구현 결과

휴대폰을 1차 시연 수단으로 두고, 어려울 때 운영자가 기존 HDMI 두 화면을 실행하는 구성이다.
모드 선택 UI·전환 API·자동 HDMI 전환은 추가하지 않았다.

1. native HDMI 서비스에 추가 설치하는 drop-in은 `/etc/xenova/display-mobile-first`가 있으면
   다음 HDMI 서비스 시작을 건너뛴다. marker는 실제 배포에서 운영자가 명시적으로 적용하며 이미지에는 자동 생성하지 않는다.
2. 기존 공유 display-feed를 단일 인스턴스로 유지한다. 불필요한 dom0 Xorg·Chromium·인코더를
   휴대폰 표시 때문에 실행하지 않으며, VM·API·Gateway·MQTT·네트워크를 중지하지 않는다.
3. 일회성 `python3 -m xenova_display.mobile_preflight`는 systemctl show와 인증된 loopback GET
   두 번으로 실행 상태를 확인한다. HTTP POST·세션 생성·추가 Xen/MQTT 수집은 하지 않는다.
4. 기존 recipe를 수정하지 않고 bbappend로 drop-in·실행 안내·비밀값 없는 환경 참고 예시를 추가 설치한다.
   기존 target·자동 시작 정책·팀원 native 세션 코드·공통 frontend는 유지한다.
5. 부팅 후 재현, 실패 점검, HDMI 수동 대체, 시험 변경 복원·인계 절차를 작성했다.

실제 운영 명령과 선행 조건은
[휴대폰 우선 실행·HDMI 대체 안내](../../platform/services/display/router/deploy/mobile/mobile-first.README.md)에 있다.
이 문서의 명령은 보드에서 이미 실행했다는 기록이 아니다.

## 경량화와 상태 구분

- 상주 진단 서비스·추가 표시 feed·새 MQTT/VM/Gateway 조회 경로를 만들지 않는다.
- 점검 도구는 실행할 때만 동작하고 종료한다. systemctl 조회에는 4초, 각 local HTTP에는 2초
  대기 제한이 있으며 응답 크기도 제한한다. 안내의 transient unit은 전체 실행 시간을 15초로 제한한다.
- 토큰은 기존 보호된 환경 파일에서 주입하고 argv·URL·JSON 기록에 넣지 않는다.
  proxy·redirect를 사용하지 않고 고정 loopback으로만 요청한다.
- `local_runtime_ready`는 실행 구성·응답 경로 점검이다. `sources`가 미수신/끊김이면 그대로 보고하며
  물리 휴대폰·USB·HDMI 표시 성공이나 차량 이동으로 해석하지 않는다.
- marker는 UI 옵션이 아니라 운영자가 적용하는 배포 설정이다. 이미 실행 중인 HDMI를 자동 중지하지 않는다.
  최초 적용 때 소유 native HDMI 서비스만 명시적으로 중지하는 절차가 필요하다.
- 이전 board-kms 배포는 feed가 target의 PartOf일 수 있으므로 이 절차를 그대로 적용하지 않는다.

## 로컬 검증

환경: Windows 작업 폴더 + 로컬 WSL, Python 3.14 테스트 가상환경, systemd-analyze 259.
실제 Raspberry Pi/ARM 실행·부팅 시험과는 구분한다.

```sh
# platform/services/display/router에서
python -m pytest tests/test_mobile_runtime.py tests/test_feed.py tests/test_policy.py \
  tests/test_gateway_drive.py tests/test_native_session.py
```

**103개 통과, 실패/건너뜀 0개.** 기존 httpx/Starlette 테스트 어댑터 deprecation 경고 1건이 있다.

- 새 351 검사 26개: native/legacy 구분, feed 실패, 그래픽 잔여 실행, marker의 잘못된 상태,
  단일 worker 옵션 확인, credential 누락·오류 출력 비노출, 기존 API와의 실제 인증 통합.
- 실제 local HTTP 서버: 성공/401/302/503, redirect 미추적, proxy 미사용, 응답 크기 제한.
- 실제 systemd 조건 평가: 임시 marker 없음 → 허용, 생성 → 차단, 제거 → 다시 허용.
  서비스나 시스템 unit을 시작/정지하지 않고 조건 식만 평가했다.
- bbappend의 install 조각을 임시 root에 실행해 payload 파일의 상대 경로·설치 결과를 확인했다.
  이미지에 marker·자동 시작 wants 링크를 만들지 않는 것을 검사했다.
- 기존 feed·정책·Gateway·native 세션 회귀 검사 77개도 통과했다.

[검증 요약 JSON](evidence/display-351-mobile-first-runtime-20261103/local-test-summary.json)을 함께 남겼다.
**BitBake 전체 빌드, 패키지 QA, 새 이미지 부팅은 아직 수행하지 않았다.**

## 남은 실제 확인과 티켓 경계

- **348/349**: 실제 S24 USB 드라이버·유선 IP·보드 접근·브라우저 인증 세션을 준비해야 한다.
  임의 bind 주소나 새 인증 우회를 이 작업에서 만들지 않았다.
- **350**: 실제 S24 화면의 가독성·가로/세로 확인이 남아 있다.
- **351 실제 적용**: 최신 native 배포 및 349 준비 후 원래 서비스 상태를 기록하고 marker 적용,
  재부팅 뒤 feed 유지/HDMI 미실행을 확인해야 한다. 로컬 준비만으로 실보드 적용 완료라고 하지 않는다.
- **352**: 실제 휴대폰 유선 복귀·장시간 사용·성능과 필요 시 수동 HDMI 대체 실행을 검증·인계한다.
- Jira 상태는 변경하지 않았다. 이 기록은 로컬 검증 결과이며 실보드 적용 완료를 뜻하지 않는다.
