"""Pi 로컬 xenova-drive CLI. 요청 원key/기한을 보존하며 transport timeout을 취소로 바꾸지 않는다."""
import argparse
import json
import os
from pathlib import Path
import socket
import struct
import time

from .client import trusted_endpoint,GatewayUnavailable
from .ipc_contract import HEADER,encode,decode,new_request_id
from .operator_contract import ENDPOINT,request,SAFETY


class OperatorClient:
    def __init__(self,endpoint=ENDPOINT,wait_ms=1000,uid=0):
        self.endpoint,self.wait_ms,self.uid=str(endpoint),wait_ms,uid

    def call(self,body):
        request(body);wire=encode(body);submitted=False
        deadline=time.monotonic()+max(.001,self.wait_ms/1000)
        def remaining():
            if time.monotonic()>=deadline:raise TimeoutError()
            return max(.000001,deadline-time.monotonic())
        try:
            trusted_endpoint(self.endpoint,self.uid)
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as stream:
                stream.settimeout(remaining());stream.connect(self.endpoint)
                if struct.unpack('3i',stream.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]!=self.uid:
                    raise ValueError('operator peer mismatch')
                submitted=True;stream.settimeout(remaining());stream.sendall(wire)
                def receive(n):
                    result=bytearray()
                    while len(result)<n:
                        stream.settimeout(remaining());part=stream.recv(n-len(result))
                        if not part:raise EOFError()
                        result.extend(part)
                    return bytes(result)
                size=HEADER.unpack(receive(4))[0]
                if not 0<size<=8192:raise ValueError('operator response size')
                value=decode(receive(size))
                if value.get('version')!=2 or type(value.get('ok')) is not bool:raise ValueError('operator version')
                return value
        except (OSError,EOFError,ValueError) as error:
            raise GatewayUnavailable('OUTCOME_UNKNOWN' if submitted else 'UNAVAILABLE') from error


class Attempts:
    """소유 CLI cache32KiB/16. 원장·재무장 권위가 아니며 미종결 항목은 지우지 않는다."""
    def __init__(self,path=Path('/var/lib/xenova-drive/attempts.json')):self.path=Path(path)

    def save(self,call):
        from xenova_mqtt.drive_binding import atomic_json,private_json
        self.path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        info=self.path.parent.stat()
        if info.st_uid!=os.geteuid() or info.st_mode&0o077:raise ValueError('private attempt directory required')
        rows=private_json(self.path,os.geteuid()) if self.path.exists() else []
        if not isinstance(rows,list):raise ValueError('invalid attempt cache')
        now=int(time.monotonic()*1000)
        rows=[row for row in rows if not row.get('terminal') or now<row['request']['deadline_pi_ms']+60000]
        if len(rows)>=16:raise ValueError('attempt cache full; query existing keys')
        rows=[*rows,dict(request=call,terminal=False)]
        if len(json.dumps(rows,separators=(',',':')).encode())>32768:raise ValueError('attempt bytes full')
        atomic_json(self.path,rows)

    def observe(self,key,response):
        if key is None or not self.path.exists():return
        from xenova_mqtt.drive_binding import atomic_json,private_json
        rows=private_json(self.path,os.geteuid())
        if not isinstance(rows,list) or len(rows)>16:raise ValueError('invalid attempt cache')
        terminal=bool(response.get('ok') and response.get('data',{}).get('terminal'))
        if not terminal:return
        for row in rows:
            if row['request']['request_id']==key:row['terminal']=True
        atomic_json(self.path,rows)


def main(argv=None):
    parser=argparse.ArgumentParser(prog='xenova-drive')
    parser.add_argument('--socket',default=ENDPOINT);parser.add_argument('--json',action='store_true')
    parser.add_argument('--wait-ms',type=int,default=1000)
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('status');commands.add_parser('stop')
    mode=commands.add_parser('mode');mode.add_argument('value',choices=('manual',))
    auto=commands.add_parser('auto');auto.add_argument('value',choices=('resume','cancel'))
    parking=commands.add_parser('parking');parking.add_argument('value',choices=('start','cancel'))
    parking.add_argument('--strategy',choices=('reverse','forward'),default='reverse')
    lookup=commands.add_parser('request');lookup.add_argument('value',choices=('get',));lookup.add_argument('key')
    manual=commands.add_parser('manual');manual.add_argument('value',choices=('nudge','neutral','keyboard'))
    manual.add_argument('direction',nargs='?',choices=('forward','backward'))
    manual.add_argument('--duration-ms',type=int,default=100);manual.add_argument('--steer',choices=('left','center','right'),default='center')
    manual.add_argument('--device',default='control-keyboard')
    scene=commands.add_parser('scene');scene_actions=scene.add_subparsers(dest='scene_kind',required=True)
    survey=scene_actions.add_parser('survey');survey.add_argument('value',choices=('begin','commit'))
    survey.add_argument('--policy',choices=('static-flat-survey-1',),default='static-flat-survey-1')
    survey.add_argument('--allow-mapper-restore',action='store_true');survey.add_argument('--id')
    revoke=scene_actions.add_parser('revoke');revoke.set_defaults(value='revoke');revoke.add_argument('--id')
    args=parser.parse_args(argv)
    if not 0<=args.wait_ms<=10000:parser.error('--wait-ms outside0..10000')
    if args.command=='manual' and args.value=='keyboard':
        from .keyboard_source import main as keyboard
        return keyboard(['--device',args.device,'--operator-socket',args.socket])
    client=OperatorClient(args.socket,args.wait_ms)
    try:
        if args.command=='status':body=dict(version=2,action='status')
        elif args.command=='request':body=dict(version=2,action='request.get',request_id=args.key)
        else:
            view=client.call(dict(version=2,action='status'))
            if not view['ok']:print(json.dumps(view));return 2
            current=view['data'];host=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            if current['host_boot_id']!=host:raise ValueError('CLI must run on the current Pi host')
            key=new_request_id(host);issued=int(key.split(':')[2]);parameters={}
            if args.command=='mode':action='mode.manual'
            elif args.command=='stop':action='stop'
            elif args.command=='auto':action='auto.'+args.value
            elif args.command=='parking':
                action='parking.'+args.value
                if args.value=='start':parameters=dict(strategy=args.strategy.upper())
            elif args.command=='manual':
                action='manual.'+args.value;driver=current['driver']
                if driver is None:raise ValueError('current driver unavailable')
                parameters=dict(driver_session=driver['session_id'])
                if args.value=='nudge':
                    if args.direction is None:parser.error('nudge requires direction')
                    parameters.update(expected_action_seq=driver['action_seq'],direction='FORWARD' if args.direction=='forward' else 'REVERSE',
                        steer=args.steer.upper(),duration_ms=args.duration_ms)
            else:
                action='scene.survey.'+args.value if args.value!='revoke' else 'scene.revoke'
                if args.value=='begin':parameters=dict(policy='STATIC_FLAT_SURVEY_1',allow_mapper_restore=args.allow_mapper_restore)
                else:
                    field='survey_id' if args.value=='commit' else 'admission_id'
                    target=args.id or (current['scene'] or {}).get(field)
                    if target is None:raise ValueError('original scene target unavailable')
                    parameters={'survey_id' if args.value=='commit' else 'scene_id':target}
            body=dict(version=2,action=action,request_id=key,host_boot_id=host,gateway_instance_id=current['gateway_instance_id'],
                expected_generation=current['generation'],expected_owner=current['owner'],deadline_pi_ms=issued+10000,args=parameters)
            request(body);print(key,flush=True)
            if action not in ('manual.nudge','manual.neutral'):
                try:Attempts().save(body)
                except (OSError,ValueError):
                    if action not in SAFETY:raise
        until=time.monotonic()+args.wait_ms/1000
        response=client.call(body)
        original=body.get('request_id')
        while (args.wait_ms and response.get('ok') and 'terminal' in response.get('data',{})
                and not response['data']['terminal'] and time.monotonic()<until):
            time.sleep(min(.05,max(0,until-time.monotonic())))
            client.wait_ms=max(1,int((until-time.monotonic())*1000))
            response=client.call(dict(version=2,action='request.get',request_id=original))
        try:Attempts().observe(original,response)
        except (OSError,ValueError):pass  # cache status cannot replace a known Gateway result
        print(json.dumps(response,ensure_ascii=False,separators=(',',':')))
        return 0 if response['ok'] else 3 if response['error']['outcome']=='UNKNOWN' else 2
    except (GatewayUnavailable,OSError,ValueError) as exc:
        print(json.dumps(dict(version=2,ok=False,error=dict(code=getattr(exc,'code','UNAVAILABLE'),request_id=locals().get('key'),
            outcome='UNKNOWN' if getattr(exc,'code',None)=='OUTCOME_UNKNOWN' else 'NOT_ACCEPTED',retryable=False))))
        return 3


if __name__=='__main__':raise SystemExit(main())
