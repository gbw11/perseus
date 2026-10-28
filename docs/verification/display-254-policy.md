# 254 — VM 상태별 HDMI 화면 정책 로컬 검증

2026-10-26 · S15P31E110-254. 보드를 다른 팀원이 사용하는 동안 수행한 합성 입력 기반 검증이다. 실제 HDMI 출력·제어 발행자·외부 서버·Tailscale 연결의 통과 증거가 아니다.

## 범위

모니터 1은 전체 상태, 모니터 2는 선택 VM 상세 화면을 유지한다. 기존 250·253 구현을 재사용해 선택 사유와 수신 상태 우선순위를 보완했다. Raspberry Pi, QEMU, 운영 MQTT, Spring에 접속하지 않는다. 실제 VM 생명주기·복구·주행 모드 변경 명령은 실행하지 않는다.

## 변경

- presentation `outputs[].reason`을 추가했다. 기존 필드는 유지하고 프론트는 새 필드가 없는 기존 응답도 수락한다.
- 상세 화면에 pinned / 확인된 활성 VM 추적 / 활성 VM 미확인으로 마지막 선택 유지 사유를 표시한다. 화면 피드가 끊기면 사유가 현재 판단인 것처럼 표시하지 않는다.
- follow_active는 등록된 주 구동·백업 VM만 따른다. Debian은 운영자가 pinned로 선택할 수 있지만 구동 승계 대상으로 추적하지 않는다.
- 앱 수신 경로와 보고의 신선도를 과거 failed 값보다 먼저 판단한다. 수신 경로 문제와 현재 앱 오류를 구별한다.
- 화면 선택 설정 버전 `revision`과 기존 전체 내용 해시 `render_revision`/출력 ACK 규약은 유지한다. 모든 출력이 공유하는 해시이므로 다른 VM의 상태 변화로 해시가 갱신될 수 있지만, 상세 선택 UUID를 바꾸거나 다른 VM의 표시 결정을 장애로 바꾸지는 않는다.

## 상태 전이 수락표

| 입력 | 기대 표시/선택 | 검증 |
| --- | --- | --- |
| 최초 미수신 | 상태 수신 대기, 활성 VM·모드 추정 금지 | Python 정책 |
| VM running + 신선한 앱 보고 | 앱 보고 정상 | 실제 StateStore → DisplayPolicy |
| 앱 error → 보고 경로 단절 | 현재 오류 대신 수신 경로 미확인, Xen running 유지 | 실제 StateStore → DisplayPolicy |
| MQTT 재연결·구독 허용, 새 보고 없음 | 앱 상태 미확인 유지 | 실제 수집기의 연결 세대 갱신 사용 |
| 새 heartbeat 수신 | 정상 표시 복귀 | 실제 StateStore → DisplayPolicy |
| running/paused/stopping/stopped/crashed → running | 해당 상태 표시 후 원래 표시 복귀; pinned UUID·다른 VM 결정 유지 | 합성 Xen 관측 |
| Xen 조회 실패 → 조회 복구 | 수신 지연 표시 후 관측 기반 복귀; VM crash로 단정 금지 | 실제 StateStore → DisplayPolicy |
| AUTO → MANUAL, 활성 UUID 동일 | 주행 모드만 변경, 상세 대상 유지 | 표시용 제어 메시지 주입 |
| VM1 crash만 관측 | VM2 승계로 추정하지 않음 | 정책 |
| 확인된 active VM1 → VM2 | follow_active에서만 VM2 상세 선택 | 표시용 제어 메시지 주입 |
| 제어 보고 3초 만료 | 활성/모드 미확인, 마지막 상세 UUID 유지 및 이유 표시 | 수신기 단조 시계 진행 |
| 제어 보고 재개 | 새로 확인된 구동 UUID 추적 | 실제 StateStore → DisplayPolicy |
| 잘못된 활성 대상으로 Debian 전달 | follow_active 대상 변경 거부 | 정책 방어 검증 |
| 작업 진행 상태 주입 | running일 때 작업 표시; 명시적 crash는 숨기지 않음 | 합성 operation 입력 |
| 표시 피드 단절 | 과거 선택 안내, 현재 활성 구동 배지 제거 | React 렌더 테스트 |

## 재현

기존 의존성이 준비된 Python 가상환경에서:

```sh
cd platform/services/display/router
python -m pytest
```

프론트 의존성이 준비된 환경에서:

```sh
cd frontend
npm test -- apps/vm-screens/src/display
npm run typecheck -w @xenova/vm-screens
```

실행 결과: Python 전체 101개, 프론트 표시 테스트 11개 통과. vm-screens 타입 검사와 변경한 프론트 4개 파일의 Prettier 검사도 통과했다. Python에서 기존 Starlette/httpx 사용 중단 예정 경고 1건이 발생했다. Windows 기본 Python에 FastAPI/Pydantic이 없어 기존 WSL 가상환경을 사용했다. 테스트 입력은 합성이며 기존 보드·브로커를 켜지 않는다.

## 남은 확인

- 실제 제어 담당의 활성 VM·모드 발행 및 ACL 연결 합의는 기존 의존성이다.
- 수집기는 아직 `operation=null`, `ai_status=unknown`을 제공한다. 작업 진행 표시 분기 검증은 합성 입력으로 했으며 실제 복구 이벤트·AI 상태 수집을 연결했다는 뜻이 아니다.
- 실제 HDMI 커넥터 두 개, 장치 분리·재연결, 시연 캡처는 기존 실보드 수락 절차와 256·257·255 범위에서 확인한다.
- 외부 웹 콘솔 실서버 검증(251), 보드 Tailscale 직접 연결(286)은 별도 대기 작업이다. 이 검증으로 해당 작업이나 254 전체를 자동 완료 처리하지 않는다.
