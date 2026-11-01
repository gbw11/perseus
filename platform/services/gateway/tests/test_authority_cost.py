"""Authority의 동일 JSON 재사용 비용과 내구/독립 사본 보호를 함께 검사한다."""
import errno
import json
import os
from unittest.mock import patch

import pytest
import xenova_gateway.authority as authority

HOST='11111111-1111-4111-8111-111111111111'
OWNER='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'


def store_at(path):
    store=authority.AuthorityStore(path,HOST,uid=os.geteuid(),mountpoint=None)
    store.initialize(OWNER)
    return store


@pytest.mark.parametrize('count',[0,32,128])
def test_one_bounded_serialization_and_one_durable_write(tmp_path,count):
    store=store_at(tmp_path/'authority')
    value={**store.value,'requests':['a5000000-1111-4111-8111-'+f'{i:012x}' for i in range(count)]}
    with patch.object(json,'dumps',wraps=json.dumps) as encode, \
         patch.object(json,'dump',wraps=json.dump) as write, \
         patch.object(os,'fsync',wraps=os.fsync) as sync:
        store.save(value)
    assert encode.call_count==1
    assert write.call_count==1 and sync.call_count==2
    assert json.loads(store.path.read_text())==store.value==value
    value['requests'].append('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')
    assert len(store.value['requests'])==count


def test_caller_change_during_write_does_not_change_submitted_snapshot(tmp_path):
    store=store_at(tmp_path/'authority');value={**store.value,'reason':'submitted'}
    write=authority.atomic_json
    def changed(path,snapshot):
        value['reason']='changed';write(path,snapshot)
    with patch.object(authority,'atomic_json',changed):store.save(value)
    assert store.value['reason']=='submitted'
    assert json.loads(store.path.read_text())==store.value


def test_failed_sync_preserves_old_memory_and_blocks_further_writes(tmp_path):
    store=store_at(tmp_path/'authority');before=store.value.copy()
    with patch.object(os,'fsync',side_effect=OSError(errno.EIO,'owned storage fault')):
        with pytest.raises(authority.AuthorityError,match='UNCERTAIN'):
            store.save({**before,'generation':2})
    assert store.value==before
    with pytest.raises(authority.AuthorityError,match='UNCERTAIN'):store.save(before)


def test_record_bound_rejection_has_no_storage_effect(tmp_path):
    store=store_at(tmp_path/'authority');before=store.path.read_bytes()
    with patch.object(authority,'atomic_json',side_effect=AssertionError('oversized write')):
        with pytest.raises(authority.AuthorityError,match='size limit'):
            store.save({**store.value,'extra':'x'*65536})
    assert store.path.read_bytes()==before
