# Mosquitto 브로커

VM 에이전트·앱·젯슨·게이트웨이·백엔드가 메시지를 주고받는 MQTT 브로커 설정입니다. ESP32 는 브로커에 붙지 않고 게이트웨이 시리얼 뒤에 있습니다 (계약 v1.1).
토픽·계정 권한은 [MQTT 메시지 계약 v1](../../../docs/contracts/mqtt-contract-v1.md) §1·§9 를 따릅니다.

**로컬 Docker 와 dom0 가 같은 설정 파일을 씁니다.** 둘의 차이는 리스너 파일 하나(`listeners/`)뿐입니다.

```text
platform/deploy/mosquitto/
├── etc/                  # 그대로 /etc/mosquitto 에 들어가는 공통 설정
│   ├── mosquitto.conf    #   익명 금지, passwd·acl, 64 KB 상한, persistence 끔
│   ├── acl               #   계약 §9 → 계정별 발행 권한
│   └── accounts          #   계정 이름 목록 (비밀번호 없음)
├── listeners/            # 환경마다 다른 것 → /etc/mosquitto/conf.d/listeners.conf
│   ├── local.conf        #   로컬: 1883, 9001(WebSocket)
│   └── dom0.conf         #   dom0: 192.168.77.1(VM)·127.0.0.1. Tailscale 은 설치 스크립트가 보드 주소로 만든다
├── local/                # 로컬 개발용 Docker
│   ├── docker-compose.yml
│   └── .env.example
└── scripts/
    ├── make-passwd.sh    # accounts → passwd (로컬: 공통 개발 비밀번호)
    ├── dom0-broker.sh    # dom0 설치·계정 관리 (POSIX sh, root)
    └── check.sh          # 권한·크기·WebSocket 검사 (로컬·서버 공용, bash)
```

## 로컬에서 쓰기

Docker Desktop 이 켜져 있어야 합니다. Git Bash 기준입니다.

```bash
cd platform/deploy/mosquitto
cp local/.env.example local/.env           # XENOVA_MQTT_DEV_PASSWORD 를 바꾼다
bash scripts/make-passwd.sh                # local/passwd 생성 (compose 보다 먼저)
docker compose -f local/docker-compose.yml --env-file local/.env up -d
bash scripts/check.sh                      # 결과: 통과 15, 실패 0
```

- `local/passwd` 를 만들기 전에 compose 를 올리면 Docker 가 빈 디렉터리를 만들어 브로커가 뜨지 않습니다. 그때는 `local/passwd` 디렉터리를 지우고 `make-passwd.sh` 부터 다시 합니다.
- 계정을 추가하면 `etc/accounts` 에 넣고 `make-passwd.sh` → `docker compose ... restart`.
- 끌 때: `docker compose -f local/docker-compose.yml down`
- 로그 경고 `ACL pattern 'xenova/v1/#' does not contain '%c' or '%u'` 는 모든 계정에 적용하는 규칙이라 나오는 것으로 정상입니다. Windows 에서 나오는 passwd 권한 경고도 로컬에서는 무시해도 됩니다.

### 각 컴포넌트 접속값 (로컬)

모든 계정의 비밀번호는 `local/.env` 의 `XENOVA_MQTT_DEV_PASSWORD` 입니다.

| 쓰는 쪽 | 주소 | 계정 (username) | 설정 |
| --- | --- | --- | --- |
| 백엔드 | `tcp://127.0.0.1:1883` | `backend` | `MQTT_BROKER_URL`, `MQTT_USERNAME`, `MQTT_PASSWORD` |
| VM 에이전트 | `127.0.0.1:1883` | 그 VM 의 `vm_id` | `XENOVA_AGENT_BROKER_HOST/PORT/USERNAME/PASSWORD` |
| 앱 (`xenova_mqtt`) | `127.0.0.1:1883` | `autonomous` · `manual` · `interaction` | `XenovaClient(..., username=, password=)` |
| 젯슨 | `127.0.0.1:1883` | `perception` | |
| Zephyr 앱 (dom0 vchan 중계가 대신 접속) | `127.0.0.1:1883` | 그 앱의 `app_id` | 계약 4.1 |
| 게이트웨이 | `127.0.0.1:1883` | `gateway` | |
| 브라우저 | 직접 붙지 않음 — 백엔드 중계 (시나리오 D2). `ws://127.0.0.1:9001` 은 개발 확인용 | | |
| 확인·모니터링 | `127.0.0.1:1883` | `observer` (읽기 전용) | |

## 권한 (acl)

계정 이름 = 토픽 안의 id 입니다. 그래야 `pattern` 한 줄로 "자기 토픽에만 발행" 을 겁니다.

| 계정 | 발행 가능 |
| --- | --- |
| VM 에이전트 (`vm_id`) | `xenova/v1/vms/{자기 vm_id}/heartbeat·inventory·metrics·command-result` |
| 앱·젯슨 (`app_id`) | `xenova/v1/apps/{자기 app_id}/events` |
| `backend` | `xenova/v1/apps/manual/input`, `xenova/v1/vms/+/command` |
| `gateway` | `xenova/v1/vehicle/control`, `xenova/v1/vehicle/status`, `xenova/v1/apps/vehicle/events` |
| 모든 계정 | `xenova/v1/probes/#` (지연 측정. 계약의 "자기 vm_id 만" 은 payload 조건이라 브로커가 못 거름) |

- 읽기는 개발 단계라 모든 계정이 `xenova/v1/#` 를 읽을 수 있습니다. 계정별 구독 제한은 계약 다음 개정(v1.2)에서 정합니다.
- 권한 없는 발행은 브로커가 조용히 버립니다 (MQTT 3.1.1 은 발행자에게 오류를 돌려주지 않음). `check.sh` 는 구독 쪽에서 안 오는 것으로 확인합니다.

## dom0 (실보드)

dom0 Yocto 이미지에는 `mosquitto`·`mosquitto-clients` 가 들어 있지만 서비스는 꺼져 있고 설정도 없습니다.
`scripts/dom0-broker.sh` 가 설정 배치·계정 발급·자동 기동을 합니다. dom0 에 bash 가 없을 수 있어 POSIX sh 로 씁니다.

### 설치 (보드마다 한 번)

이 폴더(`platform/deploy/mosquitto`)를 dom0 에 복사한 뒤 root 로:

```sh
sh scripts/dom0-broker.sh install
```

| 하는 일 | 결과 |
| --- | --- |
| 설정 배치 | `etc/mosquitto.conf`·`etc/acl` → `/etc/mosquitto/`, `listeners/dom0.conf` → `/etc/mosquitto/conf.d/listeners.conf` |
| 서비스·앱 계정 | `etc/accounts` 의 이름마다 무작위 비밀번호. 해시는 `/etc/mosquitto/passwd`, 평문은 `/etc/mosquitto/passwd.credentials` (root 0600). 다시 실행해도 기존 비밀번호는 그대로 |
| 명령 설치 | 이 스크립트를 `/usr/local/sbin/xenova-broker` 로 복사 (이후 저장소 사본 없이 계정 관리) |
| 자동 기동 | `mosquitto.service` drop-in: `xenova-bridge`(192.168.77.1)·`tailscaled` 뒤에 시작, 실패하면 3초 뒤 재시작 |
| Tailscale 리스너 | 시작 직전 `xenova-broker tailscale-listener` 가 `tailscale ip -4` 로 `conf.d/tailscale.conf` 를 다시 씀 (최대 20초 대기). 주소가 없으면 외부 리스너 없이 뜬다 — VM 쪽 브로커를 Tailscale 상태에 묶지 않는다 |

외부 서버의 Spring 은 Tailscale 주소로 붙습니다.

```text
MQTT_BROKER_URL=tcp://<보드 Tailscale IP>:1883
MQTT_USERNAME=backend
MQTT_PASSWORD=<passwd.credentials 의 backend 줄>
```

Recovery Worker 는 `/etc/xenova/recovery-worker.env` 에 `dom0-server` 계정을 넣습니다 ([dom0-manager](../../services/dom0-manager/README.md)).

### VM 계정 (VM 마다)

에이전트 계정 이름은 그 VM 의 `vm_id` 입니다 (ACL `%u`). VM 을 만들 때 자동으로 생기지 않으므로 직접 추가합니다.

```sh
xenova-broker add-account <vm_id>       # 대소문자 무관, 소문자로 저장. 이미 있으면 비밀번호를 바꾸지 않는다
# → /etc/mosquitto/agent-env/<vm_id>.env 의 두 줄을 그 VM 의 /etc/xenova/agent.env 에 넣고
#   그 VM 에서 systemctl restart xenova-agent
xenova-broker list
xenova-broker remove-account <vm_id>    # 이미 접속 중인 연결은 다시 접속할 때 거부
```

- 계정 추가·삭제는 SIGHUP 으로 반영되어 다른 클라이언트의 연결을 끊지 않습니다.
- 에이전트는 계정이 틀려도 경고만 남기고 재시도합니다. 대시보드에 VM 이 안 뜨면 dom0 에서 `journalctl -u mosquitto | grep <vm_id>` 로 `not authorised` 를 먼저 봅니다.
- 평문 파일(`passwd.credentials`, `agent-env/`)은 각 설정에 넣은 뒤 지워도 됩니다. 지운 계정의 비밀번호는 다시 볼 수 없으니 `remove-account` 뒤 `add-account` 로 새로 발급합니다.

### 검사

`check.sh` 는 bash 와 예시 vm_id 두 개를 씁니다. dom0 에 bash 가 있으면:

```sh
xenova-broker add-account 11111111-1111-4111-8111-111111111111
xenova-broker add-account 22222222-2222-4222-8222-222222222222
XENOVA_MQTT_HOST=192.168.77.1 XENOVA_MQTT_CREDENTIALS=/etc/mosquitto/passwd.credentials bash scripts/check.sh
xenova-broker remove-account 11111111-1111-4111-8111-111111111111
xenova-broker remove-account 22222222-2222-4222-8222-222222222222
```

남은 일: VM 생성 때 계정 자동 등록, Yocto 이미지에 설정·자동 기동 포함 (S15P31E110-258).
