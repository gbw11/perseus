# Gateway 직접 구현·공동 연동 목록

담당: 박신영 / 플랫폼·Gateway. **상태: 최소 연결 구현·로컬 검증 완료 / 후속 확장·설치·장치 수락 전.**
Gateway 자체는 타 담당자에게 넘기는 영역이 아닙니다. 현재 `48dc3cc`, `b35a675`, `0f26bf9`의 구현 이력을 기준으로 직접 작업하며, ESP32·입력 생산자·Worker와의 공동 연결만 해당 담당 요청서와 대조합니다.
기존 D03-G01~G04와 미완료 상태를 유지합니다. 이 문서는 추가 작업의 시작점이며 정식 메시지 필드·시간값의 원문이 아닙니다.

## 현재 할 일과 읽는 순서

1. 아래 범위표에서 최소 연결과 담당자 확장을 구분합니다.
2. [주행 설계](../platform/drive-pipeline-architecture.md)의 9.4·9.6·12·13·19·20절을 읽습니다.
3. [MQTT 계약](../contracts/mqtt-contract-v1.md)의 현재 제어·준비·출처 조건과 비교합니다. 새 프로필의 미확정 필드를 추측해 구현하지 않습니다.
4. [기존 Authority 수습 요청](2026-10-27-authority-recovery-ctrl.md)의 D03-G01~G04와 [원 Gateway 요청 이력](2026-10-20-manual-app-gateway-ctrl.md)을 읽습니다.

## 최소 수정과 후속 직접 구현

| 구분·연결 ID | 대상 파일·함수 | 입력·출력 및 요구 | 수락 조건 |
|---|---|---|---|
| 승인된 최소 연결, 로컬 완료 | `platform/services/gateway/xenova_gateway/warm.py:WarmDriveMachine`, `verified.py`, `verified_runner.py`, `config.py`, 공용 `drive_binding.py`·`client.py` | warm-drive-1 선택, 앱별 바인딩·대기/활성화/실제 진행과 seq 전 신원 guard. Authority·generation·출력 보호 유지 | `tests/test_warm.py`, 공용 `tests/test_client.py`, 동일 native 후보의 A/B 실제 격리 broker 시험. [후속 검증](../verification/zephyr-drive-foundation-20261029.md#후속-앱별-수명과-warm-준비-연결) |
| DEC-11·16, 공통 계약 구현 | `planning.py`, `authority.py:commit_transition`, warm actor/runner | 원 요청에 준비 근거를 채택하고 owner/generation·대상과 원 결과를 한 번 커밋. 정상 A 사전 준비 중 유지, 새 문맥 계산 뒤 출력. 현재 실제 연결은 명시적 stateless 앱 | 원 ID/기한 재발급 0, 취소/커밋 순서, 저장 미확정·프로세스 재시작, A/B 대칭 계획 인계. 상태 있는 앱의 실제 전송·연속 장치 적용 수락은 별도 |
| DEC-19·ARCH-T21~23·32·35, 후속 | 상태 수락·전환 연결부. 구체 함수는 계약 확정 후 지정 | 당시 허용 출처에서 수락한 완전 사본의 현재 호환성 확인. 경로를 대신 계산하거나 옛 명령을 새 generation으로 포장하지 않음 | 정상 사본은 유한하게 채택, 불일치·부분·권한 종료 후 지연 갱신 거절. 남은 출력 예산으로 연속 승계/정지 후 재개 구분 |
| D03-G01~G04, 기존 대기 | `authority.py`, `__main__.py`, 기존 요청의 진단·복원 지점 | 원본/백업 최신성·단일 소유·저장 미확정의 지원 수습 | 기존 요청의 성공/거절 반례 유지. marker 삭제·generation 초기화·자동 재무장 금지 |
| DEC-13·ARCH-T25·26·38, 후속 | `sink.py`, `frame.py`, 실제 장치 피드백 연결부 | 시리얼 쓰기·장치 수락·적용·실제 정지를 구분하고 오류를 출력 보호에 연결 | 제출만의 적용/정지 완료 0. 장치 미응답은 유한 종결, watchdog/E-stop 보존 |

## 제공물·선행조건·금지 효과

- 플랫폼 제공물: 실제 계산 수명·원 문맥·기한·join/잔존 근거와 현재 execution/Bootstrap. 새 연결의 완료 여부는 같은 후보의 시험 근거로 확인해야 합니다.
- 모드·owner·출력 허가는 Gateway가 소유합니다. Worker/앱 준비만으로 권한을 발급하지 않습니다.
- 사용자 MANUAL 선택, 명시적 AUTO 복귀·준비 유지·카운트다운을 유지합니다. 제어 적용 종결 1초와 명령 신선도 100ms를 같은 예산으로 보지 않습니다.
- 상태 사본 전송·장치 적용의 남은 필드는 DEC-19·13의 생산자/소비자와 확정합니다. 공통 준비·커밋과 세션 입력 의미는 설계 12.2절·MQTT 7.1절을 따릅니다. Tailscale·broker ACL·운영 서비스·장치는 변경하지 않습니다.

## 진행 기록

- 2026-10-29: 최소 소비 연결을 구현·로컬 검증했습니다. Gateway 담당은 커밋 이력과 사용자 확인에 따라 박신영입니다. 계획 인계·Authority 수습은 후속 직접 구현이고 장치 적용은 ESP32 담당자와 공동 수락합니다. 앱 하나의 receipt 미확정이 정상 다른 앱의 바인딩·출력 근거를 철회하지 않는 반례도 연결했습니다.
