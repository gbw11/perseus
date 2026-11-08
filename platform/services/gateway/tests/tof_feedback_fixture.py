"""ToF software bench: 실제 FD/framer/feedback3 clock+ACK, 명시 가상 MCU/시간.

새 ToF 경로만 검증하며 미설치 pyserial의 기존5 PTY 시험이나 MCU/물리 수락을 대체하지 않는다.
"""
import socket
import struct
from types import SimpleNamespace
from uuid import UUID,uuid4
import zlib

from xenova_drive_world import schema,encode
from xenova_drive_world.mapper_clock import ClockPolicy
from xenova_gateway.feedback import Framer,packet,HELLO,HELLO_ACK
from xenova_gateway.feedback3 import (Feedback3SerialSink,Feedback3Profile,ACK3,CLOCK_PROBE,CLOCK_REPLY,
    CLOCK_INSTALL,CLOCK_ACK,PROBE3,CLOCK3,INSTALL3,CLOCK_ACK3,SENSOR,SENSOR3,REQUIRED_CAPS)
from xenova_gateway.odometry import OdometryConfig,VehicleOdometry
from xenova_gateway.scene_authority import StationaryPolicy
from xenova_gateway.tof_feedback3 import TOF_FRAGMENT,TOF_FRAGMENT3,TOF_COMMON3,TofSelection
from xenova_gateway.mapper_uplink import UplinkProfile,GatewayMapperUplink,_Link
from xenova_gateway.verified_runner import VerifiedGatewayRunner


HOST='00000000-0000-4000-8000-000000000001'
GATEWAY='00000000-0000-4000-8000-000000000004'
DEVICE='00000000-0000-4000-8000-000000000002'


def selection(profiles=((0,'c'*64,4),(1,'d'*64,5))):
    return TofSelection(profiles,500000,20000,'rc-test-1',HOST,GATEWAY)


def fragments(pi,device,epoch,clock_sequence,*,sensor=0,sample=1,stamp=1000000,generation=1,instance=None,
              calibration=None,revision=None,error=1000,flags=7,zone_data=None):
    instance=UUID(int=100+sensor).bytes if instance is None else instance
    calibration=('c' if sensor==0 else 'd')*64 if calibration is None else calibration
    revision=(4 if sensor==0 else 5) if revision is None else revision
    zones=b''.join(struct.pack('<HBB',600+i,1,3) for i in range(64)) if zone_data is None else zone_data
    fields=(pi,device,sample,stamp,error,epoch,clock_sequence,sensor,instance,generation,revision,bytes.fromhex(calibration),8,8)
    body=TOF_COMMON3.pack(*fields,flags)+zones;assert len(body)==393
    crc=zlib.crc32(body)&0xffffffff
    return tuple(TOF_FRAGMENT3.pack(*fields,index,4,index*16,16,flags,crc,zones[index*64:index*64+64]) for index in range(4)),body


class WireBench:
    def __init__(self,*,profiles=((0,'c'*64,4),(1,'d'*64,5)),capabilities=REQUIRED_CAPS|0x200):
        self.now=1.;self.a,self.b=socket.socketpair(socket.AF_UNIX,socket.SOCK_STREAM)
        self.a.setblocking(False);self.b.setblocking(False);self.framer=Framer();self.clock_latch=0
        cfg=OdometryConfig('rc-test-1','encoder-tof-bench',.001,.2,.25,2.,20000,500000,20,32,
            .005,.01,.02,.02,metres_per_tick_upper=.001)
        physical=StationaryPolicy(10000,100000,500000,.001,.05,.1,0.,0.)
        policy=Feedback3Profile('a'*64,'b'*64,5,10,20000,1000000,200000,100,100,physical,10000000,.02,20000)
        self.sink=Feedback3SerialSink('development-fd',development_fd=self.a.fileno(),timeout_ms=1000,
            clock=lambda:self.now,odometry=VehicleOdometry(cfg,HOST),profile=policy)
        self.sink.configure_tof(selection(profiles));self.profiles=profiles
        self.sink.poll(self.now);assert self.outgoing()[0][0]==HELLO
        self.send(HELLO_ACK,ACK3.pack(3,UUID(self.sink.pi_session).bytes,UUID(DEVICE).bytes,capabilities,
            bytes.fromhex('a'*64),bytes.fromhex('b'*64),32))
        self.probe=self.outgoing()[0]

    def outgoing(self):
        result=[]
        while True:
            try:raw=self.b.recv(4096)
            except BlockingIOError:break
            if not raw:break
            result.extend(self.framer.feed(raw))
        return result

    def send(self,kind,payload,*,advance=.001):
        self.now+=advance;self.b.sendall(packet(kind,payload));self.sink.poll(self.now)

    def install_clock(self):
        for number in (1,2):
            kind,payload=self.probe;assert kind==CLOCK_PROBE
            pi,device,seq,p1=PROBE3.unpack(payload)
            d2=p1-100000+100;self.clock_latch=d2+100;d3=d2+200
            self.send(CLOCK_REPLY,CLOCK3.pack(pi,device,seq,p1,d2,self.clock_latch,d3,number,100+number,100+number,1,0,0))
            if number==1:
                self.now+=.201;self.sink.poll(self.now);self.probe=self.outgoing()[0]
        rows=self.outgoing();install=next(raw for kind,raw in rows if kind==CLOCK_INSTALL)
        pi,device,epoch,sequence,probe,reference,lower,upper,expires,cutoff=INSTALL3.unpack(install)
        self.send(CLOCK_ACK,CLOCK_ACK3.pack(pi,device,epoch,sequence,probe,1,0,self.clock_latch+1000,cutoff))
        self.outgoing();assert self.sink._lease(int(self.now*1e6)) is not None
        self.now+=.02
        self.send(SENSOR,SENSOR3.pack(pi,device,3,int(self.now*1e6)-100000,103,103,1,0,0,epoch,sequence),advance=0.)
        assert self.sink.snapshot['vehicle_sample'] is not None
        self.epoch=epoch;self.clock_sequence=sequence
        return self

    def frame(self,**kwargs):
        stamp=int(self.now*1e6)-102000
        return fragments(UUID(self.sink.pi_session).bytes,UUID(DEVICE).bytes,
            UUID(self.sink.clock_epoch).bytes,getattr(self,'clock_sequence',1),stamp=stamp,**kwargs)

    def feed_fragments(self,parts):
        self.b.sendall(b''.join(packet(TOF_FRAGMENT,payload) for payload in parts));self.now+=.001;self.sink.poll(self.now)

    def actor(self):
        client=SimpleNamespace(identity=SimpleNamespace(agent_instance_id=GATEWAY))
        facade=SimpleNamespace(device=self.sink,device_view=lambda:self.sink.snapshot)
        runner=VerifiedGatewayRunner(client,facade,authority=SimpleNamespace(host=HOST,value={}),bindings=None,
            vm_ids=('A','B'),clock=lambda:self.now)
        profile=UplinkProfile(ClockPolicy('rc-test-1','oak-1','fixture-config',self.sink.odometry.config.calibration,'f'*64,
            5,10,20000,1000000,500000,1000000),'127.0.0.1',1883,None,None,self.profiles,500000)
        uplink=GatewayMapperUplink(profile,HOST,GATEWAY,clock=lambda:int(self.now*1e6));runner.mapper_uplink=uplink
        runner.configure_mapper_tof(profile)
        value=schema.VehicleSample(major=1,**{**self.sink.snapshot['vehicle_sample'],'gateway_instance':GATEWAY})
        uplink.owner.set_vehicle(encode(value),int(self.now*1e6))
        return runner,uplink

    def close(self):
        self.sink.close();self.a.close();self.b.close()
