"""개발 descriptor/RAW 진술과 원 J pair의 순수 회귀다. OS 등록·실송신·센서 수락은 아니다.

WallCapture는 명시 literal owned bytes fixture이며 renderer/SDK/GT를 호출하지 않는다.
잘못된 원 packet과 변조 사례를 검산하며 physical v1을 개발용으로 바꾸지 않는다.
"""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import json
import unittest
from uuid import UUID

from xenova_vehicle.perception.inputs import FrameStamp, ImageFrame, PerceptionInput
from xenova_vehicle.runtime.development_source import (
    DevelopmentBindAck, DevelopmentBindContext, DevelopmentBindMessage,
    DevelopmentSourceBinding, DevelopmentSourceError, j_capture_pairs,
)
from xenova_vehicle.runtime.lifecycle_messages import LifecyclePeer
from xenova_vehicle.runtime.local_envelope import decode_structure
from xenova_vehicle.runtime.raw_binding import RawBinding, RawBindingError
from xenova_vehicle.runtime.raw_binding_messages import RawBindAck
from xenova_vehicle.runtime.raw_leases import Endpoint
from xenova_vehicle.simulation.wall_sensor import WallCapture, WallDiagnosticCapture


def uid(value):
    return str(UUID(int=value))


def descriptor_fields():
    return dict(profile='development-owned-source-1', service_instance=uid(1), producer_instance=uid(3),
        capture_epoch=uid(11), stream_instances=dict(rgb_video=uid(12), depth_right=uid(13)),
        clock_epoch=uid(14), calibration_model_sha256='a'*64, motion_profile_sha256='b'*64,
        calibration_profile_sha256='c'*64, nn_manifest_sha256='d'*64)


def context(role='A', *, binding=None):
    expected = DevelopmentSourceBinding(descriptor_fields()) if binding is None else binding
    endpoint = Endpoint(uid(2), uid(3))
    peer = LifecyclePeer(uid(1), uid(2) if role == 'A' else uid(4),
                         uid(3) if role == 'A' else uid(5), role, 'f'*64)
    return DevelopmentBindContext(peer, endpoint, expected)


def packet(fields, *, pretty=False):
    return decode_structure(json.dumps(fields, sort_keys=True, ensure_ascii=True,
        indent=2 if pretty else None, separators=None if pretty else (',', ':'), allow_nan=False).encode('ascii'))


def bind_fields(ctx, *, binding=None, issued=100, expires=5_000_100):
    value = ctx.expected_binding if binding is None else binding
    return dict(version=1, service_instance=ctx.peer.service_instance, channel_epoch=ctx.peer.channel_epoch,
        sender_instance=ctx.peer.service_instance, message_sequence='10', kind='BIND', binding_domain='RAW',
        binding_revision='1', request_id=uid(20), issued_j_us=str(issued), expires_j_us=str(expires),
        payload=dict(binding_revision='1', binding_sha256=value.sha256, binding=value.fields))


def bind(ctx=None):
    ctx = context() if ctx is None else ctx
    return DevelopmentBindMessage(packet(bind_fields(ctx)), ctx)


def ack_fields(original, *, accepted=True, state=None, reason=None):
    state = ('WARMUP' if original.context.peer.role == 'A' else 'WAIT_INPUT') if state is None else state
    return dict(version=1, service_instance=original.context.peer.service_instance,
        channel_epoch=original.context.peer.channel_epoch, sender_instance=original.context.peer.worker_instance,
        message_sequence='11', kind='BIND_ACK', binding_domain='RAW', binding_revision='1',
        request_id=original.request_id, issued_j_us='200', expires_j_us=str(original.expires_us),
        payload=dict(binding_revision='1', binding_sha256=original.binding.sha256,
                     accepted=accepted, state=state, reason=reason))


def capture(binding=None, *, sequence=9, interval=(1_000_000, 1_000_007), completed=1_000_100,
            expires=1_500_000, source=None, clock=None, calibration=None, diagnostic=False):
    value = DevelopmentSourceBinding(descriptor_fields()) if binding is None else binding
    def image(stream, width, height, encoding, size):
        stamp = FrameStamp(value.source_id if source is None else source, stream, sequence,
            value.clock_id if clock is None else clock, interval, completed)
        return ImageFrame(stamp, width, height, encoding, bytes(size), 0)
    observation = PerceptionInput('SYNTHETIC', value.fields['calibration_profile_sha256'] if calibration is None else calibration,
        image('rgb_video', 640, 360, 'NV12', 640*360*3//2),
        image('depth_right', 640, 480, 'RAW16_LE', 640*480*2))
    cls = WallDiagnosticCapture if diagnostic else WallCapture
    return cls(observation, 'e'*64, interval, completed, expires)


class DevelopmentSourceTests(unittest.TestCase):
    def test_descriptor_exact_canonical_owned_bytes_and_explicit_nn_absent(self):
        fields = descriptor_fields()
        value = DevelopmentSourceBinding(fields)
        expected = json.dumps(fields, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode('ascii')
        self.assertEqual(value.canonical_bytes, expected)
        self.assertEqual(value.sha256, sha256(expected).hexdigest())
        self.assertLessEqual(len(expected), 2048)
        self.assertEqual(value.domain, 'DEVELOPMENT_SYNTHETIC')
        fields['stream_instances']['rgb_video'] = uid(500)
        copied = value.fields
        copied['motion_profile_sha256'] = 'f'*64
        copied['stream_instances'].clear()
        self.assertEqual(value.fields, descriptor_fields())
        with self.assertRaises(FrozenInstanceError):
            value.canonical_bytes = b'changed'
        absent = descriptor_fields(); absent['nn_manifest_sha256'] = None
        self.assertIsNone(DevelopmentSourceBinding(absent).fields['nn_manifest_sha256'])
        self.assertNotIn('sdk_bridge_epoch', value.fields)

    def test_descriptor_rejects_unknown_missing_foreign_version_and_unbounded_values(self):
        base = descriptor_fields()
        for key in tuple(base):
            bad = deepcopy(base); del bad[key]
            with self.subTest(missing=key), self.assertRaises(DevelopmentSourceError):
                DevelopmentSourceBinding(bad)
        variants = []
        for name, value in (('profile', 'development-owned-source-2'), ('producer_instance', uid(0)),
                ('clock_epoch', True), ('capture_epoch', 'x'*100_000), ('nn_manifest_sha256', 'D'*64),
                ('motion_profile_sha256', None), ('calibration_model_sha256', 'a'*63)):
            bad = deepcopy(base); bad[name] = value; variants.append(bad)
        bad = deepcopy(base); bad['sdk_bridge_epoch'] = uid(99); variants.append(bad)
        bad = deepcopy(base); bad['stream_instances']['depth_right'] = bad['stream_instances']['rgb_video']; variants.append(bad)
        bad = deepcopy(base); bad['stream_instances']['nn_output0'] = uid(99); variants.append(bad)
        bad = deepcopy(base); bad['stream_instances'] = list(bad['stream_instances']); variants.append(bad)
        for bad in variants:
            with self.subTest(bad=repr(bad)[:90]), self.assertRaises(DevelopmentSourceError):
                DevelopmentSourceBinding(bad)

    def test_context_binds_registered_a_and_separate_m_reader_without_claiming_os_facts(self):
        for role in ('A', 'M'):
            ctx = context(role)
            self.assertEqual(ctx.expected_binding.fields['producer_instance'], ctx.acquisition.worker_instance)
            if role == 'M':
                self.assertNotEqual(ctx.peer.worker_instance, ctx.acquisition.worker_instance)
            for name in ('channel_epoch', 'worker_instance'):
                endpoint = replace(ctx.acquisition, **{name: uid(99) if role == 'A' else getattr(ctx.peer, name)})
                with self.subTest(role=role, field=name), self.assertRaises(DevelopmentSourceError):
                    DevelopmentBindContext(ctx.peer, endpoint, ctx.expected_binding)
        ctx = context()
        for peer in (replace(ctx.peer, role='D'), replace(ctx.peer, service_instance=uid(99))):
            with self.assertRaises(DevelopmentSourceError):
                DevelopmentBindContext(peer, ctx.acquisition, ctx.expected_binding)
        with self.assertRaises(DevelopmentSourceError):
            DevelopmentBindContext(object(), ctx.acquisition, ctx.expected_binding)

    def test_bind_ack_preserve_actual_packet_identity_and_original_lifetime_for_both_targets(self):
        for role in ('A', 'M'):
            ctx = context(role)
            original_packet = packet(bind_fields(ctx), pretty=True)
            original = DevelopmentBindMessage(original_packet, ctx, cap_us=5_000_100)
            response_packet = packet(ack_fields(original))
            response = DevelopmentBindAck(response_packet, original)
            self.assertIs(original.packet, original_packet)
            self.assertIs(response.packet, response_packet)
            self.assertIs(response.original_bind, original)
            self.assertIs(response.binding, original.binding)
            self.assertIs(response.context, ctx)
            self.assertEqual(original.raw, original_packet.raw_bytes)
            self.assertEqual((original.issued_us, response.issued_us, response.expires_us), (100, 200, 5_000_100))
            self.assertTrue(response.accepted)
            self.assertEqual(response.request_id, original.request_id)
            self.assertFalse(any(hasattr(response, name) for name in ('registered', 'nn_ready', 'metric_ready', 'authorized')))
            copied = response.fields; copied['payload']['accepted'] = False
            self.assertTrue(response.payload['accepted'])

    def test_self_consistent_changed_body_sha_cannot_replace_s_independent_pins(self):
        ctx = context()
        changes = dict(clock_epoch=uid(99), capture_epoch=uid(98), calibration_model_sha256='1'*64,
            motion_profile_sha256='2'*64, calibration_profile_sha256='3'*64, nn_manifest_sha256=None,
            service_instance=uid(97), producer_instance=uid(96))
        for name, replacement in changes.items():
            body = descriptor_fields(); body[name] = replacement
            value = DevelopmentSourceBinding(body)
            with self.subTest(pin=name), self.assertRaises(DevelopmentSourceError):
                DevelopmentBindMessage(packet(bind_fields(ctx, binding=value)), ctx)
        body = descriptor_fields(); body['stream_instances']['rgb_video'] = uid(95)
        with self.assertRaises(DevelopmentSourceError):
            DevelopmentBindMessage(packet(bind_fields(ctx, binding=DevelopmentSourceBinding(body))), ctx)

    def test_bind_requires_exact_envelope_payload_domain_revision_hash_and_original_caps(self):
        ctx = context()
        base = bind_fields(ctx)
        for name, replacement in (('service_instance', uid(99)), ('channel_epoch', uid(99)),
                ('sender_instance', ctx.peer.worker_instance), ('kind', 'BIND_ACK'),
                ('binding_domain', 'PUBLICATION'), ('binding_revision', '0'),
                ('issued_j_us', '5000100'), ('expires_j_us', '5000101')):
            bad = deepcopy(base); bad[name] = replacement
            with self.subTest(field=name), self.assertRaises(DevelopmentSourceError):
                DevelopmentBindMessage(packet(bad), ctx)
        for name, replacement in (('binding_revision', '2'), ('binding_sha256', 'f'*64), ('unknown', None)):
            bad = deepcopy(base); bad['payload'][name] = replacement
            with self.subTest(payload=name), self.assertRaises(DevelopmentSourceError):
                DevelopmentBindMessage(packet(bad), ctx)
        with self.assertRaises(DevelopmentSourceError):
            DevelopmentBindMessage(packet(base), ctx, cap_us=5_000_099)
        with self.assertRaises(DevelopmentSourceError):
            DevelopmentBindMessage(packet(base), ctx, cap_us=True)

    def test_ack_matches_original_request_peer_hash_revision_and_contained_lifetime(self):
        original = bind()
        base = ack_fields(original)
        for name, replacement in (('request_id', uid(99)), ('sender_instance', uid(99)),
                ('channel_epoch', uid(99)), ('service_instance', uid(99)), ('kind', 'BIND'),
                ('binding_domain', 'STORAGE'), ('binding_revision', '2'),
                ('issued_j_us', '99'), ('expires_j_us', '5000101'), ('issued_j_us', '5000100')):
            bad = deepcopy(base); bad[name] = replacement
            with self.subTest(field=name, value=replacement), self.assertRaises(DevelopmentSourceError):
                DevelopmentBindAck(packet(bad), original)
        for name, replacement in (('binding_sha256', 'f'*64), ('binding_revision', '2'), ('unknown', None)):
            bad = deepcopy(base); bad['payload'][name] = replacement
            with self.subTest(payload=name), self.assertRaises(DevelopmentSourceError):
                DevelopmentBindAck(packet(bad), original)

    def test_ack_refusal_and_active_state_rules_match_existing_raw_variant(self):
        for role in ('A', 'M'):
            original = bind(context(role))
            negative = ack_fields(original, accepted=False, state='FAILED', reason='LOCAL_SETUP_FAILED')
            self.assertFalse(DevelopmentBindAck(packet(negative), original).accepted)
            for accepted, state, reason in ((True, 'FAILED', None), (True, 'WARMUP', 'ASSET_UNAVAILABLE'),
                    (False, 'FAILED', None), (False, 'FAILED', 'NEW_REASON'), (1, 'WARMUP', None)):
                with self.subTest(role=role, values=(accepted, state, reason)), self.assertRaises(DevelopmentSourceError):
                    DevelopmentBindAck(packet(ack_fields(original, accepted=accepted, state=state, reason=reason)), original)

    def test_physical_binding_and_ack_entry_points_remain_separate(self):
        value = DevelopmentSourceBinding(descriptor_fields())
        with self.assertRaises(RawBindingError):
            RawBinding(value.fields)
        original = bind()
        with self.assertRaises(RawBindingError):
            RawBindAck(packet(ack_fields(original)), original)
        physical = value.fields; physical['sdk_bridge_epoch'] = uid(99)
        with self.assertRaises(DevelopmentSourceError):
            DevelopmentSourceBinding(physical)

    def test_j_pairs_keep_both_original_endpoints_sequence_and_registered_streams(self):
        value = DevelopmentSourceBinding(descriptor_fields())
        original = capture(value)
        rgb_bytes, depth_bytes = original.observation.rgb.payload, original.observation.depth.payload
        pairs = j_capture_pairs(original, value)
        self.assertEqual(pairs, (((uid(12), 9, 1_000_000), (uid(12), 9, 1_000_007)),
                                 ((uid(13), 9, 1_000_000), (uid(13), 9, 1_000_007))))
        self.assertIs(original.observation.rgb.payload, rgb_bytes)
        self.assertIs(original.observation.depth.payload, depth_bytes)
        self.assertEqual((original.captured_j_us, original.completed_j_us, original.expires_j_us),
                         ((1_000_000, 1_000_007), 1_000_100, 1_500_000))
        with self.assertRaises(TypeError):
            pairs[0][0] = (uid(1), 0, 0)
        self.assertFalse(any(hasattr(original.observation.rgb, name) for name in ('sdk_us', 'device_us')))

    def test_j_pairs_reject_foreign_source_clock_calibration_and_diagnostic_or_zero_width(self):
        value = DevelopmentSourceBinding(descriptor_fields())
        for kwargs in (dict(source='sim-wall:'+uid(99)), dict(clock='jetson-monotonic:'+uid(99)),
                dict(calibration='f'*64), dict(diagnostic=True), dict(interval=(1_000_000, 1_000_000))):
            with self.subTest(kwargs=kwargs), self.assertRaises(DevelopmentSourceError):
                j_capture_pairs(capture(value, **kwargs), value)

    def test_j_pairs_revalidate_tampered_original_hull_sequence_nn_and_no_new_expiry(self):
        value = DevelopmentSourceBinding(descriptor_fields())
        for mutate in ('reversed', 'sequence', 'stream_hull', 'nn', 'extended_expiry', 'availability'):
            original = capture(value)
            # 이 표본만 의도적으로 변조해 helper의 원 metadata 재검사를 확인한다.
            if mutate == 'reversed':
                object.__setattr__(original, 'captured_j_us', (1_000_007, 1_000_000))
            elif mutate == 'sequence':
                changed = replace(original.observation.depth.stamp, sequence=10)
                depth = replace(original.observation.depth, stamp=changed)
                object.__setattr__(original, 'observation', replace(original.observation, depth=depth))
            elif mutate == 'stream_hull':
                changed = replace(original.observation.depth.stamp, captured_us=(1_000_000, 1_000_006))
                depth = replace(original.observation.depth, stamp=changed)
                object.__setattr__(original, 'observation', replace(original.observation, depth=depth))
            elif mutate == 'nn':
                object.__setattr__(original, 'observation', replace(original.observation, nn=object(), nn_key=('foreign',)))
            elif mutate == 'extended_expiry':
                object.__setattr__(original, 'expires_j_us', 1_500_001)
            else:
                object.__setattr__(original, 'completed_j_us', 1_000_101)
            with self.subTest(mutation=mutate), self.assertRaises(DevelopmentSourceError):
                j_capture_pairs(original, value)
