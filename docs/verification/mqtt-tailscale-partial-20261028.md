# MQTT 운영 설정·기존 Tailscale 부분 적용 검증

이 보고서는 아래 당시 후보의 보드·EC2 부분 적용 기록이다. 이후의 계정 운영 통합과
v1/v2 Bootstrap 변경은 [계정 통합 검증](mqtt-account-integration-20261029.md)을 따른다.
새 후보는 이 보고서의 실보드 결과만으로 수락하지 않는다.

## 범위와 기준선

원 작업방의 `feat/platform/health-and-recovery-contracts`,
HEAD `5ffdae662b7c1730e8a85395e69bf10b4d9433a2`에 MQTT 운영 설정을 추가했다.
미커밋 후보는 아래 recipe·runtime 해시와 별도 증거의 `FINAL_PACKAGING_VERIFICATION.json`으로
식별한다. 기존 미추적 `docs/platform/drive-pipeline-architecture.md`는 수정하지 않았다.

사용자는 설정·ACL·최초 계정 절차, MQTT 설정 패키지 부분 빌드, 기존 Tailscale RPM의
의존성 대조, 백업·설치·인증·서비스 검증을 요청했다. 최종 접속 요구는
**EC2에서 보드의 Tailscale IP로 MQTT·FastAPI 직접 접속**이다. 사용자 PC는 중계하지 않는다.
초기에 선택했던 PC SSH 터널은 요구 정정에 따라 운영 구성에서 제외했다.
초기 보드 LAN 주소는 `192.168.123.112`였으며 2026-10-29 확인은 `root@192.168.30.96`으로
수행했다. 인증된 보드 Tailnet 주소는 `100.108.226.65`, EC2는 `100.80.180.63`이다.
기본 VM·CPU 정책·관리 Wi-Fi·소비자 env·호스트 재부팅·이미지/SD·commit/push는 대상에서 제외했다.

## 현재 판정

| 단계 | 판정과 근거 |
|---|---|
| MQTT 소스·부분 패키지 | 통과. `xenova-mqtt-broker` recipe 인식·package QA·runtime 소스/묶음 바이트 대조 |
| 기존 Tailscale·TUN 의존성 | 통과. 고정 ARM64 RPM 재사용, 실제 커널 build ID·Image 해시 대조, 실제 실행·TUN 로드 |
| 보드 설치·두 서비스 | 통과. enable/active, 63파일·필수 conf.d 디렉터리 대조, 백업 archive 원본 해시·원복 계획 확인 |
| MQTT 인증·ACL | 로컬 격리 broker 14항목, 보드 8항목 통과. 계정 재사용·재시작 후 실제 독립 수신 포함 |
| EC2의 MQTT·FastAPI 직접 접속 | 통과. EC2에서 실제 토큰으로 HTTP 200, backend 계정으로 독립 MQTT 발행·수신 확인 |
| FastAPI 인증 | 무토큰/틀린 토큰 401, 유효 토큰 200. 기존 token·API 제품 코드는 변경하지 않음 |
| 불필요한 SSH MQTT 예외 제거 | 완료. 원본/적용 해시 대조 후 직전 추가 한 항목만 복원·문법 검사·reload. 원 인증·기존 목적지 유지 |
| Tailnet 인증 | 통과. 보드를 켠 상태에서 새 인증 요청을 승인하고 실제 Running 확인 |
| Tailscale 서비스 재시작 | 통과. 같은 장치 ID·IP·두 Serve 설정·accept-dns=false 유지 |
| Tailnet 원격 SSH·인터넷 단절 복구 | 미수행. EC2 등 별도 Tailnet 장치에서 실제 접속 확인 필요 |
| 호스트 재부팅·SD 전체 재설치 | 미수행. 데이터 보존/같은 보드의 암호화 백업·복원 절차와 실제 전원/SD 수락을 구분 |
| 전체 이미지·WIC 포함 | 이번 부분 적용으로 수락하지 않음. 별도 진행 중 이미지의 고정 입력도 수정하지 않음 |

서비스 active는 소비자 계정 배포·Agent/주행 앱 종단 연동·Tailnet 인증 성공을 뜻하지 않는다.
운영 Spring env·컨테이너는 변경하지 않았다. Agent·Gateway·Worker·Bootstrap 담당자는 보드에서
발급한 **자기 계정만** 보안 경로로 전달받아야 한다.

## 구현·패키징

현재 사용법의 원문은 [MQTT 명세](../platform/cli-manager-spec.md#cmd-mqtt-init),
[브로커 안내](../../platform/deploy/mosquitto/README.md),
[부팅 경계](../platform/management-boot-boundaries.md)의 “MQTT의 저장소·부팅 경계”에 둔다.

- `recipes-connectivity/xenova-mqtt-broker/`: 기존 `mosquitto.service`의 전체 override,
  Xenova drop-in·설정·preset·로컬 `xenova-mqtt-init` 설치.
- `xenova-xen-bringup-image.bb`: 새 설정 패키지 포함.
- `test_xenova_mqtt_broker.py`: 초기화·중단·재시도·마운트·신원 독립·서비스 경계 회귀.
- `xenova-platform/files/xenova-auth.conf`는 최종적으로 원 소스와 같다. PC 터널을 위해
  추가했던 한 항목을 제거했고 보드도 원본 해시로 복원했다. 기존 API 8081·8082의 SSH
  전달 허용은 건드리지 않았다. MQTT RPM의 runtime 바이트도 바뀌지 않아 재빌드는 하지 않았다.
- `README.md`, `build-guide.md`, `cli-manager-spec.md`, `management-boot-boundaries.md`,
  `platform/deploy/mosquitto/README.md`: 빌드·운영·네트워크 범위 반영.

기존 Mosquitto 단독 패키지의 disable은 유지하며 설정 패키지가 enable을 소유한다.
systemd drop-in의 빈 `Wants=`/`After=`로는 의존성을 제거할 수 없으므로 기존 notify unit의
서비스 동작을 보존한 전체 override에서 외부 `network-online.target` 대기만 제외했다.
실제 보드의 유효 그래프에도 해당 target이 없고 내부 bridge·데이터 mount만 요구한다.
VM 자동 시작/admission은 broker나 Tailnet 인증에 새로운 성공 의존성을 갖지 않는다.

계정은 이미지가 아닌 `/srv/xenova/mqtt`에 명시적으로 게시한다. 평문 접속정보·manifest는
root 0600, 해시 passwd·ACL은 root:mosquitto 0640, 디렉터리는 0750이다.
계획 모드는 쓰지 않는다. 같은 VM UUID 목록의 재실행은 비밀번호를 재발급하지 않는다.
staging·초기화 marker·유실 흔적은 보존하고 차단한다. 계정 목록 추가/회전은 이 최초
설정 명령의 지원 범위가 아니다. v1 VM 계정과 v2 전용 계정은 분리했다.
listener는 `127.0.0.1:1883`, `192.168.77.1:1883`뿐이다.
EC2의 Tailnet 접속은 보드의 기존 tailscaled가 1883·8081 TCP를 같은 보드의 localhost로
전달한다. Mosquitto/FastAPI bind·인증·제품 코드·systemd 부팅 의존성은 유지한다.
별도 daemon·PC 중계·관리 LAN listener·VM 사설망 라우팅은 추가하지 않는다.
표시 피드 8082는 공개 대상이 아니다.

Tailscale 1.104.2는 기존 고정 RPM과 source/helper를 그대로 재사용했다. 새 의존성·버전·레이어는
추가하지 않았다. 실행 중 커널 `6.18.33-v8`의 GNU build ID는
`49036a495ac4ebb76506ff49c31b7eee1e0641de`, `/boot/Image` SHA256은
`a4baa7d3fd4f225d5a2591d39cf6c9e6da73f7811d716263b60e9cbd96569ac1`로 원 빌드와 일치했다.
기존 iptables 도구·선정 extension·동일 커널 모듈 등 27개 RPM에서 필요한 파일만 가져왔다.
iptables/ip6tables rule 복원 서비스·preset·rule 파일과 전체 RPM 후처리는 실행하지 않았다.
실제 TUN 로드와 ARM64 `tailscale`, `tailscaled`, `iptables`, `ip6tables` 실행을 확인했다.

새 MQTT 패키지 RDEPENDS는 기존 Mosquitto/clients·xenova-bringup·Python 분할 패키지를
사용한다. 실제 pkgdata의 전이 의존성에서 `python3-crypt`, `python3-netclient`까지 확인했다.
runtime 소스 5파일은 빌드 입력·추출 묶음·설치본이 일치한다.

| 산출물 | bytes | SHA256 |
|---|---:|---|
| `xenova-mqtt-broker-1.0-r0.noarch.rpm` | 13,546 | `8b90afe620bc4d2cd88533c19624d8d5d2e0a84c058474803f6aaff1069c9feb` |
| `candidate-final.tar.gz` | 37,316,115 | `045cfdcdd988fa71b11cbc7ba765b6e1aa33cc625729319c9af3dbd4d797f072` |

묶음은 63파일과 필수 빈 `etc/xenova/mqtt/conf.d`를 포함한다. 인증자료·Tailnet state·
개인 키·`/srv` 상태는 포함하지 않는다. 최초 전달 후보와 최종 묶음의 파일 바이트는 같으며,
최종 묶음은 누락됐던 빈 디렉터리를 명시적으로 포함한다.

## 실행한 검증

```sh
# WSL의 격리 fixture에서 root 소유권 검사. 실제 /run·/srv는 쓰지 않는다.
python3 -B -m unittest discover -s platform/tests -p test_xenova_mqtt_broker.py -v
python3 -B -m unittest discover -s platform/tests -p test_xenova_tailscale.py -v

# 별도 TOPDIR의 설정 패키지. 전체 이미지의 실행 중 TOPDIR과 구별한다.
bitbake xenova-mqtt-broker
```

- MQTT 15 tests, Tailscale 19 tests 통과, skip 없음. SSH 권한이 확대되지 않는 회귀를 추가했다.
  `LOCAL_TEST_RESULTS_FINAL.json`과 개별 로그 보존.
- 실제 native Mosquitto 2.0.18·nobody 프로세스·독립 network namespace: 실제 암호 해시,
  무인증/오인증 거절, VM별 v1 출처·v2 계정 분리·읽기 전용 계정 등 14항목 통과.
  mount/device 경계만 fixture로 대체했다. `ISOLATED_BROKER_RESULT.json`.
- 별도 TOPDIR의 최종 BitBake: 2,536 tasks 중 2,504 재사용, 실패 없음, package QA 포함.
  107.511초는 이 캐시·설정에서의 관측값이다. 전체 이미지 빌드 시간으로 해석하지 않는다.
  저우선순위·1 job, 별도 TMPDIR/DL_DIR/SSTATE_DIR를 사용했다.
- 보드 Mosquitto 2.1.2: 무인증/오인증 거절, 실제 독립 probe 수신, observer 쓰기 거절,
  내부 listener 인증, restart 후 계정·수신 유지, non-root 실행, listener 제한의 8항목 통과.
  주행·heartbeat·제어 메시지 대신 전용 probe 토픽만 사용했다.
- tailscaled만 정상 restart: `NeedsLogin` 유지, persistent state 바이트와 DNS·기본 경로·boot ID 유지.
  인증 요청 직후에는 `CorpDNS=false`였으나 미인증 재시작 시험 뒤 후속 조회에서는 true였다.
  DNS 파일은 그대로이며 인증된 설정 재사용을 수락하지 않는다. 인증 완료 뒤
  `--accept-dns=false`의 적용·재시작 유지 검증이 필요하다. `RouteAll=false`, `RunSSH=false`,
  advertised route 없음은 계속 확인했다.
- 과거 후보의 SSH 터널 수신은 통과했으나 최종 EC2 직접 접속의 근거로 사용하지 않는다.
  그때의 계정은 암호화 SSH로 메모리에만 회수했고 출력·파일에 남기지 않았다.
- 요구 정정 후 SSH 예외 한 항목을 원복했다. 현재 파일 SHA256은 원본
  `892c57f43f40be891bf7ac5ed3f50f91f725d29aedd6d58def3b78eb9a9564f3`와 같으며,
  `sshd -T` 전후 차이는 1883 한 항목 제거뿐이다. 기존 계정·ProxyJump·API 목적지는 유지했다.
- 현재 8081은 `xenova-dom0-manager.service`, 8082는 `xenova-display-feed.service`임을
  실제 PID/cgroup으로 확인했다. 틀린 HTTP 토큰은 양쪽에서 401을 반환했다.
  직접 공개 대상은 FastAPI 8081과 MQTT 1883으로 한정했다.
- 고정 Tailscale 1.104.2의 raw TCP Serve와 `--bg`를 선택했다. HTTPS 모드의 인증서 활성화
  분기와 raw TCP 분기를 [고정 버전 CLI 소스](https://github.com/tailscale/tailscale/blob/v1.104.2/cmd/tailscale/cli/serve_v2.go)에서
  대조했다. 이 기능은 이미 설치한 tailscaled에 있으며 새 런타임 의존성을 추가하지 않는다.
  문서화된 재시작 보존과 실제 EC2/보드 수락을 구분한다.
- 최종 설치 63파일·빈 디렉터리 검증. 보호 파일 14개와 Xen domain 4개(dom0 포함)의 신원·
  domid, 기본 경로와 boot ID 유지. 기본 VM의 stop/restart는 수행하지 않았다.
- 변경 문서 상대 링크 250개·명시 command anchor 검사 통과. `git diff --check` 통과.
- 진행 중인 별도 graphics 빌드의 `local.conf`·`bblayers.conf` 해시가 원 값과 같음을 대조했다.
  그 빌드의 고정 source에는 이번 변경을 끼워 넣지 않았다. 완성될 이미지가 새 MQTT 설정을
  자동 포함한다고 가정하면 안 된다.

## 확인한 실패와 수습

| 관측 | 처리와 범위 |
|---|---|
| 보드에 `ldd` 없음 | 추가 설치 없이 실제 ARM64 도구 실행·데몬 기동으로 필요한 동적 로딩 확인 |
| 보드에 `systemd-analyze` 없음 | 도구를 실행했다고 보고하지 않음. package QA·실제 unit load/유효 의존성·서비스 실행을 확인 |
| 전달 후보에 빈 `conf.d` 누락 | 계정은 보존하고 디렉터리만 보완. 최종 전달 묶음에 빈 디렉터리 명시 |
| SSH 터널의 TCP reset | 과거 후보에서 단일 허용·수신을 확인한 뒤, 최종 Tailnet 직접 접속 요구에 따라 그 허용만 원복 |
| 미인증 상태의 DNS 옵션 | 후속 조회에서 `CorpDNS=true`. DNS 원문 해시는 유지. 인증 후 `--accept-dns=false`와 재시작 설정 재검증은 미수락 |
| 초기 인증 CLI의 10초 대기 만료 | 당시 NeedsLogin을 완료로 처리하지 않음. 보드를 켠 채 새 인증 요청을 승인해 최종 Running 확인 |

과거 SSH 후보는 `/etc/ssh/xenova-auth.conf`의 기존 PermitOpen 끝에 `127.0.0.1:1883`만 추가했다.
소유 작업 폴더의 전체 후보 main/include를 `sshd -t`·`sshd -T`로 대조했으며,
달라지는 유효 설정은 위 한 항목뿐이었다. 사용자의 추가 승인 뒤 원본 해시 확인·백업·
원자 교체·문법 검사·reload를 수행했고 새 SSH/MQTT 터널 수신을 확인했다.
실제 보드의 기존 localhost 8081·8082 허용도 보존했다. 인증 정책이나 기존 ProxyJump 목록을
넓히거나 서비스 restart로 우회하지 않았다. `SSH_TUNNEL_APPLY_RESULT.json`,
`SSH_TUNNEL_RESULT.json`에 결과를 보관한다.

SSH 원본은 보드 작업 폴더의 `xenova-auth.conf.original`에 root 0600으로 보존한다.
원복 시에는 현재 정책이 적용 후보 해시와 일치하는지 확인한 뒤 이 파일만 원자적으로
복원하고 `sshd -t`·reload한다. 다른 운영자 변경이 있으면 자동 덮어쓰지 않는다.
요구 정정 뒤 위 조건을 확인해 실제 원복했고 `SSH_MQTT_EXCEPTION_RESTORED.json`에 결과를 남겼다.
원 소스·보드 SSH 정책을 유지하며 MQTT/Tailscale 파일 원복 도구가 다른 SSH 설정을 자동으로
바꾸는 것으로 해석하지 않는다.

## 최종 Tailnet 연결과 EC2 실측 — 2026-10-29

사용자는 보드를 EC2와 같은 Tailnet으로 브라우저 승인했다고 알렸으며, 현재 보드 전원이
꺼져 있다고 설명했다. SSH 접속 전 준비 프로세스는 중단했고 추가 보드 변경은 하지 않았다.
전원이 돌아온 첫 조회에서는 여전히 NeedsLogin이었다. 보드를 켠 채 새 인증 요청을 열고
사용자가 승인한 뒤 실제 Running을 확인했다. 승인 전 `NeedsLogin`일 때는
설정 도구가 아무 경로도 추가하지 않고 `pending=user_tailnet_authentication`으로 반환했다.
인증 뒤 localhost FastAPI의 기존 토큰 검사를 확인하고 아래 두 경로만 등록한다.

```sh
tailscale serve --bg --tcp=1883 tcp://127.0.0.1:1883
tailscale serve --bg --tcp=8081 tcp://127.0.0.1:8081
```

설정 도구는 기존 Serve 상태를 보드 root 전용 경로에 보존하고 다른 포트/프로필을 덮어쓰지 않는다.
두 명령은 하나의 원자 트랜잭션이 아니므로 부분 결과를 기록하고 재조회한다. 같은 두 경로의
재실행은 재사용하며 전체 `serve reset`이나 Funnel 활성화는 수행하지 않는다.
다음 결과를 실제로 확인했다.

| 검사 | 결과·증거 |
|---|---|
| Tailscale 설정 등록 | 1883→localhost:1883, 8081→localhost:8081만 background TCP Serve에 등록. Funnel·subnet route 없음 |
| 정상 daemon restart | 장치 ID·공개 키·IPv4/IPv6·Serve JSON 일치, DNS 수락 false 유지 |
| 내부 서비스 보존 | broker와 FastAPI PID 유지. 기존 MQTT 계정 바이트 유지, host boot ID·보호 파일 15개·domain 신원 4개·기본 경로/DNS 유지 |
| EC2 호스트 무인증 접속 | FastAPI 401, MQTT CONNACK 5로 네트워크 도달과 익명 거절 확인 |
| EC2 실제 인증 | 기존 FastAPI 토큰 200. 기존 backend MQTT 계정의 두 client가 같은 nonce를 독립 발행/수신 |
| 실행 중 backend 컨테이너 | 최종 기본 curl과 proxy 우회 curl 모두 401로 FastAPI 경로 도달 확인. 환경·서비스 변경 없음 |

초기 컨테이너 curl 한 번은 시간 초과였지만 후속 기본/직접 경로 검사에서는 재현되지 않았다.
proxy 환경변수는 없었다. 이를 방화벽·proxy 결함으로 확정하거나 수정하지 않았다.
검사에는 전용 `xenova/v1/probes/ec2-preflight/`만 사용했고 차량·VM 제어 메시지는 보내지 않았다.

EC2의 SSH는 사용자가 지정한 PEM으로 인증했다. 키 내용은 수집·복사하지 않았다.
API 토큰·MQTT 비밀번호는 probe의 메모리에만 전달하고 EC2 파일·명령행·로그에 남기지 않았다.
별도의 EC2 배포용 env 파일은 사용자 요청에 따라 PC의 사용자 전용 NTFS ACL 디렉터리에
보관했으며 보고서·Git에는 비밀값을 넣지 않았다. 기존 운영 env 전체를 덮어쓰는 파일이 아니다.

주요 증거는 `DIRECT_RUNTIME_VERIFIED_PUBLIC-20261029.json`,
`EC2_PUBLIC_PREFLIGHT-20261029.json`, `EC2_AUTHENTICATED_VERIFICATION-20261029.json`,
`EC2_CONTAINER_NETWORK_READONLY-20261029.json`, `EC2_CONTAINER_FINAL-20261029.json`이다.
마감 검증 기준 boot ID는 `92a32916-08cd-4fe8-98e5-9ad7d4b4d74d`다. 연결 준비 중 이전
boot ID와 달라진 것이 확인돼 새 부팅의 보호 기준을 별도로 저장했다. 에이전트는 host
재부팅을 실행하지 않았다. 정상 daemon restart 검사는 위 기준 boot 안에서 수행했다.

PC를 켜놓거나 SSH 터널을 실행하는 절차를 운영의 선행조건으로 두지 않는다.
실제 Spring env 반영·컨테이너 재생성·화면 연동은 담당자 배포 뒤 별도 수락이며,
host 전원 부팅·인터넷 단절 주입·전체 이미지/SD 수락으로 확대하지 않는다.

## EC2 담당자에게 전달한 실제 게시 버전

처음 조회는 플랫폼 잠금 충돌로 실패했으나, 최종 부팅의 `xenova-vm templates --json`과
`list --short`는 정상 완료했다. 첫 실패를 빈 카탈로그로 바꾸거나 잠금을 해제하지 않았다.

| template_id | version | 기본 vCPU | 기본 memory_mib |
|---|---|---:|---:|
| `ubuntu-24.04-arm64` | `1.0.2` | 1 | 512 |
| `debian-13-arm64` | `1.0.2` | 1 | 512 |
| `zephyr-xenvm-starter-arm64` | `1.1.0` | 1 | 16 |

현재 저장소의 Spring 템플릿 카탈로그는 세 버전 모두 1.0.0이므로 담당자가 보드와 맞춰야 한다.
게시 Zephyr 템플릿은 Network Starter이며 선택된 VM별 주행 firmware와 구분한다.
기존 backend Compose에는 MQTT 계정·비밀번호 전달 두 키가 빠져 있으므로 설정 예제도
함께 준비했다. 상위 제품 소스·실제 EC2 env는 수정하지 않았다.

초기 전달·진단 실패의 ledger와 후보는 덮어 성공으로 바꾸지 않고 별도 보존했다.
패키지 대조 도구의 최초 wildcard가 dev RPM을 고른 오류도 실제 runtime 파일명을 지정해
재검사했다. runtime RPM·설치 파일의 바이트가 달랐던 사례는 아니다.

## 백업·원복과 증거

보드 `/root/xenova-mqtt-tailscale-20261028`(0700)에 원 unit/파일 메타데이터·동적 로더/모듈
검색 메타데이터 archive·설치 ledger·검증 결과·원복 도구를 보존한다. archive의 원 파일
해시를 대조했고 원복 도구는 **계획 모드만** 실행했다. 원복은 두 서비스 정지 뒤 ledger에
이번에 생성한 것으로 기록된 파일과 현재 해시가 일치하는 경우에만 제거·메타데이터를
복원한다. 뒤에 바뀐 파일은 자동 덮어쓰지 않는다. 발급한 MQTT/Tailscale 신원은 삭제하지 않는다.

PC 증거 루트:
`C:/Users/SSAFY/Documents/Codex/2026-10-28/xenova-mqtt-tailscale-rollout/`.
여기에 고정 입력·부분 빌드 로그·원 RPM 목록/해시·최종 manifest·검증 도구·비밀을 제외한
보드 결과를 보관한다. 비밀번호·state 원문·인증 URL은 보고서/소스/공통 묶음에 남기지 않는다.

정상 사용의 최소 명령은 [MQTT 최초 설정](../platform/cli-manager-spec.md#cmd-mqtt-init)과
[Tailscale 최초 인증](../platform/cli-manager-spec.md#cmd-tailscale)을 따른다.
SD 전체 재기록 전에는 기존 [같은 보드 상태 백업·복원](../platform/cli-manager-spec.md#cmd-tailscale-backup)을
오프보드 암호화 저장소에서 준비해야 한다. 이번 미인증 설치를 실제 SD 재설치 후 신원 유지
시험이나 전원 상실 수락으로 보고하지 않는다.
