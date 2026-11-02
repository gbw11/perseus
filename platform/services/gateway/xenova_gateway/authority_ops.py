"""Offline, lease-protected authority diagnosis and current-original revalidation.

No broker, output device, VM operation, backup restoration or generation issue.
A checksum binds an operator's inspection to a record; it does not prove that a
backup is latest. Missing/corrupt originals remain blocked even with a backup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path

from xenova_mqtt.drive_binding import private_json,canonical
from .authority import AuthorityError, AuthorityStore, GatewayLease, GATEWAY_LOCK


def fingerprint(path, uid):
    """Hash bounded private bytes without opening devices, FIFOs or links."""
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=uid or info.st_mode&0o077 or info.st_size>65536:
            raise AuthorityError('private bounded regular record required')
        data=stream.read(65537)
        if len(data)>65536:raise AuthorityError('authority grew beyond limit')
    return hashlib.sha256(data).hexdigest()


def inspect(directory, host, *, uid=0, mountpoint=Path('/srv/xenova'), candidate=None):
    """Read-only local evidence. VALID is not device/output/rearm permission."""
    directory=Path(directory)
    result={'schema_version':'1.0-drive-authority-inspection','status':'BLOCKED',
            'output_permitted':False,'restoration_supported':False}
    try:
        canonical(host)
        AuthorityStore.check_storage(directory,mountpoint)
        try:info=directory.lstat()
        except FileNotFoundError:
            if candidate is not None:return {**result,'reason':'CURRENT_ORIGINAL_MISSING'}
            return {**result,'status':'FIRST_INSTALL','reason':'DIRECTORY_ABSENT'}
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=uid or info.st_mode&0o077:
            raise AuthorityError('private owned authority directory required')
        if next(directory.iterdir(),None) is None:
            if candidate is not None:return {**result,'reason':'CURRENT_ORIGINAL_MISSING'}
            return {**result,'status':'FIRST_INSTALL','reason':'EMPTY_DIRECTORY'}
        store=AuthorityStore(directory,host,uid=uid,mountpoint=mountpoint)
        before=fingerprint(store.path,uid);value=store.load()
        if fingerprint(store.path,uid)!=before:raise AuthorityError('authority changed during inspection')
        result.update(status='VALID',reason='CURRENT_ORIGINAL',record_sha256=before,
            generation=value['generation'],owner=value['owner'],mode=value['mode'],phase=value['phase'],
            rearm_required=value['rearm_required'],request_count=len(value['requests']),
            transition_count=len(value.get('transitions',[])))
        if candidate is not None:
            fingerprint(Path(candidate),uid)
            backup=private_json(Path(candidate),uid);store.validate(backup)
            result['candidate_matches_current']=(backup==value)
            result['candidate_is_freshness_proof']=False
    except (AuthorityError,OSError,ValueError,KeyError,TypeError):
        result.update(status='BLOCKED',reason='EVIDENCE_UNAVAILABLE_OR_INVALID')
    return result


def revalidate(directory,host,expected_sha256,lease,*,uid=0,mountpoint=Path('/srv/xenova')):
    """Fsync the same valid original, preserving all IDs/results and disarming.

    Caller holds the same lease as the Gateway. Only cooperating writers are
    serialized; an out-of-band root file replacement is not a supported writer.
    Failure is uncertain, not permission to retry with another generation/key.
    """
    if lease.fd is None:raise AuthorityError('exclusive Gateway lease required')
    locked=os.fstat(lease.fd);current=lease.path.lstat()
    if (locked.st_dev,locked.st_ino)!=(current.st_dev,current.st_ino):
        raise AuthorityError('Gateway lease replaced')
    report=inspect(directory,host,uid=uid,mountpoint=mountpoint)
    if report['status']!='VALID' or report['record_sha256']!=expected_sha256:
        raise AuthorityError('valid unchanged current original required; backup cannot authorize recovery')
    store=AuthorityStore(Path(directory),host,uid=uid,mountpoint=mountpoint)
    value=store.load()
    if fingerprint(store.path,uid)!=expected_sha256:raise AuthorityError('authority changed after inspection')
    store.save({**value,'rearm_required':True})
    if not store.verify():raise AuthorityError('authority changed after revalidation')
    return {'schema_version':'1.0-drive-authority-revalidation','status':'REVALIDATED',
            'generation':value['generation'],'output_permitted':False,'rearm_required':True,
            'record_sha256':fingerprint(store.path,uid)}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('inspect','revalidate'))
    parser.add_argument('--authority-dir',type=Path,default=Path('/srv/xenova/drive-control'))
    parser.add_argument('--candidate',type=Path,help='inspect only; never a restoration source')
    parser.add_argument('--expected-sha256')
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args(argv)
    if os.geteuid()!=0:parser.error('run on dom0 as root')
    if args.action=='inspect' and (args.execute or args.expected_sha256):parser.error('inspection is read-only')
    if args.action=='revalidate' and (not args.execute or not args.expected_sha256 or args.candidate):
        parser.error('revalidation requires --execute and --expected-sha256; no backup input')
    try:
        host=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        if args.action=='inspect':result=inspect(args.authority_dir,host,candidate=args.candidate)
        else:
            lease=GatewayLease(GATEWAY_LOCK).acquire()
            try:result=revalidate(args.authority_dir,host,args.expected_sha256,lease)
            finally:lease.close()
        print(json.dumps(result,separators=(',',':')))
        return 0 if result['status'] in ('VALID','FIRST_INSTALL','REVALIDATED') else 2
    except (AuthorityError,OSError,ValueError) as exc:
        print(json.dumps({'status':'BLOCKED','reason':str(exc),'output_permitted':False}));return 2


if __name__=='__main__':raise SystemExit(main())
