"""완료된 MapperMotionObservation을 PC snapshot-1 파일로 내보내는 개발 도구.

실험의 독립 diagnostic owner가 원 fields JSON bytes와 같은 capture.observation을
넘긴다. 제품/renderer/SDK/뷰어/inference를 import하지 않는다. GT/oracle을 읽거나
권한·시각·순번을 생산하지 않으며 제품 hot loop에 자동 연결하지 않는다.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import stat
import threading
from uuid import uuid4


JSON_CAP=256*1024
MEDIA_CAP=1024*1024
PIXEL_CAP=2097152
SCHEMA='jetson-viewer-snapshot-1'
PROFILE='sim-mapper-motion-export-1'
_NAMES=('rgb.nv12','depth.raw16','observation.json')
_U64=(1<<64)-1


class ObservationExportError(ValueError):
    """원 입력/파일/순서 검산 실패. 직전 게시 snapshot의 권위를 만들지 않는다."""


def _json(raw):
    if type(raw) is not bytes or not 0<len(raw)<=JSON_CAP:raise ObservationExportError('JSON_CAP')
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ObservationExportError('DUPLICATE_JSON_KEY')
            result[key]=value
        return result
    def constant(value):raise ObservationExportError('NONFINITE_JSON')
    try:value=json.loads(raw,object_pairs_hook=pairs,parse_constant=constant)
    except (UnicodeError,json.JSONDecodeError,RecursionError) as error:raise ObservationExportError('INVALID_JSON') from error
    if type(value) is not dict:raise ObservationExportError('JSON_OBJECT_REQUIRED')
    pending=[(value,0)];count=0
    while pending:
        item,depth=pending.pop();count+=1
        if count>65536 or depth>24:raise ObservationExportError('JSON_STRUCTURE_CAP')
        if type(item) is float and not math.isfinite(item):raise ObservationExportError('NONFINITE_JSON')
        if type(item) is dict:pending.extend((child,depth+1) for child in item.values())
        elif type(item) is list:pending.extend((child,depth+1) for child in item)
    return value


def _packed(value):
    try:raw=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')
    except (TypeError,ValueError,RecursionError) as error:raise ObservationExportError('INVALID_PROJECTION_JSON') from error
    if len(raw)>JSON_CAP:raise ObservationExportError('SNAPSHOT_CAP')
    return raw


def _uint(value):
    if type(value) is str and value.isascii() and value.isdecimal() and len(value)<=20 and str(int(value))==value:value=int(value)
    if type(value) is not int or not 0<=value<=_U64:raise ObservationExportError('ORIGINAL_U64_REQUIRED')
    return value


def _text(value):
    if type(value) is not str or not 1<=len(value)<=128 or any(not 33<=ord(c)<=126 for c in value):
        raise ObservationExportError('ORIGINAL_TEXT_REQUIRED')
    return value


def _hash(value):
    if type(value) is not str or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):raise ObservationExportError('ORIGINAL_SHA_REQUIRED')
    return value


def _number(value,*,nonnegative=False):
    if type(value) not in (int,float) or not math.isfinite(value) or abs(value)>10000 or nonnegative and value<0:
        raise ObservationExportError('INVALID_POSE_OR_DISPLAY_POINT')
    return value


def _interval(value):
    if type(value) not in (list,tuple) or len(value)!=2:raise ObservationExportError('ORIGINAL_INTERVAL_REQUIRED')
    lo,hi=map(_uint,value)
    if lo>hi:raise ObservationExportError('REVERSED_ORIGINAL_INTERVAL')
    return lo,hi


def _regular(path):
    value=path.lstat()
    if stat.S_ISLNK(value.st_mode) or getattr(value,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400):
        raise ObservationExportError('SYMLINK_OR_REPARSE_REFUSED')
    return value


def _read(path,cap):
    before=_regular(path)
    if not stat.S_ISREG(before.st_mode) or before.st_size>cap:raise ObservationExportError('FILE_CAP_OR_TYPE')
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_CLOEXEC',0))
    try:
        start=os.fstat(fd);raw=os.read(fd,cap+1);end=os.fstat(fd);after=_regular(path)
        if (not stat.S_ISREG(start.st_mode) or not os.path.samestat(before,start) or not os.path.samestat(end,after)
                or len(raw)!=before.st_size or len(raw)>cap or (start.st_size,start.st_mtime_ns,start.st_ctime_ns)!=(end.st_size,end.st_mtime_ns,end.st_ctime_ns)
                or (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns)):
            raise ObservationExportError('FILE_CHANGED_OR_PARTIAL')
        return raw
    finally:os.close(fd)


@dataclass(frozen=True)
class _Frame:
    width: int
    height: int
    encoding: str
    payload: bytes
    stamp: dict
    exposure_us: int


def _frame(value):
    try:
        stamp=value.stamp
        if type(stamp.captured_us) is not tuple or len(stamp.captured_us)!=2:
            raise ObservationExportError('ORIGINAL_CAPTURE_INTERVAL_REQUIRED')
        original=dict(source_id=stamp.source_id,stream_id=stamp.stream_id,sequence=stamp.sequence,
            clock_id=stamp.clock_id,captured_us=list(stamp.captured_us),available_us=stamp.available_us)
        return _Frame(value.width,value.height,value.encoding,value.payload,original,value.exposure_us)
    except (AttributeError,TypeError) as error:raise ObservationExportError('ORIGINAL_CAPTURE_FRAME_REQUIRED') from error


def _projection(raw,frames,display_gt_layers):
    fields=_json(raw)
    if (fields.get('profile')!='sim-mapper-motion-observation-1'
            or fields.get('scope')!='ACTUAL_FD_MQTT_RETURNED_TRUST_SYNTHETIC_ODOMETRY_DIAGNOSTIC'
            or any(fields.get(key) is not False for key in ('nn_executed','free_positive','authority','physical_acceptance','closed_loop'))):
        raise ObservationExportError('UNSUPPORTED_DIAGNOSTIC_SCOPE')
    capture=fields.get('capture')
    if type(capture) is not dict or capture.get('input_kind')!='SYNTHETIC' or capture.get('live_authority') is not False:
        raise ObservationExportError('ORIGINAL_SYNTHETIC_RECEIPT_REQUIRED')
    source=_text(capture.get('source_id'));clock=_text(capture.get('clock_id'));sequence=_uint(capture.get('sequence'))
    if not source.startswith('sim-wall:') or not clock.startswith('jetson-monotonic:'):
        raise ObservationExportError('ORIGINAL_WALL_SOURCE_REQUIRED')
    pair=_interval(capture.get('captured_j_us'));completed=_uint(capture.get('completed_j_us'));expires=_uint(capture.get('expires_j_us'))
    if not pair[1]<=completed<expires or not completed<=_uint(fields.get('checked_j_us'))<expires:
        raise ObservationExportError('ORIGINAL_CAPTURE_RECEIPT_ORDER')
    _hash(capture.get('calibration_id'));_hash(capture.get('profile_sha256'))
    media=[]
    for panel,frame,encoding,stream in zip(('rgb','depth'),frames,('NV12','RAW16_LE'),('rgb_video','depth_right')):
        s=frame.stamp;w,h=frame.width,frame.height
        if (type(s) is not dict or frame.encoding!=encoding or type(w) is not int or type(h) is not int
                or not 0<w<=4096 or not 0<h<=4096 or w*h>PIXEL_CAP or type(frame.payload) is not bytes
                or not 0<len(frame.payload)<=MEDIA_CAP or type(frame.exposure_us) is not int or frame.exposure_us!=0):raise ObservationExportError('MEDIA_CAP_OR_LAYOUT')
        if encoding=='NV12' and (w%2 or h%2 or len(frame.payload)!=w*h*3//2):raise ObservationExportError('NV12_LAYOUT')
        if encoding=='RAW16_LE' and len(frame.payload)!=w*h*2:raise ObservationExportError('RAW16_LAYOUT')
        if (s.get('source_id')!=source or s.get('clock_id')!=clock or s.get('stream_id')!=stream
                or _uint(s.get('sequence'))!=sequence or _interval(s.get('captured_us'))!=pair
                or _uint(s.get('available_us'))!=completed):raise ObservationExportError('ORIGINAL_PAIR_STAMP_MISMATCH')
        digest=sha256(frame.payload).hexdigest()
        if capture.get(panel+'_sha256')!=digest:raise ObservationExportError('RECEIPT_MEDIA_HASH_MISMATCH')
        media.append(dict(panel=panel,sha256=digest,byte_length=len(frame.payload),width=w,height=h,encoding=encoding,
            depth_unit='mm' if panel=='depth' else None,source_sequence=str(sequence),
            stamp=dict(domain=clock,epoch=source,us=str(pair[0])),original_stamp=s,exposure_us=frame.exposure_us))
    layers=[];pose=fields.get('pose');pose_frame=None
    if pose is not None:
        if type(pose) is not dict or type(pose.get('sample')) is not dict:raise ObservationExportError('ORIGINAL_POSE_REQUIRED')
        sample=pose['sample'];origin=sample.get('origin')
        if origin not in ('ODOMETRY','SYNTHETIC_ODOMETRY'):raise ObservationExportError('ORACLE_OR_UNKNOWN_POSE_REFUSED')
        if sample.get('clock_id')!=clock or sample.get('calibration_id')!=capture['calibration_id'] or _interval(pose.get('captured_us'))!=pair:
            raise ObservationExportError('POSE_FRAME_CAPTURE_MISMATCH')
        bracket=_interval(pose.get('bracket_us'))
        if not bracket[0]<=pair[0]<=pair[1]<=bracket[1] or not pair[0]<=_uint(sample.get('at_us'))<=pair[1]:
            raise ObservationExportError('POSE_BRACKET_MISMATCH')
        pose_frame='ODOMETRY:'+_text(sample.get('epoch'))
        x,y=_number(sample.get('x')),_number(sample.get('y'));_number(sample.get('yaw'))
        _number(sample.get('position_error_m'),nonnegative=True);_number(sample.get('yaw_error_rad'),nonnegative=True)
        layers.append(dict(kind='ESTIMATE',frame=pose_frame,points=[[x,y]],closed=False))
    gt=[] if display_gt_layers is None else display_gt_layers
    if type(gt) not in (list,tuple) or len(gt)>7:raise ObservationExportError('DISPLAY_GT_CAP')
    for layer in gt:
        if type(layer) is not dict or layer.get('kind')!='GT' or set(layer)-{'kind','frame','points','closed'}:
            raise ObservationExportError('EXPLICIT_DISPLAY_GT_ONLY')
        frame=_text(layer.get('frame'));points=layer.get('points')
        if pose_frame is not None and frame!=pose_frame:raise ObservationExportError('DISPLAY_GT_FRAME_MISMATCH')
        if type(points) is not list or len(points)>2048 or 'closed' in layer and type(layer['closed']) is not bool:
            raise ObservationExportError('DISPLAY_GT_CAP')
        for point in points:
            if type(point) is not list or len(point)!=2:raise ObservationExportError('DISPLAY_GT_POINT')
            for number in point:_number(number)
        layers.append(json.loads(_packed(layer)))
    if len({x['frame'] for x in layers})>1 or sum(len(x['points']) for x in layers)>8192:raise ObservationExportError('DISPLAY_FRAME_OR_POINT_CAP')
    fingerprint=sha256(_packed(dict(raw_sha256=sha256(raw).hexdigest(),media=media,
        gt=[x for x in layers if x['kind']=='GT']))).hexdigest()
    fd=fields.get('fd');fd_ids=None if type(fd) is not dict else {key:fd.get(key) for key in
        ('pi_session','device_session','odom_epoch','clock_epoch','clock_sequence','clock_valid_until_pi_us')}
    body=dict(schema=SCHEMA,source=dict(id=source,epoch=clock,sequence=str(sequence),kind='OFFLINE_SYNTHETIC',
        stamp=dict(domain=clock,epoch=source,us=str(pair[0]))),operation=None,map=dict(layers=layers),media=media,
        identities=dict(export_profile=PROFILE,input_sha256=fingerprint,capture=capture,pose=pose,pose_frame=pose_frame,
            fd=fd_ids,context_sha256=fields.get('context_sha256')),
        metrics=None,limitations=['HISTORICAL_SYNTHETIC_ONLY','RAW_JSON_IS_A_DIAGNOSTIC_NOT_ORIGINAL_WIRE',
            'NN_NOT_EXPORTED','FREE_UNKNOWN','MAP_AND_ROUTE_NOT_EXPORTED','OPERATION_UNKNOWN','LIVE_TRANSFER_NOT_CONNECTED',
            'ESTIMATE_IS_ODOMETRY_FRAME_ONLY','GT_ONLY_WHEN_EXPLICITLY_SUPPLIED'])
    return dict(body=body,raw=raw,frames=frames,key=(source,clock),sequence=sequence,capture=pair,fingerprint=fingerprint)


def _document(prepared,slot):
    body=prepared['body'];base=f'slot-{slot}/'
    body['media']=[dict(row,path=base+name) for row,name in zip(body['media'],_NAMES)]
    body['raw_payload']=dict(path=base+_NAMES[2],sha256=sha256(prepared['raw']).hexdigest(),byte_length=len(prepared['raw']))
    return _packed(body)


@dataclass(frozen=True)
class ExportResult:
    status: str
    snapshot_path: Path
    source_sequence: int
    original_json_sha256: str
    snapshot_sha256: str


class SimObservationExporter:
    """전용 폴더/단일 diagnostic owner의 newest-only 두 슬롯. 제품 loop에서 호출하지 않는다.

반환은 WRITTEN 또는 UNCHANGED다. 같은 원 source/seq의 다른 bytes와 역전은 거절하며
source가 바뀌면 새 폴더를 명시해야 한다. 프로세스 crash의 lock은 자동 인수하지 않는다.
"""
    def __init__(self,directory):
        self.root=Path(directory).absolute();self.owner=threading.get_ident();self.closed=False;self._lock=None
        for path in reversed((self.root,*self.root.parents)):
            if path.exists():_regular(path)
            elif path==self.root:path.mkdir(mode=0o700)
            else:raise ObservationExportError('EXISTING_PARENT_REQUIRED')
        if not stat.S_ISDIR(_regular(self.root).st_mode):raise ObservationExportError('EXPORT_DIRECTORY_REQUIRED')
        self._root_info=_regular(self.root);self._slot_info={}
        for path in self.root.iterdir():
            if path.name not in ('snapshot.json','slot-0','slot-1'):raise ObservationExportError('DEDICATED_EXPORT_DIRECTORY_REQUIRED')
            _regular(path)
        token=(PROFILE+':'+uuid4().hex).encode('ascii')
        self._lock_path=self.root/'.export-owner.lock';self._token=token
        self._lock=os.open(self._lock_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        try:
            if os.write(self._lock,token)!=len(token):raise ObservationExportError('PARTIAL_OWNER_CLAIM')
            for name in ('slot-0','slot-1'):
                path=self.root/name
                if not path.exists():path.mkdir(mode=0o700)
                if not stat.S_ISDIR(_regular(path).st_mode):raise ObservationExportError('EXPORT_SLOT_REQUIRED')
                self._slot_info[name]=_regular(path)
                for item in path.iterdir():
                    if item.name not in _NAMES:raise ObservationExportError('PARTIAL_OR_FOREIGN_SLOT')
                    if not stat.S_ISREG(_regular(item).st_mode):raise ObservationExportError('EXPORT_REGULAR_FILE_REQUIRED')
            self.current=self._load()
        except BaseException:
            self.close();raise

    def _owned(self):
        if self.closed or self.owner!=threading.get_ident():raise ObservationExportError('SINGLE_DIAGNOSTIC_OWNER_REQUIRED')

    def _check_tree(self):
        for path in (self.root,*self.root.parents):_regular(path)
        if not os.path.samestat(self._root_info,_regular(self.root)):raise ObservationExportError('EXPORT_DIRECTORY_REPLACED')
        if not os.path.samestat(os.fstat(self._lock),_regular(self._lock_path)):raise ObservationExportError('EXPORT_OWNER_REPLACED')
        for path in self.root.iterdir():
            if path.name not in ('snapshot.json','slot-0','slot-1','.export-owner.lock'):
                raise ObservationExportError('PARTIAL_OR_FOREIGN_EXPORT_FILE')
        for name,info in self._slot_info.items():
            path=self.root/name
            if not os.path.samestat(info,_regular(path)):raise ObservationExportError('EXPORT_SLOT_REPLACED')
            for item in path.iterdir():
                if item.name not in _NAMES:raise ObservationExportError('PARTIAL_OR_FOREIGN_SLOT')
                value=_regular(item);cap=JSON_CAP if item.name==_NAMES[2] else MEDIA_CAP
                if not stat.S_ISREG(value.st_mode) or value.st_size>cap:raise ObservationExportError('FILE_CAP_OR_TYPE')

    def _load(self):
        self._check_tree()
        path=self.root/'snapshot.json'
        if not path.exists():
            if any((self.root/slot/name).exists() for slot in ('slot-0','slot-1') for name in _NAMES):
                raise ObservationExportError('UNCOMMITTED_EXPORT_DIRECTORY')
            return None
        raw=_read(path,JSON_CAP);body=_json(raw)
        identities=body.get('identities')
        if body.get('schema')!=SCHEMA or type(identities) is not dict or identities.get('export_profile')!=PROFILE:
            raise ObservationExportError('FOREIGN_EXPORT_MANIFEST')
        media=body.get('media');descriptor=body.get('raw_payload')
        if type(media) is not list or len(media)!=2 or any(type(x) is not dict for x in media) or type(descriptor) is not dict:
            raise ObservationExportError('PARTIAL_EXPORT_MANIFEST')
        slot=next((n for n in (0,1) if [x.get('path') for x in media]==[f'slot-{n}/'+name for name in _NAMES[:2]]
            and descriptor.get('path')==f'slot-{n}/'+_NAMES[2]),None)
        if slot is None:raise ObservationExportError('FOREIGN_EXPORT_PATH')
        frames=[]
        for item,name in zip(media,_NAMES):
            payload=_read(self.root/f'slot-{slot}'/name,MEDIA_CAP)
            if len(payload)!=item.get('byte_length') or sha256(payload).hexdigest()!=item.get('sha256'):
                raise ObservationExportError('PARTIAL_EXPORT_MEDIA')
            frames.append(_Frame(item.get('width'),item.get('height'),item.get('encoding'),payload,item.get('original_stamp'),item.get('exposure_us')))
        fields=_read(self.root/f'slot-{slot}'/_NAMES[2],JSON_CAP)
        if len(fields)!=descriptor.get('byte_length') or sha256(fields).hexdigest()!=descriptor.get('sha256'):
            raise ObservationExportError('PARTIAL_EXPORT_ORIGINAL_JSON')
        mapping=body.get('map');layers=None if type(mapping) is not dict else mapping.get('layers')
        if type(layers) is not list or any(type(x) is not dict for x in layers):raise ObservationExportError('PARTIAL_EXPORT_MANIFEST')
        gt=[x for x in layers if x.get('kind')=='GT']
        prepared=_projection(fields,tuple(frames),gt)
        if _document(prepared,slot)!=raw:raise ObservationExportError('EXPORT_MANIFEST_CONTENT_CHANGED')
        return dict(key=prepared['key'],sequence=prepared['sequence'],capture=prepared['capture'],fingerprint=prepared['fingerprint'],slot=slot,raw=raw)

    def export(self,raw_fields,capture_observation,*,display_gt_layers=None):
        """원 fields bytes를 attachment로 보존한다. .fields 재직렬화가 원 wire인 것은 아니다."""
        self._owned()
        try:
            if capture_observation.kind!='SYNTHETIC' or capture_observation.nn is not None or capture_observation.nn_key is not None:
                raise ObservationExportError('UNPROCESSED_SYNTHETIC_CAPTURE_REQUIRED')
            frames=(_frame(capture_observation.rgb),_frame(capture_observation.depth))
            prepared=_projection(raw_fields,frames,display_gt_layers)
            if capture_observation.calibration_id!=prepared['body']['identities']['capture']['calibration_id']:
                raise ObservationExportError('ORIGINAL_CAPTURE_CALIBRATION_MISMATCH')
        except AttributeError as error:raise ObservationExportError('ORIGINAL_CAPTURE_OBSERVATION_REQUIRED') from error
        disk=self._load()
        if (None if disk is None else disk['raw'])!=(None if self.current is None else self.current['raw']):
            raise ObservationExportError('EXPORT_MANIFEST_REPLACED')
        if self.current is not None:
            old=self.current
            if prepared['key']!=old['key']:raise ObservationExportError('SOURCE_CHANGED_USE_NEW_DIRECTORY')
            if prepared['sequence']<old['sequence']:raise ObservationExportError('SEQUENCE_REVERSED')
            if prepared['sequence']==old['sequence']:
                if prepared['fingerprint']!=old['fingerprint']:raise ObservationExportError('SAME_SEQUENCE_DIFFERENT_BYTES')
                return ExportResult('UNCHANGED',self.root/'snapshot.json',old['sequence'],sha256(raw_fields).hexdigest(),sha256(old['raw']).hexdigest())
            if prepared['capture'][0]<=old['capture'][1]:raise ObservationExportError('CAPTURE_REVERSED_OR_OVERLAPPED')
        slot=0 if self.current is None else 1-self.current['slot'];document=_document(prepared,slot)
        temporaries=[]
        def write(target,payload):
            self._check_tree()
            temp=target.with_name(target.name+'.tmp');fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0),0o600)
            temporaries.append(temp)
            try:
                if os.write(fd,payload)!=len(payload):raise ObservationExportError('PARTIAL_FILE_WRITE')
            finally:os.close(fd)
            os.replace(temp,target);temporaries.remove(temp)
        try:
            for name,payload in zip(_NAMES,(frames[0].payload,frames[1].payload,raw_fields)):
                write(self.root/f'slot-{slot}'/name,payload)
            # Only a completed media/original set can become the newest manifest.
            current_path=self.root/'snapshot.json'
            if (self.current is None and current_path.exists()
                    or self.current is not None and _read(current_path,JSON_CAP)!=self.current['raw']):
                raise ObservationExportError('EXPORT_MANIFEST_REPLACED')
            write(self.root/'snapshot.json',document)
        finally:
            for temp in temporaries:
                if temp.exists():temp.unlink()
        self.current=dict(key=prepared['key'],sequence=prepared['sequence'],capture=prepared['capture'],fingerprint=prepared['fingerprint'],slot=slot,raw=document)
        return ExportResult('WRITTEN',self.root/'snapshot.json',prepared['sequence'],sha256(raw_fields).hexdigest(),sha256(document).hexdigest())

    def close(self):
        if self._lock is None:self.closed=True;return
        self._owned();fd=self._lock;self._lock=None;self.closed=True
        try:info=os.fstat(fd);owned=os.path.samestat(info,_regular(self._lock_path))
        finally:os.close(fd)
        # Windows cannot unlink the live CRT handle. Retain the original file
        # identity across close and never remove a replaced owner's lock.
        if owned and os.path.samestat(info,_regular(self._lock_path)):self._lock_path.unlink()

    def __enter__(self):self._owned();return self
    def __exit__(self,*unused):self.close()
