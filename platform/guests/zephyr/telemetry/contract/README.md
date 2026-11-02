# Xenova Telemetry 계약

[CONTRACT.md](CONTRACT.md), [message schema](schemas/message.schema.json),
[metric catalog](schemas/metric-catalog.json)가 현재 **2.1**의 원문이다.
Publisher는 기존 v2 토픽을 유지하고 2.1을 명시한다. 2.0으로 조용히 변환하지 않는다.

- [2.0 원문·schema/catalog](versions/2.0/CONTRACT.md)와 기존 fixtures/는 과거 입력을 보존한다.
- `tools/validate_contract.py --self-test`는 보존한 2.0 합성 Golden과
  `fixtures-2.1/`의 결정적 production-C 경계 표본을 구분해 검사한다.
- 실제 C bytes와 native 수신은 `platform/tests/test_xenova_telemetry.py` 및
  `tests/run_native_acceptance.py`에서 별도로 검사한다. Golden 통과는 실계측 수락이 아니다.
- `docs/`의 날짜별 검토·과거 결과는 당시 근거다. 현재 기능·사용법은 계약과 상위 README를 따른다.
- 제품 소비자는 schema 2.1/catalog 분기, CPU window/수명 label, memory region,
  nullable UTC와 capability/표본/현재 상태의 구분을 지원해야 한다.
- `SHA256SUMS`는 현재 파일 묶음에 대한 검사 목록이다.
  `versions/2.0/ORIGINAL_SHA256SUMS`는 제공받은 원본 묶음의 체크섬을 보존한 이력이며,
  변경된 현재 파일에 적용하지 않는다.

```sh
python tools/validate_contract.py --self-test
python tools/validate_contract.py --message received-metrics.json \
  --topic xenova/v2/vms/11111111-1111-4111-8111-111111111111/telemetry/metrics --qos 0
```

독립 구독기 사용법은 [Publisher README](../README.md)를 따른다.
합성 fixture, 로컬 실행, ARM, 이미지 포함, 실제 Xen·제품 종단을 구분한다.
