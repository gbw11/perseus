# 페르세우스 - Raspberry Pi 4 + Xen 멀티 OS 관리 플랫폼

## Yocto / Xen 문서 읽는 순서

GitLab 프로젝트와 그 안의 `platform/yocto/layers.lock`을 기준으로 새 Linux 작업 경로에 환경을 구성합니다.

| 목적 | 문서 |
| --- | --- |
| 처음 환경 준비·최신 프로젝트 갱신·선택적 이미지 빌드·domU 통합 개발 | [재구성 및 domU 인계](docs/build-guides/yocto-rpi4-xen-reproduce.md) |
| 커널·SD 이미지 검사와 실보드 Xen/dom0·domU 검증 | [최소 bring-up 검증](docs/build-guides/yocto-rpi4-xen-minimal-bringup.md) |
| 이전 환경·성공 로그·검증 한계 조회 | [2026-10-15 감사 기록](docs/build-guides/yocto-rpi4-xen-audit-20261015.md) |

환경만 준비하려면 재구성 문서의 1~2절을 따르고 `setup-yocto.sh --verify` 완료 후 멈추면 됩니다. 감사 기록은 당시의 관측 기록이며 현재 실행 절차는 재구성 문서를 따릅니다.
