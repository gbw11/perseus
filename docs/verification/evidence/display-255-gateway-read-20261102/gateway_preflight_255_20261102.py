"""Read-only Gateway readiness check; no service/VM/control mutation."""
import json
from pathlib import Path
from board_prepare import connect, execute

REMOTE=r'''
import importlib.util,json,os,stat,subprocess
from pathlib import Path
def run(args):
 try:
  p=subprocess.run(args,capture_output=True,text=True,timeout=8)
  return {'code':p.returncode,'stdout':p.stdout.strip(),'stderr':p.stderr.strip()[:400]}
 except Exception as e:return {'error':type(e).__name__}
p=Path('/run/xenova-gateway/control.sock')
out={'host_boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
 'gateway_service':run(['systemctl','show','xenova-gateway.service','-p','ActiveState','-p','MainPID','-p','FragmentPath']),
 'socket':{'exists':p.exists()},'gateway_package':bool(importlib.util.find_spec('xenova_gateway')),
 'display_services':run(['systemctl','is-active','xenova-display-feed','xenova-display-kiosk','xenova-display-graphics'])}
if p.exists():
 info=p.lstat();out['socket'].update(uid=info.st_uid,mode=oct(stat.S_IMODE(info.st_mode)),is_socket=stat.S_ISSOCK(info.st_mode))
if out['gateway_package']:
 try:
  from xenova_gateway.client import GatewayClient
  c=GatewayClient(budget_s=.5).context()
  out['context']={k:c.get(k) for k in ['host_boot_id','owner','generation','mode','phase','output_permitted','storage_uncertain']}
  out['targets']=c.get('targets')
 except Exception as e:out['context_error']={'type':type(e).__name__,'code':getattr(e,'code',None)}
print(json.dumps(out))
'''
t=connect()
try:
 result=execute(t,REMOTE,timeout=50)
 Path(__file__).with_name('gateway-preflight-255-20261102.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(result))
finally:t.close()
