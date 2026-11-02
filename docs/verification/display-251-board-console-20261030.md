# 251 외부 웹 → 실보드 Debian 콘솔 — 2026-10-30

상태: **호환 보완 후보의 Debian 외부 웹 실보드 검증 통과**. 새 출력·연결 유지·실제
바쁨 후 재개·해제·재접속을 확인했다. 시험 후 보드는 원본으로 복원했으므로 정식 배포는
아직 아니다. VM1·VM2 외부 웹 등록/시험도 제외되어 251 전체 완료와 구분한다.
아래 초기 실패는 원인을 추적하고 보완한 과정의 기록이다.

## 시험 경로와 보존 범위

외부 웹 `https://k15e110.p.ssafy.io/dashboard/console` → 외부 Spring
`xenova-backend:13` → 기존 Tailscale 주소 `100.108.226.65:8081` → dom0-manager
→ `xl console` → Debian VM3.

- Debian UUID: `055226af-b876-4707-8b5d-99e7f33ad3d7`, domid 1.
- VM1·VM2는 domid 2·3으로 실행 중이다. 사용자가 DB 변경을 원하지 않아
  Spring에 명세를 추가하지 않았고, 이번 외부 웹 시험은 등록된 Debian에 한정했다.
- 기존 API·콘솔 기능은 active/enabled였다. 통신 설정·서비스 토큰을 변경하지 않았다.
- dom0 MemTotal 2,918,188KiB, MemAvailable 2,595,724KiB 관측.
  이번 시험을 위해 메모리·CPU·HDMI 설정을 변경하지 않았다.
- HDMI 키오스크는 조회 당시 inactive였으며 켜거나 끄지 않았다.

사용자가 Debian 재생성·이미지 교체를 확인했다. PC의 옛 SSH 키 기록과 달라
최초 인증은 중지했고, 검증된 dom0의 기존 known_hosts와 현재 게스트 공개키가
일치하는 것을 확인한 뒤 조회했다. SSH 인증 후 실제 Xen UUID도 대조했다.
기존 PC known_hosts나 SSH 정책을 덮어쓰지 않았다.

## 확인한 항목

| 항목 | 결과 |
|---|---|
| 외부 Spring 컨테이너 → 보드 health·실행 목록 | 기존 서비스 토큰으로 HTTP 200, 실행 VM 3대 |
| 외부 WSS upgrade·잘못된 시험 JWT | upgrade 성공 후 4401 종료 |
| Debian 로그인 대기 사전 확인 | UUID 일치, hvc0 프로세스 `agetty` |
| 외부 웹의 실제 콘솔 출력 | 부팅·audit 로그와 `debian-dev login:` 표시 |
| 첫 단절 뒤 수동 재연결 | 성공, 실제 출력 수신 |
| 같은 VM에 두 번째 연결 | 점유 안내로 거절, 첫 번째 화면은 연결 유지 |
| 연결 해제 버튼 | 연결 해제 안내로 복귀, 연결 버튼 다시 활성화 |
| 이후 다시 연결 | 성공했으나 유지 중 다시 단절 |
| 마지막 보드 점유 확인 | xl/xenconsole 프로세스 없음, VM 3대 UUID·domid 유지 |

로그인 정보·게스트 명령을 웹 터미널에 입력하지 않았다. 다만 배포된 기존 프론트는
Linux 연결 성공마다 Enter 1회를 자동 전송하고, xterm도 수신한 터미널 질의에
제어 응답을 자동 전송한다. 따라서 전체 시험의 입력이 0바이트였다고 기록하지 않는다.
웹에 제어 응답 문자열과 `Password:` 안내도 보였지만 암호는 입력하지 않았다.
게스트 로그인 상태를 다시 확인한 뒤 다음 연결을 진행했다.

## 재현된 단절과 상태 조회 오류

- 첫 연결: 보드 로그 11:53:44 accept/open 이후 11:53:53에
  `console unavailable for VM ...` 경고와 종료 확인.
- 세 번째 연결: PC에서 12:02:45 연결됨 확인, 47초 시점에도 연결됨.
  보드 로그 12:03:33에 같은 경고·종료, 이후 웹도 연결 오류 표시.
- Nginx의 실제 `/ws/` 설정은 read/send timeout 1시간이다.
  이를 근거 없이 기본 60초 timeout 문제로 처리하지 않았다.
- 같은 VM 단건 상태 API를 네 번 조회한 표본에서 200 세 번, 503
  `PlatformUnavailable` 한 번(약 0.054초)이 나왔다.
  뒤의 여덟 표본은 모두 200(약 0.41~1.60초)이었다.
- 콘솔 watcher는 VM 상태를 재확인하며 조회 실패 시 종료한다. 위 503과
  단절의 관련 가능성은 있지만, 현재 경고에는 오류 종류·플랫폼 코드가 없으므로
  이 단계에서는 정확한 원인을 단정하지 않았다. VM 검증을 생략하거나 오래된 상태로 연결을 유지하지 않았다.

## 진단 준비와 남은 확인

로컬에 예외 종류·제한된 플랫폼 오류 코드만 기록하는 진단 패치를 준비했다.
예외 message/detail·터미널 본문·토큰을 로그에 넣지 않고 종료 동작도 그대로다.
콘솔 테스트 38개와 하위 검증 9개를 통과했다.

적용 후보는 `/usr/lib/python3.14/site-packages/dom0_manager/api/console.py`이며
조회한 원본 SHA256은 `59909b45a533c394fc8ced0882a5467a0feaf2410a1e14f7195852b57f407398`이다.
API 서비스의 ExecStop·ExecStopPost·PartOf·BindsTo·PropagatesStopTo는 없었다.
사용자 허용 후 원본 백업·해시 검사로 임시 적용했고 API만 재시작했다.
12:16:05 외부 웹 연결 뒤 12:16:38에 같은 단절을 재현했다.

```text
error_type=PlatformUnavailable, platform_code=OPERATION_IN_PROGRESS
```

즉, 이번 단절은 상태 재확인에서 다른 관리 작업이 진행 중이라는 응답을 받아
현재 watcher가 종료한 경우다. 인증 실패나 VM 종료로 분류하지 않는다.
어떤 관리 요청이 정확히 잠금을 보유했는지까지 확정한 것은 아니다.

시험 뒤 임시 진단 코드를 위 원본 해시로 복원하고 API를 재시작했다.
인증된 health 200, API·Mosquitto·Tailscale·feed active, kiosk inactive(시험 전과 동일),
VM 3대 UUID·domid 유지, xl/xenconsole 잔여 없음과 임시 폴더 제거를 확인했다.
Spring DB·VM·펌웨어·MQTT·Tailscale·영구 설정은 변경하지 않았다.

1. 바쁜 상태 조회의 처리 정책을 정하고 로컬 회귀 검증 후 별도로 실보드 재시험한다.
   짧게 재확인하는 안이라면 미검증 동안 입출력을 전달하지 않고, 입력 재생·오래된
   관측 재사용을 하지 않으며 VM 변경 또는 제한 시간 초과 시 종료해야 한다.
2. 현재 즉시 종료 정책을 임의로 완화하거나 제어 검증을 생략하지 않았다.
3. 실제 원인에 대한 수정과 재시험 전에는 251 연결 안정성을 통과로 처리하지 않는다.
4. VM1·VM2 외부 웹 시험은 향후 명세 등록 합의 후 별도로 진행.

## 사용자 선택 후 준비한 보완안

사용자가 즉시 종료 대신 입출력 보류 후 짧은 재확인을 선택했다.
로컬 후보는 재시도 가능 OPERATION_IN_PROGRESS에만 최대 2초 유예를 적용하고,
같은 UUID·domid·제공된 실행 식별자를 새로 확인한 경우에만 전달을 재개한다.
입출력 조각은 현재 연결에서만 보류하며, 재확인 실패·실행 변경·시간 초과 때
취소하고 다음 연결에 재전송하지 않는다. 다른 오류의 종료 처리는 유지한다.

같은 실행 재개·VM/실행 변경 시 미전달·대기 시간 상한·명시적 종료·flock 해제와
재접속을 로컬에서 검증했다. API 계약·드라이버·제어 회귀를 포함해 194 tests와
9 subtests 통과. 바쁨 보류/재개 시 UUID만 기록하는 관측 로그를 추가한 뒤
관련 40 tests도 재통과했다. 터미널 본문은 기록하지 않는다.

수정 후보는 한 파일이며 기준 소스 해시가 위 보드 원본과 정확히 일치한다.
원본 해시가 다르면 덮어쓰지 않는 임시 적용·복원 스크립트를 준비했으며,
보완안의 별도 실보드 재시험 허용을 요청했다. 아직 후보의 실보드 통과로 처리하지 않는다.

### 첫 수정 후보의 구버전 호환 실패와 보완

사용자가 임시 적용·재시험·원복을 허용해 첫 후보를 적용했지만 유지 시험은 실패했다.
사용자의 초기 연결됨·출력 관측 이후 서버에서는 약 20초 뒤 종료됐고,
입력 없는 내부 WebSocket 시험도 약 23초 만에 종료됐다. 통과로 기록하지 않는다.
API 서비스 기록에서 `AttributeError: 'PlatformUnavailable' object has no attribute 'retryable'`
를 확인했다. 처리되지 않은 예외 때문에 종료 코드가 정상 1000으로 보였으므로
종료 코드만으로 시험 성공을 판단하지 않는다.

실제 보드 오류 객체의 생성자는 message/detail/resource/platform_code만 지원했고
Domain도 observation_id 필드 이전 버전이었다. 대상 console.py 파일 해시는 기준과
같았지만 나머지 설치 패키지까지 같은 버전은 아니었다. 첫 후보는 원본으로 복원했고
health 200·VM 유지·잔여 콘솔 없음·임시 폴더 제거를 확인했다.

보완안은 필드가 없는 구버전에서 정확한 OPERATION_IN_PROGRESS 코드만 제한적으로
재확인한다. 최신 객체의 명시적 false/null과 다른 원본 오류는 여전히 종료한다.
미제공 실행 식별자는 getattr으로 다루며, 구버전에서는 기존 UUID·domid 검증을 유지한다.
이전 버전에서 실행 식별자 검증이 새로 가능해졌다고 주장하지 않는다.
구버전 회귀를 포함한 최종 199 tests와 9 subtests가 통과했다.

동일하게 허용된 한 파일 임시 시험 범위에서 호환 보완 후보를 준비했다.
후보 SHA256은 `f154ebd80b993131b5d0575c7bde595838b50fc318b29c20f59817719e945fb2`이다.
새 실보드 시험 결과는 별도로 기록하고, 이번에도 시험 후 원본으로 복원한다.

### 호환 보완 후보의 실보드 결과

호환 보완 후보를 임시 적용하고 health 200·VM UUID/domid 유지를 확인했다.
이 턴의 인앱 브라우저 제어는 `browser.user is unavailable`로 실패했고 Chrome 제어도
허용되지 않아 조작하지 않았다. 사용자는 외부 웹의 초기 연결됨·출력 표시를 확인했지만,
이는 첫 후보에서의 관측이므로 호환 보완 후보의 최종 외부 웹 통과로 확대하지 않는다.

입력 없는 내부 API WebSocket 시험에서 호환 보완 후보는 60.098초 유지 후 명시적
disconnect로 1000 종료했다. 새 바이너리 출력은 0건이었으며 출력 성공 대신
연결 유지·정상 정리 검증으로 기록한다. 이 시험은 외부 Spring·브라우저를 제외한다.

같은 연결의 보드 로그에서 다음 실제 재확인을 관측했다.

| 보드 시각(KST) | 사건 |
|---|---|
| 12:49:58.709 | 플랫폼 바쁨으로 VM 검증·양방향 전달 보류 |
| 12:49:59.685 | 새 조회로 확인 후 같은 연결 전달 재개 |

보류는 약 0.976초였고, 해당 구간의 보류/재개는 각각 1회, console unavailable은
0회였다. 오류를 인위적으로 주입하거나 플랫폼 작업 잠금을 직접 잡지 않았다.

최종 외부 웹 연결 전 SSH 조회에서는 같은 Debian UUID의 hvc0가 `agetty`가 아닌
`bash`로 바뀌어 있었다. 연결 시 자동 Enter가 실행될 수 있어 재연결을 요청하거나
누르지 않았다. 게스트 셸에서 logout·명령·암호 입력도 하지 않았다.
따라서 **호환 보완안의 API 경로는 통과했지만 외부 웹 최종 재시험은 대기**다.

사용자와 약속한 대로 보드 소스를 원본 SHA256
`59909b45a533c394fc8ced0882a5467a0feaf2410a1e14f7195852b57f407398`로 복원하고
API를 재시작했다. 인증된 health 200, API/MQTT/Tailscale/feed active,
kiosk inactive(기존 상태), VM 3대 UUID/domid 유지, 잔여 xl/xenconsole 없음,
임시 후보 폴더 제거를 최종 확인했다. 보완안은 현재 보드의 영구 배포가 아니다.

소스·계약·테스트·이 기록은 `feat/platform/S15P31E110-251-console-busy-recheck`에서
platform 대상 일반 MR로 제출한다. 이후 외부 웹 재시험 결과는 다음 절에 기록한다.
정식 배포 및 VM1·VM2 등록 합의/시험은 별도로 남는다.

### 로그아웃 확인 후 외부 웹 재시험

사용자가 로그아웃 완료를 알린 뒤 같은 UUID의 hvc0가 `agetty`임을 재확인했다.
중단된 SSH 연결은 비밀번호 인증 전에 만료돼 적용되지 않았으며, 새 연결로
원본 해시를 확인한 뒤 같은 호환 후보를 별도 임시 폴더에 적용했다.

브라우저 자동 제어 오류가 지속돼 사용자가 외부 웹에서 화면 지우기 → 연결을
직접 수행하고 **새 출력이 보인다**고 확인했다. 이전 xterm 버퍼를 새 수신의 증거로
사용하지 않도록 화면을 먼저 지웠다. 관측 스크립트는 게스트 입력을 보내지 않았다.

- 같은 xenconsole PID `53443`이 61.1초 관측의 13개 표본에서 유지됐다.
- 이 PID는 API PID `53073`의 자식 실행임을 부모 관계로 확인했다.
- 14:07:43.408 상태 조회 바쁨으로 보류, 14:07:44.706 재확인 후 재개했다.
  약 1.30초이며 보류·재개 각 1회, 오류 종료는 0회였다.
- 사용자가 연결 해제를 확인한 뒤 잔여 콘솔 0개, VM 3대 UUID/domid 유지,
  Debian `agetty` 상태를 확인했다.
- 외부 Nginx의 실제 해당 WSS 요청은 101이며 송신 바이트 1,113이었다.
  인증 토큰·IP·터미널 본문 대신 요청 경로·시각·상태·바이트 수만 수집했다.

이후 사용자가 수동 재연결 성공을 확인했다. 첫 연결의 PID `53443`이 해제된 후
새 xenconsole PID `55680`이 같은 API PID `53073` 소유로 생성됐음을 확인했다.
VM 3대의 UUID/domid는 그대로였고 Debian은 `agetty` 상태였다.

사용자가 마지막 연결 해제를 확인한 뒤, **API를 재시작하기 전에도 콘솔 프로세스가
0개임을 확인**했다. 서비스 재시작으로 남은 점유를 강제로 정리한 결과를 정상 해제로
잘못 해석하지 않았다.

그 뒤 소스를 원본 SHA256
`59909b45a533c394fc8ced0882a5467a0feaf2410a1e14f7195852b57f407398`로 복원하고
API만 재시작했다. health 200, API/MQTT/Tailscale/feed active, kiosk inactive(기존),
VM UUID/domid 유지, 재시작 후 콘솔 0개, 임시 폴더 제거를 확인했다.
게스트·펌웨어·Spring DB·통신/메모리/CPU 설정은 바꾸지 않았다.

이로써 **보완 후보의 Debian 외부 웹 경로 시험은 통과**했다. 보완 코드는 원본 복원
때문에 현재 보드에 영구 적용되어 있지 않다. 소스의 정식 반영·배포와 VM1·VM2의
명세 등록 합의/실제 웹 시험은 별도로 남는다. 장시간/두 HDMI 부하 검증도 대신하지 않는다.

## 로컬 근거

작업 공간 `outputs/display-251/`의 다음 파일을 참조한다. 비밀값은 포함하지 않는다.

- `board-preflight-20261030.json`, `external-preflight-20261030.json`
- `guest-console-state-20261030.json`, `external-authentication-20261030.json`
- `board-first-disconnect-diagnostics-20261030.json`
- `board-console-query-samples-20261030.json`, `board-console-query-codes-20261030.json`
- `board-active-console-20261030.json` (정확한 NUL 구분 프로세스 확인)
- `external-debian-reconnected-20261030.png`, `external-duplicate-rejected-20261030.png`
- `board-console-diag-applied-20261030.json`, `board-console-diag-result-20261030.json`
- `board-console-diag-restored-20261030.json` (원본 복원·최종 서비스/VM 상태)
- `board-console-busy-*-20261030-trial1.json` (첫 수정 후보 실패·원복)
- `board-console-busy-applied-v2-20261030.json`
- `board-console-busy-direct-v2-20261030.json`, `board-console-busy-events-v2-20261030.json`
- `board-console-busy-restored-v2-20261030.json` (최종 원복)
- `board-console-busy-web-applied-20261030.json`, `board-console-busy-web-live-20261030.json`
- `board-console-busy-web-owner-20261030.json`, `board-console-busy-web-after-detach-20261030.json`
- `external-console-busy-web-access-20261030.json`
- `board-console-busy-web-after-reconnect-20261030.json`
- `board-console-busy-web-restored-20261030.json` (최종 원복·재시작 전후 점유 확인)

초기 진단 스크립트의 프로세스 스캔은 cmdline 구분 처리에 문제가 있어 그 빈 결과를
단독 증거로 사용하지 않는다. 수정한 NUL 구분 검사와 실제 재연결 결과로 정리를 확인했다.
스크린샷에는 콘솔 출력 일부가 있으므로 토큰·암호를 포함하지 않는 화면만 보관했다.
