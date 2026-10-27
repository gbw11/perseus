#!/usr/bin/env python3
"""Persistence audit only: production file stores and two temporary SQLite projections.

No production data, service, policy, broker or device is accessed. SQLite models
cover storage transactions, not replacement Manager/Engine implementations.
Exit zero means probes completed; findings remain explicit in the JSON output.
"""
from __future__ import annotations
import argparse,copy,errno,hashlib,importlib.util,json,multiprocessing as mp,os
from pathlib import Path
import signal,sqlite3,stat,sys,tempfile,time
from unittest.mock import patch
from uuid import UUID,uuid4

ROOT=Path(__file__).resolve().parents[2]
for p in ('platform/tests','platform/services/dom0-manager','platform/services/gateway',
          'platform/services/vm-agent/mqtt-client',
          'platform/first-install','platform/guests/common',
          'platform/yocto/meta-xenova/recipes-core/xenova-platform/files'):
    sys.path.insert(0,str(ROOT/p))

def module(name,relative):
    spec=importlib.util.spec_from_file_location(name,ROOT/relative)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

def record_pair():
    from dom0_manager.recovery.state import RecoveryRecord,RecoveryStatus
    e=module('audit_event_fixture','platform/services/dom0-manager/tests/test_recovery_events.py').event(1)
    vm,rec,attempt,host,execution=map(str,[uuid4() for _ in range(5)])
    e.update(vm_id=vm,recovery_id=rec,recovery_attempt_id=attempt)
    old=RecoveryRecord(UUID(rec),UUID(vm),'HEARTBEAT_LOST','linux',RecoveryStatus.DETECTED,
                       '2026-10-27T00:00:00Z',1000)
    new=copy.deepcopy(old);new.status=RecoveryStatus.EXECUTING;new.attempt=1
    new.recovery_attempt_id=UUID(attempt);new.mode='graceful-restart'
    new.attempt_started_monotonic=1000;new.pending_events=[e]
    new.request={'vm_id':vm,'recovery_attempt_id':attempt,'mode':new.mode,
        'expected_host_boot_id':host,'expected_lifecycle_generation':0,'expected_intent_revision':3,
        'expected_execution':{'execution_id':execution},'deadline_monotonic_ms':1110000}
    from xenova_recovery import validate_request
    validate_request(new.request);old.validate();new.validate()
    return old,new,e

def quarantine_gap(home):
    from dom0_manager.recovery.state import RecoveryStore
    from dom0_manager.recovery.arming import ArmingStore
    path=home/'recoveries.json';path.write_text('{broken')
    store=RecoveryStore(path)
    with patch('builtins.open',side_effect=OSError(errno.ENOSPC,'audit injected disk full')):
        store.load()
    assert store.history_lost and not path.exists() and path.with_suffix('.json.corrupt').exists()
    reopened=RecoveryStore(path);assert reopened.load()=={} and not reopened.history_lost
    engine=module('audit_engine_fixture','platform/services/dom0-manager/tests/test_recovery_engine.py')
    world=engine.make(home,store=reopened)
    try:
        world.engine.on_judgment(engine.judgment());world.engine.tick()
        calls=len(world.platform.recover_calls());assert calls==1
    finally:world.engine.shutdown()
    arm=home/'armed.json';arm.write_text('{broken');arming=ArmingStore(arm)
    with patch.object(Path,'write_text',side_effect=OSError(errno.ENOSPC,'audit injected disk full')):
        first=arming.load()
    after=ArmingStore(arm).load()
    assert first.history_lost and not after.history_lost and arm.with_suffix('.json.corrupt').exists()
    return {'finding':'S01','result':'FAIL_BLOCKING_STATE_LOST_AFTER_QUARANTINE_AND_SAVE_FAILURE',
        'recovery_history_lost_first':store.history_lost,'recovery_history_lost_reopened':reopened.history_lost,
        'new_mock_recovery_calls_after_restart':calls,'arming_history_lost_first':first.history_lost,
        'arming_history_lost_reopened':after.history_lost,
        'boundary':'actual stores/Engine; write-open ENOSPC injection; external FakePlatform only; no real Xen'}

def append_uncertainty(home):
    from dom0_manager.recovery.events import EventLog
    _,_,event=record_pair();path=home/'events.jsonl';log=EventLog(path)
    with patch('dom0_manager.recovery.events.os.fsync',side_effect=OSError(errno.EIO,'audit fsync failure')):
        accepted=log.append(event)
    assert accepted is False and path.stat().st_size>0
    assert log.append(event) is True
    raw=path.read_text().splitlines();events=log.events()
    assert len(raw)==2 and len(events)==1
    return {'finding':'S02','result':'FAIL_FALSE_MEANS_NO_EFFECT_COMMENT; SAME_ID_DEDUP_PASSES',
        'append_return_on_failed_sync':accepted,'raw_lines_after_same_event_retry':len(raw),
        'visible_events':len(events),'event_id_preserved':events[0]['event_id']==event['event_id']}

def sync_boundaries(home):
    from dom0_manager.recovery.state import RecoveryStore
    from xenova_runtime import atomic_write
    from xenova_mqtt.drive_binding import atomic_json
    old,_,_=record_pair();calls={};real=os.fsync
    for name,save in [('worker_state',lambda:RecoveryStore(home/'recoveries.json').save({old.recovery_id:old})),
                      ('platform_atomic',lambda:atomic_write(home/'intent.json','{}')),
                      ('binding_atomic',lambda:atomic_json(home/'binding.json',{}))]:
        seen=[]
        def sync(fd):
            seen.append('directory' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file');real(fd)
        with patch.object(os,'fsync',side_effect=sync):save()
        calls[name]=seen
    assert calls['worker_state']==['file'] and calls['platform_atomic']==calls['binding_atomic']==['file','directory']
    return {'finding':'S03','result':'DEFAULT_TMPFS_CONTRACT; PERSISTENT_OVERRIDE_DURABILITY_GAP',
            'observed_fsync_calls':calls,'power_loss_tested':False}

def file_crash_child(path,record,cut):
    from dom0_manager.recovery.state import RecoveryStore,RecoveryRecord
    real=os.replace
    def replace(a,b):
        if cut=='before-replace':os.kill(os.getpid(),signal.SIGKILL)
        real(a,b)
        if cut=='after-replace':os.kill(os.getpid(),signal.SIGKILL)
    with patch.object(os,'replace',side_effect=replace):
        value=RecoveryRecord.from_json(record);RecoveryStore(Path(path)).save({value.recovery_id:value})

def file_crash_atomicity(home):
    from dom0_manager.recovery.state import RecoveryStore
    old,new,_=record_pair();rows=[]
    for cut in ('before-replace','after-replace'):
        path=home/(cut+'.json');store=RecoveryStore(path);assert store.save({old.recovery_id:old})
        process=mp.get_context('fork').Process(target=file_crash_child,args=(str(path),new.to_json(),cut))
        process.start();process.join(5);assert process.exitcode==-signal.SIGKILL
        got=RecoveryStore(path).load()[old.recovery_id].to_json()
        expected=old.to_json() if cut=='before-replace' else new.to_json()
        assert got==expected
        rows.append({'cut':cut,'status':got['status'],'pending_events':len(got['pending_events']),'half_state':False})
    return {'result':'PASS_STATE_AND_EVENT_ARE_ALREADY_ONE_ATOMIC_FILE','cuts':rows,
            'boundary':'actual file store; SIGKILL process test, not power-loss test'}

def authority_limits(home):
    from xenova_gateway.authority import AuthorityStore,AuthorityError
    from xenova_gateway.machine import Mode
    owner,host=str(uuid4()),str(uuid4());store=AuthorityStore(home/'authority',host,uid=os.getuid(),mountpoint=None)
    store.initialize(owner);store.issue(Mode.AUTO,owner,'audit',1,1,arm=False)
    original=copy.deepcopy(store.value);request=str(uuid4());assert store.consume_request(request,2)
    reopened=AuthorityStore(store.directory,host,uid=os.getuid(),mountpoint=None);reopened.load()
    assert not reopened.consume_request(request,2)
    for _ in range(127):assert reopened.consume_request(str(uuid4()),2)
    overflow=reopened.consume_request(str(uuid4()),2);assert overflow is False
    reopened.issue(Mode.MANUAL,owner,'audit-next',2,1,arm=False);higher=reopened.payload()
    # A deliberately old, internally valid backup is not evidence of latest issuance.
    store.save(original);restored=AuthorityStore(store.directory,host,uid=os.getuid(),mountpoint=None);restored.load()
    conflict=False
    try:restored.reconcile(higher)
    except AuthorityError:conflict=True
    assert conflict
    interrupted=AuthorityStore(home/'init-interrupted',host,uid=os.getuid(),mountpoint=None)
    with patch.object(interrupted,'save',side_effect=OSError(errno.EIO,'audit init interruption')):
        try:interrupted.initialize(owner)
        except OSError:pass
    blocked=False
    try:interrupted.load()
    except AuthorityError:blocked=True
    assert blocked
    return {'finding':'S06','result':'PASS_PROTECTION; REQUEST_OUTCOME_AND_REPAIR_REMAIN_SEPARATE',
        'same_request_rejected_after_restart':True,'request_ids_per_generation_limit':128,
        'overflow_rejected_without_eviction':not overflow,'old_backup_conflicts_with_higher_retained':conflict,
        'interrupted_initialization_blocks':blocked,'completed_request_result_stored':False}

def existing_counterexample_boundaries(_):
    prior=Path('/mnt/c/Users/SSAFY/Documents/Codex/2026-10-27/xenova-recovery-audit/AUDIT_PROBES.json')
    value=json.loads(prior.read_text())['probes']
    return {'result':'REUSED_IDENTICAL_PRODUCT_SOURCE',
        'stopping':value['stopping-owner'],'receipt':value['uncertain-receipt'],
        'http_stop':value['http-stop'],'binding':value['binding-invalidation'],
        'boundary':'prior exact-source probes retained; storage engine cannot resolve external effects/DTO loss'}

# SQLite comparison is deliberately limited to local stop admission and Worker state/events.
def connect(path,mode,*,existing=True,remaining_s=.05):
    if existing:
        db=sqlite3.connect(f'file:{path}?mode=rw',uri=True,timeout=max(0,remaining_s),isolation_level=None)
    else:
        db=sqlite3.connect(path,timeout=max(0,remaining_s),isolation_level=None)
    if existing and db.execute('pragma user_version').fetchone()[0]!=1:
        db.close();raise ValueError('unsupported audit schema; no implicit migration')
    db.execute('pragma foreign_keys=ON');assert db.execute('pragma foreign_keys').fetchone()[0]==1
    actual=db.execute('pragma journal_mode='+mode).fetchone()[0];assert actual.lower()==mode.lower()
    db.execute('pragma synchronous='+('EXTRA' if mode=='DELETE' else 'FULL'))
    db.execute('pragma busy_timeout='+str(max(0,int(remaining_s*1000))))
    return db

def init_control(path,mode,vm,host):
    db=connect(path,mode,existing=False)
    db.executescript('''
    CREATE TABLE intent(vm TEXT PRIMARY KEY, revision TEXT NOT NULL, desired TEXT NOT NULL CHECK(desired IN ('running','stopped')),
      lifecycle TEXT NOT NULL, host TEXT NOT NULL, accepted_operation TEXT REFERENCES operation(id) DEFERRABLE INITIALLY DEFERRED);
    CREATE TABLE operation(id TEXT PRIMARY KEY, vm TEXT NOT NULL REFERENCES intent(vm), request_key TEXT UNIQUE,
      request_hash TEXT NOT NULL, original_spec TEXT NOT NULL, deadline_ms TEXT NOT NULL,
      phase TEXT NOT NULL CHECK(phase IN ('accepted','executing','completed','uncertain')), result TEXT);
    ''')
    db.execute('pragma user_version=1')
    db.execute('insert into intent values(?,?,?,?,?,NULL)',(vm,'1','running','0',host));db.close();path.chmod(0o600)

def accept_control(path,mode,vm,host,key,spec,*,opid=None,deadline='1110000',cut=None,expected_revision='1',remaining_s=.05,absolute_deadline=None):
    body=json.dumps(spec,sort_keys=True,separators=(',',':'));digest=hashlib.sha256(body.encode()).hexdigest()
    if absolute_deadline is not None:
        remaining_s=min(remaining_s,max(0,absolute_deadline-time.monotonic()))
        if not remaining_s:raise TimeoutError('original local budget expired')
    db=connect(path,mode,remaining_s=remaining_s)
    try:
        db.execute('begin immediate')
        if absolute_deadline is not None and time.monotonic()>=absolute_deadline:
            raise TimeoutError('original local budget expired after lock wait')
        previous=db.execute('select id,vm,request_hash,deadline_ms from operation where request_key=?',(key,)).fetchone()
        if previous:
            if previous[1]!=vm or previous[2]!=digest:raise ValueError('same key with different original spec')
            db.rollback();return previous[0],previous[3]
        row=db.execute('select revision,host from intent where vm=?',(vm,)).fetchone()
        if not row or row!=(expected_revision,host):raise ValueError('intent/host changed')
        identity=opid or str(uuid4())
        db.execute('insert into operation values(?,?,?,?,?,?,?,NULL)',(identity,vm,key,digest,body,str(deadline),'accepted'))
        if cut=='before-intent':os.kill(os.getpid(),signal.SIGKILL)
        db.execute('update intent set revision=?,desired=?,accepted_operation=? where vm=?',
                   (str(int(expected_revision)+1),'stopped',identity,vm))
        if cut=='before-commit':os.kill(os.getpid(),signal.SIGKILL)
        db.commit()
        if cut=='after-commit':os.kill(os.getpid(),signal.SIGKILL)
        return identity,str(deadline)
    except BaseException:
        if db.in_transaction:db.rollback()
        raise
    finally:db.close()

def init_worker(path,mode):
    db=connect(path,mode,existing=False)
    db.executescript('''
    CREATE TABLE recovery(id TEXT PRIMARY KEY,vm TEXT NOT NULL,status TEXT NOT NULL,attempt_id TEXT UNIQUE,value_json TEXT NOT NULL);
    CREATE UNIQUE INDEX one_open_per_vm ON recovery(vm) WHERE status IN ('DETECTED','EXECUTING','VERIFYING','BACKOFF');
    CREATE TABLE event(seq INTEGER PRIMARY KEY,event_id TEXT NOT NULL UNIQUE,recovery_id TEXT NOT NULL,
      type TEXT NOT NULL CHECK(type IN ('recovery.started','recovery.completed','recovery.recovered','recovery.cancelled','recovery.failed')),
      payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB))<=8192), notify_attempted INTEGER NOT NULL DEFAULT 0 CHECK(notify_attempted IN (0,1)));
    ''')
    db.execute('pragma user_version=1');db.close();path.chmod(0o600)

def worker_commit(path,mode,record,event=None,cut=None):
    from dom0_manager.recovery.state import validate_event
    record.validate()
    if event is not None:validate_event(event)
    db=connect(path,mode)
    try:
        db.execute('begin immediate')
        detail=record.to_json()
        # Typed identity/state columns and event rows are authoritative. Do not
        # duplicate a persistent pending_events list inside a second JSON source.
        for field in ('recovery_id','vm_id','status','recovery_attempt_id','pending_events'):detail.pop(field)
        db.execute('insert into recovery values(?,?,?,?,?) on conflict(id) do update set status=excluded.status,attempt_id=excluded.attempt_id,value_json=excluded.value_json',
                   (str(record.recovery_id),str(record.vm_id),record.status.value,
                    str(record.recovery_attempt_id) if record.recovery_attempt_id else None,json.dumps(detail)))
        if cut=='after-state':os.kill(os.getpid(),signal.SIGKILL)
        if event:
            body=json.dumps(event,sort_keys=True,separators=(',',':'))
            old=db.execute('select payload from event where event_id=?',(event['event_id'],)).fetchone()
            if old and old[0]!=body:raise ValueError('event identity/content conflict')
            db.execute('insert into event(event_id,recovery_id,type,payload) values(?,?,?,?) on conflict(event_id) do nothing',
                       (event['event_id'],event['recovery_id'],event['type'],body))
        if cut=='before-commit':os.kill(os.getpid(),signal.SIGKILL)
        db.commit()
        if cut=='after-commit':os.kill(os.getpid(),signal.SIGKILL)
    except BaseException:
        if db.in_transaction:db.rollback()
        raise
    finally:db.close()

def kill_process(target,args):
    process=mp.get_context('fork').Process(target=target,args=args);process.start();process.join(5)
    assert not process.is_alive() and process.exitcode==-signal.SIGKILL

def control_crash_child(path,mode,vm,host,key,spec,opid,cut):
    accept_control(Path(path),mode,vm,host,key,spec,opid=opid,cut=cut)

def worker_crash_child(path,mode,raw,event,cut):
    from dom0_manager.recovery.state import RecoveryRecord
    worker_commit(Path(path),mode,RecoveryRecord.from_json(raw),event,cut)

def sqlite_atomicity(home,mode):
    vm,host,key,opid=map(str,[uuid4() for _ in range(4)]);spec={'vm_id':vm,'action':'stop','force':False};rows=[]
    for cut in ('before-intent','before-commit','after-commit'):
        path=home/(cut+'.db');init_control(path,mode,vm,host)
        kill_process(control_crash_child,(str(path),mode,vm,host,key,spec,opid,cut))
        db=connect(path,mode);intent=db.execute('select revision,desired,accepted_operation from intent').fetchone()
        count=db.execute('select count(*) from operation').fetchone()[0];db.close()
        committed=cut=='after-commit';assert count==int(committed)
        assert intent==(('2','stopped',opid) if committed else ('1','running',None))
        if committed:
            replay=accept_control(path,mode,vm,host,key,spec,deadline='999999999')
            assert replay==(opid,'1110000')
        rows.append({'cut':cut,'both_values_committed':committed,'half_state':False})
    old,new,event=record_pair();event_rows=[]
    for cut in ('after-state','before-commit','after-commit'):
        path=home/('event-'+cut+'.db');init_worker(path,mode);worker_commit(path,mode,old)
        kill_process(worker_crash_child,(str(path),mode,new.to_json(),event,cut))
        db=connect(path,mode);state=db.execute('select status from recovery').fetchone()[0]
        count=db.execute('select count(*) from event').fetchone()[0];db.close()
        assert (state,count)==(('EXECUTING',1) if cut=='after-commit' else ('DETECTED',0))
        worker_commit(path,mode,new,event)
        db=connect(path,mode);assert db.execute('select count(*) from event').fetchone()[0]==1;db.close()
        event_rows.append({'cut':cut,'state':state,'events':count,'same_id_retry_rows':1})
    return {'result':'PASS_LOCAL_SQL_TRANSACTIONS','mode':mode,'control':rows,'state_event':event_rows,
            'boundary':'minimal projection; actual SQLite process SIGKILL; not production adapter or power-loss test'}

def concurrent_child(path,mode,vm,host,key,spec,gate,queue):
    gate.wait(3)
    try:queue.put(('accepted',*accept_control(Path(path),mode,vm,host,key,spec)))
    except Exception as error:queue.put(('error',type(error).__name__,str(error)))

def sqlite_concurrency(home,mode):
    path=home/'concurrent.db';vm,host,key=map(str,[uuid4() for _ in range(3)])
    spec={'vm_id':vm,'action':'stop','force':False};init_control(path,mode,vm,host)
    ctx=mp.get_context('fork');gate=ctx.Event();queue=ctx.Queue()
    processes=[ctx.Process(target=concurrent_child,args=(str(path),mode,vm,host,key,spec,gate,queue)) for _ in range(2)]
    for p in processes:p.start()
    gate.set();results=[queue.get(timeout=3) for _ in processes]
    for p in processes:p.join(3);assert p.exitcode==0
    assert all(v[0]=='accepted' for v in results) and results[0][1:]==results[1][1:]
    rejected=False
    try:accept_control(path,mode,vm,host,key,{**spec,'force':True})
    except ValueError:rejected=True
    assert rejected
    db=connect(path,mode);assert db.execute('select count(*) from operation').fetchone()[0]==1
    try:db.execute('insert into operation values(?,?,?,?,?,?,?,NULL)',(str(uuid4()),str(uuid4()),'bad','x','{}','1','accepted'))
    except sqlite3.IntegrityError:fk=True
    else:fk=False
    assert fk;db.close()
    return {'result':'PASS_CONCURRENT_SAME_KEY_AND_CONFLICT','mode':mode,'callers':2,'operation_rows':1,
            'original_deadline_preserved':True,'different_spec_rejected':rejected,'foreign_key_enforced':fk,
            'physical_execution_in_this_projection':False}

def sqlite_failures(home,mode):
    path=home/'errors.db';vm,host,key=map(str,[uuid4() for _ in range(3)]);init_control(path,mode,vm,host)
    lock=connect(path,mode);lock.execute('begin immediate');start=time.monotonic()
    try:accept_control(path,mode,vm,host,key,{'action':'stop'},remaining_s=.03)
    except sqlite3.OperationalError as error:busy=error.sqlite_errorname
    else:raise AssertionError('busy writer unexpectedly admitted')
    waited=time.monotonic()-start;lock.rollback();lock.close();assert busy=='SQLITE_BUSY'
    ro=sqlite3.connect(f'file:{path}?mode=ro',uri=True)
    try:ro.execute("update intent set desired='stopped'")
    except sqlite3.OperationalError as error:readonly=error.sqlite_errorname
    ro.close();assert readonly=='SQLITE_READONLY'
    db=connect(path,mode);db.execute('create table fault_filler(value text)')
    pages=db.execute('pragma page_count').fetchone()[0];db.execute('pragma max_page_count='+str(pages+1))
    try:
        db.execute('begin immediate');db.execute("update intent set desired='stopped'")
        db.execute('insert into fault_filler values(?)',('x'*131072,));db.commit()
    except sqlite3.DatabaseError as error:
        full=error.sqlite_errorname
        if db.in_transaction:db.rollback()
    else:raise AssertionError('SQLITE_FULL not induced')
    assert full=='SQLITE_FULL' and db.execute('select desired from intent').fetchone()[0]=='running'
    # Preserve a u64 identifier as TEXT rather than coercing through signed INTEGER/float.
    db.execute('create table u64_probe(value text not null)');value=str(2**64-1)
    db.execute('insert into u64_probe values(?)',(value,));assert db.execute('select value from u64_probe').fetchone()[0]==value
    db.close()
    absent=home/'missing.db';refused=False
    try:connect(absent,mode)
    except sqlite3.OperationalError:refused=not absent.exists()
    assert refused
    expired=False
    try:accept_control(path,mode,vm,host,'expired',{'action':'stop'},absolute_deadline=time.monotonic()-1)
    except TimeoutError:expired=True
    assert expired
    corrupt=home/'corrupt.db';corrupt.write_bytes(b'not an SQLite database');before=corrupt.read_bytes()
    try:connect(corrupt,mode)
    except sqlite3.DatabaseError as error:corrupt_code=error.sqlite_errorname
    assert corrupt.read_bytes()==before and corrupt_code=='SQLITE_NOTADB'
    db=connect(path,mode);db.execute('pragma user_version=99');db.close();schema_refused=False
    try:connect(path,mode)
    except ValueError:schema_refused=True
    assert schema_refused
    return {'result':'PASS_BUSY_READONLY_FULL_AND_NO_IMPLICIT_INIT','mode':mode,
        'busy':busy,'busy_timeout_ms':30,'observed_wait_ms':round(waited*1000,3),
        'readonly':readonly,'full':full,'full_is_page_budget_not_real_disk':True,
        'u64_text_roundtrip':value,'missing_existing_db_refused':refused,
        'expired_local_budget_refused':expired,'corrupt_code':corrupt_code,'unknown_schema_refused':schema_refused}

def sqlite_external_boundary(home,mode):
    path=home/'external.db';vm,host,key=map(str,[uuid4() for _ in range(3)])
    init_control(path,mode,vm,host);gate=mp.get_context('fork').Event();entered=mp.get_context('fork').Event()
    # External execution owns no SQL connection/transaction while waiting.
    def external():entered.set();gate.wait(3)
    process=mp.get_context('fork').Process(target=external);process.start();assert entered.wait(1)
    start=time.monotonic();identity,deadline=accept_control(path,mode,vm,host,key,{'action':'stop'})
    elapsed=time.monotonic()-start;still_waiting=process.is_alive();gate.set();process.join(3)
    assert still_waiting and process.exitcode==0
    db=connect(path,mode);db.execute("update operation set phase='executing' where id=?",(identity,))
    effects=home/'submitted-effect.txt';effects.write_text('one synthetic external submission\n')
    # SQL consistency cannot determine whether the external side effect completed.
    db.close();db=connect(path,mode);phase=db.execute('select phase from operation').fetchone()[0]
    assert phase=='executing' and len(effects.read_text().splitlines())==1
    db.close()
    return {'result':'PASS_SHORT_ADMISSION; EXTERNAL_RECONCILIATION_STILL_REQUIRED','mode':mode,
        'stop_committed_while_external_waited':still_waiting,'observed_admission_ms':round(elapsed*1000,3),
        'phase_after_external_effect_without_result':'executing','synthetic_effects':1,
        'automatic_resubmissions':0,'boundary':'storage projection and explicit synthetic external marker; no Xen/Store'}

def sqlite_event_and_backup(home,mode):
    path=home/'events.db';init_worker(path,mode);old,new,event=record_pair();worker_commit(path,mode,new,event)
    db=connect(path,mode);pending=db.execute('select event_id from event where notify_attempted=0').fetchall();db.close()
    submissions=[pending[0][0]]  # crash after nonblocking publish attempt, before local mark
    db=connect(path,mode);retry=db.execute('select event_id from event where notify_attempted=0').fetchall()
    submissions.append(retry[0][0]);db.execute('update event set notify_attempted=1 where event_id=?',(retry[0][0],))
    assert submissions[0]==submissions[1] and db.execute('select count(*) from event').fetchone()[0]==1
    second=copy.deepcopy(new);second.recovery_id=uuid4();second.recovery_attempt_id=uuid4()
    second.request={**second.request,'recovery_attempt_id':str(second.recovery_attempt_id)}
    second.pending_events=[]
    try:worker_commit(path,mode,second)
    except sqlite3.IntegrityError:duplicate_open=True
    else:duplicate_open=False
    assert duplicate_open
    bad=copy.deepcopy(event);bad['event_id']=str(uuid4());bad['type']='app.heartbeat'
    try:db.execute('insert into event(event_id,recovery_id,type,payload) values(?,?,?,?)',(bad['event_id'],bad['recovery_id'],bad['type'],json.dumps(bad)))
    except sqlite3.IntegrityError:periodic_rejected=True
    else:periodic_rejected=False
    assert periodic_rejected
    # An existing valid Backup API snapshot can still be older than current authority/work state.
    backup=home/'backup.db';target=sqlite3.connect(backup);db.backup(target);target.close()
    db.execute("update recovery set status='VERIFYING'");db.close()
    restored=sqlite3.connect(backup);stale=restored.execute('select status from recovery').fetchone()[0]
    integrity=restored.execute('pragma integrity_check').fetchone()[0];restored.close();assert stale=='EXECUTING' and integrity=='ok'
    return {'result':'PASS_BOUNDED_EVENT_ID_AND_CONSTRAINTS; BACKUP_NOT_LATEST_STATE','mode':mode,
        'event_rows':1,'publish_attempts_after_crash':len(submissions),'same_event_id':True,
        'mq_or_consumer_exactly_once_claimed':False,'duplicate_open_vm_rejected':duplicate_open,
        'periodic_message_type_rejected':periodic_rejected,'backup_api_integrity':integrity,
        'valid_backup_can_be_stale':True,'end_to_end_event_delivery_tested':False}

def sqlite_migration_and_retention(home,mode):
    """Dry run on fixture-produced originals; unsafe pruning is an explicit counterexample."""
    import test_xenova_recovery_foundation as t
    from xenova_runtime import operation_lock
    f=t.FoundationTest('runTest');f.setUp()
    try:
        f.start();key='audit-persistence-original-stop'
        with operation_lock(f.p.runtime):
            response=f.call('stop',{'vm_id':f.vm_id},key=key)
        assert not response['ok'] and response['error']['details']['accepted']
        original=next(q for q in f.p.execution().requests() if q['idempotency_key']==key)
        intent=f.p.execution().intent(f.r);vm=f.vm_id;host=original['host_boot_id']
        path=home/'migrated-control.db';init_control(path,mode,vm,host);db=connect(path,mode)
        db.execute('begin immediate')
        db.execute('insert into operation values(?,?,?,?,?,?,?,?)',(
            original['operation_id'],vm,key,original['request_hash'],json.dumps(original['request'],sort_keys=True,separators=(',',':')),
            str(original['deadline_monotonic_ms']),original['phase'],json.dumps(original['result'])))
        db.execute('update intent set revision=?,desired=?,accepted_operation=?,lifecycle=? where vm=?',
            (str(intent['intent_revision']),intent['desired_state'],original['operation_id'],str(intent['lifecycle_generation']),vm))
        db.commit();db.close()
        replay=accept_control(path,mode,vm,host,key,original['request'],deadline='999999999999')
        assert replay==(original['operation_id'],str(original['deadline_monotonic_ms']))
        physical=len(f.physical('shutdown'));assert physical==0
    finally:f.doCleanups()
    old,new,event=record_pair();worker_path=home/'migrated-worker.db';init_worker(worker_path,mode)
    from dom0_manager.recovery.state import RecoveryStore,RecoveryRecord
    source=home/'source-fixture.json';assert RecoveryStore(source).save({new.recovery_id:new})
    loaded=RecoveryStore(source).load()[new.recovery_id];worker_commit(worker_path,mode,loaded,event)
    db=connect(worker_path,mode);row=db.execute('select id,vm,status,attempt_id,value_json from recovery').fetchone()
    reconstructed={**json.loads(row[4]),'recovery_id':row[0],'vm_id':row[1],'status':row[2],'recovery_attempt_id':row[3],
                   'pending_events':[json.loads(e[0]) for e in db.execute('select payload from event where notify_attempted=0')]}
    assert RecoveryRecord.from_json(reconstructed).to_json()==new.to_json()
    # Closed event history has no FK to currently retained recovery rows: the
    # production limits (20 closed states / 2000 events) have different lifetimes.
    for i in range(4):
        value=copy.deepcopy(event);value['event_id']=str(uuid4())
        db.execute('insert into event(event_id,recovery_id,type,payload,notify_attempted) values(?,?,?,?,1)',
                   (value['event_id'],value['recovery_id'],value['type'],json.dumps(value)))
    db.execute('delete from event where notify_attempted=1 and seq not in (select seq from event order by seq desc limit 2)')
    pending=db.execute('select count(*) from event where notify_attempted=0').fetchone()[0];assert pending==1
    db.close()
    # Deliberately deleting a completed idempotency row is NOT a supported policy.
    # Demonstrate that SQLite alone cannot keep a deleted request from becoming new.
    p=home/'unsafe-prune.db';v,h,k=map(str,[uuid4() for _ in range(3)]);init_control(p,mode,v,h)
    first,_=accept_control(p,mode,v,h,k,{'action':'stop'});db=connect(p,mode)
    db.execute("update operation set phase='completed'");db.close()
    accept_control(p,mode,v,h,'newer',{'action':'stop'},expected_revision='2')
    db=connect(p,mode);db.execute('delete from operation where id=?',(first,));db.close()
    again,_=accept_control(p,mode,v,h,k,{'action':'stop'},expected_revision='3')
    assert again!=first
    return {'result':'PASS_FIXTURE_MIGRATION; UNSAFE_KEY_PRUNING_REOPENS_ADMISSION',
        'mode':mode,'source_is_actual_manager_fixture':True,'original_operation_intent_revision_phase_deadline_preserved':True,
        'new_physical_shutdowns_during_fixture_migration':physical,'worker_record_and_event_reconstructed_exactly':True,
        'pending_event_preserved_during_bounded_test_cleanup':pending==1,
        'deliberate_unsafe_prune_allows_same_key_new_operation':True,'production_pruning_changed':False}

FILE_PROBES={'quarantine':quarantine_gap,'event_append':append_uncertainty,'sync':sync_boundaries,
             'file_crash':file_crash_atomicity,'authority':authority_limits,'prior_boundaries':existing_counterexample_boundaries}
SQL_PROBES={'atomicity':sqlite_atomicity,'concurrency':sqlite_concurrency,'failure':sqlite_failures,
            'external_boundary':sqlite_external_boundary,'event_backup':sqlite_event_and_backup,
            'migration_retention':sqlite_migration_and_retention}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--phase',choices=('files','sqlite'),required=True)
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    if args.output.resolve().is_relative_to(ROOT):p.error('evidence belongs outside product source')
    results={}
    if args.phase=='files':
        for name,probe in FILE_PROBES.items():
            with tempfile.TemporaryDirectory(prefix='xenova-persistence-file-') as directory:
                results[name]=probe(Path(directory));print(name,results[name]['result'],flush=True)
    else:
        for mode in ('DELETE','WAL'):
            for name,probe in SQL_PROBES.items():
                with tempfile.TemporaryDirectory(prefix='xenova-persistence-sql-') as directory:
                    results[mode+'-'+name]=probe(Path(directory),mode)
                    print(mode,name,results[mode+'-'+name]['result'],flush=True)
    args.output.write_text(json.dumps({'audit_completed':True,'all_product_guarantees_passed':False,
        'phase':args.phase,'sqlite_version':sqlite3.sqlite_version,'results':results},ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
