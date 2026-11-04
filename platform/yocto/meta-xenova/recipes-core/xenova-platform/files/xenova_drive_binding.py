"""Private execution-binding observation; not a new Manager action.

Reuses incarnation checks and short admission locks, without an unrelated VM's
long physical-operation lock. No start, CPU preparation or app-ready inference.
"""
from xenova_execution import short_lock,fingerprint
from xenova_vm import PlatformError

def observe(platform,identity,*,timeout_s=2.0,wait_for_live=False,previous_live=None):
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
    rechecked_unscheduled = False
    while True:
        observation=execution.observe(record,end,identity_only=True)
        with short_lock(platform.runtime,'admission',deadline=end,clock=platform.clock,sleep=platform.sleep):
            if fingerprint(before)!=fingerprint(state()):raise PlatformError('execution binding changed during observation')
            execution.gate()
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
        'observed_monotonic_ms':int(platform.clock()*1000),
        'execution_owner_kind':(context.get('owner') or {}).get('kind'),
        'recovery_attempt_id':(context.get('owner') or {}).get('recovery_attempt_id')}
