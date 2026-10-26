#!/usr/bin/env python3
"""Xenova Telemetry 2.0/2.1 reference validator. Offline only; no hardware or broker access.

Requires jsonschema>=4,<5. Run `python tools/validate_contract.py --self-test`.
This is an integration-test oracle, not a recovery controller or production subscriber.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator, FormatChecker

BASE = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((BASE/'schemas/message.schema.json').read_text(encoding='utf-8'))
CATALOG = {x['name']:x for x in json.loads((BASE/'schemas/metric-catalog.json').read_text(encoding='utf-8'))['entries']}
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
OLD_SCHEMA = json.loads((BASE/'versions/2.0/schemas/message.schema.json').read_text(encoding='utf-8'))
OLD_CATALOG = {x['name']:x for x in json.loads((BASE/'versions/2.0/schemas/metric-catalog.json').read_text(encoding='utf-8'))['entries']}
OLD_VALIDATOR = Draft202012Validator(OLD_SCHEMA, format_checker=FormatChecker())
U64_MAX = 2**64-1
I64_MIN, I64_MAX = -(2**63), 2**63-1
STREAMS = ('inventory','heartbeat','metrics','events','snapshot')

class ContractError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

def require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise ContractError(code, message)

def wire_bytes(message: dict[str,Any]) -> bytes:
    return json.dumps(message, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')

def reject_duplicate_keys(pairs: list[tuple[str,Any]]) -> dict[str,Any]:
    result:dict[str,Any] = {}
    for key,val in pairs:
        require(key not in result, 'DUPLICATE_JSON_KEY',key)
        result[key]=val
    return result

def load_message(path: Path) -> dict[str,Any]:
    def bad_constant(s:str)->None:
        raise ContractError('NONFINITE_NUMBER',s)
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=reject_duplicate_keys, parse_constant=bad_constant)

def unsigned(value: Any, field: str) -> int:
    require(isinstance(value,str) and re.fullmatch(r'0|[1-9][0-9]{0,19}',value) is not None, 'INTEGER_RANGE',field)
    n=int(value)
    require(n<=U64_MAX, 'INTEGER_RANGE',field)
    return n

def signed(value: Any, field: str) -> int:
    require(isinstance(value,str) and re.fullmatch(r'0|-?[1-9][0-9]{0,18}',value) is not None, 'INTEGER_RANGE',field)
    n=int(value)
    require(I64_MIN<=n<=I64_MAX,'INTEGER_RANGE',field)
    return n

def check_event(event:dict[str,Any],m:dict[str,Any])->None:
    n=unsigned(event['event_seq'],'event_seq')
    require(n>0,'INTEGER_RANGE','event_seq must be positive')
    require(event['event_id']==f"{m['vm_id']}/{m['boot_id']}/{n}",'EVENT_ID','event origin key')
    unsigned(event['occurred_uptime_ms'],'occurred_uptime_ms')
    require(int(event['occurred_uptime_ms'])<=int(m['payload']['collection']['end_uptime_ms']),'CLOCK_ORDER','event from future')
    require(len(event['stage'].encode('utf-8'))<=31,'STAGE_BYTES','stage includes at most 31 UTF-8 bytes')
    require(not any(ord(c)<32 or 127<=ord(c)<160 for c in event['stage']),'STAGE_BYTES','stage controls forbidden')

def check_coverage(c:dict[str,Any])->None:
    total=unsigned(c['source_total'],'source_total')
    missed=unsigned(c['missed_before_capture'],'missed_before_capture')
    unsigned(c['publisher_dropped_total'],'publisher_dropped_total')
    require(c['source_retained']<=total and missed<=total,'HISTORY_COVERAGE','counts beyond original history')

def check_distribution(d:dict[str,Any])->None:
    count=unsigned(d['count'],'distribution.count')
    buckets=[unsigned(v,'bucket') for v in d['buckets']]
    for key in ('min_us','max_us','sum_us','p95_upper_us','p99_upper_us'):
        if d[key] is not None: unsigned(d[key],key)
    if count==0:
        require(d['status']=='no_sample' and not any(buckets) and all(d[k] is None for k in ('min_us','max_us','sum_us','p95_upper_us','p99_upper_us')),'HISTOGRAM_EMPTY','no-sample fields must be null')
        require(not any(d[k] for k in ('count_saturated','sum_saturated','histogram_saturated')),'HISTOGRAM_EMPTY','empty cannot saturate')
        return
    require(d['status']!='no_sample','HISTOGRAM_EMPTY','nonempty no_sample')
    saturated=any(d[k] for k in ('count_saturated','sum_saturated','histogram_saturated'))
    require((d['status']=='saturated')==saturated,'HISTOGRAM_SATURATION','saturation status')
    if not (d['count_saturated'] or d['histogram_saturated']):
        require(sum(buckets)==count,'HISTOGRAM_COUNT','bucket sum differs from count')
    require(d['min_us'] is not None and d['max_us'] is not None and int(d['min_us'])<=int(d['max_us']),'HISTOGRAM_RANGE','min/max invalid')
    if d['sum_saturated']:
        require(d['sum_us'] is None,'HISTOGRAM_SATURATION','saturated sum must be null')
    else:
        require(d['sum_us'] is not None,'HISTOGRAM_RANGE','missing sum')
        if not d['count_saturated']:
            require(int(d['min_us'])*count<=int(d['sum_us'])<=int(d['max_us'])*count,'HISTOGRAM_RANGE','sum outside extrema')
    def upper(percent:int)->str|None:
        if saturated:return None
        rank=(count*percent+99)//100
        cumulative=0
        for i,n in enumerate(buckets):
            cumulative+=n
            if cumulative>=rank:
                return None if i==31 else str(0 if i==0 else 2**i-1)
        return None
    require(d['p95_upper_us']==upper(95) and d['p99_upper_us']==upper(99),'HISTOGRAM_QUANTILE','only histogram upper bounds are accepted')

def validate(message:dict[str,Any],topic:str,qos:int,retain:bool,raw_length:int|None=None)->None:
    try: encoded=wire_bytes(message)
    except (ValueError,TypeError) as exc:raise ContractError('NONFINITE_NUMBER',str(exc)) from exc
    require((raw_length if raw_length is not None else len(encoded))<=4096,'PAYLOAD_TOO_LARGE','UTF-8 MQTT application payload >4096 bytes')
    current=message.get('schema_version')=='2.1'
    validator=VALIDATOR if current else OLD_VALIDATOR
    catalog=CATALOG if current else OLD_CATALOG
    errors=list(validator.iter_errors(message))
    require(not errors,'SCHEMA_INVALID',str(errors[0].message) if errors else '')
    m=message; stream=m['type'].split('.')[1]
    require(topic==f"xenova/v2/vms/{m['vm_id']}/telemetry/{stream}",'TOPIC_MISMATCH','topic/VM/type mismatch')
    require(type(qos) is int and qos==(1 if stream in ('inventory','events','snapshot') else 0),'QOS_POLICY','publish QoS mismatch')
    require(type(retain) is bool and retain==(stream=='inventory'),'RETAIN_POLICY','publish RETAIN mismatch')
    n=unsigned(m['seq'],'seq');require(n>0,'INTEGER_RANGE','seq starts at 1')
    require(m['message_id']==f"{m['vm_id']}/{m['boot_id']}/{m['publisher_instance_id']}/{stream}/{n}",'MESSAGE_ID','composite ID required')
    context=m['context']
    ids=[context[k] for k in ('host_boot_id','execution_id','receipt_id')]
    require(all(v is None for v in ids) if context['binding']=='unbound' else all(v is not None for v in ids),'CONTEXT_BINDING','binding fields are all-or-none; no invented context')
    c=m['clock']; sent=unsigned(c['sent_uptime_ms'],'sent_uptime_ms')
    if c['utc_quality']=='unavailable':
        require(all(c[k] is None for k in ('sent_at','utc_source','uncertainty_ms')),'CLOCK_QUALITY','unavailable UTC must be null')
    else:
        require(c['sent_at'] is not None and c['sent_at'].endswith('Z') and c['utc_source'] is not None and c['uncertainty_ms'] is not None,'CLOCK_QUALITY','synchronized UTC needs source and bound')
    p=m['payload'];co=p['collection']
    start=unsigned(co['start_uptime_ms'],'collection.start');end=unsigned(co['end_uptime_ms'],'collection.end')
    require(start<=end<=sent,'CLOCK_ORDER','collection order is not monotonic')
    if co['elapsed_us'] is not None:unsigned(co['elapsed_us'],'collection.elapsed')
    require(co['source_rc']==0 if co['status']=='ok' else co['source_rc'] is not None and co['source_rc']<0,'COLLECTION_STATUS','collection rc mismatch')
    if stream=='heartbeat':
        for k in ('collection_failures_total','connection_attempts_total','publish_submit_failures_total','events_dropped_total'):
            unsigned(p[k],k)
        if p['last_collection_success_uptime_ms'] is not None:
            require(unsigned(p['last_collection_success_uptime_ms'],'last_success')<=end,'CLOCK_ORDER','last collector success from future')
    elif stream=='inventory':
        freq=p['clock_domains']['frequency_hz']
        if freq is not None:require(unsigned(freq,'frequency')>0,'INTEGER_RANGE','zero timer frequency')
        for cap in p['capabilities']:
            require((cap['reason'] is None) if cap['support']=='available' else bool(cap['reason']),'CAPABILITY_REASON','unsupported capability needs reason')
    elif stream=='metrics':
        if p['family']=='loop_timing':check_distribution(p['distribution'])
        else:
            if p['family']=='cpu':
                w=p['window']
                require(unsigned(w['start_uptime_ms'],'window.start')<=unsigned(w['end_uptime_ms'],'window.end')<=end,'CLOCK_ORDER','CPU window differs from collection interval')
                require(w['part_index']<w['part_count'],'CPU_WINDOW','invalid CPU part')
                if w['threads_status'] in ('ok','truncated'):
                    require(w['rows_seen'] is not None and w['rows_seen']>=w['rows_returned'],'CPU_WINDOW','invalid CPU coverage')
                else:require(w['rows_seen'] is None and w['rows_returned']==0,'CPU_WINDOW','unobserved rows are not zero source rows')
                require(w['threads_status']!='ok' or w['rows_seen']==w['rows_returned'],'CPU_WINDOW','silent thread omission')
                require(w['threads_status']!='truncated' or w['rows_seen']>w['rows_returned'],'CPU_WINDOW','false truncation')
                expected_parts=max(1,(w['rows_returned']+3)//4)
                require(w['part_count']==expected_parts,'CPU_WINDOW','part count does not match bounded rows')
                threads=[v for v in p['samples'] if v['name']=='cpu.thread.percent']
                require(len(threads)==min(4,max(0,w['rows_returned']-w['part_index']*4)),'CPU_WINDOW','missing/extra rows in part')
            if p['family']=='memory_regions':
                require(unsigned(p['observed_uptime_ms'],'pool.observed')<=end,'CLOCK_ORDER','pool observation after collection')
            seen=set()
            for v in p['samples']:
                expected=catalog.get(v['name'])
                require(expected is not None,'METRIC_CONTRACT',v['name'])
                require(all(v[k]==expected[k] for k in ('unit','kind','encoding')) and p['family']==expected['family'],'METRIC_CONTRACT',v['name'])
                require(set(v['labels'])==set(expected['labels']),'METRIC_LABELS',v['name'])
                if current and v['name']=='cpu.thread.percent':
                    require(unsigned(v['labels']['thread_id'],'thread_id')>0,'METRIC_LABELS','thread lifetime ID')
                    require(v['labels']['name_truncated'] in ('true','false') and len(v['labels']['thread_name'].encode('utf-8'))<=31,'METRIC_LABELS','thread name bytes/truncation')
                if current and p['family']=='memory_regions':
                    require(v['labels']['region'] in ('net_rx_data','net_tx_data'),'METRIC_LABELS','unknown selected memory region')
                if 'task_epoch' in v['labels']:
                    require(re.fullmatch(SCHEMA['$defs']['uuid']['pattern'],v['labels']['task_epoch']) is not None,'METRIC_LABELS','task_epoch UUID')
                key=(v['name'],tuple(sorted(v['labels'].items())))
                require(key not in seen,'METRIC_DUPLICATE',v['name']);seen.add(key)
                if v['status']!='ok':
                    require(v['value'] is None and bool(v['reason']),'MEASUREMENT_STATUS','unavailable must be null with reason')
                    if v['status']=='collection_failed':require(v['source_rc'] is not None and v['source_rc']<0,'MEASUREMENT_STATUS','failure rc required')
                    continue
                require(v['value'] is not None and v['reason'] is None and v['source_rc']==0,'MEASUREMENT_STATUS','ok requires collected value and rc0')
                value=v['value'];enc=v['encoding']
                if enc=='u64_decimal':
                    require(isinstance(value,str),'MEASUREMENT_TYPE','u64 uses decimal string')
                    nvalue=unsigned(value,v['name'])
                    if v['kind']=='raw_counter':require(nvalue<2**expected['raw_bits'],'RAW_COUNTER_RANGE',v['name'])
                elif enc=='i64_decimal':signed(value,v['name'])
                elif enc=='boolean':require(type(value) is bool,'MEASUREMENT_TYPE',v['name'])
                else:
                    require(type(value) in (int,float) and math.isfinite(value),'MEASUREMENT_TYPE',v['name'])
                    if v['unit']=='percent':require(0<=value<=100,'MEASUREMENT_TYPE','percent requires declared single normalized denominator')
    elif stream=='events':
        check_event(p['event'],m);check_coverage(p['coverage'])
    elif stream=='snapshot':
        part=p['part'];require(part['index']<part['count'],'SNAPSHOT_PART','part index must be <count')
        if p['source_status']=='ok':require(p['source_rc']==0,'COLLECTION_STATUS','snapshot ok rc')
        if p['kind']=='threads':
            if p['source_status'] in ('ok','truncated'):
                require(p['rows_seen'] is not None and p['rows_seen']>=p['rows_returned_total']>=len(p['rows']),'SNAPSHOT_COMPLETENESS','invalid row count')
                if p['source_status']=='ok':require(p['rows_seen']==p['rows_returned_total'],'SNAPSHOT_COMPLETENESS','omission without truncation')
                else:require(p['rows_seen']>p['rows_returned_total'],'SNAPSHOT_COMPLETENESS','truncated must declare omission')
            else:require(p['rows_seen'] is None and not p['rows'] and p['rows_returned_total']==0,'SNAPSHOT_COMPLETENESS','unobserved total must be null')
            ids=[r['row_id'] for r in p['rows']]
            require(len(ids)==len(set(ids)),'SNAPSHOT_COMPLETENESS','duplicate row IDs')
            if part['count']==1:require(len(p['rows'])==p['rows_returned_total'],'SNAPSHOT_COMPLETENESS','single part incomplete')
            for r in p['rows']:
                size=unsigned(r['stack']['size_bytes'],'stack.size');unused=r['stack']['unused_high_water_bytes']
                if r['stack']['status']=='ok':
                    require(unused is not None and unsigned(unused,'stack.unused')<=size and r['stack']['source_rc']==0,'STACK_RANGE','invalid stack unused/rc')
                else:require(unused is None,'STACK_RANGE','uncollected unused must be null')
        else:
            if p['source_status'] not in ('ok','truncated'):
                require(p['coverage'] is None and not p['events'],'HISTORY_COVERAGE','unavailable history cannot invent counts')
            else:
                require(p['coverage'] is not None,'HISTORY_COVERAGE','collected history needs coverage')
                check_coverage(p['coverage'])
                for event in p['events']:check_event(event,m)
                require(len(p['events'])<=p['coverage']['source_retained'],'HISTORY_COVERAGE','events exceed retained')

class ReferenceConsumer:
    """Test-only state oracle. `bind` stands for external verified context, not MQTT arrival order."""
    def __init__(self)->None:
        self.trusted:dict[str,tuple[str,str]]={}
        self.seen:dict[str,str]={}
        self.last:dict[tuple[str,...],int]={}
        self.received:dict[tuple[str,...],tuple[int,int]]={}
        self.parts:dict[tuple[str,...],dict[int,dict[str,Any]]]={}
        self.source_samples:dict[tuple[str,...],int]={}
        self.cpu_parts:dict[tuple[str,...],dict[int,dict[str,Any]]]={}
    def bind(self,vm_id:str,boot_id:str,publisher_instance_id:str)->None:
        self.trusted[vm_id]=(boot_id,publisher_instance_id)
    def ingest(self,m:dict[str,Any],received_ms:int,retained_delivery:bool=False)->str:
        stream=m['type'].split('.')[1]
        if retained_delivery:
            return 'HISTORICAL_ONLY' if stream=='inventory' else 'RETAIN_VIOLATION'
        if self.trusted.get(m['vm_id'])!=(m['boot_id'],m['publisher_instance_id']):return 'UNBOUND_OR_RETIRED'
        ident=m['message_id'];h=hashlib.sha256(wire_bytes(m)).hexdigest()
        if ident in self.seen:return 'DUPLICATE' if self.seen[ident]==h else 'ID_REUSE_CONFLICT'
        self.seen[ident]=h
        key=(m['vm_id'],m['boot_id'],m['publisher_instance_id'],stream)
        if stream=='events':return 'HISTORY_ACCEPTED'
        if stream=='snapshot':
            p=m['payload'];part=p['part'];pk=(*key,part['snapshot_id'])
            parts=self.parts.setdefault(pk,{})
            if parts:
                first=next(iter(parts.values()))
                if any(first.get(k)!=p.get(k) for k in ('collection','kind','source_status','source_rc','identity_scope','rows_seen','rows_returned_total','coverage')) or first['part']['count']!=part['count']:return 'SNAPSHOT_CONFLICT'
            if part['index'] in parts:return 'PART_DUPLICATE'
            parts[part['index']]=p
            if len(parts)<part['count']:return 'SNAPSHOT_PENDING'
            if p['kind']=='threads':
                rows=[r for pp in parts.values() for r in pp['rows']]
                if len(rows)!=p['rows_returned_total'] or len({r['row_id'] for r in rows})!=len(rows):return 'SNAPSHOT_CONFLICT'
            return 'SNAPSHOT_SOURCE_TRUNCATED' if p['source_status']=='truncated' else 'SNAPSHOT_COMPLETE' if p['source_status']=='ok' else 'SNAPSHOT_UNAVAILABLE'
        seq=int(m['seq'])
        if seq<=self.last.get(key,0):return 'OUT_OF_ORDER'
        self.last[key]=seq
        p=m['payload'];origin=p['collection']['end_uptime_ms']
        if stream=='metrics' and p['family']=='cpu':
            window=p['window'];pk=(*key,window['sample_seq'])
            parts=self.cpu_parts.setdefault(pk,{})
            identity=lambda x:{k:v for k,v in x['window'].items() if k!='part_index'}
            if parts and identity(next(iter(parts.values())))!=identity(p):return 'CPU_SAMPLE_CONFLICT'
            index=window['part_index']
            if index in parts and parts[index]!=p:return 'CPU_SAMPLE_CONFLICT'
            parts[index]=p
            rows=[s['labels']['thread_id'] for page in parts.values() for s in page['samples'] if s['name']=='cpu.thread.percent']
            if len(rows)!=len(set(rows)):return 'CPU_SAMPLE_CONFLICT'
            systems=[s for page in parts.values() for s in page['samples'] if s['name']=='cpu.system.percent']
            if any(s!=systems[0] for s in systems):return 'CPU_SAMPLE_CONFLICT'
        if stream=='metrics' and p['family']=='cpu':origin=p['window']['end_uptime_ms']
        if stream=='metrics' and p['family']=='memory_regions':origin=p['observed_uptime_ms']
        source_age=int(m['clock']['sent_uptime_ms'])-int(origin)
        max_age=2000 if stream in ('heartbeat','metrics') else None
        if max_age is not None and source_age>max_age:return 'SOURCE_STALE'
        
        for value_key in self.value_keys(m):
            if stream=='metrics' and p['family'] in ('cpu','memory_regions'):
                sample=int(p['window']['sample_seq'] if p['family']=='cpu' else p['source_sample_seq'])
                if sample<=self.source_samples.get(value_key,-1):continue
                self.source_samples[value_key]=sample
            self.received[value_key]=(received_ms,source_age)
        return 'CURRENT_OBSERVATION' if stream in ('metrics','heartbeat') else 'INVENTORY_ACCEPTED'
    @staticmethod
    def value_keys(m:dict[str,Any])->list[tuple[str,...]]:
        stream=m['type'].split('.')[1]
        base=(m['vm_id'],m['boot_id'],m['publisher_instance_id'],stream)
        if stream!='metrics':return [base]
        p=m['payload']
        if p['family']=='loop_timing':
            d=p['distribution'];return [(*base,'loop_timing',d['task_id'],d['task_epoch'],d['name'])]
        return [(*base,s['name'],json.dumps(s['labels'],sort_keys=True)) for s in p['samples']]
    def freshness(self,m:dict[str,Any],now_ms:int)->str:
        stream=m['type'].split('.')[1]
        key=self.value_keys(m)[0]
        if key not in self.received:return 'UNKNOWN'
        received,source_age=self.received[key]
        ttl=6000 if stream=='heartbeat' else {'loop_progress':3000,'memory':6000,'cpu':6000,'memory_regions':6000,'network':6000,'publisher':15000,'loop_timing':15000}[m['payload']['family']]
        return 'STALE' if now_ms-received+source_age>ttl else 'RECEIPT_LEASE_VALID'

def run_sequences()->list[dict[str,Any]]:
    reports=[]
    for path in sorted((BASE/'fixtures/sequences').glob('*.json')):
        scenario=json.loads(path.read_text(encoding='utf-8'));c=ReferenceConsumer();out=[]
        for step in scenario['steps']:
            op=step['op']
            if op=='bind':c.bind(step['vm_id'],step['boot_id'],step['publisher_instance_id']);continue
            m=load_message(BASE/step['file'])
            if op=='ingest':got=c.ingest(m,step['received_ms'],step.get('retained_delivery',False))
            elif op=='freshness':got=c.freshness(m,step['now_ms'])
            else:raise ValueError('unknown sequence operation')
            require(got==step['expected'],'SEQUENCE_EXPECTATION',f"{path.name}: {got} != {step['expected']}")
            out.append(got)
        reports.append({'name':path.stem,'assertions':len(out),'status':'PASS'})
    return reports

def self_test()->dict[str,Any]:
    Draft202012Validator.check_schema(SCHEMA)
    manifest=json.loads((BASE/'fixtures/manifest.json').read_text(encoding='utf-8'))
    report={'contract_version':'2.0','fixtures_only':True,'production_code_or_hardware_tested':False,'valid':[],'invalid':[]}
    for item in manifest['valid']:
        m=load_message(BASE/item['file']);validate(m,item['topic'],item['qos'],item['retain'])
        report['valid'].append({'name':item['name'],'status':'PASS','wire_payload_bytes':len(wire_bytes(m))})
    for item in manifest['invalid']:
        try:validate(load_message(BASE/item['file']),item['topic'],item['qos'],item['retain'])
        except ContractError as exc:
            require(exc.code==item['expected_error'],'WRONG_REJECTION',f"{item['name']}: {exc.code} != {item['expected_error']} ({exc})")
            report['invalid'].append({'name':item['name'],'status':'EXPECTED_REJECTION','error':exc.code})
        else:raise ContractError('INVALID_ACCEPTED',item['name'])
    report['sequences']=run_sequences()
    current=json.loads((BASE/'fixtures-2.1/manifest.json').read_text(encoding='utf-8'))
    report['current_valid']=[]
    for item in current['valid']:
        m=load_message(BASE/item['file']);validate(m,item['topic'],item['qos'],item['retain'])
        report['current_valid'].append({'name':item['name'],'status':'PASS'})
    report['summary']={'valid_pass':len(report['valid']),'invalid_rejected':len(report['invalid']),
                       'sequence_pass':len(report['sequences']),'sequence_assertions':sum(s['assertions'] for s in report['sequences']),
                       'max_valid_payload_bytes':max(s['wire_payload_bytes'] for s in report['valid']),
                       'current_2_1_valid_pass':len(report['current_valid'])}
    return report

def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test',action='store_true')
    parser.add_argument('--report',type=Path)
    parser.add_argument('--message',type=Path)
    parser.add_argument('--topic');parser.add_argument('--qos',type=int,choices=[0,1,2]);parser.add_argument('--retain',action='store_true')
    args=parser.parse_args()
    try:
        if args.self_test:
            report=self_test()
            if args.report:args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(json.dumps(report['summary'],ensure_ascii=False));return 0
        if args.message is None or args.topic is None or args.qos is None:parser.error('--message, --topic and --qos are required unless --self-test')
        validate(load_message(args.message),args.topic,args.qos,args.retain,raw_length=len(args.message.read_bytes()))
        print('VALID: shape and semantics, not delivery or hardware acceptance');return 0
    except (ContractError,ValueError,OSError) as exc:
        print(f"INVALID [{getattr(exc,'code','INPUT_ERROR')}]: {exc}",file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
