# 349·350 PC 브라우저 연결 흐름 통합 검증

기준 `platform`: `cd571b9`. 작업 브랜치:
`feat/platform/S15P31E110-349-mobile-browser-flow`.
보드·휴대폰·USB·운영 서버에 접속하지 않았다. Jira 상태는 기존 진행 중을 유지한다.

## 검증 경로

PC IAB의 실제 브라우저 폼 → 실제 `mobile_access.create_app` → 실제 `LocalFeed`
HTTP 어댑터 → loopback 합성 feed/실제 vm-screens 제품 빌드 → 브라우저 통합 화면.
브라우저의 API 응답을 route mocking으로 대체하지 않았다. 합성 상태 주입은 별도
loopback feed에만 있으며 화면에는 PC 합성 시험 표시를 추가했다.

USB 검색·장치 바인딩·root launcher는 호출하지 않고 시험용 loopback Endpoint를
직접 주입했다. **USB 실증이나 실제 보드 데이터 수신을 검증한 것이 아니다.**
제품 main/resolve/bind의 USB 전용 제한은 변경하지 않는다.
두 시험 listener는 모두 127.0.0.1에만 바인딩한다. 진단 명령은 stdin으로만 받으며
제어용 HTTP 경로를 추가하지 않는다. 시험 종료/최대 20분에 서버를 정리한다.

## 발견·수정한 실제 폼 오류

기존 연결 페이지의 `Referrer-Policy: no-referrer`에서 브라우저가 네이티브
폼 POST에 `Origin: null`을 보냈다. 올바른 번호도 출처 검사에서 403으로 거부돼
연결 화면을 통과하지 못했다. 기존 TestClient 검사는 Origin을 직접 넣어서
이 브라우저 동작을 재현하지 못했다.

`/connect` 응답에만 `Referrer-Policy: same-origin`을 적용했다. 이 정책은 다른
origin으로 referrer를 보내지 않으면서 같은 origin의 폼이 실제 Origin을 보내게 한다.
나머지 응답은 `no-referrer`를 유지한다. peer IP/Host/정확한 POST Origin/CSP/
시도 횟수/TTL/단일 세션/HttpOnly cookie 검사는 유지한다.
누락·다른 출처뿐 아니라 `Origin: null`도 계속 거부하는 회귀 검사를 남겼다.

[수정 전 Origin 분류](evidence/display-349-browser-flow-20261104/mobile-browser-origin-diagnostic-20261104.json),
[수정 후 요청 계수](evidence/display-349-browser-flow-20261104/mobile-browser-flow-fixed-20261104.json).
기록에는 연결번호·쿠키·서비스 토큰·요청 body를 저장하지 않는다.

## 실제 브라우저 관측

| 단계 | 결과 |
| --- | --- |
| 미인증 모바일 진입 | /connect로 303, upstream 데이터 요청 없음 |
| 2분 지난 번호 제출 | 401과 번호 오류·만료 안내 |
| SIGUSR1로 새 번호 발급 후 제출 | 실제 form Origin 일치, 303 후 통합 화면·상태 API 200 |
| 합성 상태 준비 중→출력 허가 | 준비 중과 자율주행/현재 구동 VM1을 구분해 표시 |
| 서버 세션 8시간 만료 | API 401, 인증 필요 안내, 주행/실행 상태 미확인으로 전환; 이전 활성 VM1 문구 없음 |
| 새 번호 발급·새로고침 | /connect로 복귀; 이전에 사용한 번호는 401 |
| 새 번호 재접속 | 새로운 인증으로 통합 화면·최신 상태 API 200 복귀 |
| 합성 feed 단절 | API 503, 인증 만료와 다른 수집기 단절 안내; 이전 정상 상태 숨김 |
| feed 복구 | 재인증 없이 새 상태로 복귀, 오류 안내 제거 |

번호 TTL **120초**, 서버 세션 TTL **28,800초**는 제품값 그대로다.
대기 시간을 줄이기 위해 주입한 서버 단조 시계만 121초/28,801초 앞당겼다.
실제 8시간 유지 시험이나 브라우저 cookie Max-Age 만료를 측정한 것은 아니다.
세션 만료 뒤에는 사용자가 새로고침하여 연결 화면으로 돌아왔다. 자동 재인증/
자동 번호 발급을 구현하거나 통과한 것으로 기록하지 않는다.

수정 후 브라우저 폼 요청 네 번 중 두 번은 만료/재사용 번호 401, 두 번은 성공 303이었다.
상태 조회 145회 200, 세션 만료 26회 401, feed 단절 13회 503을 관측했다.
upstream 상태 요청은 158회(200+503)여서 미인증 26회가 전달되지 않았음을 대조했다.
브라우저 쓰기 요청은 /connect뿐이다. 차량/VM 제어 요청이나 새 WebSocket은 없다.
dev 오류 로그 관측은 빈 목록이었다. allowlist 밖 favicon 요청의 404는 연결 실패와 구분한다.

[연결 성공](evidence/display-349-browser-flow-20261104/paired.png),
[세션 만료](evidence/display-349-browser-flow-20261104/session-expired.png),
[새 번호 재접속](evidence/display-349-browser-flow-20261104/reconnected.png),
[수집 경로 단절](evidence/display-349-browser-flow-20261104/feed-unavailable.png),
[관측 요약](evidence/display-349-browser-flow-20261104/browser-flow-summary.json).
화면은 PC 브라우저 증거이며 실물 S24의 화면 비율·가독성 합격 증거로 사용하지 않는다.

## 로컬 검사와 재현

- `npm run build -w @xenova/vm-screens`: TypeScript/제품 번들 빌드 통과.
- 인증/USB 설정/중계/배포/실행/feed/정책/Gateway/native HDMI 회귀 **181개 통과**.
  기존 Starlette/httpx deprecation warning 1건, 실패/건너뜀 없음.
- 브라우저 탭을 닫고 viewport override를 해제했으며 시험 서버 종료 후
  PC loopback 18754·18756의 접속 거부를 확인했다.

재현은 frontend에서 위 build를 수행한 뒤 router 디렉터리의 기존 Python 환경에서:

```sh
python tests/serve_mobile_browser_flow.py --port 18756 --seconds 1200 --report /tmp/mobile-browser-flow.json
```

브라우저로 출력된 loopback URL을 연다. 터미널 `code`로 현재 시험 번호를 확인한다.
`expire-code`, `expire-session`은 시험 시계만 이동한다. `rotate`는 제품 SIGUSR1
callback을 호출하며 다음 `code`로 새 번호를 확인한다. `applied`, `fail-feed`,
`recover-feed`는 합성 feed만 바꾼다. `status`는 비밀값 없는 계수를 출력하고
`stop`은 서버를 종료한다. 시험 번호는 증거 파일·URL·스크린샷에 저장하지 않는다.

실제 USB IP/장치 bind, root 설정/프로세스 잠금, ARM 이미지 빌드·부팅, 실제 S24
입력·화면·전원·물리 분리/복귀·성능, HDMI 대체 실행은 348·349·351·352의 후속 실증이다.
이 결과만으로 해당 Jira 전체를 완료 처리하지 않는다.
