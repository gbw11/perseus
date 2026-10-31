"""정적 Xen hard affinity 정책·호스트 준비·시점 관측.

호출자는 공통 operation_lock을 보유한다. 조회/일반 시작은 affinity를 변경하지 않는다.
외부 root/xl과 원자적으로 직렬화하지 않으며 실행 신원과 CPU 준수는 별도 계약이다.
"""
import json
import re
import subprocess
from pathlib import Path

import xenova_catalog as catalog
from first_install_contract import uuid4
from xenova_execution import ExecutionError, fingerprint, refuse
from xenova_runtime import PlatformError
from xenova_trace import event, traced
import xenova_vm as vm


class _ValidationEvidence:
    """한 호출 단계의 완전한 검증만 전달한다. 직렬화·다른 요청의 캐시는 아니다."""
    def __init__(self, cpu, record, deadline, context, layout):
        self.platform = cpu.p
        self.host = cpu.e.host
        self.record = fingerprint(record)
        self.lifecycle = cpu.e.lifecycle(record)
        self.intent = fingerprint(cpu.e.intent(record))
        self.deadline = deadline
        self.context = context
        self.layout = layout
        self.consumed = False


def cpus(value):
    if (not isinstance(value, list) or not value or
            any(type(c) is not int or not 0 <= c < 4 for c in value) or len(set(value)) != len(value)):
        refuse('INVALID_SPEC', 'pCPUs must be a nonempty unique list of RPi4 CPU numbers 0..3')
    return sorted(value)


def validate_spec(spec):
    if not isinstance(spec, dict) or set(spec) != {'expected_policy_revision', 'shared_pcpus', 'exclusive'}:
        refuse('INVALID_SPEC', 'CPU policy requires expected_policy_revision, shared_pcpus and exclusive')
    revision = spec['expected_policy_revision']
    if revision is not None and (type(revision) is not int or revision < 1):
        refuse('INVALID_SPEC', 'expected_policy_revision must be null or a positive integer')
    shared = cpus(spec['shared_pcpus'])
    entries = spec['exclusive']
    if not isinstance(entries, list) or len(entries) > 2:
        refuse('RESOURCE_UNSUPPORTED', 'at most two dedicated 1-vCPU Zephyr reservations supported')
    identities, cores, normalized = set(), set(), []
    for item in entries:
        if (not isinstance(item, dict) or set(item) != {'vm_id', 'pcpu'} or
                not isinstance(item['vm_id'], str) or not vm.UUID.fullmatch(item['vm_id']) or
                item['vm_id'] == '00000000-0000-0000-0000-000000000000'):
            refuse('INVALID_SPEC', 'dedicated reservation requires a permanent canonical VM UUID and pcpu')
        core = cpus([item['pcpu']])[0]
        if item['vm_id'] in identities or core in cores or core in shared:
            refuse('INVALID_SPEC', 'duplicate or overlapping CPU reservation')
        identities.add(item['vm_id']); cores.add(core)
        normalized.append({'vm_id':item['vm_id'], 'pcpu':core})
    return {'expected_policy_revision':revision, 'shared_pcpus':shared,
            'exclusive':sorted(normalized, key=lambda v:v['vm_id'])}


def affinity_text(context):
    """xl scalar cpus applies the same mask to every max_vcpu; a list maps individual vCPUs."""
    if not context or not context['configured']:
        return ''
    return "cpus = '"+','.join(map(str, context['desired_hard_affinity'][0]['pcpus']))+"'\n"


def bitmap(text, maximum):
    if text == 'all':
        return list(range(maximum+1))
    if text == 'none':
        return []
    result = []
    for part in text.split(','):
        if not re.fullmatch(r'[0-9]+(?:-[0-9]+)?', part):
            raise ValueError('invalid CPU bitmap')
        ends = list(map(int, part.split('-')))
        first, last = ends[0], ends[-1]
        if first > last or last > maximum:
            raise ValueError('CPU bitmap outside Xen maximum')
        result.extend(range(first, last+1))
    if len(result) != len(set(result)):
        raise ValueError('duplicate CPU bitmap entry')
    return sorted(result)


class CPU:
    def __init__(self, platform):
        self.p = platform
        self.e = platform.execution()
        self.policy_path = platform.data/'cpu-policy.json'
        self.anchor = platform.data/'cpu-policy-enabled.json'
        self.journals = platform.data/'cpu-policy-operations'
        self.receipt_path = platform.runtime/'cpu-host.json'

    def read(self, path):
        try:
            return self.e.read(path)
        except (PlatformError, OSError, ValueError) as error:
            refuse('CPU_POLICY_INVALID', 'CPU state is unreadable or corrupt', details={'file':path.name})

    def operations(self):
        try:
            if not self.journals.exists() and not self.journals.is_symlink():
                return []
            catalog.plain(self.journals, directory=True)
            values = []
            for path in self.journals.iterdir():
                if path.suffix != '.json':
                    continue
                op = self.read(path)
                request = validate_spec(op['request'])
                candidate = op['candidate']
                if (not vm.UUID.fullmatch(path.stem) or op['operation_id'] != path.stem or
                        op['phase'] not in ('prepared','completed') or op['base_revision'] != request['expected_policy_revision'] or
                        not isinstance(op['idempotency_key'],str) or not op['idempotency_key'] or
                        candidate != {'schema_version':'1.0', 'operation_id':path.stem,
                                      'policy_revision':(op['base_revision'] or 0)+1,
                                      'pool_name':candidate['pool_name'], 'shared_pcpus':request['shared_pcpus'],
                                      'exclusive':request['exclusive']}):
                    raise ValueError('CPU journal mismatch')
                values.append(op)
            if len({v['candidate']['policy_revision'] for v in values}) != len(values):
                raise ValueError('duplicate policy revision')
            if len({v['idempotency_key'] for v in values}) != len(values):
                raise ValueError('duplicate CPU operation key')
            return values
        except (KeyError, TypeError, OSError, ValueError, PlatformError):
            refuse('CPU_POLICY_INVALID', 'CPU publication history is corrupt or unreadable')

    def policy(self, *, pending=None):
        value = self.read(self.policy_path)
        activated = self.read(self.anchor)
        try:
            if self.journals.exists() or self.journals.is_symlink():
                catalog.plain(self.journals, directory=True)
                history = [p for p in self.journals.iterdir() if p.suffix == '.json']
            else:
                history = []
        except (PlatformError, OSError, ValueError):
            refuse('CPU_POLICY_INVALID', 'CPU publication history is unreadable')
        if value is None:
            if activated or history or self.journals.exists():
                # Only the original interrupted first publication may finish activation.
                if pending and len(history) == 1 and history[0].stem == pending['operation_id'] and pending['base_revision'] is None:
                    return None
                refuse('CPU_POLICY_INVALID', 'activated CPU policy is missing; explicit journal reconciliation required')
            return None
        try:
            if not activated or not vm.UUID.fullmatch(activated.get('activation_operation_id','')) or type(value['policy_revision']) is not int or value['policy_revision'] < 1:
                raise ValueError('missing activation or revision')
            normalized = validate_spec({k:value[k] for k in ('shared_pcpus', 'exclusive')} |
                                       {'expected_policy_revision':value['policy_revision']})
            if normalized['shared_pcpus'] != value['shared_pcpus'] or normalized['exclusive'] != value['exclusive']:
                raise ValueError('noncanonical policy')
            operations = self.operations()
            op = next((v for v in operations if v['operation_id'] == value['operation_id']),None)
            latest = max((v['candidate']['policy_revision'] for v in operations if v['phase'] == 'completed'),default=0)
            anchor_op = next((v for v in operations if v['operation_id'] == activated['activation_operation_id']),None)
            if latest > value['policy_revision'] or (activated['activation_operation_id'] != value['operation_id'] and
                    (not anchor_op or anchor_op['phase'] != 'prepared' or anchor_op['base_revision'] != value['policy_revision'])):
                raise ValueError('policy rollback or activation owner mismatch')
            if not op or op['candidate'] != value or not re.fullmatch(r'[A-Za-z0-9_.-]+', value['pool_name']):
                raise ValueError('publication journal mismatch')
        except (KeyError, ValueError, TypeError, ExecutionError):
            refuse('CPU_POLICY_INVALID', 'CPU policy does not match its publication journal')
        return value

    def context(self, record, policy=None):
        policy = self.policy() if policy is None else policy
        if policy is None:
            return {'configured':False}
        dedicated = next((v for v in policy['exclusive'] if v['vm_id'] == record['uuid']), None)
        if dedicated and (vm.guest_kind(record) != 'zephyr' or record['vcpus'] != 1):
            refuse('CPU_POLICY_INVALID', 'dedicated UUID no longer has the 1-vCPU Zephyr profile')
        cores = [dedicated['pcpu']] if dedicated else policy['shared_pcpus']
        return {'configured':True, 'policy_revision':policy['policy_revision'],
                'policy_sha256':fingerprint(policy), 'allocation':'exclusive' if dedicated else 'shared',
                'desired_hard_affinity':[{'vcpu':v, 'pcpus':cores} for v in range(record['vcpus'])]}

    def command(self, argv, deadline):
        if self.p.clock() >= deadline:
            refuse('DEADLINE_EXCEEDED', 'CPU observation deadline exceeded')
        try:
            result = self.p.timed_command(argv, deadline)
        except subprocess.TimeoutExpired:
            refuse('DEADLINE_EXCEEDED', 'Xen CPU command timed out', details={'command':argv[:2]})
        except (PlatformError, OSError, ValueError, subprocess.CalledProcessError):
            refuse('OBSERVATION_UNAVAILABLE', 'Xen CPU command failed', details={'command':argv[:2]})
        if self.p.clock() >= deadline:
            refuse('DEADLINE_EXCEEDED', 'CPU command completed after deadline')
        return result

    def hardware(self, deadline):
        info = self.command(['xl', 'info'], deadline)
        fields = dict(re.findall(r'^(nr_cpus|max_cpu_id)\s*:\s*([0-9]+)\s*$', info, re.M))
        count, maximum = int(fields['nr_cpus']), int(fields['max_cpu_id'])
        if maximum != 3 or not 1 <= count <= 4:
            refuse('RESOURCE_UNSUPPORTED', 'static CPU policy supports the four-pCPU RPi4 Xen topology')
        pool_lines = self.command(['xl', 'cpupool-list'], deadline).splitlines()
        maps = self.command(['xl', 'cpupool-list', '-c'], deadline).splitlines()
        if not pool_lines or not maps or 'Sched' not in pool_lines[0] or 'CPU list' not in maps[0]:
            raise ValueError('missing CPU pool headers')
        rows, masks = [v.split() for v in pool_lines[1:] if v.strip()], [v.split() for v in maps[1:] if v.strip()]
        if len(rows) != 1 or len(masks) != 1 or len(rows[0]) != 5 or len(masks[0]) != 2:
            refuse('RESOURCE_UNSUPPORTED', 'a single existing Credit2 pool with all online pCPUs is required')
        name, ncpu, scheduler, active, domains = rows[0]
        online = bitmap(masks[0][1], maximum)
        if (masks[0][0] != name or scheduler != 'credit2' or active != 'y' or
                int(ncpu) != len(online) or count != len(online)):
            refuse('RESOURCE_UNSUPPORTED', 'CPU pool is changing, incomplete, or not Credit2')
        # Xen cpupool cpu_valid excludes offlined CPUs. Equality with Xen nr_cpus
        # proves the single pool covers every online CPU; Linux CPU IDs are unused.
        return {'online_pcpus':online, 'max_cpu_id':maximum, 'pool_name':name, 'scheduler':scheduler}

    @traced('cpu.sample')
    def sample(self, deadline):
        hardware = self.hardware(deadline)
        listing = self.command(['xl', 'list', '-c'], deadline).splitlines()
        if not listing or 'Cpupool' not in listing[0]:
            raise ValueError('missing domain pool header')
        rows = {}
        for line in listing[1:]:
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) != 7 or parts[6] != hardware['pool_name']:
                raise ValueError('invalid domain pool row')
            name, domid, mem, vcpus, state, elapsed, pool = parts
            domid, vcpus = int(domid), int(vcpus)
            if domid in rows or vcpus < 1:
                raise ValueError('duplicate domain or invalid vCPU count')
            rows[domid] = {'name':name, 'domid':domid, 'vcpus':vcpus, 'hard_affinity':[]}
        details = self.p.domains(deadline=deadline)
        if {v['domid'] for v in details}-{0} != set(rows)-{0} or 0 not in rows:
            refuse('TARGET_CHANGED', 'domain set changed during CPU observation')
        if not any(v['domid'] == 0 for v in details):
            details.append({'domid':0})
        for row in details:
            target = rows[row['domid']]
            if row['domid']:
                if row['config']['c_info']['name'] != target['name']:
                    refuse('TARGET_CHANGED', 'domain name changed during CPU observation')
                target['vm_id'] = row['config']['c_info']['uuid']
            else:
                target['vm_id'] = None
            target['xen_unique_id'] = self.e.unique(row, deadline, allow_dom0=row['domid'] == 0)
        affinity = self.command(['xl', 'vcpu-list'], deadline).splitlines()
        if not affinity or 'Affinity (Hard / Soft)' not in affinity[0]:
            raise ValueError('missing hard affinity header')
        for line in affinity[1:]:
            if not line.strip():
                continue
            # CPU (the current running CPU), state and time are not affinity.
            parts = line.split()
            if len(parts) != 9 or parts[7] != '/':
                raise ValueError('invalid hard/soft affinity row')
            name, domid, vcpu = parts[0], int(parts[1]), int(parts[2])
            if domid not in rows or rows[domid]['name'] != name:
                refuse('TARGET_CHANGED', 'affinity domain identity changed')
            rows[domid]['hard_affinity'].append({'vcpu':vcpu, 'pcpus':bitmap(parts[6], hardware['max_cpu_id'])})
        for row in rows.values():
            row['hard_affinity'].sort(key=lambda v:v['vcpu'])
            if [v['vcpu'] for v in row['hard_affinity']] != list(range(row['vcpus'])):
                raise ValueError('missing, offline or duplicate vCPU observation')
        return {'hardware':hardware, 'domains':rows}

    @traced('cpu.observe')
    def observe(self, deadline):
        if self.p.boot_id_reader() != self.e.host:
            refuse('TARGET_CHANGED', 'CPU observation belongs to a previous host boot')
        try:
            first = self.sample(deadline)
            second = self.sample(deadline)
        except ExecutionError:
            raise
        except subprocess.TimeoutExpired:
            refuse('DEADLINE_EXCEEDED', 'domain identity/CPU observation timed out')
        except subprocess.CalledProcessError:
            refuse('OBSERVATION_UNAVAILABLE', 'domain identity/CPU command failed')
        except (KeyError, TypeError, ValueError, PlatformError, OSError) as error:
            refuse('OBSERVATION_UNAVAILABLE', 'cannot parse or identify complete Xen CPU state',
                   details={'cause_type':type(error).__name__})
        if first != second:
            refuse('TARGET_CHANGED', 'domain incarnation, set, topology or affinity changed during observation')
        if self.p.boot_id_reader() != self.e.host:
            refuse('TARGET_CHANGED', 'host boot changed during CPU observation')
        if self.p.clock() >= deadline:
            refuse('DEADLINE_EXCEEDED', 'CPU observation completed after deadline')
        return first

    @traced('cpu.layout')
    def layout(self, records, deadline, vm_id=None):
        policy = self.policy()
        if vm_id and not any(r['uuid'] == vm_id for r in records.values()):
            refuse('VM_NOT_FOUND', 'registered permanent VM UUID not found')
        sample = self.observe(deadline)
        hardware, domains = sample['hardware'], sample['domains']
        receipt = self.read(self.receipt_path) if policy else None
        desired = policy['shared_pcpus'] if policy else None
        host_matches = bool(policy and hardware['pool_name'] == policy['pool_name'] and
                            all(v['pcpus'] == desired for v in domains[0]['hard_affinity']) and
                            set(desired + [v['pcpu'] for v in policy['exclusive']]) <= set(hardware['online_pcpus']))
        pending = [v['operation_id'] for v in self.operations() if v['phase'] != 'completed']
        ready = bool(not pending and host_matches and receipt and receipt.get('phase') == 'ready' and
                     receipt.get('host_boot_id') == self.e.host and receipt.get('policy_sha256') == fingerprint(policy) and
                     receipt.get('policy_revision') == policy['policy_revision'] and
                     receipt.get('dom0_unique_id') == domains[0]['xen_unique_id'] and
                     receipt.get('vcpus') == domains[0]['vcpus'])
        all_issues = [{'code':'POLICY_PUBLICATION_PENDING', 'operation_id':v} for v in pending]
        if policy and not ready:
            all_issues.append({'code':'CPU_NOT_READY', 'domid':0})
        managed = {r['uuid']:r for r in records.values()}
        for row in domains.values():
            if not policy or row['domid'] == 0:
                continue
            record = managed.get(row['vm_id'])
            expected = self.context(record, policy)['desired_hard_affinity'] if record else [
                {'vcpu':v, 'pcpus':desired} for v in range(row['vcpus'])]
            if row['hard_affinity'] != expected:
                all_issues.append({'code':'AFFINITY_MISMATCH', 'domid':row['domid'], 'vm_id':row['vm_id'],
                                   'name':row['name'], 'expected':expected, 'actual':row['hard_affinity']})
        vms = []
        for record in records.values():
            context = self.context(record, policy)
            row = next((v for v in domains.values() if v['vm_id'] == record['uuid']), None)
            if row and row['name'] != record['name']:
                refuse('TARGET_CHANGED', 'managed UUID has an unexpected Xen name')
            matches, exclusive, issues = None, None, []
            if policy and row:
                matches = row['vcpus'] == record['vcpus'] and row['hard_affinity'] == context['desired_hard_affinity']
                if not matches:
                    issues.append({'code':'AFFINITY_MISMATCH'})
                if context['allocation'] == 'exclusive':
                    occupied = {c for a in row['hard_affinity'] for c in a['pcpus']}
                    conflicts = [{'domid':v['domid'], 'vm_id':v['vm_id'], 'name':v['name'],
                                  'vcpu':a['vcpu'], 'pcpus':sorted(occupied & set(a['pcpus']))} for v in domains.values()
                                 if v['domid'] != row['domid'] for a in v['hard_affinity'] if occupied & set(a['pcpus'])]
                    exclusive = bool(matches and not conflicts)
                    if conflicts:
                        issue = {'code':'EXCLUSIVE_CONFLICT', 'vm_id':record['uuid'], 'conflicts':conflicts}
                        issues.append(issue); all_issues.append(issue)
            run = self.e.run_record(record)
            lifecycle, generation = self.e.lifecycle(record)
            execution_id = run['execution_id'] if row and run and lifecycle == 'REGISTERED' and run.get('lifecycle_generation') == generation and run.get('phase') == 'observed' and (
                run.get('domid'), run.get('xen_unique_id')) == (row['domid'], row['xen_unique_id']) else None
            vms.append({'vm_id':record['uuid'], 'execution_id':execution_id, 'vcpus':record['vcpus'],
                        'allocation':context.get('allocation'), 'desired_hard_affinity':context.get('desired_hard_affinity'),
                        'observed':{'presence':'present' if row else 'absent',
                                    **{k:row[k] if row else None for k in ('domid', 'xen_unique_id', 'vcpus', 'hard_affinity')}},
                        'affinity_matches':matches, 'exclusive_verified':exclusive, 'issues':issues})
        if self.policy() != policy:
            refuse('TARGET_CHANGED', 'CPU policy changed during observation')
        return {'configured':policy is not None, 'policy_revision':policy['policy_revision'] if policy else None,
                'host_boot_id':self.e.host, 'observed_monotonic_ms':int(self.p.clock()*1000),
                'host':{**hardware, 'desired_dom0_pcpus':desired,
                        'observed_dom0_hard_affinity':domains[0]['hard_affinity'], 'ready':ready},
                'reservations':policy['exclusive'] if policy else [],
                'vms':[v for v in vms if vm_id is None or v['vm_id'] == vm_id], 'issues':all_issues}

    @traced('cpu.require')
    def require(self, record, deadline, *, expected=None, present=False, expected_identity=None, capture=None):
        """전체 두 표본 검증. capture는 바로 중첩된 내부 호출에만 근거를 전달한다."""
        if self.p.clock() >= deadline:
            refuse('DEADLINE_EXCEEDED', 'CPU validation deadline expired')
        context = self.context(record)
        if expected is not None and expected != context:
            refuse('TARGET_CHANGED', 'accepted CPU policy context changed')
        if not context['configured']:
            if capture is not None:
                capture['evidence'] = _ValidationEvidence(self, record, deadline, context, None)
            return context
        if any(v['phase'] != 'completed' for v in self.operations()):
            refuse('RECONCILE_REQUIRED', 'CPU policy publication is pending; finish the original operation')
        layout = self.layout(self.p.records(validate_assets=False), deadline)
        if not layout['host']['ready']:
            refuse('CPU_NOT_READY', 'prepare this host boot and policy revision before starting guests',
                   details={'issues':layout['issues']})
        target = next(v for v in layout['vms'] if v['vm_id'] == record['uuid'])
        if expected_identity and any(target['observed'][k] != expected_identity[k] for k in ('domid','xen_unique_id')):
            refuse('TARGET_CHANGED', 'CPU observation belongs to a different execution incarnation')
        if layout['issues'] or (present and target['observed']['presence'] != 'present'):
            refuse('CPU_APPLY_INCOMPLETE', 'observed CPU layout does not comply; no automatic repinning',
                   details={'issues':layout['issues'], 'vm':target})
        if capture is not None:
            capture['evidence'] = _ValidationEvidence(self, record, deadline, context, layout)
            event('evidence.created', phase='cpu.require', policy_revision=context['policy_revision'])
        return context

    def consume(self, record, deadline, evidence, *, expected=None):
        """동일 단계의 일회성 근거를 소비한다. 정책/의도/구성 변경은 즉시 거절한다."""
        if (not isinstance(evidence, _ValidationEvidence) or evidence.consumed or
                evidence.platform is not self.p or evidence.host != self.e.host or
                self.p.boot_id_reader() != evidence.host or evidence.deadline != deadline or
                evidence.record != fingerprint(record) or evidence.lifecycle != self.e.lifecycle(record) or
                evidence.intent != fingerprint(self.e.intent(record))):
            refuse('TARGET_CHANGED', 'CPU evidence does not belong to this unchanged launch phase')
        evidence.consumed = True
        if self.p.clock() >= deadline:
            refuse('DEADLINE_EXCEEDED', 'CPU evidence expired with the operation deadline')
        context = self.context(record)
        if context != evidence.context or (expected is not None and expected != context):
            refuse('TARGET_CHANGED', 'CPU policy changed after complete validation')
        event('evidence.reused', phase='launch.prepare', policy_revision=context.get('policy_revision'))
        return context

    def guard_stopped(self, records, sample, *, policy_operation=None):
        from xenova_preparation import assets_quiet
        assets_quiet(self.p)
        for record in records.values():
            if self.p.matching(record, [{'domid':v['domid'], 'config':{'c_info':{'uuid':v['vm_id'], 'name':v['name']}}} for v in sample['domains'].values()]):
                refuse('VM_NOT_STOPPED', 'all managed guests must be stopped before CPU policy or host preparation')
            run = self.e.run_record(record)
            if run and run.get('phase') in ('launching', 'uncertain'):
                refuse('RECONCILE_REQUIRED', 'unfinished launch owns CPU configuration')
        for op in self.operations():
            if op['phase'] != 'completed' and op['operation_id'] != policy_operation:
                refuse('RECONCILE_REQUIRED', 'finish the pending CPU policy publication first')
        from xenova_management import Manager
        from xenova_creation import holds
        manager=Manager(self.p)
        if any(holds(manager,op) for op in manager._operations()):
            refuse('OPERATION_IN_PROGRESS', 'unfinished asset operation blocks CPU policy', retryable=True)
        from xenova_recovery import Recovery
        for op in self.e.requests()+Recovery(Manager(self.p)).attempts():
            if op.get('host_boot_id') == self.e.host and op.get('phase') not in ('completed', 'failed'):
                refuse('RECONCILE_REQUIRED', 'unfinished control/recovery blocks CPU policy')
        # Unknown domains are never silently moved. They must already respect the
        # proposed shared set; checked separately once the candidate is known.

    def set_policy(self, spec, records, *, execute, key, context, deadline=None):
        # 등록 검증과 factory 준비도 호출자의 전체 기한을 공유한다.
        deadline = self.p.clock()+15 if deadline is None else deadline
        spec = validate_spec(spec)
        if not isinstance(key, str) or not key or len(key.encode()) > 200:
            refuse('INVALID_SPEC', 'cpu-policy-set requires an idempotency key in plan and execute mode')
        ops = self.operations()
        op = next((v for v in ops if v.get('idempotency_key') == key), None)
        if op and op['request'] != spec:
            refuse('IDEMPOTENCY_CONFLICT', 'CPU policy key has a different request')
        if op:
            context.update(operation_id=op['operation_id'], replayed=True)
            if op['phase'] == 'completed':
                return {'policy':op['candidate'], 'current_policy':self.policy(), 'historical':True}
        previous = self.policy(pending=op)
        if op and previous == op['candidate']:
            if execute:
                op['phase'] = 'completed'; self.e.write(self.journals/(op['operation_id']+'.json'), op)
            return {'policy':previous, 'plan':not execute}
        revision = previous['policy_revision'] if previous else None
        if spec['expected_policy_revision'] != revision:
            refuse('TARGET_CHANGED', 'CPU policy revision changed', details={'current_policy_revision':revision})
        if any(v['phase'] != 'completed' and v != op for v in ops):
            refuse('RECONCILE_REQUIRED', 'resume the pending CPU policy operation with its original key')
        receipt = self.read(self.receipt_path)
        if receipt and receipt.get('host_boot_id') == self.e.host and receipt.get('phase') in ('applying', 'failed', 'restoring'):
            refuse('RECONCILE_REQUIRED', 'finish or restore partial dom0 affinity before changing policy')
        sample = self.observe(deadline)
        self.guard_stopped(records, sample, policy_operation=op['operation_id'] if op else None)
        available = set(sample['hardware']['online_pcpus'])
        if not set(spec['shared_pcpus']+[v['pcpu'] for v in spec['exclusive']]) <= available:
            refuse('RESOURCE_UNSUPPORTED', 'selected pCPU is not online in the existing Xen pool')
        for entry in spec['exclusive']:
            record = next((r for r in records.values() if r['uuid'] == entry['vm_id']), None)
            if record is None:
                refuse('VM_NOT_FOUND', 'reservation requires an existing registered UUID')
            if vm.guest_kind(record) != 'zephyr' or record['vcpus'] != 1:
                refuse('RESOURCE_UNSUPPORTED', 'only 1-vCPU Zephyr can have a dedicated reservation')
        self.check_unknown(records, sample, spec['shared_pcpus'])
        opid = op['operation_id'] if op else uuid4()
        candidate = {'schema_version':'1.0', 'policy_revision':(revision or 0)+1, 'operation_id':opid,
                     'pool_name':sample['hardware']['pool_name'], 'shared_pcpus':spec['shared_pcpus'], 'exclusive':spec['exclusive']}
        if self.p.clock() >= deadline:
            refuse('DEADLINE_EXCEEDED', 'CPU policy validation exceeded the original deadline')
        if not execute:
            return {'plan':True, 'policy':candidate, 'host_preparation_required':True}
        if op and op['candidate'] != candidate:
            refuse('TARGET_CHANGED', 'pending CPU publication no longer fits the observed host')
        op = op or {'schema_version':'1.0', 'operation_id':opid, 'idempotency_key':key, 'request':spec,
                    'base_revision':revision, 'candidate':candidate, 'phase':'prepared'}
        context['operation_id'] = opid
        self.e.write(self.journals/(opid+'.json'), op)
        self.e.checkpoint('cpu-policy-prepared', op)
        self.e.write(self.anchor, {'schema_version':'1.0', 'activation_operation_id':opid})
        self.e.write(self.policy_path, candidate)
        self.e.checkpoint('cpu-policy-published', op)
        op['phase'] = 'completed'; self.e.write(self.journals/(opid+'.json'), op)
        return {'policy':candidate, 'host_preparation_required':True}

    def check_unknown(self, records, sample, shared):
        managed = {r['uuid'] for r in records.values()}
        for row in sample['domains'].values():
            if row['domid'] and row['vm_id'] not in managed and any(v['pcpus'] != shared for v in row['hard_affinity']):
                refuse('CPU_APPLY_INCOMPLETE', 'unregistered domain conflicts with shared placement', details={'domain':row})

    def factory(self, records, deadline):
        path = self.p.data/'.first-install-state.json'
        if not path.exists():
            return
        catalog.plain(path)
        state = json.loads(path.read_text())
        activation = state.get('cpu_policy_activation')
        if not activation:
            return
        if any(v['vm_id'] not in {i['uuid'] for i in state['identities'].values()} for v in activation['exclusive']):
            refuse('CPU_POLICY_INVALID', 'factory CPU activation does not match the durable identity plan')
        key = 'factory-cpu:'+state['storage_uuid']
        if self.policy_path.exists():
            self.policy()
            previous = next((v for v in self.operations() if v['idempotency_key'] == key),None)
            if previous and previous['phase'] != 'completed':
                self.set_policy(activation, records, execute=True, key=key, context={}, deadline=deadline)
            return
        self.set_policy(activation, records, execute=True, key=key, context={}, deadline=deadline)

    def prepare(self, records, deadline, *, execute=False, restore=False, context=None):
        from xenova_runtime import boot_progress
        display = boot_progress('cpu', self.p.runtime, clock=self.p.clock, host=self.e.host)
        if execute:
            display('preparing' if not restore else 'restoring')
        if execute and not restore:
            self.factory(records, deadline)
        policy = self.policy()
        if not policy:
            if execute:
                display('complete', configured=False)
            return {'configured':False, 'ready':False, 'legacy':True}
        sample = self.observe(deadline)
        self.guard_stopped(records, sample)
        self.check_unknown(records, sample, policy['shared_pcpus'])
        if (sample['hardware']['pool_name'] != policy['pool_name'] or not
                set(policy['shared_pcpus']+[v['pcpu'] for v in policy['exclusive']]) <= set(sample['hardware']['online_pcpus'])):
            refuse('CPU_NOT_READY', 'saved CPU policy does not fit the current Xen pool/topology')
        row = sample['domains'][0]
        receipt = self.read(self.receipt_path)
        same = bool(receipt and receipt.get('host_boot_id') == self.e.host and
                    receipt.get('policy_sha256') == fingerprint(policy) and receipt.get('dom0_unique_id') == row['xen_unique_id'] and
                    receipt.get('vcpus') == row['vcpus'])
        if restore and not same:
            refuse('TARGET_CHANGED', 'no matching dom0 preparation receipt to restore')
        if not execute:
            return {'plan':True, 'configured':True, 'policy_revision':policy['policy_revision'],
                    'dom0_vcpus':row['vcpus'], 'restore':restore}
        if not same:
            receipt = {'schema_version':'1.0', 'operation_id':uuid4(), 'host_boot_id':self.e.host,
                       'policy_revision':policy['policy_revision'], 'policy_sha256':fingerprint(policy),
                       'dom0_unique_id':row['xen_unique_id'], 'vcpus':row['vcpus'],
                       'original_hard_affinity':row['hard_affinity'], 'completed_vcpus':[]}
        if context is not None:
            context['operation_id'] = receipt['operation_id']
        desired = receipt['original_hard_affinity'] if restore else [
            {'vcpu':v, 'pcpus':policy['shared_pcpus']} for v in range(row['vcpus'])]
        original = receipt['original_hard_affinity']
        if any(a not in (original[i], {'vcpu':i, 'pcpus':policy['shared_pcpus']}) for i,a in enumerate(row['hard_affinity'])):
            refuse('TARGET_CHANGED', 'dom0 affinity was changed externally; receipt cannot be resumed safely')
        receipt.update(phase='restoring' if restore else 'applying', error=None)
        self.e.write(self.receipt_path, receipt)
        try:
            for affinity in desired:
                if self.policy() != policy or self.e.unique({'domid':0}, deadline, allow_dom0=True) != row['xen_unique_id']:
                    refuse('TARGET_CHANGED', 'CPU policy or dom0 incarnation changed during prepare')
                self.command(['xl', 'vcpu-pin', '0', str(affinity['vcpu']), ','.join(map(str, affinity['pcpus'])) or 'none', '-'], deadline)
                receipt['completed_vcpus'] = sorted(set(receipt['completed_vcpus']+[affinity['vcpu']]))
                self.e.write(self.receipt_path, receipt)
                self.e.checkpoint('cpu-dom0-vcpu-applied', receipt)
            after = self.observe(deadline)
            before_ids = {k:v['xen_unique_id'] for k,v in sample['domains'].items()}
            after_ids = {k:v['xen_unique_id'] for k,v in after['domains'].items()}
            if before_ids != after_ids or after['hardware'] != sample['hardware'] or self.policy() != policy:
                refuse('TARGET_CHANGED', 'domain set, incarnation, pool or policy changed during dom0 preparation')
            if after['domains'][0]['hard_affinity'] != desired or after['domains'][0]['xen_unique_id'] != row['xen_unique_id']:
                refuse('CPU_APPLY_INCOMPLETE', 'dom0 hard affinity verification failed')
            receipt['phase'] = 'restored' if restore else 'ready'
            self.e.write(self.receipt_path, receipt)
        except (PlatformError, OSError, ValueError) as error:
            receipt.update(phase='failed', error=getattr(error, 'error', {'code':'STORAGE_UNAVAILABLE', 'message':str(error)}))
            self.e.write(self.receipt_path, receipt)
            raise
        display('complete', configured=True, policy_revision=policy['policy_revision'], restored=restore)
        return {'configured':True, 'ready':not restore, 'preparation':receipt}


def factory_activation(identities, preset_path=Path('/etc/xenova/cpu-policy-preset.json')):
    """Called only while a new factory identity plan is created, never on upgrades."""
    if not preset_path.exists():
        return None
    catalog.plain(preset_path)
    preset = json.loads(preset_path.read_text())
    if set(preset) != {'shared_pcpus', 'exclusive_instances'}:
        raise ValueError('invalid factory CPU preset')
    return validate_spec({'expected_policy_revision':None, 'shared_pcpus':preset['shared_pcpus'],
                          'exclusive':[{'vm_id':identities[v['instance_id']]['uuid'], 'pcpu':v['pcpu']}
                                       for v in preset['exclusive_instances']]})
