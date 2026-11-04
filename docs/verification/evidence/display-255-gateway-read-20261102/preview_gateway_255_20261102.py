"""Local built-frontend check with explicitly synthetic presentation data."""
import json
import argparse
import threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser()
parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2]/'outputs/worktrees/display-255-drive-state')
ROOT=parser.parse_args().repo.resolve()
DEST=ROOT/'docs/verification/evidence/display-255-gateway-read-20261102'
DEST.mkdir(parents=True,exist_ok=True)
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT/'frontend/apps/vm-screens/dist'),**kwargs)
    def do_GET(self):
        if self.path.startswith('/display/'):
            self.path='/index.html'
        super().do_GET()
    def log_message(self,*args):pass

def presentation(phase):
    source={'status':'fresh','age_ms':100}
    roles=['primary_drive','backup_drive','interaction']
    ids=[f'00000000-0000-4000-8000-00000000000{i}' for i in (1,2,3)]
    vms=[dict(vm_id=vm,role=role,run_id=f'run-{i}',xen_state='running',app_state='unknown',
              agent_supported=i==2,os=dict(expected='debian' if i==2 else 'zephyr',observed=None,current_run_verified=False),
              sources={key:source if key=='xen' else {'status':'unknown','age_ms':None} for key in ['xen','app','agent','inventory']},
              screen_source={'kind':'status','handle':None}) for i,(vm,role) in enumerate(zip(ids,roles))]
    uncertain=phase=='uncertain'
    active=phase=='applied'
    return dict(schema_version=1,type='display.presentation',policy_epoch='LOCAL-SYNTHETIC',revision=1,render_revision='local',
                outputs=[dict(output_id=f'monitor-{i+1}',view='overview' if i==0 else 'detail',screen='instrument' if i==0 else 'infotainment',
                              selection='pinned',vm_id=None if i==0 else ids[0]) for i in (0,1)],
                decisions={vm:dict(kind='app_waiting',label='VM 실행 중 · 앱 상태 미확인',tone='warning') for vm in ids},
                state=dict(source_epoch='LOCAL-SYNTHETIC',sequence=1,generated_at='2026-11-02T00:00:00Z',mqtt_connected=False,vms=vms,
                           drive=dict(active_vm_id=ids[0] if active else None,mode='unknown' if uncertain else 'AUTO',ai_status='unknown',source=source,
                                      authority=dict(selected_vm_id=ids[0],selected_mode='AUTO',generation=4,phase='APPLIED' if active else 'DISARMED',
                                                     output_permitted=active,storage_uncertain=uncertain))))

server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
results=[]
try:
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe',headless=True)
        try:
            for phase in ['preparing','applied','uncertain']:
                for output in ['monitor-1','monitor-2']:
                    data=presentation(phase)
                    page=browser.new_page(viewport={'width':1280,'height':720})
                    def response(route):
                        data['state']['sequence']+=1
                        route.fulfill(json=data)
                    page.route('**/api/v1/display/presentation',response)
                    page.route('**/api/v1/display/outputs/*/ack',lambda route:route.fulfill(status=204))
                    page.goto(f'http://127.0.0.1:{server.server_port}/display/{output}')
                    wanted={'preparing':'자율 모드 선택 · 구동 준비 중','applied':'자율주행','uncertain':'주행 모드 미확인'}[phase]
                    page.get_by_text(wanted,exact=True).wait_for()
                    page.evaluate('''async () => {await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));window.scrollTo(0,0)}''')
                    dimensions=page.evaluate('''() => ({width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight,
                        headerTop:document.querySelector('header').getBoundingClientRect().top,
                        footerBottom:document.querySelector('footer').getBoundingClientRect().bottom})''')
                    assert dimensions['width']<=1280 and dimensions['height']<=720 and dimensions['footerBottom']<=720 and dimensions['headerTop']>=0,dimensions
                    if phase!='applied':assert page.get_by_text('현재 구동 VM1',exact=True).count()==0
                    page.evaluate('''() => {const e=document.createElement('div');e.textContent='LOCAL SYNTHETIC DATA · NOT BOARD';
                        e.style='position:fixed;right:8px;bottom:2px;font:10px sans-serif;color:#f8b84e;z-index:99999';document.body.append(e)}''')
                    name=f'{output}-{phase}.png'
                    page.screenshot(path=str(DEST/name))
                    results.append({'output':output,'phase':phase,'label':wanted,'dimensions':dimensions,'screenshot':name})
                    page.close()
        finally:browser.close()
finally:
    server.shutdown();server.server_close();thread.join(3)
(DEST/'local-visual-summary.json').write_text(json.dumps({'environment':'local headless Chrome / synthetic API / real production build','results':results},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'checked':len(results),'all_720p_without_overflow':True}))
