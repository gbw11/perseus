# 계약 패키지 검증 결과 — 2026-10-23

## 실제 실행

```sh
python tools/validate_contract.py --self-test --report docs/validation-results.json
```

환경: Python 3.13.5, jsonschema 4.26.0.

| 검사 | 결과 |
|---|---:|
| JSON Schema 2020-12 자체 검사 | PASS |
| 정상·미지원·오류·재부팅·snapshot 합성 메시지 | 30 / 30 PASS |
| 의도적 규격 위반의 예상 오류 코드 일치 | 30 / 30 거절 확인 |
| 세션·중복·retained·순서·snapshot·freshness 시나리오 | 11 / 11 PASS |
| 위 시나리오의 개별 기대 결과 | 28개 PASS |
| 정상 예제 최대 compact UTF-8 application payload | 2347 bytes / 계약 상한4096bytes |

개별 결과는 `validation-results.json`, 파일/토픽/QoS/retain은 `../fixtures/manifest.json`에 있다.

## 이번에 검증한 것

구조뿐 아니라 topic/VM/type 일치, 복합 ID, decimal u64/i64 범위, unit/catalog 일치, 오류/null,
시간 순서·UTC 품질, 원본32bit counter, histogram count·quantile 상한, 스택 범위,
source truncation과 wire part, 수집 미요청/실패의 unknown을 확인했다.

검토 과정에서 추가한 경계: **스캔하지 않은 전체 스레드 수와 읽지 못한 오류 이력은 0이 아니라 null**로 표현한다.
서로 다른 종류의 snapshot에 같은 snapshot_id를 재사용하면 conflict로 처리한다.

## 검증하지 않은 것

- 실제 C Publisher 구현·타깃 빌드·네트워크 연결.
- 실제 broker 인증/ACL/수신·PUBACK·subscriber 저장.
- 실제 Diagnostics Core의 runtime 값·코드 전체·실보드 timer/stack/heap.
- RPi4 또는 native_sim에서의 주기·시간 비용.
- 실제 A/B 미러링·주행·Gateway·ESP32·안전정지.
- Spring/HTTP/화면의 수신·DB 저장·표시.

이 수치는 **계약과 합성 fixture의 자기 일관성 검사**다. 새로운 펌웨어나 제품 기능이 구현됐거나
실환경에서 정상 작동한다는 증거가 아니다. Publisher 구현 뒤 독립적인 실제 수신 검증이 추가로 필요하다.
