# Xenova MQTT 브로커

Mosquitto를 dom0 이미지에 포함하고, 계정·ACL은 데이터 파티션에서 관리합니다.
메시지 의미는 [v1 계약](../../../docs/contracts/mqtt-contract-v1.md)과
[v2 Telemetry 계약](../../guests/zephyr/telemetry/contract/CONTRACT.md)을 따릅니다.
명령 인자·응답·오류의 원문은 [CLI 명세](../../../docs/platform/cli-manager-spec.md#cmd-broker)입니다.

## 구성과 원본

일반 VM 생성·factory 등록의 실제 공급은 [VM 공통 provisioning 계약](../../../docs/contracts/vm-provisioning-v1.md)에 연결했다.
`ensure_accounts`는 같은 UUID 자격을 보존하고 필요한 신규 계정만 기존 writer로 준비한다.
명시 폐기한 계정은 revoked fence로 자동 부활을 막는다. 구형 writer와 새 fence를 혼합하지 않는다.
해시 전/후의 staging을 원 암호로 재개하며 손상/소실을 빈 저장소로 취급하지 않는다.
Linux Agent의 실제 v2 송신과 drive/link 생산·소비를 자기 VM/승인 Gateway ACL에 연결했다.
자격 확보·broker 반영·guest 전달·인증·건강은 다른 상태다. package library는
`xenova-mqtt-broker-provider`, 기존 서비스·활성화는 main package가 소유한다.

| 파일·구성 | 역할 |
|---|---|
| `etc/policy.json` | 계정 프로필과 허용 토픽의 공통 원본. 비밀번호 없음 |
| `etc/accounts`, `etc/acl` | 공통 정책에서 생성한 로컬 개발용 파일 |
| `scripts/render-policy.py` | 개발용 파일 생성·일치 검사 |
| `scripts/xenova_broker.py` | dom0 계정의 유일한 저장·변경 구현 |
| `scripts/dom0-broker.sh` | 기존 팀 명령을 공통 도구로 연결하는 POSIX sh 진입점 |
| Yocto `xenova-mqtt-broker` | 서비스·설정·도구·정책을 이미지에 설치 |
| `/srv/xenova/mqtt` | 보드별 계정·해시·비공개 접속정보·저장 세대 |

기존 dom0-broker.sh의 install/add-account/remove-account/list와 Agent 설정 전달 기능을
영구 저장 기반에 연결했습니다. `/etc/mosquitto`와 `/srv/xenova/mqtt`를 각각 수정하는
두 계정 관리자를 함께 실행하지 않습니다. `make-passwd.sh`는 로컬 개발용입니다.

`xenova/v1/#` 읽기는 기존 v1 개발 계약입니다. probe 쓰기는 v1 발행 계정에만 유지합니다.
observer와 Telemetry 구독기는 읽기 전용이며, v2 발행자는 자기 VM의 다섯 Telemetry
토픽만 쓸 수 있습니다. 전역 pattern으로 모든 계정에 쓰기를 열지 않습니다.
probe payload의 source/target 일치는 수신 측에서도 검사해야 합니다.
계정 권한은 Gateway의 현재 실행·앱 준비·출력 허가 검증을 대신하지 않습니다.

## 새 이미지에서 시작

root로 실행합니다. 계획은 비밀번호·잠금·임시 파일을 만들지 않습니다.

```sh
xenova-broker initialize
xenova-broker initialize --execute
xenova-broker check --runtime
systemctl enable --now mosquitto
```

기본 서비스 계정만 먼저 만들어도 됩니다. VM UUID를 미리 모두 확정할 필요는 없습니다.
VM을 추가하면 실제 등록 UUID를 확인하고 필요한 v1/v2 계정을 추가합니다.

```sh
xenova-vm list --short
read -r -p 'VM UUID: ' VM_ID
xenova-broker add-account "$VM_ID" --execute
xenova-broker add-account "telemetry-$VM_ID" --execute
xenova-broker list
```

v1 Agent·주행 계정은 UUID, v2 Publisher 계정은 telemetry-UUID입니다. 두 비밀번호는
독립적입니다. 한 계정을 지워도 동반 계정·VM·자산·CPU 정책은 바뀌지 않습니다.
새 서비스 이름은 공통 정책에 명시해야 하며 알 수 없는 이름에는 권한을 추측해 주지 않습니다.
기존 message-board 계정은 유지하지만 새 기본 서비스에는 현행 interaction을 사용합니다.

## 계정 전달과 소비자 적용

비밀번호를 채팅·로그·명령행 인자·공통 이미지에 넣지 않습니다.
각 소비자에 필요한 계정 하나만 root 전용 파일로 내보냅니다.

```sh
install -d -m 0700 /root/xenova-private
xenova-broker export-account "$VM_ID" --format agent-env \
  --output /root/xenova-private/agent.env --execute
xenova-broker export-account "telemetry-$VM_ID" --format telemetry \
  --output /root/xenova-private/telemetry.json --execute
```

agent-env는 기존 `XENOVA_AGENT_BROKER_USERNAME/PASSWORD` 두 항목입니다.
backend-env는 `MQTT_USERNAME/PASSWORD`, credentials는 username/password JSON입니다.
drive·telemetry는 기존 provisioning 도구용 private JSON이며 내부 주소 192.168.77.1:1883을
사용합니다. 파일 생성만으로 게스트 env·Store·receipt·EC2 컨테이너를 변경하지 않습니다.
각 소비자의 기존 설치·provisioning·재시작 절차로 별도 적용합니다.

새 v2 Bootstrap의 telemetry-UUID는 Zephyr와 향후 Linux v2 Publisher에 공통입니다.
기존 Linux Agent의 v1 메시지·계정은 유지합니다. 새 펌웨어의 decoder는 신뢰된 Store
채널과 VM UUID를 대조하며, 예전 UUID 형식 v2 입력으로 자동 fallback하지 않습니다.

## 기존 계정과 업데이트

- 기존 `/srv/xenova/mqtt` 초기 후보: `initialize` 계획 후 `--execute`로 전환합니다.
  원 파일과 기존 비밀번호·해시를 유지한 채 새 저장 세대를 만듭니다.
- 기존 팀 스크립트의 `/etc/mosquitto` 계정: `import-legacy` 계획 후 `--execute`로 복사합니다.
  passwd와 passwd.credentials를 읽기만 합니다. 필요한 기본 서비스는 이후 initialize로 추가합니다.
- 원본 평문이 없거나 계정 집합이 다르면 자동 재발급하지 않습니다. 원본을 보존해 수습합니다.
- 정상 재부팅·서비스 재시작·데이터 보존 업데이트는 기존 인증정보를 재사용합니다.
- SD 전체 재설치로 데이터가 초기화되면 새 비밀번호를 소비자에 반영합니다. VM UUID도
  바뀌면 계정 이름·Bootstrap 입력도 바뀝니다. 이미지 조립·데이터 초기화 정책은 그대로입니다.

기존 `/usr/local/sbin/xenova-broker`가 남은 설치에서는 `command -v xenova-broker`를 확인합니다.
전환 도구의 확정 경로는 `/usr/sbin/xenova-broker`입니다. 오래된 별도 작성자를 병용하지 않습니다.
이 저장소의 dom0-broker.sh는 새 패키지가 없으면 기존 파일을 변경하지 않고 종료합니다.
`listeners/dom0.conf`는 과거 수동 배포 구성의 참고 파일이며 제품 패키지가 읽지 않습니다.

## 저장·반영·실패의 의미

계정 변경은 단일 작성자 잠금에서 기존 세대를 다시 확인합니다. 새 계정만 비밀번호를
발급하고 기존 비밀번호·해시는 복사합니다. passwd·ACL·접속정보·manifest를 fsync한 뒤
current 포인터로 완성된 세대를 게시합니다. 설정은 그 세대의 passwd·ACL 경로를 고정합니다.
저장 중단·포인터 유실·읽기 실패를 정상 최초 설치로 바꾸지 않습니다.

저장 완료와 broker reload는 별개입니다. 실행 중에는 SIGHUP reload를 요청하고,
비활성이면 다음 시작에서 읽습니다. 신호 요청 성공은 실제 인증 성공이 아닙니다.
중단 작업은 같은 명령으로 준비된 비밀번호를 재사용합니다. 불완전 파일을 지워 새 상태로
만들지 않습니다. 이전 세대·중단 기록은 보존하며 자동 활성화하거나 삭제하지 않습니다.

삭제는 password와 ACL 양쪽에서 해당 계정을 제거합니다. password reload는 이미 연결된
클라이언트를 강제로 끊지 않으므로 기존 연결의 권한 제거와 새 연결 거절을 각각 검사합니다.
동일 이름 삭제·재등록만으로 기존 세션까지 안전하게 암호 회전했다고 판단하지 않습니다.
다른 클라이언트를 끊는 broker 전체 재시작은 도구가 자동 수행하지 않습니다.

## Tailscale로 EC2에서 직접 접속

내부 broker는 127.0.0.1:1883과 192.168.77.1:1883만 엽니다. Tailnet 주소 대기나
인터넷 연결을 VM 부팅 조건으로 추가하지 않습니다. Tailscale 인증 후 한 번 설정합니다.

```sh
tailscale up --accept-dns=false
tailscale serve --bg --tcp=1883 tcp://127.0.0.1:1883
tailscale serve status
tailscale ip -4
```

EC2는 `tcp://<보드 Tailscale IP>:1883`에 자기 MQTT 계정으로 접속합니다. 사용자 PC는
중계하지 않습니다. FastAPI 8081의 별도 Serve·Bearer 토큰과 Tailscale의 같은 보드
암호화 백업·복원은 [접속 명세](../../../docs/platform/cli-manager-spec.md#cmd-tailscale)를 따릅니다.
Wi-Fi LAN listener·Funnel·subnet router·8082 공개는 자동 추가하지 않습니다.

## 로컬 Docker와 검증

정책을 바꾸면 생성 결과를 검토하고 커밋합니다. 도구는 비밀번호를 생성하지 않습니다.

```sh
python3 scripts/render-policy.py
python3 scripts/render-policy.py --check
cp local/.env.example local/.env
# 개발용 비밀번호를 local/.env에서 설정한다.
bash scripts/make-passwd.sh
docker compose -f local/docker-compose.yml --env-file local/.env up -d
bash scripts/check.sh
```

로컬 개발 계정의 비밀번호는 `local/.env`의 `XENOVA_MQTT_DEV_PASSWORD`입니다.
보드에서 발급한 계정·비밀번호와는 별개이며, 포트를 변경했다면 아래 기본값도 바꿉니다.

| 소비자 | 기본 접속 주소 | 계정·설정 |
|---|---|---|
| 백엔드 | `tcp://127.0.0.1:1883` | `MQTT_BROKER_URL`, `MQTT_USERNAME=backend`, `MQTT_PASSWORD` |
| VM Agent | `127.0.0.1:1883` | username은 VM UUID. `XENOVA_AGENT_BROKER_HOST/PORT/USERNAME/PASSWORD` |
| legacy 앱·Jetson | `127.0.0.1:1883` | 앱 계정 `autonomous`·`manual`·`interaction`, Jetson 계정 `perception` |
| 공통 Zephyr 주행 앱 | 내부 브리지의 `192.168.77.1:1883` | netfront 직접 MQTT, username은 VM UUID. 로컬 시험에서는 broker 주소를 별도 지정 |
| v2 Publisher | 시험·실행 환경의 broker 주소 | `telemetry-UUID`, v1 계정과 구분 |
| Gateway / Recovery Worker | `127.0.0.1:1883` | 각각 `gateway` / `dom0-server` |
| 확인·모니터링 | `127.0.0.1:1883` | `observer`는 v1 읽기, `telemetry-reference-subscriber`는 v2 읽기 전용 |
| 브라우저 | 백엔드 중계 | `ws://127.0.0.1:9001`은 개발 확인용 |

Recovery Worker의 보드별 인증정보는 `/etc/xenova/recovery-worker.env`에 적용합니다.
설정 항목과 서비스 사용법은 [dom0-manager 안내](../../services/dom0-manager/README.md)를 따릅니다.
로컬 계정·ACL을 변경할 때는 `policy.json`을 수정한 뒤 생성·검사·passwd 재생성 순서로
반영하며 `etc/accounts`를 별도로 편집하지 않습니다. 로컬 broker 종료는
`docker compose -f local/docker-compose.yml --env-file local/.env down`입니다.

로컬 예시 UUID 두 개는 개발용 생성물에만 포함됩니다. WebSocket 9001은 개발 경로입니다.
실제 인증·구독·독립 수신은 서비스 active나 SUBACK/PUBACK만으로 대체하지 않습니다.
로컬 검증과 기존 보드 시험의 범위는
[초기 부분 적용](../../../docs/verification/mqtt-tailscale-partial-20261028.md) 및
[계정 통합 검증](../../../docs/verification/mqtt-account-integration-20261029.md)에 구분합니다.
