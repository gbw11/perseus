"""Offline recovery must preserve evidence and never infer backup freshness."""
import errno
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from xenova_gateway.authority import AuthorityStore,AuthorityError,GatewayLease
from xenova_gateway.authority_ops import inspect,revalidate
from xenova_mqtt.drive_binding import atomic_json

HOST='11111111-1111-4111-8111-111111111111'
A='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'


def store_at(path):
    store=AuthorityStore(path,HOST,uid=os.geteuid(),mountpoint=None)
    store.initialize(A)
    return store


def report(path,**kwargs):return inspect(path,HOST,uid=os.geteuid(),mountpoint=None,**kwargs)


def test_inspection_never_creates_or_initializes(tmp_path):
    path=tmp_path/'absent'
    assert report(path)['status']=='FIRST_INSTALL' and not path.exists()
    path.mkdir(mode=0o700)
    assert report(path)['status']=='FIRST_INSTALL' and not list(path.iterdir())
    (path/'control.json.interrupted.tmp').write_text('{}')
    assert report(path)['status']=='BLOCKED'
    with pytest.raises(AuthorityError):AuthorityStore(path,HOST,uid=os.geteuid(),mountpoint=None).initialize(A)


@pytest.mark.parametrize('marker',['symlink','public','invalid','fifo'])
def test_marker_is_private_regular_evidence(tmp_path,marker):
    store=store_at(tmp_path/'authority');original=store.marker.read_bytes()
    store.marker.unlink()
    if marker=='symlink':
        target=tmp_path/'target';target.write_bytes(original);store.marker.symlink_to(target)
    elif marker=='fifo':os.mkfifo(store.marker,0o600)
    else:
        store.marker.write_bytes(b'bad' if marker=='invalid' else original)
        store.marker.chmod(0o644 if marker=='public' else 0o600)
    assert report(store.directory)['status']=='BLOCKED'
    with pytest.raises(AuthorityError):store.load()
    assert not store.verify()


def test_same_generation_and_old_backup_are_not_authority(tmp_path):
    store=store_at(tmp_path/'authority');backup=tmp_path/'backup.json'
    atomic_json(backup,store.value)
    assert report(tmp_path/'missing',candidate=backup)['status']=='BLOCKED'
    assert report(store.directory,candidate=backup)['candidate_matches_current']
    store.update(generation=2)
    value=report(store.directory,candidate=backup)
    assert value['status']=='VALID' and not value['candidate_matches_current']
    assert not value['candidate_is_freshness_proof'] and not value['restoration_supported']
    store.path.write_text('{corrupt')
    assert report(store.directory,candidate=backup)['status']=='BLOCKED'
    before=store.path.read_bytes()
    lease=GatewayLease(tmp_path/'owner.lock').acquire()
    try:
        with pytest.raises(AuthorityError):revalidate(store.directory,HOST,'0'*64,lease,uid=os.geteuid(),mountpoint=None)
    finally:lease.close()
    assert store.path.read_bytes()==before


def test_nonregular_record_is_blocked_without_waiting(tmp_path):
    store=store_at(tmp_path/'authority');store.path.unlink();os.mkfifo(store.path,0o600)
    with pytest.raises(AuthorityError):store.load()
    assert report(store.directory)['status']=='BLOCKED'


def test_valid_original_revalidation_preserves_original_context(tmp_path):
    store=store_at(tmp_path/'authority');request=str(uuid4())
    store.update(generation=7,mode='AUTO',requests=[request],rearm_required=False,phase='PENDING',
                 pending={'host_boot_id':HOST,'generation':7,'deadline_ms':9000,'arm_after_apply':True})
    old=json.loads(store.path.read_text());marker=store.marker.read_bytes()
    lease=GatewayLease(tmp_path/'owner.lock').acquire()
    try:
        expected=report(store.directory)['record_sha256']
        result=revalidate(store.directory,HOST,expected,lease,uid=os.geteuid(),mountpoint=None)
    finally:lease.close()
    new=json.loads(store.path.read_text())
    assert new=={**old,'rearm_required':True} and store.marker.read_bytes()==marker
    assert result['status']=='REVALIDATED' and result['generation']==7 and not result['output_permitted']
    restarted=AuthorityStore(store.directory,HOST,uid=os.geteuid(),mountpoint=None)
    assert restarted.load()==new and request in restarted.value['requests']


def test_revalidation_process_exit_preserves_disarm_and_original_request(tmp_path):
    import multiprocessing
    store=store_at(tmp_path/'authority');request=str(uuid4())
    store.update(generation=9,requests=[request],rearm_required=False)
    expected=report(store.directory)['record_sha256']
    def child():
        import xenova_gateway.authority as authority
        def exit_after_replace(path,value):
            atomic_json(path,value);os._exit(71)
        authority.atomic_json=exit_after_replace
        lease=GatewayLease(tmp_path/'owner.lock').acquire()
        revalidate(store.directory,HOST,expected,lease,uid=os.geteuid(),mountpoint=None)
    process=multiprocessing.get_context('fork').Process(target=child)
    process.start();process.join(5)
    assert process.exitcode==71
    restarted=AuthorityStore(store.directory,HOST,uid=os.geteuid(),mountpoint=None)
    value=restarted.load()
    assert value['generation']==9 and value['requests']==[request] and value['rearm_required']
    lease=GatewayLease(tmp_path/'owner.lock').acquire();lease.close()


def test_stale_diagnostic_or_owner_is_rejected(tmp_path):
    store=store_at(tmp_path/'authority');expected=report(store.directory)['record_sha256']
    lease=GatewayLease(tmp_path/'owner.lock').acquire()
    try:
        store.update(generation=2)
        with pytest.raises(AuthorityError):revalidate(store.directory,HOST,expected,lease,uid=os.geteuid(),mountpoint=None)
        assert json.loads(store.path.read_text())['generation']==2
        with pytest.raises(AuthorityError):GatewayLease(lease.path).acquire()
    finally:lease.close()
    with pytest.raises(AuthorityError):revalidate(store.directory,HOST,expected,lease,uid=os.geteuid(),mountpoint=None)


@pytest.mark.parametrize('replace_first',[False,True])
def test_revalidation_sync_failure_is_uncertain(tmp_path,monkeypatch,replace_first):
    store=store_at(tmp_path/'authority');store.update(generation=8,rearm_required=False)
    expected=report(store.directory)['record_sha256'];old=store.path.read_bytes()
    def fail(path,value):
        if replace_first:atomic_json(path,value)
        raise OSError(errno.EIO,'injected fsync failure')
    lease=GatewayLease(tmp_path/'owner.lock').acquire()
    monkeypatch.setattr('xenova_gateway.authority.atomic_json',fail)
    try:
        with pytest.raises(AuthorityError,match='UNCERTAIN'):
            revalidate(store.directory,HOST,expected,lease,uid=os.geteuid(),mountpoint=None)
    finally:lease.close()
    value=json.loads(store.path.read_text())
    assert value['generation']==8
    if not replace_first:assert store.path.read_bytes()==old
    else:assert value['rearm_required']
