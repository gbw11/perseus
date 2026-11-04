"""Rasterize explicitly supplied preferred-road geometry into existing cell flags.

Cell-center classification is a preference, not evidence that a cell is free.
Validity, occupancy, age and the outer hard boundary are preserved verbatim.
The producer owns road geometry/source quality; encoding does not certify terrain.
"""
import math
from .codec import ROAD_PRIORITY,CELL_ROAD


def mark_road(world,left,right):
    """Mark a bounded road strip in place; `left/right` are same-frame Points.

    No automatic width, free-cell fallback or time renewal. Encode/validate the
    completed snapshot normally after marking; its hard boundary is unchanged.
    """
    if not 2<=len(left)<=64 or len(left)!=len(right):raise ValueError('preferred road arrays')
    if not 2<=world.width<=64 or not 2<=world.height<=64:raise ValueError('grid dimensions')
    if len(world.cell_flags)!=world.width*world.height:raise ValueError('grid arrays incomplete')
    resolution=world.resolution_m
    if not math.isfinite(resolution) or resolution<=0:raise ValueError('resolution')
    origin=world.origin
    if not all(math.isfinite(v) for v in (origin.x,origin.y,origin.yaw)):raise ValueError('origin')
    c,s=math.cos(origin.yaw),math.sin(origin.yaw);polygon=[]
    for point in list(left)+list(right)[::-1]:
        if not all(math.isfinite(v) and abs(v)<1e6 for v in (point.x,point.y)):raise ValueError('preferred road point')
        dx,dy=point.x-origin.x,point.y-origin.y
        polygon.append((dx*c+dy*s,-dx*s+dy*c))
    flags=bytearray(v&~CELL_ROAD for v in world.cell_flags)
    for row in range(world.height):
        y=(row+.5)*resolution;crossings=[]
        for a,b in zip(polygon,polygon[1:]+polygon[:1]):
            if (a[1]>y)!=(b[1]>y):crossings.append(a[0]+(y-a[1])*(b[0]-a[0])/(b[1]-a[1]))
        crossings.sort()
        if len(crossings)%2:raise ValueError('preferred road scanline')
        for first,last in zip(crossings[::2],crossings[1::2]):
            lo=max(0,math.ceil(first/resolution-.5));hi=min(world.width-1,math.ceil(last/resolution-.5)-1)
            for col in range(lo,hi+1):flags[row*world.width+col]|=CELL_ROAD
    if not any(v&CELL_ROAD for v in flags):raise ValueError('missing preferred road')
    world.cell_flags=bytes(flags);world.required_features|=ROAD_PRIORITY
    world.minor=max(world.minor,1)
    return world
