"""Explicit feedback-1 serial profile. Legacy command bytes are unchanged.

New frames are sent only after selecting this profile. A handshake echoes the Pi
session and declares a device boot session/capabilities; it never grants output.
"""
from collections import OrderedDict
import struct
import time
from uuid import UUID, uuid4
from .frame import cobs_encode,cobs_decode,crc16,encode as legacy_encode,decode as legacy_decode,StopFrame
from .sink import SerialSink,SinkError

HELLO=0x0f00
HELLO_ACK=0x0f01
COMMAND=0x0110
FEEDBACK=0x0f02
CAP_ACCEPT=1
CAP_APPLY=2
CAP_VELOCITY=4
CAP_STEERING=8
CAP_STATIONARY=16
HEADER=struct.Struct('<HB')
HELLO_PAYLOAD=struct.Struct('<B16s')
ACK_PAYLOAD=struct.Struct('<B16s16sH')
COMMAND_PREFIX=struct.Struct('<16s16sQ')
REPORT=struct.Struct('<16s16sQIBBQBiiB')
MAX_FRAME=256
STATUS={1:'ACCEPTED',2:'APPLIED',3:'REJECTED',4:'FAULT'}


class FeedbackError(ValueError):pass


def packet(kind,payload):
    if not 0<=len(payload)<=240:raise FeedbackError('serial payload too large')
    raw=HEADER.pack(kind,len(payload))+payload
    return cobs_encode(raw+struct.pack('<H',crc16(raw)))+b'\0'


def unpack(wire):
    if not wire.endswith(b'\0') or len(wire)>MAX_FRAME:raise FeedbackError('serial boundary/size')
    try:raw=cobs_decode(wire[:-1])
    except ValueError as exc:raise FeedbackError('serial COBS') from exc
    if len(raw)<5:raise FeedbackError('short serial frame')
    kind,length=HEADER.unpack_from(raw)
    if len(raw)!=length+5 or crc16(raw[:-2])!=struct.unpack('<H',raw[-2:])[0]:
        raise FeedbackError('serial length/CRC')
    return kind,raw[3:-2]


def hello(pi_session):return packet(HELLO,HELLO_PAYLOAD.pack(1,UUID(pi_session).bytes))


def hello_ack(pi_session,device_session,capabilities=CAP_ACCEPT|CAP_APPLY):
    return packet(HELLO_ACK,ACK_PAYLOAD.pack(1,UUID(pi_session).bytes,UUID(device_session).bytes,capabilities))


def command(pi_session,device_session,sequence,frame):
    return packet(COMMAND,COMMAND_PREFIX.pack(UUID(pi_session).bytes,UUID(device_session).bytes,sequence)+legacy_encode(frame))


def parse_command(wire):
    kind,payload=unpack(wire)
    if kind!=COMMAND or len(payload)<=COMMAND_PREFIX.size:raise FeedbackError('command profile')
    pi,device,sequence=COMMAND_PREFIX.unpack_from(payload)
    if not sequence:raise FeedbackError('zero command sequence')
    return str(UUID(bytes=pi)),str(UUID(bytes=device)),sequence,legacy_decode(payload[COMMAND_PREFIX.size:])


def report(pi_session,device_session,sequence,frame,status,sample_seq,*,velocity_mmps=None,steering_mrad=None,stationary=None):
    if status not in STATUS or not 1<=sample_seq<=2**64-1:raise FeedbackError('feedback status/sample')
    if stationary is not None and type(stationary) is not bool:raise FeedbackError('stationary type')
    for value in (velocity_mmps,steering_mrad):
        if value is not None and (type(value) is not int or not -2**31<=value<2**31):raise FeedbackError('measurement integer')
    flags=int(velocity_mmps is not None)|int(steering_mrad is not None)<<1|int(stationary is not None)<<2
    return packet(FEEDBACK,REPORT.pack(UUID(pi_session).bytes,UUID(device_session).bytes,sequence,
        frame.generation,frame.counter,status,sample_seq,flags,
        0 if velocity_mmps is None else velocity_mmps,0 if steering_mrad is None else steering_mrad,
        int(stationary) if stationary is not None else 0))


class Framer:
    def __init__(self):self.buffer=bytearray();self.discard=False;self.invalid=0;self.oversize=0
    def feed(self,data):
        completed=[]
        for byte in data:
            if byte==0:
                if self.discard:self.discard=False
                elif self.buffer:
                    try:completed.append(unpack(bytes(self.buffer)+b'\0'))
                    except FeedbackError:self.invalid+=1
                self.buffer.clear()
            elif not self.discard:
                if len(self.buffer)>=MAX_FRAME-1:
                    self.buffer.clear();self.discard=True;self.oversize+=1
                else:self.buffer.append(byte)
        return completed


class FeedbackSerialSink(SerialSink):
    """One serial handle, polled only by OutputOwner; no RX thread/second writer."""
    def __init__(self,port,baudrate=115200,*,timeout_ms,clock=time.monotonic):
        if type(timeout_ms) is not int or not 1<=timeout_ms<=2**31-1:
            raise ValueError('approved feedback timeout_ms required')
        super().__init__(port,baudrate)
        self.clock=clock;self.timeout=timeout_ms/1000
        self.pi_session=str(uuid4());self.device_session=None;self.capabilities=0
        self.started=None;self.ready=False;self.fault=None
        self.framer=Framer();self.pending=OrderedDict();self.sequence=0
        self.last_sample=0;self.last_report=None;self.latest_command=None
        self.rejected=0;self.received=0;self.duplicates=0
        self.events=__import__('queue').Queue(maxsize=64)
        self.snapshot=self._view()

    def _view(self):
        result=dict(feedback_supported=True,ready=self.ready,pi_session=self.pi_session,
                    device_session=self.device_session,capabilities=self.capabilities,fault=self.fault,
                    accepted=None,applied=None,physical_stop=None,reported_velocity_mps=None,
                    reported_steering_rad=None,observed_monotonic=None,command_generation=None,
                    command_counter=None,command_sequence=None,command_kind=None,
                    parser_invalid=self.framer.invalid,parser_oversize=self.framer.oversize,
                    stale_or_invalid=self.rejected,pending=len(self.pending))
        if self.last_report is not None:result.update(self.last_report)
        result['latest_command_sequence']=self.latest_command
        result['report_is_latest']=bool(self.last_report and result['command_sequence']==self.latest_command)
        if (self.fault or not result['report_is_latest'] or
                (result['observed_monotonic'] is not None and self.clock()-result['observed_monotonic']>self.timeout)):
            for key in ('accepted','applied','physical_stop','reported_velocity_mps','reported_steering_rad'):
                result[key]=None
        return result

    def _send(self,data):
        accepted=self._serial.write(data)
        if accepted!=len(data):raise SinkError('partial feedback-profile serial write')
        return accepted

    def write(self,frame,t):
        if not self.ready:raise SinkError('feedback device handshake not ready')
        if self.fault and not isinstance(frame,StopFrame):raise SinkError('feedback device fault')
        if self.sequence==2**64-1:raise SinkError('serial session sequence exhausted')
        if len(self.pending)>=32:
            # Preserve bounded reference history and a STOP slot. An unresolved
            # old command is explicitly failed, never relabelled as applied.
            if not isinstance(frame,StopFrame):raise SinkError('device acknowledgement window full')
            self.pending.popitem(last=False)
        self.sequence+=1
        wire=command(self.pi_session,self.device_session,self.sequence,frame)
        self._send(wire)
        self.pending[self.sequence]=dict(frame=frame,deadline=t+self.timeout,accepted=False,applied=False)
        self.latest_command=self.sequence
        self.snapshot=self._view()
        from .output_owner import SubmissionReceipt
        result=SubmissionReceipt(len(legacy_encode(frame)),frame)
        result.metadata=dict(wire_bytes=len(wire),wire_hex=wire.hex(),command_sequence=self.sequence,
                             pi_session=self.pi_session,device_session=self.device_session)
        return result

    def poll(self,now):
        if self.started is None:
            self.started=now;self._send(hello(self.pi_session))
        data=self._serial.read(min(4096,self._serial.in_waiting))
        for kind,payload in self.framer.feed(data):
            self.received+=1
            try:self._receive(kind,payload,now)
            except (FeedbackError,ValueError,struct.error):self.rejected+=1
        if not self.ready and now-self.started>=self.timeout:
            self.fault=self.fault or 'DEVICE_HANDSHAKE_TIMEOUT'
        for row in self.pending.values():
            if now>=row['deadline'] and not row['applied']:
                self.fault=self.fault or 'DEVICE_APPLY_TIMEOUT'
        self.snapshot=self._view()

    def _receive(self,kind,payload,now):
        if kind==HELLO_ACK:
            version,pi,device,caps=ACK_PAYLOAD.unpack(payload)
            device=str(UUID(bytes=device))
            if version!=1 or str(UUID(bytes=pi))!=self.pi_session or UUID(device).int==0:
                raise FeedbackError('handshake session mismatch')
            if caps&(CAP_ACCEPT|CAP_APPLY)!=(CAP_ACCEPT|CAP_APPLY):
                self.fault=self.fault or 'DEVICE_CAPABILITY_UNSUPPORTED';return
            if self.device_session is not None and device!=self.device_session:
                self.fault=self.fault or 'DEVICE_RESTARTED'
                self.pending.clear();self.last_report=None;self.last_sample=0
            self.device_session=device;self.capabilities=caps;self.ready=True
            return
        if kind!=FEEDBACK or not self.ready:raise FeedbackError('unexpected device frame')
        pi,device,sequence,generation,counter,status,sample,flags,velocity,steering,stationary=REPORT.unpack(payload)
        if str(UUID(bytes=pi))!=self.pi_session or str(UUID(bytes=device))!=self.device_session:
            raise FeedbackError('old serial session')
        if flags&~7 or stationary not in (0,1) or status not in STATUS or not sample:
            raise FeedbackError('feedback fields')
        if ((flags&1 and not self.capabilities&CAP_VELOCITY) or
                (flags&2 and not self.capabilities&CAP_STEERING) or
                (flags&4 and not self.capabilities&CAP_STATIONARY)):
            raise FeedbackError('unadvertised device measurement')
        row=self.pending.get(sequence)
        if (not row or row['frame'].generation!=generation or row['frame'].counter!=counter or now>=row['deadline']):
            raise FeedbackError('feedback not for a live command')
        if sample<=self.last_sample:
            self.duplicates+=1;return
        if status==1 and row['applied']:self.duplicates+=1;return
        self.last_sample=sample
        if status in (3,4):self.fault=self.fault or ('DEVICE_REJECTED' if status==3 else 'DEVICE_FAULT')
        if status in (1,2):row['accepted']=True
        if status==2:row['applied']=True
        evidence=dict(accepted=row['accepted'],applied=row['applied'],
            physical_stop=bool(stationary) if status==2 and flags&4 and isinstance(row['frame'],StopFrame) else None,
            reported_velocity_mps=velocity/1000 if flags&1 else None,
            reported_steering_rad=steering/1000 if flags&2 else None,observed_monotonic=now,
            command_generation=generation,command_counter=counter,command_sequence=sequence,
            command_kind='STOP' if isinstance(row['frame'],StopFrame) else 'DRIVE',sample_seq=sample,
            observation_source='device_report',pi_session=self.pi_session,device_session=self.device_session)
        evidence['status']=STATUS[status]
        if status==2:
            try:self.events.put_nowait(evidence)
            except __import__('queue').Full:self.fault=self.fault or 'DEVICE_EVIDENCE_OVERFLOW'
        # An old command's valid late report cannot replace the current device
        # view. It only updates that bounded reference's own completion.
        if sequence==self.latest_command:
            self.last_report=evidence
        while len(self.pending)>16:
            first=next(iter(self.pending))
            if not self.pending[first]['applied']:break
            self.pending.pop(first)
