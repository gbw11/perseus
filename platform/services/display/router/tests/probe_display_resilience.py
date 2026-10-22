"""Extended virtual integration checks; called by probe_display_runtime.py.

Only faults in the owned local display lab are injected. Guest VMs are read-only.
psutil is an optional dependency of this opt-in probe, not the production service.
"""
import asyncio
import json
import statistics
import time

import psutil


async def exercise(kiosk, viewer, context, captures, driver, feed, args, evidence, wait):
    cases=evidence["cases"]
    recovery={}

    async def connected(page):
        return await page.locator('[data-connected="true"]').count()==1

    async def disconnected(page):
        return await page.locator('[data-connected="false"]').count()==1

    async def both_remote():
        return await viewer.get_by_text("두 화면 연결됨",exact=True).count()==1

    # Query transport failure is injected at the adapter; no guest stop/crash command.
    driver.unavailable=True
    try:
        await wait(lambda:all(v["sources"]["xen"]["status"]=="disconnected"
                              for v in feed.state.store.snapshot()["vms"]))
        for page in kiosk.pages.values():
            await page.locator('[data-kind="stale"]').first.wait_for()
        assert feed.state.store.snapshot()["drive"]["active_vm_id"] is None
        await kiosk.pages["monitor-1"].screenshot(path=str(args.output/"xen-source-disconnected.png"))
    finally:
        driver.unavailable=False
    started=time.monotonic()
    await wait(lambda:all(v["sources"]["xen"]["status"]=="fresh"
                          for v in feed.state.store.snapshot()["vms"]))
    recovery["xen_query_seconds"]=round(time.monotonic()-started,3)
    cases.append("query_transport_failure_marks_all_xen_sources_stale_and_recovers_without_guest_commands")

    # Interrupt only the local browser HTTP state channel, preserve its last screen.
    contexts=list(kiosk.contexts.values())
    async def abort(route): await route.abort()
    for local in contexts: await local.route("**/api/v1/display/presentation",abort)
    try:
        for page in kiosk.pages.values(): await wait(lambda page=page:disconnected(page))
        assert all(not p.is_closed() for p in kiosk.pages.values())
        await kiosk.pages["monitor-2"].screenshot(path=str(args.output/"local-feed-interrupted.png"))
    finally:
        for local in contexts: await local.unroute("**/api/v1/display/presentation",abort)
    started=time.monotonic()
    for page in kiosk.pages.values(): await wait(lambda page=page:connected(page))
    recovery["local_http_seconds"]=round(time.monotonic()-started,3)
    cases.append("local_http_interruption_preserves_windows_marks_last_data_and_reconnects")

    # Actual native browser context exit: only that output must be replaced.
    other=kiosk.contexts["monitor-2"]
    old=kiosk.pages["monitor-1"]
    await kiosk.contexts["monitor-1"].close()
    started=time.monotonic()
    async def replaced():
        page=kiosk.pages.get("monitor-1")
        try: return page is not None and page is not old and not page.is_closed() and await connected(page)
        except Exception: return False
    await wait(replaced,60)
    assert kiosk.contexts["monitor-2"] is other
    await wait(lambda:all(c.snapshot()[0]["status"]=="live" for c in captures.values()))
    await wait(both_remote)
    recovery["native_window_seconds"]=round(time.monotonic()-started,3)
    cases.append("native_browser_context_exit_restarts_only_affected_output_and_capture_recovers")

    # Network emulation belongs to the remote viewer context only.
    identities={key:id(value) for key,value in kiosk.contexts.items()}
    await context.set_offline(True)
    try:
        await viewer.get_by_text("연결 끊김 · 아래는 마지막 수신 화면입니다.",exact=True).wait_for(timeout=15000)
        await viewer.screenshot(path=str(args.output/"remote-offline.png"),full_page=True)
        seq={key:c.sequence for key,c in captures.items()}
        await asyncio.sleep(3)
        assert all(c.sequence>seq[key] for key,c in captures.items())
        assert identities=={key:id(value) for key,value in kiosk.contexts.items()}
    finally:
        await context.set_offline(False)
    started=time.monotonic()
    await wait(both_remote,30)
    recovery["remote_network_seconds"]=round(time.monotonic()-started,3)
    cases.append("remote_offline_stales_viewer_while_local_windows_and_encoders_continue_then_auto_recovers")

    # Same-host monotonic marker measurement includes capture/encode/HTTP/decode.
    # This is controlled visual change detection, not a physical Pi/network p95.
    delays=[]
    for index in range(20):
        identity="monitor-1" if index%2==0 else "monitor-2"
        color=[180+(index*3)%60,25+(index*7)%50,190+(index*2)%50]
        started=time.monotonic()
        await kiosk.pages[identity].evaluate("c=>{let e=document.getElementById('latency-proof');if(!e){e=document.createElement('div');e.id='latency-proof';document.body.append(e);}Object.assign(e.style,{position:'fixed',left:'20px',top:'80px',width:'160px',height:'90px',background:`rgb(${c.join(',')})`,zIndex:99999});}",color)
        async def received():
            return await viewer.evaluate("([id,c])=>{const x=document.getElementById(id),a=x.getContext('2d').getImageData(0,0,x.width,x.height).data;let n=0;for(let i=0;i<a.length;i+=32)if(Math.abs(a[i]-c[0])<3&&Math.abs(a[i+1]-c[1])<3&&Math.abs(a[i+2]-c[2])<3)n++;return n>100;}",[identity,color])
        # Remove prior marker before the next color to prevent a similar-color false match.
        await wait(received,10)
        delays.append(round((time.monotonic()-started)*1000,1))
        await kiosk.pages[identity].evaluate("document.getElementById('latency-proof').remove()")
        async def absent():
            return not await received()
        await wait(absent,10)
    evidence["visual_change_latency"]={"clock":"same-host monotonic; 200ms polling quantization; 20 alternating-output changes",
        "samples_ms":delays,"p95_ms":sorted(delays)[18],"max_ms":max(delays)}
    cases.append("twenty_local_visible_changes_are_observed_as_decoded_remote_pixels")

    # Sample the complete owned lab process tree (Python/feed, Xvfb, local and remote
    # Chromium, two encoders). RSS sums shared pages, not unique physical memory.
    root=psutil.Process()
    previous={}
    samples=[]
    started=time.monotonic()
    initial={key:(c.sequence,c.bytes_total) for key,c in captures.items()}
    log=(args.output/"soak-samples.jsonl").open("w")
    try:
        while time.monotonic()-started < args.soak_seconds:
            tick=time.monotonic()
            rss=0;cpu_delta=0;count=0;current={}
            for proc in [root]+root.children(recursive=True):
                try:
                    cpu=proc.cpu_times();value=cpu.user+cpu.system
                    key=(proc.pid,proc.create_time())
                    current[key]=value
                    if key in previous: cpu_delta+=max(0,value-previous[key])
                    rss+=proc.memory_info().rss;count+=1
                except (psutil.NoSuchProcess,psutil.AccessDenied): pass
            elapsed=tick-started
            interval=tick-samples[-1]["monotonic"] if samples else None
            previous=current
            statuses={key:c.snapshot()[0] for key,c in captures.items()}
            assert all(s["status"]=="live" for s in statuses.values()),statuses
            assert identities=={key:id(value) for key,value in kiosk.contexts.items()}
            assert all(not p.is_closed() for p in kiosk.pages.values())
            assert await both_remote()
            for page in kiosk.pages.values(): assert await connected(page)
            sample={"elapsed_seconds":round(elapsed,2),"monotonic":tick,"rss_mib":round(rss/2**20,2),
                    "cpu_percent_one_core":round(cpu_delta/interval*100,2) if interval else None,
                    "processes":count,"streams":{key:{k:s[k] for k in ("frame_sequence","frame_age_ms","bytes_total")} for key,s in statuses.items()}}
            samples.append(sample);log.write(json.dumps(sample)+"\n");log.flush()
            if len(samples)%6==1: print(json.dumps({"soak_seconds":round(elapsed),"rss_mib":sample["rss_mib"],"streams_live":2}),flush=True)
            await asyncio.sleep(min(5,max(0,args.soak_seconds-(time.monotonic()-started))))
    finally:
        log.close()
    duration=time.monotonic()-started
    assert len(samples)>1
    assert all(c.sequence>initial[key][0] for key,c in captures.items())
    cpu=[s["cpu_percent_one_core"] for s in samples if s["cpu_percent_one_core"] is not None]
    evidence["soak"]={"duration_seconds":round(duration,2),"sample_count":len(samples),"interval_seconds":5,
        "all_samples_live":True,"local_contexts_unchanged":True,
        "process_scope":"entire virtual lab tree including receiving Chromium; summed RSS includes shared pages",
        "cpu_percent_one_core_mean":round(statistics.mean(cpu),2),"cpu_percent_one_core_max":max(cpu),
        "rss_mib_first":samples[0]["rss_mib"],"rss_mib_last":samples[-1]["rss_mib"],
        "rss_mib_max":max(s["rss_mib"] for s in samples),
        "streams":{key:{"fps":round((c.sequence-initial[key][0])/duration,2),
                           "jpeg_payload_mbps":round((c.bytes_total-initial[key][1])*8/duration/1e6,3)} for key,c in captures.items()}}
    evidence["recovery_observations"]=recovery
    cases.append("continuous_two_screen_soak_with_live_local_and_remote_resource_samples")
    await viewer.screenshot(path=str(args.output/"remote-after-soak.png"),full_page=True)
    print(json.dumps({"extended_faults_passed":True,"soak":evidence["soak"]}),flush=True)
