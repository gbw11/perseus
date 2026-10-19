# 플랫폼 폴더 구조

Yocto·Xen 빌드, 부팅 환경, 게스트 템플릿, 최초 설치 이미지 구성을 관리한다.

| 폴더 | 역할 |
| --- | --- |
| `boot/` | 보드·실행 환경별 부팅 구성 영역 |
| `boot/rpi4/` | Raspberry Pi 4 부팅 환경용 예약 폴더 |
| `boot/qemu/` | QEMU 부팅 환경용 예약 폴더 |
| `first-install/` | 최초 설치용 공통 SD 이미지 조립과 입력·산출물 검증 |
| `guests/` | 게스트 OS의 템플릿 구성과 준비 코드 |
| `guests/common/` | Linux 템플릿·인스턴스의 공통 설정, 신원 관리, Xen 설정 생성 |
| `guests/debian/` | Debian 게스트의 Xen 실행 설정 템플릿 |
| `guests/ubuntu/` | Ubuntu 게스트의 Xen 실행 설정 템플릿 |
| `guests/linux/` | Linux 배포판별 템플릿 recipe와 적용 파일 |
| `guests/examples/` | Linux Xen 설정 생성용 오프라인 입력 예제 |
| `guests/zephyr/` | Zephyr Xen 펌웨어의 출처·번들 검증·게스트 설정 |
| `guests/zephyr/starter-app/` | 사용자 앱 개발을 시작하기 위한 기본 Zephyr 앱 |
| `network/` | 네트워크 설정용 예약 폴더 |
| `yocto/` | Yocto 빌드 설정, 외부 레이어 버전, 프로젝트 레이어 |
| `yocto/config/` | 빌드 환경에 적용할 공통 설정 |
| `yocto/meta-xenova/` | 이미지·부팅·네트워크·dom0 서비스를 정의하는 프로젝트 레이어 |
