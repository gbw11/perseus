"""Vehicle-specific bounds on submitted command targets, not device feedback.

The owner supplies measured limits. No vehicle values are inferred here. STOP
always bypasses slew checks; a rejected drive latches until this sink is closed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from xenova_mqtt.drive_binding import private_json
from .frame import DriveFrame, StopFrame, decode, encode
from .sink import SinkError


@dataclass(frozen=True)
class VehicleLimits:
    min_v_mps: float
    max_v_mps: float
    min_steer_rad: float
    max_steer_rad: float
    max_accel_mps2: float
    max_steer_rate_rad_s: float

    def __post_init__(self):
        values=vars(self)
        try:invalid=any(type(v) not in (int,float) or not math.isfinite(v) for v in values.values())
        except OverflowError:invalid=True
        if invalid:
            raise ValueError('finite numeric vehicle limits required')
        if not (-32.768<=self.min_v_mps<=0<=self.max_v_mps<=32.767 and
                -32.768<=self.min_steer_rad<=0<=self.max_steer_rad<=32.767 and
                self.min_v_mps<self.max_v_mps and self.min_steer_rad<self.max_steer_rad and
                self.max_accel_mps2>0 and self.max_steer_rate_rad_s>0):
            raise ValueError('vehicle bounds must contain neutral and fit the serial codec')

    @classmethod
    def load(cls,path: Path,*,uid=0):
        value=private_json(Path(path),uid)
        if value.pop('schema_version',None)!='1.0-drive-output-limits' or set(value)!=set(cls.__annotations__):
            raise ValueError('explicit vehicle output limit fields required')
        return cls(**value)


class LimitedSink:
    """Actor-owned wrapper: validate before write, advance only on full acceptance.

    Rates compare successive submitted targets on the dom0 clock, capped by the
    existing 100ms command freshness interval. They are not actual acceleration
    or steering observations. Successful STOP resets the speed target to zero;
    it does not prove that the vehicle stopped or changed its steering angle.
    """
    def __init__(self,sink,limits: VehicleLimits,now):
        self.sink=sink;self.limits=limits;self.previous=(0.,0.,now)
        self.failed_reason=None;self.rejected=0

    def _check(self,frame,now):
        if self.failed_reason:raise SinkError(self.failed_reason)
        before_v,before_steer,before_t=self.previous
        if not math.isfinite(now) or now<before_t:raise SinkError('OUTPUT_CLOCK_INVALID')
        cfg=self.limits
        # Validate before encoding: the codec's integer saturation must never
        # turn an out-of-range command into an accepted vehicle command.
        if (not math.isfinite(frame.v_mps) or not math.isfinite(frame.steer_rad) or
                not cfg.min_v_mps<=frame.v_mps<=cfg.max_v_mps or
                not cfg.min_steer_rad<=frame.steer_rad<=cfg.max_steer_rad):
            raise SinkError('OUTPUT_RANGE_EXCEEDED')
        encoded=decode(encode(frame))
        if (not cfg.min_v_mps<=encoded.v_mps<=cfg.max_v_mps or
                not cfg.min_steer_rad<=encoded.steer_rad<=cfg.max_steer_rad):
            raise SinkError('OUTPUT_QUANTIZED_RANGE_EXCEEDED')
        dt=min(now-before_t,.100)
        if (abs(encoded.v_mps-before_v)>cfg.max_accel_mps2*dt+1e-12 or
                abs(encoded.steer_rad-before_steer)>cfg.max_steer_rate_rad_s*dt+1e-12):
            raise SinkError('OUTPUT_RATE_EXCEEDED')
        return encoded

    def write(self,frame,now):
        if isinstance(frame,DriveFrame):
            try:target=self._check(frame,now)
            except (SinkError,ValueError,OverflowError) as exc:
                self.rejected+=1;self.failed_reason=str(exc);raise SinkError(self.failed_reason) from exc
        elif isinstance(frame,StopFrame):target=None
        else:raise SinkError('OUTPUT_FRAME_INVALID')
        try:
            accepted=self.sink.write(frame,now)
            if accepted!=len(encode(frame)):raise SinkError('OUTPUT_PARTIAL_WRITE')
        except (SinkError,OSError) as exc:
            self.failed_reason='OUTPUT_SUBMISSION_FAILED';raise SinkError(self.failed_reason) from exc
        if target is not None:self.previous=(target.v_mps,target.steer_rad,now)
        else:
            if not math.isfinite(now) or now<self.previous[2]:
                self.failed_reason='OUTPUT_CLOCK_INVALID'
                now=self.previous[2]
            self.previous=(0.,self.previous[1],now)
        return accepted

    def close(self):self.sink.close()
