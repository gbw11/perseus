# Xenova Telemetry Publisher 2.0 — 구현 인계 패키지

새 Telemetry 발행 계약과 **합성 Golden Payload**다. 실제 Publisher 펌웨어·브로커 설정·백엔드 변경이나 보드 시험을 수행한 패키지가 아니다.

## 먼저 읽기

| 파일 | 용도 |
|---|---|
| `CONTRACT.md` | 발행자 책임·토픽·신원·시간·metric·오류·snapshot·전송 계약 |
| `schemas/message.schema.json` | 5개 stream의 JSON Schema 2020-12 |
| `schemas/metric-catalog.json` | 24개 scalar metric의 의미·단위·폭·원천·label |
| `fixtures/manifest.json` | Golden 메시지별 topic/QoS/retain·정상/거절 기대값 |
| `fixtures/valid/` | 정상·미지원·실패·정체·재부팅·snapshot 등 30개 메시지 |
| `fixtures/invalid/` | 의도적인 규격 위반 30개 |
| `fixtures/sequences/` | 수신 상태 전이 11개 사례 |
| `tools/validate_contract.py` | 독립적인 구조·의미·시험 순서 validator. 운영 서비스 아님 |
| `docs/CODEX_HANDOFF.md` | Publisher 구현 지시와 단계별 수락 |
| `docs/CONSUMER_HANDOFF.md` | 백엔드/MQTT/표시 담당자가 확장할 최소 계약 |
| `docs/ACL.example` | 새 namespace의 좁은 ACL 예시. 실제 적용하지 않음 |
| `docs/REVIEW.md` | 설계 검토·자료 제약·의도적 비목표 |
| `docs/GOLDEN_INDEX.md` | 모든 Golden·거절·순서 사례 색인 |
| `docs/VALIDATION.md` | 이 패키지에 실제 수행한 검사와 한계 |
| `docs/SOURCES.md` | 사용자 자료/확인 소스/공식 표준 및 신규 설계 구분 |

## Offline 검증

Python 3.10+와 개발 PC의 `jsonschema` 4.x가 필요하다. 대상 Zephyr에 Python/JSON Schema runtime을 넣지 않는다.

```sh
python tools/validate_contract.py --self-test --report docs/validation-results.json
```

실제 수신 payload 검사 예시:

```sh
python tools/validate_contract.py \
  --message received-metrics.json \
  --topic xenova/v2/vms/11111111-1111-4111-8111-111111111111/telemetry/metrics \
  --qos 0
```

위 UUID는 Golden의 합성 예제다. 실보드 사용 시 실제 신원으로 바꾼다. 수신 packet의 실제 bytes를 파일로 보존할 때 `--message`는 그 실제 길이까지 검사한다. 단순 fixture self-test는 compact wire 표현의 크기를 검사한다.

원래 `agent.*`/`app.heartbeat`/`drive.command` 의미를 바꾸지 않고 `xenova/v2/.../telemetry/...`를 추가한다. 새 계약의 값이 구형 백엔드에 거절되더라도 unit·신원을 거짓으로 변환해 맞추지 않는다.
