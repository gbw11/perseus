# Jira 27 분석 및 검증 기록

- 분석 기준: `origin/platform`의 `6ec2f1fec7d58a3975cdb5824fd98ec20d2f88e8`.
- 작업 브랜치: `feat/platform/S15P31E110-27-first-boot`.
- 기존 28번 작업 폴더의 미커밋 QEMU 파일을 옮기거나 수정하지 않고 별도 worktree에서 작업.
- production 코드·템플릿·기본 VM 구성 변경 없음.

## 기존 코드 검사

일반 VM 생성 시 `prepare_rootfs.prepare_instance()`가 호스트명·machine-id·UUID 기반 instance-id·고유 SSH 키를 오프라인 디스크에 넣는다.
DHCP 클라이언트와 SSH 서비스 활성화는 템플릿 준비 단계에 있다.
공통 SD용 seed는 별도 `guest_identity.ensure()`로 첫 부팅에 키와 식별자를 생성하고, 이후 부팅은 기존 값을 검사한다.

현행 Linux 관리 API는 `name`을 hostname으로 사용하며, `role` 또는 에이전트 설정 입력을 받지 않는다.
에이전트 설치·등록과 네트워크까지 포함하는 전체 provisioning 완료/실패 상태는 공통 코드에 구현돼 있지 않다.
따라서 identity 완료 표식을 Jira 27의 전체 완료 표식으로 해석하면 안 된다.

## 실행한 자동 테스트

WSL Ubuntu에서 다음 기존 테스트를 실행했다. 임시 rootfs·모의 명령을 사용하는 코드 테스트이며 실보드 테스트가 아니다.

```bash
python3 -m unittest discover -s tests/platform -p test_xenova_first_install.py -q
# Ran 45 tests ... OK
python3 -m unittest discover -s tests/platform -p test_xenova_seed_ssh.py -q
# Ran 9 tests ... OK
```

포함 범위: seed 식별자 생성, 중단 후 재시도, 재실행 시 암호·키 보존, 키 유실/손상 시 실패, SSH 정책 확인과 임시 마운트 정리.

## 실제 QEMU 통합 검증

검증 도구는 `tests/platform/qemu_first_boot_probe.py`. 결과 JSON은 WSL lab의 `logs/jira27-<token>.json`에 보관한다.
최종 증거는 같은 폴더의 `jira27-qemu-evidence.json`이다.

| 검증 | 결과 |
| --- | --- |
| 새 Debian VM의 hostname·UUID·지정 IP·SSH | 통과 |
| 기존 Debian과 machine-id·instance-id·SSH 공개키 지문 독립 | 통과 |
| 실제 재부팅: boot ID 변경 | 통과 |
| 재부팅 후 hostname·UUID·IP·machine-id·SSH 키·사용자 파일 유지 | 통과 |
| 설정 검증 합계 | 12/12 통과 (`provisioning_checks_passed=true`) |
| 정상 종료·보관 | 통과, `RETIRED`, 강제 종료·영구 삭제 없음 |
| 기존 세 VM 등록 정보 유지 | 통과 (`original_records_unchanged=true`) |
| 재부팅 제어 API | 오류 반환, 아래 참조 |
| 전체 통합 판정 | 실패 유지 (`passed=false`) |

검증 VM은 `firstboot-b868826f61`, UUID `def09f37-686d-4315-9432-85ffad71f8d4`, IP `192.168.77.31`이다.
boot ID는 `850baa30-7904-4729-af49-76828d24c09f`에서 `d7d14bc1-adff-44f5-a1fb-e81fc0b08711`로 바뀌었다.
검증 종료 후 `xl list`에서 기존 `zephyr-dev`, `zephyr-backup`, `debian-dev`만 실행 중임을 확인했다.

### P1/P3에 전달할 재부팅 관찰 오류

관리 계층 `Manager.call('reboot', {'vm_id': '<검증 UUID>', 'force': False}, execute=True)` 호출 후
실제 Xen 도메인은 재부팅됐지만 전환 중 `xl` 설정 조회에서 다음 오류가 발생했다.

```text
ARTIFACT_INVALID
xl omitted live domain configuration; writable-disk safety cannot be checked
```

즉시 시도한 정상 종료도 같은 오류로 거부됐다.
이후 안정화된 VM에 SSH 접속하여 이전 첫 부팅 스냅샷과 비교했고, 재부팅을 다시 요청하지 않았다.
이 관찰 실행은 제어 API 오류를 `control_plane_reboot_error`에 보존하므로 전체 통합을 성공으로 바꾸지 않는다.
그 뒤 정상 종료와 보관 API는 성공했다.
production 안전 검사를 약화하거나 재부팅 코드를 수정하지 않았으며, 재현 로그를 P1/P3가 검토할 수 있게 남겼다.
해당 팀원에게 메시지를 보내지는 않았다.

첫 실행(`13b1f11576`)은 새 VM 생성 중 클라이언트의 300초 제한에 도달했다.
서버 생성 작업은 이후에도 한동안 실행됐다. 같은 멱등키의 재호출은 처음에는 `OPERATION_IN_PROGRESS`, 이후 부분 준비 디스크가 남아 `RECOVERY_REQUIRED`를 반환했다.
시간 제한 이후 최초 서버 작업이 끝난 직접 원인은 확보된 출력만으로 확정하지 않는다.
이 실패를 숨기거나 부분 디스크를 지우지 않았으며, 최종 검증과 별도로 기록한다.

- 실패 결과: `logs/jira27-13b1f11576.json`.
- 재호출 결과: `logs/jira27-13b1f11576-resume-fe0ec6.json`, `logs/jira27-13b1f11576-resume-51521d.json`.
- 작업 ID: `a3466a71-a30a-454a-b73d-b28d3b469bb4`.
- 보존 경로: dom0 `/srv/xenova/instances/.operation-a3466a71-a30a-454a-b73d-b28d3b469bb4/`.
- 등록/실행된 검증 VM은 아니며, 이 작업이 예약한 `.30` IP는 재사용하지 않았다.

검증 도구 대기 시간을 1800초로 늘리고, 새 검증 실행은 `.31`을 사용했다.
이는 QEMU 검증 도구 조정이며 production 생성 규약을 우회하거나 변경한 것이 아니다.

두 번째 실행(`b868826f61`)은 시간 제한 없이도 `FileNotFoundError`로 실패했다.
직후 검사에서 QEMU dom0에 `tune2fs`와 `e2fsprogs` 패키지가 없음을 확인했다.
production `xenova-platform_1.0.bb`에는 `e2fsprogs-tune2fs`가 이미 RDEPENDS에 있어,
이번 보완은 격리된 Debian QEMU dom0에만 `e2fsprogs`(1.47.2-3+b12)와 의존 패키지 `libss2`를 설치한 것이다.
검증 도구에는 필수 명령 사전 검사도 추가했다.

실패 작업 `881aaa23-006c-4bc2-a435-db0fe9b8743b`의 잠금을 얻고, 검증용 이름·미등록·root 마운트 경로 부재를 확인한 뒤
부분 디스크를 `.retained-jira27-881aaa23-006c-4bc2-a435-db0fe9b8743b`에 **삭제 없이 보관**했다.
보관 전 작업 JSON과 파일 목록은 `logs/jira27-quarantine-b868826f61.json`에 저장했다.
그 뒤 원래 요청의 동일 멱등키로 새 작업 공간에서 재시도했다. production의 부분 복사본 보호 규칙은 변경하지 않았다.

이 검증은 Debian 일반 인스턴스 경로를 대상으로 한다. 에이전트 준비, seed 실부팅, Ubuntu, Zephyr 앱 설정, Raspberry Pi/Yocto 수락은 포함하지 않는다.

## 남은 완료 조건

`platform/guests/common/README.md`에 기존 동작과 후속 인계 항목을 정리했다.
특히 P3의 에이전트 산출물·설정·서비스 규약과 ProvisioningSpec 전달 방법을 정한 뒤 통합해야 한다.
Jira 상태는 변경하지 않았다.
