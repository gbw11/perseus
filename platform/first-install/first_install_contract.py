"""공통 SD의 MBR·용량 계약. 호스트 계획과 보드 확장은 같은 바이트 규칙을 쓴다."""
import struct
import os

SCHEMA = 1
MIB = 1024 ** 2
GIB = 1024 ** 3
SECTOR = 512
FEATURES = {'schema': SCHEMA, 'layout': 'dos-boot1-root2-data3-v1',
            'growth': 'actual-device-last-ext4-v1', 'identity': 'systemd-machine-id-guest-seed-v1'}


def align(value, unit=4 * MIB):
    return (value + unit - 1) // unit * unit


def layout(mbr, image_bytes=None, *, with_data=False):
    """확장 파티션/GPT는 받지 않는다. 기존 1·2번 항목과 DOS 식별자를 보존한다."""
    if len(mbr) != SECTOR or mbr[510:] != b'\x55\xaa':
        raise ValueError('invalid DOS MBR signature')
    entries = []
    for index in range(4):
        raw = mbr[446 + index * 16:462 + index * 16]
        flag, kind = raw[0], raw[4]
        start, count = struct.unpack_from('<II', raw, 8)
        if flag not in (0, 128):
            raise ValueError('invalid MBR boot flag')
        if kind or start or count:
            if not kind or not start or not count:
                raise ValueError('incomplete partition entry')
            entries.append({'number': index + 1, 'start': start, 'size': count, 'type': kind})
    expected = [1, 2, 3] if with_data else [1, 2]
    if [p['number'] for p in entries] != expected:
        raise ValueError('expected only primary boot1/root2' + ('/data3' if with_data else ''))
    if entries[0]['type'] not in (0x0B, 0x0C, 0x0E) or any(p['type'] != 0x83 for p in entries[1:]):
        raise ValueError('expected FAT boot and Linux root/data partitions')
    last = 1
    for p in entries:
        if p['start'] < last:
            raise ValueError('overlapping or unordered partitions')
        last = p['start'] + p['size']
    if image_bytes is not None and last * SECTOR > image_bytes:
        raise ValueError('partition extends past the image/device')
    disk_id = struct.unpack_from('<I', mbr, 440)[0]
    if not disk_id:
        raise ValueError('DOS disk identifier must be nonzero for PARTUUID boot')
    return {'schema': SCHEMA, 'disk_id': f'{disk_id:08x}', 'sector_size': SECTOR, 'partitions': entries}


def growth(current, device_bytes):
    """32/64GB를 구별하지 않고 실제 크기까지 마지막 데이터 영역만 확장한다. 축소 금지."""
    parts = current['partitions']
    if (current['sector_size'] != SECTOR or len(parts) != 3
            or [p['number'] for p in parts] != [1, 2, 3] or parts[-1]['type'] != 0x83):
        raise ValueError('unsupported growth layout')
    if type(device_bytes) is not int or device_bytes % SECTOR or not 0 < device_bytes <= 2 ** 32 * SECTOR:
        raise ValueError('expected 512-byte-sector DOS device, at most 2 TiB')
    data = parts[-1]
    size = device_bytes // SECTOR - data['start']
    if size < data['size'] or size <= 0 or size > 0xFFFFFFFF:
        raise ValueError('device is too small; partitions will never be shrunk')
    return {'start': data['start'], 'size': size, 'grow_partition': size > data['size'],
            'filesystem_max_bytes': size * SECTOR}


def capacity(platform_bytes, guests, *, template_policy='full', card_bytes=None, reserve_bytes=2 * GIB):
    """희소 파일 점유량 대신 VM의 최대 논리 용량을 모두 예약한다. MiB/GiB는 2진 단위다."""
    if template_policy != 'full' or not guests:
        raise ValueError('invalid template policy or missing guests')
    for g in guests:
        if not 4 * GIB <= g['logical_bytes'] <= 32 * GIB or not 0 <= g['allocated_bytes'] <= g['logical_bytes']:
            raise ValueError('invalid guest disk accounting')
    vm_bytes = sum(g['logical_bytes'] for g in guests)
    template_bytes = vm_bytes if template_policy == 'full' else 0
    boot_catalog = sum(g['boot_bytes'] for g in guests) * 2
    payload = vm_bytes + template_bytes + boot_catalog
    metadata = align((payload + 19) // 20 + 256 * MIB)  # 실측치가 아닌 보수적 5% + 고정 메타데이터 여유.
    data_bytes = align(payload + metadata + reserve_bytes)
    start = align(platform_bytes)
    image_bytes = start + data_bytes
    # source.raw·데이터 영역·게스트 준비 사본·최종 출력이 동시에 존재하는 상한.
    scratch_bytes = sum(g.get('preparation_scratch_bytes', 0) for g in guests)
    result = {'template_policy': template_policy, 'platform_bytes': platform_bytes,
              'data_start_bytes': start, 'data_bytes': data_bytes, 'image_bytes': image_bytes,
              'minimum_card_bytes': image_bytes, 'vm_max_bytes': vm_bytes, 'retained_template_root_bytes': template_bytes,
              'boot_catalog_bytes': boot_catalog, 'filesystem_metadata_allowance_bytes': metadata,
              'operating_reserve_bytes': reserve_bytes,
              'initial_vm_allocated_bytes': sum(g['allocated_bytes'] for g in guests),
              'vm_growth_budget_bytes': vm_bytes - sum(g['allocated_bytes'] for g in guests),
              'wsl_scratch_upper_bound_bytes': 2 * image_bytes + platform_bytes + scratch_bytes,
              'card_bytes': card_bytes}
    if card_bytes is not None:
        if type(card_bytes) is not int or card_bytes < image_bytes:
            raise ValueError(f'insufficient card bytes: need {image_bytes}, got {card_bytes}; no disk sizes were reduced')
    return result


def uuid4():
    value = bytearray(os.urandom(16))
    value[6], value[8] = (value[6] & 15) | 64, (value[8] & 63) | 128
    text = value.hex()
    return f'{text[:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:]}'
