"""부록A7 drive-command-2/3 exact payload. STOP은 이동 권위 검사와 분리한다."""
import math
from xenova_mqtt.drive_binding import canonical as uuid
from xenova_mqtt.lifecycle import counter
from .authority_journal import canonical

NIL='00000000-0000-0000-0000-000000000000'
FIELDS={'profile','generation','producer_role','command_kind','stop_reason','v_mps','steer_rad',
    'mission_id','mission_revision','grant_revision','calculation_sequence','vehicle_sequence','world_sequence',
    'plan_revision','phase_revision','segment_index','cusp_index','completed_guest_us','valid_until_pi_us',
    'driver_session','driver_action_seq','driver_state_seq'}
COUNTERS={'mission_revision','grant_revision','calculation_sequence','vehicle_sequence','world_sequence',
    'plan_revision','phase_revision','completed_guest_us','valid_until_pi_us','driver_action_seq','driver_state_seq'}


def parse(payload,app):
    modern=type(payload) is dict and payload.get('profile')=='drive-command-3'
    if (type(payload) is not dict or set(payload)!=(FIELDS|{'clock_probe_sequence','next_direction'} if modern else FIELDS)
            or payload['profile'] not in ('drive-command-2','drive-command-3')):
        raise ValueError('exact command2 fields required')
    if modern:
        counter(payload['clock_probe_sequence'])
        if type(payload['next_direction']) is not int or payload['next_direction'] not in (-1,0,1):raise ValueError('command3 next direction')
        if (app=='manual' or payload['command_kind']=='DRIVE') and payload['next_direction']:
            raise ValueError('next direction is only an AUTO pending transition intent')
    if len(canonical(payload))>1536:raise ValueError('command2 payload cap')
    for name in COUNTERS:counter(payload[name])
    # 역할별 미사용 ID의 정확한 NIL sentinel은 wire 계약이다. 활성 ID의
    # nonzero 여부는 아래 role 검사에서 확인하고 다른 UUID는 정규형을 요구한다.
    for name in ('mission_id','driver_session'):
        if payload[name]!=NIL:uuid(payload[name])
    for name,upper in (('generation',2**32-1),('stop_reason',2**32-1),('segment_index',2**32-1),('cusp_index',2**32-1)):
        if type(payload[name]) is not int or not 0<=payload[name]<=upper:raise ValueError('command2 integer')
    if payload['producer_role']!=('AUTO' if app=='autonomous' else 'MANUAL' if app=='manual' else None):
        raise ValueError('command2 authenticated role')
    if payload['command_kind'] not in ('DRIVE','STOP','HOLD_STEER'):raise ValueError('command2 kind')
    if any(type(payload[n]) not in (int,float) or not math.isfinite(payload[n]) for n in ('v_mps','steer_rad')):
        raise ValueError('finite command2 target')
    if payload['command_kind']=='STOP':
        if payload['v_mps'] or payload['steer_rad']:raise ValueError('STOP reserved targets')
        return dict(payload)
    if (not payload['generation'] or any(not int(payload[n]) for n in
            ('calculation_sequence','vehicle_sequence','completed_guest_us','valid_until_pi_us'))
            or modern and not int(payload['clock_probe_sequence'])):
        raise ValueError('current calculation/vehicle/source expiry required')
    if payload['command_kind']=='HOLD_STEER' and payload['v_mps']:raise ValueError('stationary steering')
    if app=='manual':
        if (payload['mission_id']!=NIL or any(int(payload[n]) for n in ('mission_revision','grant_revision',
                'world_sequence','plan_revision','phase_revision','segment_index','cusp_index'))
                or payload['driver_session']==NIL or not int(payload['driver_state_seq'])):
            raise ValueError('MANUAL sentinel/driver witness')
    elif (payload['mission_id']==NIL or payload['driver_session']!=NIL or any(int(payload[n]) for n in
            ('driver_action_seq','driver_state_seq')) or not int(payload['mission_revision'])
            or not int(payload['grant_revision']) or not int(payload['world_sequence'])):
        raise ValueError('AUTO mission/world witness')
    return dict(payload)
