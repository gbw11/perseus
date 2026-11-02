"""Actor-owned Gateway management. Transport never writes Authority or devices.

Protection is a durable exclusion of one VM execution until explicit completion
or cancellation, not a cached permission to restart it. Manager guards remain
necessary. Historical results and current protection are returned separately.
"""
from .authority import AuthorityError
from .ipc_contract import integer, uuid, request_key
from .machine import Mode, ForwardStop

PROTECTION_LIMIT = 16
REQUIRED = {'host_boot_id', 'target_vm', 'target_execution_id', 'intent_revision',
            'lifecycle_generation', 'expected_owner', 'expected_generation', 'deadline_ms'}


class ProviderError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def normalize(spec, *, original=None, mode=None):
    if set(spec) - (REQUIRED | {'purpose', 'mode', 'parent_deadline_ms','manual_hold'}) or REQUIRED - set(spec):
        raise ValueError('Gateway request fields invalid')
    result = dict(spec)
    purpose = result.setdefault('purpose', original['purpose'] if original else 'protect')
    if purpose not in ('protect', 'handoff'):
        raise ValueError('Gateway purpose invalid')
    for name in ('host_boot_id', 'target_vm', 'target_execution_id', 'expected_owner'):
        uuid(result[name])
    for name in ('intent_revision', 'lifecycle_generation'):
        integer(result[name])
    integer(result['deadline_ms'], minimum=1)
    result.setdefault('parent_deadline_ms',original['parent_deadline_ms'] if original else result['deadline_ms'])
    integer(result['parent_deadline_ms'],minimum=1)
    if result['deadline_ms']>result['parent_deadline_ms']:
        raise ValueError('Gateway deadline exceeds parent budget')
    integer(result['expected_generation'], minimum=1, maximum=0xffffffff)
    if purpose == 'handoff':
        result.setdefault('mode', original['mode'] if original else mode)
        if result['mode'] not in ('AUTO', 'MANUAL'):
            raise ValueError('planned mode required')
    elif 'mode' in result:
        raise ValueError('protection does not select a mode')
    if 'manual_hold' in result:
        if type(result['manual_hold']) is not bool or purpose!='handoff' or result['mode']!='MANUAL':
            raise ValueError('manual_hold is a MANUAL handoff choice')
        if not result['manual_hold']:result.pop('manual_hold')
    elif original and original.get('manual_hold'):result['manual_hold']=True
    return result


def validate_records(value):
    rows = value.get('protections', [])
    if not isinstance(rows, list) or len(rows) > PROTECTION_LIMIT:
        raise ValueError('protection record limit')
    seen = {row['request_id'] for row in value.get('transitions', [])}
    held = set()
    for row in rows:
        request_key(row['request_id']); uuid(row['host_boot_id'])
        if row['request_id'] in seen or row['phase'] not in ('PROTECTED', 'RELEASED', 'CANCELLED'):
            raise ValueError('protection record invalid')
        seen.add(row['request_id'])
        spec = row['spec']
        if normalize(spec) != spec or spec['purpose'] != 'protect':
            raise ValueError('protection spec invalid')
        if row['host_boot_id'] != spec['host_boot_id']:
            raise ValueError('protection host invalid')
        integer(row['accepted_generation'], minimum=1, maximum=0xffffffff)
        validate_result(row, spec, row['accepted_generation'])
        if row['phase'] == 'PROTECTED':
            if spec['target_vm'] in held:
                raise ValueError('multiple active protections')
            held.add(spec['target_vm'])
        else:
            end = row['completion']
            uuid(end['host_boot_id']); uuid(end['target_execution_id'])
            integer(end['expected_generation'], minimum=1, maximum=0xffffffff)


def validate_result(row, spec, generation):
    """Validate persisted stage evidence separately from the live device view."""
    result = row.get('result', {})
    if not isinstance(result, dict) or set(result)-{'app', 'device'}:
        raise ValueError('provider result fields')
    if 'app' in result:
        app = result['app']
        if spec['purpose']!='handoff' or app!={**row['spec']['target'], 'generation':generation}:
            raise ValueError('provider application result identity')
    if 'device' in result:
        event=result['device']
        required={'accepted','applied','physical_stop','reported_velocity_mps','reported_steering_rad',
                  'command_generation','command_counter','command_sequence','command_kind','sample_seq',
                  'observation_source','pi_session','device_session','status','observed_monotonic_ms'}
        if not isinstance(event,dict) or set(event)!=required:
            raise ValueError('provider device result fields')
        if (event['accepted'] is not True or event['applied'] is not True or event['status']!='APPLIED' or
                event['observation_source']!='device_report' or event['command_generation']!=generation or
                event['command_kind'] not in ('DRIVE','STOP')):
            raise ValueError('provider device result stage')
        for field in ('pi_session','device_session'):uuid(event[field])
        integer(event['command_counter'],maximum=255)
        for field in ('command_sequence','sample_seq'):integer(event[field],minimum=1,maximum=2**64-1)
        integer(event['observed_monotonic_ms'],maximum=spec['deadline_ms'])
        if event['physical_stop'] is not None and (type(event['physical_stop']) is not bool or event['command_kind']!='STOP'):
            raise ValueError('provider physical observation')
        import math
        for field in ('reported_velocity_mps','reported_steering_rad'):
            if event[field] is not None and (type(event[field]) not in (int,float) or not math.isfinite(event[field])):
                raise ValueError('provider device measurement')


class GatewayProvider:
    def __init__(self, runner):
        self.runner = runner
        self.capture_pending={r['request_id'] for r in runner.authority.value.get('transitions',[])
                              if r['phase']=='COMMITTED' and 'provider_request' in r['spec']}
        self.capture_pending.update(r['request_id'] for r in runner.authority.value.get('protections',[])
            if r['phase']=='PROTECTED' and r['spec']['target_vm']==r['spec']['expected_owner'])

    @property
    def m(self):
        return self.runner.machine

    @property
    def a(self):
        return self.runner.authority

    def find(self, request_id):
        for row in self.a.value.get('protections', []):
            if row['request_id'] == request_id:
                return row, row['spec']
        for row in self.a.value.get('transitions', []):
            if row['request_id'] == request_id and 'provider_request' in row['spec']:
                return row, row['spec']['provider_request']
        return None, None

    def target(self, spec):
        row = self.m.bindings.current(spec['target_vm'], self.m.clock())
        if (not row or row['execution_id'] != spec['target_execution_id'] or
                any(row[k] != spec[k] for k in ('intent_revision', 'lifecycle_generation'))):
            raise ProviderError('TARGET_CHANGED')
        return row

    def current(self, spec):
        if spec['host_boot_id'] != self.a.host:
            raise ProviderError('HOST_CHANGED')
        if (spec['expected_generation'] != self.m.generation or spec['expected_owner'] != self.m.active_vm):
            raise ProviderError('AUTHORITY_CHANGED')
        if self.m.clock()*1000 >= spec['deadline_ms']:
            raise ProviderError('DEADLINE_EXCEEDED')
        if self.a.error:
            raise ProviderError('STORAGE_UNCERTAIN')

    def context(self):
        m = self.m
        rows = {}
        for vm in m.vm_ids:
            row = m.bindings.current(vm, m.clock())
            rows[vm] = None if row is None else {k: row[k] for k in
                ('execution_id', 'boot_id', 'intent_revision', 'lifecycle_generation')}
        return dict(host_boot_id=self.a.host, owner=m.active_vm, generation=m.generation,
                    mode=m.mode.value, phase=m.phase, output_permitted=m.output_permitted,
                    manual_hold=bool(self.a.value.get('manual_hold',False)),
                    output_wait=dict(rate_pending=self.runner._deferred_drive is not None,
                                     rate_deferred=self.runner.serial_deferred,manual_withheld=self.runner.manual_withheld,
                                     manual_activation=None if self.runner._manual_activation is None else
                                         {k:self.runner._manual_activation[k] for k in ('generation','ready','kind','deadline_ms')}),
                    storage_uncertain=bool(self.a.error), targets=rows,
                    **({'drive_input':self.runner.drive_link.status()} if self.runner.drive_link is not None else {}))

    def view(self, request_id):
        row, spec = self.find(request_id)
        if row is None:
            if self.a.error:raise ProviderError('OUTCOME_UNKNOWN')
            key=request_key(request_id)
            if key and key[0]!=self.a.host:raise ProviderError('HOST_CHANGED')
            fence=self.a.value.get('history',{})
            if key and fence.get('host_boot_id')==self.a.host and key[1]<=fence['floor_ms']:
                raise ProviderError('RESULT_NOT_RETAINED_OR_UNKNOWN')
            if not key and fence:raise ProviderError('RESULT_NOT_RETAINED_OR_UNKNOWN')
            raise ProviderError('NOT_FOUND')
        m = self.m
        binding = m.bindings.current(spec['target_vm'], m.clock())
        same = bool(binding and binding['execution_id'] == spec['target_execution_id'] and
                    all(binding[k] == spec[k] for k in ('intent_revision', 'lifecycle_generation')))
        held = row['phase'] == 'PROTECTED'
        generation = row.get('committed_generation')
        from .planning import context as binding_context
        app_context=bool(binding and spec['purpose']=='handoff' and
                         binding_context(binding,row['spec']['app'])==row['spec']['target'])
        applied = bool(generation and generation == m.generation and m.phase == 'APPLIED' and
                       m.active_vm == spec['target_vm'] and same and app_context and m.active_app and
                       m._ready(m.active_vm, m.active_app, m.clock()))
        # Only bounded public facts. No raw Authority/receipt/private paths.
        return dict(request_id=request_id, purpose=spec['purpose'], phase=row['phase'],
                    acceptance='durable', host_boot_id=row['host_boot_id'], target_vm=spec['target_vm'],
                    target_execution_id=spec['target_execution_id'], intent_revision=spec['intent_revision'],
                    lifecycle_generation=spec['lifecycle_generation'], deadline_ms=spec['deadline_ms'],
                    retry_until_ms=row.get('retry_until_ms',spec['deadline_ms']),
                    retained_through_ms=row.get('retain_until_ms'),
                    accepted_generation=row.get('accepted_generation', spec['expected_generation']),
                    committed_generation=generation, reason=row.get('reason'), app_applied=applied,
                    protection=dict(held=held, owner_excluded=held and not m.output_permitted if m.active_vm==spec['target_vm'] else held,
                                    current_target_matches=same, current_host_matches=row['host_boot_id']==self.a.host,
                                    deadline_elapsed=m.clock()*1000>=spec['deadline_ms']),
                    current_generation=m.generation, current_output_permitted=m.output_permitted,
                    storage_uncertain=bool(self.a.error),
                    original=dict(app=row.get('result',{}).get('app'),device=row.get('result',{}).get('device'),
                                  continuity=row.get('evidence',{}).get('continuity')),
                    device=self.runner.device_view())

    def submit(self, request_id, spec):
        old, original = self.find(request_id)
        spec = normalize(spec, original=original, mode=self.m.mode.value)
        if old is not None:
            if spec != original:
                raise ProviderError('IDEMPOTENCY_CONFLICT')
            return self.view(request_id)
        if (any(r['request_id']==request_id for r in self.a.value.get('transitions', [])) or
                request_id in self.a.value['requests']):
            raise ProviderError('IDEMPOTENCY_CONFLICT')
        self.current(spec)
        from .history import candidate,admission,room,stamp
        now_ms=int(self.m.clock()*1000)
        history=candidate(self.a,now_ms)
        error=admission(self.a,request_id,spec,now_ms,history)
        if error:raise ProviderError(error)
        if spec['target_vm'] not in self.m.vm_ids:
            raise ProviderError('TARGET_NOT_MANAGED')
        self.target(spec)
        if spec['purpose'] == 'handoff':
            self.m.planning.begin(request_id, spec['mode'], spec['target_vm'],
                expected_owner=spec['expected_owner'], expected_generation=spec['expected_generation'],
                deadline_ms=spec['deadline_ms'], provider_request=spec)
        else:
            rows=history.get('protections',self.a.value.get('protections',[]))
            if len(rows) >= PROTECTION_LIMIT:
                raise ProviderError('HISTORY_FULL')
            if self.a.protected(spec['target_vm']):
                raise ProviderError('TARGET_ALREADY_PROTECTED')
            active = self.m.active_vm == spec['target_vm']
            # Capacity is checked before revoking the current output/effect.
            row = dict(request_id=request_id, host_boot_id=self.a.host, spec=spec,
                       phase='PROTECTED', accepted_generation=self.m.generation+int(active))
            row=stamp(self.a,row)
            fields={**history,'protections':[*rows,row]}
            if not room({**self.a.value,**fields}):raise ProviderError('HISTORY_BYTES_RESERVED')
            if active:
                # Protection/STOP happens before any required slow storage or
                # response. The durable exclusion and generation publish once.
                self.m.output_permitted = False
                self.m._authorized = False
                self.runner._submit(ForwardStop(self.m.generation))
            generation = self.m.generation + int(active)
            if generation > 0xffffffff:
                raise ProviderError('GENERATION_EXHAUSTED')
            row = dict(request_id=request_id, host_boot_id=self.a.host, spec=spec,
                       phase='PROTECTED', accepted_generation=generation)
            row=stamp(self.a,row)
            fields['protections']=[*rows,row]
            if active:
                fields.update(generation=generation, mode='SAFE_STOP', phase='PENDING', rearm_required=True,
                    reason='recovery_protection', requests=[], pending=dict(generation=generation,
                        host_boot_id=self.a.host, deadline_ms=min(spec['deadline_ms'], int(self.m.clock()*1000)+1000),
                        arm_after_apply=False))
            self.a.update(**fields)
            if active:
                self.runner._emit(self.m._adopt_control(Mode.SAFE_STOP, self.m.active_vm,
                                                       'recovery_protection', self.m.clock()))
                if self.runner.device_view()['feedback_supported']:self.capture_pending.add(request_id)
        return self.view(request_id)

    def end(self, request_id, context, *, cancel=False):
        row, spec = self.find(request_id)
        if row is None:
            raise ProviderError('OUTCOME_UNKNOWN' if self.a.error else 'NOT_FOUND')
        if spec['purpose'] == 'handoff':
            if not cancel:
                raise ProviderError('NOT_A_PROTECTION')
            if context:
                raise ValueError('planned cancel accepts no context')
            self.m.planning.cancel(request_id)
            return self.view(request_id)
        expected = {'host_boot_id', 'expected_generation', 'target_execution_id'}
        if not isinstance(context, dict) or set(context) != expected:
            raise ValueError('explicit completion context required')
        uuid(context['host_boot_id']); uuid(context['target_execution_id'])
        integer(context['expected_generation'], minimum=1, maximum=0xffffffff)
        if row['phase'] != 'PROTECTED':
            if context != row['completion'] or cancel != (row['phase']=='CANCELLED'):
                raise ProviderError('IDEMPOTENCY_CONFLICT')
            return self.view(request_id)
        if context['host_boot_id'] != self.a.host:
            raise ProviderError('HOST_CHANGED')
        if context['expected_generation'] != self.m.generation:
            raise ProviderError('AUTHORITY_CHANGED')
        current = self.m.bindings.current(spec['target_vm'], self.m.clock())
        if not current or current['execution_id'] != context['target_execution_id']:
            raise ProviderError('TARGET_CHANGED')
        if (cancel and current['execution_id'] != spec['target_execution_id']) or (
                not cancel and current['execution_id'] == spec['target_execution_id']):
            raise ProviderError('TARGET_CHANGED')
        if self.m.active_vm == spec['target_vm'] and self.m.output_permitted:
            raise ProviderError('OUTPUT_NOT_PROTECTED')
        ended = {**row, 'phase': 'CANCELLED' if cancel else 'RELEASED', 'completion': dict(context)}
        self.a.update(protections=[ended if r['request_id']==request_id else r
                                   for r in self.a.value['protections']])
        self.capture_pending.discard(request_id)
        # No preference rollback, generation allocation, automatic resume or
        # freshness renewal. New bindings/calculation/rearm remain necessary.
        return self.view(request_id)

    def handle(self, call):
        if self.m is None:
            raise ProviderError('NOT_READY')
        action = call['action']; request_id = call.get('request_id'); spec = call.get('spec', {})
        if action == 'history':
            from .history import status
            return status(self.a,int(self.m.clock()*1000))
        if action == 'context':
            return self.context()
        if action == 'submit':
            return self.submit(request_id, spec)
        if action == 'get':
            return self.view(request_id)
        if action == 'commit':
            row, original = self.find(request_id)
            if original is None or original['purpose'] != 'handoff':
                raise ProviderError('NOT_A_HANDOFF')
            _, actions = self.m.planning.commit(request_id)
            self.runner._emit(actions)
            self.capture_pending.add(request_id)
            return self.view(request_id)
        return self.end(request_id, spec, cancel=action=='cancel')

    def capture(self,events):
        """Persist management stage evidence once, never per command/sample.

        This follows only admitted IDs. It does not rescan Xen/Store/disk or
        create a periodic full-journal validation pass.
        """
        if self.a.error or not self.capture_pending:return
        changed={};now=self.m.clock();supported=self.runner.device_view()['feedback_supported']
        for identity in tuple(self.capture_pending):
            row,spec=self.find(identity)
            if row is None or row['host_boot_id']!=self.a.host or row['phase'] not in ('COMMITTED','PROTECTED'):
                self.capture_pending.discard(identity);continue
            result=dict(row.get('result',{}));generation=row.get('committed_generation',row.get('accepted_generation'))
            planned=spec['purpose']=='handoff'
            if planned and 'app' not in result and generation==self.m.generation and self.m.phase=='APPLIED' and now*1000<=spec['deadline_ms']:
                target=self.m.bindings.current(spec['target_vm'],now)
                from .planning import context as binding_context
                if (target and self.m.active_vm==spec['target_vm'] and self.m.active_app==row['spec']['app'] and
                        binding_context(target,row['spec']['app'])==row['spec']['target'] and
                        self.m._ready(spec['target_vm'],row['spec']['app'],now)):
                    result['app']={**row['spec']['target'],'generation':generation}
            kind='DRIVE' if planned and row.get('evidence',{}).get('continuity')!='stop_then_resume' else 'STOP'
            if supported and 'device' not in result:
                for event in events:
                    if (event['command_generation']==generation and event['command_kind']==kind and event['applied'] and
                            event['observed_monotonic']*1000<=spec['deadline_ms']):
                        result['device']={k:v for k,v in event.items() if k!='observed_monotonic'}
                        result['device']['observed_monotonic_ms']=int(event['observed_monotonic']*1000)
                        break
            if result!=row.get('result',{}):changed[identity]={**row,'result':result}
            if (not planned or 'app' in result) and (not supported or 'device' in result):
                self.capture_pending.discard(identity)
            elif now*1000>=spec['deadline_ms']:self.capture_pending.discard(identity)
        if changed:
            fields={}
            for name in ('transitions','protections'):
                rows=self.a.value.get(name,[])
                if any(r['request_id'] in changed for r in rows):
                    fields[name]=[changed.get(r['request_id'],r) for r in rows]
            self.a.update(**fields)
