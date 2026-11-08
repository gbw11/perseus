"""road-basis-1: 관측한 열린 divider의 방향/prefix를 고정한다.

ego lane·회피 path·등록 FrozenReference가 아니며 scene/metric/이동 권위는 없다.
오차 tube의 가능한 seam 전체를 비교한다. 기존 prefix는 이동/정렬/감축하지 않고
모호함·재방문·64점 상한은 명시 section handoff가 필요한 HOLD로 보존한다.
"""

from dataclasses import dataclass
from fractions import Fraction as F
import math
import struct
from uuid import UUID

from .lane_catalog import LaneCatalog,LaneModelCandidates
from ..perception.inputs import FrameStamp
from ..storage.contracts import StorageContext,canonical,json_bytes,uint
from ..storage.records import PROVENANCE


POINT=struct.Struct('<ff')
STATE_CAP=512*1024


def _scope(catalog):
    context=catalog.context
    # M/raw/scene incarnation은 원 history에 남는다. MAP 물리 좌표의 연속성과 alias하지 않는다.
    return dict(profile='road-basis-geometry-1',database_uuid=context.database_uuid,map_epoch=context.map_epoch,
        frame_id=catalog.frame_id,frame_epoch=context.frame_epoch,odom_epoch=context.odom_epoch,pi_host_boot=context.pi_host_boot,
        configuration=context.configuration,calibration=context.calibration,primitive_sha256=context.primitive_sha256,
        calibration_profile_sha256=catalog.calibration_id,pi_geometry_origin=context.geometry_origin_fields)


def _id(map_epoch,ids):
    return 'rb1:'+map_epoch+':'+format(ids[0],'08x')+':'+format(ids[1],'08x')


def _pack(points,error):
    raw=b''.join(POINT.pack(*p) for p in points);stored=tuple(POINT.iter_unpack(raw))
    if any(not math.isfinite(v) for p in stored for v in p):raise ValueError('road basis exceeds finite float32')
    errors=tuple(math.nextafter(error+math.nextafter(math.dist(p,q),math.inf),math.inf) for p,q in zip(points,stored))
    return raw,stored,errors


def _distance2(p,a,b):
    p,a,b=tuple(map(F,p)),tuple(map(F,a)),tuple(map(F,b))
    dx,dy=b[0]-a[0],b[1]-a[1];den=dx*dx+dy*dy
    t=F(0) if not den else max(F(0),min(F(1),((p[0]-a[0])*dx+(p[1]-a[1])*dy)/den))
    return (p[0]-a[0]-t*dx)**2+(p[1]-a[1]-t*dy)**2


def _contact(a,b,c,d,pad):
    """빠른 외접 box 배제 뒤 exact binary-float 유리수로 tube 접촉을 판정한다."""
    pad=math.nextafter(pad,math.inf)
    if any(math.nextafter(max(a[k],b[k])+pad,math.inf)<min(c[k],d[k])
           or math.nextafter(max(c[k],d[k])+pad,math.inf)<min(a[k],b[k]) for k in (0,1)):
        return False
    def orient(a,b,c):return (F(b[0])-F(a[0]))*(F(c[1])-F(a[1]))-(F(b[1])-F(a[1]))*(F(c[0])-F(a[0]))
    ab=(orient(a,b,c),orient(a,b,d));cd=(orient(c,d,a),orient(c,d,b))
    if ab[0]*ab[1]<=0 and cd[0]*cd[1]<=0:
        # 공선의 서로 먼 선분은 위 box에서 이미 배제했다.
        if any(ab) or any(cd) or all(max(min(a[k],b[k]),min(c[k],d[k]))<=min(max(a[k],b[k]),max(c[k],d[k])) for k in (0,1)):
            return True
    return min(_distance2(a,c,d),_distance2(b,c,d),_distance2(c,a,b),_distance2(d,a,b))<=F(pad)**2


def _revisit(points,errors,resolution,segments=None):
    if points[0]==points[-1]:return True
    arc=[0.]
    for a,b in zip(points,points[1:]):arc.append(arc[-1]+math.dist(a,b))
    if len(points)>2 and arc[-1]>resolution+errors[0]+errors[-1] and _contact(
            points[0],points[0],points[-1],points[-1],errors[0]+errors[-1]):return True
    for i in range(len(points)-1):
        for j in range(i+2,len(points)-1):
            a,b,c,d=points[i],points[i+1],points[j],points[j+1]
            if _contact(a,b,c,d,0.):return True
            pad=(max(errors[i:i+2])+max(errors[j:j+2]) if segments is None else segments[i]+segments[j])
            # 이웃 station의 오차 접촉은 재방문과 구분한다. 새 해상도/threshold는 발급하지 않는다.
            if arc[j]-arc[i+1]>resolution+pad and _contact(a,b,c,d,pad):return True
    return False


def _components(pairs):
    remaining=set(pairs);groups=[]
    while remaining:
        first=min(remaining);remaining.remove(first);group={first};pending=[first]
        while pending:
            i,j=pending.pop()
            neighbors={(i+di,j+dj) for di in (-1,0,1) for dj in (-1,0,1)}&remaining
            remaining-=neighbors;group|=neighbors;pending.extend(neighbors)
        groups.append(group)
    return groups


@dataclass(frozen=True)
class RoadBasisSnapshot:
    status: str
    reason: str | None
    reference_id: str | None
    reference_revision: int
    points_f32: bytes
    point_error_m: tuple
    segment_error_m: tuple
    lane_ids: tuple
    origin_source: object
    latest_lane_source: object
    latest_model_revision: int
    roots: tuple

    @property
    def points(self):return tuple(POINT.iter_unpack(self.points_f32))

    @property
    def geometry_error_m(self):return max((*self.point_error_m,*self.segment_error_m),default=0.)


class RoadBasis:
    """하나의 열린 section owner. handoff를 자동 시작하거나 새 ID로 우회하지 않는다."""

    def __init__(self):
        self._scope=None;self._ids=();self._raw=b'';self._errors=();self._segments=();self._events=[];self._latest=None
        self._handoff=None;self._status='WAIT_CURRENT';self._reason='CURRENT_LANE_REQUIRED';self.revision=0

    @property
    def roots(self):
        return tuple(sorted(self.originals()))

    def originals(self):
        """원 prefix/append/latest/handoff의 증거 ID→원 model. fresh provenance를 발급하지 않는다."""
        models=[model for model,_ in self._events]
        if self._latest is not None:models.append(self._latest)
        if self._handoff is not None:models.append(self._handoff[0])
        result={}
        for model in models:
            for root in model.provenance_ids:
                old=result.get(root)
                if old is not None and old is not model and old.fields()!=model.fields():raise ValueError('road provenance ID changed original model')
                result[root]=model
        return result

    def snapshot(self):
        origin=None if not self._events else self._events[0][0].source
        return RoadBasisSnapshot(self._status,self._reason,None if not self._ids else _id(self._scope['map_epoch'],self._ids),
            self.revision,self._raw,self._errors,self._segments,self._ids,origin,None if self._latest is None else self._latest.source,
            0 if self._latest is None else self._latest.model_revision,self.roots)

    def _hold(self,model,resolution,reason):
        if self._handoff is None:self._handoff=(model,resolution,reason)
        self._status='SECTION_HANDOFF_REQUIRED';self._reason=self._handoff[2]

    def _apply(self,model,resolution):
        h=model.road.hypotheses[0];raw,points,errors=_pack(h.divider,h.error_m)
        if _revisit(points,errors,resolution):self._hold(model,resolution,'CLOSED_OR_REVISITED_ROAD');return
        if not self._raw:
            if math.dist(points[0],points[-1])<=resolution+errors[0]+errors[-1]:
                self._hold(model,resolution,'DIRECTION_OR_SPAN_UNRESOLVED');return
            self._ids=model.lane_ids;self._raw=raw;self._errors=errors
            self._segments=tuple(max(a,b) for a,b in zip(errors,errors[1:]));self._events.append((model,resolution));self.revision=1
            self._status='READY';self._reason=None;return
        old=tuple(POINT.iter_unpack(self._raw))
        if model.lane_ids!=self._ids:self._hold(model,resolution,'DIFFERENT_LANE_PAIR');return
        pairs=[(i,j) for i in range(len(old)-1) for j in range(len(points)-1)
            if _contact(old[i],old[i+1],points[j],points[j+1],self._segments[i]+max(errors[j:j+2]))]
        groups=_components(pairs)
        if len(groups)!=1:self._hold(model,resolution,'AMBIGUOUS_OR_MISSING_OVERLAP');return
        group=groups[0];oi=sorted({i for i,_ in group});nj=sorted({j for _,j in group})
        a,b=old[oi[0]],old[oi[-1]+1];c,d=points[nj[0]],points[nj[-1]+1]
        va=(b[0]-a[0],b[1]-a[1]);vb=(d[0]-c[0],d[1]-c[1]);la,lb=math.hypot(*va),math.hypot(*vb)
        ea,eb=max(self._segments[oi[0]:oi[-1]+1]),max(errors[nj[0]:nj[-1]+2])
        if min(la,lb)<=resolution+2*(ea+eb):self._hold(model,resolution,'OVERLAP_SPAN_UNRESOLVED');return
        yaw=max(event[0].road.hypotheses[0].yaw_error_rad for event in self._events)+h.yaw_error_rad
        angle=abs(math.atan2(va[0]*vb[1]-va[1]*vb[0],va[0]*vb[0]+va[1]*vb[1]))
        uncertainty=yaw+math.asin(min(1.,2*ea/la))+math.asin(min(1.,2*eb/lb))
        if angle+uncertainty>=math.pi/2:self._hold(model,resolution,'DIRECTION_UNRESOLVED');return
        if oi[-1]!=len(old)-2:
            self._status='READY';self._reason=None;return
        # 실제 prefix 끝과 접촉하는 가능한 모든 seam을 남긴다. nearest는 고르지 않는다.
        seam=[j for j in nj if _contact(old[-1],old[-1],points[j],points[j+1],self._errors[-1]+max(errors[j:j+2]))]
        if not seam or seam!=list(range(min(seam),max(seam)+1)):
            self._hold(model,resolution,'AMBIGUOUS_END_SEAM');return
        tangent=(va[0]/la,va[1]/la)
        noise=math.nextafter(math.nextafter(resolution+self._errors[-1],math.inf)+max(errors),math.inf)
        first=None
        for k in range(min(seam),len(points)):
            delta=(points[k][0]-old[-1][0],points[k][1]-old[-1][1])
            if delta[0]*tangent[0]+delta[1]*tangent[1]>noise:first=k;break
            if k>max(seam) and math.hypot(*delta)>noise:
                self._hold(model,resolution,'UNOBSERVED_OR_AMBIGUOUS_SEAM');return
        if first is None:self._status='READY';self._reason=None;return
        if first<=max(seam):self._hold(model,resolution,'AMBIGUOUS_END_SEAM');return
        added=points[first:];added_errors=errors[first:]
        if len(old)+len(added)>64:self._hold(model,resolution,'POINT_CAP');return
        combined=old+added;combined_errors=self._errors+added_errors
        # seam 접속의 보수 pad는 첫 새 선분에만 둔다. 새 끝점 원 오차를 누적 drift로 바꾸지 않는다.
        segment_errors=self._segments+(math.nextafter(noise+max(self._errors[-1],added_errors[0]),math.inf),)+tuple(
            max(a,b) for a,b in zip(added_errors,added_errors[1:]))
        if _revisit(combined,combined_errors,resolution,segment_errors):self._hold(model,resolution,'CLOSED_OR_REVISITED_ROAD');return
        self._raw+=raw[first*POINT.size:];self._errors=combined_errors;self._segments=segment_errors;self._events.append((model,resolution))
        self.revision+=1;self._status='READY';self._reason=None

    def observe(self,catalog,currentcandidate,*,current_stamp,resolution_m,now_us,max_age_us):
        """같은 draft의 실제 update 반환과 원 frame을 받는다. old latest는 current가 아니다."""
        if type(catalog) is not LaneCatalog or type(currentcandidate) is not LaneModelCandidates or type(current_stamp) is not FrameStamp:
            raise ValueError('actual current LaneCatalog/model/original frame required')
        if type(resolution_m) not in (int,float) or not math.isfinite(resolution_m) or resolution_m<=0:
            raise ValueError('explicit positive grid resolution required')
        uint(now_us);uint(max_age_us,positive=True)
        model=currentcandidate;age=model.age_us(now_us,catalog.clock_id)
        # latest는 UNKNOWN 뒤에도 남는다. 원 실제 association까지 같은 update인지 확인한다.
        association=catalog.current_association
        current=(catalog.latest is model and any(m is model for m in catalog.models)
            and model.model_revision==catalog.revision and model.original_context_sha256==catalog.context.sha256
            and association is not None and association.status in ('NEW','UNIQUE')
            and association.source is model.source and association.catalog_revision+1==model.model_revision
            and model.source.stamp==current_stamp
            and model.source.domain==(catalog.calibration_id,catalog.context.map_epoch,catalog.frame_id,catalog.context.odom_epoch)
            and model.source.qualified and model.road.calibration_qualified and age is not None and age<=max_age_us
            and now_us>=model.source.stamp.available_us and catalog.durable_hwm is not None
            and all(identity<catalog.durable_hwm for identity in model.lane_ids)
            and all(record in catalog.checkpoint_records() for record in model.records()))
        if not current:
            if self._handoff is None:self._status='WAIT_CURRENT';self._reason='CURRENT_LANE_REQUIRED'
            return self.snapshot()
        scope=_scope(catalog)
        if self._scope is not None and scope!=self._scope:
            self._hold(model,resolution_m,'GEOMETRY_SCOPE_CHANGED');return self.snapshot()
        if self._scope is None:self._scope=scope
        if self._latest is not None and model.source.stamp.key==self._latest.source.stamp.key:
            if model.fields()!=self._latest.fields():raise ValueError('same road source changed original model')
            if self._handoff is None:self._status='READY';self._reason=None
            return self.snapshot()
        if not self._ids or model.lane_ids==self._ids:self._latest=model
        if self._handoff is None:self._apply(model,resolution_m)
        return self.snapshot()

    def state_fields(self):
        value=dict(profile='road-basis-1',geometry_scope=self._scope,reference_id=None if not self._ids else _id(self._scope['map_epoch'],self._ids),
            reference_revision=self.revision,points_f32=self._raw.hex(),point_error_m=self._errors,segment_error_m=self._segments,
            events=[dict(model=model.fields(),resolution_m=resolution) for model,resolution in self._events],
            latest=None if self._latest is None else self._latest.fields(),handoff=None if self._handoff is None else
            dict(model=self._handoff[0].fields(),resolution_m=self._handoff[1],reason=self._handoff[2]))
        raw=canonical(value)
        if len(raw)>STATE_CAP:raise ValueError('road basis state exceeds existing mapper-state cap')
        return json_bytes(raw,STATE_CAP)

    def restore(self,complete,catalog,*,evidence_scope,now_us,state_key='road_basis'):
        """원 SourceComplete/state/증거로만 복원한다. 현재성은 새 observe까지 WAIT다."""
        from ..runtime.map_checkpoint import SourceComplete
        from ..runtime.scene import SceneEvidenceScope
        if (self._scope is not None or type(complete) is not SourceComplete or complete.state is None
                or type(catalog) is not LaneCatalog or type(evidence_scope) is not SceneEvidenceScope):
            raise ValueError('fresh owner and actual complete/source scope required')
        uint(now_us)
        evidence_scope=evidence_scope.with_complete_history(complete,now_us=now_us)
        state=json_bytes(complete.state,STATE_CAP);value=state.get(state_key)
        names={'profile','geometry_scope','reference_id','reference_revision','points_f32','point_error_m','segment_error_m','events','latest','handoff'}
        if (type(value) is not dict or set(value)!=names or value['profile']!='road-basis-1'
                or type(value['events']) is not list or len(value['events'])>64):
            raise ValueError('exact referenced road basis state/geometry required')
        if value['geometry_scope'] is None:
            if canonical(value)!=canonical(self.state_fields()):raise ValueError('nonempty road basis has no original geometry scope')
            return self.snapshot()
        if value['geometry_scope']!=_scope(catalog):raise ValueError('restored road geometry scope differs')
        if (evidence_scope.calibration.sha256!=catalog.calibration_id
                or any(getattr(evidence_scope.context,key)!=getattr(catalog.context,key) for key in
                    ('database_uuid','map_epoch','frame_epoch','odom_epoch','pi_host_boot','configuration','calibration','primitive_sha256'))
                or evidence_scope.context.geometry_origin!=catalog.context.geometry_origin):
            raise ValueError('actual source evidence scope differs from restored road geometry')
        actual_hwm=next(value for kind,value,_ in complete.page_receipts[-1][3].heads if kind=='LANE')
        if catalog.durable_hwm!=actual_hwm:raise ValueError('road restore lost its actual D LOAD head')
        records={r.provenance_id:r for r in complete.evidence.provenance};contexts={c.sha256:c for c in complete.evidence.contexts}
        def original(fields):
            try:model=LaneModelCandidates.from_fields(fields)
            except (ValueError,TypeError,KeyError,OverflowError) as error:raise ValueError('invalid original road model') from error
            if model.source.domain!=(catalog.calibration_id,catalog.context.map_epoch,catalog.frame_id,catalog.context.odom_epoch):
                raise ValueError('historical road source geometry differs')
            if (not model.source.qualified or not model.road.calibration_qualified
                    or model.source.stamp.captured_us is None or model.model_revision>catalog.revision):
                raise ValueError('original qualified observed road source required')
            if catalog.durable_hwm is None or any(i>=catalog.durable_hwm for i in model.lane_ids):raise ValueError('D-issued lane IDs missing')
            raw_source=False;seen=set();pending=list(model.provenance_ids)
            while pending:
                identity=pending.pop()
                if identity in seen:continue
                seen.add(identity);record=records.get(identity);context=None if record is None else contexts.get(record.context_sha256)
                if context is None:raise ValueError('original road ancestor/context closure missing')
                evidence_scope.record_sources(record,context)
                value=json_bytes(context.payload,4096);body=value['body'];data=PROVENANCE.unpack(record.payload)
                raw=body['raw_binding']
                if value['kind'] in ('RAW','METRIC') and (raw['acquisition_worker'],raw['clock_epoch'],raw['stream_instances']['rgb_video'])==(
                        model.source.stamp.source_id,model.source.stamp.clock_id,model.source.stamp.stream_id):
                    for at in range(data[3]):
                        stream,sequence,_=struct.unpack_from('<16sQQ',data[-3],at*32)
                        if stream==UUID(model.source.stamp.stream_id).bytes and sequence==model.source.stamp.sequence:raw_source=True
                pending.extend(record.parents)
            if not raw_source:raise ValueError('road model has no original RGB source/sequence evidence')
            for identity in model.provenance_ids:
                record=records.get(identity);context=None if record is None else contexts.get(record.context_sha256)
                if context is None:raise ValueError('original road provenance/context closure missing')
                evidence_scope.record_sources(record,context)
                body=json_bytes(context.payload,4096)['body'];source=StorageContext.from_fields(body['storage_context'])
                if (source.sha256!=model.original_context_sha256
                        or body['calibration_profile_sha256']!=model.source.calibration_id
                        or body['input_kind']!=model.source.input_kind):raise ValueError('road model changed original full context/asset')
            return model
        staged=RoadBasis();staged._scope=value['geometry_scope']
        for event in value['events']:
            if type(event) is not dict or set(event)!={'model','resolution_m'}:raise ValueError('exact road append event required')
            model=original(event['model']);resolution=event['resolution_m']
            if type(resolution) not in (int,float) or not math.isfinite(resolution) or resolution<=0:raise ValueError('original resolution required')
            if staged._events:
                previous=staged._events[-1][0]
                if model.model_revision<=previous.model_revision or any(a<=b for a,b in zip(model.lane_revisions,previous.lane_revisions)):
                    raise ValueError('road append model/lane revisions regressed')
            before=staged.revision;staged._apply(model,resolution)
            if staged.revision!=before+1 or staged._handoff is not None:raise ValueError('stored road event is not an actual append')
        latest=None if value['latest'] is None else original(value['latest'])
        if latest is not None:
            matches=[m for m in catalog.models if m.lane_ids==latest.lane_ids]
            actual={r.stable_id:r for r in complete.evidence.catalog if r.kind=='LANE'}
            if len(matches)!=1 or matches[0].fields()!=latest.fields() or any(actual.get(r.stable_id)!=r for r in latest.records()):
                raise ValueError('actual LANE1/current restored model differs from road state')
            staged._latest=latest
        if value['handoff'] is not None:
            event=value['handoff']
            if type(event) is not dict or set(event)!={'model','resolution_m','reason'}:raise ValueError('exact handoff cause required')
            resolution=event['resolution_m']
            if type(resolution) not in (int,float) or not math.isfinite(resolution) or resolution<=0:raise ValueError('original handoff resolution required')
            model=original(event['model']);staged._apply(model,resolution)
            if staged._handoff is None or staged._handoff[2]!=event['reason']:raise ValueError('original handoff cause changed')
        if canonical(staged.state_fields())!=canonical(value):raise ValueError('road prefix/revision/float32/error/source state changed')
        self.__dict__.update(staged.__dict__)
        if self._handoff is None:self._status='WAIT_CURRENT';self._reason='RESTORED_REOBSERVATION_REQUIRED'
        return self.snapshot()
