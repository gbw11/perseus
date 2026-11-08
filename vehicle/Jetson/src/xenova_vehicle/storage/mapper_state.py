"""manifest.scene.mapper_state가 명시 참조한 유한 META page의 순수 bytes codec.

wire/SQL schema를 바꾸지 않는다. 실제 source/scene 승인은 하지 않으며 알 수 없는
metadata를 실행하거나 새 관측으로 해석하지 않는다. 구형 manifest bytes는 그대로다.
"""

from base64 import b64decode,b64encode
from hashlib import sha256
import math
import zlib

from .contracts import PersistenceError,canonical,digest,json_bytes,uint


STATE_CAP=512*1024
CHUNK_BYTES=2304
MAX_PAGES=48
REF_FIELDS=frozenset(('profile','encoding','state_sha256','state_length','compressed_length','pages'))
PAGE_FIELDS=frozenset(('profile','state_sha256','state_length','compressed_length','index','count','chunk_b64'))


def mapper_state_reference(scene):
    """새 local extension만 검사한다. 기존 scene/survey의 다른 필드는 변경하지 않는다."""
    if scene is not None and type(scene) is not dict:raise PersistenceError('original scene object required')
    if scene is None or 'mapper_state' not in scene:return None
    ref=scene['mapper_state']
    if (type(ref) is not dict or set(ref)!=REF_FIELDS or ref['profile']!='mapper-state-1'
            or ref['encoding']!='ZLIB_CANONICAL_JSON_1'):
        raise PersistenceError('unsupported exact mapper-state reference')
    digest(ref['state_sha256']);uint(ref['state_length'],STATE_CAP,positive=True)
    uint(ref['compressed_length'],CHUNK_BYTES*MAX_PAGES,positive=True)
    pages=ref['pages'];count=(ref['compressed_length']+CHUNK_BYTES-1)//CHUNK_BYTES
    if type(pages) is not list or len(pages)!=count or not 1<=count<=MAX_PAGES:
        raise PersistenceError('mapper-state page count/cap')
    for index,item in enumerate(pages):
        if type(item) is not list or len(item)!=2 or type(item[0]) is not int or item[0]!=index:
            raise PersistenceError('ordered exact mapper-state page reference required')
        digest(item[1])
    if len({item[1] for item in pages})!=count:raise PersistenceError('duplicate mapper-state page reference')
    return ref


def encode_mapper_state(state):
    """canonical 상태를 최대48개의 실제 ContextRecord로 만든다. cap 초과는 명시 실패다."""
    from .records import ContextRecord
    raw=canonical(state)
    if not 0<len(raw)<=STATE_CAP:raise PersistenceError('mapper state exceeds bounded metadata capacity')
    if type(state) is dict and ('atlas' in state or 'hazard' in state):
        checked=json_bytes(raw,STATE_CAP);_validate_atlas_state(checked)
        # outer spec의 tuple 정규화와 달리 hazard는 exact native JSON 문법을 지킨다.
        if 'hazard' in state:validate_hazard_state(state['hazard'],state=checked)
    compressed=zlib.compress(raw,6)
    if len(compressed)>CHUNK_BYTES*MAX_PAGES:raise PersistenceError('mapper-state compressed page capacity exceeded')
    key=sha256(raw).hexdigest();count=(len(compressed)+CHUNK_BYTES-1)//CHUNK_BYTES
    records=[]
    for index in range(count):
        body=dict(profile='mapper-state-page-1',state_sha256=key,state_length=len(raw),compressed_length=len(compressed),
            index=index,count=count,chunk_b64=b64encode(compressed[index*CHUNK_BYTES:(index+1)*CHUNK_BYTES]).decode('ascii'))
        records.append(ContextRecord(canonical(dict(kind='METRIC',body=body))))
    ref=dict(profile='mapper-state-1',encoding='ZLIB_CANONICAL_JSON_1',state_sha256=key,state_length=len(raw),
        compressed_length=len(compressed),pages=[[index,record.sha256] for index,record in enumerate(records)])
    mapper_state_reference(dict(mapper_state=ref))
    return ref,tuple(records)


def decode_mapper_state(scene,contexts):
    """원 manifest가 참조한 모든 page만 합친다. zlib 출력도 선언 cap을 넘기지 않는다."""
    ref=mapper_state_reference(scene)
    if ref is None:return None,()
    by_sha={record.sha256:record for record in contexts};selected=[];chunks=[]
    for index,key in ref['pages']:
        record=by_sha.get(key)
        if record is None:raise PersistenceError('missing referenced mapper-state page')
        value=json_bytes(record.payload,4096);body=value['body']
        if (value['kind']!='METRIC' or type(body) is not dict or set(body)!=PAGE_FIELDS
                or body['profile']!='mapper-state-page-1' or body['index']!=index or type(body['index']) is not int
                or body['count']!=len(ref['pages']) or type(body['count']) is not int
                or any(type(body[name]) is not int for name in ('state_length','compressed_length'))
                or any(body[name]!=ref[name] for name in ('state_sha256','state_length','compressed_length'))
                or type(body['chunk_b64']) is not str):
            raise PersistenceError('mapper-state page differs from original manifest reference')
        try:chunk=b64decode(body['chunk_b64'],validate=True)
        except (ValueError,TypeError) as error:raise PersistenceError('invalid mapper-state page encoding') from error
        if (not 0<len(chunk)<=CHUNK_BYTES or index<len(ref['pages'])-1 and len(chunk)!=CHUNK_BYTES
                or b64encode(chunk).decode('ascii')!=body['chunk_b64']):
            raise PersistenceError('noncanonical mapper-state chunk length/encoding')
        chunks.append(chunk);selected.append(record)
    compressed=b''.join(chunks)
    if len(compressed)!=ref['compressed_length']:raise PersistenceError('mapper-state compressed length mismatch')
    try:
        decoder=zlib.decompressobj();raw=decoder.decompress(compressed,ref['state_length']+1)
        if (len(raw)!=ref['state_length'] or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
                or sha256(raw).hexdigest()!=ref['state_sha256']):
            raise PersistenceError('mapper-state decompression length/hash mismatch')
    except zlib.error as error:raise PersistenceError('corrupt mapper-state compressed bytes') from error
    state=json_bytes(raw,STATE_CAP)
    _validate_atlas_state(state)
    if type(state) is dict and 'hazard' in state:validate_hazard_state(state['hazard'],state=state)
    return state,tuple(selected)


def _validate_atlas_state(state):
    """선택형 좌표 설명만 검사한다. body/proof ID나 source/Scene/복원 자격을 만들지 않는다."""
    if type(state) is not dict or 'atlas' not in state:return
    def exact(value,names):
        return (type(value) is dict and len(value)==len(names)
            and all(type(key) is str for key in value) and set(value)==names)
    def number(value):
        if type(value) not in (int,float):raise PersistenceError('finite native atlas number required')
        if type(value) is int and not -(1<<63)<value<1<<63:
            raise PersistenceError('native atlas integer exceeds canonical range')
        try:valid=math.isfinite(value)
        except OverflowError:valid=False
        if not valid:raise PersistenceError('finite native atlas number required')
        return value
    def pair(value):
        if type(value) is not list or len(value)!=2:raise PersistenceError('exact two-element atlas JSON array required')
        return value
    atlas=state['atlas'];spec=state.get('spec')
    names={'profile','lattice_origin_xy_m','roi_origin_cell','tile_cells','live_restore','policy_sha256'}
    if (type(state.get('profile')) is not str or state['profile']!='mapper-pipeline-state-1'
            or not exact(atlas,names) or type(atlas['profile']) is not str or atlas['profile']!='atlas-rolling-1'
            or type(atlas['tile_cells']) is not int or atlas['tile_cells']!=64
            or type(atlas['live_restore']) is not str or atlas['live_restore']!='RETAINED_EVIDENCE_ONLY'):
        raise PersistenceError('unsupported exact atlas metadata profile')
    digest(atlas['policy_sha256'])
    if not exact(spec,{'width','height','resolution_m','origin_xy'}):raise PersistenceError('exact original atlas grid spec required')
    for size in (spec['width'],spec['height']):
        if type(size) is not int or not 2<=size<=64:raise PersistenceError('atlas grid must fit2..64 cells per side')
    resolution=number(spec['resolution_m'])
    if not .005<=resolution<=1:raise PersistenceError('atlas resolution exceeds original grid profile')
    anchor=tuple(number(value) for value in pair(atlas['lattice_origin_xy_m']))
    offset=pair(atlas['roi_origin_cell'])
    for value,size in zip(offset,(spec['width'],spec['height'])):
        if (type(value) is not int or not -(1<<31)*64<=value
                or value+size-1>(1<<31)*64-1):
            raise PersistenceError('atlas window exceeds signed int32 tile address')
    origin=tuple(number(value) for value in pair(spec['origin_xy']))
    try:expected=tuple(math.fsum((value,index*resolution)) for value,index in zip(anchor,offset))
    except (OverflowError,ValueError) as error:raise PersistenceError('atlas origin arithmetic is not finite') from error
    if any(not math.isfinite(value) for value in expected) or origin!=expected:
        raise PersistenceError('atlas ROI origin differs from the immutable shared lattice')


def validate_hazard_state(value,*,state):
    """원 occupied와 발급 revision만 해석한다. LIVE/source/시계/Scene 권한은 없다.

    좌표는 같은 META의 spec/atlas에 종속한다. fixed는64칸 stride의(0,0)
    tile 하나이며 ROI 밖 bit는0이다. 반환 body는 canonical base64의 소유 bytes다.
    """
    names={'profile','revision','method_manifest_sha256','tiles'}
    if (type(state) is not dict or type(state.get('profile')) is not str
            or state['profile']!='mapper-pipeline-state-1' or type(value) is not dict
            or any(type(key) is not str for key in value) or set(value)!=names
            or type(value['profile']) is not str or value['profile']!='mapper-hazard-state-1'):
        raise PersistenceError('unsupported exact hazard metadata profile')
    revision=uint(value['revision'],positive=True);digest(value['method_manifest_sha256'])
    def finite(number):
        if type(number) not in (int,float):return False
        if type(number) is int and not -(1<<63)<number<1<<63:return False
        return math.isfinite(number)
    spec=state.get('spec')
    if (type(spec) is not dict or set(spec)!={'width','height','resolution_m','origin_xy'}
            or any(type(key) is not str for key in spec)):
        raise PersistenceError('original hazard grid spec required')
    for name in ('width','height'):
        if type(spec[name]) is not int or not 2<=spec[name]<=64:
            raise PersistenceError('hazard grid must fit2..64 cells per side')
    resolution=spec['resolution_m'];origin=spec['origin_xy']
    if (not finite(resolution) or not .005<=resolution<=1
            or type(origin) is not list or len(origin)!=2
            or any(not finite(v) for v in origin)):
        raise PersistenceError('finite original hazard grid coordinates required')
    rolling='atlas' in state
    if rolling:_validate_atlas_state(state)
    rows=value['tiles']
    if type(rows) is not list or len(rows)>128:raise PersistenceError('hazard tile capacity128 exceeded')
    result=[];previous=None
    for row in rows:
        if (type(row) is not list or len(row)!=3 or any(type(v) is not int or not -(1<<31)<=v<1<<31 for v in row[:2])
                or type(row[2]) is not str or len(row[2])!=684):
            raise PersistenceError('bounded int32 hazard tile and512-byte base64 body required')
        key=tuple(row[:2])
        if previous is not None and key<=previous:raise PersistenceError('hazard tiles must be sorted and unique')
        try:body=b64decode(row[2],validate=True)
        except (ValueError,TypeError) as error:raise PersistenceError('invalid hazard bitset encoding') from error
        if len(body)!=512 or b64encode(body).decode('ascii')!=row[2] or not any(body):
            raise PersistenceError('canonical nonempty512-byte hazard bitset required')
        if not rolling:
            if key!=(0,0):raise PersistenceError('fixed hazard has only the original zero tile')
            for y in range(64):
                bits=int.from_bytes(body[y*8:y*8+8],'little')
                if (bits if y>=spec['height'] else bits>>spec['width']):
                    raise PersistenceError('fixed hazard bit lies outside the original grid')
        result.append((key,body));previous=key
    return revision,value['method_manifest_sha256'],tuple(result)


def _road_basis_roots(value):
    """road-basis-1의 명시 ID 문법만 읽는다. model/source 내부를 실행·보정하지 않는다."""
    names={'profile','geometry_scope','reference_id','reference_revision','points_f32','point_error_m',
           'segment_error_m','events','latest','handoff'}
    model_names={'road','source','lane_ids','lane_revisions','model_revision','crossing','provenance_ids','original_context_sha256'}
    def exact(item,names):
        return (type(item) is dict and len(item)==len(names) and all(type(k) is str for k in item)
                and set(item)==names)
    def finite(item,*,positive=False):
        if type(item) not in (int,float):raise PersistenceError('bounded finite road metadata scalar required')
        try:valid=math.isfinite(item)
        except OverflowError:valid=False
        if not valid or item<0 or positive and item==0:raise PersistenceError('bounded finite road metadata scalar required')
    if not exact(value,names) or type(value['profile']) is not str or value['profile']!='road-basis-1':
        raise PersistenceError('unsupported exact road basis metadata profile')
    if value['geometry_scope'] is not None and type(value['geometry_scope']) is not dict:
        raise PersistenceError('original road geometry metadata object required')
    identity=value['reference_id']
    if identity is not None and (type(identity) is not str or not 1<=len(identity)<=64 or any(not 33<=ord(c)<=126 for c in identity)):
        raise PersistenceError('bounded original road reference label required')
    uint(value['reference_revision'])
    points=value['points_f32']
    if (type(points) is not str or len(points)>1024 or len(points)%16
            or any(c not in '0123456789abcdef' for c in points)):
        raise PersistenceError('bounded original road float32 byte string required')
    count=len(points)//16
    for name,length in (('point_error_m',count),('segment_error_m',max(0,count-1))):
        column=value[name]
        if type(column) is not list or len(column)!=length:raise PersistenceError('road metadata error column shape mismatch')
        for item in column:finite(item)
    events=value['events']
    if type(events) is not list or len(events)>64:raise PersistenceError('road append metadata exceeds64 events')
    roots=[]
    def model(item):
        if not exact(item,model_names):raise PersistenceError('exact original road model metadata required')
        ids=item['provenance_ids']
        if type(ids) not in (list,tuple) or len(ids)!=2:raise PersistenceError('two explicit original road provenance IDs required')
        roots.extend(uint(identity,positive=True) for identity in ids)
    for event in events:
        if not exact(event,{'model','resolution_m'}):raise PersistenceError('exact original road append metadata required')
        finite(event['resolution_m'],positive=True);model(event['model'])
    if value['latest'] is not None:model(value['latest'])
    handoff=value['handoff']
    if handoff is not None:
        if not exact(handoff,{'model','resolution_m','reason'}):raise PersistenceError('exact original road handoff metadata required')
        finite(handoff['resolution_m'],positive=True)
        reason=handoff['reason']
        if type(reason) is not str or not 1<=len(reason)<=64 or any(not 33<=ord(c)<=126 for c in reason):raise PersistenceError('bounded original handoff reason required')
        model(handoff['model'])
    return roots


def mapper_state_roots(state):
    """명시 versioned META의 원 ID union만 반환한다. LOAD/geometry/현재성 승인은 아니다."""
    if state is None:return ()
    if type(state) is not dict:raise PersistenceError('mapper state must preserve an exact object')
    _validate_atlas_state(state)
    if 'hazard' in state:validate_hazard_state(state['hazard'],state=state)
    if state.get('profile')!='mapper-pipeline-state-1':
        if {'reference_provenance','alignment_provenance'}&set(state):raise PersistenceError('unknown metadata proof-root profile')
        roots=[]
    else:
        refs=state.get('reference_provenance')
        if type(refs) is not dict or len(refs)>64:raise PersistenceError('bounded original reference proof roots required')
        roots=[]
        for key,value in refs.items():digest(key);roots.append(uint(value,positive=True))
        alignment=state.get('alignment_provenance')
        if alignment is not None:roots.append(uint(alignment,positive=True))
    if 'road_basis' in state:roots.extend(_road_basis_roots(state['road_basis']))
    return tuple(sorted(set(roots)))
