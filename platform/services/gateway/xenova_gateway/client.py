"""유한한 단일 UDS 호출. timeout은 원 작업 취소나 미수락 증거가 아니다."""
import argparse
import json
import os
from pathlib import Path
import socket
import stat
import struct
import time

from .ipc_contract import ENDPOINT, HEADER, MAX_BYTES, VERSION, decode, encode, request, seconds


class GatewayUnavailable(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class GatewayRejected(RuntimeError):
    def __init__(self, response):
        super().__init__(response['error']['code'])
        self.code = response['error']['code']
        self.response = response


def trusted_endpoint(endpoint, uid):
    path = Path(endpoint)
    if not path.is_absolute() or path.parent.resolve() != path.parent:
        raise ValueError('canonical absolute endpoint required')
    parent = path.parent.lstat()
    info = path.lstat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != uid or parent.st_mode & 0o077 or
            not stat.S_ISSOCK(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077):
        raise ValueError('private owned endpoint required')


class GatewayClient:
    def __init__(self, endpoint=ENDPOINT, *, budget_s=1.0, uid=0):
        if not isinstance(endpoint, (str, Path)):
            raise ValueError('endpoint path required')
        self.endpoint = str(endpoint)
        self.budget_s = seconds(budget_s)
        if type(uid) is not int or uid < 0:
            raise ValueError('peer uid required')
        self.uid = uid

    def _call(self, action, request_id=None, spec=None):
        body = {'version': VERSION, 'action': action}
        if request_id is not None:
            body['request_id'] = request_id
        if spec is not None:
            body['spec'] = spec
        request(body)
        wire = encode(body)
        deadline = time.monotonic() + self.budget_s
        sent = False
        try:
            trusted_endpoint(self.endpoint, self.uid)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
                def remaining():
                    stream.settimeout(max(0.000001, deadline-time.monotonic()))
                    if time.monotonic() >= deadline:
                        raise TimeoutError()
                remaining(); stream.connect(self.endpoint)
                _, uid, _ = struct.unpack('3i', stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != self.uid:
                    raise ValueError('Gateway peer uid mismatch')
                # Any partial submission can have reached the provider. Do not
                # turn a failed send/receive into a new key or an implicit cancel.
                sent = True
                view = memoryview(wire)
                while view:
                    remaining(); count = stream.send(view)
                    if not count:
                        raise EOFError()
                    view = view[count:]
                def receive(size):
                    result = bytearray()
                    while len(result) < size:
                        remaining(); data = stream.recv(size-len(result))
                        if not data:
                            raise EOFError()
                        result.extend(data)
                    return bytes(result)
                size = HEADER.unpack(receive(4))[0]
                if not 0 < size <= MAX_BYTES:
                    raise ValueError('Gateway response size invalid')
                result = decode(receive(size))
                if type(result.get('version')) is not int or result['version'] != VERSION or type(result.get('ok')) is not bool:
                    raise ValueError('Gateway response invalid')
                if not result['ok']:
                    if not isinstance(result.get('error'),dict) or not isinstance(result['error'].get('code'),str):
                        raise ValueError('Gateway error envelope invalid')
                    raise GatewayRejected(result)
                if not isinstance(result.get('data'),dict):raise ValueError('Gateway result invalid')
                return result['data']
        except GatewayRejected:
            raise
        except (OSError, EOFError, ValueError) as exc:
            raise GatewayUnavailable('OUTCOME_UNKNOWN' if sent else 'UNAVAILABLE') from exc

    def context(self):
        return self._call('context')

    def submit(self, request_id, spec):
        return self._call('submit', request_id, spec)

    def get(self, request_id):
        return self._call('get', request_id)

    def commit(self, request_id):
        return self._call('commit', request_id)

    def cancel(self, request_id, *, context=None):
        return self._call('cancel', request_id, context)

    def finish(self, request_id, context):
        return self._call('finish', request_id, context)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('context', 'submit', 'get', 'commit', 'cancel', 'finish'))
    parser.add_argument('--request-id')
    parser.add_argument('--spec-file', type=Path)
    parser.add_argument('--endpoint', default=ENDPOINT)
    parser.add_argument('--budget-s', type=float, default=1.0)
    parser.add_argument('--peer-uid',type=int,default=0,help='expected local Gateway uid; production unit uses root')
    args = parser.parse_args(argv)
    try:
        spec = decode(args.spec_file.read_bytes()) if args.spec_file else None
        result = GatewayClient(args.endpoint,budget_s=args.budget_s,uid=args.peer_uid)._call(args.action,args.request_id,spec)
        print(json.dumps(result, allow_nan=False)); return 0
    except GatewayRejected as exc:
        print(json.dumps(exc.response)); return 2
    except (GatewayUnavailable, ValueError, OSError) as exc:
        print(json.dumps({'ok': False, 'error': {'code': getattr(exc, 'code', 'INVALID_INPUT')}})); return 3


if __name__ == '__main__':
    raise SystemExit(main())
