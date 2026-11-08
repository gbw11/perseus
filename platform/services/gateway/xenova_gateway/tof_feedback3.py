"""정본 TOF_FRAGMENT3의 bounded RX handoff와 actor 조립·원 시각 mapping.

serial owner는 fixed209B만 queue8에 넘긴다. actor가 센서별 partial/latest393B를
소유한다. encoder/명령/clock을 기다리거나 재설정하지 않으며 새 thread는 없다.
관측의 TARGET_VALID는 FREE 허가가 아니다. native reducer/보정·coverage는 MCU/mapper 책임이다.
"""
from dataclasses import dataclass
from collections import Counter
import queue
import struct
from uuid import UUID
import zlib

from xenova_drive_world import schema,encode
from xenova_drive_world.mapper_clock import drift_pad


TOF_FRAGMENT=0x0f16
TOF_CAPABILITY=0x0200
TOF_FRAGMENT3=struct.Struct('<16s16sQQI16sQB16sII32sBBBBBBHI64s')
TOF_COMMON3=struct.Struct('<16s16sQQI16sQB16sII32sBBH')
REASSEMBLY_US=100000
NIL=bytes(16)
U63=(1<<63)-1


@dataclass(frozen=True)
class TofSelection:
    profiles: tuple
    maximum_age_us: int
    maximum_clock_error_us: int
    vehicle: str
    host_boot: str
    gateway_instance: str

    def __post_init__(self):
        if type(self.profiles) is not tuple or len(self.profiles)>2:raise ValueError('TOF profile count')
        ids=set()
        for row in self.profiles:
            if (type(row) is not tuple or len(row)!=3 or type(row[0]) is not int or row[0] not in (0,1)
                    or row[0] in ids or type(row[1]) is not str or len(row[1])!=64
                    or any(c not in '0123456789abcdef' for c in row[1])
                    or type(row[2]) is not int or not 0<row[2]<1<<32):raise ValueError('TOF selected calibration')
            ids.add(row[0])
        if (type(self.maximum_age_us) is not int or not 0<self.maximum_age_us<=1000000
                or type(self.maximum_clock_error_us) is not int or not 0<self.maximum_clock_error_us<=2**31-1):
            raise ValueError('TOF original time bounds')
        if type(self.vehicle) is not str or not 1<=len(self.vehicle)<=64:raise ValueError('TOF vehicle')
        for value in (self.host_boot,self.gateway_instance):
            if str(UUID(value))!=value:raise ValueError('TOF source UUID')


@dataclass(frozen=True)
class TofClock:
    epoch: bytes
    sequence: int
    lower: int
    upper: int
    reference_pi_us: int
    expires_pi_us: int
    drift_ppm: int


@dataclass(frozen=True)
class FragmentReceipt:
    payload: bytes
    received_pi_us: int
    clock: TofClock


class TofInbox:
    """RX1 producer/actor1 consumer. loss snapshot은 원 source fence2개뿐이다."""
    def __init__(self):
        self.inputs=queue.Queue(maxsize=8);self.loss=(0,((0,None),(0,None)));self.counts=Counter()

    @staticmethod
    def source(payload):
        if type(payload) is not bytes or len(payload)!=TOF_FRAGMENT3.size:return None
        sensor=payload[76]
        if sensor not in (0,1):return None
        sample=struct.unpack_from('<Q',payload,32)[0];generation=struct.unpack_from('<I',payload,93)[0]
        return sensor,(payload[16:32],generation,payload[77:93],sample)

    @staticmethod
    def merge_fence(old,new):
        if old is None or old[0]!=new[0] or new[1]>old[1]:return new
        if new[1]<old[1]:return old
        # NIL here is an internal conflict tombstone, never a sensor UUID or
        # outgoing observation. That generation cannot choose either claimant.
        instance=old[2] if old[2]==new[2] else NIL
        return old[0],old[1],instance,max(old[3],new[3])

    def reject(self,payload=None,reason='invalid'):
        source=self.source(payload);revision,fences=self.loss;values=list(fences)
        if source is None:values=[(revision+1,row[1]) for row in values]
        else:
            fence=self.merge_fence(values[source[0]][1],source[1])
            values[source[0]]=(revision+1,fence)
        self.loss=(revision+1,tuple(values));self.counts[reason]+=1
        if source is None:
            # An outer-frame/source loss has no trustworthy sensor key. Retire
            # every already queued original sample as well as actor buffers.
            for _ in range(8):
                try:old=self.inputs.get_nowait()
                except queue.Empty:break
                self.reject(old.payload,reason)

    def offer(self,payload,received_pi_us,clock):
        if type(payload) is not bytes or len(payload)!=TOF_FRAGMENT3.size or type(clock) is not TofClock:
            self.reject(payload);return False
        try:self.inputs.put_nowait(FragmentReceipt(payload,received_pi_us,clock))
        except queue.Full:
            # A saturated low-priority stream cannot reserve the serial owner.
            # Drop the whole queued batch and fence every affected source.
            for _ in range(8):
                try:old=self.inputs.get_nowait()
                except queue.Empty:break
                self.reject(old.payload,'overflow')
            self.reject(payload,'overflow');return False
        return True

    def drain(self):
        before=self.loss;items=[]
        for _ in range(8):
            try:items.append(self.inputs.get_nowait())
            except queue.Empty:break
        after=self.loss
        if before!=after:
            # Actor-local loss snapshot also retires the batch taken during
            # a producer loss. Do not mutate the producer's loss slot here.
            values=list(after[1])
            for item in items:
                source=self.source(item.payload)
                if source is not None:
                    values[source[0]]=(after[0],self.merge_fence(values[source[0]][1],source[1]))
            return (after[0],tuple(values)),()
        return after,tuple(items)


class TofAssembler:
    """Gateway actor-only. raw partial/latest payload <=393B each/sensor, <=1572B total."""
    def __init__(self,selection):
        if type(selection) is not TofSelection:raise ValueError('TOF installed selection')
        self.selection=selection;self.scope=None;self.loss_revision=0;self.loss_seen=[0,0]
        self.sensors={row[0]:dict(profile=row,identity=None,blocked_generation=0,sample=0,stamp=0,
            partial=None,latest=None,fence=None) for row in selection.profiles}
        self.counts=Counter();self.revoked=set()

    def _clear(self,sensor,*,sample=0,latest=False,identity=None):
        state=self.sensors[sensor];state['partial']=None
        identity=state['identity'] if identity is None else identity
        if identity is not None and sample>0:
            state['fence']=TofInbox.merge_fence(state['fence'],(*identity,sample))
        if latest:state['latest']=None
        self.revoked.add(sensor)

    def _loss(self,loss):
        revision,fences=loss
        if revision==self.loss_revision:return
        self.loss_revision=revision
        for sensor,state in self.sensors.items():
            version,fence=fences[sensor]
            if version==self.loss_seen[sensor]:continue
            self.loss_seen[sensor]=version
            sample=state['sample'] if fence is None else fence[3]
            self._clear(sensor,sample=sample,latest=True,identity=None if fence is None else fence[:3])

    def _context(self,context):
        # Published by the one serial owner; no live mutable clock dict is read.
        if context is None or not context[3]:
            for sensor,state in self.sensors.items():self._clear(sensor,sample=state['sample'],latest=True)
            return False
        scope=context[:3]
        if self.scope!=scope:
            first=self.scope is None
            self.scope=scope
            for sensor,state in self.sensors.items():
                fence=state['fence'] if first and state['fence'] is not None and state['fence'][0]==scope[1] else None
                self._clear(sensor,latest=True);state.update(identity=None,blocked_generation=0,sample=0,stamp=0,fence=fence)
        return True

    def process(self,loss,items,context,now):
        """현재 source 손실/원E를 먼저 확인한다. 반환 raw는 arrival로 시각을 바꾸지 않는다."""
        self.revoked.clear();self._loss(loss)
        current=self._context(context)
        for sensor,state in self.sensors.items():
            partial=state['partial']
            if partial is not None and now>=partial['deadline']:
                self._clear(sensor,sample=state['sample']);self.counts['timeout']+=1
            latest=state['latest']
            if latest is not None and now>=latest.get('valid_until',1<<63):
                state['latest']=None;self.revoked.add(sensor);self.counts['expired']+=1
        if current:
            for item in items:
                try:self._fragment(item,now)
                except (ValueError,struct.error):self.counts['rejected']+=1
        else:
            # Loss after RX admission also retires that original batch. A new
            # ACK/source observation cannot lend its validity to queued bytes.
            for item in items:
                source=TofInbox.source(item.payload)
                if source is not None and source[0] in self.sensors:
                    self._clear(source[0],sample=source[1][3],latest=True,identity=source[1][:3])
        result=[]
        for sensor,state in self.sensors.items():
            latest=state['latest']
            if latest is None or latest['published']:continue
            try:raw=self._mapped(latest,now)
            except ValueError:
                self._clear(sensor,sample=state['sample'],latest=True);self.counts['mapping_rejected']+=1
            else:latest['published']=True;result.append(raw)
        return tuple(result),tuple(sorted(self.revoked))

    def _fragment(self,item,now):
        if type(item) is not FragmentReceipt or not 0<=item.received_pi_us<=now:raise ValueError('TOF receipt')
        values=TOF_FRAGMENT3.unpack(item.payload)
        pi,device,sample,stamp,error,epoch,seq,sensor,instance,generation,revision,calibration,rows,cols,index,count,first,zones,flags,crc,data=values
        state=self.sensors.get(sensor)
        if state is None:raise ValueError('TOF unselected')
        if ((pi,device,epoch)!=self.scope or not 0<sample<=U63 or not 0<stamp<=U63 or not 0<seq<=U63
                or not 0<generation<1<<32 or instance==NIL or rows!=8 or cols!=8 or flags&~7
                or not 0<=index<4 or count!=4 or first!=index*16 or zones!=16):
            self._clear(sensor,sample=sample if 0<sample<=U63 else state['sample']);raise ValueError('TOF shape/source')
        identity=(device,generation,instance);previous=state['identity']
        if (calibration.hex(),revision)!=state['profile'][1:]:
            self._clear(sensor,sample=sample,latest=True,identity=identity);raise ValueError('TOF calibration')
        if previous is not None and identity!=previous:
            if generation<=previous[1] or instance==previous[2]:
                if generation==previous[1]:state['blocked_generation']=generation;self._clear(sensor,sample=state['sample'],latest=True)
                raise ValueError('TOF generation conflict/replay')
            self._clear(sensor,latest=True);state.update(sample=0,blocked_generation=0)
        state['identity']=identity
        fence=state['fence']
        if fence is not None and fence[0]==device and (generation<fence[1]
                or generation==fence[1] and instance!=fence[2]):
            if generation==fence[1]:state['blocked_generation']=generation;self._clear(sensor,latest=True)
            raise ValueError('TOF retired generation/instance')
        if generation<=state['blocked_generation'] or fence is not None and fence[:3]==identity and sample<=fence[3]:
            raise ValueError('TOF dropped source')
        clock=item.clock
        if clock.epoch!=epoch or clock.sequence!=seq or now>=clock.expires_pi_us or item.received_pi_us>=clock.expires_pi_us:
            self._clear(sensor,sample=sample);raise ValueError('TOF original installed clock')
        common=item.payload[:135]+item.payload[139:141]
        latest=state['latest']
        if latest is not None and sample==latest['sample']:
            offset=137+first*4
            if latest['body'][:137]!=common or latest['crc']!=crc or latest['body'][offset:offset+64]!=data:
                self._clear(sensor,sample=sample,latest=True);raise ValueError('TOF completed conflict')
            self.counts['duplicate']+=1;return
        partial=state['partial']
        if sample>state['sample']:
            if stamp<=state['stamp']:self._clear(sensor,sample=sample);raise ValueError('TOF source time regression')
            self._clear(sensor,sample=state['sample'])
            state.update(sample=sample,stamp=stamp)
            partial=dict(body=bytearray(common+bytes(256)),crc=crc,mask=0,clock=clock,
                first_received=item.received_pi_us,deadline=item.received_pi_us+REASSEMBLY_US)
            state['partial']=partial
        if sample!=state['sample'] or partial is None:raise ValueError('TOF old sample')
        if now>=partial['deadline'] or item.received_pi_us>=partial['deadline']:
            self._clear(sensor,sample=sample);raise ValueError('TOF original reassembly deadline')
        if partial['body'][:137]!=common or partial['crc']!=crc or partial['clock']!=clock:
            self._clear(sensor,sample=sample);raise ValueError('TOF fragment metadata conflict')
        offset=137+first*4
        if partial['mask']&(1<<index):
            if partial['body'][offset:offset+64]!=data:
                self._clear(sensor,sample=sample);raise ValueError('TOF fragment conflict')
            self.counts['duplicate']+=1;return
        partial['body'][offset:offset+64]=data;partial['mask']|=1<<index
        if partial['mask']!=15:return
        body=bytes(partial['body'])
        if len(body)!=393 or zlib.crc32(body)&0xffffffff!=crc:
            self._clear(sensor,sample=sample);raise ValueError('TOF whole CRC32')
        for offset in range(137,393,4):
            distance,status,quality=struct.unpack_from('<HBB',body,offset)
            if status>7 or quality&~31 or quality&1 and (status!=1 or distance in (0,65535)):
                self._clear(sensor,sample=sample);raise ValueError('TOF zone status/quality')
        state['latest']=dict(body=body,crc=crc,clock=clock,received=partial['first_received'],sample=sample,published=False)
        state['partial']=None;self.counts['completed']+=1

    def _mapped(self,latest,now):
        values=TOF_COMMON3.unpack(latest['body'][:137])
        pi,device,sample,stamp,error,epoch,seq,sensor,instance,generation,revision,calibration,rows,cols,flags=values
        clock=latest['clock'];p=self.selection
        if not flags&2 or now>=clock.expires_pi_us:raise ValueError('TOF capture timing/clock')
        # The capture may predate P4. Include the entire allowed capture age and
        # sensor acquisition uncertainty in drift growth, never just RX age.
        pad=drift_pad(clock.drift_ppm,abs(latest['received']-clock.reference_pi_us)+p.maximum_age_us+error)
        lo=stamp+clock.lower-pad;hi=min(stamp+clock.upper+pad,latest['received'])
        if not 0<=lo<=hi<=latest['received']:raise ValueError('TOF conservative mapping')
        middle=(lo+hi)//2;uncertainty=(hi-lo+1)//2
        if (uncertainty>p.maximum_clock_error_us or middle-uncertainty-error<0
                or now>=middle+p.maximum_age_us-uncertainty-error):raise ValueError('TOF original capture expiry')
        zone=latest['body'][137:];distance=bytearray();status=bytearray();quality=bytearray()
        for offset in range(0,256,4):distance.extend(zone[offset:offset+2]);status.append(zone[offset+2]);quality.append(zone[offset+3])
        value=schema.TofObservation(major=1,vehicle=p.vehicle,host_boot=p.host_boot,gateway_instance=p.gateway_instance,
            pi_session=str(UUID(bytes=pi)),device_session=str(UUID(bytes=device)),sensor_id=sensor,source_sample=sample,
            source_us=stamp,pi_sample_us=middle,clock_error_us=uncertainty,sensor_time_error_us=error,
            calibration=calibration.hex(),sensor_config_revision=revision,rows=rows,cols=cols,distance_mm_le=bytes(distance),
            status=bytes(status),quality=bytes(quality),frame_status=flags,source_crc32=latest['crc'],clock_epoch=str(UUID(bytes=epoch)),
            clock_seq=seq,complete=True,sensor_instance=instance,sensor_generation=generation)
        raw=encode(value)
        if len(raw)>1024:raise ValueError('TOF observation cap')
        latest['valid_until']=middle+p.maximum_age_us-uncertainty-error
        return raw

    def payload_bytes(self):
        return sum(len(row[key]['body']) for row in self.sensors.values() for key in ('partial','latest') if row[key] is not None)
