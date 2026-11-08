"""실제 OAK host-fed 대표 장면의 과거 아티팩트만 읽는 PC 관측 adapter.

선택한 파일의 manifest/receipt/원 stamp를 검산한다. 제품 import·NN/기하 재실행·
GT/oracle 입력·운전 출력은 없다. manifest 전체 파일의 검증을 주장하지 않는다.
"""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import PurePosixPath

from snapshot import (SCHEMA,SnapshotReader,SnapshotError,read_file,json_object,
                      _integer,_hash,_text)

MANIFEST='s23-device-results-01.manifest.json'
KIND='HISTORICAL_OAK_REPRESENTATIVE'
CASE_CAP=32


class RepresentativeDataset:
    """최신 표시1개와 최대32개 기록의 최초 수신 metadata만 보존한다."""
    def __init__(self,root,**kwargs):
        self.reader=SnapshotReader(root,**kwargs)
        self.manifest_sha=None;self.first_seen={};self.frame_keys={}

    def close(self):
        self.reader.close();self.first_seen.clear();self.frame_keys.clear()

    def read(self,ordinal=-1):
        if self.reader.closed:return self.reader.read()
        original=None
        try:
            manifest_raw=read_file(self.reader.root,MANIFEST,256*1024)
            manifest=json_object(manifest_raw);manifest_sha=sha256(manifest_raw).hexdigest()
            if self.manifest_sha is not None and self.manifest_sha!=manifest_sha:
                raise SnapshotError('REPRESENTATIVE_MANIFEST_CHANGED')
            if (set(manifest)!={'schema_version','files'} or type(manifest['schema_version']) is not int or
                    manifest['schema_version']!=1 or type(manifest['files']) is not list or not 1<=len(manifest['files'])<=512):
                raise SnapshotError('REPRESENTATIVE_MANIFEST_SCHEMA')
            files={}
            for row in manifest['files']:
                if type(row) is not dict or set(row)!={'path','bytes','sha256'}:raise SnapshotError('REPRESENTATIVE_MANIFEST_ENTRY')
                name=_text(row['path'],'manifest path',512);path=PurePosixPath(name)
                if path.is_absolute() or not path.parts or str(path)!=name or '\\' in name or ':' in name or '..' in path.parts or name in files:
                    raise SnapshotError('REPRESENTATIVE_MANIFEST_PATH')
                _integer(row['bytes'],'artifact bytes',64*1024*1024);_hash(row['sha256']);files[name]=row
            sources=[name for name in files if name.endswith('/nn-source-receipt.json')]
            if len(sources)!=1:raise SnapshotError('REPRESENTATIVE_SINGLE_SOURCE_RECEIPT_REQUIRED')
            trial=str(PurePosixPath(sources[0]).parent);verified={}

            def checked(name,cap=256*1024):
                row=files.get(name)
                if row is None:raise SnapshotError('REPRESENTATIVE_ARTIFACT_NOT_IN_MANIFEST: '+name)
                raw=read_file(self.reader.root,name,cap)
                if len(raw)!=row['bytes'] or sha256(raw).hexdigest()!=row['sha256']:
                    raise SnapshotError('REPRESENTATIVE_HASH_OR_LENGTH: '+name)
                verified[name]=dict(path=name,byte_length=len(raw),sha256=row['sha256'])
                return raw

            source=json_object(checked(sources[0]));profile=source.get('profile',{});graph=source.get('graph_declaration',{})
            if (source.get('input_kind')!='SYNTHETIC' or source.get('live_authority') is not False or
                    source.get('metric_ready') is not False or type(profile) is not dict or
                    profile.get('input_kind')!='SYNTHETIC' or type(profile.get('camera_nodes')) is not int or profile['camera_nodes']!=0 or
                    type(graph) is not dict or type(graph.get('camera_nodes')) is not int or graph['camera_nodes']!=0 or graph.get('input_kind')!='SYNTHETIC'):
                raise SnapshotError('REPRESENTATIVE_SOURCE_SCOPE')
            source_id=_text(profile.get('source_id'),'record source');clock_id=_text(profile.get('clock_id'),'record clock')
            calibration=_hash(profile.get('calibration_id'));context=_hash(source.get('nn_context_key'))
            device=_text(source.get('device_mxid'),'OAK mxid');model=_text(source.get('model_id'),'model id')
            asset=source.get('model_asset',{})
            if type(asset) is not dict or asset.get('asset_id')!=model:raise SnapshotError('REPRESENTATIVE_MODEL_ID')
            blob=_hash(asset.get('blob_sha256'))
            if type(graph.get('asset')) is not dict or graph['asset'].get('asset_id')!=model or graph['asset'].get('blob_sha256')!=blob:
                raise SnapshotError('REPRESENTATIVE_MODEL_SOURCE')
            top=json_object(checked(trial+'/result.json'));cases=top.get('cases')
            if (top.get('profile')!='sim-representative-diagnostics-1' or top.get('actual_nn_required') is not True or
                    top.get('live_authority') is not False or top.get('closed_loop') is not False or
                    top.get('performance_accepted') is not False or top.get('physical_acceptance') is not False or
                    type(cases) is not list or not 1<=len(cases)<=CASE_CAP or type(top.get('case_count')) is not int or top['case_count']!=len(cases)):
                raise SnapshotError('REPRESENTATIVE_RESULT_SCOPE')
            names=[]
            for case in cases:
                if type(case) is not dict:raise SnapshotError('REPRESENTATIVE_CASE_DESCRIPTOR')
                name=_text(case.get('name'),'case name',128)
                if PurePosixPath(name).name!=name or name in ('.','..') or '\\' in name or ':' in name or name in names:
                    raise SnapshotError('REPRESENTATIVE_CASE_PATH')
                names.append(name)
            selected=len(cases)-1 if ordinal==-1 else _integer(ordinal,'representative ordinal',CASE_CAP-1)
            if selected>=len(cases):raise SnapshotError('REPRESENTATIVE_ORDINAL_NOT_FOUND')
            directory=trial+'/'+names[selected]
            original=checked(directory+'/result.json');case=json_object(original)
            if case!=cases[selected] or case.get('live_authority') is not False:raise SnapshotError('REPRESENTATIVE_CASE_RECEIPT')
            entries=case.get('files');case_files={}
            if type(entries) is not list or len(entries)>32:raise SnapshotError('REPRESENTATIVE_CASE_FILES')
            for row in entries:
                if type(row) is not dict or set(row)!={'path','bytes','sha256'}:raise SnapshotError('REPRESENTATIVE_CASE_FILES')
                name=row['path']
                if type(name) is not str or PurePosixPath(name).name!=name or name in ('.','..') or '\\' in name or ':' in name or name in case_files:
                    raise SnapshotError('REPRESENTATIVE_CASE_FILES')
                path=directory+'/'+name
                _integer(row['bytes'],'case artifact bytes',64*1024*1024);_hash(row['sha256'])
                if files.get(path)!=dict(path=path,bytes=row['bytes'],sha256=row['sha256']):raise SnapshotError('REPRESENTATIVE_CASE_MANIFEST_MISMATCH')
                case_files[name]=row

            def artifact(name,cap=256*1024):
                if name not in case_files:raise SnapshotError('REPRESENTATIVE_CASE_FILE_REQUIRED: '+name)
                return checked(directory+'/'+name,cap)

            media=[];stamp=None;sequence=None;pipeline=None;core=None;detections=None;packet=None;frame_key=frame_value=None
            not_executed=case.get('status')=='NOT_EXECUTED'
            if not_executed:
                if not names[selected].startswith('P06-') or case.get('actual_nn') is not False:
                    raise SnapshotError('REPRESENTATIVE_NOT_EXECUTED_SCOPE')
            else:
                input_raw=artifact('input.json');inputs=json_object(input_raw);capture=json_object(artifact('capture-receipt.json'))
                profile_raw=artifact('profile.json');json_object(profile_raw)
                if capture.get('profile_sha256')!=sha256(profile_raw).hexdigest():raise SnapshotError('REPRESENTATIVE_CAPTURE_PROFILE_HASH')
                pipeline=json_object(artifact('metrics.json'));core=json_object(artifact('core-summary.json'))
                detections=json_object(artifact('detections.json'));packet=json_object(artifact('nn-packet.json'))
                if (inputs.get('kind')!='SYNTHETIC' or inputs.get('calibration_id')!=calibration or
                        capture.get('calibration_id')!=calibration or capture.get('input_kind')!='SYNTHETIC' or
                        capture.get('live_authority') is not False or capture.get('diagnostic_only') is not True or
                        capture.get('source_id')!=source_id or capture.get('clock_id')!=clock_id or
                        capture.get('timestamp_basis')!='original_host_pose_sample_not_OAK_exposure'):
                    raise SnapshotError('REPRESENTATIVE_CAPTURE_SOURCE')
                if (pipeline.get('actual_nn') is not True or pipeline.get('live_authority') is not False or
                        pipeline.get('geometry_expired')!=case.get('geometry_expired') or pipeline.get('status')!=case.get('status') or
                        core.get('kind')!='SYNTHETIC' or core.get('live_authority') is not False or core.get('calibration_id')!=calibration):
                    raise SnapshotError('REPRESENTATIVE_METRIC_SOURCE')
                rgb=inputs.get('rgb')
                if type(rgb) is not dict:raise SnapshotError('REPRESENTATIVE_RGB_REQUIRED')
                original_stamp=rgb.get('stamp')
                if type(original_stamp) is not dict:raise SnapshotError('REPRESENTATIVE_ORIGINAL_STAMP')
                captured=original_stamp.get('captured_us')
                if type(captured) is not list or len(captured)!=2:raise SnapshotError('REPRESENTATIVE_CAPTURE_INTERVAL')
                lo,hi=(_integer(t,'capture time') for t in captured);sequence=_integer(original_stamp.get('sequence'),'original sequence')
                available=_integer(original_stamp.get('available_us'),'original available')
                if (not lo<=hi<=available or original_stamp.get('source_id')!=source_id or original_stamp.get('clock_id')!=clock_id or
                        original_stamp.get('stream_id')!='rgb_video' or sequence!=_integer(capture.get('sequence'),'receipt sequence') or
                        captured!=[_integer(t,'receipt capture') for t in capture.get('captured_j_us',[])] or
                        available!=_integer(capture.get('completed_j_us'),'receipt completed') or core.get('rgb_captured_us')!=captured):
                    raise SnapshotError('REPRESENTATIVE_ORIGINAL_STAMP_MISMATCH')
                if packet.get('context_key')!=context or sequence!=_integer(packet.get('sequence'),'NN sequence') or packet.get('stream')!='nn_output':
                    raise SnapshotError('REPRESENTATIVE_NN_SOURCE')
                geometry_e=_integer(capture.get('expires_j_us'),'capture original expiry')
                nn_e=_integer(case.get('nn_expires_j_us'),'case NN original expiry')
                completed=_integer(pipeline.get('completed_j_us'),'pipeline completed')
                nn_input=_integer(pipeline.get('nn_input_received_j_us'),'NN input received')
                nn_output=_integer(pipeline.get('nn_output_received_j_us'),'NN output received')
                decoded=_integer(pipeline.get('nn_decode_completed_j_us'),'NN decode completed')
                if (geometry_e!=_integer(case.get('geometry_expires_j_us'),'case geometry original expiry') or
                        geometry_e!=_integer(pipeline.get('geometry_expires_j_us'),'pipeline geometry original expiry') or
                        nn_e!=_integer(pipeline.get('nn_expires_j_us'),'pipeline NN original expiry') or
                        not available<=geometry_e<=nn_e or not available<=nn_input<=nn_output<=decoded<=completed or
                        nn_output!=_integer(packet.get('received_j_us'),'packet received') or
                        type(pipeline.get('geometry_expired')) is not bool or pipeline['geometry_expired']!=(completed>geometry_e) or
                        pipeline.get('evaluation_time_basis')!='actual_monotonic_no_restamp'):
                    raise SnapshotError('REPRESENTATIVE_ORIGINAL_TIME_OR_EXPIRY_MISMATCH')
                stamp=dict(domain=clock_id,epoch=source_id,us=lo)

                def image(descriptor,name,panel,width,height,encoding):
                    if (type(descriptor) is not dict or (descriptor.get('width'),descriptor.get('height'),descriptor.get('encoding'))!=(width,height,encoding)):
                        raise SnapshotError('REPRESENTATIVE_MEDIA_LAYOUT')
                    raw=artifact(name,1024*1024)
                    expected=width*height*(3 if encoding=='NV12' else 4)//2
                    if len(raw)!=expected or descriptor.get('bytes')!=len(raw) or descriptor.get('sha256')!=sha256(raw).hexdigest():
                        raise SnapshotError('REPRESENTATIVE_MEDIA_RECEIPT')
                    media.append(dict(panel=panel,path=directory+'/'+name,sha256=descriptor['sha256'],byte_length=len(raw),
                        width=width,height=height,encoding=encoding,stamp=stamp,source_sequence=str(sequence),
                        original_stamp=descriptor['stamp'],depth_unit='mm' if encoding=='RAW16_LE' else None))
                    return raw
                image(rgb,'rgb.nv12','rgb',640,360,'NV12')
                if capture.get('rgb_sha256')!=rgb['sha256']:raise SnapshotError('REPRESENTATIVE_CAPTURE_RGB_HASH')
                # The producer saves capture/rendered depth before applying its
                # NONE/ZERO input fault. Its receipt does not hash faulted input.
                rendered=case_files.get('rendered-depth.raw16le')
                if rendered is None or capture.get('depth_sha256')!=rendered['sha256']:
                    raise SnapshotError('REPRESENTATIVE_CAPTURE_RENDERED_DEPTH_DECLARATION')
                depth=inputs.get('depth')
                if depth is not None:
                    other=depth.get('stamp') if type(depth) is dict else None
                    if (type(other) is not dict or other.get('stream_id')!='depth_right' or
                            any(other.get(k)!=original_stamp.get(k) for k in ('source_id','clock_id','sequence','captured_us','available_us'))):
                        raise SnapshotError('REPRESENTATIVE_DEPTH_STAMP')
                    payload=image(depth,'input-depth.raw16le','depth',640,480,'RAW16_LE')
                    mode=inputs.get('depth_fault')
                    if mode=='NATIVE':
                        if capture.get('depth_sha256')!=depth['sha256']:raise SnapshotError('REPRESENTATIVE_CAPTURE_DEPTH_HASH')
                    elif mode=='ZERO':
                        if any(payload):raise SnapshotError('REPRESENTATIVE_ZERO_DEPTH_INPUT')
                    else:raise SnapshotError('REPRESENTATIVE_DEPTH_FAULT_MODE')
                elif inputs.get('depth_fault')!='NONE':raise SnapshotError('REPRESENTATIVE_DEPTH_ABSENCE')
                png=artifact('four-panels.png',1024*1024)
                for panel,role in (('yolop','REPRESENTATIVE_YOLOP_TOP_LEFT'),('grid','REPRESENTATIVE_GRID_BOTTOM_LEFT')):
                    media.append(dict(panel=panel,path=directory+'/four-panels.png',sha256=sha256(png).hexdigest(),byte_length=len(png),
                        width=1280,height=720,encoding='PNG',display_crop=role,stamp=stamp,source_sequence=str(sequence),original_stamp=original_stamp))
                frame_key=(source_id,clock_id,sequence);frame_value=(names[selected],sha256(input_raw).hexdigest())
                if frame_key in self.frame_keys and self.frame_keys[frame_key]!=frame_value:raise SnapshotError('REPRESENTATIVE_SEQUENCE_CONFLICT')
                if len(self.frame_keys)>=CASE_CAP and frame_key not in self.frame_keys:raise SnapshotError('REPRESENTATIVE_SEQUENCE_CAP')
            body=dict(schema=SCHEMA,source=dict(id=source_id,epoch=clock_id,sequence=None if sequence is None else str(sequence),kind=KIND,stamp=stamp),
                operation=None,map=dict(layers=[]),media=media,
                recording=dict(case_name=names[selected],ordinal=selected,case_count=len(cases),status=case.get('status'),
                    input_scope='합성 RGB·가상 native depth → 실제 OAK 추론. 카메라 실측/실차/LIVE 아님'),
                identities=dict(device_mxid=device,model_id=model,blob_sha256=blob,nn_context_key=context,calibration_id=calibration,
                    source_receipt_sha256=files[sources[0]]['sha256'],manifest_sha256=manifest_sha),
                metrics=dict(case_status=case.get('status'),case_reason=case.get('reason'),pipeline=pipeline,
                    depth_input_mode=None if not_executed else inputs.get('depth_fault'),
                    road_status=None if core is None else core.get('road_status'),
                    neural_counts=None if core is None else (core.get('neural_layer') or {}).get('counts'),
                    frame_local_map_counts=None if core is None else core.get('map_counts'),detections=detections,
                    nn_packet_metadata=packet,recorded_run_status=top.get('status'),geometry_expired_cases=top.get('geometry_expired_cases')),
                raw_payload=dict(path=directory+'/result.json',sha256=sha256(original).hexdigest(),byte_length=len(original)),
                verified_artifacts=list(verified.values()),
                limitations=['HISTORICAL_ONLY','HOST_FED_SYNTHETIC_NOT_OAK_CAMERA_EXPOSURE','NO_LIVE_AUTHORITY_OR_HIL_OR_FPS_ACCEPTANCE',
                    'SELECTED_ARTIFACTS_ONLY_NOT_WHOLE_ARCHIVE_VERIFICATION','RAW_NN_BUFFER_NOT_READ_OR_VERIFIED','ORACLE_AND_EVALUATION_ONLY_NOT_READ',
                    'MODEL_BLOB_NOT_IN_ARCHIVE_NOT_READ_OR_VERIFIED','RENDERED_DEPTH_DECLARATION_ONLY_BYTES_NOT_READ',
                    'GT_PANEL_NOT_DISPLAYED','DEPTH_NONE_NOT_REPLACED_BY_RENDERED_DEPTH'])
            packed=json.dumps(body,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
            result=self.reader.accept(packed,historical=True)
            identity=(selected,sha256(original).hexdigest())
            if identity not in self.first_seen:
                if len(self.first_seen)>=CASE_CAP:raise SnapshotError('REPRESENTATIVE_RECEIPT_CAP')
                self.first_seen[identity]=self.reader.clock()
            first=self.first_seen[identity];snapshot=replace(result.snapshot,first_received=first)
            self.reader.current=snapshot;self.manifest_sha=manifest_sha
            if frame_key is not None:self.frame_keys[frame_key]=frame_value
            return replace(result,snapshot=snapshot,received_age_s=max(0,self.reader.clock()-first))
        except (OSError,ValueError,KeyError,TypeError,AttributeError) as error:return self.reader.failure(error,original)
