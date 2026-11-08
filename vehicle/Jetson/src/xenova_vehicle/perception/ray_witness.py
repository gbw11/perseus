"""검증된 ray 하나의 연속 교차 증명. scalar interval의 반올림 순서를 유지한다.

입력 record/target의 검증은 호출자 책임이다. 추가 ray를 미리 평가하지 않으므로
원 ray-test 예산·첫 witness 순서와 결과를 바꾸지 않는다.
"""

import math


def _rounded(lo,hi):
    lo,hi=math.nextafter(lo,-math.inf),math.nextafter(hi,math.inf)
    if not math.isfinite(lo) or not math.isfinite(hi) or lo>hi:
        raise ValueError('unbounded witness interval')
    return lo,hi


def _product(a,b,c,d):
    if not all(math.isfinite(v) for v in (a,b,c,d)):
        raise ValueError('unbounded witness operand')
    values=(a*c,a*d,b*c,b*d)
    return _rounded(min(values),max(values))


def witness(patch,lower,upper,guard):
    """한 ray bundle 전체가 동일 core 내부를 측정 전 통과하는 충분조건이다.

    Interval의 subtract→reciprocal→multiply→add와 각 nextafter를 그대로 적용한다.
    수치 발산/0 포함 분모는 해당 축을 증명하지 못한다. 반환값은 이동 권한이 아니다.
    """
    if not math.isfinite(guard) or guard<0:
        raise ValueError('invalid witness numeric guard')
    olo,ohi=patch.origin_lower,patch.origin_upper
    dlo,dhi=patch.direction_lower,patch.direction_upper
    axes=sorted(range(3),key=lambda i:-min(abs(dlo[i]),abs(dhi[i])))
    for axis in axes:
        if dlo[axis]<=0<=dhi[axis]:
            continue
        cut=(lower[axis]+upper[axis])/2
        if not math.isfinite(cut):
            continue
        try:
            nlo,nhi=_rounded(cut-ohi[axis],cut-olo[axis])
            rlo,rhi=math.nextafter(1./dhi[axis],-math.inf),math.nextafter(1./dlo[axis],math.inf)
            lo,hi=_product(nlo,nhi,rlo,rhi)
            if lo<=max(0.,patch.near_m)+guard or hi>=min(patch.range_lower_m,patch.far_m)-guard:
                continue
            intersects=True
            for i in range(3):
                if i==axis:
                    a=b=cut
                else:
                    a,b=_product(lo,hi,dlo[i],dhi[i])
                    a,b=_rounded(olo[i]+a,ohi[i]+b)
                if a<=lower[i]+guard or b>=upper[i]-guard:
                    intersects=False
                    break
            if intersects:
                return True
        except (ValueError,OverflowError,ZeroDivisionError):
            continue
    return False
