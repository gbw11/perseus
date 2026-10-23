# Network Starter 1.1.0 (development candidate)

Diskless 1-vCPU / 16-MiB Xen guest, with Ethernet/IPv4/ARP/ICMP/DHCP and a
reusable cooperative shutdown hook. The old Starter and G1 app are preserved.

PV console commands: net iface, net ping <IPv4>, xenova stats.
The demo hook has no driving state or physical output and says so explicitly.
xenova hook normal|reject|fail|timeout controls only this development hook.
A shutdown failure is latched until a new execution; no output is rearmed.

The xenvm board has no entropy device. Zephyr's timer test RNG is explicitly
limited to DHCP/ping identifiers and UDP source selection. It is not an
authentication, secret, cryptographic identity or vehicle authority source.
Applications needing cryptography must supply a reviewed entropy source.

Partial target build/native tests are separate from actual board acceptance.
See [the build guide](../../../../docs/platform/build-guide.md). Do not publish this candidate as a
board-accepted release before actual A/B tests.

## 공통 진단 연동

`xenova diag`는 로컬 부팅·오류·kernel heap·netfront·타이밍 snapshot을 출력하고,
`xenova diag threads`는 요청 시 스레드·스택을 추가 분석한다.
기존 `xenova stats`와 `xenova hook`은 그대로 사용한다.
기본 빌드는 주기 시험 스레드를 생성하지 않는다.
공통 API·수집 범위·오류/null 의미·계측 on/off 비교는
[Diagnostics Core](../diagnostics/README.md)를 따른다.

PV console uses the interrupt-driven serial API. The frozen Xen HVC polling
TX path discards a character when its output ring is full; FIFO TX retries
with a hypervisor yield. Keep a console reader attached while requesting
large diagnostic JSON. Console output is intrusive and is outside loop hooks.

## Telemetry Publisher 2.0

TCP와 별도의 worker-owned MQTT client를 연결한다. 인스턴스별 XenStore bootstrap이
확인된 뒤 자기 v2 telemetry 토픽으로만 발행하며, 미설정이면 로컬 Core와 네트워크는 계속 동작한다.
timer test RNG·공유 firmware의 고정 UUID·익명 인증은 사용하지 않는다.
`xenova telemetry`는 상태, `xenova telemetry history|threads`는 명시적 상세 발행 요청이다.
원천/시간/손실 매핑·bootstrap·격리 수신 검증은 [공통 Publisher](../telemetry/README.md)를 따른다.
기존 diag/stats/hook과 협조 종료를 보존하며, quiesce에서 Publisher를 먼저 중지한 뒤 netfront를 닫는다.
