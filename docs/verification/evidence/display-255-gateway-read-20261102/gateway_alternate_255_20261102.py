import json
from pathlib import Path
from board_prepare import connect,execute
REMOTE=r'''
import json,subprocess
from pathlib import Path
def run(args):
 p=subprocess.run(args,capture_output=True,text=True,timeout=8)
 return {'code':p.returncode,'stdout':p.stdout,'stderr':p.stderr[:300]}
out={}
for key,args in [('units',['systemctl','list-units','--all','--no-legend','--no-pager']),
                 ('unit_files',['systemctl','list-unit-files','--no-legend','--no-pager']),
                 ('unix',['ss','-xln'])]:
 r=run(args);r['stdout']='\n'.join(line for line in r['stdout'].splitlines() if any(s in line.lower() for s in ['gateway','control.sock']))
 out[key]=r
out['candidate_directories']=[str(p) for base in ['/opt','/run','/etc/xenova'] for p in Path(base).glob('*gateway*')]
print(json.dumps(out))
'''
t=connect()
try:
 result=execute(t,REMOTE,timeout=40)
 Path(__file__).with_name('gateway-alternate-255-20261102.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(result))
finally:t.close()
