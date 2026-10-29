# 실행 로그 파이프라인 로컬 검증 — 2026-10-28

## 작업과 변경

사용자가 확인한 범위는 **기존 터미널 입력 + 별도 실행 로그 조회**다. 입력 키/명령 이력 감사 기록은 추가하지 않았다. 최신 platform `5035a87`에서 `feat/platform/runtime-log-pipeline`을 분리했다. VM·펌웨어·실보드·외부 서버 배포는 변경하지 않았다.

- dom0 unit allowlist의 journal 로그를 읽는 별도 collector와 bounded SQLite 저장소.
- Linux 게스트/Jetson용 별도 journal HTTP shipper, 수신 성공 후 cursor checkpoint, 재전송 중복 제거.
- source별 키·VM UUID·component allowlist에 묶인 private ingestion. source 키로 제어/조회 거부. 별도 listener는 ingestion만 노출.
- 기존 Manager 인증을 재사용한 `/api/v1/logs`, `/api/v1/logs/sources`, Spring JWT 조회 중계. 로그 작성 기능을 공개 Spring API에 추가하지 않음.
- 웹 콘솔과 VM 상세의 기존 터미널 아래 별도 실행 로그 패널. 전체/선택 VM 조회, 1초 증분 polling, 일시정지·단절 안내, 저장소 epoch 교체·보존 gap 처리,1000개 브라우저 buffer.
- ANSI 제거·일반 토큰/비밀번호 마스킹, source ID/boot/native cursor 기반 중복 제거,24시간/20000개 보존 및 메시지 크기 한도.
- 배포 unit·환경·source 등록 예제, 수집 가능/미연동 범위를 문서화.

## 검증 결과

| 검증 | 결과·범위 |
|---|---|
| Python 로그/인증/OpenAPI |25개 통과. 보존·중복·epoch·버퍼 framing·인증·source 위조/컴포넌트 거부·ingest 크기·private listener 격리 |
| Spring MVC+HTTP client |19개 통과. 실제 보안 필터 기반 MVC slice와 실제 HTTP stub에 대한 Manager client. 외부 서버·MySQL 전체 통합은 아님 |
| 프론트 로그/기존 console API |관련14개와 전체134개(18파일) 통과. 중복/용량·텍스트 렌더·pause·오프라인·DB교체 및 기존 console API |
| 기존 Linux PTY |9개 통과. 실제 PTY와 자식 프로세스. 실제 Xen 게스트 명령은 보내지 않음 |
| 프론트 type/build/lint |통과. 기존 admin chunk>500kB 경고 유지 |
| 실제 로컬 journal |WSL에서 임시 systemd 서비스 출력→SQLite, 수집기 stop/restart 후 checkpoint 이어받기·중복 없음·정상 정리 |
| 실제 로컬 HTTP |WSL 실제 journal→shipper→실제 FastAPI Manager(mock driver)→SQLite→인증된 GET. receiver 단절 시 checkpoint 유지, 재시작 후 중복 없음, source 키의 read/control401 |

로컬 probe의 VM UUID는 테스트 식별자이며 실제 Debian/Zephyr의 송신 검증이 아니다. 실제 journal probe용 임시 unit/수집기/HTTP 서버는 종료했다. 결과의 /tmp DB 경로는 당시 실행 위치이며 영속 배포 경로가 아니다. WSL 종료 후 /tmp가 사라질 수 있으므로 JSON 결과와 정확한 probe source를 별도로 보관했다.

## 발견한 문제와 수정

- Windows sandbox의 socketpair 생성에서 TestClient가 멈췄다. 같은 코드를 로컬 통신이 허용된 테스트 실행 환경에서 통과시켰다. 제품 코드를 우회하지 않았다.
- 초기 SpringBootTest 전체 컨텍스트는 Docker DB 부재로 실패했다. 새 로그 경로의 경계를 검증하는 MVC slice+실제 HTTP client 테스트로 분리했다. 전체 DB 통합을 통과했다고 하지 않는다.
- 미등록 Spring 경로가 기존 catch-all에서500으로 바뀌는 것을 발견해 NoResourceFoundException을404로 매핑했다. 로그 전용 장애 응답은 LOG_SOURCE_UNAVAILABLE로 구별했다.
- 실제 HTTP 송신 시험에서 BufferedReader.readline+selector의 선행 읽기로 일부 journal 줄이 대기하는 문제를 발견했다. bounded binary chunk framing으로 두 수집기를 고쳤고, coalesced/partial/oversize framing 회귀를 추가했다. 초기 HTTP_FIRST 미도착 결과도 보존했다. 수정 후 실제 HTTP 단절·재시작·조회 시험을 통과했다.
- React effect 내 동기 state reset을 이벤트 처리로 옮겨 lint를 통과시켰다.

## 아직 수행하지 않은 것

사용자가 현재 보드 사용 불가라고 답했으므로 보드 배포·Manager 재시작·실제 guest/Jetson 로그 수신은 하지 않았다. 실보드 Python sqlite3 포함 여부도 아직 미확인이다. 외부 Spring/Docker 서버에도 배포하지 않았다.

Zephyr는 journal이 없어 펌웨어 담당의 로그 송신/기존 중계 연결이 필요하다. Spring이 Docker stdout에만 쓰면 인프라 담당이 Docker→journal/소스 adapter를 연결해야 한다. 실제 Linux service 이름·VM UUID·소스별 키·bridge/VPN 주소는 배포 시 확정한다. 이 작업만으로 모든 SSH TTY/모든 앱 printf가 자동 수집됐다고 표기하지 않는다.

소스가 처음부터 로그를 남겨야 수집할 수 있다. 기존 journal vacuum 뒤 복구 불가능한 기록까지 보장하는 무손실 감사 저장소는 아니다. 입력 문자열·로그인 비밀번호는 콘솔에서 수집하지 않는다.

## 인계

[실행 로그 계약](../contracts/runtime-logs-v1.md), [배포 README](../../platform/services/dom0-manager/deploy/runtime-logs/README.md), [로컬 결과 요약](evidence/runtime-logs-local-20261028/summary.json).

커밋·푸시·MR·지라 변경은 하지 않았다. 보드가 가능해지면 새 collector·DB 경로를 먼저 배치하고 Manager 설정/코드를 반영한 뒤, 실제 서비스와 소스 하나씩 수신을 검증한다. 기존 HDMI·터미널·주행 기능은 재개발하거나 자동 변경하지 않는다.
