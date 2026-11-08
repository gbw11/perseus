"""새 road basis와 global hard domain의 Python/C 동일 수락 경계.

기하·시각은 명시 software fixture다. 실제 source/scene/통행 승인은 만들지 않는다.
공용 실제 C decoder를 ASan/UBSan으로 실행하며 legacy side 정책도 별도로 보존한다.
"""

import pytest

from xenova_drive_world import encode
from test_world import probe,product,fixture,parking_world
from test_world import modern_world


def world(features):
    value=parking_world() if features==15 else modern_world() if features==11 else fixture.world()
    value.sequence=2
    value.ClearField('reference');value.ClearField('left_boundary');value.ClearField('right_boundary')
    for x,y in ((1.,1.),(3.,1.),(3.,3.),(1.,3.)):
        value.reference.add(x=x,y=y)
    # actual global rectangle의 두 chain. basis의 마지막 서향 구간과 독립이다.
    for x in (0.,4./3,8./3,4.):
        value.left_boundary.add(x=x,y=4.)
        value.right_boundary.add(x=x,y=0.)
    return value


@pytest.mark.parametrize('features',[11,15])
def test_open_curved_basis_does_not_reinterpret_global_domain_as_lane_sides(probe,features):
    value=world(features)
    wire=encode(value)
    assert product(probe,wire)['result']=='OK'


@pytest.mark.parametrize('features',[0,1])
def test_legacy_per_station_side_rule_is_retained(probe,features):
    value=world(0)
    if features==1:
        value.required_features=1;value.cell_flags=bytes([9])*(value.width*value.height)
    assert product(probe,value.SerializeToString())['result']=='GEOMETRY'


@pytest.mark.parametrize('kind',['SELF_CROSSING_DOMAIN','CLOSED_BASIS','SELF_CROSSING_BASIS'])
def test_new_geometry_remains_closed_to_crossings_and_revisited_station(probe,kind):
    value=world(11)
    if kind=='SELF_CROSSING_DOMAIN':
        value.left_boundary[1].x,value.left_boundary[1].y=3.,-1.
    else:
        points=((1.,1.),(3.,1.),(3.,3.),(1.,1.)) if kind=='CLOSED_BASIS' else ((1.,1.),(3.,3.),(1.,3.),(3.,1.))
        for target,(x,y) in zip(value.reference,points):target.x,target.y=x,y
    with pytest.raises(ValueError):encode(value)
    assert product(probe,value.SerializeToString())['result']=='GEOMETRY'


@pytest.mark.parametrize('points',[((1.,1.),(2.,1.),(1.,1.)),((1.,1.),(3.,1.),(2.,1.))])
def test_two_adjacent_edges_cannot_close_or_retrace_station(probe,points):
    value=world(11)
    for field in ('reference','left_boundary','right_boundary'):value.ClearField(field)
    for (x,y),boundary_x in zip(points,(0.,2.,4.)):
        value.reference.add(x=x,y=y)
        value.left_boundary.add(x=boundary_x,y=4.)
        value.right_boundary.add(x=boundary_x,y=0.)
    with pytest.raises(ValueError):encode(value)
    assert product(probe,value.SerializeToString())['result']=='GEOMETRY'


def test_forward_collinear_stations_are_preserved(probe):
    value=world(11)
    for field in ('reference','left_boundary','right_boundary'):value.ClearField(field)
    for x,boundary_x in zip((1.,2.,3.),(0.,2.,4.)):
        value.reference.add(x=x,y=1.)
        value.left_boundary.add(x=boundary_x,y=4.)
        value.right_boundary.add(x=boundary_x,y=0.)
    assert product(probe,encode(value))['result']=='OK'
