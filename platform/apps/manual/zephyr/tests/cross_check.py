"""Python XenovaClient ↔ Zephyr 수동 앱(native_sim) 양방향 교차 확인 (S15P31E110-236 완료 조건).

Zephyr 앱이 먼저 127.0.0.1:1883 에 붙어 있어야 한다 (run-native-sim.sh 와 같은 브로커).
  Python → C : 게이트웨이(vehicle.control, retained)·백엔드(drive.input) 역할을 XenovaClient 로 보낸다
  C → Python : Zephyr 앱의 drive.command·app.heartbeat 를 XenovaClient 가 받아 봉투 검사·seq 를 통과한 것만 센다
    python3 cross_check.py <mqtt-client 경로> [vm_id]
"""
import sys
import time

sys.path.insert(0, sys.argv[1])
from xenova_mqtt import Identity, XenovaClient, topics  # noqa: E402

VM_ID = sys.argv[2] if len(sys.argv) > 2 else "22222222-2222-4222-8222-222222222222"
EVENTS = topics.app_events("manual")

received = []
observer = XenovaClient(Identity(source_id="cross-observer"), "127.0.0.1", 1883)
observer.subscribe(EVENTS, lambda m: received.append(m))  # 검사(봉투·seq)를 통과한 것만 핸들러로 온다
gateway = XenovaClient(Identity(source_id="dom0-gateway"), "127.0.0.1", 1883)
backend = XenovaClient(Identity(source_id="dom0-server"), "127.0.0.1", 1883)
for c in (observer, gateway, backend):
    assert c.start(wait_s=5), "broker connect failed"
time.sleep(0.5)


def commands(since):
    return [m.payload for m in received if m.type == "drive.command" and m.received_monotonic >= since and m.envelope["vm_id"] == VM_ID]


def state(since):
    beats = [m.payload["state"] for m in received if m.type == "app.heartbeat" and m.received_monotonic >= since]
    return beats[-1] if beats else None


results = []

# 1) Python 게이트웨이 → C: 세대·활성 앱
t = time.monotonic()
gateway.publish(topics.vehicle_control(), "vehicle.control",
                {"generation": 9, "mode": "MANUAL", "active_app": "manual", "active_vm_id": VM_ID, "reason": "cross_check"})
time.sleep(1.3)
cmds = commands(t)
results.append(("Python vehicle.control(gen 9, MANUAL) → C", bool(cmds) and cmds[-1]["generation"] == 9 and state(t) == "active",
                f"last generation={cmds[-1]['generation'] if cmds else None}, heartbeat state={state(t)}"))

# 2) Python 백엔드 → C: 키 입력 20 Hz 1초
t = time.monotonic()
end = t + 1.0
while time.monotonic() < end:
    backend.publish(topics.manual_input(), "drive.input", {"keys": ["up", "right"]})
    time.sleep(0.05)
cmds = commands(t)
moving = [c for c in cmds if c["v_mps"] > 0]
results.append(("Python drive.input(up,right) → C", len(moving) >= 40 and moving[-1]["steer_rad"] < 0,
                f"{len(moving)}/{len(cmds)} commands moving, sample={moving[-1] if moving else None}"))

# 3) 입력 끊김 → C 가 정지
t = time.monotonic()
time.sleep(0.5)
cmds = commands(t + 0.3)
results.append(("입력 끊김 → C 정지", bool(cmds) and all(c["v_mps"] == 0 for c in cmds), f"{len(cmds)} commands after 300 ms, all stop"))

# 4) Python 게이트웨이 AUTO → C standby
t = time.monotonic()
gateway.publish(topics.vehicle_control(), "vehicle.control",
                {"generation": 10, "mode": "AUTO", "active_app": "autonomous", "active_vm_id": None, "reason": "cross_check"})
time.sleep(1.3)
cmds = commands(t)
results.append(("Python vehicle.control(gen 10, AUTO) → C standby", bool(cmds) and cmds[-1]["generation"] == 10 and state(t) == "standby",
                f"last generation={cmds[-1]['generation'] if cmds else None}, heartbeat state={state(t)}"))

# 5) C → Python: 받은 전부가 XenovaClient 검사 통과 (통과한 것만 handler 로 온다), 주기
cmd_total = [m for m in received if m.type == "drive.command"]
span = cmd_total[-1].received_monotonic - cmd_total[0].received_monotonic if len(cmd_total) > 1 else 0
rate = (len(cmd_total) - 1) / span if span else 0
results.append(("C drive.command → Python XenovaClient 검사·seq 통과", rate > 45,
                f"{len(cmd_total)} commands, {rate:.1f} Hz, {sum(1 for m in received if m.type == 'app.heartbeat')} heartbeats"))

gateway.clear_retained(topics.vehicle_control())
for c in (observer, gateway, backend):
    c.stop()
for name, ok, detail in results:
    print(f"{'PASS' if ok else 'FAIL'}  {name}  — {detail}")
sys.exit(0 if all(ok for _, ok, _ in results) else 1)
