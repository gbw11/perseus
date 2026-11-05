#!/usr/bin/env python3
"""Xenova Telemetry 2.0/2.1 reference validator. Offline only; no hardware or broker access.

Requires jsonschema>=4,<5. Run `python tools/validate_contract.py --self-test`.
This is an integration-test oracle, not a recovery controller or production subscriber.
"""
from __future__ import annotations
import argparse
from decimal import Decimal, ROUND_HALF_UP, localcontext
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

APP_REGIONS = ('drive_maps','drive_paths','drive_search_nodes','drive_odometry',
               'manual_input','manual_pending','manual_running','manual_result')
APP_METRICS = ('reserved_bytes','used_bytes','free_bytes','capacity_bytes',
               'high_water_bytes','min_free_bytes','capacity_failure_total','contention_total')

def check_app_memory(p:dict[str,Any],collection_end:int)->None:
    """Occupancy is independent from input validity, retained results and permission."""
    regions={s['labels']['region'] for s in p['samples']}
    require(len(regions)==1,'APP_MEMORY_SHAPE','one owner region per message')
    region=next(iter(regions))
    values={s['name'].removeprefix('memory.app.'):s for s in p['samples']}
    require(set(values)==set(APP_METRICS) and len(p['samples'])==8,
            'APP_MEMORY_SHAPE','all eight original metrics are required')
    unsigned(p['source_sample_seq'],'app.source_sample_seq')
    statuses={(s['status'],s['reason'],s['source_rc']) for s in p['samples']}
    require(len(statuses)==1,'APP_MEMORY_STATUS','one owner observation status per region')
    observed=all(s['status']=='ok' for s in p['samples'])
    if observed:
        n={k:unsigned(v['value'],k) for k,v in values.items()}
        require(n['used_bytes']<=n['high_water_bytes']<=n['capacity_bytes']<=n['reserved_bytes']
                and n['used_bytes']+n['free_bytes']==n['capacity_bytes']
                and n['high_water_bytes']+n['min_free_bytes']==n['capacity_bytes'],
                'APP_MEMORY_ACCOUNTING','inconsistent owner occupancy/capacity/peak')
    if not region.startswith('manual_'):
        require('buffer' not in p,'MANUAL_BUFFER_SHAPE','AUTO has no MANUAL buffer')
        return
    require('buffer' in p,'MANUAL_BUFFER_SHAPE','MANUAL owner metadata must be explicit')
    b=p['buffer']
    if not observed:
        require(b is None,'MANUAL_BUFFER_STATUS','unobserved owner metadata is null')
        return
    require(isinstance(b,dict),'MANUAL_BUFFER_STATUS','observed owner needs buffer metadata')
    expected={'manual_input':'stored','manual_pending':'pending','manual_running':'running','manual_result':'retained'}
    require(b['phase'] in ('empty',expected[region]),'MANUAL_BUFFER_PHASE','phase does not match its owner slot')
    content=unsigned(b['content_bytes'],'buffer.content_bytes')
    require(content<=n['used_bytes'],'MANUAL_BUFFER_CONTENT','content is a subset of owned slot occupancy')
    if b['phase']=='empty':
        require(b['sequence'] is None and b['source_uptime_ms'] is None and content==0 and n['used_bytes']==0,
                'MANUAL_BUFFER_PHASE','empty is not retained content or work')
    else:
        unsigned(b['sequence'],'buffer.sequence')  # Envelope sequence 0 remains a real value.
        if b['source_uptime_ms'] is not None:
            require(unsigned(b['source_uptime_ms'],'buffer.source_uptime_ms')<=collection_end,
                    'MANUAL_BUFFER_TIME','original receive time is from the future')
    for key in ('context_revision','connection_revision'):
        unsigned(b[key],'buffer.'+key)
    for key in ('owner_lifetime','last_read_sequence'):
        if b[key] is not None:
            require(unsigned(b[key],'buffer.'+key)>0,'MANUAL_BUFFER_LIFETIME',key+' starts at1; unknown is null')
    if region=='manual_result':
        require(type(b['performed']) is bool,'MANUAL_BUFFER_SHAPE','result performed is explicit')
    else:
        require(b['performed'] is None and b['last_read_sequence'] is None,
                'MANUAL_BUFFER_SHAPE','only result has performed/read evidence')

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
            if p['family'] in ('memory_regions','app_memory'):
                require(unsigned(p['observed_uptime_ms'],'pool.observed')<=end,'CLOCK_ORDER','pool observation after collection')
            if current and p['family']=='memory':
                require(('source_sample_seq' in p)==('observed_uptime_ms' in p),
                        'MEMORY_SOURCE','cached memory source fields are all-or-none')
                if 'source_sample_seq' in p:
                    unsigned(p['source_sample_seq'],'memory.source_sample_seq')
                    require(unsigned(p['observed_uptime_ms'],'memory.observed')<=end,
                            'CLOCK_ORDER','cached memory observation after collection')
            seen=set()
            for v in p['samples']:
                expected=catalog.get(v['name'])
                require(expected is not None,'METRIC_CONTRACT',v['name'])
                require(all(v[k]==expected[k] for k in ('unit','kind','encoding')) and p['family']==expected['family'],'METRIC_CONTRACT',v['name'])
                require(set(v['labels'])==set(expected['labels']),'METRIC_LABELS',v['name'])
                if current and v['name']=='cpu.thread.percent':
                    require(unsigned(v['labels']['thread_id'],'thread_id')>0,'METRIC_LABELS','thread lifetime ID')
                    require(v['labels']['name_truncated'] in ('true','false') and len(v['labels']['thread_name'].encode('utf-8'))<=31,'METRIC_LABELS','thread name bytes/truncation')
                if current and p['family'] in ('memory_regions','app_memory'):
                    regions=('net_rx_data','net_tx_data') if p['family']=='memory_regions' else APP_REGIONS
                    require(v['labels']['region'] in regions,'METRIC_LABELS','unknown selected memory region')
                if 'task_epoch' in v['labels']:
                    require(re.fullmatch(SCHEMA['$defs']['uuid']['pattern'],v['labels']['task_epoch']) is not None,'METRIC_LABELS','task_epoch UUID')
                if 'counter_epoch' in v['labels']:
                    epoch=unsigned(v['labels']['counter_epoch'],'counter_epoch')
                    require(epoch>0 or v['status']!='ok','METRIC_LABELS','observed byte counter requires a ring epoch')
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
                    if v['kind']=='raw_counter':
                        bits=expected.get('raw_bits_by_region',{}).get(v['labels'].get('region'),expected['raw_bits'])
                        require(nvalue<2**bits,'RAW_COUNTER_RANGE',v['name'])
                elif enc=='i64_decimal':signed(value,v['name'])
                elif enc=='boolean':require(type(value) is bool,'MEASUREMENT_TYPE',v['name'])
                else:
                    require(type(value) in (int,float) and math.isfinite(value),'MEASUREMENT_TYPE',v['name'])
                    if v['unit']=='percent':require(0<=value<=100,'MEASUREMENT_TYPE','percent requires declared single normalized denominator')
            if current and p['family']=='app_memory':check_app_memory(p,end)
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
                if 'max_used_bytes' in r['stack']:
                    peak=r['stack']['max_used_bytes']
                    if r['stack']['status']=='ok':
                        require(peak is not None and unsigned(peak,'stack.max_used')==size-int(unused),'STACK_RANGE','stack max-use differs from fill scan')
                    else:require(peak is None,'STACK_RANGE','uncollected max-use must be null')
        else:
            if p['source_status'] not in ('ok','truncated'):
                require(p['coverage'] is None and not p['events'],'HISTORY_COVERAGE','unavailable history cannot invent counts')
            else:
                require(p['coverage'] is not None,'HISTORY_COVERAGE','collected history needs coverage')
                check_coverage(p['coverage'])
                for event in p['events']:check_event(event,m)
                require(len(p['events'])<=p['coverage']['source_retained'],'HISTORY_COVERAGE','events exceed retained')

RAM_FIELDS=('memory.firmware.ram_region_bytes','memory.firmware.mapped_bytes',
            'memory.firmware.static_data_bytes','memory.page_allocator.free_bytes')

def _ram_unknown(message:Any,reason:str,status:str='unknown')->dict[str,Any]:
    m=message if isinstance(message,dict) else {}
    return {'scope':'Zephyr 관리 RAM 확보량','status':status,'reason':reason,
            'identity':{key:(dict(m[key]) if key=='context' and isinstance(m.get(key),dict) else m.get(key)) for key in ('vm_id','boot_id','publisher_instance_id','context')},
            'source_sample_seq':None,'observed_uptime_ms':None,
            'total_bytes':None,'committed_bytes':None,'free_bytes':None,'percent':None,
            'source_status':{}}

def ram_summary(message:Any)->dict[str,Any]:
    """One complete wire message only; no joins, I/O, current-binding or live-age claim.

    Physical management total minus allocator-free pages includes static reserves.
    Bytes are exact decimal strings. Percent is100*committed/total rounded half-up
    to3 decimal places. Publication-source age is bounded; receiver age and an
    optional strict context filter are checked by ReferenceConsumer.ram_view().
    """
    result=_ram_unknown(message,'RAM_MESSAGE_TYPE','invalid')
    if not isinstance(message,dict):return result
    kind=message.get('type','')
    stream=kind.split('.',1)[1] if isinstance(kind,str) and '.' in kind else 'metrics'
    topic=f"xenova/v2/vms/{message.get('vm_id')}/telemetry/{stream}"
    try:validate(message,topic,1 if stream in ('inventory','events','snapshot') else 0,stream=='inventory')
    except ContractError as error:
        result['reason']=error.code;return result
    if message['schema_version']!='2.1':
        result.update(status='unknown',reason='RAM_SCHEMA_UNSUPPORTED');return result
    p=message['payload']
    if message['type']!='telemetry.metrics' or p['family']!='memory':
        result.update(status='unknown',reason='RAM_NOT_MEMORY');return result
    if 'source_sample_seq' not in p or 'observed_uptime_ms' not in p:
        result.update(status='unknown',reason='RAM_SOURCE_UNIDENTIFIED');return result
    result.update(source_sample_seq=p['source_sample_seq'],observed_uptime_ms=p['observed_uptime_ms'])
    selected={sample['name']:sample for sample in p['samples'] if sample['name'] in RAM_FIELDS}
    if set(selected)!=set(RAM_FIELDS):
        result.update(status='unknown',reason='RAM_FIELDS_MISSING');return result
    result['source_status']={name:{key:selected[name][key] for key in ('status','reason','source_rc')} for name in RAM_FIELDS}
    if p['collection']['status']!='ok' or p['collection']['source_rc']!=0:
        result['source_status']['collection']={key:p['collection'][key] for key in ('status','source_rc')}
        result.update(status='unknown',reason='RAM_COLLECTION_UNAVAILABLE');return result
    if any(sample['status']!='ok' or sample['source_rc']!=0 or sample['value'] is None for sample in selected.values()):
        result.update(status='unknown',reason='RAM_SOURCE_UNAVAILABLE');return result
    if int(message['clock']['sent_uptime_ms'])-int(p['observed_uptime_ms'])>2000:
        result.update(status='unknown',reason='RAM_SOURCE_EXPIRED');return result
    total,mapped,static,free=(unsigned(selected[name]['value'],name) for name in RAM_FIELDS)
    error=('RAM_TOTAL_ZERO' if total==0 else 'RAM_FREE_RANGE' if free>total else
           'RAM_MAPPED_RANGE' if mapped>total else 'RAM_STATIC_RANGE' if static>mapped else
           'RAM_MAPPED_FREE_CONFLICT' if mapped>total-free else None)
    if error:result.update(status='invalid',reason=error);return result
    committed=total-free
    with localcontext() as context:
        context.prec=50
        percent=(Decimal(committed)*100/Decimal(total)).quantize(Decimal('0.001'),rounding=ROUND_HALF_UP)
    result.update(status='ok',reason=None,total_bytes=str(total),committed_bytes=str(committed),
                  free_bytes=str(free),percent=format(percent,'f'))
    return result

class ReferenceConsumer:
    """Test-only oracle. RAM is recent observation; explicit bind opts into strict session filtering."""
    def __init__(self)->None:
        self.trusted:dict[str,tuple[str,str]]={}
        self.seen:dict[str,str]={}
        self.last:dict[tuple[str,...],int]={}
        self.received:dict[tuple[str,...],tuple[int,int]]={}
        self.parts:dict[tuple[str,...],dict[int,dict[str,Any]]]={}
        self.source_samples:dict[tuple[str,...],int]={}
        self.cpu_parts:dict[tuple[str,...],dict[int,dict[str,Any]]]={}
        self.ram_payloads:dict[str,tuple[int,int|None,dict[str,Any],str|None]]={}  # One whole RAM payload per observed VM, not a health/permission grant.
    def bind(self,vm_id:str,boot_id:str,publisher_instance_id:str)->None:
        if self.trusted.get(vm_id)!=(boot_id,publisher_instance_id):self.ram_payloads.pop(vm_id,None)
        self.trusted[vm_id]=(boot_id,publisher_instance_id)
    def ingest(self,m:dict[str,Any],received_ms:int,retained_delivery:bool=False)->str:
        stream=m['type'].split('.')[1]
        if retained_delivery:
            return 'HISTORICAL_ONLY' if stream=='inventory' else 'RETAIN_VIOLATION'
        session=(m['boot_id'],m['publisher_instance_id']);bound=self.trusted.get(m['vm_id'])
        ram=stream=='metrics' and m['payload']['family']=='memory'
        if bound!=session and (not ram or bound is not None):return 'UNBOUND_OR_RETIRED'
        key=(m['vm_id'],*session,stream)
        if ram:
            previous=self.ram_payloads.get(m['vm_id'])
            old=previous[2] if previous else None
            old_session=(old['boot_id'],old['publisher_instance_id']) if old else None
            p=m['payload']
            same_source=bool(old and session==old_session and
                p.get('source_sample_seq') is not None and p.get('observed_uptime_ms') is not None and
                (p['source_sample_seq'],p['observed_uptime_ms'])==
                (old['payload'].get('source_sample_seq'),old['payload'].get('observed_uptime_ms')))
            # Reuse stream ordering history to retire previously observed RAM
            # sessions. An unseen session is an observation, not verified recency.
            if bound is None:
                if (m['vm_id'],session[0],'retired_ram_boot') in self.last:return 'UNBOUND_OR_RETIRED'
                if old_session!=session and (*key,'memory') in self.last:return 'UNBOUND_OR_RETIRED'
        ident=m['message_id'];h=hashlib.sha256(wire_bytes(m)).hexdigest()
        if ident in self.seen:
            if self.seen[ident]==h:return 'DUPLICATE'
            if ram and same_source and previous[3] is None and ram_summary(old)['status']=='ok':
                # A conflicting identity cannot leave this same source healthy.
                # Preserve its original payload/lease and any earlier failure.
                self.ram_payloads[m['vm_id']]=(previous[0],previous[1],old,'ID_REUSE_CONFLICT')
            return 'ID_REUSE_CONFLICT'
        if ram and old_session==session and all(
            p.get(field) is not None and old['payload'].get(field) is not None
            for field in ('source_sample_seq','observed_uptime_ms')):
            source_seq,source_ms=int(p['source_sample_seq']),int(p['observed_uptime_ms'])
            old_seq,old_ms=int(old['payload']['source_sample_seq']),int(old['payload']['observed_uptime_ms'])
            if source_seq<old_seq:return 'OUT_OF_ORDER'
            if source_seq==old_seq and source_ms!=old_ms:
                if previous[3] is None and ram_summary(old)['status']=='ok':
                    self.ram_payloads[m['vm_id']]=(previous[0],previous[1],old,'RAM_SAMPLE_CONFLICT')
                return 'RAM_SAMPLE_CONFLICT'
            if source_ms<old_ms:return 'OUT_OF_ORDER'
        self.seen[ident]=h
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
        if ram:
            if bound is None and old_session and old_session[0]!=session[0]:
                self.last[(m['vm_id'],old_session[0],'retired_ram_boot')]=1
            self.last[(*key,'memory')]=seq
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
        if stream=='metrics' and p['family'] in ('memory_regions','app_memory'):origin=p['observed_uptime_ms']
        source_age=int(m['clock']['sent_uptime_ms'])-int(origin)
        if ram:
            current_ram=ram_summary(m)
            ram_age=(int(m['clock']['sent_uptime_ms'])-int(current_ram['observed_uptime_ms'])) if current_ram['observed_uptime_ms'] is not None else None
            first_received=previous[0] if same_source else received_ms
            first_age=previous[1] if same_source else ram_age
            fields=lambda value:{v['name']:v for v in value['payload']['samples'] if v['name'] in RAM_FIELDS}
            old_ram=ram_summary(old) if same_source else None
            if same_source and previous[3]:
                return previous[3]  # Conflicting source values remain tainted until a new source.
            if same_source and old_ram['status']!='ok':
                # A newer heap envelope cannot rehabilitate the failed RAM source.
                # Preserve its first failure/cause as well as its original lease.
                pass
            elif same_source and m['context']!=old['context']:
                # A changed receipt/execution does not create a new RAM source.
                # Keep the original context/lease; a new source must establish it.
                self.ram_payloads[m['vm_id']]=(first_received,first_age,old,'RAM_CONTEXT_CHANGED')
                return 'RAM_CONTEXT_CHANGED'
            elif same_source and current_ram['status']=='ok':
                if fields(m)!=fields(old):
                    self.ram_payloads[m['vm_id']]=(first_received,first_age,json.loads(wire_bytes(m)),'RAM_SAMPLE_CONFLICT')
                    return 'RAM_SAMPLE_CONFLICT'
                # Same source in a newer heap envelope never renews its first lease.
            else:
                # A new failure/null replaces old success even under the same source pair.
                self.ram_payloads[m['vm_id']]=(first_received,first_age,json.loads(wire_bytes(m)),None)
        max_age=2000 if stream in ('heartbeat','metrics') else None
        if max_age is not None and source_age>max_age:return 'SOURCE_STALE'
        
        for value_key in self.value_keys(m):
            if stream=='metrics' and p['family'] in ('cpu','memory_regions','app_memory'):
                sample=int(p['window']['sample_seq'] if p['family']=='cpu' else p['source_sample_seq'])
                if sample<=self.source_samples.get(value_key,-1):continue
                self.source_samples[value_key]=sample
            self.received[value_key]=(received_ms,source_age)
        return 'CURRENT_OBSERVATION' if stream in ('metrics','heartbeat') else 'INVENTORY_ACCEPTED'
    def ram_view(self,vm_id:str,now_ms:int,*,expected_context:dict[str,Any]|None=None)->dict[str,Any]:
        """Recent whole-payload observation, never execution health or output permission.

        No external query is required. Explicit bind and expected_context are
        opt-in strict filters; source identity and the original6s lease still apply.
        """
        cached=self.ram_payloads.get(vm_id)
        if cached is None:return _ram_unknown(None,'RAM_NOT_RECEIVED')
        received,source_age,m,source_error=cached
        result=ram_summary(m)
        def unavailable(reason:str)->dict[str,Any]:
            # View failure does not erase the observed source or its original rc.
            return dict(result,status='unknown',reason=reason,total_bytes=None,
                        committed_bytes=None,free_bytes=None,percent=None)
        bound=self.trusted.get(vm_id)
        if bound is not None and bound!=(m['boot_id'],m['publisher_instance_id']):
            return unavailable('RAM_CONTEXT_UNVERIFIED')
        if expected_context is not None and m['context']!=expected_context:
            return unavailable('RAM_CONTEXT_CHANGED')
        if source_error:return unavailable(source_error)
        if result['status']!='ok':return result
        if source_age is None:return unavailable('RAM_SOURCE_UNIDENTIFIED')
        age=now_ms-received+source_age
        if now_ms<received:return unavailable('RAM_RECEIVER_CLOCK_ORDER')
        if age>6000:return unavailable('RAM_RECEIPT_EXPIRED')
        return result
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
        ttl=6000 if stream=='heartbeat' else {'loop_progress':3000,'memory':6000,'cpu':6000,'memory_regions':6000,'app_memory':6000,'network':6000,'publisher':15000,'loop_timing':15000}[m['payload']['family']]
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

def ram_view_self_test(manifest:dict[str,Any])->int:
    cases={item['name']:load_message(BASE/item['file']) for item in manifest.get('ram_summaries',[])}
    if not cases:return 0
    m=cases['ram-committed'];vm=m['vm_id'];c=ReferenceConsumer();checks=0
    def check(condition:bool)->None:
        nonlocal checks
        require(condition,'RAM_VIEW_EXPECTATION',f'whole-payload/observation-session/freshness invariant{checks+1}');checks+=1
    check(c.ram_view(vm,1000,expected_context=m['context'])['percent'] is None)
    c.bind(vm,m['boot_id'],m['publisher_instance_id']);check(c.ingest(m,1000)=='CURRENT_OBSERVATION')
    check(c.ram_view(vm,1000,expected_context=m['context'])['percent']=='25.000')
    check(c.ram_view(vm,1000)['percent']=='25.000')
    changed=dict(m['context']);changed['execution_id']='dddddddd-dddd-4ddd-8ddd-dddddddddddd'
    check(c.ram_view(vm,1000,expected_context=changed)['reason']=='RAM_CONTEXT_CHANGED')
    check(c.ram_view(vm,7001,expected_context=m['context'])['reason']=='RAM_RECEIPT_EXPIRED')
    pool=load_message(BASE/'fixtures-2.1/memory_regions.json')
    pool.update(vm_id=vm,boot_id=m['boot_id'],publisher_instance_id=m['publisher_instance_id'],seq='100')
    pool['message_id']=f"{vm}/{m['boot_id']}/{m['publisher_instance_id']}/metrics/100";pool['context']=dict(m['context'])
    # A separate fresh pool observation at receiver+8s must not renew RAM.
    pool['payload']['collection'].update(start_uptime_ms='17999',end_uptime_ms='18000')
    pool['payload']['observed_uptime_ms']='18000';pool['payload']['source_sample_seq']='2'
    pool['clock']['sent_uptime_ms']='18002'
    check(c.ingest(pool,9000)=='CURRENT_OBSERVATION')
    check(c.ram_view(vm,9000,expected_context=m['context'])['reason']=='RAM_RECEIPT_EXPIRED')
    check(ram_summary([m,cases['ram-reserved-increase']])['percent'] is None)
    c.bind(vm,'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',m['publisher_instance_id'])
    check(c.ram_view(vm,1000,expected_context=m['context'])['reason']=='RAM_NOT_RECEIVED')
    check(c.ingest(m,1001)=='UNBOUND_OR_RETIRED')
    c.bind(vm,m['boot_id'],'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee')
    check(c.ingest(m,1002)=='UNBOUND_OR_RETIRED')
    d=ReferenceConsumer();d.bind(vm,m['boot_id'],m['publisher_instance_id'])
    check(d.ingest(m,1000)=='CURRENT_OBSERVATION')
    heap=json.loads(wire_bytes(cases['ram-heap-changed']))
    heap['seq']='2';heap['message_id']=f"{vm}/{m['boot_id']}/{m['publisher_instance_id']}/metrics/2"
    heap['payload']['collection'].update(start_uptime_ms='10999',end_uptime_ms='11000')
    heap['clock']['sent_uptime_ms']='11002'
    check(d.ingest(heap,6000)=='CURRENT_OBSERVATION')
    check(d.ram_view(vm,8000,expected_context=m['context'])['reason']=='RAM_RECEIPT_EXPIRED')
    failed=json.loads(wire_bytes(heap));failed['seq']='3'
    failed['message_id']=f"{vm}/{m['boot_id']}/{m['publisher_instance_id']}/metrics/3"
    next(v for v in failed['payload']['samples'] if v['name']=='memory.page_allocator.free_bytes').update(
        value=None,status='collection_failed',reason='sample_expired',source_rc=-75)
    check(d.ingest(failed,8001)=='CURRENT_OBSERVATION')
    unavailable=d.ram_view(vm,8001,expected_context=m['context'])
    check(unavailable['status']=='unknown' and unavailable['percent'] is None and
          unavailable['source_status']['memory.page_allocator.free_bytes']['source_rc']==-75)
    same_again=json.loads(wire_bytes(heap));same_again['seq']='4'
    same_again['message_id']=f"{vm}/{m['boot_id']}/{m['publisher_instance_id']}/metrics/4"
    check(d.ingest(same_again,8002)=='CURRENT_OBSERVATION')
    unavailable=d.ram_view(vm,8002,expected_context=m['context'])
    check(unavailable['status']=='unknown' and unavailable['percent'] is None and
          unavailable['source_status']['memory.page_allocator.free_bytes']['source_rc']==-75)
    new=json.loads(wire_bytes(cases['ram-reserved-increase']));new['seq']='5'
    new['message_id']=f"{vm}/{m['boot_id']}/{m['publisher_instance_id']}/metrics/5"
    new['payload'].update(source_sample_seq='2',observed_uptime_ms='12000')
    new['payload']['collection'].update(start_uptime_ms='11999',end_uptime_ms='12000')
    new['clock']['sent_uptime_ms']='12002'
    check(d.ingest(new,8003)=='CURRENT_OBSERVATION')
    check(d.ram_view(vm,8003,expected_context=m['context'])['percent']=='50.000')
    conflict=json.loads(wire_bytes(new));conflict['seq']='6'
    conflict['message_id']=f"{vm}/{m['boot_id']}/{m['publisher_instance_id']}/metrics/6"
    next(v for v in conflict['payload']['samples'] if v['name']=='memory.page_allocator.free_bytes')['value']='12288'
    check(d.ingest(conflict,8004)=='RAM_SAMPLE_CONFLICT')
    check(d.ram_view(vm,8004,expected_context=m['context'])['reason']=='RAM_SAMPLE_CONFLICT')
    # Failure and conflict must stay sticky inside the original 6s receiver lease,
    # not merely become unknown once that lease has already expired.
    e=ReferenceConsumer();e.bind(vm,m['boot_id'],m['publisher_instance_id'])
    check(e.ingest(m,10)=='CURRENT_OBSERVATION')
    check(e.ram_view(vm,10,expected_context=m['context'])['percent']=='25.000')
    check(e.ingest(failed,20)=='CURRENT_OBSERVATION')
    failed_view=e.ram_view(vm,20,expected_context=m['context'])
    check(failed_view['status']=='unknown' and failed_view['percent'] is None and
          failed_view['source_status']['memory.page_allocator.free_bytes']['source_rc']==-75)
    check(e.ingest(same_again,30)=='CURRENT_OBSERVATION')
    check(e.ram_view(vm,30,expected_context=m['context'])==failed_view)
    check(e.ram_payloads[vm][0:2]==(10,2) and len(e.ram_payloads)==1)
    check(e.ingest(m,40)=='DUPLICATE')
    check(e.ram_view(vm,40,expected_context=m['context'])==failed_view)
    check(e.ram_view(vm,40,expected_context=changed)['reason']=='RAM_CONTEXT_CHANGED')
    check(e.ingest(new,50)=='CURRENT_OBSERVATION')
    check(e.ram_view(vm,50,expected_context=m['context'])['percent']=='50.000')
    check(e.ram_payloads[vm][0:2]==(50,2))
    check(e.ingest(conflict,60)=='RAM_SAMPLE_CONFLICT')
    same_after_conflict=json.loads(wire_bytes(new));same_after_conflict['seq']='7'
    same_after_conflict['message_id']=f"{vm}/{m['boot_id']}/{m['publisher_instance_id']}/metrics/7"
    check(e.ingest(same_after_conflict,70)=='RAM_SAMPLE_CONFLICT')
    check(e.ram_view(vm,70,expected_context=m['context'])['reason']=='RAM_SAMPLE_CONFLICT')
    check(e.ram_payloads[vm][0:2]==(50,2))
    recovered=json.loads(wire_bytes(new));recovered['seq']='8'
    recovered['message_id']=f"{vm}/{m['boot_id']}/{m['publisher_instance_id']}/metrics/8"
    recovered['payload']['source_sample_seq']='3'
    check(e.ingest(recovered,80)=='CURRENT_OBSERVATION')
    check(e.ram_view(vm,80,expected_context=m['context'])['percent']=='50.000')
    # Default RAM display needs neither bind nor an external current execution.
    # Other telemetry streams retain their explicit-binding contract.
    u=ReferenceConsumer()
    check(u.ingest(m,10)=='CURRENT_OBSERVATION')
    check(u.ram_view(vm,10)['percent']=='25.000')
    check(u.ram_view(vm,10,expected_context=changed)['reason']=='RAM_CONTEXT_CHANGED')
    check(u.ingest(failed,20)=='CURRENT_OBSERVATION')
    failed_observation=u.ram_view(vm,20)
    check(failed_observation['status']=='unknown' and failed_observation['percent'] is None and
          failed_observation['source_status']['memory.page_allocator.free_bytes']['source_rc']==-75)
    check(u.ingest(same_again,30)=='CURRENT_OBSERVATION')
    check(u.ram_view(vm,30)==failed_observation and u.ram_payloads[vm][0:2]==(10,2))
    check(u.ingest(new,50)=='CURRENT_OBSERVATION')
    check(u.ram_view(vm,50)['percent']=='50.000')
    check(u.ingest(conflict,60)=='RAM_SAMPLE_CONFLICT')
    check(u.ingest(same_after_conflict,70)=='RAM_SAMPLE_CONFLICT')
    check(u.ram_view(vm,70)['reason']=='RAM_SAMPLE_CONFLICT')
    check(u.ingest(recovered,80)=='CURRENT_OBSERVATION')
    check(u.ram_view(vm,80)['percent']=='50.000')
    newer_heap=json.loads(wire_bytes(recovered));newer_heap['seq']='9'
    newer_heap['message_id']=f"{vm}/{m['boot_id']}/{m['publisher_instance_id']}/metrics/9"
    newer_heap['payload']['collection'].update(start_uptime_ms='12999',end_uptime_ms='13000')
    newer_heap['clock']['sent_uptime_ms']='13002'
    check(u.ingest(newer_heap,6000)=='CURRENT_OBSERVATION')
    check(u.ram_payloads[vm][0:2]==(80,2))
    check(u.ram_view(vm,6078)['percent']=='50.000')  # Exactly6000ms source+receiver age.
    check(u.ram_view(vm,6079)['reason']=='RAM_RECEIPT_EXPIRED')
    check(u.ingest(newer_heap,6080)=='DUPLICATE')
    check(u.ram_view(vm,6080)['reason']=='RAM_RECEIPT_EXPIRED')
    check(u.ingest(pool,9000)=='UNBOUND_OR_RETIRED')
    check(u.ram_view(vm,9000)['reason']=='RAM_RECEIPT_EXPIRED')
    # Changing context cannot renew the lease of the same boot/publisher/source.
    contexts=ReferenceConsumer()
    check(contexts.ingest(m,1000)=='CURRENT_OBSERVATION')
    changed_receipt=json.loads(wire_bytes(heap))
    changed_receipt['context']['receipt_id']='e2222222-2222-4222-8222-222222222222'
    check(contexts.ingest(changed_receipt,6000)=='RAM_CONTEXT_CHANGED')
    check(contexts.ram_payloads[vm][0:2]==(1000,2))
    check(contexts.ram_payloads[vm][2]['context']==m['context'])
    check(contexts.ram_view(vm,8000)['reason']=='RAM_CONTEXT_CHANGED')
    check(contexts.ingest(same_again,8001)=='RAM_CONTEXT_CHANGED')
    check(contexts.ram_payloads[vm][0:2]==(1000,2))
    changed_new=json.loads(wire_bytes(new));changed_new['context']=dict(changed_receipt['context'])
    check(contexts.ingest(changed_new,8002)=='CURRENT_OBSERVATION')
    check(contexts.ram_view(vm,8002)['percent']=='50.000')
    check(contexts.ram_view(vm,8002)['identity']['context']==changed_receipt['context'])
    # A new RAM boot/publisher begins a separate observed session even when its
    # source counters/times/context match; a departed known session cannot return.
    sessions=ReferenceConsumer()
    first_failed=json.loads(wire_bytes(failed));first_failed['seq']='1'
    first_failed['message_id']=m['message_id']
    check(sessions.ingest(first_failed,10)=='CURRENT_OBSERVATION')
    check(sessions.ram_view(vm,10)['percent'] is None)
    next_boot=json.loads(wire_bytes(m));next_boot['boot_id']='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
    next_boot['message_id']=f"{vm}/{next_boot['boot_id']}/{next_boot['publisher_instance_id']}/metrics/1"
    check(sessions.ingest(next_boot,20)=='CURRENT_OBSERVATION')
    check(sessions.ram_view(vm,20)['percent']=='25.000' and sessions.ram_payloads[vm][0:2]==(20,2))
    old_boot_new_pub=json.loads(wire_bytes(m));old_boot_new_pub['publisher_instance_id']='eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee'
    old_boot_new_pub['message_id']=f"{vm}/{m['boot_id']}/{old_boot_new_pub['publisher_instance_id']}/metrics/1"
    check(sessions.ingest(old_boot_new_pub,25)=='UNBOUND_OR_RETIRED')
    check(sessions.ram_view(vm,25)['identity']['boot_id']==next_boot['boot_id'])
    check(sessions.ram_view(vm,20)['identity']['boot_id']==next_boot['boot_id'])
    check(sessions.ingest(new,30)=='UNBOUND_OR_RETIRED')
    check(sessions.ram_view(vm,30)['identity']['boot_id']==next_boot['boot_id'])
    next_pub=json.loads(wire_bytes(next_boot));next_pub['publisher_instance_id']='eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee'
    next_pub['message_id']=f"{vm}/{next_pub['boot_id']}/{next_pub['publisher_instance_id']}/metrics/1"
    check(sessions.ingest(next_pub,40)=='CURRENT_OBSERVATION')
    check(sessions.ram_view(vm,40)['identity']['publisher_instance_id']==next_pub['publisher_instance_id'])
    check(sessions.ram_payloads[vm][0:2]==(40,2))
    delayed=json.loads(wire_bytes(next_boot));delayed['seq']='2'
    delayed['message_id']=f"{vm}/{delayed['boot_id']}/{delayed['publisher_instance_id']}/metrics/2"
    check(sessions.ingest(delayed,50)=='UNBOUND_OR_RETIRED')
    check(sessions.ram_view(vm,50)['identity']['publisher_instance_id']==next_pub['publisher_instance_id'])
    check(sessions.ingest(next_pub,60)=='DUPLICATE')
    check(sessions.ram_payloads[vm][0:2]==(40,2) and len(sessions.ram_payloads)==1)
    third_boot=json.loads(wire_bytes(next_pub));third_boot['boot_id']='ffffffff-ffff-4fff-8fff-ffffffffffff'
    third_boot['message_id']=f"{vm}/{third_boot['boot_id']}/{third_boot['publisher_instance_id']}/metrics/1"
    check(sessions.ingest(third_boot,65)=='CURRENT_OBSERVATION')
    check(sessions.ram_view(vm,65)['identity']['boot_id']==third_boot['boot_id'] and
          sessions.ram_view(vm,65)['percent']=='25.000')
    check(sessions.ingest(next_pub,66)=='UNBOUND_OR_RETIRED')
    check(sessions.ram_payloads[vm][0:2]==(65,2))
    # Binding is optional, but a caller that explicitly chooses it remains strict.
    sessions.bind(vm,m['boot_id'],m['publisher_instance_id'])
    check(sessions.ram_view(vm,70)['reason']=='RAM_NOT_RECEIVED')
    check(sessions.ingest(next_pub,80)=='UNBOUND_OR_RETIRED')
    explicit=json.loads(wire_bytes(new));explicit['seq']='10'
    explicit['message_id']=f"{vm}/{m['boot_id']}/{m['publisher_instance_id']}/metrics/10"
    check(sessions.ingest(explicit,90)=='CURRENT_OBSERVATION')
    check(sessions.ram_view(vm,90)['percent']=='50.000')
    check(sessions.ram_view(vm,90,expected_context=changed)['reason']=='RAM_CONTEXT_CHANGED')
    # Duplicate-envelope identity conflicts taint only the currently cached
    # source. Neither old conflicting IDs nor exact duplicates renew its lease.
    identities=ReferenceConsumer()
    check(identities.ingest(m,10)=='CURRENT_OBSERVATION')
    check(identities.ingest(m,15)=='DUPLICATE')
    check(identities.ram_view(vm,15)['percent']=='25.000')
    id_conflict=json.loads(wire_bytes(m))
    next(v for v in id_conflict['payload']['samples'] if v['name']=='memory.page_allocator.free_bytes')['value']='8192'
    check(identities.ingest(id_conflict,20)=='ID_REUSE_CONFLICT')
    check(identities.ram_view(vm,20)['reason']=='ID_REUSE_CONFLICT')
    check(identities.ram_payloads[vm][0:2]==(10,2) and identities.ram_payloads[vm][2]==m)
    check(identities.ingest(heap,30)=='ID_REUSE_CONFLICT')
    check(identities.ram_view(vm,30)['reason']=='ID_REUSE_CONFLICT')
    check(identities.ingest(new,40)=='CURRENT_OBSERVATION')
    check(identities.ram_view(vm,40)['percent']=='50.000')
    check(identities.ingest(id_conflict,50)=='ID_REUSE_CONFLICT')
    check(identities.ram_view(vm,50)['percent']=='50.000' and identities.ram_payloads[vm][0:2]==(40,2))
    errors=ReferenceConsumer();check(errors.ingest(m,10)=='CURRENT_OBSERVATION')
    check(errors.ingest(failed,20)=='CURRENT_OBSERVATION')
    failed_conflict=json.loads(wire_bytes(failed))
    next(v for v in failed_conflict['payload']['samples'] if v['name']=='memory.page_allocator.free_bytes').update(
        value='8192',status='ok',reason=None,source_rc=0)
    check(errors.ingest(failed_conflict,30)=='ID_REUSE_CONFLICT')
    check(errors.ram_view(vm,30)['source_status']['memory.page_allocator.free_bytes']['source_rc']==-75)
    check(errors.ram_payloads[vm][0:2]==(10,2))
    # Existing stream-order rejection and retained-delivery protection remain.
    ordering=ReferenceConsumer()
    check(ordering.ingest(m,1,True)=='RETAIN_VIOLATION')
    check(ordering.ram_view(vm,1)['reason']=='RAM_NOT_RECEIVED')
    check(ordering.ingest(new,10)=='CURRENT_OBSERVATION')
    check(ordering.ingest(heap,20)=='OUT_OF_ORDER')
    check(ordering.ram_view(vm,20)['percent']=='50.000')
    # Unavailable display values retain the identity/source/time/status evidence.
    expired=u.ram_view(vm,6079)
    check(expired['reason']=='RAM_RECEIPT_EXPIRED' and expired['percent'] is None and
          expired['source_sample_seq']=='3' and expired['observed_uptime_ms']=='12000' and
          expired['source_status']['memory.page_allocator.free_bytes']['source_rc']==0)
    context_view=contexts.ram_view(vm,8002,expected_context=m['context'])
    check(context_view['reason']=='RAM_CONTEXT_CHANGED' and context_view['percent'] is None and
          context_view['source_sample_seq']=='2' and context_view['observed_uptime_ms']=='12000' and
          context_view['identity']['context']==changed_receipt['context'])
    id_evidence=ReferenceConsumer();id_evidence.ingest(m,10);id_evidence.ingest(id_conflict,20)
    id_view=id_evidence.ram_view(vm,20)
    check(id_view['reason']=='ID_REUSE_CONFLICT' and id_view['percent'] is None and
          id_view['source_sample_seq']=='1' and id_view['observed_uptime_ms']=='10000' and
          id_view['source_status']['memory.page_allocator.free_bytes']['source_rc']==0)
    # A→B→A with fresh envelope IDs cannot replay A as a new RAM source.
    source_order=ReferenceConsumer();check(source_order.ingest(m,1000)=='CURRENT_OBSERVATION')
    b=json.loads(wire_bytes(new));b['seq']='2'
    b['message_id']=f"{vm}/{m['boot_id']}/{m['publisher_instance_id']}/metrics/2"
    check(source_order.ingest(b,2000)=='CURRENT_OBSERVATION')
    returned_a=json.loads(wire_bytes(m));returned_a['seq']='3'
    returned_a['message_id']=f"{vm}/{m['boot_id']}/{m['publisher_instance_id']}/metrics/3"
    returned_a['payload']['collection'].update(start_uptime_ms='11999',end_uptime_ms='12000')
    returned_a['clock']['sent_uptime_ms']='12000'  # Source age exactly2000ms is valid on wire.
    check(ram_summary(returned_a)['status']=='ok')
    check(source_order.ingest(returned_a,6000)=='OUT_OF_ORDER')
    check(source_order.ram_payloads[vm][0:2]==(2000,2))
    check(source_order.ram_view(vm,7998)['source_sample_seq']=='2' and source_order.ram_view(vm,7998)['percent']=='50.000')
    check(source_order.ram_view(vm,8000)['reason']=='RAM_RECEIPT_EXPIRED')
    reversed_time=json.loads(wire_bytes(b));reversed_time['seq']='4'
    reversed_time['message_id']=f"{vm}/{m['boot_id']}/{m['publisher_instance_id']}/metrics/4"
    reversed_time['payload'].update(source_sample_seq='3',observed_uptime_ms='11999')
    reversed_time['payload']['collection'].update(start_uptime_ms='11998',end_uptime_ms='11999')
    reversed_time['clock']['sent_uptime_ms']='12001'
    check(source_order.ingest(reversed_time,6001)=='OUT_OF_ORDER')
    check(source_order.ram_payloads[vm][0:2]==(2000,2))
    same_seq_reversed_time=json.loads(wire_bytes(reversed_time));same_seq_reversed_time['payload']['source_sample_seq']='2'
    check(source_order.ingest(same_seq_reversed_time,6001)=='RAM_SAMPLE_CONFLICT')
    check(source_order.ram_view(vm,6001)['reason']=='RAM_SAMPLE_CONFLICT' and source_order.ram_payloads[vm][0:2]==(2000,2))
    changed_source_time=json.loads(wire_bytes(b));changed_source_time['seq']='5'
    changed_source_time['message_id']=f"{vm}/{m['boot_id']}/{m['publisher_instance_id']}/metrics/5"
    changed_source_time['payload']['observed_uptime_ms']='12001'
    changed_source_time['payload']['collection'].update(start_uptime_ms='12000',end_uptime_ms='12001')
    changed_source_time['clock']['sent_uptime_ms']='12003'
    check(source_order.ingest(changed_source_time,6002)=='RAM_SAMPLE_CONFLICT')
    check(source_order.ram_view(vm,6002)['reason']=='RAM_SAMPLE_CONFLICT' and source_order.ram_payloads[vm][0:2]==(2000,2))
    same_b=json.loads(wire_bytes(b));same_b['seq']='6'
    same_b['message_id']=f"{vm}/{m['boot_id']}/{m['publisher_instance_id']}/metrics/6"
    check(source_order.ingest(same_b,6003)=='RAM_SAMPLE_CONFLICT')
    next_source=json.loads(wire_bytes(changed_source_time));next_source['seq']='7'
    next_source['message_id']=f"{vm}/{m['boot_id']}/{m['publisher_instance_id']}/metrics/7"
    next_source['payload']['source_sample_seq']='3'
    check(source_order.ingest(next_source,6100)=='CURRENT_OBSERVATION')
    check(source_order.ram_view(vm,6100)['percent']=='50.000' and source_order.ram_payloads[vm][0:2]==(6100,2))
    return checks

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
        report['current_valid'].append({'name':item['name'],'status':'PASS','wire_payload_bytes':len(wire_bytes(m))})
    report['current_invalid']=[]
    for item in current.get('invalid',[]):
        try:validate(load_message(BASE/item['file']),item['topic'],item['qos'],item['retain'])
        except ContractError as exc:
            require(exc.code==item['expected_error'],'WRONG_REJECTION',f"{item['name']}: {exc.code} != {item['expected_error']} ({exc})")
            report['current_invalid'].append({'name':item['name'],'status':'EXPECTED_REJECTION','error':exc.code})
        else:raise ContractError('INVALID_ACCEPTED',item['name'])
    report['ram_summaries']=[]
    for item in current.get('ram_summaries',[]):
        outcome=ram_summary(load_message(BASE/item['file']))
        require(all(outcome[key]==value for key,value in item['expected'].items()),
                'RAM_SUMMARY_EXPECTATION',f"{item['name']}: {outcome}")
        if outcome['status']!='ok':
            require(all(outcome[key] is None for key in ('total_bytes','committed_bytes','free_bytes','percent')),'RAM_UNKNOWN_ZERO','unknown must not become0')
        report['ram_summaries'].append({'name':item['name'],'status':'PASS','result':outcome})
    report['ram_view_assertions']=ram_view_self_test(current)
    report['summary']={'valid_pass':len(report['valid']),'invalid_rejected':len(report['invalid']),
                       'sequence_pass':len(report['sequences']),'sequence_assertions':sum(s['assertions'] for s in report['sequences']),
                       'max_valid_payload_bytes':max(s['wire_payload_bytes'] for s in report['valid']),
                       'current_2_1_valid_pass':len(report['current_valid']),
                       'current_2_1_invalid_rejected':len(report['current_invalid']),
                       'current_2_1_max_payload_bytes':max(s['wire_payload_bytes'] for s in report['current_valid']),
                       'ram_summary_pass':len(report['ram_summaries']),
                       'ram_view_assertions':report['ram_view_assertions']}
    return report

def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test',action='store_true')
    parser.add_argument('--report',type=Path)
    parser.add_argument('--message',type=Path)
    parser.add_argument('--ram-summary',type=Path,metavar='MESSAGE',help='Reference-only same-payload Zephyr managed RAM summary; no live state lookup')
    parser.add_argument('--topic');parser.add_argument('--qos',type=int,choices=[0,1,2]);parser.add_argument('--retain',action='store_true')
    args=parser.parse_args()
    if args.ram_summary is not None and (args.self_test or args.report is not None or args.message is not None or args.topic is not None or args.qos is not None or args.retain):
        parser.error('--ram-summary MESSAGE is a standalone reference mode; other validation options are not consumed')
    try:
        if args.ram_summary is not None:
            outcome=ram_summary(load_message(args.ram_summary))
            print(json.dumps(outcome,ensure_ascii=False))
            return 1 if outcome['status']=='invalid' else 0
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
