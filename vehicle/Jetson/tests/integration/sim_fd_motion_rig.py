"""실제 B08 FD/clock/odometry와 가상 측정 plant를 잇는 격리 진단 rig다.

Gateway·clock install·Vehicle을 대역으로 만들지 않는다. caller가 선택한 coherent
B08 패키지와 명시 개발 정책을 사용한다. renderer GT/실물/Authority/움직이는
지도/FREE는 이 첫 단계의 수락 범위가 아니다. 제품 변경이나 broker I/O도 없다.
"""

from base64 import b64encode
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import socket
import time
from uuid import UUID


def _uuid(value):
    if type(value) is not str or str(UUID(value)) != value or not UUID(value).int:
        raise ValueError('caller must declare original canonical source UUIDs')
    return value


def _seconds(value, maximum):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError('caller must declare a finite bounded wait policy')
    return value


@dataclass(frozen=True, slots=True)
class FdMotionObservation:
    """검산 시점의 원 bytes다. 나중 도착·정상 갱신으로 원 E를 옮기지 않는다."""

    observed_pi_us: int
    motion_profile_sha256: str
    pi_session: str
    device_session: str
    odom_epoch: str
    clock_epoch: str
    clock_sequence: int
    clock_valid_until_pi_us: int
    device_lease_cutoff_us: int
    install_payload: bytes
    peer_produced_ack_wire: bytes
    clock_ack_verified_by_gateway: bool
    vehicle_bytes: bytes
    vehicle_window: tuple
    sensor_wire: bytes
    steering_provenance: bytes
    stop_command_payload: bytes
    stop_feedback_payload: bytes
    omitted_prepared_evidence: int

    @property
    def fields(self):
        """원 payload·producer ACK와 Gateway 게시 검산 상태를 구분하는 독립 JSON 사본이다."""
        encode=lambda raw:dict(base64=b64encode(raw).decode('ascii'),bytes=len(raw),sha256=sha256(raw).hexdigest())
        value=dict(profile='sim-fd-motion-observation-1',scope='ACTUAL_B08_BYTES_SOFTWARE_PLANT_DIAGNOSTIC',
            observed_pi_us=str(self.observed_pi_us),motion_profile_sha256=self.motion_profile_sha256,
            pi_session=self.pi_session,device_session=self.device_session,odom_epoch=self.odom_epoch,
            clock_epoch=self.clock_epoch,clock_sequence=str(self.clock_sequence),
            clock_valid_until_pi_us=str(self.clock_valid_until_pi_us),
            device_lease_cutoff_us=str(self.device_lease_cutoff_us),
            install_payload=encode(self.install_payload),
            peer_produced_ack_wire=encode(self.peer_produced_ack_wire),
            clock_ack_verified_by_gateway=self.clock_ack_verified_by_gateway,
            clock_ack_scope='PEER_ORIGINAL_ACK_BYTES_AND_SEPARATE_PUBLISHED_GATEWAY_VALIDATION',
            vehicle=encode(self.vehicle_bytes),vehicle_window=[encode(raw) for raw in self.vehicle_window],
            sensor_wire=encode(self.sensor_wire),steering_provenance=encode(self.steering_provenance),
            stop_command_payload=encode(self.stop_command_payload),stop_feedback_payload=encode(self.stop_feedback_payload),
            omitted_prepared_evidence=self.omitted_prepared_evidence,
            physical_acceptance=False,authority=False,returned_pi_j_trust=False,metric_ready=False,
            free_positive=False,closed_loop=False)
        raw=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')
        if len(raw)>1024**2:raise ValueError('bounded FD diagnostic projection exceeds1MiB')
        return json.loads(raw)


class SimFdMotionRig:
    """caller의 실제 monotonic 시간으로만 진행한다. start/wait_ready/close를 유한하게 사용한다.

    current_vehicle()/poll()은 실제 feedback_events를 최대8개씩 소비한다. 긴 후속
    계산에서도 caller actor가 이를 주기적으로 호출해야 하며 별도 수신 thread는 없다.
    source/lease/seq를 조립하거나 odometry.observe_mapped를 직접 부르지 않는다.
    """

    def __init__(self,motion_profile,protocol_profile,*,host_boot,gateway_instance,
                 device_session,plant_epoch,initial_pose,stop_frame,command_timeout_s,
                 startup_timeout_s,poll_interval_s,minimum_stationary_samples,
                 peer_close_timeout_s,owner_cleanup_timeout_s,
                 clock=time.monotonic,device_clock=None):
        from xenova_gateway import feedback,feedback3,scene_authority
        from xenova_gateway.frame import StopFrame
        from xenova_gateway.odometry import OdometryConfig,VehicleOdometry
        from xenova_gateway.output_owner import OutputOwner
        from xenova_drive_world import schema,decode
        from xenova_vehicle.simulation.motion_profile import DevelopmentMotionProfile
        from xenova_vehicle.simulation.plant import VirtualPlant
        from xenova_vehicle.simulation.feedback_peer import VirtualFeedbackPeer
        if (type(motion_profile) is not DevelopmentMotionProfile or motion_profile.steering_sensor_profile is None
                or type(protocol_profile) is not feedback3.Feedback3Profile):
            raise ValueError('explicit measured development variant and coherent B08 policy required')
        if type(stop_frame) is not StopFrame or type(stop_frame.witness) is not dict:
            raise ValueError('caller must provide the original STOP frame and source witness')
        for value in (host_boot,gateway_instance,device_session,plant_epoch):_uuid(value)
        if stop_frame.witness.get('gateway_instance') != gateway_instance:
            raise ValueError('STOP witness must name the actual caller Gateway instance')
        if type(minimum_stationary_samples) is not int or not 2<=minimum_stationary_samples<=128:
            raise ValueError('caller must declare minimum stationary samples2..128')
        _seconds(command_timeout_s,1);_seconds(startup_timeout_s,10);_seconds(poll_interval_s,.01)
        _seconds(peer_close_timeout_s,5);_seconds(owner_cleanup_timeout_s,1)
        if not callable(clock):raise ValueError('actual Gateway monotonic clock callable required')
        self.profile,self.protocol=motion_profile,protocol_profile
        self.host,self.gateway=host_boot,gateway_instance
        self.device_session,self.plant_epoch=device_session,plant_epoch
        self.initial_pose,self.stop_frame=initial_pose,stop_frame
        self.command_timeout_s,self.startup_timeout_s=command_timeout_s,startup_timeout_s
        self.poll_interval_s,self.minimum_samples=poll_interval_s,minimum_stationary_samples
        self.peer_close_timeout_s,self.owner_cleanup_timeout_s=peer_close_timeout_s,owner_cleanup_timeout_s
        self.clock,self.device_clock=clock,device_clock
        self.fb,self.f3,self.sa=feedback,feedback3,scene_authority
        self._schema,self._decode=schema,decode
        self._OdometryConfig,self._VehicleOdometry=OdometryConfig,VehicleOdometry
        self._OutputOwner,self._VirtualPlant,self._VirtualFeedbackPeer=OutputOwner,VirtualPlant,VirtualFeedbackPeer
        self.plant=self.peer=self.sink=self.owner=None
        self._caller_sockets=()
        self._stop_submission=None
        self._events=0
        self._started=False;self._closed=False
        self.last_reason='NOT_STARTED'
        self.cleanup_warnings=[]

    def start(self):
        if self._started or self._closed:raise RuntimeError('rig already used; no automatic session replacement')
        self._started=True
        a,b=socket.socketpair();self._caller_sockets=(a,b)
        a.setblocking(False);b.setblocking(False)
        try:
            kwargs={} if self.device_clock is None else dict(clock=self.device_clock)
            self.plant=self._VirtualPlant(self.profile,initial_pose=self.initial_pose,epoch=self.plant_epoch,**kwargs)
            self.peer=self._VirtualFeedbackPeer(b.fileno(),self.plant,feedback=self.fb,feedback3=self.f3,
                scene_authority=self.sa,protocol_profile=self.protocol,
                configuration_sha256=self.protocol.vehicle_config_sha256,
                firmware_sha256=self.protocol.firmware_sha256,device_session=self.device_session,
                capabilities=self.f3.REQUIRED_CAPS|self.fb.CAP_STEERING|0x400)
            odometry=self._VehicleOdometry(self._OdometryConfig(**self.profile.odometry_arguments),self.host)
            self.sink=self.f3.Feedback3SerialSink('development-fd',development_fd=a.fileno(),
                timeout_ms=math.ceil(self.command_timeout_s*1000),odometry=odometry,
                profile=self.protocol,clock=self.clock)
            # 두 owner의 dup가 모두 생긴 뒤 caller 원 endpoint를 닫아 EOF 수명을 숨기지 않는다.
            a.close();b.close()
            self.peer.start()
            self.owner=self._OutputOwner(self.sink,command_timeout_s=self.command_timeout_s,clock=self.clock)
            return self
        except BaseException:
            self.close()
            raise

    def poll(self):
        """원 owner가 만든 feedback event만 소비한다. wire parser나 clock FSM을 직접 돌리지 않는다."""
        if self.owner is None:return None
        events=self.owner.feedback_events()
        self._events+=len(events)
        return self.owner.device_view()

    def current_vehicle(self):
        """실제 MatchedStop의 원 codec bytes와 동일 SENSOR/installed lease일 때만 반환한다."""
        if self._closed or self.owner is None:return None
        view=self.poll()
        peer_state=self.peer.snapshot()
        if (self.owner.closing or not self.owner.thread.is_alive() or peer_state['closed']
                or peer_state['simulation_stopped'] or not peer_state['thread_alive']):
            self.last_reason='SOURCE_OWNER_NOT_ALIVE';return None
        if self.owner.first_failure is not None or self.peer.failure is not None:
            self.last_reason='ACTUAL_OWNER_OR_PEER_FAILURE';return None
        if not view.get('clock_ready') or not view.get('ready'):
            self.last_reason='WAIT_ACTUAL_CLOCK_ACK';return None
        sample=view.get('vehicle_sample')
        if sample is None:
            self.last_reason='WAIT_ACTUAL_FRESH_VEHICLE';return None
        if self._stop_submission is None:
            self.last_reason='WAIT_ORIGINAL_STOP';return None
        if self._stop_submission.outcome() is None:
            self.last_reason='WAIT_ORIGINAL_STOP_SUBMISSION';return None
        facts=view.get('stop_facts_source')
        matched=None if facts is None else facts.get('matched')
        if (type(matched) is not self.sa.MatchedStop or len(matched.vehicle_window)<self.minimum_samples):
            self.last_reason='WAIT_ACTUAL_STATIONARY_WINDOW';return None
        now=int(self.clock()*1e6)
        try:
            vehicle=matched.check(dict(vehicle=sample['vehicle'],calibration=sample['calibration'],
                odom_epoch=sample['odom_epoch']),self.host,self.gateway,self.stop_frame.generation,
                sample['pi_session'],sample['device_session'],self.protocol.stationary,now)
        except ValueError:
            self.last_reason='WAIT_CHECKED_STATIONARY_WINDOW';return None
        if (vehicle.sequence,vehicle.source_sample,vehicle.source_us)!=(sample['sequence'],sample['source_sample'],sample['source_us']):
            self.last_reason='WAIT_CURRENT_MATCHED_REPORT';return None
        records,omitted=self.peer.evidence_snapshot()
        sensor_record=None;fields=None
        for record in reversed(records):
            if record.kind!=self.f3.SENSOR or (record.source_sample,record.source_us)!=(vehicle.source_sample,vehicle.source_us):continue
            kind,payload=self.fb.unpack(record.wire)
            candidate=self.f3.SENSOR3.unpack(payload)
            if (candidate[0],candidate[1],candidate[2],candidate[3],candidate[4],candidate[5]) != (
                    UUID(vehicle.pi_session).bytes,UUID(vehicle.device_session).bytes,vehicle.source_sample,
                    vehicle.source_us,vehicle.left_ticks,vehicle.right_ticks):continue
            sensor_record,fields=record,candidate
            break
        if sensor_record is None or sensor_record.steering_provenance is None:
            self.last_reason='WAIT_ORIGINAL_MEASURED_SENSOR_WIRE';return None
        if fields[6]!=3 or fields[8]<=0 or not vehicle.HasField('measured_steering_rad'):
            raise ValueError('measured variant lost original quality/error')
        # world.proto의 두 필드는 float32다. 원 mrad를 같은 공용 형식에 투영해
        # 정확히 비교하며 binary64와의 차이를 새 오차 허용치로 숨기지 않는다.
        projected=self._schema.VehicleSample(measured_steering_rad=fields[7]/1000,
            steering_error_rad=fields[8]/1000)
        if (vehicle.measured_steering_rad,vehicle.steering_error_rad)!=(
                projected.measured_steering_rad,projected.steering_error_rad):
            raise ValueError('actual Vehicle changed original steering measurement')
        sequence=fields[10]
        clock_evidence=None;install=None
        for original in self.peer.clock_evidence_snapshot():
            candidate=self.f3.INSTALL3.unpack(original.install_payload)
            if (candidate[0],candidate[1],candidate[2],candidate[3])==(fields[0],fields[1],fields[9],sequence):
                clock_evidence,install=original,candidate
                break
        if clock_evidence is None:
            self.last_reason='WAIT_ORIGINAL_ACCEPTED_INSTALL';return None
        kind,ack_payload=self.fb.unpack(clock_evidence.peer_produced_ack_wire)
        ack=self.f3.CLOCK_ACK3.unpack(ack_payload)
        if (kind!=self.f3.CLOCK_ACK or ack[:5]!=install[:5] or ack[5:7]!=(1,0)
                or ack[8]!=install[9] or not 0<ack[7]<install[9] or ack[7]>vehicle.source_us):
            raise ValueError('original peer ACK differs from its accepted INSTALL/source time')
        epoch=str(UUID(bytes=install[2]))
        def same_published_clock(snapshot):
            return bool(snapshot.get('ready') and snapshot.get('clock_ready')
                and snapshot.get('clock_epoch')==epoch and snapshot.get('clock_lease_revision')==sequence
                and snapshot.get('clock_valid_until_pi_us')==install[8])
        if not same_published_clock(view):
            self.last_reason='WAIT_PUBLISHED_GATEWAY_ACK_OF_ORIGINAL_CLOCK';return None
        final_view=self.owner.device_view()
        current=final_view.get('vehicle_sample')
        if (not same_published_clock(final_view) or current is None
                or (current['pi_session'],current['device_session'],current['odom_epoch'],current['calibration'])
                !=(vehicle.pi_session,vehicle.device_session,vehicle.odom_epoch,vehicle.calibration)):
            self.last_reason='PUBLISHED_CLOCK_OR_SOURCE_CHANGED';return None
        final_now=int(self.clock()*1e6)
        if (not final_now<install[8] or final_now-(vehicle.pi_sample_us-vehicle.clock_error_us)>
                self.profile.fields['odometry']['maximum_sample_age_us']):
            self.last_reason='ORIGINAL_SOURCE_OR_LEASE_EXPIRED';return None
        if (self.owner.first_failure is not None or self.peer.failure is not None
                or self.owner.closing or self.peer.snapshot()['closed']):
            self.last_reason='SOURCE_FAILED_DURING_DIAGNOSTIC';return None
        self.last_reason='ACTUAL_CLOCK_AND_STATIONARY_VEHICLE'
        return FdMotionObservation(final_now,self.profile.sha256,vehicle.pi_session,vehicle.device_session,
            vehicle.odom_epoch,epoch,sequence,install[8],install[9],clock_evidence.install_payload,
            clock_evidence.peer_produced_ack_wire,True,
            matched.vehicle_window[-1],matched.vehicle_window,sensor_record.wire,sensor_record.steering_provenance,
            matched.command_payload,matched.feedback_payload,omitted)

    def wait_ready(self):
        if not self._started:self.start()
        deadline=self.clock()+self.startup_timeout_s
        while self.clock()<deadline:
            view=self.poll()
            if self.owner.first_failure is not None or self.peer.failure is not None:
                raise RuntimeError('actual FD motion startup failed: '+json.dumps(self.diagnostics(),sort_keys=True))
            if (self._stop_submission is None and view.get('clock_ready') and view.get('vehicle_sample') is not None):
                self._stop_submission=self.owner.submit_stop(self.stop_frame,self.clock())
            observation=self.current_vehicle()
            if observation is not None:return observation
            time.sleep(self.poll_interval_s)
        raise TimeoutError('original FD motion startup deadline: '+json.dumps(self.diagnostics(),sort_keys=True))

    def force_peer_eof(self,*,timeout_s):
        """명시 simulation owner 종료로 실제 FD EOF를 만든다. 물리 정지 성공으로 해석하지 않는다."""
        _seconds(timeout_s,5)
        self.peer.close(timeout_s=timeout_s)
        deadline=self.clock()+timeout_s
        while self.clock()<deadline:
            view=self.poll()
            if (self.owner.first_failure is not None and view.get('fault')=='DEVICE_LINK_UNAVAILABLE'
                    and view.get('ready') is False and view.get('vehicle_sample') is None
                    and view.get('physical_stop') is None):
                return dict(scope='ACTUAL_FD_EOF_NEGATIVE',fault=view['fault'],ready=False,
                    vehicle_sample=None,physical_stop=None,owner_failure=type(self.owner.first_failure).__name__,
                    read_fd_detached=self.owner._read_fd is None,peer=self.peer.snapshot())
            time.sleep(self.poll_interval_s)
        raise TimeoutError('actual EOF was not observed within caller deadline')

    def diagnostics(self):
        view={} if self.owner is None else self.owner.device_view()
        return dict(last_reason=self.last_reason,caller_endpoints_closed=bool(self._caller_sockets)
            and all(sock.fileno()==-1 for sock in self._caller_sockets),feedback_events_consumed=self._events,
            gateway_ready=view.get('ready'),clock_ready=view.get('clock_ready'),fault=view.get('fault'),
            vehicle_present=view.get('vehicle_sample') is not None,
            owner_failure=None if self.owner is None or self.owner.first_failure is None
                else type(self.owner.first_failure).__name__+': '+str(self.owner.first_failure)[:160],
            owner_thread_alive=self.owner is not None and self.owner.thread.is_alive(),
            peer=None if self.peer is None else self.peer.snapshot(),cleanup_warnings=list(self.cleanup_warnings))

    def close(self):
        if self._closed:return
        try:
            if self.owner is not None:
                try:self.owner.close()
                except BaseException as error:self.cleanup_warnings.append(type(error).__name__+': '+str(error)[:160])
            elif self.sink is not None:self.sink.close()
        finally:
            try:
                if self.peer is not None:self.peer.close(timeout_s=self.peer_close_timeout_s)
            finally:
                for sock in self._caller_sockets:sock.close()
        if self.owner is not None and self.owner.thread.is_alive():self.owner.thread.join(self.owner_cleanup_timeout_s)
        if self.owner is not None and self.owner.thread.is_alive():
            raise RuntimeError('actual Gateway thread termination remains unconfirmed')
        if self.peer is not None and self.peer.snapshot()['thread_alive']:
            raise RuntimeError('actual physics peer thread termination remains unconfirmed')
        self._closed=True

    def __enter__(self):return self.start()
    def __exit__(self,*unused):self.close()


def run_sim_fd_motion(*args,check_eof,eof_timeout_s=None,**kwargs):
    """유한 단독 진단용이다. 반환 원본은 종료된 source의 역사 자료이며 새 lease가 아니다."""
    if type(check_eof) is not bool:raise ValueError('explicit diagnostic EOF selection required')
    if check_eof:_seconds(eof_timeout_s,5)
    rig=SimFdMotionRig(*args,**kwargs)
    try:
        observation=rig.wait_ready()
        negative=rig.force_peer_eof(timeout_s=eof_timeout_s) if check_eof else None
        value=dict(observation=observation.fields,eof=negative)
    finally:rig.close()
    return dict(value,cleanup=rig.diagnostics(),source_alive_after_return=False,
        scope='HISTORICAL_FINITE_DIAGNOSTIC_NOT_CURRENT_AUTHORITY')
