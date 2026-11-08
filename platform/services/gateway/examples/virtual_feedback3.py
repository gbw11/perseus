"""명시 PTY 시험 대역. 실제 ESP32 펌웨어·센서 수락이나 실차 동역학 증거가 아니다."""
import os
import select
import threading
import time
from uuid import UUID,uuid4

from xenova_gateway.feedback import Framer,packet,HELLO,HELLO_ACK
from xenova_gateway.feedback3 import (HELLO3,ACK3,PROBE3,CLOCK3,INSTALL3,CLOCK_ACK3,SENSOR3,
    CLOCK_PROBE,CLOCK_REPLY,CLOCK_INSTALL,CLOCK_ACK,COMMAND,REPORT,SENSOR,REQUIRED_CAPS)
from xenova_gateway.scene_authority import COMMAND3,FEEDBACK3


class VirtualFeedback3:
    def __init__(self,fd,configuration,firmware,*,metres_per_tick=.000001,drop_first_feedback=False):
        self.fd=fd;self.configuration=configuration;self.firmware=firmware;self.scale=metres_per_tick
        self.device=uuid4().bytes;self.pi=None;self.install=None;self.current=None;self.sequence=0;self.report=0
        self.ticks=0.;self.velocity=0;self.steering=0;self.previous=time.monotonic();self.last_sensor=0
        self.drop_first_feedback=drop_first_feedback;self.dropped=False;self.commands=[]
        self.stop=threading.Event();self.thread=None;self.failure=None;self.framer=Framer()

    def start(self):
        self.thread=threading.Thread(target=self._run,name='test-feedback3-peer',daemon=False);self.thread.start();return self

    def send(self,kind,payload):
        raw=packet(kind,payload);at=0
        while at<len(raw):at+=os.write(self.fd,raw[at:])

    def latch(self,now):
        self.ticks+=self.velocity/1000*(now-self.previous)/self.scale;self.previous=now;self.sequence+=1
        return self.sequence,int(now*1e6),round(self.ticks),round(self.ticks)

    def feedback(self,sample,stamp):
        if self.current is None:return
        if self.drop_first_feedback and not self.dropped:self.dropped=True;return
        c=self.current;self.report+=1
        self.send(REPORT,FEEDBACK3.pack(c[0],c[1],c[2],c[8],c[9],c[10],2,self.report,sample,stamp,7,
            self.velocity,self.steering,int(self.velocity==0),c[3],c[4],c[7],c[11],0))

    def receive(self,kind,raw,now):
        if kind==HELLO:
            version,pi,configuration=HELLO3.unpack(raw)
            if version!=3 or configuration.hex()!=self.configuration:raise ValueError('fake peer configuration')
            self.pi=pi;self.send(HELLO_ACK,ACK3.pack(3,pi,self.device,REQUIRED_CAPS|0x408,configuration,
                bytes.fromhex(self.firmware),32));return
        if kind==CLOCK_PROBE:
            pi,device,seq,p1=PROBE3.unpack(raw)
            if (pi,device)!=(self.pi,self.device):raise ValueError('fake clock source')
            sample,stamp,left,right=self.latch(now)
            self.send(CLOCK_REPLY,CLOCK3.pack(pi,device,seq,p1,stamp,stamp,int(time.monotonic()*1e6),
                sample,left,right,3,self.steering,0));return
        if kind==CLOCK_INSTALL:
            row=INSTALL3.unpack(raw)
            if row[:2]!=(self.pi,self.device):raise ValueError('fake install source')
            self.install=row
            self.send(CLOCK_ACK,CLOCK_ACK3.pack(*row[:5],1,0,int(now*1e6),row[9]));return
        if kind!=COMMAND:raise ValueError('unsupported fake fragment/profile')
        c=COMMAND3.unpack(raw)
        if c[:2]!=(self.pi,self.device):raise ValueError('fake command source')
        if c[10]!=2:
            lease=self.install
            if lease is None or (c[3],c[4])!=(lease[2],lease[3]) or not int(now*1e6)<c[7]<=lease[9]:
                raise ValueError('fake command cutoff')
        self.current=c;self.commands.append(c);self.velocity=c[12];self.steering=c[13]
        sample,stamp,_,_=self.latch(now);self.feedback(sample,stamp)

    def _run(self):
        try:
            while not self.stop.is_set():
                now=time.monotonic()
                if select.select([self.fd],[],[],.003)[0]:
                    data=os.read(self.fd,4096)
                    for kind,raw in self.framer.frames(data):self.receive(kind,raw,time.monotonic())
                now=time.monotonic()
                if self.current is not None and self.current[10]!=2 and int(now*1e6)>=self.current[7]:self.velocity=0
                if self.pi is not None and self.install is not None and now-self.last_sensor>=.02:
                    self.last_sensor=now;sample,stamp,left,right=self.latch(now);lease=self.install
                    if stamp<lease[9]:
                        self.send(SENSOR,SENSOR3.pack(self.pi,self.device,sample,stamp,left,right,3,self.steering,0,lease[2],lease[3]))
                        self.feedback(sample,stamp)
        except BaseException as error:self.failure=error

    def close(self):
        self.stop.set()
        if self.thread is not None:self.thread.join(1)
        if self.thread is not None and self.thread.is_alive():raise RuntimeError('fake peer termination unconfirmed')
        if self.failure is not None:raise RuntimeError('fake peer failed') from self.failure
