"""Explicit local fixture producer, not a Jetson mapper or an operating profile."""
import argparse
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'world/python'))
from world_codec import schema,encode

BOOT='11111111-1111-4111-8111-111111111111'
INSTANCE='22222222-2222-4222-8222-222222222222'
MAP='33333333-3333-4333-8333-333333333333'
FRAME='44444444-4444-4444-8444-444444444444'
ODOM='55555555-5555-4555-8555-555555555555'
CLOCK='66666666-6666-4666-8666-666666666666'
PRIMITIVE=json.loads((Path(__file__).resolve().parents[1]/'planner/generated/primitive-manifest.json').read_text())['asset_sha256']

def world(*,width=64,height=32,sequence=1):
    value=schema.WorldSnapshot(major=1,minor=0,required_features=0,vehicle='rc-test-1',
        mapper_boot=BOOT,mapper_instance=INSTANCE,map_epoch=MAP,frame_epoch=FRAME,odom_epoch=ODOM,
        sequence=sequence,configuration='lattice-lab-1',calibration='encoder-lab-1',primitive=PRIMITIVE,
        reference_id='straight-lab-road',reference_revision=1,resolution_m=.1,width=width,height=height,
        reference_odom_seq=1,observed_us=1000000,completed_us=1001000,published_us=1002000,
        observation_seq=1,clock_epoch=CLOCK,complete=True,truncated=False,inflation_policy=0)
    value.origin.CopyFrom(schema.Pose(x=0,y=0,yaw=0))
    value.reference_vehicle_pose.CopyFrom(schema.Pose(x=.3,y=1.6,yaw=0))
    for x in (0.,1.,2.,3.,4.,5.,6.):
        value.reference.add(x=x,y=1.6)
        value.left_boundary.add(x=x,y=3.)
        value.right_boundary.add(x=x,y=.2)
    count=width*height
    value.occupancy=bytes(count)
    value.cell_flags=bytes([3])*count
    value.age_le32=struct.pack('<I',3600000)*count # retained cells are not fake fresh observations.
    return value

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--maximum',action='store_true')
    args=parser.parse_args()
    value=world(height=64 if args.maximum else 32)
    args.output.write_bytes(encode(value))

if __name__=='__main__':main()
