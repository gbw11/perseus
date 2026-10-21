# MO-04 내부 콘솔 WebSocket v1

관리 게이트웨이가 Linux dom0-manager의 PV 텍스트 콘솔에 접속하는 계약이다.
기존 HTTP OpenAPI 계약은 변경하지 않으며 WebSocket은 이 문서로 정의한다.
주행 명령·미러링·이중화 경로와는 별개인 개발/운영 콘솔이다.

## 접속

```text
ws://127.0.0.1:8081/api/v1/vms/{vm_id}/console
Authorization: Bearer <service-token>
```

- `{vm_id}`는 HTTP VM API와 동일한 UUID다. domid, 표시 이름, 셸 명령을 받지 않는다.
- `XENOVA_CONSOLE_ENABLED=1`을 명시하고 `XENOVA_SERVICE_TOKEN`을 설정해야 한다.
  기본값은 비활성이다. 토큰 없이 활성화하거나 `XENOVA_XL_SSH`와 함께 활성화하면 시작을 거부한다.
- `xen` / `platform` 드라이버로 Linux dom0에서 실행한다. `mock`은 콘솔을 열지 않는다.
  `xen` 드라이버의 생명주기 API는 계속 조회 전용이며, 콘솔 입력은 별도 활성화 기능이다.
- HTTP 인증 미들웨어와 별개로 WebSocket handshake의 Authorization을 직접 검증한다.
  URL query의 토큰은 인정하지 않는다. 비활성/인증 실패는 upgrade 전 거부(네트워크 HTTP 403).
- 서비스 토큰 보유자는 관리 대상 게스트 콘솔에 접근할 수 있다. 사용자 로그인·VM별 사용자 권한은
  게이트웨이에서 확인해야 한다. 브라우저 표준 WebSocket은 임의 Authorization 헤더를 넣지 못하므로
  4단계에서는 인증된 백엔드 프록시 등을 연결해야 한다. 서비스 토큰을 프론트엔드에 배포하지 않는다.
- 기본 loopback 바인드를 유지한다. 외부 배포의 TLS/게이트웨이 연결은 배포 계층에서 구성한다.

## 데이터

| 방향 | 프레임 | 내용 |
| --- | --- | --- |
| 클라이언트 → 서버 | binary 또는 UTF-8 text | 터미널 입력. text는 UTF-8로 인코딩해 전달 |
| 서버 → 클라이언트 | binary | PTY 원시 출력, 프레임당 최대 65536바이트 |

- JSON 래퍼나 명령 실행 RPC가 아니다. ANSI escape와 제어 문자를 그대로 전달한다.
- 입력 한 메시지는 UTF-8 인코딩 후 최대 65536바이트다. 초과하면 1009로 종료한다.
  `python -m dom0_manager`는 Uvicorn 수신 상한도 65536바이트, 대기 큐도 4로 설정한다.
  별도 Uvicorn CLI로 기동한다면 `--ws-max-size 65536 --ws-max-queue 4`를 지정한다.
- 출력 조각이 UTF-8 문자/ANSI 시퀀스 중간에서 나뉠 수 있다. xterm.js에 Uint8Array를 전달하거나
  상태를 유지하는 스트리밍 디코더를 사용해야 한다.
- 연결 직후 Enter(`\r`)를 전송하면 대기 중인 로그인 프롬프트를 확인할 수 있다.
- 서버는 게스트 자동 로그인을 하지 않고, 입력·출력·게스트 암호를 로그에 저장하지 않는다.
- 출력이 없는 콘솔은 연결을 유지한다. 조용한 Zephyr 콘솔만으로 장애로 판정하지 않는다.

## VM 선택과 동시 접속

인증 뒤 UUID로 기존 VmService를 조회해 현재 domid를 확인한다. dom0, 없는 게스트,
정지/종료 중인 VM은 거부한다. PTY 실행 직후 한 번 더 조회해 인스턴스 변경을 확인한다.
연결 중에도 2초마다 UUID가 같은 domid를 가리키는지 확인하며, VM 소멸/변경 시 닫는다.
자동 재접속으로 다른 인스턴스에 입력을 전달하지 않는다. 확인 사이의 Xen 생명주기 경쟁을
완전히 원자적으로 잠그는 계약은 아니며, 제어 계층의 VM 종료를 콘솔 락으로 막지 않는다.

같은 VM은 콘솔 연결 한 개만 허용한다. 서로 다른 VM은 동시에 접속할 수 있다.
`XENOVA_CONSOLE_LOCK_DIR`(기본 `/run/xenova/console-locks`)의 UUID별 `flock`으로
같은 dom0의 여러 worker 사이에서도 점유를 공유한다. 모든 worker는 같은 경로를 사용해야 한다.
잠금 파일은 inode 경쟁을 피하려고 삭제하지 않으며, FD를 닫거나 프로세스가 끝나면 점유가 해제된다.
별도로 실행한 수동 `xl console`은 이 점유에 참여하지 않으므로 동시 사용을 피한다.

## 종료

인증에 성공해 upgrade한 뒤에는 아래 코드를 사용한다.

| 코드 | 의미 |
| --- | --- |
| 1000 | 클라이언트 종료 또는 정상 콘솔 detach/종료 |
| 1003 | 지원하지 않는 입력 형식 |
| 1009 | 입력 메시지 상한 초과 |
| 1011 | PTY 실행·입출력·조회 실패, 쓰기/출력 전송 timeout, 콘솔 비정상 종료 |
| 4400 | 잘못된 UUID 또는 dom0 접근 |
| 4404 | VM 없음 또는 연결 중 VM 소멸 |
| 4409 | 이미 점유 중, 사용할 수 없는 상태 또는 VM domid 변경 |

WebSocket 단절, 콘솔 EOF, 오류 중 하나가 발생하면 양방향 전달과 감시 작업을 취소하고
ConsoleSession을 닫는다. Ctrl+] detach 후 필요하면 콘솔 프로세스에 TERM/KILL을 적용한다.
VM shutdown/destroy/reboot는 호출하지 않는다. 출력 전송은 5초 안에 완료되지 않으면 종료한다.

## 검증

```sh
cd platform/services/dom0-manager
python -m pip install -e '.[test]'
python -B -m pytest
```

실제 실행 중인 개발용 Debian 콘솔 시험(토큰/게스트 암호는 대화형 입력):

```sh
python -B tests/probe_websocket_live.py --execute \
  --base-url ws://127.0.0.1:8081 --vm-id <debian-vm-uuid>
```

이 probe는 Debian 호스트명 `debian-dev`, 개발용 root 로그인 콘솔을 전제로 한다.
미인증 거부, 중복 거부, 실제 명령 왕복, 종료 후 재접속, 없는 VM 거부를 확인한다.
QEMU 성공은 라즈베리파이 실보드 검증이나 Zephyr 입력 기능 검증을 대체하지 않는다.
