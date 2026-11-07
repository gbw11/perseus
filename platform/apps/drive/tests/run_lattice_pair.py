"""Two production native firmwares, two brokers and a serial physics fixture.

Only root/Store identity, mapper input and the ESP32 are fixtures. The world
decoder, lattice, swept collision checks, tracker, state adoption, workers,
Gateway actor, Authority and serial codec are the product implementations.
Requires root in a NEW network namespace; never connects to an operating broker.
"""
import argparse
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import pty
import secrets
import subprocess
import sys
import threading
import time
from uuid import UUID,uuid4

ROOT=Path(__file__).resolve().parents[4]
sys.path[:0]=[str(ROOT/p) for p in ('platform/services/gateway','platform/services/vm-agent/mqtt-client',
    'platform/apps/drive/world/python','platform/apps/drive/examples')]
from xenova_drive_world import schema,encode,decode
from xenova_gateway.authority import AuthorityStore,GatewayLease
from xenova_gateway.ipc import GatewayServer
from xenova_gateway.client import new_request_id,GatewayRejected
from xenova_gateway.history import HistoryPolicy
from xenova_gateway.verified_runner import VerifiedGatewayRunner
from xenova_gateway.drive_link import DriveLinkIO,DriveLinkProvider
from xenova_gateway.odometry import VehicleOdometry,OdometryConfig,PROBE,SAMPLE,ODOM_PROBE,ODOM_REPORT,CAP_ODOMETRY
from xenova_gateway.feedback import (FeedbackSerialSink,Framer,HELLO,HELLO_PAYLOAD,COMMAND,COMMAND_PREFIX,
    hello_ack,packet,report,CAP_ACCEPT,CAP_APPLY,CAP_VELOCITY,CAP_STEERING,CAP_STATIONARY)
from xenova_gateway.frame import DriveFrame,decode as command_decode
from xenova_gateway.output_guard import VehicleLimits,LimitedSink
from xenova_gateway.output_owner import OutputOwner
from xenova_mqtt import Identity,XenovaClient,topics
from xenova_mqtt.drive_binding import BindingCache,atomic_json
import paho.mqtt.client as mqtt
from world_fixture import world,BOOT,INSTANCE,MAP,FRAME,CLOCK


class Vehicle:
    """Independent rear-axle bicycle physics driven ONLY by actual serial bytes."""
    def __init__(self,master,stop):
        self.fd=master;self.stop=stop;os.set_blocking(master,False)
        self.device=str(uuid4());self.x=.3;self.y=1.6;self.yaw=0.;self.v=0.;self.steer=0.
        self.target=(0.,0.);self.left=0.;self.right=0.;self.last_command=0.;self.sample=0
        self.commands=0;self.drives=0;self.trace=[];self.lock=threading.Lock()
        self.next_trace=0.;self._last_advance=None;self._last_odom_us=None
    def _advance(self):
        """Advance the physical state to this actual observation, including idle time."""
        now=time.monotonic();dt=0. if self._last_advance is None else now-self._last_advance
        self._last_advance=now
        target=self.target if now-self.last_command<.15 else (0.,self.steer)
        self.v+=max(-dt,min(dt,target[0]-self.v));self.steer+=max(-1.5*dt,min(1.5*dt,target[1]-self.steer))
        ds=self.v*dt;turn=ds*math.tan(self.steer)/.25
        self.x+=ds*math.cos(self.yaw+turn/2);self.y+=ds*math.sin(self.yaw+turn/2);self.yaw+=turn
        self.left+=(ds-turn*.1)/.00005;self.right+=(ds+turn*.1)/.00005
        if now>=self.next_trace and len(self.trace)<40000:
            self.trace.append((now,self.x,self.y,self.yaw,self.v,self.steer));self.next_trace=now+.01
        return now
    def run(self):
        parser=Framer();self._last_advance=time.monotonic()
        while not self.stop.wait(.002):
            self._advance()
            try:data=os.read(self.fd,4096)
            except BlockingIOError:continue
            for kind,payload in parser.frames(data):
                if kind==HELLO:
                    version,pi=HELLO_PAYLOAD.unpack(payload)
                    os.write(self.fd,hello_ack(str(UUID(bytes=pi)),self.device,
                        CAP_ACCEPT|CAP_APPLY|CAP_VELOCITY|CAP_STEERING|CAP_STATIONARY|CAP_ODOMETRY,version=version))
                elif kind==COMMAND:
                    pi,device,sequence=COMMAND_PREFIX.unpack_from(payload);frame=command_decode(payload[COMMAND_PREFIX.size:])
                    assert str(UUID(bytes=device))==self.device
                    now=self._advance()
                    self.commands+=1;self.last_command=now
                    if isinstance(frame,DriveFrame):self.target=(frame.v_mps,frame.steer_rad);self.drives+=int(frame.v_mps>0)
                    else:self.target=(0.,self.steer)
                    self.sample+=1
                    os.write(self.fd,report(str(UUID(bytes=pi)),self.device,sequence,frame,2,self.sample,
                        velocity_mmps=round(self.v*1000),steering_mrad=round(self.steer*1000),stationary=abs(self.v)<.001))
                elif kind==ODOM_PROBE:
                    pi,device,echo=PROBE.unpack(payload);now=self._advance()
                    # The fixture's device clock deliberately has another origin.
                    source_us=int(now*1e6)+7000000
                    # A coarse clock cannot manufacture a new encoder sample.
                    if self._last_odom_us is not None and source_us<=self._last_odom_us:continue
                    self._last_odom_us=source_us;self.sample+=1
                    os.write(self.fd,packet(ODOM_REPORT,SAMPLE.pack(pi,device,echo,source_us,
                        self.sample,round(self.left),round(self.right),5,round(self.steer*1000),5)))


def audit_trace(trace):
    """Independent physical OBB/rectangle SAT, never asks the planner for truth."""
    obstacle=((1.9,1.3),(2.5,1.3),(2.5,1.9),(1.9,1.9))
    minimum=math.inf
    for k,(_,x,y,yaw,velocity,steer) in enumerate(trace):
        c=math.cos(yaw);s=math.sin(yaw)
        dt=trace[k][0]-trace[k-1][0] if k else 0.
        pad=.5*(.5+.5/.25*math.tan(.55)*math.hypot(.3,.1))*dt
        body=tuple((x+u*c-v*s,y+u*s+v*c) for u,v in
            ((-.1-pad,-.1-pad),(.3+pad,-.1-pad),(.3+pad,.1+pad),(-.1-pad,.1+pad)))
        assert all(.2-1e-6<=py<=3.+1e-6 for px,py in body),('road departure',x,y,yaw)
        separation=[]
        for ax,ay in ((1.,0.),(0.,1.),(c,s),(-s,c)):
            a=[px*ax+py*ay for px,py in body];b=[px*ax+py*ay for px,py in obstacle]
            separation.append(max(min(b)-max(a),min(a)-max(b)))
        assert max(separation)>0,('physical obstacle collision',x,y,yaw)
        minimum=min(minimum,max(separation))
        assert -.251<=velocity<=.501 and abs(steer)<=.551
    return dict(samples=len(trace),minimum_separating_projection_m=minimum)


def _scenario(args):
    logging.basicConfig(level=logging.WARNING)
    args.output.mkdir(mode=0o700);private=args.output/'private';private.mkdir(mode=0o700)
    env=dict(os.environ,LD_LIBRARY_PATH=str(args.tools_root/'usr/lib/x86_64-linux-gnu'),PYTHONDONTWRITEBYTECODE='1')
    children=[];handles=[];threads=[];clients=[];errors=[];stop=threading.Event();guest_stop={};records=[];stages=[]
    initial_threads={t.ident for t in threading.enumerate()};initial_fds=len(list(Path('/proc/self/fd').iterdir()))
    runner=None;link=None;sink=None;passed=False;bindings=None;ipc=None;lease=None
    host=str(uuid4());vms={n:str(uuid4()) for n in ('a','b')};sessions={};world_enabled=threading.Event();world_enabled.set()
    current_vehicle=[None];manual_state=[[],1];driver=str(uuid4());world_count=[0]
    passwords={n:secrets.token_urlsafe(24) for n in ('a','b','map-a','map-b','telemetry-a','telemetry-b','gateway','fixture','mapper')}
    app_paths={};app_bootstraps={};guest_processes={};online={n:True for n in vms};execution_losses=[];support_evidence=None
    def write(name,value):
        path=private/name
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as f:f.write(value if isinstance(value,str) else json.dumps(value))
        return path
    def spawn(name,argv,settings=env):
        out=(args.output/(name+'.stdout')).open('wb');err=(args.output/(name+'.stderr')).open('wb');handles.extend((out,err))
        proc=subprocess.Popen(list(map(str,argv)),env=settings,stdout=out,stderr=err);children.append(proc);return proc
    def thread(fn):
        def body():
            try:fn()
            except BaseException as e:errors.append(repr(e));stop.set()
        t=threading.Thread(target=body,daemon=True);t.start();threads.append(t);return t
    def until(name,fn,timeout=25):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            if errors:raise RuntimeError(errors[0])
            if fn():
                stages.append(dict(name=name,t=time.monotonic()))
                (args.output/'stages.json').write_text(json.dumps(stages))
                return
            time.sleep(.01)
        raise TimeoutError(name)
    def run(argv):subprocess.run(list(map(str,argv)),env=env,check=True,capture_output=True,timeout=10)
    def call(action,key=None,spec=None):
        argv=[sys.executable,'-B','-m','xenova_gateway.client',action,'--endpoint',str(private/'ipc/control.sock')]
        if key:argv+=['--request-id',key]
        if spec is not None:argv+=['--spec-file',str(write('call-'+str(uuid4())+'.json',spec))]
        result=subprocess.run(argv,env=dict(env,PYTHONPATH=str(ROOT/'platform/services/gateway')+':'+env.get('PYTHONPATH','')),
            capture_output=True,text=True,timeout=3)
        value=json.loads(result.stdout)
        if result.returncode==2:raise GatewayRejected(value)
        if result.returncode:raise RuntimeError(('G1',action,value))
        return value
    def handoff(label,mode='AUTO',*,hold=False):
        key=new_request_id(host);vm=vms[label];e=sessions[label]
        spec=dict(purpose='handoff',mode=mode,host_boot_id=host,target_vm=vm,target_execution_id=e['execution_id'],
            intent_revision=1,lifecycle_generation=0,expected_owner=runner.machine.active_vm,
            expected_generation=runner.machine.generation,deadline_ms=int((time.monotonic()+8)*1000))
        if hold:spec['manual_hold']=True
        row=call('submit',key,spec)
        committed=None
        def conditional_commit():
            nonlocal committed
            try:committed=call('commit',key)
            except GatewayRejected as error:
                if (error.code=='TRANSITION_REJECTED' and
                    error.response['error'].get('reason')=='transition not prepared'):return False
                raise
            return committed['phase']=='COMMITTED'
        # submit/get replay is read-only. The explicit commit conditionally
        # refreshes preparation in the actor using the SAME key/deadline.
        until('g1-real-conditional-commit-'+label+'-'+mode,conditional_commit,5)
        until('g1-real-app-applied-'+label+'-'+mode,lambda:call('get',key)['app_applied'],5)
        assert runner.machine.active_vm==vm and runner.machine.mode.value==mode
        assert call('submit',key,spec)['committed_generation']==committed['committed_generation']
        return key
    def start_guest(n):
        i=('a','b').index(n);vm=vms[n]
        entry=dict(vm_id=vm,boot_id=str(uuid4()),execution_id=str(uuid4()),drive_instance=str(uuid4()),
            app_instances={a:str(uuid4()) for a in ('autonomous','manual')});sessions[n]=entry
        common=dict(vm_id=vm,boot_id=entry['boot_id'],host_boot_id=host,execution_id=entry['execution_id'],
            broker_ipv4='192.0.2.1',trusted_internal_plaintext=True)
        suffix=n+'-'+entry['execution_id']
        app_bootstraps[n]=dict(common,publisher_id=entry['drive_instance'],receipt_id=str(uuid4()),port=18884,
            username=vm,password=passwords[n],drive_profile='warm-drive-1',auto_instance_id=entry['app_instances']['autonomous'],
            manual_instance_id=entry['app_instances']['manual'],world_broker_ipv4='192.0.2.1',world_port=18885,
            world_username=vm,world_password=passwords['map-'+n],world_profile='lattice-lab-1')
        app=app_paths[n]=write(suffix+'-drive.json',app_bootstraps[n])
        telemetry=write(suffix+'-telemetry.json',dict(common,publisher_id=str(uuid4()),receipt_id=str(uuid4()),port=18883,
            username='telemetry-'+vm,password=passwords['telemetry-'+n]))
        guest_stop[n]=private/(suffix+'.stop')
        child_env=dict(env,XT_TEST_BOOTSTRAP=str(telemetry),XD_TEST_APP_BOOTSTRAP=str(app),XT_TEST_INSTANCE_ID=vm,
            XD_TEST_DURATION_MS='240000',XD_TEST_STOP_FILE=str(guest_stop[n]))
        guest_processes[n]=spawn('firmware-'+suffix,[args.binary,'-rt','-no-color',f'-eth-if=xdtap{i}',
            f'-mac-addr=02:00:5e:00:53:0{i+2}',f'-ipv4-addr=192.0.2.{i+2}','-ipv4-gw=192.0.2.1','-ipv4-nm=255.255.255.0'],child_env)
        online[n]=True
    try:
        for argv in (['ip','link','set','lo','up'],['ip','link','add','xdbr','type','bridge'],
            ['ip','address','add','192.0.2.1/24','dev','xdbr'],['ip','link','set','xdbr','up']):run(argv)
        for i in range(2):
            for argv in (['ip','tuntap','add','dev','xdtap'+str(i),'mode','tap'],['ip','link','set','xdtap'+str(i),'master','xdbr'],
                ['ip','link','set','xdtap'+str(i),'up']):run(argv)
        accounts={vms[n]:passwords[n] for n in vms}
        accounts.update({'telemetry-'+vms[n]:passwords['telemetry-'+n] for n in vms})
        accounts.update({n:passwords[n] for n in ('gateway','fixture','mapper')})
        passwd=write('passwords',''.join(n+':'+pw+'\n' for n,pw in accounts.items()))
        run([args.tools_root/'usr/bin/mosquitto_passwd','-U',passwd])
        acl='user fixture\ntopic read xenova/v1/#\ntopic write xenova/v1/apps/manual/input\ntopic write xenova/v1/console/resume\n'
        acl+='user mapper\ntopic read xenova/v1/vehicle/state.pb\ntopic write xenova/v1/world/snapshot.pb\n'
        acl+='user gateway\ntopic read xenova/v1/#\ntopic write xenova/v1/vehicle/+\ntopic write xenova/v1/apps/vehicle/events\n'
        for vm in vms.values():
            acl+=f'topic write xenova/v1/vms/{vm}/drive/link/in\n'
        for vm in vms.values():
            acl+=(f'user {vm}\ntopic write xenova/v1/vms/{vm}/apps/+/events\ntopic write xenova/v1/vms/{vm}/drive/link/out\n'
                f'topic read xenova/v1/vms/{vm}/drive/link/in\ntopic read xenova/v1/vehicle/control\ntopic read xenova/v1/vehicle/state.pb\n'
                'topic read xenova/v1/apps/manual/input\ntopic read xenova/v1/world/snapshot.pb\n'
                f'user telemetry-{vm}\ntopic write xenova/v2/vms/{vm}/telemetry/+\n')
        aclpath=write('acl',acl)
        map_passwd=write('map-passwords',''.join(vms[n]+':'+passwords['map-'+n]+'\n' for n in vms)+
            'mapper:'+passwords['mapper']+'\n')
        run([args.tools_root/'usr/bin/mosquitto_passwd','-U',map_passwd])
        map_acl=write('map-acl','user mapper\ntopic write xenova/v1/world/snapshot.pb\n'+
            ''.join('user '+vm+'\ntopic read xenova/v1/world/snapshot.pb\n' for vm in vms.values()))
        for name,ports in (('pi',(18883,18884)),('jetson',(18885,))):
            selected_passwd,selected_acl=(passwd,aclpath) if name=='pi' else (map_passwd,map_acl)
            conf=write(name+'.conf',''.join(f'listener {port} 192.0.2.1\n' for port in ports)+
                f'user root\nallow_anonymous false\npassword_file {selected_passwd}\nacl_file {selected_acl}\npersistence false\nset_tcp_nodelay true\nlog_type all\n')
            proc=spawn(name,[args.tools_root/'usr/sbin/mosquitto','-c',conf]);time.sleep(.2);assert proc.poll() is None
        def raw(name,port,on_message=None,subscribe=None):
            c=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,client_id=name+'-'+str(uuid4()))
            c.username_pw_set(name,passwords[name]);c.on_message=on_message
            if subscribe:c.on_connect=lambda c,u,f,r,props:c.subscribe(subscribe,qos=0)
            c.connect('192.0.2.1',port);c.loop_start();clients.append(c);return c
        def observed(c,u,msg):
            if msg.topic.endswith('/state.pb'):current_vehicle[0]=decode(bytes(msg.payload),schema.VehicleSample)
            elif not msg.topic.endswith(('/in','/out')):
                value=json.loads(msg.payload)
                if len(records)<40000:records.append(dict(t=time.monotonic(),topic=msg.topic,message=value))
        raw('fixture',18884,observed,'xenova/v1/#');mapper=raw('mapper',18885)
        sender=XenovaClient(Identity(source_id='jetson'),host='192.0.2.1',port=18884,username='fixture',password=passwords['fixture'])
        assert sender.start(wait_s=3);clients.append(sender)
        gw=XenovaClient(Identity(source_id='dom0-gateway'),host='192.0.2.1',port=18884,username='gateway',password=passwords['gateway'])
        clients.append(gw)
        master,slave=pty.openpty();device=Vehicle(master,stop);thread(device.run)
        odom=VehicleOdometry(OdometryConfig('rc-test-1','encoder-lab-1',.00005,.2,.25,1.,20000,200000,20,64,
            .003,.003,.001,.001),host)
        serial=FeedbackSerialSink(os.ttyname(slave),timeout_ms=500,odometry=odom)
        sink=OutputOwner(LimitedSink(serial,VehicleLimits(-.25,.5,-.55,.55,2.,3.),time.monotonic()),command_timeout_s=.1)
        authority=AuthorityStore(private/'authority',host,mountpoint=None,history_policy=HistoryPolicy(10000,1000))
        authority.initialize(vms['a']);authority.update(mode='AUTO',reason='isolated_test_preference',manual_input_profile='manual-session-1')
        bindings=BindingCache(host)
        runner=VerifiedGatewayRunner(gw,sink,authority=authority,bindings=bindings,vm_ids=tuple(vms.values()),readiness_profile='warm-drive-1')
        lease=GatewayLease(private/'gateway.lock').acquire();ipc=GatewayServer(runner,lease,private/'ipc/control.sock').start()
        template=world(height=64 if args.maximum else 32);trust=schema.WorldTrust(vehicle='rc-test-1',mapper_boot=BOOT,mapper_instance=INSTANCE,map_epoch=MAP,
            frame_epoch=FRAME,odom_epoch=str(uuid4()),configuration='lattice-lab-1',calibration='encoder-lab-1',
            primitive=template.primitive,clock_epoch=CLOCK,lower_offset_us=-9001000,upper_offset_us=-8999000,
            expires_pi_us=int((time.monotonic()+240)*1e6),max_error_us=2000)
        link=DriveLinkIO('192.0.2.1',18884,'gateway',passwords['gateway'],tuple(vms.values()))
        runner.drive_link=DriveLinkProvider(runner,link,trust,gw.identity.agent_instance_id);link.start()
        for n in ('a','b'):start_guest(n)
        def bind():
            while not stop.wait(.05):
                now=int(time.monotonic()*1000);rows={}
                for n,e in sessions.items():
                    if not online[n]:continue
                    rows[e['vm_id']]=dict(status='observed',execution_id=e['execution_id'],boot_id=e['boot_id'],
                        app_instance_id=e['drive_instance'],intent_revision=1,lifecycle_generation=0,domid=1 if n=='a' else 2,
                        xen_unique_id='1',observed_monotonic_ms=now,profile='warm-drive-1',app_instances=e['app_instances'],
                        app_state_models={'autonomous':'stateful','manual':'stateless'})
                bindings.install(dict(schema_version='1.0-drive-bindings',host_boot_id=host,observed_monotonic_ms=now,
                    expires_monotonic_ms=now+3000,vms=rows))
        thread(bind)
        def inputs():
            sample=0
            while not stop.wait(.04):
                sample+=1;generation=runner.machine.generation if runner.machine else authority.value['generation']
                sender.publish(topics.manual_input(),'drive.input',dict(keys=manual_state[0],profile='manual-session-1',
                    session_id=driver,sample_seq=str(sample),action_seq=str(manual_state[1]),generation=generation,valid=True,age_ms=0))
        thread(inputs)
        def maps():
            while not stop.wait(.1):
                vehicle=current_vehicle[0]
                if not world_enabled.is_set() or vehicle is None:continue
                w=world(height=64 if args.maximum else 32);world_count[0]+=1;now=int(time.monotonic()*1e6)+9000000
                w.sequence=world_count[0];w.observation_seq=w.sequence;w.odom_epoch=vehicle.odom_epoch
                w.reference_odom_seq=vehicle.sequence;w.observed_us=now;w.completed_us=now;w.published_us=now
                w.reference_vehicle_pose.CopyFrom(schema.Pose(x=.3+vehicle.odom_pose.x,y=1.6+vehicle.odom_pose.y,yaw=vehicle.odom_pose.yaw))
                # A central rectangular obstacle forces a real nonconstant path.
                cells=bytearray(w.occupancy)
                for y in range(13,19):
                    for x in range(19,25):cells[y*w.width+x]=100
                w.occupancy=bytes(cells)
                mapper.publish('xenova/v1/world/snapshot.pb',encode(w),qos=0,retain=False)
        thread(maps);thread(lambda:runner.run(stop))
        until('real-input-and-both-firmwares-ready',lambda:runner.machine and
            runner.machine.node_status(vms['a'],time.monotonic())=='ready_disarmed' and
            runner.machine.node_status(vms['b'],time.monotonic())=='standby',35)
        assert not runner.machine.output_permitted and device.drives==0
        until('causation-context',lambda:runner._control_message_id is not None)
        sender.publish('xenova/v1/console/resume','drive.resume.requested',dict(requested_by='isolated-fixture',
            expected_generation=runner.machine.generation),causation_id=runner._control_message_id)
        until('actual-serial-command-moves-vehicle',lambda:device.x>.8 and device.drives>5,15)
        until('full-state-applied-to-standby',lambda:runner.machine.states.evidence(vms['b'],'autonomous') is not None,12)
        handoff('b')
        until('actual-bypass',lambda:abs(device.y-1.6)>.35,20)
        until('actual-rejoin',lambda:device.x>3.1 and abs(device.y-1.6)<.15 and abs(device.yaw)<.22,25)
        handoff('a')
        if args.scenario!='basic':
            world_enabled.clear()
            manual_state[:]=[['up'],manual_state[1]+1]
            until('map-outage-selects-manual',lambda:runner.machine.mode.value=='MANUAL',6)
            until('map-outage-actual-stationary-report',lambda:abs(device.v)<.001 and
                runner._manual_activation is not None and runner._manual_activation['ready'],6)
            # Held keys from AUTO do not become a new MANUAL action.
            generation=runner.machine.generation;before=device.x
            time.sleep(.25);assert abs(device.x-before)<.005 and runner.machine.generation==generation
            if not runner.machine.output_permitted:
                until('manual-qualified-for-explicit-rearm',lambda:runner.machine._qualified(
                    runner.machine.active_vm,'manual',time.monotonic(),held=True),6)
                sender.publish('xenova/v1/console/resume','drive.resume.requested',dict(requested_by='isolated-fixture',
                    expected_generation=runner.machine.generation),causation_id=runner._control_message_id)
                until('manual-explicit-rearm-after-no-ready-standby',lambda:runner.machine.output_permitted,8)
            manual_state[:]=[[],manual_state[1]+1];time.sleep(.2)
            until('standby-manual-qualified-before-held-key-test',lambda:runner.machine._qualified(
                vms['b'],'manual',time.monotonic(),held=True),6)
            manual_state[:]=[['up'],manual_state[1]+1]
            until('manual-progress-without-jetson',lambda:device.v>.05,5)
            handoff('b','MANUAL',hold=True)
            until('same-driver-manual-b-progress',lambda:device.v>.05 and any(s.get('v_mps',0)>.05 and
                s.get('serial_result')=='accepted' and (s.get('source') or {}).get('vm_id')==vms['b'] and
                (s.get('source') or {}).get('app_id')=='manual' for s in runner.submissions),4)
            manual_state[:]=[[],manual_state[1]+1]
            until('manual-neutral-before-auto-rejoin',lambda:abs(device.v)<.001,5)
            # Root-owned app-instance replacement exercises real cancel/join,
            # planner restart and state reapplication while MANUAL keeps going.
            old=sessions['a']['app_instances']['autonomous'];fresh=str(uuid4())
            app_bootstraps['a']['auto_instance_id']=fresh
            atomic_json(app_paths['a'],app_bootstraps['a'])
            sessions['a']['app_instances']={**sessions['a']['app_instances'],'autonomous':fresh}
            world_enabled.set()
            until('new-auto-instance-real-rejoin',lambda:runner.machine._qualified(vms['a'],'autonomous',time.monotonic(),held=True),15)
            assert fresh!=old and sessions['a']['app_instances']['manual']==app_bootstraps['a']['manual_instance_id']
            time.sleep(3.2)
            assert runner.machine.mode.value=='MANUAL' and authority.value['manual_hold']
            manual_state[:]=[[],manual_state[1]+1]
            until('manual-neutral-stops',lambda:abs(device.v)<.001,5)
            sender.publish('xenova/v1/console/resume','drive.resume.requested',dict(requested_by='isolated-fixture',
                expected_generation=runner.machine.generation),causation_id=runner._control_message_id)
            until('explicit-return-to-auto',lambda:runner.machine.mode.value=='AUTO' and runner.machine.output_permitted,8)
            handoff('a')
        if args.scenario=='repeat':
            for lost,other in (('a','b'),('b','a')):
                if runner.machine.active_vm!=vms[lost]:handoff(lost)
                until('fresh-standby-before-loss-'+lost,lambda:runner.machine._qualified(
                    vms[other],'autonomous',time.monotonic(),held=True),8)
                previous=sessions[lost]['execution_id'];online[lost]=False
                guest_processes[lost].kill();guest_processes[lost].wait(timeout=3)
                execution_losses.append(dict(vm=vms[lost],execution=previous,returncode=guest_processes[lost].returncode))
                until('execution-loss-real-successor-'+lost,lambda:runner.machine.active_vm==vms[other] and
                    runner.machine.mode.value=='AUTO' and runner.machine.output_permitted,6)
                start_guest(lost)
                until('fresh-execution-real-standby-'+lost,lambda:runner.machine._qualified(
                    vms[lost],'autonomous',time.monotonic(),held=True),18)
                assert sessions[lost]['execution_id']!=previous and runner.machine.active_vm==vms[other]
            # Keep a real neutral MANUAL computation selected during history
            # cycling. Maximum maps still arrive and AUTO stays available, but
            # a mapper freshness fault must not be confused with a history
            # operation issuing a generation. No synthetic readiness or STOP.
            handoff('a','MANUAL',hold=True)
            until('neutral-manual-before-history',lambda:runner.machine.output_permitted and
                runner._manual_activation is not None and runner._manual_activation['ready'] and abs(device.v)<.001,5)
            # Same live actor/Authority/device after the entire mode/rejoin
            # sequence. No new empty state or renewed retry window per round.
            first=None;generation=runner.machine.generation
            for index in range(40):
                began=time.monotonic();target='b' if runner.machine.active_vm==vms['a'] else 'a'
                key=new_request_id(host);e=sessions[target]
                spec=dict(host_boot_id=host,target_vm=vms[target],target_execution_id=e['execution_id'],
                    intent_revision=1,lifecycle_generation=0,expected_owner=runner.machine.active_vm,
                    expected_generation=runner.machine.generation,deadline_ms=int((began+2)*1000))
                protected=call('submit',key,spec);assert protected['phase']=='PROTECTED' and protected['protection']['held']
                assert call('submit',key,{**spec,'purpose':'protect'})['request_id']==key
                ended=call('cancel',key,dict(host_boot_id=host,expected_generation=runner.machine.generation,
                    target_execution_id=e['execution_id']));assert ended['phase']=='CANCELLED'
                if first is None:first=key,spec
                assert runner.machine.generation==generation and runner.machine.output_permitted
                assert call('history')['serialized_bytes']<65536
                stop.wait(max(0,1.-(time.monotonic()-began)))
            try:call('get',first[0])
            except GatewayRejected as error:assert error.code=='RESULT_NOT_RETAINED_OR_UNKNOWN'
            else:raise AssertionError('terminal history not collected after its original promise')
            try:call('submit',first[0],{**first[1],'deadline_ms':int((time.monotonic()+2)*1000)})
            except GatewayRejected as error:assert error.code=='REQUEST_WINDOW_EXPIRED'
            else:raise AssertionError('collected request resurrected')
            standby='b' if runner.machine.active_vm==vms['a'] else 'a'
            until('normal-standby-after-forty-history-rounds',lambda:runner.machine._qualified(
                vms[standby],'autonomous',time.monotonic(),held=True),8)
            stages.append(dict(name='same-authority-forty-normal-rounds-and-old-key-fence',t=time.monotonic()))
        if args.support_delay:
            # Test-only delays at the real durability and diagnostic sinks.
            # The independent output owner must expire the original command
            # while both producers are still blocked; no output TTL is raised.
            from xenova_gateway import authority as authority_module
            from xenova_gateway.diagnostics import GatewayDiagnostics
            until('actual-drive-before-support-delay',lambda:isinstance(sink.last,DriveFrame) and
                sink.last.v_mps>.05 and sink.expiry is not None and sink.expiry>time.monotonic()+.02,5)
            entered=threading.Event();release=threading.Event();log_entered=threading.Event();log_release=threading.Event()
            class SlowDiagnostic(logging.Handler):
                def emit(self,record):log_entered.set();assert log_release.wait(2)
            logger=logging.getLogger('owned-support-delay');logger.handlers=[SlowDiagnostic()];logger.propagate=False
            diagnostic=GatewayDiagnostics(logger);original=authority_module.atomic_json;delayed=[False];reply=[]
            target='b' if runner.machine.active_vm==vms['a'] else 'a';entry=sessions[target];key=new_request_id(host)
            spec=dict(host_boot_id=host,target_vm=vms[target],target_execution_id=entry['execution_id'],
                intent_revision=1,lifecycle_generation=0,expected_owner=runner.machine.active_vm,
                expected_generation=runner.machine.generation,deadline_ms=int((time.monotonic()+2)*1000))
            def held_save(path,value):
                if not delayed[0] and any(row['request_id']==key for row in value.get('protections',[])):
                    delayed[0]=True;entered.set();assert release.wait(2)
                return original(path,value)
            authority_module.atomic_json=held_save;watchdog=sink.watchdog_stops;submitted=runner.serial_accepted
            pending=thread(lambda:reply.append(call('submit',key,spec)))
            log_producer=thread(lambda:logger.critical('owned rare defect diagnostic; original cause retained'))
            try:
                until('authority-and-critical-sink-blocked',lambda:entered.is_set() and log_entered.is_set(),1)
                until('real-expiry-stop-before-support-release',lambda:sink.watchdog_stops>watchdog and device.target[0]==0,.5)
                assert pending.is_alive() and log_producer.is_alive()
                support_evidence=dict(watchdog_stops=sink.watchdog_stops-watchdog,
                    original_diagnostic_preserved='original cause retained' in diagnostic.last_critical,
                    maximum_map_stream=args.maximum)
            finally:
                release.set();log_release.set();pending.join(3);log_producer.join(3)
                authority_module.atomic_json=original;diagnostic.close();logger.handlers=[]
            assert len(reply)==1 and reply[0]['phase']=='PROTECTED' and not pending.is_alive() and not log_producer.is_alive()
            current=call('context')
            call('cancel',key,dict(host_boot_id=host,expected_generation=current['generation'],target_execution_id=entry['execution_id']))
            if not runner.machine.output_permitted:
                app='autonomous' if runner.machine.mode.value=='AUTO' else 'manual'
                until('qualified-after-support-delay',lambda:runner.machine._qualified(
                    runner.machine.active_vm,app,time.monotonic(),held=True),6)
                sender.publish('xenova/v1/console/resume','drive.resume.requested',dict(requested_by='isolated-fixture',
                    expected_generation=runner.machine.generation),causation_id=runner._control_message_id)
            until('normal-current-output-after-support-delay',lambda:runner.machine.output_permitted and runner.serial_accepted>submitted+5,8)
        assert runner.machine.output_permitted and runner.machine.failure is None and runner.serial_failed==0
        passed=True
    finally:
        failure=sys.exc_info()[1]
        if runner and runner.machine:
            m=runner.machine;at=time.monotonic()
            (args.output/'live-at-end.json').write_text(json.dumps(dict(
                at=at,phase=m.phase,failure=m.failure,output_permitted=m.output_permitted,
                qualification={str(k):v for k,v in m.qualification.items()},
                state_applications={str(k):v for k,v in m.states.applications.items()},
                state_rejected=m.states.rejected,drive=runner.drive_link.status()),indent=2))
        for path in guest_stop.values():path.touch()
        # Cooperative normal firmware cleanup is checked separately from the
        # harness's emergency process termination after a failed assertion.
        for child in children[2:]:
            try:child.wait(timeout=5)
            except subprocess.TimeoutExpired:child.terminate()
        if runner and runner.machine:
            summary=dict(mode=runner.machine.mode.value,owner=runner.machine.active_vm,generation=runner.machine.generation,
                nodes={n:runner.machine.node_status(vm,time.monotonic()) for n,vm in vms.items()},
                drive=runner.drive_link.status(),device=runner.device_view(),submissions=list(runner.submissions))
        else:summary={}
        stop.set()
        for t in threads:t.join(timeout=3)
        cleanup_errors=[]
        for owner in (link,ipc,lease):
            if owner:
                try:owner.close()
                except BaseException as error:cleanup_errors.append(repr(error)[:300])
        for c in clients:
            if isinstance(c,XenovaClient):c.stop()
            else:c.disconnect();c.loop_stop()
        for child in children:
            if child.poll() is None:child.terminate()
            try:child.wait(timeout=2)
            except subprocess.TimeoutExpired:child.kill();child.wait()
        for handle in handles:handle.close()
        trace=device.trace if 'device' in locals() else []
        if 'master' in locals():os.close(master);os.close(slave)
        # loop_stop keeps Paho's reusable wakeup pair on the client object.
        # Measure complete object lifetimes, after joining their owners.
        clients.clear();c=gw=sender=mapper=runner=ipc=link=sink=None
        import gc
        gc.collect()
        # The independent full-trace geometry audit is offline. Running it on
        # this Python process's GIL while the 10ms device receipt owner is live
        # would inject the measurement's CPU cost into the tested controller.
        trace_audit=None
        if passed:
            try:trace_audit=audit_trace(trace)
            except AssertionError as error:passed=False;failure=failure or error
        result=dict(passed=passed,error=repr(failure) if failure else None,thread_errors=errors,stages=stages,
            binary_sha256=hashlib.sha256(args.binary.read_bytes()).hexdigest(),summary=summary,
            physical_fixture=dict(trace=trace,commands=device.commands if 'device' in locals() else 0),
            trace_audit=trace_audit if passed else None,
            intentional_execution_losses=execution_losses,
            support_delay=support_evidence,
            world_published=world_count[0],child_returncodes=[c.returncode for c in children])
        (args.output/'result.json').write_text(json.dumps(result,indent=2));(args.output/'messages.json').write_text(json.dumps(records))
        residual_threads=[t.name for t in threading.enumerate() if t.ident not in initial_threads]
        remaining_fds=len(list(Path('/proc/self/fd').iterdir()))
        expected_killed={row['execution'] for row in execution_losses}
        unexpected_children=[c.returncode for c in children[2:] if c.returncode not in (0,-9)]
        # Only explicitly injected execution loss may skip cooperative cleanup.
        if sum(c.returncode==-9 for c in children[2:])!=len(expected_killed):unexpected_children.append('unexpected SIGKILL')
        resource_result=dict(initial_fds=initial_fds,final_fds=remaining_fds,residual_threads=residual_threads,
            cleanup_errors=cleanup_errors,unexpected_child_results=unexpected_children)
        (args.output/'resources.json').write_text(json.dumps(resource_result,indent=2))
        if errors or residual_threads or cleanup_errors or unexpected_children:
            passed=False;result['passed']=False;result['cleanup']=resource_result
            if result['error'] is None:result['error']='owned fixture/runtime cleanup incomplete'
            (args.output/'result.json').write_text(json.dumps(result,indent=2))
    return 0 if passed else 1


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,required=True)
    p.add_argument('--tools-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--scenario',choices=('basic','modes','repeat'),default='repeat')
    p.add_argument('--maximum',action='store_true',help='use the supported 64x64 input bound')
    p.add_argument('--support-delay',action='store_true',help='block owned Authority/critical diagnostic sinks and check expiry STOP before release')
    args=p.parse_args()
    if os.geteuid() or os.readlink('/proc/self/ns/net')==os.readlink('/proc/1/ns/net'):p.error('new root network namespace required')
    if args.output.exists():p.error('new output directory required')
    initial=len(list(Path('/proc/self/fd').iterdir()))
    # End the complete scenario frame before measuring client destruction.
    # Keeping its local machine/callback graph alive is still object ownership.
    try:
        rc=_scenario(args)
    finally:
        import gc
        gc.collect()
        final=len(list(Path('/proc/self/fd').iterdir()))
        resource=json.loads((args.output/'resources.json').read_text())
        resource.update(initial_scope_fds=initial,after_object_release_fds=final)
        (args.output/'resources.json').write_text(json.dumps(resource,indent=2))
    if final!=initial:
        result=json.loads((args.output/'result.json').read_text());result['passed']=False
        if result['error'] is None:result['error']='FD lifetime did not return after all owners/clients ended'
        result['cleanup']=resource;(args.output/'result.json').write_text(json.dumps(result,indent=2));rc=1
    return rc

if __name__=='__main__':raise SystemExit(main())
