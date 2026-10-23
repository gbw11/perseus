# 표시 파이프라인 1단계 — 환경 확인 및 검증 경계

확인일: 2026-10-21. 읽기 전용 확인이며 VM/보드 설정을 변경하지 않았다.

## 관측 결과

| 대상 | 확인 결과 | 의미 |
| --- | --- | --- |
| 현재 QEMU dom0 | `uname -m` → `aarch64` | 기존 ARM64 Xen 환경 활용 가능 |
| 현재 QEMU dom0 | `/dev/dri` 없음 | 현재 환경으로 DRM/KMS HDMI 두 출력 실증 불가 |
| 현재 QEMU dom0 | 아래 `command -v`에서 출력 경로 없음 | weston/chromium/chromium-browser/cog/gst-launch-1.0/ffmpeg/wayvnc를 PATH에서 찾지 못함. 패키지 DB 전체 조사나 보드 이미지 판정은 아님 |
| 저장소 표시 서비스 | `platform/services/display/router`, `kiosk`에 `.gitkeep`만 존재 | 실행 서비스 신규 구현 필요 |
| 기존 vm-screens | overview/detail 화면과 텔레메트리 코드 존재 | 재사용 후보. autonomous/manual 역할 구분을 새 주·백업 모델로 수정해야 함 |
| 저장소 배포 기반 | Yocto/Poky·Xen 구성 | Raspberry Pi OS 데스크톱 구성 및 캡처 도구가 설치됐다고 가정할 수 없음 |

재현 명령(dom0 안에서):

```sh
uname -m
test -d /dev/dri && ls /dev/dri
command -v weston chromium chromium-browser cog gst-launch-1.0 ffmpeg wayvnc
```

두 번째/세 번째 명령의 비정상 종료는 현재 장치/도구 부재를 뜻한다. 기존 QEMU 콘솔 동작의 실패를 뜻하지 않는다. 이 확인으로 HDMI·인코딩·원격 미러링을 시험했다고 주장하지 않는다.

## 이후 시험 항목

| 환경 | 검증할 사항 | 통과 증거 |
| --- | --- | --- |
| QEMU/단위 시뮬레이션 | 상태 수집, UUID·세대, 지연/역순 처리, 주·백업/모드 분리, 출력 결정/ACK, 외부 콘솔 회귀 | 입력 상태·기대 화면 결정·실제 결정 비교, 기존 콘솔 수락 시험 |
| 가상 렌더러/테스트 PC | overview/detail 화면, 소스 없는 상태 대체 화면, 전환 revision | 렌더 화면/스크린샷과 ACK 기록. 실제 HDMI로 명명하지 않음 |
| Raspberry Pi 실보드 + 모니터 2대 | DRM/EDID 매핑, 서로 다른 두 화면, 재연결, 로컬 재시작·복구 | 커넥터/모드 기록, 두 실모니터 영상, 적용 revision |
| 실보드 + 시연 PC | 실제 출력 소스 캡처 일치, 출력별 스트림 구분, 지연/FPS/CPU/메모리/대역폭 | 동일 타임코드의 로컬·원격 비교 영상과 측정 로그 |
| 장애 주입 | 원격 네트워크/송신 프로세스 중단, 상태 소스 단절, VM 정지/재실행 | 원격 장애 중 로컬 두 출력 유지, stale 표시, 이전 세대 프레임 미노출 |

## 남은 결정

- 기본 레이아웃은 2026-10-21 사용자 확인으로 모니터 1 전체 VM 상태 / 모니터 2 선택 VM 상세로 확정했다. 설치 시 물리 커넥터 매핑은 별도 확인한다.
- 활성 구동 VM·주행 모드의 권위 발행자와 실행 세대 식별 방식은 제어 담당과 맞춘다.
- 실보드 이미지, 두 모니터 지원 모드, 컴포지터·웹 렌더러·출력 캡처/인코딩 조합을 확인한다.
- 원격 프로토콜/인증 및 720p·10 FPS 등 초기 측정 목표는 실측 후 확정한다.

본 단계 산출물은 [설계 초안](../design/hdmi-display-plan.md)과 [계약 초안](../contracts/display-pipeline-v1.md)이다. 위 미결정 사항 때문에 Jira 250 전체 완료 조건을 충족했다고 처리하지 않는다.
