"""catalog 첫 ID 사용의 versioned geometry scope. 원 job/context/receipt는 바꾸지 않는다.

scene 사용권과 정상 binding revision의 변화는 geometry identity가 아니다. 이 projection은
범위 재사용 비교만 담당하며 원 current job·full context·COMMIT·관측 자격 검사를 대체하지 않는다.
"""

from dataclasses import dataclass
from hashlib import sha256

from .contracts import StorageContext,PersistenceError,canonical,decimal,digest,json_bytes,text,uuid


ORIGIN_PROFILE='xenova-pi-geometry-origin-1'
SCOPE_PROFILE='xenova-catalog-geometry-scope-1'
PUBLICATION_FIELDS=frozenset(('service_instance','mapper_worker','mapper_instance','raw_binding_sha256','map_epoch','frame_epoch',
    'odom_epoch','configuration','calibration','primitive','scene_admission_ref','source_binding_revision'))


@dataclass(frozen=True,slots=True)
class PiGeometryOrigin:
    """S가 보유한 원 VehicleSample의 실제 origin. odom UUID를 session의 대용으로 쓰지 않는다."""
    vehicle: str
    pi_host_boot: str
    gateway_instance: str
    device_session: str
    pi_session: str
    odom_epoch: str
    calibration: str

    def __post_init__(self):
        text(self.vehicle)
        for value in (self.pi_host_boot,self.gateway_instance,self.device_session,self.pi_session,self.odom_epoch):uuid(value)
        digest(self.calibration)

    @property
    def fields(self):
        return dict(profile=ORIGIN_PROFILE,vehicle=self.vehicle,pi_host_boot=self.pi_host_boot,gateway_instance=self.gateway_instance,
            device_session=self.device_session,pi_session=self.pi_session,odom_epoch=self.odom_epoch,calibration=self.calibration)

    @classmethod
    def from_fields(cls,value):
        if type(value) is not dict or set(value)!= {'profile','vehicle','pi_host_boot','gateway_instance','device_session','pi_session','odom_epoch','calibration'} or value['profile']!=ORIGIN_PROFILE:
            raise PersistenceError('exact versioned Pi geometry origin required')
        fields=dict(value);fields.pop('profile')
        return cls(**fields)


@dataclass(frozen=True,slots=True,init=False)
class CatalogGeometryScope:
    """full context SHA를 별도로 보존한다. same_geometry는 whitelist projection의 bytes만 비교한다."""
    context_sha256: str
    _scope: bytes

    @classmethod
    def from_context(cls,context,kind,origin=None):
        if type(context) is not StorageContext or context.geometry_origin is None or kind not in ('LANE','SLOT'):
            raise PersistenceError('original context/kind/actual Pi geometry origin required')
        original=PiGeometryOrigin.from_fields(context.geometry_origin_fields)
        if origin is None:origin=original
        if type(origin) is not PiGeometryOrigin or origin!=original:raise PersistenceError('geometry origin is not the original context proof')
        if (origin.pi_host_boot,origin.odom_epoch,origin.calibration)!=(context.pi_host_boot,context.odom_epoch,context.calibration):
            raise PersistenceError('Pi origin differs from original context host/odom/calibration')
        binding=json_bytes(context.source_binding,2048)
        if type(binding) is not dict or set(binding)!=PUBLICATION_FIELDS:raise PersistenceError('geometry projection requires exact supported PublicationBinding')
        uuid(binding['service_instance']);digest(binding['raw_binding_sha256']);decimal(binding['source_binding_revision'],positive=True)
        if binding['scene_admission_ref'] is not None and type(binding['scene_admission_ref']) is not dict:raise PersistenceError('original scene ref must be object or live-only null')
        expected=dict(mapper_worker=context.mapper_worker,mapper_instance=context.origin_mapper_instance,map_epoch=context.map_epoch,
            frame_epoch=context.frame_epoch,odom_epoch=context.odom_epoch,configuration=context.configuration,
            calibration=context.calibration,primitive=context.primitive_sha256)
        if any(binding[key]!=value for key,value in expected.items()):raise PersistenceError('geometry binding differs from original full context')
        fields=dict(profile=SCOPE_PROFILE,database_uuid=context.database_uuid,map_epoch=context.map_epoch,kind=kind,
            mapper_worker=context.mapper_worker,origin_mapper_instance=context.origin_mapper_instance,service_instance=binding['service_instance'],
            raw_binding_sha256=binding['raw_binding_sha256'],frame_epoch=context.frame_epoch,odom_epoch=context.odom_epoch,
            configuration=context.configuration,calibration=context.calibration,primitive_sha256=context.primitive_sha256,pi_origin=origin.fields)
        raw=canonical(fields)
        if len(raw)>2048:raise PersistenceError('bounded catalog geometry scope exceeded')
        result=object.__new__(cls)
        object.__setattr__(result,'context_sha256',context.sha256);object.__setattr__(result,'_scope',raw)
        return result

    @property
    def fields(self):return json_bytes(self._scope,2048)

    @property
    def bytes(self):return self._scope

    @property
    def sha256(self):return sha256(self._scope).hexdigest()

    def same_geometry(self,other):
        return type(other) is CatalogGeometryScope and self._scope==other._scope


def same_catalog_geometry(original,current,kind):
    """이미 수락한 범위의 소유권 비교. old receipt를 새 full context에서 최초 수락하는 API가 아니다."""
    if type(original) is not StorageContext or type(current) is not StorageContext or kind not in ('LANE','SLOT'):
        raise PersistenceError('exact original/current catalog contexts and kind required')
    if original.bytes==current.bytes:return True
    if original.geometry_origin is None or current.geometry_origin is None:return False
    try:return CatalogGeometryScope.from_context(original,kind).same_geometry(CatalogGeometryScope.from_context(current,kind))
    except PersistenceError:return False
