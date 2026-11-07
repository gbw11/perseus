"""Private execution-binding observation; not a new Manager action.

Reuses incarnation checks and short admission locks, without an unrelated VM's
long physical-operation lock. No start, CPU preparation or app-ready inference.
"""
from copy import copy
from xenova_execution import short_lock,fingerprint
from xenova_vm import PlatformError
# Load the read layer before observe starts its per-call budget.
from xenova_management import Manager,ReadContext

def observe(platform,identity,*,timeout_s=2.0,wait_for_live=False,previous_live=None):
    end=platform.clock()+timeout_s
    previous=getattr(platform,'response_deadline',None)
    if previous is not None:end=min(previous,end)
    # Production rejoin shares its Platform across A/B executor lanes. The
    # per-read budget belongs to a shallow request copy, never that shared one.
    scoped=copy(platform);scoped.response_deadline=end
    return _observe(scoped,identity,end,wait_for_live,previous_live)


def _observe(platform,identity,end,wait_for_live,previous_live):
    # 같은 회차의 publication 입력을 재사용한다. 전후 전체 등록부 검사와
    # publication 직전 대상/원 작업/실행 파일 검증은 그대로 수행한다.
    manager=Manager(platform)
    read=ReadContext(manager,'get',{'vm_id':identity},end)
    record=read.by_id[identity]['record'];life=read.lifecycles[identity]
    execution=read.e;inputs=read.inputs[identity]
    before=record,life,inputs['intent'],inputs['run']
    def target_state():
        current,tomb,current_inputs=read.validate_one(identity)
        # Admission writers cannot change these atomic files during this
        # short check. Keep strict JSON type identity as well as field values.
        if life[0]!='REGISTERED':
            current=read.optional(manager.data/'instances'/record['id']/'instance.json')
        if fingerprint(tomb)!=fingerprint(read.tombs.get(identity)):
            raise PlatformError('execution binding changed during observation')
        return current,life,current_inputs['intent'],current_inputs['run']
    with short_lock(platform.runtime,'admission',deadline=end,clock=platform.clock,sleep=platform.sleep):
        if fingerprint(before)!=fingerprint(target_state()):
            raise PlatformError('execution binding changed during observation')
    def state():
        records=platform.records(validate_assets=False)
        record=next((r for r in records.values() if r['uuid']==identity),None)
        if record is None:raise PlatformError('binding target not registered')
        tombs=manager._tombs(deadline=end)
        tomb=tombs.get(identity)
        life=(tomb['lifecycle'],tomb['generation']) if tomb else ('REGISTERED',0)
        return record,life,execution.intent(record),execution.run_record(record)
    record,life,intent,run=before
    if life[0]!='REGISTERED' or not intent or intent['desired_state']!='running' or intent['lifecycle_generation']!=life[1]:
        return {'vm_id':identity,'status':'stopped','host_boot_id':execution.host}
    rechecked_unscheduled = False
    while True:
        observation=execution.observe(record,end,identity_only=True,_read_context=read)
        after=state()  # Full uniqueness/path checks stay outside admission.
        with short_lock(platform.runtime,'admission',deadline=end,clock=platform.clock,sleep=platform.sleep):
            if fingerprint(before)!=fingerprint(after) or fingerprint(before)!=fingerprint(target_state()):
                raise PlatformError('execution binding changed during observation')
            execution.gate()
            read.check()
        if observation['presence']=='absent':return {'vm_id':identity,'status':'absent','host_boot_id':execution.host}
        context=(observation.get('_description') or {}).get('execution_context')
        if not context or context.get('phase')!='observed' or observation['execution_status']!='managed':
            raise PlatformError('managed live execution required')
        if observation['xen_state'] in ('running','blocked'):
            break
        if observation['xen_state_flags'] != '------' or (not wait_for_live and rechecked_unscheduled):
            raise PlatformError('managed live execution required')
        # Xen can report ------ between scheduling samples. Reuse the initial
        # live evidence only while provisioning this exact managed execution.
        if wait_for_live and previous_live and (
                previous_live['host_boot_id'],previous_live['execution_id'],
                previous_live['domid'],previous_live['xen_unique_id']) == (
                execution.host,context['execution_id'],context['domid'],str(context['xen_unique_id'])):
            break
        if end-platform.clock()<=0.05:
            raise PlatformError('managed live execution required')
        # The flags describe one scheduling sample. Background binding takes
        # one fresh sample in the original budget and requires r/b to publish.
        rechecked_unscheduled = True
        platform.sleep(0.05)
    return {'vm_id':identity,'status':'observed','host_boot_id':execution.host,'execution_id':context['execution_id'],
        'signal_profile':context.get('signal_profile'),
        'domid':context['domid'],'xen_unique_id':str(context['xen_unique_id']),
        'intent_revision':intent['intent_revision'],'lifecycle_generation':life[1],
        'observed_monotonic_ms':observation['observed_monotonic_ms'],
        'execution_owner_kind':(context.get('owner') or {}).get('kind'),
        'recovery_attempt_id':(context.get('owner') or {}).get('recovery_attempt_id')}
