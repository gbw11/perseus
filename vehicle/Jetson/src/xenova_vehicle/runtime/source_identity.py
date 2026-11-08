"""M의 지도 식별 제안을 S의 PublicationBinding에 연결한다.

body 없는 SOURCE_IDENTITY local variant다. 현재 worker/raw/원 VehicleSample과 D의
bootstrap을 대조하며 S 소유 publication incarnation/OS clock을 M이 바꾸지 못한다.
identity ACK는 clock/metric/world/scene/이동 사용권이 아니다.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
from uuid import UUID,uuid4

from .data_channel import DataPacket,DataPeer
from .raw_binding import RawBinding
from .storage_messages import publication_binding


PROPOSAL_FIELDS=frozenset(('proposal_nonce','database_uuid','mapper_instance','raw_binding_sha256',
    'map_epoch','frame_epoch','odom_epoch','vehicle','configuration','calibration','primitive',
    'calibration_profile_sha256','vehicle_sample_sha256','vehicle_sequence','pi_host_boot',
    'device_session','gateway_instance','pi_session','checkpoint_manifest_sha256'))


def _canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode('ascii')


def scene_ref_fields(value):
    """원 protobuf 표준 JSON만 보존한다. bytes/uint64에 별도 local 표현을 만들지 않는다."""
    from google.protobuf.json_format import MessageToDict, ParseDict, ParseError
    from xenova_drive_world import decode, encode, schema
    if type(value) is dict:
        try: message = ParseDict(json.loads(_canonical(value)), schema.SceneAdmissionRef(), ignore_unknown_fields=False)
        except (ParseError,TypeError) as error: raise ValueError('invalid standard scene reference JSON') from error
    elif type(value) is schema.SceneAdmissionRef:
        message = value
    else:
        raise ValueError('exact SceneAdmissionRef or its standard protobuf JSON required')
    raw = encode(message)
    fields = MessageToDict(decode(raw, schema.SceneAdmissionRef), preserving_proto_field_name=True)
    if type(value) is dict and fields != value:
        raise ValueError('scene reference is not the original standard protobuf JSON')
    return fields


def current_scene_grant(raw, lease, source, origin, now_us):
    """현재 compatible lease와 원 Pi SceneGrant만 검산하고 보수 J expiry를 돌려준다."""
    from xenova_drive_world import decode, schema
    from xenova_drive_world.mapper_clock import ClockLease
    if (type(raw) is not bytes or not 0 < len(raw) <= 1024 or type(lease) is not ClockLease
            or type(now_us) is not int or not 0 <= now_us < 1 << 63
            or lease.source != source or lease.pi_host_boot != origin['pi_host_boot']):
        raise ValueError('scene has no original compatible source clock')
    grant = decode(raw, schema.SceneGrant)
    if (grant.vehicle != source['vehicle'] or grant.mapper_device != source['device']
            or grant.pi_host_boot != origin['pi_host_boot'] or grant.gateway_instance != origin['gateway_instance']
            or grant.source_admission_revision != lease.source_revision
            or any(getattr(grant, key) != source[key] for key in
                ('map_epoch','frame_epoch','odom_epoch','configuration','calibration','primitive'))):
        raise ValueError('scene grant changed current Pi/source identity')
    lease.pi_interval_to_j((lease.probe_pi_send_us, lease.probe_pi_send_us), now_us)
    trust = decode(lease.trust_bytes, schema.WorldTrust)
    expires = min(grant.valid_until_pi_us, lease.expires_pi_us) - trust.upper_offset_us
    if grant.issued_pi_us > now_us + trust.lower_offset_us or now_us >= expires:
        raise ValueError('original scene/clock lease is not current')
    return grant, expires


def known_scene_negative(raw, known_raw, *, source, pi_host_boot, gateway_instance):
    """알려진 원 grant의 terminal 진술만 lease 없이 식별한다. positive 승인은 없다.

    lifecycle state/revision 이외 원 nonce/full SceneRef/source revision/수명을 고정한다.
    미등록·외부·옛 nonce·충돌은 False이며 같은 revision은 원 bytes 동일 때만 허용한다.
    """
    from xenova_drive_world import decode, encode, schema
    if (type(raw) is not bytes or type(known_raw) is not bytes
            or not 0 < len(raw) <= 1024 or not 0 < len(known_raw) <= 1024 or type(source) is not dict):
        return False
    try:
        value = decode(raw, schema.SceneGrant); known = decode(known_raw, schema.SceneGrant)
        if value.lifecycle_state not in (3, 4):
            return False
        if ((known.vehicle, known.mapper_device, known.pi_host_boot, known.gateway_instance)
                != (source['vehicle'], source['device'], pi_host_boot, gateway_instance)
                or any(getattr(known, name) != source[name] for name in
                    ('map_epoch', 'frame_epoch', 'odom_epoch', 'configuration', 'calibration', 'primitive'))
                or value.lifecycle_revision < known.lifecycle_revision):
            return False
        if value.lifecycle_revision == known.lifecycle_revision:
            return raw == known_raw
        expected = schema.SceneGrant(); expected.CopyFrom(known)
        expected.lifecycle_state = value.lifecycle_state; expected.lifecycle_revision = value.lifecycle_revision
        return encode(expected) == encode(value)
    except (ValueError, TypeError, KeyError, OverflowError):
        return False


def validate_survey_summary(raw, grant_raw, lease, source, origin, *, issued_us, expires_us, now_us):
    """보고 수명과 현재 grant를 확인한다. M의 실제 기하/자료 COMPLETE를 대신 승인하지 않는다."""
    from xenova_drive_world import decode, encode, schema
    if type(raw) is not bytes or not 0 < len(raw) <= 512:
        raise ValueError('bounded original SurveySummary bytes required')
    summary = decode(raw, schema.SurveySummary)
    if encode(summary)!=raw:
        raise ValueError('survey must preserve the original shared-encoder bytes without dropped fields')
    grant, limit = current_scene_grant(grant_raw, lease, source, origin, now_us)
    if (type(issued_us) is not int or type(expires_us) is not int
            or issued_us != summary.completed_peer_us or not issued_us <= now_us < expires_us
            or not issued_us < expires_us <= min(limit, issued_us + 5_000_000)
            or summary.request_nonce != grant.request_nonce or summary.survey_id != grant.scene.survey_id
            or summary.survey_revision != grant.scene.survey_revision
            or summary.coverage_profile_sha256 != grant.scene.coverage_profile_sha256):
        raise ValueError('survey report changed original grant/nonce/profile/time')
    allowed = {0: (0,), 1: (1,), 2: (2,), 3: (0,1,2), 4: (1,)}
    if (grant.lifecycle_state not in allowed[summary.status]
            or summary.status == 1 and grant.purpose != 0 or summary.status == 4 and grant.purpose != 1):
        raise ValueError('survey status is not compatible with current grant')
    if summary.status == 2:
        evidence = grant.restore_checkpoint_sha256 if grant.purpose else grant.frozen_evidence_sha256
        if (summary.survey_revision != grant.frozen_survey_revision or summary.evidence_sha256 != evidence
                or summary.hazard_revision != grant.frozen_hazard_revision):
            raise ValueError('APPLIED changed original admitted frozen evidence')
    return summary, grant


def _uuid(value):
    if type(value) is not str or str(UUID(value))!=value or not UUID(value).int:
        raise ValueError('canonical nonzero identity UUID required')
    return value


def _sha(value):
    if type(value) is not str or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('lowercase identity SHA256 required')
    return value


def _label(value):
    if type(value) is not str or not 1<=len(value)<=64 or any(not 33<=ord(c)<=126 for c in value):
        raise ValueError('bounded ASCII identity label required')
    return value


def _sample(wire):
    from xenova_drive_world import schema,decode
    if type(wire) is not bytes or len(wire)>1024:
        raise ValueError('original bounded VehicleSample bytes required')
    sample=decode(wire,schema.VehicleSample)
    if not sample.HasField('gateway_instance') or not sample.HasField('pi_session'):
        raise ValueError('S13 identity requires original Gateway and Pi session provenance')
    _uuid(sample.gateway_instance);_uuid(sample.pi_session)
    return sample


@dataclass(frozen=True)
class SourcePolicy:
    """설치자가 명시한 승인 구성. profile과 차량 bundle의 관계를 alias로 만들지 않는다."""
    vehicle: str
    configuration: str
    calibration: str
    primitive: str
    calibration_profile_sha256: str

    def __post_init__(self):
        for value in (self.vehicle,self.configuration):_label(value)
        for value in (self.calibration,self.primitive,self.calibration_profile_sha256):_sha(value)

    def check_profile(self,profile):
        if (profile.sha256,profile.vehicle_bundle_sha256)!=(self.calibration_profile_sha256,self.calibration):
            raise ValueError('pinned local calibration profile does not declare the approved vehicle bundle')


def proposal_metadata(*,raw_binding,mapper_instance,bootstrap,vehicle_wire,policy,nonce,
                      map_epoch=None,frame_epoch=None):
    """M producer는 EMPTY에서 선택한 UUID를 ACK/재시도까지 유지해야 한다."""
    if type(raw_binding) is not RawBinding or type(policy) is not SourcePolicy:
        raise ValueError('original raw binding and source policy required')
    sample=_sample(vehicle_wire)
    _uuid(mapper_instance);_uuid(nonce)
    if bootstrap['status']=='EMPTY':
        _uuid(map_epoch);_uuid(frame_epoch)
        checkpoint=None
    elif bootstrap['status']=='COMPLETE':
        selected=bootstrap['selected_checkpoint']
        map_epoch,frame_epoch=selected['map_epoch'],selected['frame_epoch']
        if (selected['odom_epoch'],selected['pi_host_boot'],selected['configuration'],selected['calibration'],selected['primitive_sha256'])!=(
                sample.odom_epoch,sample.host_boot,policy.configuration,policy.calibration,policy.primitive):
            raise ValueError('restored identity is incompatible with the original current vehicle/context')
        checkpoint=selected['manifest_sha256']
    else:raise ValueError('identity proposal needs an admitted EMPTY or COMPLETE bootstrap')
    if (sample.vehicle,sample.calibration)!=(policy.vehicle,policy.calibration):
        raise ValueError('original VehicleSample differs from the approved vehicle/bundle')
    if raw_binding.fields['calibration_bundle']!=policy.calibration_profile_sha256:
        raise ValueError('local raw calibration profile differs from approved source policy')
    return dict(proposal_nonce=nonce,database_uuid=bootstrap['database_uuid'],mapper_instance=mapper_instance,
        raw_binding_sha256=raw_binding.sha256,map_epoch=map_epoch,frame_epoch=frame_epoch,
        odom_epoch=sample.odom_epoch,vehicle=sample.vehicle,configuration=policy.configuration,
        calibration=sample.calibration,primitive=policy.primitive,calibration_profile_sha256=policy.calibration_profile_sha256,
        vehicle_sample_sha256=sha256(vehicle_wire).hexdigest(),vehicle_sequence=str(sample.sequence),
        pi_host_boot=sample.host_boot,device_session=sample.device_session,gateway_instance=sample.gateway_instance,
        pi_session=sample.pi_session,checkpoint_manifest_sha256=checkpoint)


class IdentityOwner:
    """S-main만 호출한다. 현재 incarnation의 지도 ID와 nonce 원문을 유한하게 고정한다."""

    def __init__(self,peer,raw_binding,policy,*,mapper_instance,revision_floor=0):
        if type(peer) is not DataPeer or type(raw_binding) is not RawBinding or type(policy) is not SourcePolicy:
            raise ValueError('registered M peer/current raw/source policy required')
        _uuid(mapper_instance)
        if (raw_binding.fields['service_instance']!=peer.service_instance
                or type(revision_floor) is not int or not 0<=revision_floor<2**64-1):
            raise ValueError('identity owner differs from registered service/revision')
        self.peer,self.raw,self.policy=peer,raw_binding,policy
        self.mapper_instance=mapper_instance
        self.revision=revision_floor
        self._binding=None
        self._nonces={}
        self._identity=None
        self._origin_fields=None

    @property
    def binding(self):return None if self._binding is None else json.loads(self._binding)

    @property
    def binding_sha256(self):return None if self._binding is None else sha256(self._binding).hexdigest()

    def clear_scene(self):
        """현재 ref만 즉시 null로 철회한다. 같은 철회는 revision을 다시 늘리지 않는다."""
        return self._scene_binding(None)

    def _scene_binding(self, scene):
        if self._binding is None: raise ValueError('scene publication requires original source identity')
        binding = self.binding
        if binding['scene_admission_ref'] == scene: return False
        if self.revision >= (1 << 64) - 1: raise OverflowError('source publication revision exhausted')
        self.revision += 1
        binding['scene_admission_ref'] = scene
        binding['source_binding_revision'] = str(self.revision)
        publication_binding(binding)
        self._binding = _canonical(binding)
        return True

    def accept_survey(self, packet, *, grant_raw, lease, source, origin, now_us):
        """등록 M의 현재 보고만 받는다. APPLIED+ADMITTED 이외에는 positive ref를 만들지 않는다."""
        if type(packet) is not DataPacket or packet.kind != 'SURVEY_SUMMARY':
            raise ValueError('actual typed SURVEY_SUMMARY packet required')
        header = packet.header
        binding=self.binding
        if (binding is None or source is None or origin!=self._origin_fields
                or source['vehicle']!=self.policy.vehicle or source['clock_epoch']!=self.raw.fields['clock_epoch']
                or any(source[key]!=binding[key] for key in
                    ('mapper_instance','map_epoch','frame_epoch','odom_epoch','configuration','calibration','primitive'))):
            raise ValueError('survey report changed the pinned source/origin')
        if (header['service_instance'],header['mapper_worker'],header['channel_epoch'],header['sender']) != (
                self.peer.service_instance,self.peer.mapper_worker,self.peer.channel_epoch,'M'):
            raise ValueError('survey report belongs to another registered M/channel')
        if header['metadata'] != dict(publication_sha256=self.binding_sha256, grant_sha256=sha256(grant_raw).hexdigest()):
            raise ValueError('survey report has no current original publication/grant')
        summary, grant = validate_survey_summary(packet.body, grant_raw, lease, source, origin,
            issued_us=packet.issued_us, expires_us=packet.expires_us, now_us=now_us)
        changed = self._scene_binding(scene_ref_fields(grant.scene)) if summary.status == 2 else (
            self.clear_scene() if summary.status == 3 else False)
        return changed, summary

    def accept(self,packet,*,bootstrap,vehicle_wire,vehicle_received_us,now_us,
               current_vehicle_wire=None,restore_material=None):
        """현재 원 sample의 신원만 대조한다. Pi/J 시계 수락이나 pose freshness 판정이 아니다."""
        if type(packet) is not DataPacket or packet.kind!='SOURCE_IDENTITY' or packet.body:
            raise ValueError('bodyless typed source identity proposal required')
        header=packet.header
        if (header['service_instance'],header['mapper_worker'],header['channel_epoch'],header['sender'])!=(
                self.peer.service_instance,self.peer.mapper_worker,self.peer.channel_epoch,'M'):
            raise ValueError('identity proposal belongs to another worker/channel')
        if (type(now_us) is not int or type(vehicle_received_us) is not int
                or not packet.issued_us<=now_us<packet.expires_us
                or not 0<=now_us-vehicle_received_us<1_000_000):
            raise ValueError('identity proposal or original vehicle delivery is no longer current')
        proposal=header['metadata']
        if set(proposal)!=PROPOSAL_FIELDS:
            raise ValueError('identity proposal has unsupported fields')
        if (bootstrap.get('status') not in ('EMPTY','COMPLETE') or not bootstrap.get('allocator_ready')
                or type(bootstrap.get('schema_version')) is not int or bootstrap['schema_version']!=1
                or proposal['database_uuid']!=bootstrap.get('database_uuid')):
            raise ValueError('identity proposal no longer has its admitted current D database')
        original_sample=_sample(vehicle_wire)
        current_sample=_sample(vehicle_wire if current_vehicle_wire is None else current_vehicle_wire)
        origin=('vehicle','host_boot','gateway_instance','device_session','pi_session','odom_epoch','calibration')
        if any(getattr(original_sample,key)!=getattr(current_sample,key) for key in origin):
            raise ValueError('original identity sample belongs to an old current Pi/device/odom source')
        if proposal['vehicle_sample_sha256']!=sha256(vehicle_wire).hexdigest():
            raise ValueError('identity proposal did not reference the original retained VehicleSample')
        nonce=_uuid(proposal['proposal_nonce'])
        original=(header['request_id'],packet.issued_us,packet.expires_us,_canonical(proposal))
        previous=self._nonces.get(nonce)
        if previous is not None:
            if previous!=original:raise ValueError('same identity nonce changed its original body')
            return self.binding
        if len(self._nonces)>=16:raise ValueError('identity proposal registry full; no silent eviction')
        _uuid(proposal['map_epoch']);_uuid(proposal['frame_epoch'])
        for name in ('database_uuid','mapper_instance','odom_epoch','pi_host_boot','device_session','gateway_instance','pi_session'):_uuid(proposal[name])
        if proposal['mapper_instance']!=self.mapper_instance or proposal['raw_binding_sha256']!=self.raw.sha256:
            raise ValueError('M cannot change the S-owned publication incarnation or current raw binding')
        if bootstrap['status']=='COMPLETE':
            from .mapper_bootstrap import BootstrapIdentityEvidence
            if type(restore_material) is not BootstrapIdentityEvidence or not restore_material.current(
                    bootstrap,self.peer,self.mapper_instance,now_us):
                raise ValueError('COMPLETE identity needs matched original LOAD material validation')
        expected=proposal_metadata(raw_binding=self.raw,mapper_instance=self.mapper_instance,
            bootstrap=bootstrap,vehicle_wire=vehicle_wire,policy=self.policy,nonce=nonce,
            map_epoch=proposal['map_epoch'],frame_epoch=proposal['frame_epoch'])
        if proposal!=expected:
            raise ValueError('identity proposal changed original Pi source or approved configuration')
        stable=tuple(proposal[name] for name in ('database_uuid','map_epoch','frame_epoch','odom_epoch',
            'vehicle','configuration','calibration','primitive','pi_host_boot','device_session',
            'gateway_instance','pi_session','checkpoint_manifest_sha256'))
        if self._identity is not None and stable!=self._identity:
            raise ValueError('current incarnation cannot replace its pinned identity through a retry')
        if self._binding is None:
            self.revision+=1
            binding=dict(service_instance=self.peer.service_instance,mapper_worker=self.peer.mapper_worker,
                mapper_instance=self.mapper_instance,raw_binding_sha256=self.raw.sha256,
                map_epoch=proposal['map_epoch'],frame_epoch=proposal['frame_epoch'],odom_epoch=proposal['odom_epoch'],
                configuration=proposal['configuration'],calibration=proposal['calibration'],primitive=proposal['primitive'],
                scene_admission_ref=None,source_binding_revision=str(self.revision))
            publication_binding(binding)
            self._binding=_canonical(binding)
            self._identity=stable
            self._origin_fields={key:proposal[key] for key in ('pi_host_boot','gateway_instance','device_session','pi_session')}
        self._nonces[nonce]=original
        return self.binding
