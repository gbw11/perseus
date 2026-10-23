# 251번 — 외부 웹 중계 연결 계획

2026-10-23 · 최초 1단계 소스·배포 전제 조사는 `feat/platform/S15P31E110-253-display-source-collection`에서 수행했다. 이후 사용자 요청으로 253번을 [MR !153](https://lab.ssafy.com/s15-final/S15P31E110/-/merge_requests/153)에 올리고, 최신 `origin/platform`의 `0aac7ce`에서 **`feat/platform/S15P31E110-251-external-web-relay`**를 새로 분리했다. 현재 문서는 이 251번 브랜치의 준비 기록이다. platform에는 직접 커밋·푸시·병합하지 않았다. 최초 조사와 새 기준 사이의 코드 차이는 [브랜치 인계](../verification/display-251-branch-handoff.md)를 따른다.

## 목적과 담당 범위

프론트 담당자가 만든 외부 웹에 실제 상태와 기존 대화형 콘솔을 연결한다. 외부 페이지·메뉴·대시보드 라우팅은 프론트 담당 영역이다. 본인은 dom0→Spring API/WebSocket과 기존 MO-04 연결·인증·단절 처리·인계를 담당한다. HDMI의 표시 선택·로컬 출력·원격 영상 전송은 별도 경로이며 외부 웹 연결의 선행 완료 조건이 아니다.

```text
게스트 보고 → dom0 표시 수집기 ─ 내부 HTTP ─┐
                                          ├ Spring ─ 공개 REST/WS ─ 외부 웹
게스트 콘솔 → dom0-manager ─ 내부 WS ──────┘
```

상태 수집기의 내부 WS도 존재하지만 현재 Spring display 중계는 내부 HTTP 전체 상태를 읽어 공개 REST/WS로 제공한다. 모든 구간이 WebSocket인 구조는 아니다. 수집기와 dom0-manager는 서로 다른 서비스·주소·토큰 설정을 사용한다.

## 현재 재사용할 구현

| 구간 | 확인한 소스 | 확인 결과 |
| --- | --- | --- |
| 상태 공개 REST | [DisplayStateController](../../backend/src/main/java/com/xenova/backend/display/DisplayStateController.java) | `GET /api/v1/display/state`, 기존 사용자 JWT 요구 |
| 상태 내부 조회 | [DisplayFeedClient](../../backend/src/main/java/com/xenova/backend/display/DisplayFeedClient.java) | 설정한 수집기에 서비스 Bearer를 붙여 조회, 비활성/실패 시 503 |
| 상태 공개 WS | [DisplayStateWebSocket](../../backend/src/main/java/com/xenova/backend/display/DisplayStateWebSocket.java) | 첫 auth 프레임, 약 1초 주기 전체 상태, 실패 시 `display.unavailable`, 입력 거부 |
| 기존 콘솔 | [ConsoleWebSocketHandler](../../backend/src/main/java/com/xenova/backend/console/ConsoleWebSocketHandler.java) | 공개 `/ws/v1/vms/{vm_id}/console`→내부 `/api/v1/vms/{vm_id}/console`, 첫 JWT·바이너리 입출력 |
| 로컬 터미널 UI | [ConsoleTransport](../../frontend/apps/admin-console/src/api/console.ts) | 기존 로그인·재발급·연결/정리·바이너리 처리 재사용 가능 |
| 프론트 개발 프록시 | [vite.config.ts](../../frontend/apps/admin-console/vite.config.ts) | `/api`·`/ws`를 Spring으로 전달. 개발 프록시는 production 배포 프록시가 아님 |
| VM 조회·등록 | [VmService](../../backend/src/main/java/com/xenova/backend/vm/application/VmService.java) | Spring DB에 등록된 VM만 관리 대상으로 사용. 플랫폼에서 따로 만든 VM은 자동 편입되지 않음 |

최초 조사 당시 admin-console 소스에서 display 상태 경로의 수신 연결은 찾지 못했다. 이것은 프론트 담당자의 다른 브랜치·미푸시 작업에 해당 기능이 없다는 뜻은 아니다. 연결 규격은 253번에 보관한 [프론트 인계](https://lab.ssafy.com/s15-final/S15P31E110/-/blob/bf472e6/docs/contracts/display-external-integration.md), 콘솔의 binary/종료 규격은 [기존 계약](../contracts/browser-console-v1.md)을 따른다. 최신 platform에는 별도의 `/api/v1/displays`·표시 서비스 WS 구현이 추가돼 있으므로 실제 연결 전 수신·라우팅 계약을 구분해 재확인한다.

## 연결 방식 제안 — 아직 실행·확정하지 않음

1. **첫 확인은 PC·QEMU 안에서**: PC의 Spring이 SSH 로컬 포워딩을 통해 dom0의 표시 수집기와 dom0-manager에 접근한다. 프론트 개발 서버의 `/api`·`/ws` 프록시는 이 Spring을 향한다. 먼저 외부 서버망과 무관하게 두 내부 경로가 동작하는지 확인한다.
2. **외부 서버 시연은 내부망 또는 터널로 연결**: 팀의 VPN/사설망이 있다면 우선 재사용한다. 없다면 승인된 PC→서버 SSH 역방향 포워딩으로 서버 loopback에 내부 두 서비스를 연결하는 안을 제안한다. 그 뒤 서버의 Spring이 이 두 주소를 사용한다. 실제 SSH 계정·포워딩 허용·포트 충돌·접근 범위 확인 전 실행하지 않는다.
3. **외부 웹은 같은 origin의 프록시 구성 우선 검토**: 웹·`/api`·`/ws`를 같은 도메인에서 제공하면 기존 쿠키·상대 경로를 재사용하기 쉽다. HTTPS/WSS·WS upgrade·허용 Origin·프록시 timeout은 인프라 담당과 맞춘다. 별도 origin을 선택하면 REST CORS·refresh 쿠키 조건을 별도로 검증해야 한다. 현재 SecurityConfig에서 명시적 REST CORS 구성을 확인하지 못했으며 `WEBSOCKET_ALLOWED_ORIGINS` 설정만으로 REST CORS까지 해결됐다고 가정하지 않는다.

Spring을 컨테이너에서 실행한다면 컨테이너의 `127.0.0.1`은 호스트/WSL의 loopback과 다르다. 터널 끝점과 Spring의 실행 위치를 확인한 뒤 실제 접근 주소를 정한다. 이 단계에서 터널·방화벽·컨테이너 네트워크·서버 설정은 변경하지 않는다.

## 2단계에 필요한 설정과 확인

| 대상 | 필요한 항목 | 현재 상태 |
| --- | --- | --- |
| 외부 서버 | 준비 여부, 공개 주소, Spring 실행 위치(호스트/컨테이너), 인프라 담당 | 사용자가 실행 중이라고 답변. 관리 화면 `http://k15e110.p.ssafy.io/`, VM 화면 `/screens/` 제공. 실행 위치·인프라 담당·실제 배포 구성은 미확인 |
| 보드↔서버 | 기존 VPN/터널, 외부 서버 SSH 주소·사용자·설정 위치, 포워딩 가능 여부 | 제공된 `192.168.30.96`은 Raspberry Pi Xen dom0로 읽기 전용 접속 확인. 외부 웹 서버 접속 정보나 기존 VPN/터널 확인을 대신하지 않음. 비밀번호/토큰은 기록하지 않음 |
| dom0 표시 수집기 | 실제 bindings, MQTT 구독 경로/계정, `XENOVA_DISPLAY_TOKEN`, listen 주소 | 253번 임시 실행/검증 증거 있음. 상시 배포 완료는 아님 |
| Spring 표시 중계 | `DISPLAY_FEED_ENABLED`, `DISPLAY_FEED_BASE_URL`, `DISPLAY_FEED_SERVICE_TOKEN` | 코드 존재, 실제 배포 값 미확정 |
| dom0-manager/콘솔 | 실제 드라이버·VM 조회, `XENOVA_CONSOLE_ENABLED`, `XENOVA_SERVICE_TOKEN`, listen 주소 | 기존 구현 재사용. 외부 연결을 위해 실제 기동 상태 확인 필요 |
| Spring 콘솔·VM | `DOM0_MANAGER_BASE_URL`, `DOM0_MANAGER_SERVICE_TOKEN`, 같은 UUID의 Spring VM 등록/DB | 기존 게스트를 새 DB에 자동 등록하지 않음. 등록·이관 방식은 별도 확인 |
| 외부 웹 | API/WS origin, 실제 Origin, mock 비활성 여부, 표시 상태 수신 코드, 로그인/refresh | 프론트 담당과 연결. 현재 개발 예제 `VITE_MOCK=false` |

표시 상태는 bindings의 UUID를 기반으로 하며 콘솔은 Spring DB의 등록도 확인한다. 따라서 표시 목록의 VM이 콘솔 관리 목록과 일치하는지 확인해야 한다. 현재 VM API에서 기존 외부 도메인을 단순 편입하는 공개 import/adopt 경로는 찾지 못했다. 이를 해결하려고 `POST /api/v1/vms`를 호출하면 새 VM 생성 경로를 실행할 수 있으므로 기존 게스트 등록 수단으로 임의 사용하지 않는다. 운영 DB 수정·VM 생성/재시작도 이번 범위에서 수행하지 않는다.

## 다섯 단계와 증거

| 단계 | 작업 | 완료 증거 |
| --- | --- | --- |
| 1 | 기존 코드·현재 환경 조사, 연결 경로·설정 목록 정리 | 이 문서와 [1단계 조사](../verification/display-251-step1.md). 소스 조사 완료, 서버 선택은 답변 대기 |
| 2 | 선택한 환경에서 Spring↔dom0 두 서비스 주소·인증 설정 | 내부 조회/연결 확인, 실제 값은 비밀정보 제외 기록. VM 등록 공백 해결 방법 확인 |
| 3 | 사용자 JWT 외부 REST·상태 WS·기존 콘솔 검증 | 실제 수신 UUID/세대, 미인증/잘못된 대상 거부, 콘솔 연결/정리 증거 |
| 4 | 프론트 담당 코드와 연결해 서버에서 확인 | 공개 URL에서 로그인·실제 상태 갱신·콘솔 확인. mock 데이터와 실제 보고 구분 |
| 5 | 단절·재접속·만료 시험, 실행/인계 정리 | 오래된 정상 표시 제거·최신 복귀, 콘솔 입력 재전송 없음, HDMI와의 독립성 확인 |

253번의 [소비 경로 시험](https://lab.ssafy.com/s15-final/S15P31E110/-/blob/bf472e6/docs/verification/display-253-step5.md)은 해당 브랜치의 loopback 연결 재사용 근거다. MR !153은 251번 시작 시 미병합 의존성이며 이 브랜치에 253번 코드를 임의로 병합하지 않았다. 실제 서버 배포·TLS/프록시·외부 프론트 연결·새 platform 기준 검증은 그 결과만으로 완료 처리하지 않는다. 게스트 링크 단절 후 자동 보고 복구 실패는 기존 담당자 의존성을 유지한다.

## 사용자 답변 및 실보드 조회 보완

사용자는 외부 서버에서 Spring 또는 웹이 이미 실행 중이라고 답했다. 제공한 두 URL은 접속 대상 주소로 기록했으며 이번 SSH 조사에서 웹 배포 내용·연결 상태를 조회한 것은 아니다.

추가 제공된 SSH 대상 `root@192.168.30.96`에 접속한 결과 hostname `raspberrypi4-64`, aarch64, Poky 6.0.4, `/proc/xen/capabilities=control_d`를 확인했다. Xen 목록에는 Domain-0과 debian-dev·zephyr-drive-a·zephyr-drive-b가 있었다. 이는 실제 보드 dom0의 관측이며 기존 PC QEMU나 외부 웹 서버로 분류하지 않는다.

조회 시 TCP listener는 SSH 22 및 `127.0.0.1:8081`이었다. 8082/1883 listener는 관측되지 않았다. 8081은 dom0-manager의 기본 포트와 일치하지만 HTTP 인증/응답·콘솔 활성화·프로세스 설정까지 검증한 것은 아니다. 기본 포트가 닫혀 있다는 사실로 모든 수집/브로커 기능이 없다고 단정하지 않는다.

실보드 연결을 선택한다면 2단계 전에 보드의 실제 UUID·앱/Agent·설치 서비스·운영 브로커를 읽기 전용으로 확인하고, 필요한 실행/설치 범위를 별도로 정한다. 기존 QEMU용 bindings·시험 펌웨어·계정을 보드에 그대로 적용하지 않는다. 보드 변경이나 배포는 아직 수행하지 않았다.

## 진행 순서 보완: 외부 서버 연결은 나중에

사용자 요청으로 PC→실보드 내부 API 연결 준비, PC Spring 연결, 프론트 로컬 연동, 단절/복귀 시험을 먼저 진행하고 외부 서버 연결·배포를 마지막으로 미룬다. [로컬 연결 준비 결과](../verification/display-251-local-preflight.md)에서 실제 UUID·미인증 API 응답과 SSH PermitOpen 제한을 확인했다. 최초 터널 실패 후 사용자가 두 내부 API 대상 추가를 승인했고, 백업·설정 검사·SSH reload 뒤 새 연결로 Windows PC `127.0.0.1:18081`→보드 8081 터널을 검증했다. 인증된 API·PC Spring/WSL/Docker·표시 수집기 실행은 아직 미검증이다. 원래 단계표는 최초 계획이며 이후 실행 순서는 이 결정과 로컬 준비 기록을 따른다.
