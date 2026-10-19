# Jira 28: Linux domU xl 템플릿 인계

## 상태

기준은 2026-10-19에 기능 브랜치로 병합한 `origin/platform`의 `77b6c39`다.
Ubuntu/Debian용 `.cfg` 파일, 공통 렌더러, 기존 실행 경로 연결, Yocto 설치 항목,
예제와 자동 테스트를 작성했다. 별도 VM 생성·등록 시스템을 추가하지 않았다.
이번 변경으로 재빌드한 이미지나 실보드에서의 부팅 성공은 아직 확인하지 않았다.

## 무엇을 바꿨는가

이전에는 `xenova_vm.Platform.config()` 안에 Linux 설정 문자열이 들어 있었다.
현재는 아래 경로로 설정을 만들고 기존 `xl create` 실행으로 이어진다.

```text
Platform.start(record)
  → 기존 신원·파일·부팅 자산 검증
  → Platform.config(record)
  → xen_config.render_config(record, instance_root=...)
  → Ubuntu 또는 Debian의 xen.cfg.in
  → /run/xenova/<id>.cfg
  → 기존 xl create
```

Ubuntu/Debian의 부팅 항목, 이름, UUID, MAC, 폴더용 ID와 종료 정책을 유지했다.
Zephyr는 기존 `xenova_zephyr.config()`를 계속 사용한다.
Linux 템플릿이 없거나 변수 치환에 실패하면 `PlatformError`로 중단하며,
이전 문자열 설정으로 우회해서 실행하지 않는다.

## P2 산출물

| 파일 | 역할 |
|---|---|
| platform/guests/ubuntu/xen.cfg.in | Ubuntu Xen 설정 양식 |
| platform/guests/debian/xen.cfg.in | Debian Xen 설정 양식 |
| platform/guests/common/xen_config.py | 검증된 내부 정보를 설정 문자열로 변환 |
| platform/guests/examples/*-cfg-spec.json | 기존 등록 구조에 맞춘 예제 입력 |
| tests/platform/test_xen_config.py | 문자열 계약, 기존 시작 흐름, 패키지 설치 연결 검증 |

## 변수 명세와 책임

렌더러 입력은 HTTP 요청이 아니라 **이미 선택·준비된 인스턴스 레코드**다.
`render_config(record, instance_root=...)`가 cfg 문자열을 반환한다.

| cfg 변수 | 값의 출처 | 규칙 / 의미 |
|---|---|---|
| xen_name | record.name | 기존 Xen 이름 유지. 소문자로 시작, 소문자·숫자·하이픈, 최대 40자 |
| vm_id | record.uuid | Xen uuid에 그대로 기록. P3가 API vm_id를 이 값으로 연결 |
| vcpus | record.vcpus | 기존 플랫폼 내부 범위 1~4, 정수 |
| memory_mib | record.memory_mib | 기존 플랫폼 내부 범위 128~8192 MiB, 정수 |
| kernel_path | instance_root / record.id / Image | 기존 인스턴스의 커널 |
| initrd_path | instance_root / record.id / initrd | 기존 인스턴스의 initramfs |
| disk_path | instance_root / record.id / root.raw | VM별 독립 쓰기 디스크 |
| mac | record.mac | 기존 플랫폼의 02:xx:xx:xx:xx:xx 형식, 중복 배정 금지 |
| bridge_name | 플랫폼 고정값 xenbr0 | 기존 브리지 설정 유지 |

`id`는 폴더 경로를 만드는 내부 식별자이고, `uuid`는 API VM 식별과 연결하는 UUID다.
둘이 같다고 가정하지 않는다. `name`도 현재 레코드에서 가져오며 `xenova-<UUID>`로 바꾸지 않는다.
렌더러는 UUID·MAC·IP·이름을 새로 발급하거나 기존 디스크를 옮기지 않는다.

## OS 선택과 버전

| OS | 현재 플랫폼 template_id | 기본 번들 버전 | 기존 source template |
|---|---|---|---|
| Ubuntu | ubuntu-24.04-arm64 | 1.0.0 | ubuntu-24.04-20260926 |
| Debian | debian-13-arm64 | 1.0.0 | debian-13-20261015 |

근거는 `platform/guests/default-templates.lock.json`과 기존 관리 카탈로그다.
`24.04`와 `13`은 OS 릴리스이며 번들 배포 버전 `1.0.0`과 구분한다.
OS별 cfg 선택은 공개 template_id로 한다. 기존 레코드에 이 필드가 없으면 위 두
source template에 한해서 OS를 선택하며 ID나 배포 버전을 추측해 레코드에 추가하지 않는다.
알 수 없는 ID, OS와 source가 충돌하는 조합, Alpine·Zephyr 입력은 Linux 렌더러에서 거부한다.

실제 버전 존재 여부·manifest·이미지 선택은 기존 카탈로그와 이미지 준비 계층의 책임이다.
버전은 cfg 자체의 Xen 항목이 아니며 렌더러가 버전별 이미지를 선택하거나 다운로드하지 않는다.
예제 UUID는 설명용이며 기존 VM 메타데이터에 덮어쓰면 안 된다.

## 부팅 항목의 근거

- 커널과 initrd는 해당 VM 폴더의 `Image`, `initrd`를 사용한다.
- `root.raw`는 파티션 없는 ext4 루트 디스크이며 `xvda`로 연결한다.
  `prepare_rootfs.py`는 `/etc/fstab`에도 `/dev/xvda / ext4 ...`를 쓴다.
  기존 `validate_record()`는 boot.json의 root_device가 `/dev/xvda`인지 확인한다.
- 커널 인자는 `console=hvc0 root=/dev/xvda rw rootwait net.ifnames=0`이다.
- `type=pvh`, qdisk, vif-bridge, xenbr0 설정은 기존 구현과 같다.
- 디스크 옵션은 `format=raw,vdev=xvda,access=rw,backendtype=qdisk,target=...`로
  `target=`을 마지막에 둔다.
- `on_poweroff=destroy`, `on_reboot=restart`, `on_crash=preserve`를 유지한다.
  `destroy`는 Xen 도메인 제거이며 디스크 파일 삭제가 아니다.

Ubuntu와 Debian의 현재 xl 항목은 같다. 실제 OS 차이는 선택한 이미지·커널·initrd에서 나온다.
이 코드상 일치와 변경 후 실제 보드 부팅 성공은 별개의 검증이다.

## Yocto 설치

`xenova-platform_1.0.bb`가 공통 모듈과 OS별 템플릿을 SRC_URI에 포함하고 설치한다.

```text
/usr/lib/xenova/xen_config.py
/usr/lib/xenova/xen-templates/ubuntu/xen.cfg.in
/usr/lib/xenova/xen-templates/debian/xen.cfg.in
```

실제 경로는 레시피의 `${libdir}/xenova`를 따른다.
Python 표준 `string.Template`용 `python3-stringold` 런타임 의존성을 추가했다.
테스트는 레시피의 실제 do_install 셸 본문을 임시 폴더에서 재생하고,
소스 경로 없이 설치된 모듈을 실행해 두 설치 템플릿이 사용되는지 확인한다.
이는 BitBake 파싱·패키지 빌드·새 WIC 검증을 대체하지 않는다.

## 보드 없이 실행하기

저장소 루트에서 실행한다. 화면에 cfg를 출력하며 VM·디스크·네트워크를 변경하지 않는다.

```sh
python3 -B platform/guests/common/xen_config.py platform/guests/examples/ubuntu-cfg-spec.json
python3 -B platform/guests/common/xen_config.py platform/guests/examples/debian-cfg-spec.json
python3 -B -m unittest discover -s tests/platform -p test_xen_config.py -v
```

CLI JSON의 최상위 키는 `instance`, `instance_root`다. HTTP 요청 스키마가 아니다.
실패 시 stderr에 ConfigError를 출력하고 종료 코드 2를 반환한다.
`instance_root`는 신뢰하는 dom0 내부 설정이다. 렌더러는 안전한 절대 경로 문자열을
검사하고, 실제 파일·해시·심볼릭 링크·중복 디스크 검사는 기존 플랫폼 호출자가 수행한다.

## 검증 결과

- 신규 테스트 13개 통과: 두 OS 설정, 기존 레코드 호환, 자원·경로·신원 입력 검증,
  VM별 설정 분리, 템플릿 누락 시 실행 차단, 기존 start 연결, 실제 설치 본문 재생.
- 관련 기존 테스트 165개 통과: 플랫폼, Linux·Zephyr 생명주기, 관리 계층,
  브랜드·패키징, 도메인 검증, 템플릿 워크플로, 종료 관찰.
- Xen 명령과 디스크는 fixture로 대체했다. 실제 Xen 파서·게스트 부팅 성공을 의미하지 않는다.

## P3 연결에서 남은 차이

사용자가 확정한 `uuid = vm_id`를 cfg에 그대로 반영하는 기능은 제공한다.
다만 현재 로컬 Manager create는 name을 받아 UUID를 자체 발급하고 등록 정보를 보유한다.
사용자 제공 API 계약은 Spring의 vm_id를 전달하고 dom0-manager를 무상태로 정의한다.
이 차이를 이번 cfg 분리에서 임의로 바꾸지 않았다. API 어댑터·생성·등록 정책은 P3 영역이다.

또한 API 계약의 메모리 상한은 1024 MiB이지만 기존 로컬 플랫폼은 8192 MiB다.
렌더러는 로컬 관리 기능을 축소하지 않고 기존 범위를 유지한다.
API 호출 경로의 1024 MiB 상한, 템플릿 최소 메모리, 실제 가용 자원 승인은 해당 계층에서 검사한다.
128 MiB는 코드의 하한이지 각 OS의 실측 최소 부팅 메모리가 아니다.
API 계약의 단순 OS ID와 현재 카탈로그 ID 사이의 변환도 P3와 명시적으로 연결해야 한다.

## 실기기 인계 체크

1. 이 변경을 포함한 BitBake 파싱·패키지 빌드와 이미지의 설치 파일 확인.
2. 기존 dom0의 아티팩트 검사 도구로 xl 파서 결과 대조.
3. Ubuntu·Debian 각각 부팅, 콘솔·디스크·네트워크 확인.
4. 충분한 자원에서 같은 템플릿 VM 두 대의 UUID·디스크·MAC 분리 확인.
5. 종료·재부팅 후 UUID와 디스크 유지 확인.

확정된 새 API로 외부 vm_id가 전달되는 종단 검증과 위 실기기 검증은 남아 있다.
따라서 Jira 28 전체 완료 여부는 이 결과와 실제 이슈의 완료 조건을 대조해 판단한다.

## 명칭 통일 후 전체 검증

2026-10-19에 프로젝트 표기를 Xenova로 통일한 뒤 전체 플랫폼 unittest 344개를 실행했다.
342개가 통과했고 2개는 WSL에 C 컴파일러 `cc`가 없어 실행 오류가 발생했다.
추가로 부팅·init·네트워크 회귀 17개, 실제 systemd 259.5 의존성 그래프와
셸 문법 검사를 통과했다. 신규 cfg 통합 테스트 13개는 전체 검사에 포함되어 통과했다.

명칭 변경의 산출물 해시와 재빌드 경계는
[명칭 통일 기록](../build-guides/xenova-rename-20261019.md)에 정리했다.
이번 결과는 새 이미지·템플릿·펌웨어 또는 실제 Xen에서의 부팅 검증을 대신하지 않는다.
