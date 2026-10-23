# 표시 파이프라인 3단계 — 화면 정책·렌더러 검증

2026-10-22 · `docs/platform/S15P31E110-250-display-contract` 브랜치.

## 구현

- 모니터 1: VM1·VM2·VM3 전체 상태. 모니터 2: 선택 UUID의 상세 화면.
- VM1 Zephyr 주 구동, VM2 같은 Zephyr 백업·미러링, VM3 Debian 인터랙션으로 구분.
- 기대 OS와 관측 근거를 분리하고 VM 역할·UUID·실행 상태·앱 상태·수신 지연을 표시.
- 장애/정지/종료/일시 정지, 앱 미수신·오류, Xen 소스 단절/지연에 따른 표시 결정.
- 기본 VM1 pinned 유지. 명시적인 follow_active 설정에서만 권위 소스의 신선한 활성 VM을 따라감. 주행 모드 변경만으로 VM을 바꾸지 않음.
- 서비스 토큰을 페이지에 넣지 않는 화면용 쿠키 인증, 출력별 DOM 렌더 revision ACK와 3초 만료.
- dom0 수집기가 정적 화면과 presentation을 제공하므로 로컬 화면에 외부 Spring 접속을 요구하지 않음.

실제 코드: `platform/services/display/router/xenova_display/policy.py`, `main.py`, `frontend/apps/vm-screens/src/display/`.
실행·인증·내부 API 계약은 [수집기 README](../../platform/services/display/router/README.md#3단계-로컬-화면-렌더링)를 참고한다.

## 자동 검증

| 범위 | 결과 |
| --- | --- |
| 수집기/표시 정책/화면 세션/ACK 테스트 | 30개 통과 |
| 프론트 전체 테스트(기존 화면·콘솔 포함) | 48개 통과 |
| vm-screens TypeScript 검사와 production 빌드 | 통과 |
| 변경 프론트 코드 ESLint | 오류 0개. 기존 main.tsx의 Fast Refresh 경고 1개 |

상태 우선순위, OS 보고 불일치, 미지원 Xen 상태, 고정/따라가기 선택, 세대·revision 거부, ACK 만료, 화면 쿠키 권한 제한을 확인했다. 프론트에서는 동일 Zephyr 역할 구분, 모드와 활성 VM 분리, 화면 연결 실패 시 정상 표시 제거, UUID 기반 상세 선택, 이전 스냅샷 거부를 확인했다.

## 브라우저·QEMU 검증 경계

시험 도구는 `tests/probe_browser.cjs`, 시험용 서버는 `tests/preview_server.py`다. 제품 앱에 시나리오 제어 API를 추가하지 않았다. 실제 Chrome에서 빌드 결과를 열어 검사하며, 실제 QEMU Xen은 SSH 읽기 전용 드라이버로 조회한다. 이 시험에서 상태 정규화와 웹 렌더러는 WSL에서 실행했다. 물리 dom0 HDMI 렌더링 검증은 아니다.

최종 브라우저 시험은 11개 항목 통과했다. [기계 판독 결과](display-step3-browser-evidence.json)에 사례 목록과 시험 전후 동일한 VM 실행 ID를 보관했다. 1280×720에서 두 화면 모두 문서 스크롤 없이 표시됐고, 브라우저 런타임 오류는 없었다.

실제 관측: 세 VM의 Xen 상태·UUID/실행 ID, 기본 전체/상세 화면, 두 DOM 렌더 확인. Zephyr 앱 보고와 OS 근거가 없으면 미확인으로 표시한다.

합성 입력: AUTO/MANUAL 변경, VM1 장애, Xen 상태 지연, 활성 구동 VM2 확인은 별도 시험용 관측 입력이다. 실제 VM을 고장 내거나 주행 승계가 성공했다고 기록하지 않는다.

통신 시험: 브라우저의 presentation 요청만 차단해 마지막 값/끊김 표시를 확인하고, 차단을 해제해 최신 스냅샷 복구를 확인한다. 실제 VM은 시작·정지·재시작하지 않는다. 1600×1000 및 1280×720에서 전체·상세 화면을 확인한다.

## 남은 단계

- 물리 HDMI 두 커넥터 초기화·매핑·배치 실행과 장치 분리/재연결 시험.
- 실제 키오스크 실행기·브라우저 세션 준비/갱신 자동화.
- 실제 앱 그래픽/콘솔 요약 소스 어댑터와 HDMI 캡처·인코딩·원격 송수신.
- Zephyr 앱 송신 및 권위 제어/AI/복구 상태 연결. 현재 operation·AI는 아직 미연결이다.

DOM ACK를 물리 HDMI 적용 완료나 실제 앱 영상의 증거로 해석하지 않는다. 외부 대시보드 페이지 라우팅과 기존 MO-04 터미널 경로는 변경하지 않았다. Jira 수정·커밋·푸시·platform 브랜치 변경은 하지 않았다.
