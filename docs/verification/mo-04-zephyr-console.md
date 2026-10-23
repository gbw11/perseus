# MO-04 Zephyr 양방향 콘솔 QEMU 검증

2026-10-21, `feat/platform/mo-04-web-console-proxy`에서 검증했다.
기존 Starter를 수정하지 않고 별도 `platform/guests/zephyr/console-test-app`을 빌드했다.
QEMU의 VM1 `zephyr-dev`, VM2 `zephyr-backup`에 잠시 적용해 시험하고 원래 Starter로 복구했다.
`platform` 브랜치 변경·머지·푸시·자동 커밋은 하지 않았다.

## 실제 확인한 경로

```text
Chrome xterm.js → Spring 공개 WebSocket → FastAPI → PTY → xl / xenconsole
  → Xen PV 입력 ring → Zephyr uart_poll_in() → 명령 처리 → printk() → 웹 화면
```

시험 앱은 `ping [token]`, `echo <text>`, `info`, `help`만 제공한다.
입력을 자동으로 에코하지 않는다. 전송하지 않은 `pong <고유 token>`과 `echo:` 접두사를
게스트에서 생성하므로 웹 화면에 타이핑한 내용만 보인 것을 성공으로 판단하지 않는다.
주행·상태 미러링·failover·Jetson 장애 시 수동 전환을 구현하거나 시험한 결과는 아니다.

## 결과

VM1과 VM2 모두 아래 항목이 통과했다.

| 항목 | 판정 |
| --- | --- |
| 고유 token을 넣은 `ping`에 정확한 `pong` 응답 | PASS |
| UTF-8 한글 문자 중간에서 WebSocket 입력을 두 조각으로 분할 후 `echo` | PASS |
| Delete(0x7f)로 ASCII 오타 수정 | PASS |
| CR, LF, CRLF 줄 종료 처리 | PASS |
| Ctrl+C로 미완성 명령 취소 후 새 명령 처리 | PASS |
| 알 수 없는 명령 거부 | PASS |
| 최대 127바이트 입력 처리 | PASS |
| 128바이트 초과 줄 전체 거부, 뒤에 붙은 명령 미실행, 다음 줄 정상 복구 | PASS |
| 정리 응답 직후 지연·재시도 없이 재접속 후 `ping` | PASS |

두 VM을 동시에 연결하고 한 VM에만 고유 입력을 보냈을 때 선택한 VM에서만 응답했다.
다른 VM은 응답 없이 유지됐다. 같은 작업을 반대 방향에서도 확인했다.
Chrome에서도 각각 `info`, 고유 `ping`, `echo BROWSER_INPUT_OK`를 직접 입력하고
실제 화면 출력을 확인했다. JavaScript 오류는 0개였다.

전체 원시 응답, 빌드 입력 해시, 펌웨어 적용·복구 결과는
[mo-04-zephyr-console-evidence.json](mo-04-zephyr-console-evidence.json)에 저장했다.
자격 증명·JWT·서비스 토큰은 포함하지 않는다.

## 빌드와 재현

기존 고정 Zephyr 4.4.0 commit `684c9e8f32e4373a21098559f748f06915f950c9`의
깨끗한 소스와 기존 aarch64 SDK를 사용했다. SDK archive SHA-256을 source.lock.json과
비교했다. xenvm / 1 vCPU / RAM 16MiB / GICv2 프로필로 실제 ARM64 이미지를 빌드하고
기존 `package-xenova-firmware.py`의 하드웨어·설정·번들 해시 검증을 통과했다.
Starter 카탈로그와 `starter.lock.json`은 변경하지 않았다.

```sh
# Starter README와 동일한 ZEPHYR_BASE/CROSS_COMPILE/PYTHONPATH/PATH를 설정한다.
cmake -S /absolute/repo/platform/guests/zephyr/console-test-app \
  -B /absolute/new-console-build -GNinja -DBOARD=xenvm \
  -DPython3_EXECUTABLE=/usr/bin/python3 -DWEST=/absolute/python-tools/bin/west
cmake --build /absolute/new-console-build -j 4

python3 -B platform/scripts/package-xenova-firmware.py \
  --build-dir /absolute/new-console-build/zephyr --license /absolute/zephyr/LICENSE \
  --version mo04-console-test-1.0.0 --app mo04-console-test \
  --source-revision <actual-source-hash> --build-id <unique-build-id> \
  --app-source /absolute/repo/platform/guests/zephyr/console-test-app \
  --output /absolute/new-console-bundle --execute
```

기존 Starter README의 명시적 stop/firmware-replace/start 절차를 격리 QEMU에서 사용한다.
교체 전 각 instance.json과 원래 활성 revision/manifest SHA-256을 저장한다.
시험 후 원래 revision 디렉터리를 `--bundle`로 지정해 동일한 관리 API로 복구한다.
보존한 펌웨어 파일이나 카탈로그를 덮어쓰지 않는다.

시험 펌웨어가 실행 중이고 Spring에 두 VM UUID가 등록돼 있을 때 실행한다.
표준 입력 JSON의 `access_token`에 실제 로그인 JWT를 전달한다. 토큰을 argv/로그/셸 이력에 넣지 않는다.
probe 자체는 펌웨어 교체나 VM 시작·종료를 수행하지 않는다.

```sh
python -B platform/services/dom0-manager/tests/probe_zephyr_console_live.py \
  --execute --base-url ws://127.0.0.1:18080 \
  --vm-id <VM1-UUID> --vm-id <VM2-UUID> --output <result.json>
```

## 복구와 관측 사항

시험 종료 후 두 Zephyr의 등록 정보 전체가 교체 전과 일치하고 원래 활성 펌웨어
revision으로 복구됐으며 다시 실행된 것을 확인했다. UUID·autostart·종료 정책을 유지했다.
Debian의 UUID와 domid는 그대로 유지됐다. 남아 있는 native console 프로세스는 0개다.
시험 앱 소스와 별도 불변 번들은 재시험을 위해 보존했다.

QEMU에서 기존 `xenova-vm stop --force`가 도메인을 실제 제거한 뒤에도
`replacement domain appeared during shutdown` 오류를 반환하는 경우가 있었다.
시험 절차는 반환 코드를 성공으로 고쳐 기록하지 않고, Xen을 재조회해 원래 UUID/name이
모두 사라진 경우에만 펌웨어 교체를 진행했다. 새 domid로 대체 VM이 나타나면 중단한다.
해당 관리 도구의 종료 판정은 이번 앱/콘솔 시험과 별도 문제로 남기고 기존 코드를 변경하지 않았다.

라즈베리파이 실보드의 실제 IRQ/DT/주변장치와 양방향 입출력은 아직 별도 검증 대상이다.
이 결과는 QEMU와 이 시험 앱의 콘솔 통신에 해당하며, 원래 Starter에 입력 기능이 생겼다는 뜻은 아니다.
