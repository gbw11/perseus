"""완료 결과·현재 조회·총 관측 예산의 공개 Manager 호환 경계를 검사한다.

실제 Manager/Execution/journal을 사용한다. Xen·단조 시계·작은 자산은 fixture이고,
상위 제품 코드는 수정하지 않고 실제 view mapper를 읽어 정상 응답 호환성을 검사한다.
"""
import ast
import copy
from dataclasses import dataclass
from enum import Enum
import io
import json
from contextlib import redirect_stdout
from typing import Any
import unittest
from unittest.mock import patch
from uuid import UUID

from first_install_fixture import ROOT
import test_xenova_recovery_foundation as foundation
import xenova_execution as execution
import xenova_management_cli as cli
import xenova_vm as vm


class ManagerBoundaryTest(unittest.TestCase):
    setUp = foundation.FoundationTest.setUp
    sleep = foundation.FoundationTest.sleep
    call = foundation.FoundationTest.call
    ok = foundation.FoundationTest.ok
    fails = foundation.FoundationTest.fails
    start = foundation.FoundationTest.start
    stop = foundation.FoundationTest.stop
    state = foundation.FoundationTest.state
    spec = foundation.FoundationTest.spec
    recover = foundation.FoundationTest.recover
    physical = foundation.FoundationTest.physical

    def second_vm(self):
        spec = {**self.f.spec, 'vm_id':'650e8400-e29b-41d4-a716-446655440000', 'ipv4':'192.168.77.31'}
        self.ok(self.m.call('create', spec, execute=True))
        self.xen.records.update(self.p.records(validate_assets=False))
        return spec['vm_id']

    def test_view_composition_never_queries_xen_with_or_without_observation(self):
        e = self.p.execution()
        tomb = self.m._state(self.vm_id, self.p.records(validate_assets=False))
        observation = e.observe(self.r, self.ticks + 15)
        with patch.object(self.p, 'command', side_effect=AssertionError('formatting must not query Xen')), \
             patch.object(execution.Execution, 'observe', side_effect=AssertionError('formatting must not observe')):
            observed = self.m._view(tomb, observation=observation)
            plain = self.m._view(tomb)
        self.assertEqual(observed['vm_id'], self.vm_id)
        self.assertEqual(observed['xen_state'], observation['xen_state'])
        self.assertIsNone(plain['xen_state'])
        self.assertIsNone(plain['domid'])

    def test_get_resolves_only_requested_view_and_list_observes_all_once(self):
        second = self.second_vm()
        e = self.p.execution()
        with patch.object(self.m, '_state', wraps=self.m._state) as state, \
             patch.object(execution.Execution, 'observe', autospec=True, side_effect=execution.Execution.observe) as observe:
            result = self.ok(self.m.call('get', {'vm_id':self.vm_id}))
        self.assertEqual(result['vm']['vm_id'], self.vm_id)
        self.assertEqual([c.args[0] for c in state.call_args_list], [self.vm_id])
        self.assertEqual(observe.call_count, 1)
        with patch.object(execution.Execution, 'observe_many', autospec=True, side_effect=execution.Execution.observe_many) as many, \
             patch.object(execution.Execution, 'observe', side_effect=AssertionError('list must use one batch')):
            listed = self.ok(self.m.call('list'))
        self.assertEqual({v['vm_id'] for v in listed['vms']}, {self.vm_id, second})
        self.assertEqual(many.call_count, 1)

    def test_registry_cost_consumes_get_and_list_original_fifteen_second_budget(self):
        self.second_vm()
        original = self.p.records
        def delayed(**kwargs):
            self.ticks += 5
            return original(**kwargs)
        for action, spec in (('get', {'vm_id':self.vm_id}), ('list', None)):
            entered = self.ticks
            method = 'observe' if action == 'get' else 'observe_many'
            e = self.p.execution()
            with patch.object(self.p, 'records', side_effect=delayed), \
                 patch.object(execution.Execution, method, autospec=True,
                              side_effect=getattr(execution.Execution, method)) as observe:
                self.ok(self.m.call(action, spec))
            self.assertEqual(observe.call_args.args[2], entered + 15)
            self.assertEqual(self.ticks, entered + 5)

    def test_query_observation_failure_remains_error_never_empty_or_absent_success(self):
        e = self.p.execution()
        error = execution.ExecutionError('OBSERVATION_UNAVAILABLE', 'injected unavailable Xen')
        for action, method, spec in (
            ('get', 'observe', {'vm_id':self.vm_id}),
            ('probe', 'observe', {'vm_id':self.vm_id, 'timeout_ms':1000}),
            ('execution-context', 'observe', {'vm_id':self.vm_id}),
            ('list', 'observe_many', None),
        ):
            with self.subTest(action=action), patch.object(execution.Execution, method, side_effect=error):
                response = self.m.call(action, spec)
            self.fails(response, 'OBSERVATION_UNAVAILABLE')
            self.assertIsNone(response['data'])

    def test_completed_start_replay_preserves_original_after_postquery_failure(self):
        first = self.call('start', key='manager-boundary-start')
        first_data = self.ok(first)
        before = len(self.physical('create'))
        e = self.p.execution()
        with patch.object(execution.Execution, 'observe', side_effect=execution.ExecutionError('OBSERVATION_UNAVAILABLE', 'query failed')):
            replay = self.call('start', key='manager-boundary-start')
        data = self.ok(replay)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['operation_id'], first['operation_id'])
        self.assertEqual(data['original_result'], first_data['original_result'])
        self.assertIsNone(data['vm'])
        self.assertIsNone(data['current_observation'])
        self.assertTrue(data['diagnostic_warnings'])
        self.assertEqual(len(self.physical('create')), before)
        op = next(op for op in e.requests() if op['operation_id'] == first['operation_id'])
        self.assertEqual(op['phase'], 'completed')

    def test_replay_after_user_stop_keeps_original_success_and_current_absence_separate(self):
        first = self.ok(self.call('start', key='original-start'))
        original = copy.deepcopy(first['original_result'])
        self.stop('later-user-stop')
        before = len(self.physical('create'))
        replay = self.ok(self.call('start', key='original-start'))
        self.assertEqual(replay['original_result'], original)
        self.assertIsNotNone(original['observation']['domid'])
        self.assertIsNone(replay['current_observation']['domid'])
        self.assertIsNone(replay['vm']['domid'])
        self.assertEqual(replay['vm']['desired_state'], 'stopped')
        self.assertEqual(len(self.physical('create')), before)

    def test_completed_recovery_replay_query_failure_preserves_original_and_issues_no_commands(self):
        self.start('recovery-initial')
        request = self.spec()
        first = self.recover(request)
        original = self.ok(first)['original_result']
        before = {name:len(self.physical(name)) for name in ('create', 'shutdown', 'reboot', 'destroy')}
        self.ticks = request['deadline_monotonic_ms'] / 1000 + 1
        e = self.p.execution()
        with patch.object(execution.Execution, 'observe', side_effect=execution.ExecutionError('OBSERVATION_UNAVAILABLE', 'late query failed')):
            replay = self.recover(request)
        data = self.ok(replay)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['operation_id'], first['operation_id'])
        self.assertEqual(data['original_result'], original)
        self.assertIsNone(data['current_observation'])
        self.assertTrue(data['diagnostic_warnings'])
        self.assertEqual(before, {name:len(self.physical(name)) for name in before})

    def test_failing_optional_trace_sink_does_not_reclassify_completed_start(self):
        self.m.trace_sink = lambda trace: (_ for _ in ()).throw(OSError('trace sink unavailable'))
        result = self.call('start', key='trace-failure')
        data = self.ok(result)
        self.assertIsNotNone(data['vm']['domid'])
        self.assertEqual(data['diagnostic_warnings'][-1]['sink'], 'management-trace')
        self.assertIsNotNone(self.m.last_trace)
        journal = next(op for op in self.p.execution().requests() if op['operation_id'] == result['operation_id'])
        self.assertEqual(journal['phase'], 'completed')

    def test_cli_trace_does_not_pollute_single_json_stdout(self):
        output = io.StringIO()
        with patch.object(cli, 'Manager', return_value=self.m), redirect_stdout(output):
            returned = cli.main(['start', self.vm_id, '--idempotency-key', 'single-json', '--json'])
        self.assertTrue(returned['ok'])
        envelope = json.loads(output.getvalue())
        self.assertTrue(envelope['ok'])
        self.assertIsNotNone(envelope['data']['vm']['domid'])

    def test_normal_start_view_is_consumed_by_actual_dom0_mapper_without_product_changes(self):
        # 제품 모듈 전체를 import하면 별도 FastAPI/Pydantic 의존성이 필요하다.
        # 순수 mapper와 그 Domain/VmState 정의만 원본 AST로 실행한다.
        sources = {
            ROOT / 'platform/services/dom0-manager/dom0_manager/models.py': {'VmState'},
            ROOT / 'platform/services/dom0-manager/dom0_manager/drivers/base.py': {'Domain'},
            ROOT / 'platform/services/dom0-manager/dom0_manager/drivers/platform.py': {'_STATE_MAP', 'map_platform_state', 'view_to_domain'},
        }
        namespace = {'__name__':__name__, 'Enum':Enum, 'dataclass':dataclass, 'UUID':UUID,
                     'Any':Any, 'DriverError':RuntimeError}
        for path, names in sources.items():
            tree = ast.parse(path.read_text())
            nodes = [ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]
            nodes += [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names]
            nodes += [node for node in tree.body if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                      and node.target.id in names]
            self.assertEqual(len(nodes), len(names) + 1)
            exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(path), 'exec'), namespace)
        view = self.ok(self.call('start', key='normal-consumer'))['vm']
        domain = namespace['view_to_domain'](view)
        self.assertIsNotNone(domain)
        self.assertEqual(str(domain.vm_id), self.vm_id)
        self.assertEqual(domain.vcpus, self.r['vcpus'])
        self.assertEqual(domain.memory_mib, self.r['memory_mib'])
        self.assertEqual(domain.domid, view['domid'])

    def test_handoff_examples_keep_complete_envelopes_and_historical_current_distinction(self):
        fixture = json.loads((ROOT / 'platform/tests/fixtures/management-boundary-responses.json').read_text())
        self.assertFalse(fixture['provenance']['hardware_acceptance'])
        examples = fixture['examples']
        envelope = {'schema_version', 'ok', 'request_id', 'operation_id', 'replayed', 'data', 'error'}
        for response in examples.values():
            self.assertEqual(set(response), envelope)
        normal = examples['normal_start']
        self.assertTrue(normal['ok'])
        self.assertIsNotNone(normal['data']['vm']['domid'])
        failed_current = examples['completed_replay_current_query_failed']
        self.assertTrue(failed_current['ok'])
        self.assertIsNone(failed_current['data']['vm'])
        self.assertIsNone(failed_current['data']['current_observation'])
        self.assertEqual(failed_current['data']['original_result'], normal['data']['original_result'])
        stopped = examples['completed_replay_after_user_stop']['data']
        self.assertEqual(stopped['original_result'], normal['data']['original_result'])
        self.assertIsNone(stopped['vm']['domid'])
        self.assertEqual(examples['current_domain_identity_changed']['error']['code'], 'TARGET_CHANGED')
        self.assertEqual(examples['create_submitted_result_uncertain']['error']['code'], 'RECONCILE_REQUIRED')


if __name__ == '__main__':
    unittest.main()
