"""PC 진단 파일만 읽는 유한 관측 캐시. 제품 제어·권한·시계 변환을 만들지 않는다."""
from collections import OrderedDict
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path, PurePosixPath
import stat
import time

SNAPSHOT_CAP=256*1024
MEDIA_CAP=1024*1024
MEDIA_COUNT=4
IDENTITY_CAP=16
SCHEMA='jetson-viewer-snapshot-1'
PANELS=frozenset(('rgb','yolop','depth','grid'))
REPRESENTATIVE_KIND='HISTORICAL_OAK_REPRESENTATIVE'
DISPLAY_CROPS={'REPRESENTATIVE_YOLOP_TOP_LEFT':('yolop',(0,0,640,360)),
               'REPRESENTATIVE_GRID_BOTTOM_LEFT':('grid',(0,360,640,720))}


class SnapshotError(ValueError):
    """원 파일의 형식·해시·순서 또는 경로를 검산하지 못했다."""


def _pairs(items):
    result={}
    for key,value in items:
        if key in result:raise SnapshotError('DUPLICATE_JSON_KEY: '+key)
        result[key]=value
    return result


def json_object(raw):
    def constant(value):raise SnapshotError('NONFINITE_JSON: '+value)
    try:value=json.loads(raw,object_pairs_hook=_pairs,parse_constant=constant)
    except (UnicodeError,json.JSONDecodeError,RecursionError) as error:raise SnapshotError('INCOMPLETE_OR_INVALID_JSON') from error
    if type(value) is not dict:raise SnapshotError('JSON_OBJECT_REQUIRED')
    pending=[(value,0)];count=0
    while pending:
        item,depth=pending.pop();count+=1
        if count>65536 or depth>24:raise SnapshotError('JSON_STRUCTURE_CAP')
        if type(item) is float and not math.isfinite(item):raise SnapshotError('NONFINITE_JSON')
        if type(item) is dict:pending.extend((child,depth+1) for child in item.values())
        elif type(item) is list:pending.extend((child,depth+1) for child in item)
    return value


def _integer(value,name,maximum=(1<<64)-1):
    if type(value) is str and len(value)<=20 and value.isascii() and value.isdecimal():value=int(value)
    if type(value) is not int or not 0<=value<=maximum:raise SnapshotError('INVALID_INTEGER: '+name)
    return value


def _text(value,name,maximum=256):
    if type(value) is not str or not value or len(value)>maximum:raise SnapshotError('INVALID_TEXT: '+name)
    return value


def _hash(value):
    if type(value) is not str or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):raise SnapshotError('INVALID_SHA256')
    return value


def _regular(path):
    value=path.lstat()
    if stat.S_ISLNK(value.st_mode) or getattr(value,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400):
        raise SnapshotError('REPARSE_OR_SYMLINK_REFUSED')
    return value


def safe_path(root,relative):
    relative=_text(relative,'relative path',512).replace('\\','/')
    parts=PurePosixPath(relative)
    if parts.is_absolute() or ':' in relative or '..' in parts.parts or not parts.parts or relative.startswith('/'):
        raise SnapshotError('PATH_ESCAPE')
    root=Path(root).resolve(strict=True);current=root
    for item in parts.parts:current=current/item;_regular(current)
    resolved=current.resolve(strict=True)
    if not resolved.is_relative_to(root):raise SnapshotError('PATH_ESCAPE')
    return resolved


def _stat_version(value):
    return (value.st_dev,value.st_ino,value.st_mode,value.st_size,value.st_mtime_ns,value.st_ctime_ns,
            getattr(value,'st_birthtime_ns',None),getattr(value,'st_file_attributes',None))


def _same_file_version(by_fd,by_path):
    # Windows CPython can expose ChangeTime through fstat.ctime and creation
    # time through lstat.ctime. Compare those clocks only within the same API;
    # fd↔path still must bind the same file ID, content version and birthtime.
    if not os.path.samestat(by_fd,by_path) or (by_fd.st_size,by_fd.st_mtime_ns)!=(by_path.st_size,by_path.st_mtime_ns):return False
    if getattr(by_fd,'st_birthtime_ns',None)!=getattr(by_path,'st_birthtime_ns',None):return False
    return os.name=='nt' or by_fd.st_ctime_ns==by_path.st_ctime_ns


def read_file(root,relative,cap):
    """원 descriptor 하나를 유한하게 읽고 파일 교체/길이 변화도 거절한다."""
    path=safe_path(root,relative)
    info=_regular(path)
    if not stat.S_ISREG(info.st_mode) or info.st_size>cap:raise SnapshotError('FILE_CAP_EXCEEDED')
    flags=os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_CLOEXEC',0)
    fd=os.open(path,flags)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size>cap:raise SnapshotError('FILE_CAP_EXCEEDED')
        pieces=[];size=0
        while size<=cap:
            part=os.read(fd,min(65536,cap+1-size))
            if not part:break
            pieces.append(part);size+=len(part)
        after=os.fstat(fd);by_path=_regular(path)
        if (size>cap or _stat_version(before)!=_stat_version(after) or _stat_version(info)!=_stat_version(by_path) or
                not _same_file_version(before,info) or not _same_file_version(after,by_path)):
            raise SnapshotError('FILE_CHANGED_DURING_READ')
        if safe_path(root,relative)!=path:raise SnapshotError('FILE_PATH_CHANGED')
        return b''.join(pieces)
    finally:os.close(fd)


def stamp(value):
    if value is None:return None
    if type(value) is not dict:raise SnapshotError('INVALID_CLOCK_STAMP')
    return dict(domain=_text(value.get('domain'),'clock domain'),epoch=_text(value.get('epoch'),'clock epoch'),
                us=_integer(value.get('us'),'clock us'))


def age(value,now):
    """같은 domain/epoch일 때만 원 시각을 뺀다. 수신 신선도와 별개다."""
    value=stamp(value)
    if value is None or now is None:return dict(state='UNMAPPED',age_us=None)
    now=stamp(now)
    if (value['domain'],value['epoch'])!=(now['domain'],now['epoch']):return dict(state='UNMAPPED',age_us=None)
    if now['us']<value['us']:return dict(state='INVALID_FUTURE',age_us=None)
    return dict(state='MAPPED',age_us=now['us']-value['us'])


def _number(value,name):
    if type(value) not in (int,float) or not math.isfinite(value) or abs(value)>1e6:raise SnapshotError('INVALID_NUMBER: '+name)
    return value


def validate(body):
    if body.get('schema')!=SCHEMA:raise SnapshotError('UNSUPPORTED_VIEWER_SCHEMA')
    source=body.get('source')
    if type(source) is not dict:raise SnapshotError('SOURCE_REQUIRED')
    key=(_text(source.get('id'),'source id'),_text(source.get('epoch'),'source epoch'))
    recording=body.get('recording')
    historical_unexecuted=(source.get('kind')==REPRESENTATIVE_KIND and source.get('sequence') is None and
        type(recording) is dict and recording.get('status')=='NOT_EXECUTED' and source.get('stamp') is None and not body.get('media'))
    sequence=None if historical_unexecuted else _integer(source.get('sequence'),'source sequence');stamp(source.get('stamp'))
    if source.get('kind') not in ('DIAGNOSTIC','OFFLINE_SYNTHETIC',REPRESENTATIVE_KIND):raise SnapshotError('UNKNOWN_SOURCE_KIND')
    operation=body.get('operation')
    if operation is not None:
        if type(operation) is not dict:raise SnapshotError('INVALID_OPERATION')
        for name in ('parking_enabled','auto_ready'):
            if operation.get(name) is not None and type(operation[name]) is not bool:raise SnapshotError('INVALID_OPERATION_BOOL')
        for name in ('mode','vehicle_phase','active_vm','standby_vm','blocked_reason'):
            if operation.get(name) is not None:_text(operation[name],name)
    layers=body.get('map',{}).get('layers',[]) if type(body.get('map',{})) is dict else None
    if type(layers) is not list or len(layers)>8:raise SnapshotError('INVALID_MAP_LAYERS')
    total=0
    for layer in layers:
        if type(layer) is not dict or layer.get('kind') not in ('GT','ESTIMATE','UNCERTAINTY','PATH'):raise SnapshotError('INVALID_LAYER_KIND')
        _text(layer.get('frame'),'map frame');points=layer.get('points')
        if 'closed' in layer and type(layer['closed']) is not bool:raise SnapshotError('INVALID_LAYER_CLOSED')
        if type(points) is not list or len(points)>2048:raise SnapshotError('MAP_POINT_CAP')
        total+=len(points)
        for point in points:
            if type(point) is not list or len(point)!=2:raise SnapshotError('INVALID_MAP_POINT')
            _number(point[0],'x');_number(point[1],'y')
    if total>8192:raise SnapshotError('MAP_POINT_CAP')
    media=body.get('media',[])
    if type(media) is not list or len(media)>MEDIA_COUNT:raise SnapshotError('MEDIA_COUNT_CAP')
    panels=set()
    for item in media:
        if type(item) is not dict or item.get('panel') not in PANELS or item['panel'] in panels:raise SnapshotError('INVALID_MEDIA_PANEL')
        panels.add(item['panel']);_hash(item.get('sha256'));_integer(item.get('byte_length'),'media bytes',MEDIA_CAP)
        if any(type(item.get(k)) is not int for k in ('width','height','byte_length')):raise SnapshotError('MEDIA_LAYOUT_INTEGERS_REQUIRED')
        width=_integer(item.get('width'),'media width',4096);height=_integer(item.get('height'),'media height',4096)
        if not width or not height or width*height>2097152:raise SnapshotError('MEDIA_DIMENSION_CAP')
        encoding=item.get('encoding')
        if encoding not in ('JPEG','PNG','NV12','RAW16_LE'):raise SnapshotError('UNSUPPORTED_MEDIA_ENCODING')
        if encoding=='NV12' and (width%2 or height%2 or item['byte_length']!=width*height*3//2):raise SnapshotError('NV12_LAYOUT')
        if encoding=='RAW16_LE' and item['byte_length']!=width*height*2:raise SnapshotError('DEPTH_LAYOUT')
        role=item.get('display_crop')
        if role is not None:
            if (source.get('kind')!=REPRESENTATIVE_KIND or type(role) is not str or role not in DISPLAY_CROPS or
                    (encoding,width,height)!=('PNG',1280,720) or DISPLAY_CROPS[role][0]!=item['panel']):
                raise SnapshotError('INVALID_DISPLAY_CROP')
        if source.get('kind')==REPRESENTATIVE_KIND:
            expected={'rgb':('NV12',640,360),'depth':('RAW16_LE',640,480),'yolop':('PNG',1280,720),'grid':('PNG',1280,720)}
            if (encoding,width,height)!=expected[item['panel']] or (item['panel'] in ('yolop','grid') and role is None):
                raise SnapshotError('REPRESENTATIVE_GT_CROP_REQUIRED')
        stamp(item.get('stamp'));_text(item.get('path'),'media path',512)
    raw=body.get('raw_payload')
    if raw is not None:
        if type(raw) is not dict:raise SnapshotError('INVALID_RAW_PAYLOAD')
        _hash(raw.get('sha256'));_integer(raw.get('byte_length'),'raw bytes',SNAPSHOT_CAP);_text(raw.get('path'),'raw path',512)
    return key,sequence


@dataclass(frozen=True)
class Media:
    descriptor: dict
    raw: bytes


@dataclass(frozen=True)
class Snapshot:
    body: dict
    raw: bytes
    media: tuple
    raw_payload: bytes|None
    sha256: str
    first_received: float


@dataclass(frozen=True)
class Result:
    state: str
    snapshot: Snapshot|None
    reason: str|None
    received_age_s: float|None
    last_error: dict|None


class SnapshotReader:
    """최신 snapshot1개, 원 오류1개, identity HWM16개. thread/queue/socket 없다."""
    def __init__(self,root,*,stale_after=2.,clock=time.monotonic):
        self.root=Path(root).resolve();self.clock=clock;self.stale_after=float(stale_after)
        if not math.isfinite(self.stale_after) or self.stale_after<=0:raise ValueError('positive viewer staleness threshold required')
        self.current=None;self.highwater=OrderedDict();self.seen=None;self.last_error=None;self.error_raw=None
        self.closed=False

    def accept(self,raw,*,historical=False):
        if self.closed:raise SnapshotError('VIEWER_CLOSED')
        if len(raw)>SNAPSHOT_CAP:raise SnapshotError('SNAPSHOT_CAP')
        now=self.clock();digest=sha256(raw).hexdigest()
        if self.seen is None or self.seen[0]!=digest:self.seen=(digest,now)
        body=json_object(raw);key,sequence=validate(body)
        previous=self.highwater.get(key)
        if not historical and previous:
            if sequence<previous[0]:raise SnapshotError('SEQUENCE_REVERSED')
            if sequence==previous[0] and digest!=previous[1]:raise SnapshotError('SAME_SEQUENCE_DIFFERENT_BYTES')
        first=previous[2] if previous and sequence==previous[0] and digest==previous[1] else self.seen[1]
        assets=[]
        for descriptor in body.get('media',[]):
            payload=read_file(self.root,descriptor['path'],MEDIA_CAP)
            if len(payload)!=descriptor['byte_length'] or sha256(payload).hexdigest()!=descriptor['sha256']:raise SnapshotError('MEDIA_HASH_OR_LENGTH')
            if descriptor['encoding'] in ('JPEG','PNG'):
                from render import encoded_dimensions
                if encoded_dimensions(payload,descriptor['encoding'])!=(descriptor['width'],descriptor['height']):raise SnapshotError('ENCODED_MEDIA_DIMENSIONS_DIFFER')
            assets.append(Media(descriptor,payload))
        original=None;descriptor=body.get('raw_payload')
        if descriptor:
            original=read_file(self.root,descriptor['path'],SNAPSHOT_CAP)
            if len(original)!=descriptor['byte_length'] or sha256(original).hexdigest()!=descriptor['sha256']:raise SnapshotError('RAW_PAYLOAD_HASH_OR_LENGTH')
        if key not in self.highwater and len(self.highwater)>=IDENTITY_CAP:raise SnapshotError('SOURCE_IDENTITY_CAP')
        if not historical:self.highwater[key]=(sequence,digest,first)
        self.current=Snapshot(body,raw,tuple(assets),original,digest,first)
        elapsed=max(0,now-first)
        state='HISTORICAL' if historical else 'STALE' if elapsed>=self.stale_after else 'VALID'
        return Result(state,self.current,None,elapsed,self.last_error)

    def failure(self,error,raw=None):
        self.current=None;self.error_raw=raw[:SNAPSHOT_CAP] if raw is not None else None
        detail=dict(reason=str(error),sha256=sha256(raw).hexdigest() if raw is not None else None)
        if self.last_error is None or any(self.last_error.get(k)!=v for k,v in detail.items()):
            self.last_error={**detail,'first_received':self.clock()}
        return Result('UNAVAILABLE' if isinstance(error,FileNotFoundError) else 'INVALID',None,str(error),None,self.last_error)

    def read(self):
        if self.closed:return Result('UNAVAILABLE',None,'VIEWER_CLOSED',None,self.last_error)
        raw=None
        try:raw=read_file(self.root,'snapshot.json',SNAPSHOT_CAP);return self.accept(raw)
        except (OSError,ValueError) as error:return self.failure(error,raw)

    def close(self):
        """뷰어의 메모리만 해제한다. producer·입력 파일·제품에 종료 신호를 보내지 않는다."""
        self.current=None;self.error_raw=None;self.highwater.clear();self.closed=True
