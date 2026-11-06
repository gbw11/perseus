"""Share initial host inputs within one read; never cache final validation."""
from unittest.mock import patch

import pytest

from test_xenova_batch_read_queries import pair
from xenova_execution import Execution, ReadChanged
from xenova_management import ReadContext


def context(case):
    return ReadContext(case.m.fork(), 'list', {'lifecycle': 'REGISTERED'}, case.p.clock()+5)


def test_initial_host_read_once_but_each_publication_rechecks(pair):
    calls=[]
    original=Execution.barrier
    def barrier(self):
        calls.append(1)
        return original(self)
    with patch.object(Execution, 'barrier', barrier):
        read=context(pair)
        assert len(read.states)==2 and len(calls)==1
        for state in read.states:
            read.validate_one(state['vm_id'])
        assert len(calls)==3  # one initial read + both fresh final checks


def test_new_query_does_not_reuse_previous_host_inputs(pair):
    with patch.object(Execution, 'barrier', return_value=None) as barrier:
        first=context(pair)
        second=context(pair)
        assert barrier.call_count==2
        assert first.inputs is not second.inputs


def test_host_barrier_change_before_publication_is_rejected(pair):
    with patch.object(Execution, 'barrier', return_value=None):
        read=context(pair)
    with patch.object(Execution, 'barrier', return_value={'state':'quiescing'}):
        with pytest.raises(ReadChanged):
            read.validate_one(read.states[0]['vm_id'])


def test_shutdown_change_before_publication_is_rejected(pair):
    read=context(pair)
    (pair.p.runtime/'shutdown-requested').touch()
    with pytest.raises(ReadChanged):
        read.validate_one(read.states[0]['vm_id'])


def test_non_context_inputs_still_read_current_host_every_time(pair):
    read=context(pair)
    state=read.states[0]
    with patch.object(Execution, 'barrier', side_effect=[None, {'state':'quiescing'}]) as barrier:
        first=read.e.observation_inputs(state['record'], read.lifecycles[state['vm_id']])
        second=read.e.observation_inputs(state['record'], read.lifecycles[state['vm_id']])
        assert first['barrier'] is None
        assert second['barrier']=={'state':'quiescing'}
        assert barrier.call_count==2
