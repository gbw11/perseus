"""Opt-in, isolated lab acceptance. Does not restart guests or install system services.

Requires amqtt==0.11.3 in the host test venv, existing ARM64 wheels and compiled Spring classes.
Broker is loopback-only on the test host; SSH forwards are test transport, not the production topology.
"""
import argparse
import asyncio
import base64
import io
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

from amqtt.broker import Broker
import httpx
import paho
import pexpect
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[5]
SSH = ["ssh", "-F", "/home/ssafy/xenova-qemu-28/keys/ssh_config", "-o", "ConnectTimeout=8"]
DOM0 = "xenova-qemu-dom0"

REMOTE_SERVER = '''import asyncio,json,socket,sys
from pathlib import Path
root=Path(__file__).parent
sys.path[:0]=[str(root/'deps'),str(root/'source')]
config=json.loads(sys.stdin.readline())
from xenova_display.main import create_app
from xenova_display.config import Binding,FeedSettings
from dom0_manager.config import Settings
from dom0_manager.drivers import build_driver
import uvicorn
settings=FeedSettings(tuple(Binding.model_validate(b) for b in config['bindings']),config['token'],mqtt_port=config['broker_port'])
app=create_app(settings,driver=build_driver(Settings(driver='xen')))
sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
print(json.dumps({'port':sock.getsockname()[1]}),flush=True)
async def run():
 server=uvicorn.Server(uvicorn.Config(app,log_level='warning',timeout_graceful_shutdown=3))
 async def watch():
  await asyncio.to_thread(sys.stdin.read)
  server.should_exit=True
 watcher=asyncio.create_task(watch())
 try:await server.serve(sockets=[sock])
 finally:watcher.cancel()
asyncio.run(run())
'''

GUEST_PUBLISHER = '''import json,pathlib,socket,struct,time,uuid
def string(s):
 b=s.encode();return struct.pack('!H',len(b))+b
def packet(header,body):
 n=len(body);out=bytearray([header])
 while True:
  digit=n%128;n//=128;out.append(digit|(128 if n else 0))
  if not n:break
 return bytes(out)+body
port=PORT_VALUE
vm='VM_VALUE'
boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
release=dict(line.split('=',1) for line in pathlib.Path('/etc/os-release').read_text().splitlines() if '=' in line)
os_id=release['ID'].strip(chr(34))
sock=socket.create_connection(('127.0.0.1',port),timeout=8)
sock.sendall(packet(16,string('MQTT')+bytes([4,2,0,30])+string('display-guest-probe-'+str(uuid.uuid4()))))
assert sock.recv(4)==bytes([32,2,0,0])
instance=str(uuid.uuid4())
for seq in range(1,9):
 for suffix,kind,payload in [('heartbeat','agent.heartbeat',{}),('inventory','agent.inventory',{'os':{'id':os_id},'kernel_release':__import__('platform').release(),'hostname':socket.gethostname()})]:
  env={'schema_version':'1.0','message_id':str(uuid.uuid4()),'vm_id':vm,'boot_id':boot,'agent_instance_id':instance,'seq':seq,'type':kind,'sent_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),'payload':payload}
  sock.sendall(packet(48,string('xenova/v1/vms/'+vm+'/'+suffix)+json.dumps(env).encode()))
 time.sleep(2)
sock.sendall(bytes([224,0]));sock.close()
print('GUEST_PROBE_FINISHED',flush=True)
'''


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def remote(command, **kwargs):
    return subprocess.run(SSH + ["-o", "BatchMode=yes", DOM0, command], check=True, capture_output=True, **kwargs)


def domains():
    rows = json.loads(remote("xl list -l", text=True).stdout)
    return {r["config"]["c_info"]["uuid"]: r["domid"] for r in rows if r["domid"] != 0}


async def eventually(call, predicate, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = await call()
            if predicate(result): return result
        except (httpx.HTTPError, OSError):
            pass
        await asyncio.sleep(0.3)
    raise TimeoutError("acceptance condition was not reached")


async def run(args):
    if remote("hostname", text=True).stdout.strip() != DOM0:
        raise RuntimeError("isolated named QEMU dom0 required")
    before = domains()
    bindings = json.loads((Path(__file__).parents[1] / "examples/qemu-bindings.json").read_text())
    assert {b['vm_id'] for b in bindings} == set(before)
    evidence = {"scope": "real QEMU domU -> MQTT/SSH test transport -> real dom0 feed -> Spring REST/WS; no HDMI",
                "before": before, "cases": [], "guest_app_telemetry": "not implemented in Zephyr Starter"}
    broker_port = free_port()
    broker = Broker({"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{broker_port}"}},
                     "plugins": {"amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True}}})
    await broker.start()
    processes = []
    guest = None
    with tempfile.TemporaryDirectory(prefix="display-step2-") as scratch_name:
        scratch = Path(scratch_name)
        try:
            remote_dir = remote("mktemp -d /tmp/display-step2-XXXXXXXX", text=True).stdout.strip()
            assert re.fullmatch(r"/tmp/display-step2-[A-Za-z0-9]+", remote_dir)
            stage = scratch / "stage"
            stage.mkdir()
            for wheel in Path("/tmp/mo04-step3-arm-wheels").glob("*.whl"):
                with zipfile.ZipFile(wheel) as z: z.extractall(stage / "deps")
            shutil.copytree(Path(paho.__file__).parent, stage / "deps/paho", ignore=shutil.ignore_patterns("__pycache__"))
            for source, name in [(ROOT / "platform/services/dom0-manager/dom0_manager", "dom0_manager"),
                                 (ROOT / "platform/services/vm-agent/mqtt-client/xenova_mqtt", "xenova_mqtt"),
                                 (Path(__file__).parents[1] / "xenova_display", "xenova_display")]:
                shutil.copytree(source, stage / "source" / name, ignore=shutil.ignore_patterns("__pycache__"))
            (stage / "server.py").write_text(REMOTE_SERVER)
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode="w:gz") as tar:
                for child in stage.iterdir(): tar.add(child, arcname=child.name)
            remote("tar -xz -C " + remote_dir, input=archive.getvalue())
            token = secrets.token_urlsafe(32)
            server_log = (scratch / "feed.log").open("w+")
            feed = subprocess.Popen(SSH + ["-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
                "-R", f"127.0.0.1:{broker_port}:127.0.0.1:{broker_port}", DOM0, "python3 -u -B " + remote_dir + "/server.py"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=server_log, text=True)
            processes.append(feed)
            feed.stdin.write(json.dumps({"bindings": bindings, "token": token, "broker_port": broker_port}) + "\n")
            feed.stdin.flush()
            line = await asyncio.wait_for(asyncio.to_thread(feed.stdout.readline), 45)
            if not line:
                server_log.seek(0); raise RuntimeError(server_log.read())
            remote_port = json.loads(line)["port"]
            feed_port = free_port()
            tunnel = subprocess.Popen(SSH + ["-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-N", "-L",
                f"127.0.0.1:{feed_port}:127.0.0.1:{remote_port}", DOM0], stdout=subprocess.DEVNULL, stderr=server_log)
            processes.append(tunnel)
            async with httpx.AsyncClient(timeout=5) as client:
                headers = {"Authorization": "Bearer " + token}
                async def state():
                    response = await client.get(f"http://127.0.0.1:{feed_port}/api/v1/display/state", headers=headers)
                    response.raise_for_status(); return response.json()
                observed = await eventually(state, lambda s: all(v["xen_state"] == "running" for v in s["vms"]) and s["mqtt_connected"])
                evidence["cases"].append("real_xen_three_domains_running")
                assert all(v['app_state'] == 'unknown' for v in observed['vms'])
                evidence["cases"].append("no_invented_guest_readiness_or_os_evidence")
                # One documented lab password attempt; no persistent guest files or account changes.
                password = re.search(r"root / (\d+)", (ROOT / "docs/build-guides/perseus-persistent-platform.md").read_text()).group(1)
                guest_code = GUEST_PUBLISHER.replace("PORT_VALUE", str(broker_port)).replace("VM_VALUE", bindings[2]['vm_id'])
                encoded = base64.b64encode(guest_code.encode()).decode()
                command = "python3 -u -c " + shlex.quote("import base64;exec(base64.b64decode('" + encoded + "'))")
                guest = pexpect.spawn(SSH[0], SSH[1:] + ["-o", "PreferredAuthentications=password", "-o", "PubkeyAuthentication=no",
                    "-o", "ExitOnForwardFailure=yes", "-R", f"127.0.0.1:{broker_port}:127.0.0.1:{broker_port}",
                    "xenova-qemu-debian", command], encoding="utf-8", timeout=20, echo=False)
                await asyncio.to_thread(guest.expect, r"[Pp]assword:")
                guest.sendline(password)
                observed = await eventually(state, lambda s: s['vms'][2]['os']['observed'] == 'debian')
                assert observed['vms'][2]['sources']['agent']['status'] == 'fresh'
                evidence['cases'].append('real_debian_guest_heartbeat_and_os_inventory_over_mqtt')
                evidence['snapshot'] = observed
                # Launch compiled current classes with the unchanged runtime dependencies, preserving the running MO-04 jar.
                with zipfile.ZipFile(ROOT / "backend/target/backend-0.0.1-SNAPSHOT.jar") as z:
                    for name in z.namelist():
                        if name.startswith("BOOT-INF/lib/") and name.endswith(".jar"):
                            (scratch / Path(name).name).write_bytes(z.read(name))
                java = "/tmp/mo04-step4-jdk/root/usr/lib/jvm/java-21-openjdk-amd64/bin/java"
                spring_port = free_port()
                env = dict(os.environ, SERVER_ADDRESS="127.0.0.1", SERVER_PORT=str(spring_port), MQTT_ENABLED="false",
                    DB_URL="jdbc:h2:mem:display-acceptance;DB_CLOSE_DELAY=-1", DISPLAY_FEED_ENABLED="true",
                    DISPLAY_FEED_BASE_URL=f"http://127.0.0.1:{feed_port}", DISPLAY_FEED_SERVICE_TOKEN=token)
                spring_log = (scratch / "spring.log").open("w+")
                spring = subprocess.Popen([java, "-cp", str(ROOT / "backend/target/classes") + ":" + str(scratch / "*"),
                    "com.xenova.backend.XenovaBackendApplication"], env=env, cwd=scratch, stdout=spring_log, stderr=spring_log)
                processes.append(spring)
                base = f"http://127.0.0.1:{spring_port}"
                async def healthy(): return (await client.get(base + "/actuator/health")).status_code
                try: await eventually(healthy, lambda code: code == 200, timeout=60)
                except Exception:
                    spring_log.seek(0); raise RuntimeError(spring_log.read()[-4000:])
                assert (await client.get(base + "/api/v1/display/state")).status_code == 401
                user = {"username": "display-probe-" + secrets.token_hex(4), "password": secrets.token_urlsafe(20)}
                assert (await client.post(base + "/api/v1/auth/signup", json=user)).status_code == 201
                jwt = (await client.post(base + "/api/v1/auth/login", json=user)).json()['access_token']
                public = await client.get(base + "/api/v1/display/state", headers={"Authorization": "Bearer " + jwt})
                assert public.status_code == 200 and token not in public.text
                assert public.json()['vms'][2]['os']['observed'] == 'debian'
                evidence['cases'].append('spring_jwt_rest_real_dom0_snapshot_and_credential_separation')
                async with connect(f"ws://127.0.0.1:{spring_port}/ws/v1/display/state") as ws:
                    await ws.send(json.dumps({"type": "auth", "access_token": jwt}))
                    assert json.loads(await ws.recv())['type'] == 'authenticated'
                    reply = json.loads(await asyncio.wait_for(ws.recv(), 8))
                    assert reply['type'] == 'display.state' and reply['state']['vms'][2]['os']['observed'] == 'debian'
                    evidence['cases'].append('spring_jwt_websocket_real_dom0_snapshot')
                    await broker.shutdown()
                    broker = None
                    disconnected = await eventually(state, lambda s: not s['mqtt_connected'])
                    assert all(v['xen_state'] == 'running' for v in disconnected['vms'])
                    assert disconnected['vms'][2]['sources']['agent']['status'] == 'disconnected'
                    evidence['cases'].append('mqtt_disconnect_preserves_local_xen_state_with_explicit_source_failure')
            evidence['after'] = domains()
            assert before == evidence['after']
            evidence['cases'].append('all_vm_domain_ids_unchanged')
            evidence['passed'] = True
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
            print(json.dumps({"passed": True, "cases": evidence['cases'], "evidence": str(args.output)}))
        finally:
            if guest is not None: guest.close(force=True)
            for proc in reversed(processes):
                if proc is feed and proc.stdin is not None:
                    proc.stdin.close()
                    try:
                        await asyncio.to_thread(proc.wait, 8)
                        continue
                    except subprocess.TimeoutExpired:
                        pass
                proc.terminate()
                try: await asyncio.to_thread(proc.wait, 8)
                except subprocess.TimeoutExpired: proc.kill(); await asyncio.to_thread(proc.wait)
            if broker is not None: await broker.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.execute: parser.error("--execute is required for the isolated QEMU probe")
    asyncio.run(run(args))
