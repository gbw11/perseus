# 253번 5단계: 외부 상태 중계와 HDMI용 렌더러 소비 확인

2026-10-22 · `feat/platform/S15P31E110-253-display-source-collection`.

수집 결과를 **Spring REST/WebSocket과 로컬 두 화면이 일관되게 소비하는 연결 검증**을 완료했다. 실제 loopback MQTT·제품 Python 수집기·Spring 서버·Chromium DOM을 사용했다. 게스트/Xen 관측과 시계는 합성 입력이다. 3·4단계의 실제 QEMU 게스트 증거와 별개이며, 이번 한 번의 실행이 실제 domU부터 실물 HDMI까지 이어진 시험은 아니다.

## 실행 경로와 변경 범위

```text
합성 앱·Agent → 실제 임시 MQTT → 제품 display feed
                                ├ Spring → 사용자 JWT REST / WebSocket
                                └ presentation → 전체/상세 화면 Chromium DOM → 렌더 ACK
```

[선택 실행 통합 시험](../../backend/src/test/java/com/xenova/backend/display/DisplayPipelineIntegrationTest.java)과 [격리된 Python 시험 서버](../../platform/services/display/router/tests/pipeline_server.py)를 추가했다. 기존 Spring display 중계, 상태 정규화, 표시 정책과 화면 코드를 재사용했다. 제품 코드·송신 앱·하위 통신은 변경하지 않았다. MQTT 브로커는 loopback의 임시 익명 시험 브로커이며 운영 계정/ACL 검증을 뜻하지 않는다.

시험 전용 `/test/scenario/*`, `/test/browser/*`는 이 임시 서버에만 등록되고 기존 서비스 토큰 인증을 요구한다. 제품 `create_app`에서는 서비스 토큰을 제공해도 404인 것을 확인했다. 브라우저에는 신뢰된 실행기가 쿠키 세션을 만들며 페이지에 서비스 토큰을 넣지 않는다.

## 확인 결과

| 입력/상황 | 양쪽에서 확인한 결과 |
| --- | --- |
| VM2 manual standby 보고 | VM2 UUID/백업 역할/보고 세대와 seq 유지, 앱 ready, 상세·전체 화면 `status` |
| 새 error 보고 | VM running 유지, 앱 failed, 두 화면 `app_error` |
| 앱 보고 유효기간 경과 | 앱 unknown·stale, Xen running/fresh 유지, 두 화면 `app_waiting` |
| 새 정상 보고 | 실제 MQTT를 거쳐 새 seq 수신, 두 화면 `status` 복귀 |
| 합성 Xen paused | 과거 앱 보고보다 `paused` 표시 우선 |
| 합성 Xen 도메인 부재 | stopped 표시. 정지를 가짜 Xen enum으로 만들지 않고 목록에서 제거 |
| Xen 조회 예외 주입 | 과거 실행 값은 단절로 표시, 정상 배지 대신 `stale` |
| 새 실행·보고 복귀 | 새 합성 boot_id의 보고만 수락하고 양쪽 상태/표시 복귀 |
| Spring→feed 상태 경로에 503 주입 | 외부 REST 503, WS `display.unavailable`. 로컬 presentation과 두 화면은 계속 동작 |
| 중계 경로 복원 | 같은 WS 연결로 현재 전체 상태 재수신, REST 200 |

각 상태 전이에서 외부 REST·WS와 로컬 presentation의 source_epoch, VM UUID/역할, 실행 ID, 앱·Agent 보고 세대/seq, 소스별 상태, OS/주행 데이터를 비교했다. 조회마다 달라지는 전체 snapshot sequence/generated_at, 수신 경과 시간·조회 시각은 동일 값 비교에서 제외했다. 발행자 sequence와 실행/보고 세대는 비교에서 제외하지 않았다.

로컬 화면 1은 VM 세 개 전체 상태, 화면 2는 시험 VM2 상세로 명시적 pinned 선택했다. 각 전이에서 해당 DOM의 표시 종류와 양쪽 `dom_rendered` ACK를 확인했다. UI 오류는 0건이며 상호작용 가능한 실제 차량 화면·물리 HDMI 출력의 증거는 아니다.

Debian은 기존 형식의 Agent heartbeat만 합성 발행했다. inventory가 없으므로 관측 OS는 null이며, 앱/제어 권위 보고가 없으므로 실제 주행 모드·활성 VM을 만들지 않는다. 기존 웹 콘솔을 열거나 `xl console`을 호출하지 않았다.

## 검증과 증거

- Spring display 기존 회귀 4개와 새 통합 시험 1개 통과. 최종 소스 비교/정리 기록 보완 후 새 통합 시험을 재확인했다.
- Python의 인증·제품 시험 API 비노출 HTTP/WS 시험 1개 통과. 기존 Starlette TestClient/httpx 폐기 예정 경고가 있었으며 실패는 없었다.
- [REST·WS·표시 원본 결과](display-253-step5-pipeline-evidence.json): 8개 상태 전이와 중계 실패/복귀, 인증·읽기 전용 경계.
- [브라우저 결과](display-253-step5-browser-evidence.json): 9회 양쪽 DOM 확인, 출력별 렌더 ACK와 viewport/문서 크기.
- [시험 종료 정리](display-253-step5-cleanup-evidence.json): 임시 feed·브로커·발행자·브라우저 종료. VM은 접근·변경하지 않음.

전체 화면 캡처는 작업 폴더 `outputs/display-253/step5/`에 보관했다. 1280×720 Chromium에서 전체 화면의 문서 높이는 720px, 상세 화면은 상태에 따라 최대 726px였다. 따라서 720p 무스크롤 수락은 이번 결과로 완료하지 않는다. [252번](https://ssafy.atlassian.net/browse/S15P31E110-252) 화면 배치·대상 브라우저/폰트 확인 후속 항목이며 이번 수집 연결 검증과 구분한다. 화면을 잘라 숨기는 CSS 변경은 하지 않았다.

## 재현

Java 21·Maven, Python 시험 환경의 제품 패키지/pytest·AMQTT·Playwright 및 Chromium, 빌드된 vm-screens가 필요하다. 이 실행에서는 작업 폴더의 포터블 JDK와 기존 Maven 캐시를 사용했다. 시스템/게스트 패키지나 자동 시작 서비스는 변경하지 않았다.

```sh
# frontend
npm run build -w @xenova/vm-screens

# backend: 명시한 경우에만 Python/브라우저 시험을 실행한다.
export DISPLAY_PIPELINE_PYTHON=/absolute/path/to/test-venv/bin/python
export DISPLAY_PIPELINE_OUTPUT=/absolute/path/to/test-output
./mvnw -Dtest=DisplayIntegrationTest,DisplayPipelineIntegrationTest test

# platform/services/display/router
python -B -m pytest -q -p no:cacheprovider tests/test_feed.py::test_real_http_and_ws_auth_full_snapshots_and_read_only
```

`DISPLAY_PIPELINE_PYTHON`을 지정하지 않은 일반 테스트 실행에서는 새 통합 시험을 건너뛴다. 임시 프로세스는 시험 종료 때 정리한다. 다른 브랜치·실행 중 VM·펌웨어를 필요로 하지 않는다.

## 다음 연결과 남은 의존성

외부 프론트와 서버에 먼저 연결하려면 [251번](https://ssafy.atlassian.net/browse/S15P31E110-251)을 우선한다. [외부 연동 인계](../contracts/display-external-integration.md)에 공개 경로·인증·오류·서버 설정을 정리했다. 이번에는 서버에 배포하거나 프론트 담당자의 페이지 라우팅을 변경하지 않았다.

다음 항목은 미완료로 유지한다: 실제 서버망/TLS/프록시·외부 프론트 연동, VM1 송신·최종 UUID/app 배치·상시 배포, 실제 OS inventory, 제어 권위 계약 합의, 실제 HDMI 2출력·원격 영상/실보드 성능. [4단계 자동 복구 실패](display-253-step4.md)는 사용자 결정대로 앱·하위 통신 담당자 의존성이며 실패한 JSON을 수정하지 않았다. 253번의 선택된 수신·소비 연결 범위를 검증했다는 사실로 전체 배포·차량 시연 완료나 모든 Jira 완료를 주장하지 않는다. 커밋·푸시·Jira 변경은 수행하지 않았다.
