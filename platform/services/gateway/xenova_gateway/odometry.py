"""Single Pi odometry writer from cumulative rear-wheel encoder observations.

No command integration and no steering measurement is invented. The optional
feedback-2 probe bounds sample time by a Pi send/receive interval. Geometry,
encoder scale, plausibility and clock budgets must be explicitly supplied.
"""
from dataclasses import dataclass
from collections import deque
import math
import struct
from uuid import UUID,uuid4
from .absolute_meter import AbsoluteWheelMeter

ODOM_PROBE=0x0f04
ODOM_REPORT=0x0f03
CAP_ODOMETRY=32
PROBE=struct.Struct('<16s16sQ')
SAMPLE=struct.Struct('<16s16sQQQqqBiH')

class OdometryError(ValueError):pass

@dataclass(frozen=True)
class OdometryConfig:
    vehicle: str
    calibration: str
    metres_per_tick: float
    track_width_m: float
    wheelbase_m: float
    maximum_speed_mps: float
    maximum_clock_error_us: int
    maximum_sample_age_us: int
    sample_period_ms: int
    encoder_bits: int
    position_error_m: float
    heading_error_rad: float
    distance_error_ratio: float
    heading_error_per_m: float
    metres_per_tick_upper: float | None = None

    def __post_init__(self):
        for key in ('vehicle','calibration'):
            value=getattr(self,key)
            if not isinstance(value,str) or not 1<=len(value)<=64:raise OdometryError('ODOM_CONFIG_ID')
        for key in ('metres_per_tick','track_width_m','wheelbase_m','maximum_speed_mps',
                    'position_error_m','heading_error_rad','distance_error_ratio','heading_error_per_m'):
            value=getattr(self,key)
            if type(value) not in (int,float) or not math.isfinite(value) or value<=0:raise OdometryError('ODOM_CONFIG_GEOMETRY')
        for key in ('maximum_clock_error_us','maximum_sample_age_us','sample_period_ms'):
            if type(getattr(self,key)) is not int or not 0<getattr(self,key)<=2**31-1:raise OdometryError('ODOM_CONFIG_TIME')
        if self.encoder_bits not in (32,64) or type(self.encoder_bits) is not int:raise OdometryError('ODOM_CONFIG_COUNTER')
        if (self.metres_per_tick_upper is not None and (type(self.metres_per_tick_upper) not in (int,float)
                or not math.isfinite(self.metres_per_tick_upper) or self.metres_per_tick_upper<self.metres_per_tick)):
            raise OdometryError('ODOM_CONFIG_SCALE_UPPER')

    @classmethod
    def load(cls,path,*,uid=0):
        import os,stat
        from .ipc_contract import decode
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=uid or info.st_mode&0o077 or info.st_size>2048:
                raise OdometryError('ODOM_CONFIG_STORAGE')
            value=decode(stream.read(2049))
        fields=set(cls.__dataclass_fields__)
        if not fields-{'metres_per_tick_upper'}<=set(value)<=fields:raise OdometryError('ODOM_CONFIG_FIELDS')
        return cls(**value)

class VehicleOdometry:
    def __init__(self,config,host_boot):
        self.config=config;self.host_boot=str(UUID(host_boot));self.epoch=str(uuid4())
        self.device=None;self.previous=None;self.pose=(0.,0.,0.);self.sequence=0
        self.samples=deque(maxlen=128);self.pending={};self.last_probe=None
        self.errors={};self.first_error=None;self.latest=None
        self.travel=0.
        self.absolute=AbsoluteWheelMeter(config,self.host_boot)

    def fail(self,code,*,invalidate_sample=True):
        # Only finite product reason codes, never per-input state or raw body.
        self.errors[code]=self.errors.get(code,0)+1
        if self.first_error is None:self.first_error=code
        if invalidate_sample:self.latest=None;self.absolute.invalidate()
        raise OdometryError(code)

    def device_session(self,session):
        if session==self.device:return
        self.device=session;self.epoch=str(uuid4());self.previous=None
        self.pose=(0.,0.,0.);self.sequence=0;self.samples.clear();self.pending.clear();self.latest=None
        self.travel=0.
        self.absolute.invalidate()

    def probe(self,pi_session,now_us):
        if getattr(self,'clock_profile3',False):return None
        if not self.device:return None
        if self.last_probe is not None and now_us-self.last_probe<self.config.sample_period_ms*1000:return None
        self.pending={k:v for k,v in self.pending.items() if now_us-k<=self.config.maximum_sample_age_us}
        if len(self.pending)>=2:return None
        self.pending[now_us]=None;self.last_probe=now_us
        return PROBE.pack(UUID(pi_session).bytes,UUID(self.device).bytes,now_us)

    def mark_submitted(self,echo,sent_us):
        """Bind the original probe to its first pre-write time, not a send ACK.

        The original echo still owns lookup, uniqueness and expiry. Duplicate
        marking cannot move the bound; direct users that never mark keep the
        wider creation-to-receive interval. No failed write rolls this back.
        """
        if (type(echo) is not int or type(sent_us) is not int or
                not 0<=echo<=sent_us<2**64):
            raise OdometryError('ODOM_PROBE_SUBMISSION')
        if echo not in self.pending:raise OdometryError('ODOM_PROBE_REFERENCE')
        if self.pending[echo] is not None:return False
        if sent_us-echo>self.config.maximum_sample_age_us:
            del self.pending[echo]
            return self.fail('ODOM_PROBE_EXPIRED',invalidate_sample=False)
        self.pending[echo]=sent_us
        return True

    def receive(self,pi_session,payload,now_us):
        try:pi,device,echo,source_us,sample,left,right,quality,steer_mrad,steer_error_mrad=SAMPLE.unpack(payload)
        except struct.error:return self.fail('ODOM_FRAME')
        if str(UUID(bytes=pi))!=pi_session or str(UUID(bytes=device))!=self.device:return self.fail('ODOM_SESSION')
        # Reject this probe without renewing or erasing an independently valid
        # sample. A reversed local clock still revokes that earlier evidence.
        if echo not in self.pending or now_us<echo:
            return self.fail('ODOM_PROBE_REFERENCE',invalidate_sample=now_us<echo)
        sent=self.pending[echo]
        if sent is not None and now_us<sent:return self.fail('ODOM_PROBE_REFERENCE')
        del self.pending[echo]
        if now_us-echo>self.config.maximum_sample_age_us:
            return self.fail('ODOM_PROBE_EXPIRED',invalidate_sample=False)
        submitted=echo if sent is None else sent
        error=(now_us-submitted+1)//2
        if error>self.config.maximum_clock_error_us:
            return self.fail('ODOM_CLOCK_UNCERTAIN',invalidate_sample=False)
        return self.observe_mapped(pi_session,self.device,source_us,sample,left,right,quality,steer_mrad,
            steer_error_mrad,submitted,now_us)

    def observe_mapped(self,pi_session,device,source_us,sample,left,right,quality,steer_mrad,steer_error_mrad,lower_pi_us,upper_pi_us):
        """feedback3 원 clock interval의 센서 표본. 도착시각으로 관측 나이를 바꾸지 않는다."""
        if device!=self.device or lower_pi_us<0 or upper_pi_us<lower_pi_us:return self.fail('ODOM_SESSION')
        error=(upper_pi_us-lower_pi_us+1)//2
        if error>self.config.maximum_clock_error_us:return self.fail('ODOM_CLOCK_UNCERTAIN',invalidate_sample=False)
        if not quality&1 or quality&~7 or quality&6==6 or not sample or not source_us:return self.fail('ODOM_SENSOR_QUALITY')
        if quality&6 and (not steer_error_mrad or abs(steer_mrad)>1400):return self.fail('ODOM_STEERING_QUALITY')
        if not quality&6 and (steer_mrad or steer_error_mrad):return self.fail('ODOM_STEERING_PRESENCE')
        if not -(2**(self.config.encoder_bits-1))<=left<2**(self.config.encoder_bits-1) or not -(2**(self.config.encoder_bits-1))<=right<2**(self.config.encoder_bits-1):
            return self.fail('ODOM_COUNTER_RANGE')
        pi_us=(lower_pi_us+upper_pi_us)//2
        current=(sample,source_us,left,right,pi_us)
        x,y,yaw=self.pose;velocity=0.;steering=None
        if self.previous is not None:
            old=self.previous
            if sample<=old[0] or source_us<=old[1] or pi_us<=old[4]:return self.fail('ODOM_SAMPLE_ORDER')
            if max(source_us-old[1],pi_us-old[4])>self.config.maximum_sample_age_us:
                # Unknown motion through a long sensor gap cannot be integrated
                # as one constant-curvature turn. A new odom frame needs a new
                # mapper anchor, rather than continuing an invented old pose.
                self.errors['ODOM_CONTINUITY_GAP']=self.errors.get('ODOM_CONTINUITY_GAP',0)+1
                if self.first_error is None:self.first_error='ODOM_CONTINUITY_GAP'
                self.epoch=str(uuid4());self.pose=(0.,0.,0.);self.travel=0.;self.sequence=1
                self.absolute.invalidate()
                self.previous=current;self.samples.clear();self.latest=None;return None
            modulus=1<<self.config.encoder_bits
            delta=lambda a,b:((a-b+modulus//2)%modulus)-modulus//2
            dl=delta(left,old[2])*self.config.metres_per_tick
            dr=delta(right,old[3])*self.config.metres_per_tick
            dt=(source_us-old[1])/1e6
            if max(abs(dl),abs(dr))>self.config.maximum_speed_mps*dt:return self.fail('ODOM_ENCODER_DISCONTINUITY')
            distance=(dl+dr)/2;turn=(dr-dl)/self.config.track_width_m
            if abs(distance)<1e-9 and abs(turn)>1e-6:return self.fail('ODOM_ACKERMANN_INCONSISTENT')
            if abs(turn)<1e-8:x+=distance*math.cos(yaw);y+=distance*math.sin(yaw)
            else:
                radius=distance/turn
                x+=radius*(math.sin(yaw+turn)-math.sin(yaw))
                y-=radius*(math.cos(yaw+turn)-math.cos(yaw))
            yaw=math.remainder(yaw+turn,2*math.pi);velocity=distance/dt
            self.travel+=abs(distance)
            if abs(distance)>1e-9:steering=math.atan(self.config.wheelbase_m*turn/distance)
        self.previous=current;self.pose=x,y,yaw;self.sequence+=1
        self.absolute.record(pi_session=pi_session,device_session=self.device,odom_epoch=self.epoch,
            sequence=self.sequence,source_sample=sample,source_us=source_us,left=left,right=right,
            pi_sample_us=pi_us,clock_error_us=error)
        # First valid sample establishes a frame origin, but cannot certify a
        # measured velocity. It is retained as an anchor, not published as 0.
        if self.sequence==1:return None
        self.latest=dict(vehicle=self.config.vehicle,host_boot=self.host_boot,device_session=self.device,
            odom_epoch=self.epoch,sequence=self.sequence,source_sample=sample,source_us=source_us,
            pi_sample_us=pi_us,clock_error_us=error,left_ticks=left,right_ticks=right,
            odom_pose=dict(x=x,y=y,yaw=yaw),velocity_mps=velocity,
            position_error_m=self.config.position_error_m+self.travel*self.config.distance_error_ratio+
                             self.config.maximum_speed_mps*error/1e6,
            heading_error_rad=self.config.heading_error_rad+self.travel*self.config.heading_error_per_m,
            quality=3,calibration=self.config.calibration)
        if steering is not None:self.latest['estimated_steering_rad']=steering
        if quality&6:
            self.latest.pop('estimated_steering_rad',None)
            self.latest['measured_steering_rad' if quality&2 else 'estimated_steering_rad']=steer_mrad/1000
            self.latest['steering_error_rad']=steer_error_mrad/1000
        elif steering is not None:
            # Wheel-derived curvature is only an estimate. Without a calibrated
            # steering uncertainty it cannot qualify the product tracker.
            self.latest.pop('estimated_steering_rad',None)
        self.latest['pi_session']=pi_session
        self.samples.append(self.latest)
        return self.latest

    def current(self,now_us):
        value=self.latest
        if value is None or now_us<value['pi_sample_us'] or now_us-value['pi_sample_us']+value['clock_error_us']>self.config.maximum_sample_age_us:return None
        return dict(value)
