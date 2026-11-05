# 251 새 이미지·VM 배포 후 외부 웹 콘솔 실보드 검증

- 날짜: 2026-11-05
- 저장소 기준: `origin/platform` `64bf4e5`
- 작업 브랜치: `feat/platform/S15P31E110-251-post-update-verification`
- 판정: **VM 3대의 외부 웹 콘솔 출력·해제·수동 재접속 통과**.
  Jira 상태는 변경하지 않았다. 이 결과는 253의 앱 상태 수신/구동 준비나 255·257의 HDMI/원격 영상 수락을 대신하지 않는다.

## 읽기 전용 조회

외부 Spring 컨테이너 `xenova-backend:23`에서 기존 보드 주소·서비스 토큰을 그대로
사용했다. 토큰은 프로세스 메모리와 curl 표준입력에서만 사용하고 파일/argv/출력에 넣지 않았다.
등록 조회와 실행 조회는 모두 HTTP 200이었다.

| 등록 이름 | UUID | 등록 템플릿 버전 | 실행 조회 |
|---|---|---|---|
| Zephyr 주행 A | de113a23-d80e-4333-94b8-788babe0e41e | zephyr-drive-lattice-lab-arm64 1.0.8 | running |
| Zephyr 주행 B | 5002a22d-63d6-4f7a-9106-193b14499898 | zephyr-drive-lattice-lab-arm64 1.0.8 | running |
| Debian Interaction | 17cad3c4-b3cd-4b29-8740-e679d0eaebd4 | debian-13-arm64 1.0.5 | running |

템플릿 등록 버전은 실제 실행 중 바이너리 해시/앱 보고의 검증과 구분한다.
이전 VM UUID로 시험하지 않았다. 외부 웹에도 새 세 VM이 표시됐다.

## 초기 연결 시험 — 활성화 전

- 로그인된 외부 관리 콘솔에서 Zephyr A와 B의 연결 버튼을 각각 한 번 눌렀다.
  두 번 모두 연결 중 이후 연결 오류로 바뀌었고 새 터미널 출력은 없었다.
- 두 Zephyr에 대한 Spring 컨테이너→보드 WebSocket Upgrade 요청은 각각 HTTP 403이었다.
  이 요청에서 게스트 입력은 보내지 않았다.
- 별도 외부 WSS 시험은 TLS/WebSocket 연결 후 잘못된 인증을 보내 종료 코드 4401을 확인했다.
  외부 WebSocket 업그레이드와 인증 거부 경로는 동작한다.
- HTTP 403만으로 콘솔 disabled·토큰 불일치·local-driver 조건 중 어느 것이 원인인지
  확정하지 않았다. 프론트 렌더링 실패나 WebSocket 패키지 누락으로 단정하지 않는다.
- 실행 로그 패널은 dom0 수집 연결됨과 실제 Manager/Recovery Worker 기록을 표시했다.
  이것을 Zephyr 터미널 출력 성공 또는 전체 시스템 로그 수집 완료로 쓰지 않는다.
- 조회 일시정지 checkbox는 시험 중 켰다가 초기 unchecked 상태로 돌렸다.
  시험 종료 UI는 연결 오류이며 Debian에는 연결하지 않았다.

## SSH 대상 확인 — 초기 중단 후 사용자 확인 완료

사용자가 새 이미지 배포 완료를 알려 주었다. 기존 보드 SSH 공개키 기록과 현재 키가 달라
비밀번호 인증 전에 중단했다. 새 ECDSA 공개키 지문은 다음과 같다.

`SHA256:zpMVRaxwhIKiK/8uJbxp/KBCJA3FJFd7rzvrs4kAzk8`

보드 터미널에서 `ssh-keygen -lf /etc/ssh/ssh_host_ecdsa_key.pub -E sha256`의 일치를
확인해 달라고 요청했고 사용자가 실물 보드에서 정확한 지문 일치를 확인했다.
새 지문은 별도 기록으로 보관해 검증했고 기존 신뢰 기록을 덮거나 호스트키 검사를 끄지 않았다.
이후 실제 콘솔 설정·설치 소스·프로세스/lock을 조회했다.

## 보존·제약

초기 사전 검증에서는 VM 변경·펌웨어 쓰기·API 재시작·DB 등록·네트워크/인증 설정 변경을 하지 않았다.
그 단계에서는 Zephyr 입력과 Debian 자동 Enter도 보내지 않았다.
후속 활성화·실제 연결·정리 결과는 아래 기록을 따른다. Jira 상태는 변경하지 않았다.

Windows 시험 Python에는 websockets가 없어 기존 WSL 시험 환경을 재사용했다.
첫 WSL 실행은 증거 저장 경로에 Windows 경로를 사용해 기록 단계에서 실패했고,
스크립트 위치 기반 경로로 수정한 동일 재시험에서 4401 결과를 정상 저장했다.
새 패키지 설치나 시스템 라이브러리 변경은 하지 않았다.

[API 조회·Upgrade 결과](evidence/display-251-post-update-20261105/api-preflight.json),
[외부 인증 거부 결과](evidence/display-251-post-update-20261105/invalid-auth.json)를 따른다.

## 후속 — 콘솔 활성화 및 실행 설정 수습

설치된 `dom0_manager/api/console.py` SHA-256은 저장소와 같은
`f154ebd80b993131b5d0575c7bde595838b50fc318b29c20f59817719e945fb2`였지만,
Manager 프로세스에 `XENOVA_CONSOLE_ENABLED`가 없어 기본 disabled였다.
이 설정은 인증된 Upgrade도 accept 전에 HTTP 403으로 거부한다.

이전에 사용자가 허용한 콘솔 활성화·Manager API 재시작 범위로 작업했다.
설치 소스와 기존 파일 부재·인증 API·콘솔 미점유를 검사했다.
초기 보조 호출이 SSH 응답 대기 시간을 넘었으며 재조회에서 미적용을 확인했다.
다음 안전 검사는 일시적인 로컬 API 조회 실패로 쓰기 전에 중단됐고,
동일 읽기 조회가 200으로 돌아온 것을 확인해 제한된 재확인을 추가했다.

후속 작업 중 command timeout과 rollback 경로가 겹쳐 결과가 실패로 반환됐다.
그 응답 라벨만으로 미적용이라고 판단하지 않고 다시 조회했다.
관측 결과 Manager PID는 1520→23275로 바뀌고 프로세스의 console flag는 1이었으나
추가 설정 파일은 제거돼 있었다. 따라서 성공한 원복이나 영구 활성화 완료로 기록하지 않았다.

현재 실행 값이 1이고 소스가 그대로이며 설정 경로가 비어 있는 것을 확인한 뒤,
다음 전용 파일만 다시 생성했다. 이 수습 단계에서는 API를 추가 재시작하지 않았다.

`/etc/systemd/system/xenova-dom0-manager.service.d/90-console-enabled.conf`

```ini
[Service]
Environment=XENOVA_CONSOLE_ENABLED=1
```

daemon-reload 호출 자체는 20초 timeout이 났지만 후속 조회에서 해당 drop-in이 실제
로드됐고 Manager는 같은 PID 23275·active인 것을 확인했다. 파일 내용도 정확히 일치했다.
현재 활성화와 영구 설정 준비는 확인했지만 **보드 재부팅 검증은 하지 않았다**.
설치된 제품 Python 파일·기존 서비스 토큰·MQTT/Tailscale 설정을 수정하지 않았다.

## 실제 외부 웹 연결·해제·재접속

| VM | 실제 웹 출력 | 해제·재접속 | 보드 점유 근거 |
|---|---|---|---|
| Zephyr A | Xen HVC·netfront domid=2, MQTT 연결, 자신의 UUID가 포함된 XD_READY | 성공. 재접속 뒤 새 로그가 없으면 빈 화면을 유지하는 것도 구분 | 최초 xenconsole PID 27246, 재접속 PID 29212, 해당 UUID lock 소유자 Manager 23275 |
| Zephyr B | Xen HVC·netfront domid=3, MQTT 연결, 자신의 UUID가 포함된 XD_READY | 성공 | xenconsole PID 29815·domid 3, A lock은 미점유 |
| Debian | 사용자가 login: 대기 확인 후 웹에 `debian-dev login:` 표시 | 해제 후 보드 미점유 확인, 재접속 후 login: 재표시 | 최초 PID 37260, 재접속 PID 39075·domid 1 |

Zephyr의 부팅 로그는 버퍼의 과거 출력일 수 있다. 이것만으로 현재 앱 생존·READY·
주행 출력 허가를 증명하지 않는다. XD_READY의 app_id는 `drive-controller`였다.
A/B UUID와 실행 식별자가 서로 달랐으며 다른 VM 출력을 같은 대상으로 기록하지 않았다.

A 연결을 유지한 채 별도 웹 탭의 동일 A 연결을 시도하면 잠시 연결됨 이후
`다른 콘솔이 사용 중이거나 VM 실행 상태가 변경되었습니다.` 오류로 닫혔다.
원래 A 연결은 유지됐고, 원래 연결 해제 후 프로세스 0개·lock 소유자 없음 및 새 PID 재접속을 확인했다.

Debian은 기존 strict SSH 신뢰·batch 인증으로 getty 상태를 조회하려 했지만 게스트 키가
신뢰되지 않아 중단했다. host-key 검사를 완화하지 않았다. 사용자가 login: 상태를 확인한 뒤에만
웹 시험을 진행했다. 최초에는 다른 Debian 웹 연결이 실제 점유 중이어서 두 번째 연결이 거부됐다.
기존 연결을 강제 종료하지 않고 사용자가 그 창에서 해제한 뒤 보드 미점유를 확인했다.

Debian의 정상 웹 연결 시 기존 프론트의 자동 Enter가 전송됐다.
로그인 정보나 명령은 입력하지 않았고 Zephyr 키 입력은 전혀 하지 않았다.
내부 Upgrade 시험은 세 VM 모두 게스트 입력 없이 수행했다.
Zephyr의 101 연결을 3초 후 끝낸 curl exit 28은 시험 시간 제한이며 그 자체를 연결 실패로 쓰지 않는다.
반대로 Debian의 초기 101·수신 35 bytes도 콘솔 성공 근거로 쓰지 않았다.
최종 수락은 실제 웹 출력과 점유 관측을 기준으로 한다.

## 최종 정리와 검증 경계

- 모든 시험 웹 연결을 해제했다. 최종 console 프로세스 0개, 세 UUID lock 소유자 모두 없음.
  lock 파일 자체는 제품 동시성 설계에 따라 남기고 삭제하지 않았다.
- Manager API 및 등록/실행 조회는 HTTP 200, 같은 UUID의 VM 3대 running이었다.
- 제품 콘솔 소스 해시는 그대로이며 남긴 운영 설정은 위 drop-in 한 개다.
- VM 생성·시작·정지·재부팅·펌웨어 배포·Spring DB 변경·네트워크 변경은 수행하지 않았다.
- 보조 스크립트의 전체 보존 검사 완료 응답은 얻지 못했으므로 모든 다른 서비스의 PID가
  시작 전후 동일했다는 주장은 하지 않는다. 발행한 서비스 변경 명령은 Manager용뿐이었다.
- 이번 저장소 변경은 검증 문서/정리된 증거뿐이다. 제품 코드가 바뀌지 않아 로컬 단위 테스트를
  다시 수행하지 않았다. 실제 API·브라우저·프로세스/lock 검증을 수행했다.
- 실제 게스트 로그인 후 명령 입력, 고의 VM 재시작/세대 변경, HDMI 표시·원격 전송·장시간 부하는
  이번 시험 범위가 아니다. 기존 코드 검사와 별도 통합/성능 티켓의 근거를 구분한다.

[실행·정리 증거 목록](evidence/display-251-post-update-20261105/acceptance-summary.json)을 참고한다.
