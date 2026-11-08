"""실제 broker·Pi child·S-jio·S/M binary로 identity와 clock 왕복을 연결한다.

원 Vehicle은 명시한 시험 producer이며 실제 ESP/encoder/트랙 오차 수락이 아니다.
모델·지도·자동 제어는 우회 완료로 세지 않고 이 연결의 원 시각/lease만 검사한다.
"""

from base64 import b64decode
import importlib.util
import json
import socket
import time
from types import SimpleNamespace
import pytest

# platform-only checkout에는 상대 Jetson 제품/fixture가 없다. 존재하는 코드의 import 오류는 숨기지 않는다.
if (importlib.util.find_spec('xenova_vehicle') is None
        or importlib.util.find_spec('test_source_identity') is None):
    pytest.skip('paired Jetson src and tests/unit paths are absent',allow_module_level=True)

from test_source_identity import fixture,uid
from test_state_transport import broker
from xenova_drive_world import encode,decode,schema
from xenova_drive_world.mapper_clock import ClockPolicy,restore_returned_probe
from xenova_gateway.mapper_uplink import GatewayMapperUplink,UplinkProfile
from xenova_vehicle.runtime.data_channel import DataLink,data_pair
from xenova_vehicle.runtime.resident_transport import j_us
from xenova_vehicle.runtime.source_runtime import SupervisorSource,MapperSource
from xenova_vehicle.transport.jetson_jio import JetsonJio
from xenova_vehicle.transport.jio_profile import JetsonJioProfile


@pytest.mark.skipif(not hasattr(socket,'SOCK_SEQPACKET'),reason='actual Linux seqpacket required')
def test_actual_mqtt_identity_initial_probes_and_returned_lease_keep_original_source(broker):
    profile,raw,policy,sample,bootstrap,peer,incarnation=fixture()
    clock=ClockPolicy(policy.vehicle,'oak-front',policy.configuration,policy.calibration,policy.primitive,
        20,10,100000,1000000,500000,1000000)
    jio=JetsonJio(JetsonJioProfile(policy,clock,clock.device,'127.0.0.1',broker))
    uplink=GatewayMapperUplink(UplinkProfile(clock,'127.0.0.1',broker,None,None),sample.host_boot,sample.gateway_instance)
    left,right=data_pair()
    mapper=MapperSource(DataLink(left,peer,'M'),raw,policy,mapper_instance=incarnation,device=clock.device,clock=j_us)
    supervisor=SupervisorSource(DataLink(right,peer,'S'),raw,jio,mapper_instance=incarnation,
        mapper_boot=uid(),clock_epoch=raw.fields['clock_epoch'],revision_floor=7,clock=j_us)
    material=({'service_instance':peer.service_instance,'storage_worker':uid(),'storage_profile_sha256':'c'*64,
        'database_uuid':bootstrap['database_uuid'],'db_schema_version':1},bootstrap)
    provider=SimpleNamespace(trust=None,world_failure=None);publications=[]
    source_wires={};next_vehicle=0;returned=None
    try:
        jio.start();uplink.start()
        end=time.monotonic()+8
        while time.monotonic()<end:
            now=j_us()
            if now>=next_vehicle:
                sample.sequence+=1;sample.source_sample=sample.sequence
                sample.source_us=now-100;sample.pi_sample_us=now
                wire=encode(sample);source_wires[wire]=now
                uplink.offer_vehicle(wire);next_vehicle=now+20000
            uplink.tick(provider)
            supervisor.pump(material,bind_ready=True,on_publication=lambda binding,context:publications.append((binding,context)))
            mapper.pump()
            if mapper.active_probe is not None:
                row=mapper.probes.get(mapper.active_probe)
                if row is not None:
                    returned=restore_returned_probe(b64decode(row['raw']),clock,mapper.source,sample.host_boot,j_us())
                    if returned is not None and mapper.vehicles:break
            time.sleep(.002)
        assert returned is not None,(uplink.failure,dict(uplink.counts),jio.snapshot(),dict(supervisor.counts))
        assert len(publications)==1 and int(mapper.publication['source_binding_revision'])==8
        assert returned.source_revision==uplink.owner.source_revision
        assert returned.source_revision!=int(mapper.publication['source_binding_revision'])
        assert returned.completed_pi_us is None and returned.time_lease_revision is None
        original_probe=decode(returned.raw_probe_bytes,schema.MapperClockProbe)
        assert encode(original_probe.mapper_trust)==returned.trust_bytes
        assert returned.expires_pi_us==original_probe.mapper_trust.expires_pi_us
        snapshot=json.loads(mapper.context_bytes())
        for row in snapshot['vehicles']:
            original=b64decode(row['raw']);assert original in source_wires
            assert decode(original,schema.VehicleSample).pi_sample_us==source_wires[original]
            assert int(row['received_j_us'])>=source_wires[original]
        assert uplink.counts['clock_warmup']>=1 and uplink.counts['clock_accepted']>=1
        assert jio.snapshot()['counts']['sent']>=2
        assert not jio.snapshot()['failure']
    finally:
        mapper.close();supervisor.close();jio.stop();assert jio.join(1.)
        uplink.close()
        assert uplink.process is None or uplink.process.exitcode is not None and not uplink.process.is_alive()
