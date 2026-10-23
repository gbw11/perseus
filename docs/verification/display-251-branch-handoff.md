# 251번 작업 브랜치와 다음 연결 전 확인

2026-10-23 · `feat/platform/S15P31E110-251-external-web-relay`.

## 브랜치 분리

- 기준: 새로 fetch한 `origin/platform`의 `0aac7ce`.
- 253번은 `bf472e6`까지 커밋·푸시했고 [일반 MR !153](https://lab.ssafy.com/s15-final/S15P31E110/-/merge_requests/153)을 만들었다. 자동 병합은 요청하지 않았다.
- 251번은 platform 기준으로 새로 만들었으며 253번 브랜치에서 분기하거나 해당 코드를 cherry-pick하지 않았다. 253번 수집 보완·실행 증거는 MR의 선행 의존성으로 관리한다.
- 251번 준비 문서와 보드 터널/SSH 정책 적용 증거를 이 브랜치에 분리했다. 기존 다른 작업의 untracked MO-04 문서·QEMU 파일은 포함하지 않는다.

## 현재 연결 상태

Windows PC `http://127.0.0.1:18081`→임시 SSH 터널→보드 내부 dom0-manager 8081 경로를 검증했다. OpenAPI 200·토큰 없는 API 401이며 인증된 조회·PC Spring 연결은 아직 미검증이다. 보드 표시 수집기 기본 8082는 아직 미기동 관측이다. 상세 기록과 당시 결과는 [로컬 준비](display-251-local-preflight.md)를 따른다. 터널은 임시 프로세스이므로 다음 단계 시작 시 생존 상태를 다시 확인한다.

## 최신 platform에서 추가 확인한 내용

1. [정식 서비스 토큰 준비 모듈](../../platform/services/dom0-manager/dom0_manager/service_token.py)과 [systemd 서비스](../../platform/yocto/meta-xenova/recipes-core/xenova-dom0-manager/files/xenova-dom0-manager.service)가 있다. 문서상 `/etc/xenova/dom0-manager-token.env`의 root 전용 0600 파일을 사용하며, 명시적인 `dom0-manager.env` 값이 우선한다. 이번 Git 작업에서 보드의 두 파일이나 토큰 값을 읽지 않았다. 파일을 안다고 현재 서비스가 그 값을 사용한다고 단정하지 않는다.
2. [새 표시 라우팅 API](../../backend/src/main/java/com/xenova/backend/display/api/DisplayController.java)는 `/api/v1/displays`·route 경로를, [표시 서비스 WS 설정](../../backend/src/main/java/com/xenova/backend/display/websocket/DisplayWebSocketConfig.java)은 `/ws/v1/displays`·`/ws/v1/display-service`를 제공한다. 기존 상태 feed의 `/api/v1/display/state`·`/ws/v1/display/state` 및 대화형 콘솔과 의미가 다르다. 연결 전 담당 계약을 구분하며 외부 페이지 라우팅을 본인 구현으로 추가하지 않는다.
3. 기존 `backend.display.DisplayWebSocketConfig`와 새 `backend.display.websocket.DisplayWebSocketConfig`가 모두 `@Configuration`으로 존재한다. PC Spring 시작 전 동명 bean 충돌 여부를 기동 시험에서 확인해야 한다. 이번 문서 분리 작업에서는 기동 시험을 실행하거나 제품 코드를 수정하지 않았다.

## 다음 실행 순서

- PC Spring 실행 위치를 정하고 그 위치에서 터널 주소에 접근하는지 확인한다. Windows host의 loopback이 WSL/Docker에서도 같다고 가정하지 않는다.
- 새 기준의 Spring 기동을 확인하고, 정식 서비스 토큰 관리 경로·실제 적용 설정을 확인해 비밀값을 로그·Git·명령행에 노출하지 않는 방식으로 주입한다. SSH 비밀번호를 API 토큰으로 대신 사용하지 않는다.
- 인증된 health/VM 조회 후 실제 UUID와 Spring 등록 목록의 대응을 확인한다. 기존 VM을 등록하려고 새 VM 생성 API를 임의 실행하지 않는다.
- 상태 수집기 실행·수신과 콘솔 활성화는 실제 설치 상태·팀 계약을 확인한 뒤 진행한다. 외부 서버 연결은 사용자가 정한 대로 이후 단계로 미룬다.

253번에서 통과한 회귀·연결 시험을 최신 platform의 통과 결과로 옮겨 적지 않는다. 이 251번 커밋은 준비 문서·기존 관측 증거이며 실제 Spring/프론트/배포 구현 완료를 의미하지 않는다.
