# Alpine 확장 지점

이번 설치 이미지의 활성 템플릿과 기본 VM은 Ubuntu·Debian이다. Alpine 원본·템플릿·커널·OpenRC 준비 adapter는 아직 추가하지 않았다. Alpine을 준비 완료로 표시하거나 Ubuntu/Debian adapter로 처리하지 않는다.

향후에는 공식 ARM64 원본·체크섬 고정, 게스트 커널/모듈/initramfs 검증, 최초 인증·네트워크·정상 종료·SSH 전 고유화를 구현해야 한다. 결과는 기존 `root.raw`, `Image`, `initrd`, `kernel.config`, `boot.json`, `template.json` bundle 계약을 사용한다. 활성화 전에 생성기와 첫 부팅의 배포판별 테스트를 추가한다.
