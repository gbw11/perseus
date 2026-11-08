"""공용 command3 parser의 역할별 NIL 계약. 수신/장치 권위는 별도 통합 검사다."""
import pytest

from xenova_gateway.command_contract import NIL,parse


ACTIVE='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'


def command(app,kind='DRIVE'):
    manual=app=='manual';stop=kind=='STOP'
    value=dict(profile='drive-command-3',generation=0 if stop else 44,
        producer_role='MANUAL' if manual else 'AUTO',command_kind=kind,stop_reason=1 if stop else 0,
        v_mps=0. if stop else .1,steer_rad=0.,mission_id=NIL if manual or stop else ACTIVE,
        mission_revision='0' if manual or stop else '1',grant_revision='0' if manual or stop else '2',
        calculation_sequence='0' if stop else '41',vehicle_sequence='0' if stop else '328',
        world_sequence='0' if manual or stop else '10',plan_revision='0' if manual or stop else '19',
        phase_revision='0' if manual or stop else '23',segment_index=0,cusp_index=0,
        completed_guest_us='0' if stop else '1000000',valid_until_pi_us='0' if stop else '113009999',
        driver_session=ACTIVE if manual and not stop else NIL,
        driver_action_seq='1' if manual and not stop else '0',driver_state_seq='1' if manual and not stop else '0',
        clock_probe_sequence='0' if stop else '7',next_direction=0)
    return value


@pytest.mark.parametrize('app,kind',[
    ('autonomous','DRIVE'),('manual','DRIVE'),('autonomous','STOP'),('manual','STOP')])
def test_exact_role_nil_and_clockless_stop_sentinels_are_valid(app,kind):
    value=command(app,kind)
    assert parse(value,app)==value


@pytest.mark.parametrize('app,field,value',[
    ('autonomous','mission_id',NIL),('manual','driver_session',NIL),
    ('autonomous','mission_id',ACTIVE.upper()),('manual','driver_session',ACTIVE.upper()),
    ('autonomous','mission_id',ACTIVE.replace('-','')),('manual','driver_session',ACTIVE.replace('-','')),
    ('autonomous','driver_session',ACTIVE),('manual','mission_id',ACTIVE)])
def test_active_nil_noncanonical_and_non_nil_inactive_ids_are_rejected(app,field,value):
    payload=command(app);payload[field]=value
    with pytest.raises(ValueError):parse(payload,app)
