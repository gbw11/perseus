# 제어 계층과 백엔드 HTTP 계약 v1.0 (폐기)

> **이 문서는 더 이상 기준이 아닙니다.** 내용은 [VM 관리 API 계약 v1](contracts/vm-api-contract-v1.md)에 통합됐고, 이후 리뷰에서 여러 결정이 바뀌었습니다. 구현과 리뷰는 계약 문서와 [contracts/dom0-manager-openapi.yaml](../contracts/dom0-manager-openapi.yaml)을 기준으로 합니다.

## v1.0 이후 바뀐 결정

이 문서를 예전에 읽었다면 아래 차이를 확인하세요.

| 항목 | v1.0 (이 문서) | 현재 계약 |
| --- | --- | --- |
| 실행 위치 | Spring과 dom0-manager 모두 dom0, `127.0.0.1:8081` | Spring은 EC2, dom0-manager는 보드. 접속 방식(바인드 주소·서비스 인증)은 미확정 (계약 13장) |
| `start` 엔드포인트 | `POST /api/v1/vms/{vm_id}/start` | **없음.** 같은 `vm_id`로 `POST /api/v1/vms` 재호출 (계약 4.2) |
| stop 응답 | `200 VM(state=shutdown)` | dom0-manager `204` 본문 없음. Spring 공개 API는 `200 VM(state=stopped)` |
| DELETE | 디스크 보존 | 강제 종료 후 **디스크 삭제** (계약 4.4, 6장) |
| 생성 재시도 | dom0-manager가 동일 요청에 `200` | dom0-manager는 `201` 또는 `409 VMExists`만. "동일 재시도 200"은 Spring 동작 (계약 4.2, 10.1) |
| 생성 요청의 `name` | dom0-manager에 전달 | 전달하지 않음. 이름은 Spring만 보유 (계약 1.2, 9장) |
| dom0-manager VM 객체 | `name`, `template_*` 포함 | `vm_id`, `state`, `vcpus`, `memory_mib`만 (계약 2.1) |
| state 값 | `running`, `paused`, `stopping`, `shutdown`, `crashed` | dom0-manager는 `running`, `stopping`, `paused`, `crashed`. "꺼짐"은 도메인 부재이며 Spring 공개 API에서만 `stopped` (계약 3장) |
| 자원 부족 | `409 InsufficientResources` | dom0-manager `507`, Spring 공개 `409 INSUFFICIENT_RESOURCES` (계약 8장) |
| Spring 타임아웃 | 조회 10초, 명령 45초 | 조회 6초, 명령 35초, 연결 2초 (계약 10장) |
| 명령 재시도 | 목표 상태 기준 1회 허용 | 연결 실패만 1회 재시도, 응답 타임아웃은 재시도 안 함 (계약 10장) |
| 복구 | Spring이 heartbeat 감시와 복구 정책 수행 | 복구 주체 미확정. 결정 전까지 Spring은 복구를 수행하지 않음 (계약 13장) |
