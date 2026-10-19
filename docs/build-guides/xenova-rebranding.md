# Perseus → Xenova 리브랜딩 범위

> 아래는 과거 리브랜딩 기록의 표기를 현재 명칭으로 정리한 참고 자료다. 아래 빌드·검증 수치는 2026-10-19 명칭 수정의 재검증 결과가 아니다. 이번 변경의 범위·검증·산출물 재생성 조건은 [명칭 통일 기록](xenova-rename-20261019.md)을 따른다.

기준 커밋은 `1a9a2a5793c035dd9ea6e89845f0eab33c0338dc`이며 시작 시 작업 트리는 깨끗했다.
실행 중인 BitBake·펌웨어 빌드가 없음을 확인했다. 소스·설정·회귀·문서만 변경하며
최종 이미지/템플릿/seed/Starter 생성, 조립, 배포, SD 쓰기, commit/push는 수행하지 않았다.

## 실제 연결 변경

- `meta-perseus` → `meta-xenova`: layer collection/pattern/priority/series,
  이미지·패키지 PN, IMAGE_INSTALL/RDEPENDS/SRC_URI, FILES/CONFFILES, install 목록을 함께 변경했다.
- `perseus-vm` → `xenova-vm`: Python 모듈·shell entrypoint·보조 CLI·환경변수
  `XENOVA_*`·메시지 접두사·등록/설치 marker를 연결했다. 별도 이전 명령 별칭은 없다.
- `perseus-data` → `xenova-data`: 조립기의 ext4 LABEL, 장치 탐색,
  `/run/xenova/data-device`와 `srv-xenova.mount`, `/srv/xenova` 및 레지스트리를 일치시켰다.
- 초기화→등록→autostart→정책 종료의 unit/Requires/After/Before/enable 목록,
  DHCP RuntimeDirectory와 config, guest identity/SSH Include·drop-in을 함께 변경했다.
  자동 시작 서비스는 하나이며 네트워크 격리나 VM별 종료 정책은 바꾸지 않았다.
- U-Boot 출력과 `xenova_debug`·`xenova_logargs`를 변경했다.
  Working FDT, MMC/PARTUUID·xen/Image/boot.scr, QEMU qdisk/VNC 및 Xen reset 동작은 유지했다.
- 기본 Starter 카탈로그는 `xenova-zephyr-starter-1.0.0`,
  앱은 `xenova-starter`다. 소스 안내는 `Xenova / Zephyr / Xen`으로 바꾸고
  실제 파일에서 lock SHA를 재계산했다. 새 binary/firmware manifest는 아직 없다.

VM 기본 ID·Linux template ID·UUID/MAC/IP·리소스·자동 시작·보드 종료 정책은 유지했다.
핵심 관리/초기화·Linux 준비 모듈 7개는 명칭 치환을 제외하면 이전 바이트와 같다.
조기 잠금·target 마지막 배치·UUID→domid 제어·실제 Xen 상태·종료 관측은 재설계하지 않았다.
dom0 SSH의 hard key gate·일반 서비스·generator mask도 유지했다.

## 회귀 결과와 한계

- `bash scripts/check-xenova-platform.sh --metadata`: exit 0.
- Python 280개(기존 플랫폼 267 + setup 8 + 새 리브랜딩 5), 부팅/init/network 17개 통과.
- 실제 systemd 259.5 로더와 전체 원본 fixture: SSH 순환/job 삭제 음성 대조,
  수정 후 정상과 재주입 순환 검출 통과. 기본 의존성은 평가하며 generator는 실행하지 않는다.
- 격리 BitBake `-p`: 3,213개 레시피, 5,503 targets, errors 0.
  9개 `-e` 및 WIC task 의존성 조회로 새 PN/SRC_URI·설치/소유권·SSH 모드·QEMU 옵션 확인.
  원본 기존 build conf SHA가 전후 일치했다. WSLv2 안내 경고 1개가 있었다.
- 실제 do_install을 격리 경로에서 실행하고 설치 모듈 9개 import·관리 help,
  별도 도구 help 9개·전체 Python compile·shell syntax 검사를 통과했다.
- 이전 자산 37개(이미지·Linux 템플릿·Starter 등)는 실제 SHA-256이 기존 기록과 일치했다.
  외부 Layer 6개는 고정 커밋 그대로이고 clean이다.
- 새로운 Xenova WIC·패키지 binary·Starter binary는 빌드하지 않았다.
  새 rootfs QA/enable/preset·실제 xl parser/QEMU backend·실보드 혼합 종료/reset/
  SSH 리스너·로그인·자동 시작·정적 Zephyr DTB/IRQ는 후속 검증이다.
  소스/모의 Xen·systemd 로더 검사를 실제 게스트 부팅으로 표시하지 않는다.

## 의도적으로 남긴 이전 명칭

| 위치 | 유지 이유 |
| --- | --- |
| `docs/build-guides/perseus-*`, 이전 `yocto-rpi4-xen-*` | 당시의 소스·실보드·산출물 기록. 주요 비날짜 문서에는 현재 가이드 안내만 추가하고 원문은 유지 |
| `tests/platform/results/*20261016.json` | 과거 결과 JSON을 재작성하지 않음 |
| `tests/platform/fixtures/**`의 원본 archive/provenance/README | 실제 WIC/게스트에서 추출된 출처·바이트·해시 보존 |
| dom0 fixture helper·브랜딩 음성 테스트·metadata의 이전 collection 부재 assertion | 과거 fixture를 새 시험 root로 명시적으로 투영하거나 이전 인터페이스가 없음을 검증 |
| Xen reset patch의 From/이메일 | 원래 패치 작성 출처이며 패치 전체 바이트를 유지 |
| 기존 crypt hash의 `perseus-dev` salt와 이를 확인하는 테스트 | 인증 해시의 일부이며 런타임 명칭/호환 별칭이 아님. 기본 비밀번호 계약 유지 |
| 현재 문서의 이전→신규 매핑·기존 번들 안내 | 보존 경계 설명 |

실행 경로·현재 파일명에 의도치 않은 이전 명칭은 검색되지 않았다.
소스 원격 주소·저장소명·작업 브랜치는 변경하지 않았다.
dom0-manager/Agent 등 다른 팀 모듈은 이 브랜치에 구현 코드 없이 placeholder만 있어
호출부를 수정할 수 없었다. 팀 연동에서는 새 CLI·경로·서비스·`XENOVA_*`를 사용해야 한다.

## 후속 산출물

새 dom0 타깃은 `bitbake xenova-xen-bringup-image`다.
과거 성공 build의 conf를 재사용해 덮어쓰지 않고 새 `build-rpi4-xenova`에서 준비한다.
외부 버전/캐시는 유지한다. 기존 준비 Linux 템플릿은 이전 guest 경로를 포함하므로,
기존 OS 입력으로 새 경로에 준비하고 새 seed를 만들어야 한다. OS 재다운로드는 필요하지 않다.
Starter는 같은 고정 소스/SDK로 별도 새 번들을 빌드·게시해야 한다.
기존 펌웨어를 이름만 바꾸거나 manifest 해시를 복사하지 않는다.
기존 VM/SD/원본 템플릿은 변경하지 않는다.

새 dom0·새 Xenova 준비 템플릿·새 Starter의 실측 해시를 넣어 최종 조립한다.
조립기의 소스 일치 gate는 새 이름으로 설치된 코드/유닛을 요구하므로 과거 dom0는 거부한다.
현재 명령과 후속 실보드 수락 절차는 [Xenova 플랫폼 가이드](xenova-platform.md)를 따른다.
전체 baseline·이름 변경 목록·잔존 행 분류·실행 로그·소스 묶음은 별도 검토 산출물에 보존한다.
