"""실제 고정 OpenCV의 nominal stereo artifact와 네 SDK 적용 호출을 검산한다.

명시 synthetic K/D/R/T 수학 fixture다. 실물 EEPROM/장치 warp/전역 ray 오차·
matcher validity/G3/설치/metric 수락을 생성하지 않는다. SDK leaf는 호출 기록뿐이다.
"""

from hashlib import sha256
import math
import struct
import unittest
from unittest.mock import patch

from xenova_vehicle.oak.rectification import (MESH_BYTES, MESH_SHAPE, OwnedRectificationPlan,
    RectificationError, RectificationRequest, apply_rectification_plan, build_rectification_plan)


def request_fields():
    angle=.004
    return dict(factory_raw_sha256='a'*64,
        left_k=[[430.,0.,319.5],[0.,432.,239.5],[0.,0.,1.]],
        left_d=[-.04,.004,.001,-.001,.0002],
        right_k=[[428.,0.,320.],[0.,431.,240.],[0.,0.,1.]],
        right_d=[-.035,.003,-.0008,.0009,.0001],
        right_from_left_r=[[math.cos(angle),0.,math.sin(angle)],[0.,1.,0.],[-math.sin(angle),0.,math.cos(angle)]],
        right_from_left_t_cm=[-7.5,.03,-.01],input_size=[640,480],output_size=[640,480],
        mesh_step=[16,16],alpha=0.,distortion_model='BROWN_CONRADY')


class StereoCalls:
    def __init__(self):self.calls=[]
    def setMeshStep(self,*value):self.calls.append(('step',value))
    def loadMeshData(self,left,right):self.calls.append(('mesh',left,right))
    def setFocalLength(self,value):self.calls.append(('focal_px',value))
    def setBaseline(self,value):self.calls.append(('baseline_cm',value))


class OwnedRectificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import cv2
        import numpy as np
        cls.cv2,cls.np=cv2,np
        cls.request=RectificationRequest(**request_fields())
        cls.plan=build_rectification_plan(cls.request)

    def inverse_point(self,k,d,rotation,rect_k,u,v):
        """dense map와 독립인 연속 ray→raw BrownConrady projection이다."""
        np,cv2=self.np,self.cv2
        rect=np.linalg.solve(np.asarray(rect_k),np.asarray((u,v,1.)))
        raw=np.asarray(rotation).T @ rect
        point,_=cv2.projectPoints(raw.reshape(1,1,3),np.zeros(3),np.zeros(3),
            np.asarray(k),np.asarray(d))
        return point.reshape(2)

    def test_actual_distorted_stereo_produces_explicit_projection_roi_and_owned_mesh(self):
        plan,request,np=self.plan,self.request,self.np
        self.assertIs(type(plan),OwnedRectificationPlan)
        self.assertEqual(plan.fields['request'],request.fields)
        self.assertEqual(plan.fields['factory_raw_sha256'],request.factory_raw_sha256)
        self.assertEqual(plan.fields['request_subset_sha256'],request.request_subset_sha256)
        self.assertNotEqual(plan.left_k_rect,request.left_k)
        self.assertEqual(plan.left_k_rect,plan.right_k_rect)
        self.assertGreater(plan.left_roi[2],0);self.assertGreater(plan.right_roi[3],0)
        self.assertFalse(plan.fields['metric_qualified']);self.assertFalse(plan.fields['g3_qualified'])
        for side,k,d,r,rect in (
            ('left',request.left_k,request.left_d,plan.left_r_rect,plan.left_k_rect),
            ('right',request.right_k,request.right_d,plan.right_r_rect,plan.right_k_rect)):
            body=getattr(plan,side+'_mesh_bytes')
            self.assertIs(type(body),bytes)
            self.assertEqual(len(body),MESH_BYTES)
            self.assertEqual(sha256(body).hexdigest(),plan.fields[side]['mesh_sha256'])
            samples=np.frombuffer(body,dtype='<f4').reshape(MESH_SHAPE)
            self.assertFalse(samples.flags.writeable)
            with self.assertRaises(ValueError):samples.setflags(write=True)
            for row,col in ((0,0),(3,7),(15,20),(30,40)):
                expected=self.inverse_point(k,d,r,rect,col*16,row*16)
                np.testing.assert_allclose(samples[row,col,::-1],expected,rtol=0,atol=2e-4)
            distorted=self.inverse_point(k,d,r,rect,640,480)
            zero_distorted=self.inverse_point(k,(0.,)*len(d),r,rect,640,480)
            self.assertGreater(float(np.linalg.norm(distorted-zero_distorted)),.001)

    def test_mesh_y_x_little_endian_endpoints_and_no_last_pixel_clamp_or_padding(self):
        plan,np=self.plan,self.np
        self.assertEqual(plan.fields['mesh']['dtype'],'<f4')
        self.assertEqual(plan.fields['mesh']['order'],'ROW_MAJOR_Y_X')
        self.assertEqual(plan.fields['mesh']['shape'],[31,41,2])
        self.assertEqual(plan.fields['mesh']['row_padding_bytes'],0)
        self.assertEqual(len(plan.left_mesh_bytes),31*41*8)
        actual=np.frombuffer(plan.left_mesh_bytes,dtype='<f4').reshape(31,41,2)
        self.assertEqual(struct.unpack('<ff',plan.left_mesh_bytes[:8]),tuple(actual[0,0]))
        continuous=self.inverse_point(self.request.left_k,self.request.left_d,
            plan.left_r_rect,plan.left_k_rect,640,480)
        clamped=self.inverse_point(self.request.left_k,self.request.left_d,
            plan.left_r_rect,plan.left_k_rect,639,479)
        np.testing.assert_allclose(actual[-1,-1,::-1],continuous,rtol=0,atol=2e-4)
        self.assertGreater(float(np.linalg.norm(actual[-1,-1,::-1]-clamped)),.5)

    def test_projective_epipolar_and_cm_pixel_depth_scale_use_calibrated_translation(self):
        plan,request,np,cv2=self.plan,self.request,self.np,self.cv2
        points=np.asarray(((-20.,-10.,150.),(10.,5.,200.),(35.,20.,400.)))
        rotation=np.asarray(request.right_from_left_r)
        translation=np.asarray(request.right_from_left_t_cm)
        right=(rotation @ points.T).T+translation
        zero=np.zeros(3)
        left_pixels,_=cv2.projectPoints(points,zero,zero,np.asarray(request.left_k),np.asarray(request.left_d))
        right_pixels,_=cv2.projectPoints(right,zero,zero,np.asarray(request.right_k),np.asarray(request.right_d))
        left_rect=cv2.undistortPoints(left_pixels,np.asarray(request.left_k),np.asarray(request.left_d),
            R=np.asarray(plan.left_r_rect),P=np.asarray(plan.left_k_rect)).reshape(-1,2)
        right_rect=cv2.undistortPoints(right_pixels,np.asarray(request.right_k),np.asarray(request.right_d),
            R=np.asarray(plan.right_r_rect),P=np.asarray(plan.right_k_rect)).reshape(-1,2)
        np.testing.assert_allclose(left_rect[:,1],right_rect[:,1],rtol=0,atol=1e-6)
        z_rect=(np.asarray(plan.left_r_rect) @ points.T).T[:,2]
        disparity=left_rect[:,0]-right_rect[:,0]
        self.assertTrue(bool((disparity>0).all()))
        np.testing.assert_allclose(disparity,plan.focal_px*plan.baseline_cm/z_rect,rtol=0,atol=1e-6)
        self.assertAlmostEqual(plan.baseline_cm,math.hypot(*request.right_from_left_t_cm),places=10)
        self.assertGreater(plan.baseline_cm,7.5)
        self.assertEqual(request.right_from_left_t_cm[1:],(.03,-.01))
        fields=plan.fields
        self.assertEqual(fields['sdk_baseline_cm'],struct.unpack('<f',struct.pack('<f',plan.baseline_cm))[0])
        self.assertEqual(fields['sdk_focal_px'],struct.unpack('<f',struct.pack('<f',plan.focal_px))[0])
        self.assertEqual(fields['sdk_scale_relative_rounding'],abs(
            fields['sdk_baseline_cm']*fields['sdk_focal_px']/(plan.baseline_cm*plan.focal_px)-1.))

    def test_owned_request_descriptor_and_mesh_are_immutable_without_source_hash_cycle(self):
        fields=request_fields();request=RectificationRequest(**fields)
        original=request.canonical_bytes
        fields['left_k'][0][0]=1.;fields['left_d'][0]=0.;fields['right_from_left_t_cm'][1]=0.
        self.assertEqual(request.canonical_bytes,original)
        own=request.fields;own['left_k'][0][0]=2.;own['graph_request_subset']['alpha']=1.
        self.assertEqual(request.canonical_bytes,original)
        descriptor=self.plan.canonical_bytes
        independent=self.plan.fields
        independent['left']['k_rect'][0][0]=0.;independent['request']['left_d'].clear()
        self.assertEqual(self.plan.canonical_bytes,descriptor)
        self.assertEqual(self.plan.sha256,sha256(descriptor).hexdigest())
        for value,key,new in ((request,'factory_raw_sha256','b'*64),(self.plan,'left_mesh_bytes',b'x')):
            with self.assertRaises(AttributeError):setattr(value,key,new)
        changed=request_fields();changed['left_d'][0]-=.001
        other=RectificationRequest(**changed)
        self.assertNotEqual(other.sha256,request.sha256)
        self.assertEqual(other.request_subset_sha256,request.request_subset_sha256)
        changed=request_fields();changed['alpha']=.25
        self.assertNotEqual(RectificationRequest(**changed).request_subset_sha256,request.request_subset_sha256)
        self.assertNotIn('calibration_bundle',request.request_subset)
        self.assertNotIn('mesh_sha256',request.request_subset)

    def test_missing_source_shape_nonfinite_fisheye_and_lost_calibration_are_rejected(self):
        failures=(('factory_raw_sha256',None),('factory_raw_sha256','A'*64),
            ('left_k',[[430.,0.],[0.,430.]]),('left_k',[[0.,0.,319.],[0.,430.,239.],[0.,0.,1.]]),
            ('left_d',None),('right_d',[0.,float('nan'),0.,0.,0.]),
            ('right_from_left_r',[[1.,0.,0.],[0.,1.,0.],[0.,0.,-1.]]),
            ('right_from_left_t_cm',None),('right_from_left_t_cm',[0.,0.,0.]),
            ('right_from_left_t_cm',[-7.5,float('inf'),0.]),
            ('distortion_model','FISHEYE'),('input_size',(320,240)),('output_size',(1280,960)),
            ('mesh_step',(8,8)),('mesh_step',(True,16)),('alpha',True),('alpha',float('nan')))
        for key,value in failures:
            fields=request_fields();fields[key]=value
            with self.subTest(key=key,value=value),self.assertRaises(RectificationError):
                RectificationRequest(**fields)
        fields=request_fields();fields['left_d']=[0.]*14;fields['left_d'][8]=.001
        with self.assertRaises(RectificationError):RectificationRequest(**fields)
        with self.assertRaises(RectificationError):build_rectification_plan(None)

    def test_zero_factory14_tail_is_preserved_but_vertical_or_reversed_pair_is_unsupported(self):
        fields=request_fields()
        fields['left_d']+= [0.]*9;fields['right_d']+= [0.]*9
        plan=build_rectification_plan(RectificationRequest(**fields))
        self.assertEqual(plan.fields['request']['left_d'],fields['left_d'])
        self.assertEqual(len(plan.fields['request']['left_d']),14)
        for side in ('left','right'):
            self.np.testing.assert_allclose(self.np.frombuffer(getattr(plan,side+'_mesh_bytes'),dtype='<f4'),
                self.np.frombuffer(getattr(self.plan,side+'_mesh_bytes'),dtype='<f4'),rtol=0,atol=1e-4)
        for translation in ((0.,-7.5,0.),(7.5,0.,0.)):
            fields=request_fields();fields['right_from_left_t_cm']=translation
            with self.subTest(translation=translation),self.assertRaises(RectificationError):
                build_rectification_plan(RectificationRequest(**fields))

    def test_inconsistent_projection_rotation_roi_and_pinned_version_fail_before_mesh_apply(self):
        request,np,cv2=self.request,self.np,self.cv2
        outputs=cv2.stereoRectify(np.asarray(request.left_k),np.asarray(request.left_d),
            np.asarray(request.right_k),np.asarray(request.right_d),request.input_size,
            np.asarray(request.right_from_left_r),np.asarray(request.right_from_left_t_cm),
            flags=cv2.CALIB_ZERO_DISPARITY,alpha=request.alpha,newImageSize=request.output_size)
        for kind in ('vertical','focal','rotation','nonfinite','roi'):
            changed=[item.copy() if type(item) is np.ndarray else item for item in outputs]
            if kind=='vertical':changed[3][1,3]=1.
            elif kind=='focal':changed[3][0,0]+=1.
            elif kind=='rotation':changed[0][0,0]=2.
            elif kind=='nonfinite':changed[2][0,0]=float('nan')
            else:changed[5]=(0,0,0,0)
            with self.subTest(kind=kind),patch.object(cv2,'stereoRectify',return_value=tuple(changed)):
                with self.assertRaises(RectificationError):build_rectification_plan(request)
        with patch.object(cv2,'__version__','4.1.1'):
            with self.assertRaises(RectificationError):build_rectification_plan(request)

    def test_four_fixed_sdk_calls_require_independent_factory_request_pins_and_leave_qualification_false(self):
        plan=self.plan
        options=dict(factory_raw_sha256=plan.request.factory_raw_sha256,
            request_subset_sha256=plan.request_subset_sha256,depthai_version='2.29.0.0')
        stereo=StereoCalls()
        apply_rectification_plan(stereo,plan,**options)
        self.assertEqual([row[0] for row in stereo.calls],['step','mesh','focal_px','baseline_cm'])
        self.assertEqual(stereo.calls[0][1],(16,16))
        self.assertIs(type(stereo.calls[1][1]),list)
        self.assertEqual(bytes(stereo.calls[1][1]),plan.left_mesh_bytes)
        self.assertEqual(bytes(stereo.calls[1][2]),plan.right_mesh_bytes)
        self.assertEqual(stereo.calls[2][1],plan.fields['sdk_focal_px'])
        self.assertEqual(stereo.calls[3][1],plan.fields['sdk_baseline_cm'])
        self.assertFalse(plan.fields['metric_qualified']);self.assertFalse(plan.fields['g3_qualified'])
        for key,value in (('factory_raw_sha256','b'*64),('request_subset_sha256','c'*64),
                          ('depthai_version','2.30.0.0')):
            target=StereoCalls();changed={**options,key:value}
            with self.subTest(key=key),self.assertRaises(RectificationError):
                apply_rectification_plan(target,plan,**changed)
            self.assertEqual(target.calls,[])
        target=StereoCalls();target.setBaseline=None
        with self.assertRaises(RectificationError):apply_rectification_plan(target,plan,**options)
        self.assertEqual(target.calls,[])
