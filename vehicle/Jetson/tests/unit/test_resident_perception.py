"""M의 명시 보정·원 RGB/depth 전달과 개발용 simulation import 격리를 검사한다."""

import hashlib
import json
from pathlib import Path
import subprocess
import socket
import sys
import uuid
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from raw_binding_fixtures import binding_fields
from test_resident_samples import sample
from xenova_vehicle.perception.calibration import CalibrationProfile,canonical,synthetic_calibration
from xenova_vehicle.runtime.raw_binding import RawBinding
from xenova_vehicle.runtime.resident_perception import ResidentPerception
from xenova_vehicle.runtime.resident_profile import runtime_artifacts
from xenova_vehicle.runtime.raw_binding import RawBindingProfile
from xenova_vehicle.runtime.lifecycle_messages import LifecyclePeer
from xenova_vehicle.runtime.resident_workers import _Actor
from xenova_vehicle.yolop_profile import YolopBenchProfile


def provisional():
    value=synthetic_calibration().to_dict()
    value.update(kind='PROVISIONAL',first_surface_qualified=False,time_qualified=False,minimum_target_qualified=False)
    return CalibrationProfile.from_dict(value)


def binding(calibration):
    fields=binding_fields()
    fields['calibration_bundle']=calibration.sha256
    return RawBinding(fields)


class ResidentPerceptionTests(unittest.TestCase):
    def test_raw_without_nn_reaches_shared_core_with_original_keys_and_unknown_capture_time(self):
        calibration=provisional()
        bound=binding(calibration)
        _,original=sample(nn=False)
        with patch('xenova_vehicle.perception.pipeline.PerceptionPipeline') as pipeline:
            pipeline.return_value.process.return_value=SimpleNamespace(summary=dict(world_valid=False,metric_ready=False))
            consumer=ResidentPerception(canonical(calibration.to_dict()),bound)
            summary=consumer.consume(original,now_us=1200)
            received=pipeline.return_value.process.call_args.args[0]
        self.assertEqual(received.kind,'LIVE')
        self.assertEqual(received.rgb.stamp.sequence,original.rgb.sequence)
        self.assertEqual(received.depth.stamp.sequence,original.depth.sequence)
        self.assertEqual(received.rgb.payload,original.rgb.payload)
        self.assertIsNone(received.capture_interval)
        self.assertIsNone(received.nn)
        self.assertEqual(received.rgb.stamp.available_us,original.rgb.received_j_us)
        self.assertEqual(received.rgb.stamp.clock_id,bound.fields['clock_epoch'])
        self.assertFalse(summary['world_valid'])

    def test_calibration_is_pinned_as_raw_artifact_and_must_match_live_binding(self):
        calibration=provisional()
        raw=canonical(calibration.to_dict())
        artifacts=runtime_artifacts(YolopBenchProfile('FAKE',Path('assets'),Path('manifest.json')),raw)
        self.assertEqual(artifacts['calibration_bundle'],calibration.sha256)
        self.assertEqual(hashlib.sha256(artifacts['manifests']['perception-calibration.json']).hexdigest(),calibration.sha256)
        fields=binding_fields()
        with self.assertRaises(ValueError):
            ResidentPerception(raw,RawBinding(fields))
        synthetic=synthetic_calibration()
        with self.assertRaises(ValueError):
            ResidentPerception(canonical(synthetic.to_dict()),binding(synthetic))

    def test_production_import_and_constructor_do_not_load_simulation_or_doctor(self):
        code='''import sys
from test_resident_perception import provisional,binding
from xenova_vehicle.perception.calibration import canonical
from xenova_vehicle.runtime.resident_perception import ResidentPerception
c=provisional()
ResidentPerception(canonical(c.to_dict()),binding(c))
assert not any(n.startswith('xenova_vehicle.simulation') for n in sys.modules)
assert 'xenova_vehicle.doctor' not in sys.modules
assert 'depthai' not in sys.modules
'''
        result=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)

    @unittest.skipUnless(sys.platform=='linux','실제 Linux seqpacket 통로 시험')
    def test_actor_boot_compares_the_same_pinned_calibration_and_emits_actual_hello(self):
        calibration=provisional()
        raw=canonical(calibration.to_dict())
        profile=YolopBenchProfile('FAKE',Path('assets'),Path('manifest.json'))
        artifacts=runtime_artifacts(profile,raw)
        refs=RawBindingProfile('FAKE',str(uuid.uuid4()),artifacts['model_id'],artifacts['parser_id'],
            artifacts['graph_profile_sha256'],artifacts['format_table_sha256'],artifacts['calibration_bundle'],True,False)
        peer=LifecyclePeer(str(uuid.uuid4()),str(uuid.uuid4()),str(uuid.uuid4()),'A',artifacts['release_sha256'])
        parent,child=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        parent.setblocking(False)
        child.setblocking(False)
        actor=None
        try:
            actor=_Actor(child,SimpleNamespace(profile=profile,peer=peer,raw_profile=refs,perception_profile_bytes=raw))
            parent.settimeout(2)
            packet=json.loads(parent.recv(65536))
            self.assertEqual(packet['kind'],'HELLO')
            self.assertEqual(packet['payload']['release_sha256'],artifacts['release_sha256'])
        finally:
            parent.close()
            if actor is not None:
                actor.link.close()
            else:
                child.close()


if __name__=='__main__':
    unittest.main()
