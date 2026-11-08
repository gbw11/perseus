"""M의 owned-copy 뒤 공통 인지 코어를 호출한다. simulator/doctor/장치는 import하지 않는다.

보정 파일은 S-log에서 pin한 immutable bytes로 전달한다. 실제 clock/odom/표적 수락이
없는 현재 live 경로는 frame-local HIT_ONLY/UNKNOWN이며 wire 권한을 만들지 않는다.
"""

import json


class ResidentPerception:
    """단일 M에서 명시한 보정이 있을 때 사용한다. 출력 파일 I/O는 finish 때 호출자 소유다."""

    def __init__(self, profile_bytes, binding):
        from ..perception.calibration import CalibrationProfile
        from ..perception.pipeline import PerceptionPipeline,PipelineProfile
        from ..perception.tape import TapeProfile
        from ..perception.depth import DepthConfig
        if type(profile_bytes) is not bytes or not 1<=len(profile_bytes)<=65_536:
            raise ValueError('resident perception requires bounded pinned calibration bytes')
        calibration=CalibrationProfile.from_dict(json.loads(profile_bytes))
        if calibration.kind=='SYNTHETIC' or binding.fields['calibration_bundle']!=calibration.sha256:
            raise ValueError('live binding does not match the pinned physical/provisional calibration')
        # 물리 first-surface/표적이 미수락인 경로는 FREE ray를 생산하지 않는다.
        # stride1 전체 분류는 유지하고 유한 진단 hit와 생략 수를 보고한다.
        profile=PipelineProfile(tape=TapeProfile(cell_m=.01,x_max_m=1.5,y_min_m=-.8,y_max_m=.8),
            depth=DepthConfig(max_patches=0,max_hit_boxes=1024))
        self.calibration,self.binding=calibration,binding
        self.pipeline=PerceptionPipeline(calibration,profile,targets=())
        self.latest_summary=None
        self.processed=0

    def consume(self, sample, *, now_us, nn_candidates=None):
        """caller는 모든 view/token을 이미 반환해야 한다. 원 source/time을 새로 발급하지 않는다."""
        from ..perception.adapters import from_capture_sample
        observation=from_capture_sample(sample,self.binding.fields,self.calibration,
            nn_candidates=nn_candidates,include_nn=nn_candidates is not None)
        result=self.pipeline.process(observation,now_us=now_us)
        self.latest_summary=result.summary
        self.processed+=1
        return result.summary
