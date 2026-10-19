# 백엔드 VM API 규약 v1

> 이 규약은 [VM 관리 API 계약 v1](contracts/vm-api-contract-v1.md)에 통합됐습니다. 두 문서가 다르면 계약 문서를 따릅니다.

Spring 백엔드의 VM API에 적용하는 규약입니다. 아래 세 가지를 정합니다.

1. dom0-manager 호출의 타임아웃과 재시도
2. VM 생성 입력값 검증
3. 템플릿 목록 제공 방식

## 1. 타임아웃 · 재시도

**45초는 공개 요청 하나의 전체 예산**입니다. 시도 하나당 시간이 아닙니다.

| 요청 | 연결 실패 (2초) | 응답 타임아웃 |
| --- | --- | --- |
| 조회 (GET) | 1회 재시도 → 또 실패하면 `503` | 1회 재시도 → 또 실패하면 `504` |
| 명령 (생성·시작·정지·삭제) | 1회 재시도 → 또 실패하면 `503` | **재시도 안 함** → `504`, 프론트가 GET으로 상태 확인 |

| 항목 | 값 |
| --- | --- |
| 연결 타임아웃 | 2초 |
| 재시도 간격 | 0.5초 |
| 응답 대기 시간 (시도당) | 조회 6초, 명령 35초 (드라이버 30초 + 여유 5초) |
| 전체 예산 | 조회 약 15초, 명령 약 40초 |

명령의 응답 타임아웃에서 재시도하지 않는 이유는 두 가지입니다.

- **요청이 이미 처리 중일 수 있습니다.** 연결은 됐으니 dom0-manager가 요청을 받았고, 처리하는 중일 수 있습니다.
- **재시도해도 기다리기만 합니다.** dom0-manager는 워커가 1개라, 다시 보낸 요청은 처리 중인 요청 뒤에서 대기합니다.

명령은 목표 상태 기준으로 멱등합니다. 그래서 프론트가 상태를 확인한 뒤 사용자가 다시 요청해도 안전합니다.

## 2. VM 생성 입력값 검증

| 항목 | 규칙 |
| --- | --- |
| `name` | 1~63자, 소문자·숫자·하이픈(`-`)만 |
| `vcpus` | 1~4 |
| `memory_mib` | 템플릿의 `min_memory_mib` 이상 ~ 1024 이하 (공통 하한 128) |
| 배수 제약 | 없음. 화면 입력만 64씩 오르내림 |

규칙을 벗어나면 Spring이 dom0-manager를 호출하지 않고 `422 VALIDATION_ERROR`로 거절합니다. 템플릿 목록에 없는 템플릿은 `404 TEMPLATE_NOT_FOUND`입니다.

공통 하한은 128입니다. Xen domU에서 100MiB로는 커널 부팅이 불안정하고, dom0-manager도 128 미만을 `400 InvalidArgument`로 거부합니다.

## 3. 템플릿 목록

- **API:** `GET /api/v1/templates`를 제공합니다. 1차 데이터는 Spring 설정 파일에 고정합니다.
- **버전:** OS(Ubuntu·Debian·Zephyr)마다 하나로 고정합니다. 플랫폼이 세 번째 템플릿을 Alpine에서 Zephyr로 바꿨습니다(2026-10-19 반영).
- **화면:** 드롭다운 하나로 템플릿을 고르고, 고른 템플릿의 `min_memory_mib`를 메모리 입력 칸의 최솟값으로 씁니다.
- **VM 생성 시 검증:** 없는 템플릿이거나, 메모리가 그 템플릿의 `min_memory_mib`보다 작으면 거절합니다.

응답 필드:

| 필드 | 의미 |
| --- | --- |
| `template_id` | 플랫폼 공개 카탈로그 ID (예: `debian-13-arm64`) |
| `version` | 템플릿 버전 |
| `display_name` | 드롭다운에 표시할 이름 (예: `Debian 12`) |
| `min_memory_mib` | 이 템플릿으로 만들 때의 최소 메모리 |
| `guest_kind` | VM 종류 `linux` / `zephyr` (Zephyr는 에이전트·네트워크·정상 종료가 없는 콘솔 전용 펌웨어) |

응답 예시 (ID와 버전은 플랫폼 공개 카탈로그 기준이며 메모리 값은 실측에 따라 조정 가능):

```json
[
  { "template_id": "ubuntu-24.04-arm64", "version": "1.0.0", "display_name": "Ubuntu 24.04", "min_memory_mib": 512, "guest_kind": "linux" },
  { "template_id": "debian-13-arm64", "version": "1.0.0", "display_name": "Debian 13", "min_memory_mib": 256, "guest_kind": "linux" },
  { "template_id": "zephyr-xenvm-starter-arm64", "version": "1.0.0", "display_name": "Zephyr Starter 1.0.0", "min_memory_mib": 128, "guest_kind": "zephyr" }
]
```

## 확정 전 확인 사항

- [ ] P2: OS별 `min_memory_mib` 값
