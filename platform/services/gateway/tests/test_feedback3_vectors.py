"""독립 고정 bytes와 실제 feedback3/frame codec을 교차한다.

가상 session·시간·센서값만 사용한다. MCU 적용·실물 정지·ToF payload consumer의
수락 증거는 아니며, ToF는 합의된 설계의 outer framing/원 CRC 범위만 검사한다.
"""
import json
from pathlib import Path
import struct
from uuid import UUID
import zlib

import pytest

from xenova_gateway.feedback import FeedbackError,Framer,packet,unpack
from xenova_gateway.feedback3 import HELLO3,ACK3,PROBE3,CLOCK3,INSTALL3,CLOCK_ACK3,SENSOR3
from xenova_gateway.frame import cobs_encode,cobs_decode,crc16
from xenova_gateway.odometry import OdometryConfig,OdometryError,VehicleOdometry
from xenova_gateway.scene_authority import COMMAND3,FEEDBACK3


VECTORS=json.loads((Path(__file__).resolve().parents[4]/'docs/contracts/vectors/gateway-feedback-3.json').read_text(encoding='utf-8'))
LAYOUTS={row['symbol']:row for row in VECTORS['layouts']}
ROWS={row['name']:row for row in VECTORS['frames']}
STRUCTS=dict(HELLO3=HELLO3,ACK3=ACK3,PROBE3=PROBE3,CLOCK3=CLOCK3,INSTALL3=INSTALL3,
    CLOCK_ACK3=CLOCK_ACK3,SENSOR3=SENSOR3,COMMAND3=COMMAND3,FEEDBACK3=FEEDBACK3)
PRODUCT_ROWS=[row for row in VECTORS['frames'] if row['origin']=='current_product_struct']


@pytest.mark.parametrize('row',PRODUCT_ROWS,ids=lambda row:row['name'])
def test_independent_fixed_payloads_match_actual_structs_and_serial_codec(row):
    layout=LAYOUTS[row['symbol']];actual=STRUCTS[row['symbol']]
    values=tuple(bytes.fromhex(row['fields'][field['name']]) if field['format'].endswith('s')
        else row['fields'][field['name']] for field in layout['fields'])
    payload=bytes.fromhex(row['payload_hex']);raw=bytes.fromhex(row['raw_hex']);wire=bytes.fromhex(row['wire_hex'])
    assert actual.format==layout['format'] and actual.size==layout['payload_bytes']
    assert actual.pack(*values)==payload and actual.unpack(payload)==values
    assert cobs_decode(wire[:-1])==raw
    assert packet(row['id'],payload)==wire and unpack(wire)==(row['id'],payload)
    assert Framer().feed(wire)==[(row['id'],payload)]


def test_actual_crc16_matches_ccitt_false_known_check():
    check=VECTORS['crc16_check']
    assert crc16(check['ascii'].encode('ascii'))==int(check['expected_hex'],16)==0x29b1


@pytest.mark.parametrize('row',VECTORS['cobs_checks'],ids=['empty','zero','interior-zero','all-octets','full-block'])
def test_actual_cobs_matches_independent_zero_and_block_boundaries(row):
    raw=bytes.fromhex(row['raw_hex']);encoded=bytes.fromhex(row['encoded_hex'])
    assert cobs_encode(raw)==encoded and cobs_decode(encoded)==raw


@pytest.mark.parametrize('row',VECTORS['malformed'],ids=lambda row:row['name'])
def test_actual_unpack_rejects_fixed_boundary_cobs_length_and_crc_errors(row):
    with pytest.raises(FeedbackError) as failure:unpack(bytes.fromhex(row['wire_hex']))
    assert str(failure.value)==row['error']


def test_actual_framer_keeps_partial_bytes_and_resynchronizes_after_oversize():
    row=ROWS['command_forward'];wire=bytes.fromhex(row['wire_hex']);payload=bytes.fromhex(row['payload_hex'])
    framer=Framer();assert framer.feed(wire[:7])==[]
    assert framer.feed(wire[7:-1])==[]
    assert framer.feed(wire[-1:])==[(row['id'],payload)]
    assert framer.feed(bytes.fromhex(ROWS['hello3']['wire_hex'])+b'\0')==[(ROWS['hello3']['id'],bytes.fromhex(ROWS['hello3']['payload_hex']))]
    assert framer.feed(bytes.fromhex(VECTORS['malformed'][4]['wire_hex'])+wire)==[(row['id'],payload)]
    assert framer.oversize==1


def test_actual_command_layout_preserves_stop_sentinels_direction_hold_and_original_deadlines():
    command=lambda name:COMMAND3.unpack(bytes.fromhex(ROWS[name]['payload_hex']))
    stop=command('command_stop');forward=command('command_forward');reverse=command('command_reverse');hold=command('command_hold_steer')
    assert stop[3:8]==(bytes(16),0,0,0,0) and stop[10]==2 and stop[11:]==(0,0,0,0,0)
    assert forward[12]>0 and reverse[12]<0 and hold[10]==3 and hold[12]==0 and hold[13]!=0
    assert forward[9]==255 and reverse[9]==0  # debug counter wrap does not reuse command sequence.
    assert len({item[2] for item in (stop,forward,reverse,hold)})==4
    for item in (forward,reverse,hold):
        assert item[3:8]==forward[3:8] and item[11]==stop[2]
        assert item[5]<item[6]<=item[5]+100000 and item[7]>0


def test_actual_report_layout_distinguishes_status_and_unsupported_measurements():
    report=lambda name:FEEDBACK3.unpack(bytes.fromhex(ROWS[name]['payload_hex']))
    assert [report(name)[6] for name in ('report_accepted','report_applied_stop','report_rejected','report_fault')]==[1,2,3,4]
    stop=report('report_applied_stop');assert stop[5]==2 and stop[6]==2 and stop[10]&4 and stop[13]==1
    unknown=report('report_unknown_measurements');assert unknown[10:14]==(0,0,0,0)
    fault=report('report_unsolicited_fault');assert fault[2]==0 and fault[5:7]==(0,4)


def odometry():
    # Codec/kinematic fixture only. These values are not approved physical settings.
    cfg=OdometryConfig('feedback3-vector-fixture','fixture-calibration',.001,.2,.25,2.,20000,200000,20,32,
        .005,.01,.02,.02)
    owner=VehicleOdometry(cfg,'12345678-1234-4234-8234-123456789005')
    body=SENSOR3.unpack(bytes.fromhex(ROWS['sensor_measured']['payload_hex']))
    owner.device_session(str(UUID(bytes=body[1])))
    return owner


@pytest.mark.parametrize('name,field',[('sensor_measured','measured_steering_rad'),
    ('sensor_estimated','estimated_steering_rad'),('sensor_encoder_only',None)])
def test_actual_mapped_sensor_preserves_quality_and_positive_error(name,field):
    owner=odometry();values=SENSOR3.unpack(bytes.fromhex(ROWS[name]['payload_hex']))
    pi,device,sample,stamp,left,right,quality,steer,error,_,_=values
    args=(str(UUID(bytes=pi)),str(UUID(bytes=device)))
    assert owner.observe_mapped(*args,stamp,sample,left,right,quality,steer,error,1019990,1020230) is None
    current=owner.observe_mapped(*args,stamp+20000,sample+1,left+1,right+1,quality,steer,error,1039990,1040230)
    if field is None:
        assert 'measured_steering_rad' not in current and 'estimated_steering_rad' not in current
    else:
        assert current[field]==pytest.approx(steer/1000)
        assert current['steering_error_rad']==pytest.approx(error/1000) and current['steering_error_rad']>0
        assert ('estimated_steering_rad' if field=='measured_steering_rad' else 'measured_steering_rad') not in current


@pytest.mark.parametrize('quality',[3,5])
def test_actual_mapped_sensor_rejects_measured_or_estimated_zero_error(quality):
    owner=odometry();values=SENSOR3.unpack(bytes.fromhex(ROWS['sensor_measured']['payload_hex']))
    pi,device,sample,stamp,left,right,_,steer,_,_,_=values
    with pytest.raises(OdometryError,match='ODOM_STEERING_QUALITY'):
        owner.observe_mapped(str(UUID(bytes=pi)),str(UUID(bytes=device)),stamp,sample,left,right,
            quality,steer,0,1019990,1020230)


def test_tof_design_vectors_cover_outer_frame_and_original_crc_domain_only():
    """Generic framing PASS is not acceptance by the current feedback3 payload decoder."""
    design=VECTORS['tof_design_only'];assert not design['current_serial_payload_decoder']
    whole=bytes.fromhex(design['whole_crc_body_hex']);assert len(whole)==393
    assert zlib.crc32(whole)&0xffffffff==design['sample_crc32']
    zones=[]
    for index in range(4):
        row=ROWS['design_tof_fragment_'+str(index)];payload=bytes.fromhex(row['payload_hex']);wire=bytes.fromhex(row['wire_hex'])
        assert row['origin']=='external_design_only_no_current_serial_decoder' and len(payload)==209
        assert packet(0x0f16,payload)==wire and Framer().feed(wire)==[(0x0f16,payload)]
        assert payload[135:139]==bytes((index,4,16*index,16))
        assert struct.unpack_from('<I',payload,141)[0]==design['sample_crc32']
        zones.append(payload[145:])
    assert b''.join(zones)==whole[-256:]
