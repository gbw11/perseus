"""Private execution-binding observation; not a new Manager action.

Reuses incarnation checks and short admission locks, without an unrelated VM's
long physical-operation lock. No start, CPU preparation or app-ready inference.
"""
from xenova_execution import short_lock,fingerprint
from xenova_vm import PlatformError

def observe(platform,identity,*,timeout_s=2.0):
    end=platform.clock()+timeout_s;execution=platform.execution()
    def state():
        records=platform.records(validate_assets=False)
        record=next((r for r in records.values() if r['uuid']==identity),None)
        if record is None:raise PlatformError('binding target not registered')
        return record,execution.lifecycle(record),execution.intent(record),execution.run_record(record)
    with short_lock(platform.runtime,'admission',deadline=end,clock=platform.clock,sleep=platform.sleep):
        before=state()
    record,life,intent,run=before
    if life[0]!='REGISTERED' or not intent or intent['desired_state']!='running' or intent['lifecycle_generation']!=life[1]:
        return {'vm_id':identity,'status':'stopped','host_boot_id':execution.host}
    observation=execution.observe(record,end)
    with short_lock(platform.runtime,'admission',deadline=end,clock=platform.clock,sleep=platform.sleep):
        if fingerprint(before)!=fingerprint(state()):raise PlatformError('execution binding changed during observation')
        execution.gate()
    context=(observation.get('_description') or {}).get('execution_context')
    if observation['presence']=='absent':return {'vm_id':identity,'status':'absent','host_boot_id':execution.host}
    if (not context or context.get('phase')!='observed' or observation['execution_status']!='managed' or
        observation['xen_state'] not in ('running','blocked')):raise PlatformError('managed live execution required')
    return {'vm_id':identity,'status':'observed','host_boot_id':execution.host,'execution_id':context['execution_id'],
        'signal_profile':context.get('signal_profile'),
        'domid':context['domid'],'xen_unique_id':str(context['xen_unique_id']),
        'intent_revision':intent['intent_revision'],'lifecycle_generation':life[1],
        'observed_monotonic_ms':int(platform.clock()*1000),
        'execution_owner_kind':(context.get('owner') or {}).get('kind'),
        'recovery_attempt_id':(context.get('owner') or {}).get('recovery_attempt_id')}
