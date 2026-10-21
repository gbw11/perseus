# MO-04 웹 콘솔 5단계 QEMU 인수 검증

검증일: 2026-10-21. 대상 브랜치: `feat/platform/mo-04-web-console-proxy`.
`origin/platform` 기준점은 `4752f58fd32cf58de0da50d093973db78638b8c8`이며 변경/머지/푸시하지 않았다.

## 목적과 범위

웹 브라우저에서 선택한 게스트의 텍스트 콘솔에 로그인하고, 입출력을 주고받으며,
연결 종료·인증 만료·서버 장애·VM 재시작 후 안전하게 다시 접속할 수 있는지 확인한다.

```text
Chrome / xterm.js → Vite → Spring 공개 WebSocket
  → SSH 터널 → QEMU dom0의 FastAPI → Linux PTY → Xen xenconsole → 게스트 hvc0
```

QEMU aarch64 / Xen 4.20.4-pre / Linux 6.12.107 환경에서 실행했다.
VM1 `zephyr-dev`, VM2 `zephyr-backup`, VM3 `debian-dev`를 사용했다.
Zephyr 두 VM은 현재 Starter 펌웨어이며 셸이 없다. Debian은 실제 게스트 계정으로
로그인해 `hostname`, `uname -srm`, `tty`, 고유 출력 마커를 확인했다.

FastAPI와 Spring은 임시 인스턴스로 실행했다. 장애 주입은 그 인스턴스의 자식
프로세스만 SIGKILL하고 복구했으며, 기존 시스템 서비스나 Xen 하이퍼바이저는 종료하지 않았다.
Spring의 임시 H2 DB는 재시작 동안 등록 VM 정보를 유지했다.
암호/JWT/서비스 토큰은 증거 JSON 및 Git 문서에 포함하지 않는다.

## 시나리오

아래 11개 시나리오가 수정 후 단일 전체 실행에서 모두 통과했다.

| ID | 확인 항목 |
| --- | --- |
| AUTH-01 | 잘못된 JWT, 인증 전 binary, 인증 없이 대기 시 4401 거부 |
| AUTH-02 | 잘못된 UUID·dom0·없는 VM 거부 |
| IO-01 | Debian 명령 왕복, 세 VM의 독립 콘솔, 중복 연결 4409 및 기존 연결 유지 |
| AUTH-03 | 로그아웃으로 폐기한 실제 JWT의 추가 입력 및 재접속 거부 |
| AUTH-04 | 격리 개발 키로 서명한 짧은 만료 JWT가 입력 없이도 4401 종료 |
| LIFE-01 | 브라우저 역할 클라이언트의 TCP 강제 단절 후 콘솔 정리와 재접속 |
| LIFE-02 | 12회 연결/해제 후 native console 프로세스와 서버 FD 수 확인 |
| LIFE-03 | 정리 완료 응답만 기다리고 지연/재시도 없이 12회 즉시 재접속 |
| FAULT-01 | FastAPI SIGKILL → 연결 오류 → 프로세스 정리 → 복구 후 명시적 재접속 |
| FAULT-02 | Spring SIGKILL → 연결 종료 → 프로세스 정리 → 복구 후 명시적 재접속 |
| FAULT-03 | Debian 정상 stop/start → 이전 연결 종료 → 정지 중 거부 → 같은 UUID/새 domid 재접속 |

실행 결과와 비밀 정보가 없는 증거는 [mo-04-qemu-evidence.json](mo-04-qemu-evidence.json)에 둔다.
QEMU의 부팅·정리 시간은 실보드 성능 수치로 해석하지 않는다.

Chrome에서도 FastAPI/Spring 장애의 오류 안내, 자동 재접속 없음, 명시적 재접속,
복구 후 실제 게스트 명령 실행, SPA 화면 이동, 연결 중 로그아웃과 재로그인 후 재접속을
확인했다. 페이지 JavaScript 오류는 0개였다. 최종 콘솔 프로세스는 0개이며 세 게스트를 유지했다.
Debian은 최종 전체 시험에서 domid 12 → 13으로 변경됐고 UUID는 유지됐다.
두 Zephyr 게스트는 domid 7, 8을 유지했다.

## 발견한 문제와 수정

실제 Chrome에서 연결 해제 직후 다시 연결하면 이전 FastAPI 세션의 UUID 락이
아직 정리 중인 상태에서 새 연결이 들어가 4409를 받았다. 연결 해제 UI가
WebSocket 종료 요청만 보내고 바로 재접속 버튼을 활성화한 것이 원인이었다.

명시적 text 제어 프레임 `{"type":"disconnect"}`를 추가했다. FastAPI가 PTY와
UUID 락을 먼저 정리하고 정상 종료를 보내면 Spring이 브라우저 연결을 닫는다.
브라우저는 그동안 `연결 정리 중`으로 표시하고 입력과 새 연결을 막는다.
연결 설정 도중 취소도 처리하며, Spring의 정리 대기에는 8초 상한을 둔다.
VM 종료 명령은 호출하지 않는다. 상세 계약은
[공개 콘솔](../contracts/browser-console-v1.md), [내부 콘솔](../contracts/console-websocket-v1.md)을 따른다.

프론트에는 준비 중 취소/늦은 connected 응답/조기 재접속 차단 회귀 테스트를,
Spring에는 내부 정리 응답까지 브라우저 종료를 기다리는 통합 테스트를,
FastAPI에는 종료 응답 직후 반복 연결로 락 해제를 확인하는 테스트를 추가했다.

## 재현

Linux 테스트:

```sh
cd platform/services/dom0-manager
python -B -m pytest
```

격리 QEMU 실험실의 공개 Spring 주소에 대해 아래 probe를 실행한다.
표준 입력 한 줄에 `username`, `password`, `guest_password`를 가진 JSON을 전달한다.
실제 운영 키를 사용하지 않는 격리 환경에서만 선택적으로 `lab_jwt_secret`을 지정하면
짧은 만료의 실제 서명 JWT 시험을 추가한다. 자격 증명을 인수/로그/셸 이력에 넣지 않는다.

```sh
python -B tests/probe_console_acceptance.py --execute \
  --base-url http://127.0.0.1:18080 \
  --vm-id <debian-dev-uuid> \
  --ssh-config <isolated-lab-ssh-config> --ssh-alias <qemu-dom0> \
  --output <local-result.json>
```

`--allow-vm-restart`를 명시해야 지정한 Debian의 stop/start 시험을 실행한다.
probe는 이름과 UUID가 일치하는지 먼저 확인하며, 정지한 게스트는 finally에서 복구한다.
`--only LIFE-03`처럼 특정 항목만 재실행할 수 있다.
`--lab-control-directory`는 격리 supervisor의 파일 RPC가 있을 때만 지정한다.
supervisor는 `step5-lab-command.json`의 `op`, `request_id`를 읽고
`step5-lab-response.json`에 같은 ID와 `status: OK`를 반환한다.
지원 동작은 자체 FastAPI의 `upstream-crash/start`와 자체 Spring의 `backend-crash/start`다.
일반 서버에 SIGKILL을 보내는 기능은 probe에 없다.

## 회귀 검증과 남은 범위

- dom0-manager 테스트 156개 통과. 기존 Starlette TestClient deprecation 경고 1개.
- 프론트 테스트 40개, 타입 검사, 변경 파일 ESLint, 관리자 콘솔 빌드 통과.
- Spring 콘솔 WebSocket 통합 테스트 8개 및 패키징 통과.
- 4단계에서 실행한 전체 Spring 테스트는 135개 중 133개 통과했다.
  `VmServiceTest`의 기존 Mockito stubbing 오류 2개를 기준 커밋 `a29618b`의
  격리 복사본에서도 재현했다. 이번 콘솔 수정과 무관하며 전체 테스트 통과로 표기하지 않는다.

라즈베리파이 실보드, 운영 배포의 HTTPS/WSS, Zephyr 양방향 명령 처리는 아직 검증하지 않았다.
VM1/VM2 주행 제어·미러링·자동 failover, Jetson 인식 장애에 따른 수동 전환은
MO-04 콘솔 경로의 검증 범위에 포함하지 않는다.
