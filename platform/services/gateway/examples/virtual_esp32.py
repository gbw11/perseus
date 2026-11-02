"""Owned PTY contract device; reports simulated application, never physical truth.

The caller owns/closes both PTY descriptors. This is not ESP32 firmware. The
fixture timeout belongs to tests, not an approved vehicle setting.
"""
import os
import select
import threading
from uuid import uuid4
from xenova_gateway.feedback import (Framer,HELLO,HELLO_PAYLOAD,COMMAND,hello_ack,
    parse_command,packet,report,CAP_ACCEPT,CAP_APPLY,CAP_STATIONARY)
from xenova_gateway.frame import StopFrame


class VirtualESP32:
    def __init__(self,master,*,apply=True,measure=False,fragment=False):
        self.master=master;self.apply=apply;self.measure=measure;self.fragment=fragment
        self.session=str(uuid4());self.pi=None;self.sample=0;self.commands=[]
        self.stop=threading.Event();self.error=None
        self.thread=threading.Thread(target=self.run,name='fixture-esp32',daemon=True)

    def start(self):self.thread.start();return self

    def send(self,data):
        if self.fragment:
            for offset in range(0,len(data),3):os.write(self.master,data[offset:offset+3])
        else:os.write(self.master,data)

    def handshake(self):
        self.send(hello_ack(self.pi,self.session,CAP_ACCEPT|CAP_APPLY|(CAP_STATIONARY if self.measure else 0)))

    def reply(self,item,status=2,**fields):
        pi,device,sequence,frame=item;self.sample+=1
        self.send(report(pi,device,sequence,frame,status,self.sample,**fields))

    def reboot(self):
        self.session=str(uuid4());self.sample=0;self.handshake()

    def run(self):
        parser=Framer()
        try:
            while not self.stop.is_set():
                if not select.select([self.master],[],[],.01)[0]:continue
                for kind,payload in parser.feed(os.read(self.master,4096)):
                    if kind==HELLO:
                        _,pi=HELLO_PAYLOAD.unpack(payload)
                        self.pi=str(__import__('uuid').UUID(bytes=pi));self.handshake()
                    elif kind==COMMAND:
                        item=parse_command(packet(kind,payload));self.commands.append(item)
                        self.reply(item,1)
                        if self.apply:
                            self.reply(item,2,stationary=True if self.measure and isinstance(item[3],StopFrame) else None)
        except (OSError,ValueError) as error:
            if not self.stop.is_set():self.error=error

    def close(self):
        self.stop.set();self.thread.join(1)
        if self.thread.is_alive():raise RuntimeError('fixture device still running')
        if self.error:raise self.error
