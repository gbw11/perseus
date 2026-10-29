# MQTT 계정 운영·이미지 통합 검증

## 기준선과 변경 범위

2026-10-29 원 작업방의 HEAD `5ffdae662b7c1730e8a85395e69bf10b4d9433a2`와
그 위의 MQTT 부분 적용 후보를 기준으로 계정 운영 경로를 통합했다.
이 보고서와 함께 커밋한 소스가 검증 대상이다. 기존 미추적 문서
`docs/platform/drive-pipeline-architecture.md`, `recovery-scenarios.md`는 수정하지 않았다.

병우님의 backend `759c91689c31a23cd937bec70ab58a18263d8cbd` 이후 배포 도구를
최신 backend `3f6d7ffd7af442dd40577e89cb6dc5566dfcf48c`와 대조했다.
`check.sh`와 `listeners/dom0.conf`는 원본을 재사용했다.
`dom0-broker.sh`의 설치·계정 추가/삭제·목록 진입점은 공통 도구로 연결했다.
두 도구가 서로 다른 설정을 수정하는 독립 운영 경로는 유지하지 않는다.

현재 사용법의 원문은 [CLI 명세](../platform/cli-manager-spec.md#cmd-broker),
[브로커 운영 안내](../../platform/deploy/mosquitto/README.md),
[Bootstrap 계약](../../platform/guests/zephyr/telemetry/contract/CONTRACT.md)이다.
직전 [부분 적용 보고서](mqtt-tailscale-partial-20261028.md)의 보드·EC2 접속 결과는
그 당시 후보의 근거다. 아래 새 계정 관리·Bootstrap 후보는 보드에 적용하지 않았다.

## 구현과 보호 경계

| 책임 | 구현과 보장 |
|---|---|
| 정책 원본 | `platform/deploy/mosquitto/etc/policy.json`에서 계정·ACL 생성. 기본 서비스에 `interaction` 포함. 기존 `message-board` 계정도 보존 |
| 계정 운영 | `scripts/xenova_broker.py`의 initialize/add-account/remove-account/list/check/export-account/import-legacy. 변경·export는 기본 계획 모드, 명시적 `--execute`에서만 반영 |
| 영구 저장 | 실제 writable ext4 `/srv/xenova`를 확인. 세대별 묶음과 원자적 current 교체, writer 잠금·fsync·pending 복원. 기존 비밀번호와 해시 유지 |
| 혼합 방지 | 세대별 conf가 같은 세대의 passwd/ACL 절대 경로를 참조. 손실 흔적이 있으면 최초 빈 상태로 초기화하지 않음. 미확정 게시를 효과 없음으로 보고하지 않음 |
| 기존 자료 이행 | 기존 schema와 `/etc/mosquitto` 자료는 명시적 이행으로 복사·검증. 원본을 삭제하거나 비밀번호를 자동 재발급하지 않음 |
| 이미지 | `xenova-mqtt-broker` recipe가 도구·정책·서비스 설정을 포함. 기존 Mosquitto notify/watchdog 동작 유지. 내부 브리지와 실제 데이터 마운트만 준비 의존성으로 사용 |
| 인증 분리 | v1 drive는 UUID, v2는 `telemetry-UUID`. 공유 matcher와 신뢰된 Store channel로 확인. username 버퍼 47바이트. 서로 다른 VM·channel의 계정 거절 |
| 기존 receipt | 명시적 `--reconcile`에서만 과거 UUID 형식 v2 입력 허용. 원 receipt·실행·payload 해시·실제 Store 값 대조, 새 Bootstrap 쓰기 없음 |

Coordinator가 재사용하는 2인자 `credentials(path, vm_id)`는 동일 VM에 결합된
두 계정 형식을 읽는 경로다. CLI는 실제 channel을 전달하고 게스트도 신뢰된 channel을
검증한다. 이 파서가 준비·출력 권한을 부여하는 것은 아니다. Worker 제품 코드는 변경하지 않았다.

서비스·VM의 기존 v1 읽기/probes 쓰기는 보존하며 observer와 Telemetry 구독기는
읽기 전용이다. v2 발행자는 자기 VM의 다섯 stream만 쓸 수 있다.
reload 요청 성공, MQTT 인증 성공, 앱 준비, 출력 허가는 각각 다른 결과다.
계정 제거 뒤 기존 연결의 ACL 차단은 시험했지만 모든 기존 연결의 강제 종료를 보장하지 않는다.

Tailscale 서비스와 SSH 정책은 변경하지 않았다. 기존 Tailnet TCP Serve의
1883/8081→localhost 경로와 인증 상태 백업·복원 계약은 그대로다.
공통 이미지·RPM에는 실제 계정 비밀번호·Tailscale 상태를 넣지 않는다.

## 실행한 검증

원자료는 PC의 `C:/Users/SSAFY/Documents/Codex/2026-10-29/xenova-mqtt-account-integration/`에
보존했다. 아래 파일은 해당 디렉터리 기준이다.

| 시험 | 실제 결과와 원자료 |
|---|---|
| 파일 저장·운영 CLI | 계정 20건, recipe/CLI 계약 9건을 포함한 최종 관련 회귀 212건 통과, skip 없음. `BOUNDARIES_FINAL.json`, `boundaries-final.log` |
| 기존 플랫폼 경계 | 같은 212건에서 Telemetry·receipt 재확인·배포 payload·SSH·복구 기반·Spring 계약 fixture·Wi-Fi·Agent·SSH graph 확인. 상위 제품 종단 시험과 구별 |
| 실제 격리 broker | Mosquitto 2.0.18의 인증/ACL·운영 중 추가/삭제·독립 v1/v2·다른 VM 거절·read-only·기존 계정/해시 및 재시작 보존 7개 묶음 통과. `ACTUAL_BROKER.json` |
| 실제 C decoder | native_sim에서 정상 v1/v2와 channel·UUID·길이 오류 7건 통과. `NATIVE_DECODER_CASES.json` |
| 공통 주행 회귀 | 동일 native binary의 A/B 대칭 시나리오 각 13단계 통과. `final-native-pair-a.log`, `final-native-pair-b.log`. 실제 Gateway/MQTT/C 계산, 실행 바인딩·시리얼은 시험 경계 |
| Publisher 회귀 | 2 VM·5 stream·310수신, source 73/CPU 44 대조, CPU 25그룹 완전·부분 0. UTF-8·진행 정체·인증/ACL·재접속·broker 없는 종료·Publisher/프로세스 재시작 통과. `final-native-publisher-acceptance-r2.log` |
| ARM 후보 | 고정 Zephyr `684c9e8f32e4373a21098559f748f06915f950c9`/SDK 1.0.0에서 공통 drive·Network Starter 통과. ELF/DTS·16MiB·GICv2·1vCPU·번들·netfront 송신 함수 stack 48바이트/한도 256 확인. `final-arm-drive.json`, `final-arm-starter-r2.json`, `FINAL_BUILD_ARTIFACTS.json` |
| RPM/QA | 별도 TOPDIR에서 `bitbake -c package_write_rpm xenova-mqtt-broker xenova-platform`, `bitbake -c package_qa xenova-mqtt-broker xenova-platform` 통과. 실제 두 RPM을 풀어 소스 6개 바이트·RDEPENDS·인증 상태 부재 확인. `PACKAGE_VERIFICATION.json` |

212건 실행 명령은 frozen source에서 다음과 같으며 필요한 기존 시험 의존성과
실제 systemd 259.5 verifier의 경로만 지정했다.

```sh
python3 -B -m unittest \
  test_xenova_broker_accounts test_xenova_mqtt_broker \
  test_xenova_telemetry test_xenova_reconciliation \
  test_xenova_deployment_payloads test_xenova_dom0_ssh \
  test_xenova_recovery_foundation test_xenova_spring_contract \
  test_xenova_wifi test_xenova_guest_agent test_xenova_guest_ssh_graph -v
```

실제 broker 시험 진입점은 `platform/tests/probe_xenova_broker_accounts.py`,
native 시험은 기존 `platform/apps/drive/tests/run_native_pair.py`와
`platform/guests/zephyr/telemetry/tests/run_native_acceptance.py`를 사용했다.
각 실행의 고정 입력·명령·로그는 위 원자료에 남겼다. 시험용 합성 인증정보는 배포물에 포함하지 않는다.

초기 전체 970건 실행은 통과로 판정하지 않았다. systemd verifier 버전과 축소 snapshot의
backend fixture 누락으로 실패했고, 실제 259.5 verifier와 원 backend 파일을 제공한 뒤
영향받는 suite를 포함해 위 212건을 다시 실행했다. ARM Starter의 git metadata 경계와
native PYTHONPATH 준비 실패도 원 실패 로그를 보존하고 수정된 실행을 따로 기록했다.
native Publisher에서 발견한 남은 UUID-only 검사는 공유 matcher로 수정하고
관련 native·ARM·RPM 후보를 갱신했다.

## 최종 산출물과 빌드 인계

WSL 최종 루트는 `/home/ssafy/xenova-mqtt-account-integration-20261029/final/`이다.

| 산출물 | SHA-256 |
|---|---|
| `packages/xenova-mqtt-broker-1.0-r0.noarch.rpm` | `593c667847ef093c4d2daec25037f66d492c6666d788a69751d29c60d2edc877` |
| `packages/xenova-platform-1.0-r0.cortexa72.rpm` | `9b0892a098e0fdad9fd90f2400eeb9fda6f23cd09dcabc9a99615ebeb5100c99` |
| `arm-drive/firmware/zephyr.bin` | `c44ac0aa1ea52e658a51a94466cc785f98ff61dbfe2fc78f0d082c1793384956` |
| `arm-starter-r2/firmware/zephyr.bin` | `43575fcf475710cac0b0efb7cc79d754cae8f68f9b91672a0e4ef79881816057` |

전체 목록은 `FINAL_BUILD_ARTIFACTS.json`, RPM 설치 바이트는 `PACKAGE_VERIFICATION.json`이다.
부분 패키징 TOPDIR는 `/home/ssafy/xenova-mqtt-account-integration-20261029/package-image-config`이다.
기존 이미지 빌드의 실제 local.conf/bblayers와 hash-equivalence cache를 별도 경로로 복제하여
기존 다운로드·sstate를 재사용했다. 원 빌드 설정·작업·캐시를 수정하거나 중단하지 않았다.
초기 별도 설정의 cache 불일치와 `bitbake -b` 도구 오류는 실패 기록으로 남겼다.

빌드 작업방은 자기 미커밋 변경을 보존하며 소스 브랜치를 병합하고, 정상 BitBake 의존성으로
증분 package/rootfs/WIC를 다시 확인해야 한다. RPM 수동 복사만으로 이미지 포함을 판정하지 않는다.
기존 firmware lock과 템플릿 경로는 빌드 작업방의 최신 변경을 기준으로 조정해야 하며
A/B에는 같은 최종 번들, 신원·인증·CPU 설정은 각 VM의 것을 유지한다.

## 수락 범위와 남은 항목

소스·로컬 오류 주입·실제 격리 broker/native·ARM 후보·부분 RPM/QA까지 통과했다.
새 후보의 전체 이미지 조립·WIC 포함·실보드 설치·실제 Xen Bootstrap/receipt·실제
Tailnet 소비자 재배포·장치 출력·전원/SD 시험은 여기서 수락하지 않았다.
이 작업은 보드·운영 broker·기본 VM·CPU 정책·Wi-Fi/SSH·Tailscale 상태를 변경하지 않았다.
실제 계정 이행 전에는 기존 자료 백업과 계획 검토가 필요하며,
계정 추가/삭제 후 해당 소비자에 자기 계정만 보안 경로로 전달해야 한다.
