# Mosquitto 브로커

VM 에이전트·앱·젯슨·게이트웨이·ESP32·백엔드가 메시지를 주고받는 MQTT 브로커 설정입니다.
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
│   └── dom0.conf         #   dom0 초안 (S15P31E110-152 에서 확정)
├── local/                # 로컬 개발용 Docker
│   ├── docker-compose.yml
│   └── .env.example
└── scripts/
    ├── make-passwd.sh    # accounts → passwd (로컬: 공통 개발 비밀번호 / 서버: --random)
    └── check.sh          # 권한·크기·WebSocket 검사 (로컬·서버 공용)
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
| 앱 (`xenova_mqtt`) | `127.0.0.1:1883` | `autonomous` · `manual` · `message-board` | `XenovaClient(..., username=, password=)` |
| 젯슨 | `127.0.0.1:1883` | `perception` | |
| 게이트웨이 · ESP32 | `127.0.0.1:1883` | `gateway` · `esp32` | |
| 브라우저 | `ws://127.0.0.1:9001` | (계약 v1.1 에서 결정) | |
| 확인·모니터링 | `127.0.0.1:1883` | `observer` (읽기 전용) | |

## 권한 (acl)

계정 이름 = 토픽 안의 id 입니다. 그래야 `pattern` 한 줄로 "자기 토픽에만 발행" 을 겁니다.

| 계정 | 발행 가능 |
| --- | --- |
| VM 에이전트 (`vm_id`) | `xenova/v1/vms/{자기 vm_id}/heartbeat·inventory·metrics·command-result` |
| 앱·젯슨 (`app_id`) | `xenova/v1/apps/{자기 app_id}/events` |
| `backend` | `xenova/v1/apps/manual/input`, `xenova/v1/vms/+/command` |
| `gateway` | `xenova/v1/vehicle/control`, `xenova/v1/apps/vehicle/events` |
| `esp32` | `xenova/v1/vehicle/status` |
| 모든 계정 | `xenova/v1/probes/#` (지연 측정. 계약의 "자기 vm_id 만" 은 payload 조건이라 브로커가 못 거름) |

- 읽기는 개발 단계라 모든 계정이 `xenova/v1/#` 를 읽을 수 있습니다. 계정별 구독 제한은 계약 v1.1 에서 정합니다.
- 권한 없는 발행은 브로커가 조용히 버립니다 (MQTT 3.1.1 은 발행자에게 오류를 돌려주지 않음). `check.sh` 는 구독 쪽에서 안 오는 것으로 확인합니다.

## dom0 로 옮길 때 (S15P31E110-152, 인프라 담당)

로컬에서 검증한 파일을 **수정 없이** 가져갑니다. 바꾸는 것은 리스너 파일과 계정 목록뿐입니다.

1. 이미지에 `mosquitto`(+ 검사용 `mosquitto-clients`) 패키지를 넣는다. 레시피는 `meta-openembedded/meta-networking` 에 있고 이 레이어는 이미 빌드 설정에 등록돼 있다 (`platform/yocto/layers.lock`).
2. 파일 배치

   | 저장소 | dom0 |
   | --- | --- |
   | `etc/mosquitto.conf` | `/etc/mosquitto/mosquitto.conf` |
   | `etc/acl` | `/etc/mosquitto/acl` |
   | `listeners/dom0.conf` | `/etc/mosquitto/conf.d/listeners.conf` |

3. 계정: `etc/accounts` 의 예시 vm_id 두 줄을 실제 VM 의 vm_id 로 바꾸고 계정마다 무작위 비밀번호를 만든다.

   ```bash
   bash scripts/make-passwd.sh --random etc/accounts /etc/mosquitto/passwd
   # → /etc/mosquitto/passwd.credentials 의 user:password 를 각 VM·서비스 설정에 넣은 뒤 지운다
   chown mosquitto: /etc/mosquitto/passwd && chmod 600 /etc/mosquitto/passwd
   ```

4. 패키지의 systemd 서비스(`mosquitto.service`)로 자동 기동한다.
5. 검사: `XENOVA_MQTT_HOST=192.168.77.1 XENOVA_MQTT_CREDENTIALS=/etc/mosquitto/passwd.credentials bash scripts/check.sh`

-152 에서 정할 것: VM 이 늘 때 계정 추가 방식(vm 생성 시 자동 등록 여부), 백엔드 위치에 따른 리스너 주소, 브라우저 WebSocket 노출 범위, 로그 보관.
