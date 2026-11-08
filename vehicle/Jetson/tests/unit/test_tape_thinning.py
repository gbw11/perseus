"""고정 Zhang-Suen scalar/LUT와 원 graph pixel 집합 경로의 동치를 검사한다.

합성 raster의 논리 동치이며 물리 검출률·15FPS·실제 backend 성능의 증거가 아니다.
"""

import builtins
from dataclasses import replace
import struct
import unittest
from unittest.mock import patch

from xenova_vehicle.perception import tape


def scalar_remove(code,step):
    n,ne,e,se,s,sw,w,nw=((code>>bit)&1 for bit in range(8))
    ring=(n,ne,e,se,s,sw,w,nw,n)
    changes=sum(a==0 and b==1 for a,b in zip(ring,ring[1:]))
    first=(n*e*s==0 and e*s*w==0)
    second=(n*e*w==0 and n*s*w==0)
    return 2<=n+ne+e+se+s+sw+w+nw<=6 and changes==1 and (first if step==0 else second)


def original_thin(np,mask,limit):
    """수정 전 배열 predicate를 고정한 시험 oracle. 제품 LUT/neighbor helper는 쓰지 않는다."""
    image=mask.copy()
    for iteration in range(limit):
        changed=False
        for step in (0,1):
            p=np.pad(image,1)
            p=(p[:-2,1:-1],p[:-2,2:],p[1:-1,2:],p[2:,2:],p[2:,1:-1],p[2:,:-2],p[1:-1,:-2],p[:-2,:-2])
            count=sum(v.astype(np.uint8) for v in p)
            transitions=sum((~p[i]&p[(i+1)%8]).astype(np.uint8) for i in range(8))
            corner=(~(p[0]&p[2]&p[4])&~(p[2]&p[4]&p[6]) if step==0 else
                    ~(p[0]&p[2]&p[6])&~(p[0]&p[4]&p[6]))
            remove=image&(count>=2)&(count<=6)&(transitions==1)&corner
            if np.any(remove):image[remove]=False;changed=True
        if not changed:return image,True,iteration+1
    return image,False,limit


class ThinScalarTableTests(unittest.TestCase):
    def test_all_256_states_both_steps_match_independent_scalar_predicate_and_fixed_cache(self):
        self.assertIs(type(tape._THIN_REMOVE),tuple)
        self.assertEqual(tuple(len(table) for table in tape._THIN_REMOVE),(256,256))
        self.assertEqual(sum(map(len,tape._THIN_REMOVE))+len(tape._THIN_KERNEL),548)
        self.assertEqual(struct.unpack('<9f',tape._THIN_KERNEL),(128.,1.,2.,64.,0.,4.,32.,16.,8.))
        for step,table in enumerate(tape._THIN_REMOVE):
            self.assertIs(type(table),bytes);self.assertLessEqual(set(table),{0,1})
            for code in range(256):
                with self.subTest(step=step,code=code):self.assertEqual(bool(table[code]),scalar_remove(code,step))


class ThinRasterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import numpy as np
            import cv2
        except ImportError as error:raise unittest.SkipTest('existing NumPy/OpenCV required: '+str(error))
        cls.np,cls.cv2=np,cv2

    def assert_same(self,mask,limit):
        before=mask.copy();expected=original_thin(self.np,mask,limit);actual=tape._thin(self.np,mask,limit)
        self.assertTrue(self.np.array_equal(actual[0],expected[0]));self.assertEqual(actual[1:],expected[1:])
        self.assertTrue(self.np.array_equal(mask,before));self.assertIsNot(actual[0],mask)
        self.assertEqual(actual[0].dtype,self.np.bool_)

    def test_filter16s_codes_preserve_neighbor_orientation_all_states_and_zero_border(self):
        np,cv2=self.np,self.cv2;image=np.zeros((48,48),dtype=np.uint8)
        offsets=((-1,0),(-1,1),(0,1),(1,1),(1,0),(1,-1),(0,-1),(-1,-1))
        centers=[]
        for code in range(256):
            r,c=(code//16)*3+1,(code%16)*3+1;centers.append((r,c));image[r,c]=1
            for bit,(dy,dx) in enumerate(offsets):image[r+dy,c+dx]=(code>>bit)&1
        kernel=np.frombuffer(tape._THIN_KERNEL,dtype='<f4').reshape(3,3)
        codes=cv2.filter2D(image,cv2.CV_16S,kernel,borderType=cv2.BORDER_CONSTANT)
        self.assertEqual(codes.dtype,np.int16)
        self.assertEqual([int(codes[p]) for p in centers],list(range(256)))
        border=np.asarray(((1,0,1,0,1),(1,1,0,1,0),(0,1,0,0,1)),dtype=np.uint8)
        actual=cv2.filter2D(border,cv2.CV_16S,kernel,borderType=cv2.BORDER_CONSTANT)
        for r in range(border.shape[0]):
            for c in range(border.shape[1]):
                expected=sum(1<<bit for bit,(dy,dx) in enumerate(offsets)
                    if 0<=r+dy<border.shape[0] and 0<=c+dx<border.shape[1] and border[r+dy,c+dx])
                self.assertEqual(int(actual[r,c]),expected)
        for value in (kernel,*(np.frombuffer(table,dtype=np.bool_) for table in tape._THIN_REMOVE)):
            with self.assertRaises(ValueError):value.setflags(write=True)

    def test_empty_full_border_single_junction_cycle_and_iteration_caps_match_original(self):
        np=self.np;masks=[np.zeros(shape,dtype=bool) for shape in ((0,0),(0,5),(5,0),(1,1),(1,9),(9,1),(31,29))]
        masks.extend(np.ones(shape,dtype=bool) for shape in ((1,1),(1,9),(9,1),(31,29)))
        junction=np.zeros((41,43),dtype=bool);junction[5:36,19:24]=True;junction[10:15,4:39]=True
        cycle=np.zeros_like(junction);cycle[4:37,4:39]=True;cycle[8:33,8:35]=False
        boundary=np.zeros_like(junction);boundary[:5,:]=True;boundary[:,-4:]=True
        masks.extend((junction,cycle,boundary))
        for index,mask in enumerate(masks):
            for limit in (0,1,2,64):
                with self.subTest(mask=index,limit=limit):self.assert_same(mask,limit)

    def test_seeded_random_rasters_match_original_output_and_termination(self):
        np=self.np;rng=np.random.default_rng(1873)
        for index in range(12):
            mask=rng.random((17,19))<(.1,.4,.7,.9)[index%4]
            for limit in (1,5,64):
                with self.subTest(index=index,limit=limit):self.assert_same(mask,limit)

    def test_trace_pixel_conversion_keeps_original_order_strips_ids_work_and_cap_results(self):
        np,cv2=self.np,self.cv2
        line=np.zeros((41,47),dtype=bool);line[4:37,23]=True
        junction=line.copy();junction[15,5:42]=True
        cycle=np.zeros_like(line);cycle[5:36,8]=True;cycle[5:36,38]=True;cycle[5,8:39]=True;cycle[35,8:39]=True
        boundary=line.copy();boundary[:8,23]=True
        profile=tape.TapeProfile(x_min_m=0.,x_max_m=.4,y_min_m=-.23,y_max_m=.23,cell_m=.01,min_strip_length_m=.01)
        for index,skeleton in enumerate((np.zeros_like(line),line,junction,cycle,boundary)):
            legacy_pixels={tuple(int(v) for v in p) for p in np.argwhere(skeleton)}
            current_pixels=set(map(tuple,np.argwhere(skeleton).tolist()))
            self.assertEqual(list(current_pixels),list(legacy_pixels))
            valid=np.ones_like(skeleton);occluded=np.zeros_like(skeleton)
            if index==4:valid[:3]=False;occluded[30:34,20:25]=True
            band=cv2.dilate(skeleton.astype(np.uint8),np.ones((3,3),dtype=np.uint8))!=0
            for cap in (profile,replace(profile,max_graph_pixels=8),replace(profile,edge_work_cap=1),replace(profile,max_strips=1)):
                first=[True]
                def original_set(*args):
                    if first[0]:first[0]=False;return legacy_pixels
                    return builtins.set(*args)
                def trace(work):
                    return tape._trace(np,cv2,skeleton,valid,occluded,band,np.zeros(skeleton.shape),
                        np.zeros(skeleton.shape+(2,)),cap,work)
                old_work={'edge_tests':0}
                # graph 본문은 동일하고 변경한 첫 set 생성만 원 argwhere/scalar 경로로 고정한다.
                with patch.object(tape,'set',original_set,create=True):expected=trace(old_work)
                work={'edge_tests':0};actual=trace(work)
                with self.subTest(index=index,cap=cap.max_graph_pixels,edge=cap.edge_work_cap):
                    self.assertEqual(actual,expected);self.assertEqual(work,old_work)
