"""S-main과 M-main의 실제 source 통로를 연결한다.

원 Vehicle/Probe/ToF bytes와 callback 시각만 전달한다. identity ACK는 metric/scene/
주행 승인이 아니며, COMPLETE의 LOAD 의미 검사가 없는 경우 복원을 기다린다.
큰 영상·DB copy·센서 기하 검산은 이 main-thread 경로에서 하지 않는다.
"""

from base64 import b64decode,b64encode
from collections import Counter, OrderedDict
from dataclasses import replace
from hashlib import sha256
import json
from uuid import uuid4

from .data_channel import DataLink
from .source_identity import (IdentityOwner, proposal_metadata, _sample, scene_ref_fields,
    current_scene_grant, validate_survey_summary)
from .storage_messages import publication_binding
from .mapper_bootstrap import BootstrapReport,SupervisorBootstrap,load_body,material_bytes
from ..storage.contracts import StorageContext, canonical, json_bytes
from ..storage.geometry_scope import PiGeometryOrigin


def expected_source(binding, policy, *, device, mapper_boot, clock_epoch):
    """S가 아는 OS boot/J epoch와 수락한 M 지도 ID를 분리해 결박한다."""
    return dict(vehicle=policy.vehicle,device=device,mapper_boot=mapper_boot,
        mapper_instance=binding['mapper_instance'],clock_epoch=clock_epoch,
        map_epoch=binding['map_epoch'],frame_epoch=binding['frame_epoch'],odom_epoch=binding['odom_epoch'],
        configuration=binding['configuration'],calibration=binding['calibration'],primitive=binding['primitive'])


def _origin(sample):
    return dict(pi_host_boot=sample.host_boot,gateway_instance=sample.gateway_instance,
        device_session=sample.device_session,pi_session=sample.pi_session)


class SourceChanged(ValueError):
    """새 실제 Pi origin은 현재 M/source의 입력으로 계속 사용할 수 없다."""


class SupervisorSource:
    """현재 M의 binary endpoint owner. S-jio의 작은 불변 참조만 소비한다."""

    def __init__(self, link, raw_binding, jio, *, mapper_instance, mapper_boot, clock_epoch,
                 revision_floor=0, clock):
        if type(link) is not DataLink or link.role!='S':
            raise ValueError('actual registered S binary link required')
        self.link,self.raw,self.jio,self.clock=link,raw_binding,jio,clock
        self.policy=jio.profile.source_policy
        self.mapper_boot,self.clock_epoch=mapper_boot,clock_epoch
        self.owner=IdentityOwner(link.peer,raw_binding,self.policy,
            mapper_instance=mapper_instance,revision_floor=revision_floor)
        self.pending=OrderedDict();self.vehicles=OrderedDict()
        self.current_vehicle=None;self._material=None;self._material_sha=None
        self._early_identity=None;self._acks=OrderedDict();self._turn=0
        self.context=None;self.source=None;self.origin=None
        self.bootstrap=SupervisorBootstrap(link.peer,mapper_instance);self._bootstrap_complete=None
        self.counts=Counter()
        self._last_survey=None

    def _stage(self,key,kind,metadata,body,*,issued,expires,request_id=None):
        if issued>=expires:return
        if key not in self.pending and len(self.pending)>=8:
            raise RuntimeError('bounded source relay heads are full')
        self.pending[key]=(kind,metadata,body,issued,expires,request_id)

    def _input(self,item):
        now=self.clock()
        if item.received_j_us>now:raise ValueError('future JIO delivery is not original transport evidence')
        if item.kind=='VEHICLE':
            sample=_sample(item.raw)
            if (sample.vehicle,sample.calibration)!=(self.policy.vehicle,self.policy.calibration):
                raise ValueError('original vehicle differs from installed source policy')
            if self.origin is not None and (_origin(sample)!=self.origin
                    or sample.odom_epoch!=self.source['odom_epoch']):
                self.jio.fence_source()
                raise SourceChanged('actual Pi/Gateway/device/odom source changed')
            if self.bootstrap.original is not None and self.owner.binding is None and self.current_vehicle is not None:
                previous=_sample(self.current_vehicle)
                if (_origin(sample),sample.odom_epoch,sample.calibration,sample.vehicle)!=(
                        _origin(previous),previous.odom_epoch,previous.calibration,previous.vehicle):
                    self.jio.fence_source();raise SourceChanged('Pi source changed during original bootstrap LOAD')
            key=sha256(item.raw).hexdigest()
            if key not in self.vehicles:
                self.vehicles[key]=(item.raw,item.received_j_us)
                while len(self.vehicles)>128:self.vehicles.popitem(last=False)
            self.current_vehicle=item.raw
        names={'VEHICLE':'SOURCE_VEHICLE','TOF':'SOURCE_TOF','SCENE':'SOURCE_SCENE','PROBE':'SOURCE_CLOCK'}
        if item.kind not in names:raise ValueError('unsupported actual JIO input')
        metadata=dict(received_j_us=str(item.received_j_us),sensor_id=item.sensor_id,
            clock_probe_sha256=None if item.lease is None else sha256(item.lease.raw_probe_bytes).hexdigest())
        if item.kind=='PROBE':
            metadata['clock_probe_sha256']=None if item.lease is None else sha256(item.raw).hexdigest()
        # local delivery lifetime does not replace Pi measurement age or the original trust expiry.
        expires=item.received_j_us+1_000_000
        self._stage((item.kind,item.sensor_id),names[item.kind],metadata,item.raw,
            issued=item.received_j_us,expires=expires)

    def _identity(self,packet,*,bind_ready,on_publication):
        if not bind_ready or self._material is None or self.current_vehicle is None:
            if self._early_identity is not None and self._early_identity.raw!=packet.raw:
                raise ValueError('one original early identity cannot be replaced')
            self._early_identity=packet;return
        proposal=packet.header['metadata'];vehicle=self.vehicles.get(proposal.get('vehicle_sample_sha256'))
        if vehicle is None:raise ValueError('identity references no original retained VehicleSample')
        bootstrap=self._material[1]
        if bootstrap['status']=='COMPLETE' and self.owner.binding is None and self.bootstrap.evidence is None:
            if self._early_identity is not None and self._early_identity.raw!=packet.raw:
                raise ValueError('one original COMPLETE identity cannot be replaced')
            self._early_identity=packet;self.counts['restore_wait']+=1;return
        binding=self.owner.accept(packet,bootstrap=bootstrap,vehicle_wire=vehicle[0],vehicle_received_us=vehicle[1],
            current_vehicle_wire=self.current_vehicle,now_us=self.clock(),restore_material=self.bootstrap.evidence)
        sample=_sample(vehicle[0]);origin=_origin(sample)
        context=StorageContext(bootstrap['database_uuid'],self.link.peer.mapper_worker,binding['mapper_instance'],
            binding['map_epoch'],binding['frame_epoch'],binding['odom_epoch'],sample.host_boot,
            binding['configuration'],binding['calibration'],binding['primitive'],canonical(binding),
            geometry_origin=PiGeometryOrigin(sample.vehicle,sample.host_boot,sample.gateway_instance,
                sample.device_session,sample.pi_session,sample.odom_epoch,sample.calibration).fields)
        if self.context is None:
            on_publication(binding,context)
            self.context=context
            self.source=expected_source(binding,self.policy,device=self.jio.profile.device,
                mapper_boot=self.mapper_boot,clock_epoch=self.clock_epoch)
            self.origin=origin
            self.jio.replace_source(self.source,**origin)
        elif self.context.sha256!=context.sha256:
            raise ValueError('same source incarnation changed its storage context')
        request=packet.header['request_id']
        ack=(dict(source=self.source,origin=origin,geometry_origin=context.geometry_origin_fields,
                  context_sha256=context.sha256),canonical(binding),
             packet.issued_us,packet.expires_us,request)
        previous=self._acks.get(request)
        if previous is not None:ack=previous  # scene 전환도 원 proposal ACK를 새 사건으로 다시 쓰지 않는다.
        if previous is None:
            if len(self._acks)>=16:raise RuntimeError('bounded identity ACK history is full')
            self._acks[request]=ack
        self._stage('ACK','SOURCE_IDENTITY_ACK',ack[0],ack[1],issued=ack[2],expires=ack[3],request_id=request)
        self.counts['identity_accepted']+=1

    def _publish_scene(self,on_publication,*,now,expires):
        if now>=expires:raise TimeoutError('original scene publication event already expired')
        binding=self.owner.binding
        context=replace(self.context,source_binding=canonical(binding))
        on_publication(binding,context)
        self.context=context
        self.jio.replace_source(self.source,**self.origin)  # 같은 source이므로 token/clock/odom은 유지한다.
        metadata=dict(source=self.source,origin=self.origin,geometry_origin=context.geometry_origin_fields,
            context_sha256=context.sha256)
        revision=int(binding['source_binding_revision'])
        self._stage(('PUBLICATION',revision),'SOURCE_PUBLICATION',metadata,canonical(binding),
            issued=now,expires=min(expires,now+5_000_000),request_id=str(uuid4()))
        self.counts['publication_changed']+=1

    def _scene_current(self,on_publication,now):
        if self.owner.binding is None or self.owner.binding['scene_admission_ref'] is None:return
        item=self.jio.current_scene
        try:
            if item is None:raise ValueError('scene grant absent')
            grant,_=current_scene_grant(item.raw,self.jio.current_lease,self.source,self.origin,now)
            if grant.lifecycle_state!=2 or scene_ref_fields(grant.scene)!=self.owner.binding['scene_admission_ref']:
                raise ValueError('scene grant revoked or changed')
        except (ValueError,TypeError,OverflowError):
            if self.owner.clear_scene():self._publish_scene(on_publication,now=now,expires=now+5_000_000)

    def _survey(self,packet,on_publication,now):
        item=self.jio.current_scene
        if item is None:raise ValueError('survey report has no current original grant')
        fingerprint=(packet.body,canonical(packet.header['metadata']),packet.issued_us,packet.expires_us)
        if fingerprint==self._last_survey:
            validate_survey_summary(packet.body,item.raw,self.jio.current_lease,self.source,self.origin,
                issued_us=packet.issued_us,expires_us=packet.expires_us,now_us=now)
            self.counts['survey_duplicate']+=1;return
        changed,summary=self.owner.accept_survey(packet,grant_raw=item.raw,lease=self.jio.current_lease,
            source=self.source,origin=self.origin,now_us=now)
        self.jio.offer_survey_summary(packet.body,grant_sha256=sha256(item.raw).hexdigest(),expires_j_us=packet.expires_us)
        self._last_survey=fingerprint
        if changed:self._publish_scene(on_publication,now=now,expires=packet.expires_us if summary.status==2 else now+5_000_000)
        self.counts['survey_accepted']+=1

    def pump(self,material,*,bind_ready,on_publication,on_bootstrap_load=None,bootstrap_completion=None):
        """한 회차 최대 JIO7/packet4. bind_ready는 실제 현재 RAW ACK 수락 사실이다."""
        now=self.clock()
        if material is not None:
            wire=canonical(dict(binding=material[0],bootstrap=material[1]))
            if len(wire)>2048:raise ValueError('actual source storage material exceeds local body cap')
            key=sha256(wire).hexdigest()
            if key!=self._material_sha:
                if self.owner.binding is None and self.bootstrap.original is not None and wire!=self.bootstrap.original:
                    raise SourceChanged('D material changed during original bootstrap LOAD')
                self._material,self._material_sha=material,key
                self._stage('STORAGE','SOURCE_STORAGE',{},wire,issued=now,expires=now+5_000_000)
        for item in self.jio.take_inputs():self._input(item)
        self._scene_current(on_publication,now)
        self.bootstrap.observe(None if bootstrap_completion is None else bootstrap_completion(),now)
        if self._early_identity is not None and now>=self._early_identity.expires_us:
            self._early_identity=None;self.counts['identity_expired']+=1
        if self._early_identity is not None and bind_ready and self._material is not None and self.current_vehicle is not None:
            packet=self._early_identity;self._early_identity=None
            self._identity(packet,bind_ready=True,on_publication=on_publication)
        for packet in self.link.poll():
            if not packet.issued_us<=now<packet.expires_us:
                self.counts['expired']+=1;continue
            if packet.kind=='SOURCE_IDENTITY':
                self._identity(packet,bind_ready=bind_ready,on_publication=on_publication)
            elif packet.kind=='WORLD_CANDIDATE':
                if self.owner.binding is None or packet.header['metadata']!={
                        'publication_sha256':self.owner.binding_sha256}:
                    raise ValueError('world candidate has no current original publication binding')
                self.jio.offer_world(packet.body,expires_j_us=packet.expires_us)
            elif packet.kind=='SURVEY_SUMMARY':
                try:self._survey(packet,on_publication,now)
                except (ValueError,TypeError,OverflowError):self.counts['survey_rejected']+=1
            elif packet.kind=='SOURCE_BOOTSTRAP_LOAD':
                if self._material is None:raise ValueError('bootstrap LOAD has no original D material')
                self.bootstrap.offer_load(packet,self._material)
            elif packet.kind=='SOURCE_BOOTSTRAP_COMPLETE':
                self.bootstrap._registered(packet,packet.kind)
                if self._bootstrap_complete is not None and self._bootstrap_complete.raw!=packet.raw:
                    raise ValueError('one original bootstrap completion pending')
                self._bootstrap_complete=packet
            else:raise ValueError('unsupported M source statement')
        self.bootstrap.dispatch(on_bootstrap_load if bind_ready else None,now)
        if self._bootstrap_complete is not None:
            packet=self._bootstrap_complete
            if now>=packet.expires_us:raise TimeoutError('original bootstrap complete report expired')
            if self.bootstrap.requests and len(self.bootstrap.physical)==len(self.bootstrap.requests) and self.bootstrap.pending is None:
                self.bootstrap.accept_complete(packet,self._material,now);self._bootstrap_complete=None
        self.link.flush()
        for key in tuple(self.pending):
            if now>=self.pending[key][4]:
                if self.pending[key][0]=='SOURCE_PUBLICATION':
                    raise TimeoutError('original scene publication delivery expired; explicit source resync required')
                del self.pending[key];self.counts['expired']+=1
        if self.pending:
            keys=tuple(self.pending)
            publications=[key for key in keys if type(key) is tuple and key[0]=='PUBLICATION']
            key=('ABORT' if 'ABORT' in self.pending else 'ACK' if 'ACK' in self.pending
                 else min(publications,key=lambda item:item[1]) if publications
                 else 'STORAGE' if 'STORAGE' in self.pending else keys[self._turn%len(keys)])
            kind,metadata,body,issued,expires,request=self.pending[key]
            if self.link.send(kind,metadata,body,request_id=request,issued_us=issued,expires_us=expires) is not None:
                del self.pending[key];self._turn+=1
            self.link.flush()

    def offer_abort(self,notice):
        """원 job 기한을 보존한 terminal local event. 새 DB RESULT/현재 성공을 발급하지 않는다."""
        from .resident_db import DbAbort
        if type(notice) is not DbAbort or notice.mapper.worker_instance!=self.link.peer.mapper_worker:
            raise ValueError('storage terminal belongs to another original M owner')
        now=self.clock();body=canonical(notice.fields)
        if len(body)>8192:raise ValueError('bounded original DB abort history exceeded')
        if 'ABORT' in self.pending:raise RuntimeError('one original storage terminal is already pending')
        self._stage('ABORT','SOURCE_STORAGE_ABORT',{},body,issued=now,expires=now+1_000_000,request_id=notice.request_id)

    def close(self):self.link.close()


class MapperSource:
    """M-main source 수신 owner. compute에는 최대64KiB 원본 snapshot만 넘긴다."""

    def __init__(self,link,raw_binding,policy,*,mapper_instance,device,clock):
        if type(link) is not DataLink or link.role!='M':raise ValueError('actual registered M binary link required')
        self.link,self.raw,self.policy,self.clock=link,raw_binding,policy,clock
        self.mapper_instance=mapper_instance
        self.device=device
        self.map_epoch,self.frame_epoch=str(uuid4()),str(uuid4())
        self.material=self.publication=self.source=self.origin=None
        self.storage_context=None
        self.context_sha256=None;self._proposal=None;self._proposal_packet=None
        self.vehicles=OrderedDict();self.probes=OrderedDict();self.tof={};self.scene=None
        self._outputs=OrderedDict();self._output_heads={}
        self.active_probe=None;self._vehicle_head=None;self.counts=Counter()
        self._abort=None
        self._bootstrap_pending=OrderedDict();self._bootstrap_report=None;self._bootstrap_origin=None;self._bootstrap_started=False

    def offer_bootstrap_load(self,request):
        self._bootstrap_started=True
        if self._vehicle_head is not None and self._bootstrap_origin is None:
            sample=_sample(self._vehicle_head)
            self._bootstrap_origin=(_origin(sample),sample.odom_epoch,sample.calibration,sample.vehicle)
        self._bootstrap_stage('SOURCE_BOOTSTRAP_LOAD',load_body(request),request.job.job_id,
                              request.job.issued_us,request.job.expires_us)

    def accept_bootstrap(self,report):
        """compute 소유 SourceComplete의 작은 원 보고만 받는다. restore/metric 승인이 아니다."""
        if (type(report) is not BootstrapReport or self.material is None
                or report.material_sha256!=sha256(material_bytes(self.material)).hexdigest()
                or (report.mapper_worker,report.mapper_instance)!=(self.link.peer.mapper_worker,self.mapper_instance)):
            raise ValueError('bootstrap complete belongs to another original source')
        if self._bootstrap_report is not None and self._bootstrap_report is not report:
            raise ValueError('one original complete source report required')
        self._bootstrap_report=report
        self._bootstrap_stage('SOURCE_BOOTSTRAP_COMPLETE',report.body,report.request_id,report.issued_us,report.expires_us)

    def _bootstrap_stage(self,kind,body,request_id,issued,expires):
        key=(kind,request_id);value=(kind,body,request_id,issued,expires)
        if key in self._bootstrap_pending and self._bootstrap_pending[key]!=value:raise ValueError('bootstrap retry changed original packet')
        if key not in self._bootstrap_pending and len(self._bootstrap_pending)>=2:raise ValueError('bounded bootstrap transport heads full')
        self._bootstrap_pending[key]=value

    def _delivery(self,packet):
        meta=packet.header['metadata']
        if set(meta)!= {'received_j_us','sensor_id','clock_probe_sha256'}:
            raise ValueError('source delivery metadata fields differ')
        received=int(meta['received_j_us'])
        if str(received)!=meta['received_j_us'] or not 0<=received<=packet.issued_us:
            raise ValueError('source callback time changed in local transport')
        return dict(raw=b64encode(packet.body).decode('ascii'),received_j_us=str(received),
                    clock_probe_sha256=meta['clock_probe_sha256'])

    def _offer_output(self,kind,raw,metadata,issued_us,expires_us):
        now=self.clock()
        if (type(issued_us) is not int or type(expires_us) is not int
                or not issued_us<=now<expires_us<=issued_us+5_000_000):
            raise ValueError('original mapper output event/expiry required')
        original=(raw,canonical(metadata),issued_us,expires_us)
        previous=self._output_heads.get(kind)
        if previous is not None and previous[0]==raw and previous[1]==original[1]:
            if previous!=original:raise ValueError('retry renewed original mapper output time')
            return False
        self._output_heads[kind]=original
        self._outputs[kind]=(raw,metadata,issued_us,expires_us,str(uuid4()))
        return True

    def offer_world(self,raw,*,publication_sha256,issued_us,expires_us):
        """현재 원 publication의 최신 world1개를 stage한다. 이미 선택된 packet은 바꾸지 않는다."""
        from xenova_drive_world import decode,schema
        if self.publication is None or publication_sha256!=sha256(canonical(self.publication)).hexdigest():return False
        value=decode(raw,schema.WorldSnapshot)
        if any(getattr(value,key)!=self.source[key] for key in self.source if key!='device'):
            raise ValueError('world output changed original source')
        return self._offer_output('WORLD_CANDIDATE',raw,dict(publication_sha256=publication_sha256),issued_us,expires_us)

    def offer_survey_summary(self,raw,*,publication_sha256,grant_sha256,issued_us,expires_us):
        """compute가 고정한 원 보고/완료/절대기한만 최신1에 둔다. main은 새 관측을 만들지 않는다."""
        from xenova_drive_world import decode,schema
        if self.publication is None or publication_sha256!=sha256(canonical(self.publication)).hexdigest():return False
        if self.scene is None or grant_sha256!=sha256(b64decode(self.scene['raw'],validate=True)).hexdigest():return False
        summary=decode(raw,schema.SurveySummary)
        if summary.completed_peer_us!=issued_us:raise ValueError('survey event changed actual completed time')
        return self._offer_output('SURVEY_SUMMARY',raw,
            dict(publication_sha256=publication_sha256,grant_sha256=grant_sha256),issued_us,expires_us)

    def _publication(self,packet):
        """scene-only 새 publication을 검산한다. 초기 제안이나 raw/source 신원을 다시 발급하지 않는다."""
        from xenova_drive_world import decode,schema
        if self.publication is None or self.storage_context is None:raise ValueError('no original identity publication')
        binding=publication_binding(json_bytes(packet.body,2048));meta=packet.header['metadata']
        if (meta['source']!=self.source or meta['origin']!=self.origin
                or meta['geometry_origin']!=self.storage_context.geometry_origin_fields):
            raise ValueError('scene publication changed original source/geometry origin')
        stable=set(self.publication)-{'scene_admission_ref','source_binding_revision'}
        if any(binding[key]!=self.publication[key] for key in stable):
            raise ValueError('scene publication changed original raw/map/configuration')
        context=replace(self.storage_context,source_binding=canonical(binding))
        if context.sha256!=meta['context_sha256']:raise ValueError('scene publication changed actual context SHA')
        if binding==self.publication:
            self.counts['publication_duplicate']+=1;return
        if int(binding['source_binding_revision'])!=int(self.publication['source_binding_revision'])+1:
            raise ValueError('scene publication revision is not exactly next')
        scene=binding['scene_admission_ref']
        if scene is not None:
            if self.scene is None:raise ValueError('positive scene publication has no actual received grant')
            grant=decode(b64decode(self.scene['raw'],validate=True),schema.SceneGrant)
            if grant.lifecycle_state!=2 or scene_ref_fields(scene)!=scene_ref_fields(grant.scene):
                raise ValueError('positive scene publication differs from actual ADMITTED grant')
            if (grant.pi_host_boot!=self.origin['pi_host_boot'] or grant.gateway_instance!=self.origin['gateway_instance']
                    or grant.mapper_device!=self.device or any(getattr(grant,key)!=self.source[key] for key in
                        ('vehicle','map_epoch','frame_epoch','odom_epoch','configuration','calibration','primitive'))):
                raise ValueError('positive scene publication changed grant source')
            probe_delivery=self.probes.get(self.active_probe)
            if probe_delivery is None:raise ValueError('positive scene publication has no actual returned clock')
            probe=decode(b64decode(probe_delivery['raw'],validate=True),schema.MapperClockProbe)
            if not probe.HasField('mapper_trust'):raise ValueError('positive scene publication has no original returned trust')
            trust=probe.mapper_trust;now=self.clock()
            if (probe.pi_host_boot!=self.origin['pi_host_boot'] or probe.admission_revision!=grant.source_admission_revision
                    or any(getattr(trust,key)!=self.source[key] for key in self.source if key!='device')
                    or grant.issued_pi_us>now+trust.lower_offset_us
                    or not now<packet.expires_us<=min(grant.valid_until_pi_us,trust.expires_pi_us)-trust.upper_offset_us):
                raise ValueError('positive scene publication exceeds original clock/grant expiry')
        self.publication,self.storage_context,self.context_sha256=binding,context,context.sha256
        self._outputs.clear()  # 옛 publication에 결박한 미선택 출력만 철회한다. 원 frame/관측은 유지한다.
        self.counts['publication_changed']+=1

    def pump(self):
        now=self.clock();self.link.flush()
        for packet in self.link.poll():
            if not packet.issued_us<=now<packet.expires_us:
                if packet.kind=='SOURCE_PUBLICATION':raise TimeoutError('original scene publication delivery expired')
                self.counts['expired']+=1;continue
            if packet.kind=='SOURCE_STORAGE':
                value=json_bytes(packet.body,2048)
                if type(value) is not dict or set(value)!= {'binding','bootstrap'}:raise ValueError('exact D material required')
                self.material=(value['binding'],value['bootstrap'])
            elif packet.kind=='SOURCE_STORAGE_ABORT':
                from .resident_db import DbAbort
                notice=DbAbort.from_fields(json_bytes(packet.body,8192))
                if notice.mapper.worker_instance!=self.link.peer.mapper_worker or packet.header['request_id']!=notice.request_id:
                    raise ValueError('storage terminal changed original M/request')
                if self._abort is not None:raise RuntimeError('bounded M storage terminal slot is full')
                self._abort=notice
            elif packet.kind=='SOURCE_PUBLICATION':
                try:self._publication(packet)
                except (ValueError,TypeError,OverflowError):self.counts['publication_rejected']+=1
            elif packet.kind=='SOURCE_IDENTITY_ACK':
                if self._proposal_packet is None or packet.header['request_id']!=self._proposal_packet.header['request_id']:
                    raise ValueError('identity ACK has no original M proposal')
                if (packet.issued_us,packet.expires_us)!=(self._proposal_packet.issued_us,self._proposal_packet.expires_us):
                    raise ValueError('identity ACK renewed its original proposal')
                binding=publication_binding(json_bytes(packet.body,2048));meta=packet.header['metadata']
                if set(meta)!= {'source','origin','geometry_origin','context_sha256'}:raise ValueError('exact source ACK metadata required')
                expected=self._proposal
                if any(binding[key]!=expected[key] for key in ('map_epoch','frame_epoch','odom_epoch','mapper_instance','raw_binding_sha256','configuration','calibration','primitive')):
                    raise ValueError('identity ACK differs from original M geometry proposal')
                if self.publication is not None and self.publication!=binding:
                    if (int(binding['source_binding_revision'])<int(self.publication['source_binding_revision'])
                            and all(binding[key]==self.publication[key] for key in binding
                                if key not in ('scene_admission_ref','source_binding_revision'))):
                        self.counts['identity_duplicate']+=1;continue
                    raise ValueError('identity ACK silently changed source')
                source=meta['source'];origin=meta['origin']
                expected_source_fields=set(('vehicle','device','mapper_boot','mapper_instance','clock_epoch','map_epoch',
                    'frame_epoch','odom_epoch','configuration','calibration','primitive'))
                if type(source) is not dict or set(source)!=expected_source_fields:
                    raise ValueError('identity ACK has incomplete clock source')
                if (source['vehicle']!=self.policy.vehicle or source['device']!=self.device
                        or source['clock_epoch']!=self.raw.fields['clock_epoch']
                        or any(source[key]!=binding[key] for key in ('mapper_instance','map_epoch','frame_epoch','odom_epoch','configuration','calibration','primitive'))):
                    raise ValueError('identity ACK clock source differs from actual bound raw/publication')
                if origin!={key:expected[key] for key in ('pi_host_boot','gateway_instance','device_session','pi_session')}:
                    raise ValueError('identity ACK changed original Pi origin')
                geometry=PiGeometryOrigin.from_fields(meta['geometry_origin'])
                if (geometry.vehicle,geometry.pi_host_boot,geometry.gateway_instance,geometry.device_session,
                        geometry.pi_session,geometry.odom_epoch,geometry.calibration)!=(
                        expected['vehicle'],expected['pi_host_boot'],expected['gateway_instance'],expected['device_session'],
                        expected['pi_session'],expected['odom_epoch'],expected['calibration']):
                    raise ValueError('identity ACK changed original geometry origin')
                if self.material is None:raise ValueError('identity ACK has no admitted original D material')
                context=StorageContext(self.material[1]['database_uuid'],self.link.peer.mapper_worker,binding['mapper_instance'],
                    binding['map_epoch'],binding['frame_epoch'],binding['odom_epoch'],origin['pi_host_boot'],
                    binding['configuration'],binding['calibration'],binding['primitive'],canonical(binding),geometry_origin=geometry.fields)
                if context.sha256!=meta['context_sha256']:raise ValueError('identity ACK context SHA differs from actual original material')
                self.publication=binding;self.source=meta['source'];self.origin=meta['origin'];self.context_sha256=meta['context_sha256']
                self.storage_context=context
            elif packet.kind in ('SOURCE_VEHICLE','SOURCE_CLOCK','SOURCE_TOF','SOURCE_SCENE'):
                delivery=self._delivery(packet)
                if packet.kind=='SOURCE_VEHICLE':
                    sample=_sample(packet.body)
                    origin=(_origin(sample),sample.odom_epoch,sample.calibration,sample.vehicle)
                    if self._bootstrap_origin is not None and origin!=self._bootstrap_origin:
                        raise SourceChanged('Pi source changed during original bootstrap LOAD')
                    if self._bootstrap_started and self._bootstrap_origin is None:self._bootstrap_origin=origin
                    key=sha256(packet.body).hexdigest();self._vehicle_head=packet.body
                    self.vehicles[key]=delivery
                    while len(self.vehicles)>24:self.vehicles.popitem(last=False)
                elif packet.kind=='SOURCE_CLOCK':
                    from xenova_drive_world import decode,schema
                    decode(packet.body,schema.MapperClockProbe)
                    key=sha256(packet.body).hexdigest();self.probes[key]=delivery
                    while len(self.probes)>8:self.probes.popitem(last=False)
                    self.active_probe=packet.header['metadata']['clock_probe_sha256']
                elif packet.kind=='SOURCE_TOF':
                    sensor=packet.header['metadata']['sensor_id']
                    if type(sensor) is not int or sensor not in (0,1):raise ValueError('unsupported source ToF index')
                    self.tof[sensor]=delivery
                else:self.scene=delivery
            else:raise ValueError('unsupported S source statement')
        if self._bootstrap_pending:
            key=next(iter(self._bootstrap_pending));kind,body,request,issued,expires=self._bootstrap_pending[key]
            if not issued<=now<expires:raise TimeoutError('original bootstrap transport expired')
            if self.link.send(kind,{},body,request_id=request,issued_us=issued,expires_us=expires) is not None:
                del self._bootstrap_pending[key]
            self.link.flush();return
        if self.publication is None and self._proposal_packet is None and self.material is not None and self._vehicle_head is not None:
            bootstrap=self.material[1]
            # Restore requires actual compute LOAD validation; initial copy alone does not choose identity.
            if bootstrap['status']=='COMPLETE' and self._bootstrap_report is None:self.counts['restore_wait']+=1;return
            self._proposal=proposal_metadata(raw_binding=self.raw,mapper_instance=self.mapper_instance,
                bootstrap=bootstrap,vehicle_wire=self._vehicle_head,policy=self.policy,nonce=str(uuid4()),
                map_epoch=self.map_epoch,frame_epoch=self.frame_epoch)
            expires=now+5_000_000 if bootstrap['status']=='EMPTY' else min(now+5_000_000,self._bootstrap_report.expires_us)
            if now>=expires:raise TimeoutError('original complete source report expired before identity')
            self._proposal_packet=self.link.send('SOURCE_IDENTITY',self._proposal,issued_us=now,expires_us=expires)
            self.link.flush()
        if self._proposal_packet is not None and self.publication is None and now>=self._proposal_packet.expires_us:
            raise TimeoutError('original source identity proposal expired without ACK')
        for kind in tuple(self._outputs):
            raw,metadata,issued,expires,request=self._outputs[kind]
            if now>=expires:
                del self._outputs[kind];self.counts['output_expired']+=1;continue
            if self.link.send(kind,metadata,raw,request_id=request,issued_us=issued,expires_us=expires) is not None:
                del self._outputs[kind]
            self.link.flush()
            break

    def context_bytes(self):
        """시각/qualifier를 재발급하지 않는 immutable job context. 검산은 compute 책임이다."""
        value=dict(version=1,publication=self.publication,source=self.source,origin=self.origin,
            context_sha256=self.context_sha256,active_probe_sha256=self.active_probe,
            storage_context=None if self.storage_context is None else self.storage_context.fields,
            vehicles=list(self.vehicles.values()),probes=dict(self.probes),
            tof={str(key):item for key,item in self.tof.items()},scene=self.scene)
        result=canonical(value)
        if len(result)>65536:raise ValueError('bounded original source snapshot exceeds64KiB')
        return result

    def close(self):self.link.close()

    def take_aborts(self):
        result=() if self._abort is None else (self._abort,)
        self._abort=None
        return result
