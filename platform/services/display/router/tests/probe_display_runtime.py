"""Opt-in virtual X11 screen -> real FFmpeg capture -> remote HTTP canvas acceptance.

Reads real QEMU Xen only. No physical HDMI or Raspberry Pi performance claim.
"""
import argparse
import asyncio
from contextlib import suppress
import inspect
import json
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import tempfile
import time
from datetime import datetime, timezone

from dom0_manager.drivers.xen import XenDriver
from dom0_manager.drivers.xl import SshRunner, Xl
from playwright.async_api import async_playwright
import uvicorn

from xenova_display.config import Binding, FeedSettings
from xenova_display.kiosk import Kiosk
from xenova_display.main import create_app
from xenova_display.outputs import RuntimeConfig
from xenova_display.stream import StreamServer, create_stream_app

ROOT = Path(__file__).resolve().parents[5]
SSH = ["ssh","-F","/home/ssafy/xenova-qemu-28/keys/ssh_config","-o","BatchMode=yes","xenova-qemu-dom0"]


def domains():
    rows = json.loads(subprocess.check_output(SSH+["xl list -l"],text=True))
    return sorted((r["config"]["c_info"]["uuid"],r["domid"]) for r in rows if r["domid"] != 0)


async def wait(test, timeout=40):
    until = time.monotonic()+timeout
    while time.monotonic()<until:
        result = test()
        if inspect.isawaitable(result): result=await result
        if result: return result
        await asyncio.sleep(0.2)
    raise TimeoutError("virtual display acceptance condition not reached")


async def serve(app):
    sock=socket.socket();sock.bind(("127.0.0.1",0));sock.listen(128)
    port=sock.getsockname()[1]
    server_type=StreamServer if hasattr(app.state,"stopping") else uvicorn.Server
    server=server_type(uvicorn.Config(app,log_level="warning",timeout_graceful_shutdown=2))
    task=asyncio.create_task(server.serve(sockets=[sock]))
    await wait(lambda:server.started)
    return server,task,port


async def stop(server, task):
    server.should_exit=True
    await asyncio.wait_for(task,10)


async def run(args):
    before=domains()
    evidence={"scope":"virtual X11 root with real QEMU Xen data; actual JPEG screen capture and HTTP decode; not physical HDMI",
              "before":before,"cases":[]}
    args.output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="display-step4-") as name:
        scratch=Path(name)
        display=next(f":{n}" for n in range(90,190) if not Path(f"/tmp/.X11-unix/X{n}").exists() and not Path(f"/tmp/.X{n}-lock").exists())
        xvfb_log=(args.output/"xvfb.log").open("w")
        xvfb=subprocess.Popen(["Xvfb",display,"-screen","0","2560x720x24","-nolisten","tcp","-nolisten","unix","-noreset"],stdout=xvfb_log,stderr=xvfb_log)
        kiosk_task=None;feed_server=stream_server=None;feed_task=stream_task=None
        browser=None
        try:
            async def x_ready():
                if xvfb.poll() is not None: raise RuntimeError((args.output/"xvfb.log").read_text())
                result=await asyncio.to_thread(subprocess.run,["xrandr","--display",display,"--query"],capture_output=True,timeout=3)
                return result.returncode==0
            await wait(x_ready)
            token=secrets.token_urlsafe(32)
            settings=FeedSettings(tuple(Binding.model_validate(b) for b in json.loads(
                (Path(__file__).parents[1]/"examples/qemu-bindings.json").read_text())),token)
            class NoMqtt:
                def __init__(self,s): pass
                def start(self): pass
                def stop(self): pass
            driver=XenDriver(Xl(SshRunner("xenova-qemu-dom0",ssh=tuple(SSH[:3]))))
            feed=create_app(settings,driver=driver,mqtt_factory=NoMqtt,web_root=ROOT/"frontend/apps/vm-screens/dist")
            feed_server,feed_task,feed_port=await serve(feed)
            config=RuntimeConfig.model_validate({"backend":"virtual_x11","display":display,"feed_origin":f"http://127.0.0.1:{feed_port}",
                "fps":5,"outputs":[{"output_id":"monitor-1","connector":"virtual-left","x":0},
                                     {"output_id":"monitor-2","connector":"virtual-right","x":1280}]})
            runtime=scratch/"runtime.json"
            kiosk=Kiosk(config,token,runtime,allow_virtual=True,test_no_sandbox=True)
            kiosk_task=asyncio.create_task(kiosk.run())
            async def local_ready():
                if kiosk_task.done(): await kiosk_task
                if len(kiosk.pages)!=2: return False
                try:
                    counts=await asyncio.gather(*[page.locator('[data-connected="true"]').count() for page in kiosk.pages.values()])
                    return len(counts)==2 and all(count==1 for count in counts)
                except Exception: return False
            await wait(local_ready,60)
            await wait(lambda:runtime.exists() and len(json.loads(runtime.read_text())["outputs"])==2)
            await wait(lambda:all(v["renderer_alive"] and v["status"]=="mapped" for v in json.loads(runtime.read_text())["outputs"].values()))
            assert all(not v["physical_verified"] and v["applied_revision"] is None for v in json.loads(runtime.read_text())["outputs"].values())
            evidence["cases"].append("two_native_x11_browser_windows_at_explicit_nonoverlapping_rectangles")
            page1,page2=kiosk.pages["monitor-1"],kiosk.pages["monitor-2"]
            await page1.get_by_text("VM 전체 상태",exact=True).wait_for()
            await page2.get_by_text("VM 상세 화면",exact=True).wait_for()
            assert await page1.locator('[data-role]').count()==3
            evidence["cases"].append("real_qemu_state_renders_overview_and_detail")
            stream_token=secrets.token_urlsafe(32)
            stream_app=create_stream_app(config,runtime,stream_token,allow_virtual=True)
            stream_server,stream_task,stream_port=await serve(stream_app)
            captures=stream_app.state.captures
            await wait(lambda:all(c.snapshot()[0]["status"]=="live" for c in captures.values()))
            for identity,capture in captures.items():
                metadata,frame=capture.snapshot()
                assert metadata["render_correlation"]=="unverified" and not metadata["physical_verified"]
                (args.output/f"{identity}-capture.jpg").write_bytes(frame)
            evidence["cases"].append("both_ffmpeg_x11_root_mjpeg_encoders_produce_real_pixel_frames")
            async with async_playwright() as playwright:
                browser=await playwright.chromium.launch(headless=True,args=["--no-sandbox"])
                context=await browser.new_context(viewport={"width":1600,"height":1000})
                viewer=await context.new_page()
                errors=[]
                viewer.on("pageerror",lambda error:errors.append(str(error)))
                await viewer.goto(f"http://127.0.0.1:{stream_port}/demo")
                assert (await context.request.get(f"http://127.0.0.1:{stream_port}/api/v1/streams")).status==401
                await viewer.locator("#key").fill(stream_token)
                await viewer.get_by_role("button",name="화면 연결").click()
                await viewer.get_by_text("두 화면 연결됨",exact=True).wait_for(timeout=30000)
                evidence["cases"].append("remote_viewer_authenticates_and_decodes_two_streams_without_feed_credentials")
                # Visible test markers prove the remote pixels originate in each actual native root region.
                colors={"monitor-1":[220,30,50],"monitor-2":[30,210,60]}
                for identity,page in kiosk.pages.items():
                    await page.evaluate("rgb=>{const e=document.createElement('div');e.id='capture-proof';Object.assign(e.style,{position:'fixed',left:'20px',top:'80px',width:'160px',height:'90px',background:`rgb(${rgb.join(',')})`,zIndex:'99999'});document.body.append(e);}",colors[identity])
                async def count_color(identity,color):
                    return await viewer.evaluate("([id,c])=>{const x=document.getElementById(id),a=x.getContext('2d').getImageData(0,0,x.width,x.height).data;let n=0;for(let i=0;i<a.length;i+=32)if(Math.abs(a[i]-c[0])<20&&Math.abs(a[i+1]-c[1])<20&&Math.abs(a[i+2]-c[2])<20)n++;return n;}",[identity,color])
                for identity,color in colors.items():
                    await wait(lambda:count_color(identity,color))
                    assert await count_color(identity,color)>100
                assert await count_color("monitor-1",colors["monitor-2"])<20
                assert await count_color("monitor-2",colors["monitor-1"])<20
                evidence["cases"].append("unique_native_window_color_markers_arrive_in_corresponding_remote_canvas_only")
                await viewer.screenshot(path=str(args.output/"remote-viewer-proof.png"),full_page=True)
                for page in kiosk.pages.values(): await page.evaluate("document.querySelector('#capture-proof')?.remove()")
                async def markers_removed():
                    counts=await asyncio.gather(*[count_color(key,color) for key,color in colors.items()])
                    return all(count<20 for count in counts)
                await wait(markers_removed)
                await viewer.screenshot(path=str(args.output/"remote-viewer.png"),full_page=True)
                original_contexts={key:id(value) for key,value in kiosk.contexts.items()}
                epoch=captures["monitor-1"].capture_epoch
                other_sequence=captures["monitor-2"].sequence
                captures["monitor-1"].process.terminate()
                await wait(lambda:captures["monitor-1"].capture_epoch!=epoch and captures["monitor-1"].snapshot()[0]["status"]=="live")
                assert captures["monitor-2"].sequence>other_sequence
                assert original_contexts=={key:id(value) for key,value in kiosk.contexts.items()}
                evidence["cases"].append("one_encoder_restart_does_not_restart_either_local_renderer_or_other_encoder")
                started=time.monotonic();initial={k:(c.sequence,c.bytes_total) for k,c in captures.items()}
                await asyncio.sleep(5)
                duration=time.monotonic()-started
                evidence["virtual_measurement"]={k:{"fps":round((c.sequence-initial[k][0])/duration,2),
                    "mbps":round((c.bytes_total-initial[k][1])*8/duration/1e6,3)} for k,c in captures.items()}
                await stop(stream_server,stream_task);stream_server=None
                await viewer.get_by_text("연결 끊김 · 아래는 마지막 수신 화면입니다.",exact=True).wait_for(timeout=10000)
                assert original_contexts=={key:id(value) for key,value in kiosk.contexts.items()}
                assert all(not page.is_closed() for page in kiosk.pages.values())
                evidence["cases"].append("stream_service_shutdown_leaves_two_local_windows_running_and_remote_stale_banner")
                await page1.screenshot(path=str(args.output/"local-overview-after-stream-stop.png"))
                await page2.screenshot(path=str(args.output/"local-detail-after-stream-stop.png"))
                assert errors==[],errors
                evidence["cases"].append("no_remote_browser_runtime_errors")
                await browser.close();browser=None
            evidence["after"]=domains()
            assert before==evidence["after"]
            evidence["cases"].append("qemu_vm_ids_unchanged_no_vm_restart")
            evidence["passed"]=True
            evidence["completed_at"]=datetime.now(timezone.utc).isoformat()
            evidence["capture_configuration"]={"resolution_per_output":[1280,720],"fps":config.fps,"jpeg_quality":config.jpeg_quality}
            (args.output/"evidence.json").write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+"\n")
            print(json.dumps({"passed":True,"cases":evidence["cases"],"measurement":evidence["virtual_measurement"]}))
        finally:
            if browser: await browser.close()
            if stream_server: await stop(stream_server,stream_task)
            if kiosk_task:
                kiosk_task.cancel()
                with suppress(asyncio.CancelledError): await kiosk_task
            if feed_server: await stop(feed_server,feed_task)
            xvfb.terminate()
            await asyncio.to_thread(xvfb.wait,5)
            xvfb_log.close()


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute",action="store_true")
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if not args.execute: parser.error("--execute required; virtual lab only")
    asyncio.run(run(args))
