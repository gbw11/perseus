# UI-05 웹 터미널 / MO-04 Spring 중계 v1

```text
React xterm.js → Spring /ws/v1/vms/{vm_id}/console
                → dom0-manager /api/v1/vms/{vm_id}/console → PTY → xl console
```

VM 상세 화면에서 연결 버튼으로 접속한다. 브라우저에는 사용자 JWT만 있으며,
dom0 서비스 토큰은 Spring의 서버 설정에만 둔다.
내부 콘솔 규격은 [console-websocket-v1.md](console-websocket-v1.md)를 따른다.

## 공개 WebSocket

접속 주소는 `/ws/v1/vms/{vm_id}/console`이다. URL에 토큰을 넣지 않는다.
기존 메트릭/주행 WebSocket과 같은 첫 프레임 인증 방식을 사용한다.

```json
{"type":"auth","access_token":"<user-access-token>"}
```

서버는 JWT 서명·만료·폐기 여부를 확인하고, HTTP VM API와 같은 등록된 VM 범위를
조회한다. 기존 시스템은 등록된 VM을 로그인한 관리 사용자에게 공유하며 사용자별 소유권/RBAC 모델은 없다.
실행 중이거나 paused인 게스트만 연결한다. 임의 domid나 명령 문자열을 받지 않는다.
인증/업스트림 연결이 10초 안에 완료되지 않으면 닫는다.

Spring은 설정된 dom0-manager 주소에 `Authorization: Bearer <service-token>`을 붙여 연결한다.
연결되면 아래 text 메시지를 보낸다. 그 전에는 터미널 입력을 보내지 않는다.

```json
{"type":"connected","vm_id":"<selected-vm-uuid>"}
```

이후 양방향 데이터는 binary 프레임이다. 사용자 입력은 UTF-8 바이트이고 출력은
PTY 원시 바이트다. 터미널 내용과 게스트 암호는 애플리케이션 로그에 저장하지 않는다.

- 입력 프레임 상한 64KiB. 브라우저의 큰 붙여넣기는 이 크기로 나눈다.
- 브라우저 입력 대기량은 256KiB, xterm 출력 처리 대기량은 1MiB를 넘으면 종료한다.
  끊긴 입력을 자동 재전송하지 않는다.
- 서버는 출력 바이트를 삭제하며 따라잡는 방식을 쓰지 않는다. 느린 수신자는 제한 시간/버퍼 상한으로 닫는다.
- JWT 만료 시 idle 상태도 종료하며, 추가 입력 때 폐기 여부를 다시 확인한다.
- 로그인 인증 전에 binary를 보내면 4401, 연결 준비 중 입력하면 4409로 닫는다.
- 4400: 잘못된 VM 식별자 / 4401: 인증 실패·만료 / 4404: VM 없음 /
  4409: 점유·상태 변경 / 1009: 크기 초과 / 1011: 연결·중계 실패.
- 내부 콘솔의 종료 코드도 전달한다. 인증 뒤 text는 `{"type":"disconnect"}` 제어 프레임만
  허용하며, 그 밖의 text 입력은 1003으로 닫는다.
- 연결 해제 버튼은 위 제어 프레임을 전송하고 `연결 정리 중` 상태로 입력과 재접속을 막는다.
  Spring은 내부로 요청을 전달하고 dom0-manager가 PTY/UUID 점유를 해제한 뒤 보내는 종료를
  브라우저에 전달한다. 정리 응답은 최대 8초 기다리며 실패 시 오류로 닫는다.
  연결 설정 중 취소도 업스트림이 열리는 대로 정리한다.
- 화면 이동/VM 변경/로그아웃 시 브라우저는 연결을 닫고, Spring도 업스트림을 닫는다.
  늦게 완료된 인증/연결 작업은 이전 화면의 연결을 다시 열지 않는다.
- 자동 재접속하지 않는다. 사용자가 연결 버튼을 다시 누르며 필요하면 기존 refresh 쿠키로 JWT를 갱신한다.

## 화면

VM 상세 페이지에 xterm.js 콘솔을 지연 로드한다. 연결·연결 해제·화면 지우기와
연결 상태/오류 안내를 제공한다. 화면 지우기는 표시 버퍼만 정리한다.
Linux에서는 연결 후 Enter를 보내 로그인 프롬프트를 확인한다.
브라우저 로그인과 게스트 로그인은 별개이며 게스트 계정을 자동 입력하지 않는다.

Zephyr Starter는 배너를 한 번 출력하고 대기한다. 현재 셸/입력 처리기는 없어
로그가 없을 때 빈 화면일 수 있다는 안내를 표시한다. 접속 성공을 주행/미러링 준비로 표시하지 않는다.
FitAddon은 웹 터미널 표시 크기를 맞추며, 게스트 셸의 터미널 크기 협상은 이 버전에서 제공하지 않는다.

## 실행 설정

1. Linux dom0-manager: `XENOVA_CONSOLE_ENABLED=1`, `XENOVA_SERVICE_TOKEN` 설정.
2. Spring: `DOM0_MANAGER_BASE_URL`, 같은 값의 `DOM0_MANAGER_SERVICE_TOKEN` 설정.
3. 프론트: `VITE_BACKEND_ORIGIN`을 Spring 주소로 설정. 서비스 토큰을 `VITE_*`에 넣지 않는다.

기본 Vite 개발 프록시는 `/api`와 `/ws`를 Spring으로 전달한다. Origin 허용 목록은
기존 `WEBSOCKET_ALLOWED_ORIGINS`를 사용한다. 운영 주소에는 HTTPS/WSS와 실제 Origin을 지정한다.
Spring DB에 등록하지 않은 외부 Xen 도메인은 기존 HTTP API와 동일하게 표시/접속 대상에서 제외된다.

## 검증 기록 (2026-10-21)

- 프론트: 테스트 39개, 타입 검사, 프로덕션 빌드 통과.
- Spring: 새 실제 WebSocket 중계 통합 테스트 7개 통과.
- Chrome → Vite → Spring → QEMU dom0 FastAPI → PTY → Debian `/dev/hvc0`에서
  로그인 및 `hostname`, `uname -srm`, `tty` 결과 확인.
- 실제 브라우저에서 중복 접속 거부, 종료 후 재접속, Zephyr 접속·해제 확인. 페이지 JS 오류 없음.
- 전체 Spring 테스트는 135개 중 133개 통과. `VmServiceTest`의 기존 Mockito stubbing 오류 2개는
  기준 커밋 `a29618b`의 격리 복사본에서도 동일하게 재현했다. 관련 기존 코드는 변경하지 않았다.
- 라즈베리파이 실보드 시험과 Zephyr 양방향 입력 기능 검증은 별도다.

5단계 장애/재접속 시험과 발견한 경쟁 조건 수정은
[MO-04 QEMU 인수 검증](../verification/mo-04-qemu-acceptance.md)에 기록한다.
