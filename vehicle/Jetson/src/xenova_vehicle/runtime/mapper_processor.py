"""M-compute 하나의 parser와 후보 core를 소유한다. actor/SDK/원 token은 만지지 않는다.

기존 채택 core를 독립 후보로 복사해 처리하고, compute owner의 최종 현재성 판정 뒤
accepted()에서 참조만 교체한다. 취소는 후보만 버리며 기존 map/HWM을 초기화하지 않는다.
결과는 main과 공유할 mutable array 없이 유한 JSON bytes로 넘긴다.
"""

from copy import deepcopy
import json

from .resident_transport import j_us


class ResidentMapperProcessor:
    """생성·parse·geometry·최종 채택은 모두 같은 compute thread의 책임이다."""

    def __init__(self, profile_bytes, binding, *, clock=j_us):
        from .resident_perception import ResidentPerception
        self.clock=clock
        self._accepted=(None if profile_bytes is None else ResidentPerception(profile_bytes,binding))
        self._staged=None

    def parse(self, item, checkpoint):
        from ..perception.yolop import decode_yolop
        from ..perception.decoders import decode_depth, decode_rgb
        checkpoint()
        sample=item.sample
        candidates=decode_yolop(sample.nn_output)
        if self._accepted is None:
            rgb,depth=decode_rgb(sample.rgb),decode_depth(sample.depth)
            rgb=depth=None
        checkpoint()
        return candidates

    def geometry(self, item, parsed, checkpoint):
        checkpoint()
        if self._staged is not None:
            raise RuntimeError('previous geometry candidate has no terminal decision')
        if self._accepted is None:
            return None
        # profile의 유한 RGB/graph/grid/cache만 복사한다. 모든 상태는 compute 단일 소유다.
        candidate=deepcopy(self._accepted)
        checkpoint()
        summary=candidate.consume(item.sample,now_us=self.clock(),nn_candidates=parsed,
            checkpoint=checkpoint,completed_clock=self.clock)
        checkpoint()
        payload=json.dumps(dict(processed=candidate.processed,latest=summary),sort_keys=True,
            ensure_ascii=True,separators=(',',':'),allow_nan=False).encode('ascii')
        from .mapper_compute import MAX_RESULT_BYTES
        if len(payload)>MAX_RESULT_BYTES:
            raise ValueError('mapper summary exceeds immutable result budget')
        checkpoint()
        self._staged=(item.job_id,candidate)
        return payload

    def accepted(self, item):
        """최종 원 job/current/expiry 검사가 끝난 compute owner가 참조만 교체한다."""
        if self._accepted is None:
            return
        if self._staged is None or self._staged[0]!=item.job_id:
            raise RuntimeError('accepted geometry differs from the original staged job')
        self._accepted=self._staged[1]
        self._staged=None

    def cancelled(self, item, reason):
        """원 후보의 실패/만료는 이미 채택한 지도나 source 시각을 갱신하지 않는다."""
        self._staged=None
