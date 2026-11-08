"""Local before/after list cost; real metadata/locks, simulated Xen only."""
import argparse,ast,collections,json,statistics,time
from pathlib import Path
from unittest.mock import patch
import xenova_execution as execution
import xenova_catalog as catalog
from test_xenova_read_progress import ReadProgressTest
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--baseline',type=Path,required=True,help='unmodified xenova_execution.py from the comparison commit')
parser.add_argument('--baseline-commit',required=True)
args=parser.parse_args()
source=args.baseline.read_text()
node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='read_record')
namespace=dict(vars(execution));exec(compile(ast.Module(body=[node],type_ignores=[]),'baseline-read-record','exec'),namespace)
legacy=namespace['read_record'];candidate=execution.read_record
case=ReadProgressTest('runTest');case.setUp()
try:
 case.pair();rows=[];plain=catalog.plain
 for label in ['before','after','after','before']*3:
  counts=collections.Counter();reader=legacy if label=='before' else candidate
  def measured_plain(path,**kwargs):
   counts['canonical_checks']+=1
   return plain(path,**kwargs)
  with patch.object(execution,'read_record',reader),patch.object(catalog,'plain',measured_plain):
   start=time.perf_counter();result=case.m.fork().call('list',budget_ms=10000);elapsed=time.perf_counter()-start
  assert result['ok'],result
  assert len(result['data']['vms'])==2
  rows.append({'variant':label,'elapsed_s':elapsed,'canonical_checks':counts['canonical_checks'],'views':len(result['data']['vms'])})
 summary={label:{'samples':len([r for r in rows if r['variant']==label]),'median_s':statistics.median(r['elapsed_s'] for r in rows if r['variant']==label),'canonical_checks':sorted(set(r['canonical_checks'] for r in rows if r['variant']==label))} for label in ('before','after')}
 print(json.dumps({'scope':'local Linux fixture, two registered running VMs; Xen simulated; not board acceptance','source_baseline':args.baseline_commit,'rows':rows,'summary':summary},indent=2))
finally:case.doCleanups()
