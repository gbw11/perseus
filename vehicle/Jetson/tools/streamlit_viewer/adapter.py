"""실제 SyntheticDatasetWriter의 offline 파일을 읽는 표시 전용 adapter.

제품 패키지를 import하거나 decoder/inference를 재실행하지 않는다. 원 payload·stamp·
index 한 줄은 그대로 보존한다. oracle은 기본적으로 열지 않으며 운전 출력도 없다.
"""
from dataclasses import replace
from hashlib import sha256
import json
from uuid import UUID

from snapshot import SCHEMA,SnapshotReader,SnapshotError,json_object,read_file,_integer,_hash


class OfflineDataset:
    def __init__(self,root,**kwargs):self.reader=SnapshotReader(root,**kwargs)

    def close(self):self.reader.close()

    def read(self,ordinal=-1):
        if self.reader.closed:return self.reader.read()
        raw=None
        try:
            header_raw=read_file(self.reader.root,'manifest.json',65536);header=json_object(header_raw)
            if type(header.get('version')) is not int or header['version']!=1 or header.get('scope')!='synthetic-rgb-right-first-surface':raise SnapshotError('OFFLINE_PROFILE_UNSUPPORTED')
            _hash(header.get('calibration_id'))
            _hash(header.get('effects_sha256'))
            index=read_file(self.reader.root,'snapshots.jsonl',2*1024*1024)
            receipt=json_object(read_file(self.reader.root,'result.json',4096))
            lines=index.splitlines(keepends=True)
            if type(receipt.get('complete')) is not bool or not receipt['complete'] or not lines or len(lines)>256 or any(not x.endswith(b'\n') for x in lines):raise SnapshotError('OFFLINE_INCOMPLETE')
            if (set(receipt)!={'version','complete','count','index_sha256'} or type(receipt.get('version')) is not int or
                    receipt['version']!=1 or type(receipt.get('count')) is not int or receipt['count']!=len(lines) or
                    receipt.get('index_sha256')!=sha256(index).hexdigest()):raise SnapshotError('OFFLINE_INDEX_RECEIPT_MISMATCH')
            selected=len(lines)-1 if ordinal==-1 else _integer(ordinal,'offline ordinal',255)
            if selected>=len(lines):raise SnapshotError('OFFLINE_ORDINAL_NOT_FOUND')
            previous={};row=None
            for i,line in enumerate(lines):
                item=json_object(line)
                if set(item)!={'ordinal','calibration_id','rgb','right'} or type(item.get('ordinal')) is not int or item['ordinal']!=i or item.get('calibration_id')!=header['calibration_id']:raise SnapshotError('OFFLINE_ORDER_OR_CALIBRATION')
                rgb=item.get('rgb',{});depth=item.get('right',{})
                if type(rgb) is not dict or type(depth) is not dict:raise SnapshotError('OFFLINE_FRAME_DESCRIPTOR')
                stamp=rgb.get('stamp',{});other=depth.get('stamp',{})
                if type(stamp) is not dict or type(other) is not dict:raise SnapshotError('OFFLINE_STREAM_STAMP')
                capture=stamp.get('captured_us');sequence=_integer(stamp.get('sequence'),'frame sequence')
                if type(capture) is not list or len(capture)!=2 or capture[0]!=capture[1] or not str(stamp.get('clock_id','')).startswith('simulation:'):
                    raise SnapshotError('OFFLINE_CAPTURE_INTERVAL')
                epoch=stamp['clock_id'].removeprefix('simulation:')
                if str(UUID(epoch))!=epoch or not str(stamp.get('source_id','')).startswith('sim:'+epoch+':') or not stamp['source_id'].endswith(':'+header['effects_sha256'][:16]):raise SnapshotError('OFFLINE_SOURCE_IDENTITY')
                _integer(capture[0],'capture');available=_integer(stamp.get('available_us'),'available')
                if capture[1]>available or (stamp.get('stream_id'),other.get('stream_id'))!=('rgb_video','depth_right'):
                    raise SnapshotError('OFFLINE_STREAM_STAMP')
                if any(stamp.get(k)!=other.get(k) for k in ('source_id','clock_id','sequence','captured_us','available_us')):raise SnapshotError('OFFLINE_UNPAIRED_CAPTURE')
                key=(stamp.get('source_id'),stamp.get('clock_id'));current=(sequence,capture[0])
                if key in previous and (current[0]<=previous[key][0] or current[1]<=previous[key][1]):raise SnapshotError('OFFLINE_SEQUENCE_REVERSED')
                if key not in previous and len(previous)>=16:raise SnapshotError('OFFLINE_SOURCE_CAP')
                previous[key]=current
                for name,width,height,encoding in (('rgb',640,360,'NV12'),('right',640,480,'RAW16_LE')):
                    descriptor=item[name];length=width*height*(3 if name=='rgb' else 4)//2
                    if (set(descriptor)!={'stamp','width','height','encoding','exposure_us','path','payload_length','sha256'} or
                        any(type(descriptor.get(k)) is not int for k in ('width','height','payload_length','exposure_us')) or
                        (descriptor['path'],descriptor['width'],descriptor['height'],descriptor['encoding'],descriptor['payload_length'])!=(
                            f'frames/{i:06d}-{name}.bin',width,height,encoding,length)):raise SnapshotError('OFFLINE_LAYOUT_MISMATCH')
                    _hash(descriptor['sha256'])
                if i==selected:row=item;raw=line
            media=[]
            for name,panel,width,height,encoding in (('rgb','rgb',640,360,'NV12'),('right','depth',640,480,'RAW16_LE')):
                value=row[name];length=width*height*(3 if name=='rgb' else 4)//2
                if (value.get('path'),value.get('width'),value.get('height'),value.get('encoding'),value.get('payload_length'))!=(
                    f'frames/{selected:06d}-{name}.bin',width,height,encoding,length):raise SnapshotError('OFFLINE_LAYOUT_MISMATCH')
                original=value['stamp'];capture=original['captured_us']
                media.append(dict(panel=panel,path=value['path'],sha256=value['sha256'],byte_length=length,width=width,height=height,
                    encoding=encoding,depth_unit='mm' if name=='right' else None,
                    stamp=dict(domain=original['clock_id'],epoch=original['source_id'],us=capture[0]),
                    source_sequence=str(original['sequence']),original_stamp=original))
            original=row['rgb']['stamp']
            body=dict(schema=SCHEMA,source=dict(id=original['source_id'],epoch=original['clock_id'],sequence=str(original['sequence']),
                kind='OFFLINE_SYNTHETIC',stamp=media[0]['stamp']),operation=None,map=dict(layers=[]),media=media,
                identities=dict(calibration_id=row['calibration_id'],effects_sha256=header.get('effects_sha256'),
                    ordinal=selected,index_sha256=sha256(index).hexdigest()),metrics=[],
                original_record=dict(sha256=sha256(raw).hexdigest(),byte_length=len(raw)),
                limitations=['OFFLINE_ONLY','LIVE_OBSERVER_NOT_CONNECTED','YOLOP_NOT_EXPORTED','MAP_NOT_EXPORTED','ORACLE_NOT_READ'])
            packed=json.dumps(body,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
            result=self.reader.accept(packed,historical=True)
            value=replace(result.snapshot,raw_payload=raw);self.reader.current=value
            return replace(result,snapshot=value)
        except (OSError,ValueError,KeyError,TypeError) as error:return self.reader.failure(error,raw)
