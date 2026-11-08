"""이미 A가 읽은 CalibrationHandler의 factory 사실만 추출한다. Device를 열지 않는다.

resize K와 EEPROM R은 active rectification의 readback이 아니다. 실제 K_rect/ROI,
mount/plane/focus/오차 수락을 이 snapshot의 해시로 대신하지 않는다.
"""

import hashlib
import json


def _copy(value, cap=262144):
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                     separators=(',', ':')).encode('ascii')
    if len(raw) > cap:
        raise ValueError('factory calibration snapshot exceeds cap')
    return json.loads(raw)


def snapshot_factory(calibration, dai, *, eeprom_raw=None, device_id=None, graph_profile=None,
                     sensors=()):
    """고정 v2.29 caller가 제공한 handler에서 읽는다. 원 raw EEPROM은 caller가 읽어 넘긴다."""
    if getattr(dai, '__version__', None) != '2.29.0.0':
        raise ValueError('factory extractor requires fixed DepthAI2.29.0.0')
    if eeprom_raw is not None and (type(eeprom_raw) is not bytes or not 1 <= len(eeprom_raw) <= 65536):
        raise ValueError('invalid original EEPROM bytes')
    if device_id is not None and (type(device_id) is not str or not 1 <= len(device_id) <= 128):
        raise ValueError('invalid original device ID')
    errors = {}
    def read(name, getter):
        try:
            return _copy(getter())
        except Exception as error:
            errors[name] = type(error).__name__
            return None
    eeprom = read('eeprom', calibration.eepromToJson)
    cameras = {}
    for name, socket, size in (('rgb', dai.CameraBoardSocket.CAM_A, (640, 360)),
                                ('left', dai.CameraBoardSocket.CAM_B, (640, 480)),
                                ('right', dai.CameraBoardSocket.CAM_C, (640, 480))):
        cameras[name] = dict(socket=str(socket),
            default_intrinsics=read(name + '.default_k', lambda s=socket: calibration.getDefaultIntrinsics(s)),
            distortion_model=read(name + '.distortion_model', lambda s=socket: str(calibration.getDistortionModel(s))),
            distortion_coefficients=read(name + '.distortion', lambda s=socket: calibration.getDistortionCoefficients(s)),
            resize_candidate=read(name + '.resize_k', lambda s=socket, z=size: calibration.getCameraIntrinsics(s, z[0], z[1])),
            resize_candidate_wh=list(size), resize_status='EEPROM_RESIZE_NOT_ACTIVE_READBACK')
    result = dict(schema_version=1, scope='OAK_FACTORY_CALIBRATION_NOT_METRIC_ADMISSION',
        device_id=device_id, depthai_version=dai.__version__, eeprom=eeprom,
        eeprom_json_sha256=None if eeprom is None else hashlib.sha256(json.dumps(eeprom,
            sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode('ascii')).hexdigest(),
        eeprom_raw_sha256=None if eeprom_raw is None else hashlib.sha256(eeprom_raw).hexdigest(),
        eeprom_raw_hex=None if eeprom_raw is None else eeprom_raw.hex(), cameras=cameras,
        stereo_left_socket=read('stereo_left_socket', lambda: str(calibration.getStereoLeftCameraId())),
        stereo_right_socket=read('stereo_right_socket', lambda: str(calibration.getStereoRightCameraId())),
        stored_left_rectification_rotation=read('left_r', lambda: calibration.getStereoLeftRectificationRotation()),
        stored_right_rectification_rotation=read('right_r', lambda: calibration.getStereoRightRectificationRotation()),
        calibrated_baseline_cm=read('baseline', lambda: calibration.getBaselineDistance(
            dai.CameraBoardSocket.CAM_C, dai.CameraBoardSocket.CAM_B, False)),
        calibrated_right_to_rgb_extrinsics_cm=read('right_to_rgb', lambda: calibration.getCameraExtrinsics(
            dai.CameraBoardSocket.CAM_C, dai.CameraBoardSocket.CAM_A, False)),
        factory_rgb_lens_position=read('lens_position', lambda: calibration.getLensPosition(dai.CameraBoardSocket.CAM_A)),
        active=dict(k_rect=None, r_rect=None, right_left_support_roi=None,
                    focus_status='NOT_VERIFIED', runtime_config_status='NOT_VERIFIED'),
        graph_request=_copy(graph_profile), sensor_inventory=_copy(sensors),
        getter_errors=errors, metric_qualified=False, live_authority=False)
    return _copy(result)


factory_snapshot = snapshot_factory
