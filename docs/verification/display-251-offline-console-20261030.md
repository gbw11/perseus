# 251 보드 없는 웹 콘솔 검증 — 2026-10-30

상태: 브라우저 측 구현·로컬 검증 완료. 외부 Spring부터 실제 VM 콘솔까지의
최종 검증을 대신하지 않으며, 251 전체 완료로 처리하지 않는다.

## 기준과 변경 범위

- 기준: 최신 `origin/platform`의 `2bcfb31`.
- 브랜치: `feat/platform/S15P31E110-251-console-offline-validation`.
- `git fetch origin platform` 후 깨끗한 기존 251 작업 폴더에서
  `git switch -c feat/platform/S15P31E110-251-console-offline-validation origin/platform` 실행.
  `git branch --unset-upstream`으로 platform을 기본 추적 대상으로 두지 않았다.
- platform 자체·외부 서버·보드·VM·펌웨어·MQTT·서비스 토큰은 변경하지 않았다.
- 위 작업 브랜치에서 커밋·푸시하여 platform 대상 일반 MR로 제출한다. 실제 병합·자동 병합은 하지 않는다.

## 발견한 문제와 보완

서버가 응답하지 않거나 네트워크가 준비 단계에서 멈추면 기존 브라우저는
`연결 중` 또는 `연결 정리 중`에 무기한 남을 수 있었다.

1. 토큰 갱신부터 업스트림 `connected` 응답까지 총 15초로 제한한다.
   갱신 실패·만료된 세션·소켓 생성 실패도 오류로 표시한다.
2. 사용자가 취소·연결 해제하면 10초까지 서버 정리 응답을 기다린다.
   응답이 없으면 브라우저 연결을 닫되 **서버 점유 해제는 확인되지 않았다**고 안내한다.
   서버의 실제 정리 8초 제한이나 VM lease 정책은 변경하지 않았다.
3. 종료·시간 초과·화면 이탈 후 도착하는 이전 연결의 이벤트가 화면·입력을
   다시 활성화하지 못하도록 연결 세대와 대기 타이머를 정리한다.
4. 정상 연결은 새 출력이 없어도 유지한다. 자동 재접속과 입력 재전송은 하지 않는다.

## 로컬 시험

### 전송 모듈 — 모의 소켓·가상 시계

- WebSocket 열기·인증 응답·토큰 갱신 대기 초과와 늦은 응답 차단.
- 수동 재시도, 정리 응답 대기 중 재접속 금지, 정리 시간 초과 안내.
- 인증 실패·VM 없음·중복 점유·연결 실패 종료 코드 처리.
- 정상 연결에서 출력이 없는 상황, 다른 VM의 준비 응답 거부.
- 화면 이탈 시 타이머 정리, 오래된 입력 미전송·미재생.

### 화면 — 실제 ConsolePanel·ConsoleTransport + MSW 모의 WebSocket 서버

실제 컴포넌트의 버튼·상태 안내와 전송 코드를 사용한다. jsdom에서 캔버스를
제공하지 않으므로 xterm 그리기만 대체하고 수신 바이트가 xterm.write로 전달됨을 검사한다.
MSW가 WebSocket API를 가로채므로 실제 TCP·Spring·보드에는 연결하지 않는다.

- 준비 확인 후 입력 활성화와 모의 출력 바이트 전달.
- 연결 해제 후 수동 재접속, 이전 입력 재전송 없음.
- 4401·4404·4409·1011 오류 안내와 수동 연결 버튼 복구.
- 서버 정리 응답 전 연결 버튼 비활성 유지.
- 조용한 Zephyr 콘솔의 연결 성공과 출력 수신 성공 구분.
- 두 번째 연결 거절 시 첫 번째 연결·출력 context 유지.

모의 입력과 토큰은 시험 문자열이며 실제 게스트나 서비스 인증정보를 사용하지 않는다.
프로덕션용 장애 주입 주소나 테스트 메뉴를 추가하지 않았다.

재실행 명령 (`frontend` 폴더):

```sh
npm test
npm run typecheck
npm run build -w @xenova/admin-console
npx eslint apps/admin-console/src/api/console.ts apps/admin-console/src/api/console.test.ts apps/admin-console/src/console/ConsolePanel.test.tsx
```

결과: 전체 프론트 테스트 168개, 전체 워크스페이스 타입 검사, 관리 콘솔 빌드,
수정한 TypeScript 파일 lint 통과. 빌드의 기존 500kB 초과 청크 경고는 남아 있다.

## 보드를 사용할 때 남은 확인

1. 외부 웹에서 기존 VM UUID를 선택하고 실제 콘솔 출력 수신 확인.
2. 외부 Spring·dom0의 실제 PTY/점유가 해제된 뒤 재접속되는지 확인.
3. 연결 단절·지연에서 표시되는 오류와 실제 서버 정리 시점을 대조.
4. Debian 연결 시 기존 자동 Enter 동작은 그대로이므로 먼저 게스트 콘솔 상태를 확인.
5. 모의 중복 연결 결과와 실제 서버의 단일 점유 정책을 구분하여 검증.

기존 외부 서버 시험 기록은
[2026-10-29 외부 웹 콘솔 검증](display-251-external-console-20261029.md)을 참조한다.
