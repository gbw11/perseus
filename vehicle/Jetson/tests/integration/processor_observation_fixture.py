"""전체 Processor 시험의 명시 software camera leaf와 원 sensor bytes.

이 수치들은 실측 OAK 보정이 아닌 하향 bench 기하의 수락 전제다. 처음부터 LIVE
입력형을 선언하며 SYNTHETIC 객체를 바꾸거나 simulator/oracle을 호출하지 않는다.
렌더러 좌표는 pixels에만 사용하고 pose·tape·정합·FREE certificate·World를 반환하지 않는다.
NumPy/OpenCV와 제품 모듈은 Nano에서 이 fixture를 실제 사용할 때만 import한다.
"""


_ROTATION=((-1.,0.,0.),(0.,1.,0.),(0.,0.,-1.))
_TRANSLATION=(.4,0.,1.)
_FIXTURE='PROCESSOR_PIXEL_BENCH_V4'


def software_live_calibration(vehicle_bundle_sha256):
    """원 vehicle bundle과 결박한 시험 전용 camera/plane/body 값을 만든다.

    qualification은 이 software bench의 수학적 전제다. 설치 파일을 변경하거나
    실제 RC 전방 카메라·첫 표면·시계·차량 보정을 승인하는 함수가 아니다.
    """
    from xenova_vehicle.perception.calibration import BodyModel,CalibrationProfile,CameraModel,Plane
    if type(vehicle_bundle_sha256) is not str:
        raise ValueError('explicit original vehicle bundle SHA required')
    def camera(height,focal):
        return CameraModel(640,height,((focal,0.,319.5),(0.,focal,(height-1)/2),(0.,0.,1.)),
            _ROTATION,_TRANSLATION,pixel_error=.25,ray_error_rad=.00001,
            translation_error_m=.0001,rotation_error_rad=.00001,qualified=True,
            remap_qualified=True,near_m=.15,far_m=3.)
    return CalibrationProfile(camera(360,300.),camera(480,300.),
        Plane(normal_error_rad=.00001,offset_error_m=.0001,qualified=True),
        ((-.7,-1.1),(1.9,-1.1),(1.9,1.1),(-.7,1.1)),body=BodyModel(),kind='LIVE',
        provenance=dict(purpose='SOFTWARE_TEST_ONLY',fixture=_FIXTURE,measured=False,
            input_layouts=dict(rgb='NV12_640x360',depth='RECTIFIED_RIGHT_AXIAL_Z_U16LE_MM'),
            assumptions=['top-down optical roll 90deg; height 1m and forward position .4m; RGB/RIGHT focal 300',
                'native 640px axis observes the original 1m strips over 300px; tape width is about 7.5px',
                'declared physical paint ends lie inside FOV/ROI margins; actual endpoint classification is required',
                'independent transverse marker spans world y .39 to .52m',
                'declared wider flat first surface; no measured OAK optical acceptance',
                'software bench for stopping-region ray density; positive proof still required',
                'declared test clock and error bounds','no physical RC installation acceptance']),
        first_surface_qualified=True,time_qualified=True,minimum_target_qualified=True,
        depth_error_m=.001,depth_relative_error=0.,vehicle_bundle_sha256=vehicle_bundle_sha256,
        external_leaves={})


def observation_payloads(calibration,*,blank_rgb=False,missing_depth=False):
    """원 RGB NV12와 RIGHT axial-Z uint16LE/mm의 소유 bytes만 돌려준다.

    세 평행 띠 밖의 짧은 횡방향 표식은 직선 차로만 있는 정합의 퇴화를 피한다.
    V4는 optical roll로 640px 축에 원 1m 띠를 300px로 담는다. 실제 pixels만 소비한다.
    명시한 paint 끝도 제품이 FOV로 판정하면 그대로 남기며 physical 끝으로 덮지 않는다.
    depth=0 변형은 실제 invalid pixel이며 임의 FREE나 결측 보간으로 바꾸지 않는다.
    원 sequence/device/SDK/J 시각은 이 함수 바깥의 실제 입력 producer가 별도로 고정한다.
    """
    import cv2
    import numpy as np
    from xenova_vehicle.perception.calibration import CalibrationProfile
    if (type(calibration) is not CalibrationProfile
            or calibration!=software_live_calibration(calibration.vehicle_bundle_sha256)):
        raise ValueError('explicit original processor bench calibration required')
    if type(blank_rgb) is not bool or type(missing_depth) is not bool:
        raise ValueError('exact software observation variants required')
    image=np.full((360,640,3),30,dtype=np.uint8)
    if not blank_rgb:
        camera=calibration.rgb
        def project(polygon):
            # 제품 projector와 별개의 pinhole 식이다. 이 좌표는 image에만 남는다.
            tx,ty,tz=camera.translation
            return np.rint([(camera.k[0][2]-camera.k[0][0]*(x-tx)/tz,
                camera.k[1][2]+camera.k[1][1]*(y-ty)/tz) for x,y in polygon]).astype(np.int32)
        half=.0125
        for y in (-.3,0.,.3):
            cv2.fillConvexPoly(image,project(((.3,y-half),(1.3,y-half),(1.3,y+half),(.3,y+half))),
                (245,245,245))
        cv2.fillConvexPoly(image,project(((.75-half,.39),(.75+half,.39),(.75+half,.52),(.75-half,.52))),
            (245,245,245))
    planar=cv2.cvtColor(image,cv2.COLOR_BGR2YUV_I420).reshape(-1)
    pixels=640*360
    uv=np.empty(pixels//2,dtype=np.uint8)
    uv[0::2]=planar[pixels:pixels+pixels//4]
    uv[1::2]=planar[pixels+pixels//4:]
    rgb=planar[:pixels].tobytes()+uv.tobytes()
    # 하향 optical-Z는 모든 ground ray에서1m다. GT 점군은 consumer에 전달하지 않는다.
    depth=(b'\0\0' if missing_depth else b'\xe8\x03')*(640*480)
    return rgb,depth
