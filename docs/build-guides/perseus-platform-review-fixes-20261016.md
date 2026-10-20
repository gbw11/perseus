# Perseus 독립 검토 결함 3항 보완 — 2026-10-16

## 범위와 기준

현재 설계를 유지하고 static unit 소유권, Xen netback VIF 판정, 오프라인 rootfs의 심볼릭 링크 처리만 보완했다. 시작 브랜치는 `feat/platform/yocto-build-setup`, HEAD는 `7152ce47859779c888119e1bd15c8a0d85a4a3bc`다. 기존 미커밋 작업은 유지했다. 이번 수정 직전 사본과 비교한 diff·실행 로그는 `/home/ssafy/perseus-platform-review-fixes-20261016-114121`에 보존했다. 기존 검토 압축파일은 수정하지 않았다.

## 확인된 원인과 수정

| 항목 | 원인/근거 | 변경 |
| --- | --- | --- |
| static unit | 고정 OE-core `systemd.bbclass`는 SYSTEMD_SERVICE 목록을 패키지에 추가하지만 Requires 관계를 소유권으로 따라가지 않는다. `perseus-xen-ready.service`는 설치 루프에만 있었다. | `perseus-platform_1.0.bb`의 FILES에 설치 unit 4개를 명시. SYSTEMD_SERVICE/enable 목록은 기존 3개를 유지한다. |
| VIF 거부 | 고정 커널 `xen-netback/interface.c`의 SET_NETDEV_DEV와 xenbus.c의 xenvif_alloc 호출로 정상 VIF에도 부모 device 링크가 생긴다. 드라이버는 xen-backend 버스의 vif다. | check_network는 vif 이름과 device/subsystem → xen-backend, device/driver → xen-backend/drivers/vif 바인딩을 확인한다. 물리 NIC·frontend·예상 외 이름·바인딩 누락은 거부한다. |
| 서비스 링크 | Path.resolve는 게스트의 `/usr/...` 링크를 호스트 기준으로 해석했다. enable은 교체하려는 마지막 링크까지 먼저 따라갔다. mask/getty/dbus의 직접 경로 처리도 부모 링크를 우회했다. | inside가 절대 링크를 게스트 /로 재기준화하고 상대 링크를 순서대로 처리한다. root 밖 ..와 40회를 넘는 링크 탐색은 거부한다. 교체할 마지막 링크는 따라가지 않는 replace_link로 서비스·getty·dbus 링크를 교체한다. |

호스트 경로를 가리키는 절대 링크도 게스트 루트 아래로 해석하며, 링크 교체는 호스트 대상 파일을 변경하지 않는다. 이 처리는 기존 생성 도구의 외부에서 동시에 수정하지 않는 오프라인 사본을 전제로 한다. 최초 생성/인스턴스 재사용/인증/Agent 구현 범위는 바꾸지 않았다. 런타임 의존성과 고정 레이어 버전도 변경하지 않았다.

## 변경 파일

- `platform/yocto/meta-perseus/recipes-core/perseus-platform/perseus-platform_1.0.bb`
- `platform/yocto/meta-perseus/recipes-core/perseus-platform/files/perseus_vm.py`
- `platform/guests/common/prepare_rootfs.py`
- `tests/platform/test_perseus_platform.py`: 기존 정상 VIF 검사를 실제 구조 fixture로 교체. 기존 34개 검사 이름 유지.
- `tests/platform/perseus_sysfs_fixture.py`: class/net, devices, backend bus/driver, bridge brif의 상대 링크 fixture.
- `tests/platform/test_perseus_review_fixes.py`: 신규 18개 검사.
- `tests/platform/check_perseus_metadata.py`: 설치 unit 전체를 해석된 PACKAGES/FILES와 대조하고 QA 비활성화도 검사.
- `scripts/check-perseus-platform.sh`: 기존 검사와 신규 검사를 함께 수집. 관련 없는 test_yocto_setup.py는 실행하지 않음.
- 이 보완 보고서.

## 실행 결과

| 실제 실행 명령/검사 | 결과 | 보존 로그 |
| --- | --- | --- |
| 수정 전 `python3 -B -m unittest discover -s tests/platform -p 'test_perseus*.py' -v` | 52개 중 failure 2, error 12로 결함 재현. 소유권 1건, 정상 VIF 거부/미확인 포트 허용, 절대/부모 서비스 링크 오류 포함. | tests-before-fix.log / exit-code 1 |
| 수정 후 `bash scripts/check-perseus-platform.sh` | 기존 34 + 신규 18 = 52개 통과, 기존 회귀 17개 통과, 셸 문법·diff 검사 통과. | tests-after-fix.log / exit-code 0 |
| `bash scripts/check-perseus-platform.sh --metadata` | 52 + 17 통과. 분리된 bitbake -p 및 5개 -e 통과. 3213 recipes, 5503 targets, 173 skipped, 0 errors. WSL VHDX 권고 경고 1건. | tests-and-metadata.log / exit-code 0, metadata/ |
| unit ownership·QA 정책 검사 | 설치 4개 unit 모두 perseus-platform 소유, static unit enable 제외, installed-vs-shipped ERROR_QA 유지 및 INSANE_SKIP 미지정. | tests-and-metadata.log |
| Python AST 문법 검사 | 변경 Python 6개 통과. bytecode 생성하지 않음. | python-syntax.log |
| 범위 밖 소스 보존 | 기존 검토 압축 내 대상 56개가 byte 단위 동일. Git 인덱스·HEAD 불변. | unrelated-source-preservation.log, final-integrity.json |

신규 18개는 unit 소유권 1개, 정상/물리/다른 Xen 버스/바인딩 누락/예상 외 포트 판정 5개, 첫 VM 실행 후 두 번째 시작·기존 VM start 재호출·DHCP restart의 실제 check-network ExecStartPre 진입점 3개, 절대/상대 서비스 링크·부모 링크·usrmerge·호스트 대상 보존·탈출/순환·getty/dbus 9개다. 회귀 fixture는 일반 파일·심볼릭 링크와 명령 대역만 사용한다. 실제 systemd/dnsmasq/Xen 및 VM 디스크를 구동한 검사가 아니다.

기존 검사가 놓친 부분은 unit 순서만 검사하고 FILES를 대조하지 않은 점, VIF fixture에 class/net/device 링크가 없었던 점, 게스트 fixture에 기존 absolute 서비스 링크가 없었던 점이다. 이번 검사에 해당 구조를 포함했다.

## 남은 검증과 제한

- **실제 installed-vs-shipped QA 통과는 아직 주장하지 않는다.** package/rootfs/image 태스크를 실행하지 않았으므로 이번 결과는 해석된 install/FILES의 정적 소유권 검사다. 다음 승인 빌드에서 실제 패키징 QA와 rootfs의 unit 4개 존재를 확인해야 한다.
- 실보드에서 현재 고정 커널의 VIF subsystem/driver 링크를 기록하고 두 VM 연속 기동·기존 VM start 재호출·DHCP restart를 확인해야 한다.
- 실제 Ubuntu/Debian 원본의 absolute/relative 서비스 링크를 가진 rootfs에 생성 도구를 적용하고 첫 부팅/인증/데이터 보존을 확인해야 한다.
- 이미지 빌드·템플릿/인스턴스 생성·SD 접근/변경·git add/commit/push는 하지 않았다.

`review-fixes.patch`는 Git HEAD 전체 diff가 아니라 **이번 수정 직전의 미커밋 소스**에 대한 이번 9개 파일의 차이다. 수정 전 원문은 before/, 수정 후 원문은 after/에 있다. `git-diff-HEAD.patch`와 Git 상태는 전체 작업 트리의 별도 문맥 자료다.
