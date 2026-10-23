# 외부 프론트 연동 인계 — 표시 상태와 웹 콘솔

253번은 domU 보고를 dom0에서 수집하는 작업이고, [251번](https://ssafy.atlassian.net/browse/S15P31E110-251)은 그 결과를 외부 서버·프론트에 연결하는 작업이다. HDMI 렌더링·출력·원격 화면 전송을 모두 끝내야 외부 웹을 연결할 수 있는 구조가 아니다. 이 문서는 연결 규격이며 실제 서버 배포 완료를 뜻하지 않는다.

## 프론트가 연결할 경로

| 대상 | Spring 공개 경로 | 인증과 응답 |
| --- | --- | --- |
| VM·앱·OS 근거 상태 조회 | `GET /api/v1/display/state` | 사용자 JWT Bearer. 응답 본문이 전체 상태 |
| VM·앱·OS 근거 상태 구독 | `WS /ws/v1/display/state` | 첫 프레임 `{"type":"auth","access_token":"<사용자 JWT>"}`. `authenticated` 이후 `{"type":"display.state","state":{...}}` |
| 기존 대화형 웹 콘솔 | `WS /ws/v1/vms/{vm_id}/console` | 기존 MO-04 인증·바이너리 입출력 규격 유지. 상태 구독 소켓과 별개 |

두 display 경로의 `vms`는 UUID·역할·Xen 상태·앱 상태·기대 OS/관측 근거·소스별 수신 상태를 포함한다. VM1/VM2는 둘 다 Zephyr이며 `primary_drive`/`backup_drive` 역할로 구분한다. `manual`/`autonomous` app_id만으로 현재 주행 모드·활성 VM을 판단하지 않는다. Debian heartbeat만 수신하면 OS inventory는 미수신 그대로다.

상태 WS는 읽기 전용이므로 키보드 입력이나 주행 명령을 보내면 1008로 닫힌다. 콘솔 입출력이 필요하면 기존 콘솔 소켓을 별도로 연결한다. 수집기는 콘솔을 자동으로 열거나 콘솔 텍스트를 읽어 상태를 추론하지 않는다.

## 서버 담당자가 준비할 설정

- dom0 수집기: 실제 UUID·app_id 매핑, 기존 MQTT 구독 계정/ACL, Xen 조회 권한과 내부 서비스 토큰. [수집기 실행 안내](../../platform/services/display/router/README.md)를 따른다.
- Spring: `DISPLAY_FEED_ENABLED=true`, `DISPLAY_FEED_BASE_URL=<Spring에서 접근 가능한 dom0 수집기 주소>`, `DISPLAY_FEED_SERVICE_TOKEN=<dom0의 XENOVA_DISPLAY_TOKEN과 같은 비밀값>`.
- Spring과 dom0가 다른 호스트라면 `127.0.0.1`을 그대로 쓰지 않는다. 승인된 내부망/터널 경로와 수집기 listen 주소를 정한다. 브라우저에는 dom0 서비스 토큰을 제공하지 않는다.
- 외부 웹 서버/프록시: 공개 Spring API 주소, WS 업그레이드, HTTPS 환경의 WSS, 허용 Origin을 실제 배포 환경에 맞춘다. 현재 loopback 시험은 이 배포망·TLS·리버스 프록시를 검증하지 않는다.
- 기존 콘솔은 dom0-manager의 주소·서비스 토큰·콘솔 활성화 설정과 VM 목록 연결도 별도로 필요하다. display feed 주소와 dom0-manager 주소를 혼동하지 않는다.

## 프론트 수신 예시

```js
const response = await fetch(`${apiOrigin}/api/v1/display/state`, {
  headers: { Authorization: `Bearer ${accessToken}` },
});
if (!response.ok) throw new Error(`표시 상태 조회 실패: ${response.status}`);
const initialState = await response.json();

const socket = new WebSocket(`${wsOrigin}/ws/v1/display/state`);
socket.onopen = () => socket.send(JSON.stringify({
  type: 'auth', access_token: accessToken,
}));
socket.onmessage = ({ data }) => {
  const frame = JSON.parse(data);
  if (frame.type === 'display.state') {
    // frame.state 전체를 사용. source_epoch·sequence로 이전 수신 값 혼입을 방지한다.
    // vms[].sources의 상태를 확인한 뒤 해당 값의 정상/미확인 표시를 결정한다.
    consumeDisplayState(frame.state);
  } else if (frame.type === 'display.unavailable') {
    markDisplayDisconnected(); // 마지막 값을 실시간 정상 상태로 남기지 않는다.
  }
};
socket.onclose = () => markDisplayDisconnected();
socket.onerror = () => markDisplayDisconnected();
```

`apiOrigin`·`wsOrigin`·토큰·화면 갱신 함수는 프론트 환경에서 제공한다. 예시는 자동 재접속/토큰 갱신을 포함하지 않는다. REST의 `initialState`와 WS를 함께 사용할 때도 같은 수신 순서 검사를 적용한다. 같은 `source_epoch`에서는 더 큰 `sequence`만 적용하고, 재접속 시 이전 소켓을 폐기해 과거 연결의 늦은 프레임을 섞지 않는다. 수집기 재시작으로 epoch가 바뀌면 새 연결의 전체 스냅샷을 기준으로 다시 시작한다.

REST 503 또는 WS `display.unavailable`은 수집 경로 문제이며 VM 정지를 의미하지 않는다. 연결 복구 시 현재 전체 스냅샷이 다시 온다. WS 4401은 인증 실패/만료로 처리하고 토큰을 갱신한 뒤 새 소켓에서 인증한다. 프론트의 페이지·메뉴 라우팅과 실제 서버 배포 절차는 각 담당과 251번에서 연결한다.

HDMI 두 화면은 dom0의 `/api/v1/display/presentation`을 소비한다. 외부 프론트가 HDMI 화면을 띄우기 위해 이 내부 화면 세션이나 선택/ACK API를 호출할 필요는 없다. 외부 공개 상태와 로컬 표시의 연결 증거는 [253번 5단계](../verification/display-253-step5.md)에 기록한다.
