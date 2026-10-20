# Zephyr 주행 VM의 MQTT 경로 결정 — 박신영·주소정(platform) 요청

> 작성: 공병우, 2026-10-20 · 관련: S15P31E110-155·236·238, [RC카 시나리오](../scenario-rc-car.md) §2·§8 D3, MQTT 계약 v1 §10

## 0. 요약

| 대상 | 해야 할 것 | 막혀 있나 |
| --- | --- | --- |
| 박신영·주소정 (platform, `platform/guests/zephyr/`) | Zephyr 주행 VM이 dom0 Mosquitto에 닿는 경로를 정한다: **PV 네트워크** 또는 **vchan + dom0 중계** | 🟡 곧 드러남 — 앱·라이브러리는 보드 없이(`native_sim`) 먼저 만들지만, 실보드 RC카 시연(자율·수동 VM이 실제로 명령을 냄)은 이 결정 없이 불가 |

## 1. 확정된 사실

| 항목 | 값 | 상태 |
| --- | --- | --- |
| 자율·수동주행 VM 게스트 | Zephyr (시나리오 §2, 10/20 결정) | ✅ |
| 현재 Zephyr 템플릿 | Zephyr 4.4.0 `xenvm`, 16 MiB, PV 콘솔·event channel·grant table만. **XenBus/PV 네트워크 미지원** (`platform/guests/zephyr/README.md`, `xen.cfg.in` 에 vif 없음) | ✅ |
| XenStore 모듈 | `platform/guests/zephyr/xen-store` (읽기 전용 VIF 관측 `xenbus_vif` 포함) | ✅ |
| 앱 쪽에 필요한 것 | 50 Hz `drive.command`(1 KB 미만, QoS 0) 발행, `vehicle/control`·`apps/manual/input` 구독. 지연 목표: 제어권 전환 ≤ 150 ms (시나리오 §6) | ✅ |

## 2. 판단이 필요한 것

| 안 | 내용 | 앱 쪽 영향 | 플랫폼 쪽 일 |
| --- | --- | --- | --- |
| A. PV 네트워크 | Zephyr에 netfront 드라이버를 붙여 xenbr0로 dom0 브로커(192.168.77.1:1883)에 직접 MQTT | 없음. C 라이브러리 zephyr 포트(Zephyr `net/mqtt` + 소켓) 그대로 | netfront 구현 또는 이식, `xen.cfg.in`에 vif, 메모리 증가 검토 |
| B. vchan + dom0 중계 | 앱은 vchan 프레임으로 봉투를 보내고 dom0 중계 프로세스가 대신 MQTT 발행·구독 | C 라이브러리에 vchan 포트 추가 (설계 [mqtt-c-client.md](../design/mqtt-c-client.md) §4에 자리만 있음) | Zephyr vchan(grant/event channel) 연결, dom0 중계 프로세스와 그 계정·ACL(앱별 username 대행) |

같이 정해 주면 좋은 것: 앱이 자기 `vm_id`를 XenStore에서 읽을 경로 (C 라이브러리 설계 §6 D5).

## 3. 내가 이미 해 둔 것 / 할 것

| 내용 | 위치 | 상태 |
| --- | --- | --- |
| 수동주행 동작 규칙 (Python 참조 구현) | `platform/apps/manual/` | 이 브랜치 MR 미병합 |
| C MQTT 라이브러리 + Zephyr 수동 앱 | `platform/apps/common/xenova-mqtt-c/`, `platform/apps/manual/zephyr/` (예정) | 진행 예정. 보드 없이 Zephyr `native_sim` + 호스트 소켓으로 브로커 접속까지 확인 |
| `platform/guests/zephyr/` 수정 | 하지 않음 | — |

## 4. 처리되면

A면 zephyr 포트로 `xenvm` 빌드를 실보드에 올려 dom0 브로커 수신을 확인하고, B면 vchan 포트를 만들어 같은 확인을 합니다.
수동주행 펌웨어 번들 게시(`prepare-xenova-zephyr.py` 경로)와 VM 자동 시작(`xenova-vm autostart-set`)은 그때 S15P31E110-238에서 같이 요청드리겠습니다.
