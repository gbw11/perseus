"""Private execution-binding observation; not a new Manager action.

Reuses incarnation checks and short admission locks, without an unrelated VM's
long physical-operation lock. No start, CPU preparation or app-ready inference.
"""
from copy import copy
from xenova_execution import short_lock,fingerprint
from xenova_vm import PlatformError,UUID
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


def observe_pair(platform,identities,*,timeout_s=2.0):
    """현재 due인 A/B(1~2개)를 한 회차에서 관측한다. 요청 간 cache는 없다.

    두 전체 Xen 목록과 전후 native incarnation batch를 공유하되 각 대상의
    현재 파일·게시 순서·최종 admission guard와 원 관측 시각은 유지한다.
    공통 관측 실패는 예외, 확인된 한 대상만 live가 아니면 그 항목에 원 예외를 반환한다.
    """
    if (not isinstance(identities,(tuple,list)) or not 1<=len(identities)<=2
            or any(not isinstance(identity,str) or not UUID.fullmatch(identity) for identity in identities)
            or len(set(identities))!=len(identities)):
        raise PlatformError('one or two distinct canonical binding targets required')
    end=platform.clock()+timeout_s
    previous=getattr(platform,'response_deadline',None)
    if previous is not None:end=min(previous,end)
    scoped=copy(platform);scoped.response_deadline=end
    manager=Manager(scoped)
    read=ReadContext(manager,'list',{'lifecycle':'REGISTERED'},end,_input_ids=identities)
    if any(identity not in read.by_id for identity in identities):
        raise PlatformError('binding target not registered')
    execution=read.e

    def validate(identity):
        current,tomb,inputs=read.validate_one(identity)
        expected=(read.by_id[identity]['record'],read.tombs.get(identity),read.inputs[identity])
        if fingerprint((current,tomb,inputs))!=fingerprint(expected):
            raise PlatformError('execution binding changed during observation')

    for identity in identities:validate(identity)
    active=[];result={}
    for identity in identities:
        life=read.lifecycles[identity];intent=read.inputs[identity]['intent']
        if not intent or intent['desired_state']!='running' or intent['lifecycle_generation']!=life[1]:
            result[identity]={'vm_id':identity,'status':'stopped','host_boot_id':execution.host}
        else:active.append(read.by_id[identity]['record'])
    rechecked=False
    while True:
        if active:
            observations=execution.observe_many(active,end,_read_context=read,_identity_batch=True)
        else:
            read.validate();observations={}
        # ReadContext.validate performed the second full registry/lifecycle
        # scan before publication. Recheck only the current target files here.
        with short_lock(scoped.runtime,'admission',deadline=end,clock=scoped.clock,sleep=scoped.sleep):
            for identity in identities:validate(identity)
            if active:execution.gate()
            read.check()
        unscheduled=[]
        for record in active:
            identity=record['uuid'];observation=observations[identity]
            if observation['presence']=='absent':
                result[identity]={'vm_id':identity,'status':'absent','host_boot_id':execution.host}
                continue
            context=(observation.get('_description') or {}).get('execution_context')
            if not context or context.get('phase')!='observed' or observation['execution_status']!='managed':
                result[identity]=PlatformError('managed live execution required')
                continue
            if observation['xen_state'] not in ('running','blocked'):
                if observation['xen_state_flags']!='------':
                    result[identity]=PlatformError('managed live execution required')
                    continue
                unscheduled.append(identity)
            intent=read.inputs[identity]['intent'];life=read.lifecycles[identity]
            result[identity]={'vm_id':identity,'status':'observed','host_boot_id':execution.host,
                'execution_id':context['execution_id'],'signal_profile':context.get('signal_profile'),
                'domid':context['domid'],'xen_unique_id':str(context['xen_unique_id']),
                'intent_revision':intent['intent_revision'],'lifecycle_generation':life[1],
                'observed_monotonic_ms':observation['observed_monotonic_ms'],
                'execution_owner_kind':(context.get('owner') or {}).get('kind'),
                'recovery_attempt_id':(context.get('owner') or {}).get('recovery_attempt_id')}
        if not unscheduled or rechecked or end-scoped.clock()<=.05:
            for identity in unscheduled:result[identity]=PlatformError('managed live execution required')
            read.check();return result
        # As in single-target background observation, retry one scheduling
        # sample in the original budget; it never permits a paused execution.
        rechecked=True;scoped.sleep(.05)


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
        # Keep strict JSON type identity as well as field values. The final
        # current-target check runs under admission; this early check is read-only.
        if life[0]!='REGISTERED':
            current=read.optional(manager.data/'instances'/record['id']/'instance.json')
        if fingerprint(tomb)!=fingerprint(read.tombs.get(identity)):
            raise PlatformError('execution binding changed during observation')
        return current,life,current_inputs['intent'],current_inputs['run']
    # No effect is submitted by the preliminary read. Publication revalidates
    # these files, then the final admission guard checks the target and gate.
    # Avoid serializing A/B's preliminary reads behind unrelated admission work.
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
