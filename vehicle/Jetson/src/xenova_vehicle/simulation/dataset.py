"""합성 관측의 paired export/replay와 길이·해시·완료 꼬리 검사.

같은 snapshot의 두 payload를 닫은 뒤 index 한 줄을 게시한다. oracle은 별도 파일이며
reader가 읽지 않는다. 원 simulation 시각을 보존하고 physical OAK 기록으로 위장하지 않는다.
완료 표시는 clean-close 의미이며 정전 원자성을 보장하지 않는다.
"""

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

from ..perception.inputs import FrameStamp, ImageFrame, PerceptionInput
from .capture import DepthEffects, calibration_for_effects
from .scene import canonical


MAX_SNAPSHOTS = 256
_HEADER_LIMIT = 65_536
_INDEX_LIMIT = 2 * 1024**2


class SyntheticDatasetError(ValueError):
    """합성 기록의 형식·원 profile·payload 또는 완료 상태가 맞지 않는다."""


def _load(raw, *, canonical_required=True):
    value = json.loads(raw)
    if type(value) is not dict or canonical_required and canonical(value) != raw:
        raise SyntheticDatasetError('synthetic record is not a canonical object')
    return value


def _read(path, cap):
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > cap:
            raise SyntheticDatasetError('synthetic file exceeds its expected regular-file bound')
        raw = bytearray()
        while len(raw) <= cap:
            part = os.read(descriptor, min(65_536, cap + 1 - len(raw)))
            if not part:
                break
            raw.extend(part)
        def stamp(value):
            return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns
        if (len(raw) > cap or stamp(before) != stamp(os.fstat(descriptor))
                or stamp(before) != stamp(os.stat(path, follow_symlinks=False))):
            raise SyntheticDatasetError('synthetic file changed during bounded read')
        return bytes(raw)
    finally:
        os.close(descriptor)


def _coherent(value, calibration_id):
    if (type(value) is not PerceptionInput or value.kind != 'SYNTHETIC' or value.calibration_id != calibration_id
            or value.nn is not None or value.depth is None
            or (value.rgb.width, value.rgb.height, value.rgb.encoding) != (640, 360, 'NV12')
            or (value.depth.width, value.depth.height, value.depth.encoding) != (640, 480, 'RAW16_LE')):
        raise SyntheticDatasetError('export requires the pinned RGB/RIGHT synthetic capture')
    rgb, depth = value.rgb.stamp, value.depth.stamp
    if ((rgb.sequence, rgb.captured_us, rgb.available_us) != (depth.sequence, depth.captured_us, depth.available_us)
            or rgb.captured_us is None or rgb.captured_us[0] != rgb.captured_us[1]
            or not rgb.clock_id.startswith('simulation:') or rgb.stream_id != 'rgb_video' or depth.stream_id != 'depth_right'):
        raise SyntheticDatasetError('synthetic pair is not one coherent simulation snapshot')
    epoch = rgb.clock_id.removeprefix('simulation:')
    if str(uuid.UUID(epoch)) != epoch or not rgb.source_id.startswith('sim:' + epoch + ':'):
        raise SyntheticDatasetError('synthetic source and simulation epoch differ')


def _order(value, previous):
    stamp = value.rgb.stamp
    key = stamp.source_id, stamp.clock_id
    current = stamp.sequence, stamp.captured_us[0]
    if key in previous and (current[0] <= previous[key][0] or current[1] <= previous[key][1]):
        raise SyntheticDatasetError('synthetic sequence/time is not strictly increasing in its source epoch')
    return key, current


class SyntheticDatasetWriter:
    """새 출력 directory 하나를 소유한다. write 실패 뒤 계속하거나 기존 기록을 덮지 않는다."""

    def __init__(self, output, calibration, *, effects=DepthEffects(), scene=None):
        if calibration.kind != 'SYNTHETIC' or type(effects) is not DepthEffects:
            raise SyntheticDatasetError('synthetic dataset requires explicit calibration/effects')
        self.directory = Path(output)
        self.directory.mkdir(mode=0o700)
        (self.directory / 'frames').mkdir(mode=0o700)
        calibration = calibration_for_effects(calibration, effects)
        self.calibration, self.effects = calibration, effects
        self._count, self._keys, self._order = 0, set(), {}
        self._closed = self._failed = False
        provenance = None
        if scene is not None:
            fields = scene.fields
            provenance = dict(scene_sha256=scene.sha256, **{k: fields[k] for k in ('source_commit', 'source_path', 'source_sha256')})
        header = dict(version=1, scope='synthetic-rgb-right-first-surface', calibration=calibration.to_dict(),
                      calibration_id=calibration.sha256, effects=effects.fields, effects_sha256=effects.sha256,
                      scene_provenance=provenance, clock_qualification='SIMULATION_INSTANTANEOUS_NOT_OAK',
                      durability='clean-close-no-power-loss-guarantee')
        raw = canonical(header)
        if len(raw) > _HEADER_LIMIT:
            raise SyntheticDatasetError('synthetic header exceeds its bound')
        (self.directory / 'manifest.json').write_bytes(raw)
        self._index = (self.directory / 'snapshots.jsonl').open('xb')
        self._oracle = None
        self._index_hash = hashlib.sha256()

    def write(self, value, *, oracle=None):
        if self._closed or self._failed or self._count >= MAX_SNAPSHOTS:
            raise SyntheticDatasetError('synthetic writer is closed, failed or full')
        _coherent(value, self.calibration.sha256)
        if value.geometry_key in self._keys:
            raise SyntheticDatasetError('duplicate synthetic capture cannot be accumulated twice')
        if not value.rgb.stamp.source_id.endswith(':' + self.effects.sha256[:16]):
            raise SyntheticDatasetError('capture effects differ from the dataset profile')
        order_key, order_value = _order(value, self._order)
        row = dict(ordinal=self._count, calibration_id=value.calibration_id)
        try:
            for name, frame in (('rgb', value.rgb), ('right', value.depth)):
                relative = 'frames/' + f'{self._count:06d}-{name}.bin'
                with (self.directory / relative).open('xb') as target:
                    target.write(frame.payload)
                row[name] = dict(stamp=asdict(frame.stamp), width=frame.width, height=frame.height,
                    encoding=frame.encoding, exposure_us=frame.exposure_us, path=relative,
                    payload_length=len(frame.payload), sha256=frame.sha256)
            if oracle is not None:
                if (oracle.get('kind') != 'SYNTHETIC_ORACLE' or oracle.get('sequence') != value.rgb.stamp.sequence
                        or oracle.get('sim_time_us') != value.rgb.stamp.captured_us[0]
                        or value.rgb.stamp.clock_id != 'simulation:' + oracle.get('epoch', '')):
                    raise SyntheticDatasetError('oracle does not bind the same original capture')
                oracle_raw = canonical(oracle)
                if len(oracle_raw) > _HEADER_LIMIT:
                    raise SyntheticDatasetError('oracle snapshot exceeds its bound')
                if self._oracle is None:
                    self._oracle = (self.directory / 'oracle.jsonl').open('xb')
                self._oracle.write(oracle_raw)
                self._oracle.flush()
            raw = canonical(row)
            self._index.write(raw)
            self._index.flush()
            self._index_hash.update(raw)
            self._keys.add(value.geometry_key)
            self._order[order_key] = order_value
            self._count += 1
        except BaseException:
            self._failed = True
            raise

    def close(self, *, complete=True):
        if self._closed:
            return
        self._closed = True
        self._index.close()
        if self._oracle is not None:
            self._oracle.close()
        receipt = dict(version=1, complete=bool(complete and not self._failed), count=self._count,
                       index_sha256=self._index_hash.hexdigest())
        with (self.directory / 'result.json').open('xb') as output:
            output.write(canonical(receipt))

    def __enter__(self):
        return self

    def __exit__(self, error_type, error, traceback):
        self.close(complete=error_type is None)


class SyntheticDatasetReader:
    """원 simulation clock/seq를 유지한다. oracle 파일은 열거나 core에 넘기지 않는다."""

    def __init__(self, dataset, *, allow_incomplete=False):
        from ..perception.calibration import CalibrationProfile
        self.directory = Path(dataset)
        if (type(allow_incomplete) is not bool or not stat.S_ISDIR(self.directory.lstat().st_mode)
                or not stat.S_ISDIR((self.directory / 'frames').lstat().st_mode)):
            raise SyntheticDatasetError('synthetic dataset directories are invalid')
        self.manifest = _load(_read(self.directory / 'manifest.json', _HEADER_LIMIT))
        header = self.manifest
        if (set(header) != {'version', 'scope', 'calibration', 'calibration_id', 'effects', 'effects_sha256',
                           'scene_provenance', 'clock_qualification', 'durability'}
                or type(header['version']) is not int or header['version'] != 1
                or header['scope'] != 'synthetic-rgb-right-first-surface'
                or header['clock_qualification'] != 'SIMULATION_INSTANTANEOUS_NOT_OAK'
                or header['durability'] != 'clean-close-no-power-loss-guarantee'):
            raise SyntheticDatasetError('unsupported synthetic dataset profile')
        self.calibration = CalibrationProfile.from_dict(header['calibration'])
        if self.calibration.kind != 'SYNTHETIC' or self.calibration.sha256 != header['calibration_id']:
            raise SyntheticDatasetError('synthetic calibration hash differs')
        fields = dict(header['effects'])
        fields['hole_rects'] = tuple(tuple(v) for v in fields['hole_rects'])
        fields.pop('version')
        fields.pop('algorithm')
        self.effects = DepthEffects(**fields)
        if canonical(header['effects']) != canonical(self.effects.fields) or header['effects_sha256'] != self.effects.sha256:
            raise SyntheticDatasetError('synthetic effects profile differs')
        raw = _read(self.directory / 'snapshots.jsonl', _INDEX_LIMIT)
        try:
            self.receipt = _load(_read(self.directory / 'result.json', 4096))
        except FileNotFoundError:
            self.receipt = None
        self.complete = False
        if self.receipt is not None:
            receipt = self.receipt
            if (set(receipt) != {'version', 'complete', 'count', 'index_sha256'}
                    or type(receipt['version']) is not int or receipt['version'] != 1
                    or type(receipt['complete']) is not bool or type(receipt['count']) is not int
                    or not 0 <= receipt['count'] <= MAX_SNAPSHOTS):
                raise SyntheticDatasetError('synthetic completion receipt is invalid')
            if receipt['count'] != raw.count(b'\n') or receipt['index_sha256'] != hashlib.sha256(raw).hexdigest():
                raise SyntheticDatasetError('synthetic receipt/index hash or count differs')
            self.complete = receipt['complete']
        if not self.complete and not allow_incomplete:
            raise SyntheticDatasetError('synthetic dataset is incomplete')
        lines = raw.splitlines(keepends=True)
        if lines and not lines[-1].endswith(b'\n'):
            if self.complete or not allow_incomplete:
                raise SyntheticDatasetError('synthetic index has an incomplete tail')
            lines.pop()
        if len(lines) > MAX_SNAPSHOTS:
            raise SyntheticDatasetError('synthetic snapshot count exceeds its bound')
        self._rows = tuple(_load(line) for line in lines)

    def __iter__(self):
        keys, previous = set(), {}
        for ordinal, row in enumerate(self._rows):
            if (set(row) != {'ordinal', 'calibration_id', 'rgb', 'right'}
                    or type(row['ordinal']) is not int or row['ordinal'] != ordinal):
                raise SyntheticDatasetError('synthetic index order or keys differ')
            frames = []
            for name, width, height, encoding in (('rgb', 640, 360, 'NV12'), ('right', 640, 480, 'RAW16_LE')):
                fields = row[name]
                expected = 'frames/' + f'{ordinal:06d}-{name}.bin'
                size = width * height * 3 // 2 if name == 'rgb' else width * height * 2
                if (type(fields) is not dict or set(fields) != {'stamp', 'width', 'height', 'encoding', 'exposure_us', 'path', 'payload_length', 'sha256'}
                        or any(type(fields[k]) is not int for k in ('width', 'height', 'payload_length'))
                        or (fields['path'], fields['width'], fields['height'], fields['encoding'], fields['payload_length']) != (
                            expected, width, height, encoding, size)):
                    raise SyntheticDatasetError('synthetic payload descriptor differs from its fixed layout')
                payload = _read(self.directory / expected, size)
                if len(payload) != size or hashlib.sha256(payload).hexdigest() != fields['sha256']:
                    raise SyntheticDatasetError('synthetic payload length or hash differs')
                stamp = dict(fields['stamp'])
                if stamp.get('captured_us') is not None:
                    stamp['captured_us'] = tuple(stamp['captured_us'])
                try:
                    frames.append(ImageFrame(FrameStamp(**stamp), width, height, encoding, payload, fields['exposure_us']))
                except (TypeError, ValueError) as error:
                    raise SyntheticDatasetError('synthetic original frame stamp is invalid') from error
            value = PerceptionInput('SYNTHETIC', row['calibration_id'], frames[0], frames[1])
            _coherent(value, self.calibration.sha256)
            if value.geometry_key in keys or not value.rgb.stamp.source_id.endswith(':' + self.effects.sha256[:16]):
                raise SyntheticDatasetError('synthetic duplicate or effects profile mismatch')
            order_key, order_value = _order(value, previous)
            previous[order_key] = order_value
            keys.add(value.geometry_key)
            yield value
