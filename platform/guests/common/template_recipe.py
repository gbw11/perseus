"""Linux 개발 템플릿의 수정 가능한 recipe·파일 overlay와 준비 출처를 관리한다.

게시된 root.raw를 편집하지 않는다. 고정 OS 원본의 작업 사본에만 적용하며
VM 신원·SSH gate·배포판/커널 정보는 공통 준비 코드가 소유한다.
"""
import hashlib
import json
from pathlib import Path
import re

from prepare_rootfs import inside
import guest_agent_template
import guest_display_template

ROOT = Path(__file__).resolve().parents[3]
VERSION = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9.-]+)?')
RESERVED = ('etc/xenova/', 'usr/lib/xenova/', 'etc/ssh/', 'etc/systemd/',
            'usr/lib/systemd/', 'etc/machine-id', 'etc/shadow', 'etc/os-release',
            'usr/lib/os-release', 'boot/', 'lib/modules/', 'usr/lib/modules/')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_info(recipe, source_id):
    """recipe와 overlay를 검사하고 실제 파일·공유 준비 소스의 SHA-256을 고정한다."""
    recipe = Path(recipe)
    if not recipe.is_absolute() or recipe.resolve() != recipe or recipe.is_symlink():
        raise ValueError('template recipe requires a canonical regular path')
    meta = json.loads(recipe.read_text())
    required = {'schema', 'source_id', 'version', 'files'}
    if (not required.issubset(meta) or set(meta) - required - {'guest_agent', 'guest_display'} or meta['schema'] != 1
            or meta['source_id'] != source_id or not isinstance(meta['version'], str) or not VERSION.fullmatch(meta['version'])
            or not isinstance(meta['files'], list)):
        raise ValueError('invalid template recipe/source/version')
    if 'guest_agent' in meta and meta['guest_agent'] != {'packages': guest_agent_template.PACKAGES}:
        raise ValueError('unsupported Linux guest Agent package policy')
    if 'guest_display' in meta:
        guest_display_template.validate_policy(meta['guest_display'])
    sources = {'recipe.json': {'sha256': sha(recipe), 'bytes': recipe.stat().st_size}}
    targets = set()
    for entry in meta['files']:
        if not isinstance(entry, dict) or set(entry) != {'source', 'target', 'mode'}:
            raise ValueError('invalid template overlay entry')
        src, dst = Path(entry['source']), Path(entry['target'])
        if (src.is_absolute() or dst.is_absolute() or '..' in src.parts or '..' in dst.parts
                or not src.parts or not dst.parts or dst.parts[0] in ('dev', 'proc', 'sys', 'run')
                or str(dst) in targets or any(str(dst) == v.rstrip('/') or str(dst).startswith(v) for v in RESERVED)
                or type(entry['mode']) is not int or entry['mode'] not in (0o644, 0o755)):
            raise ValueError('unsafe/reserved template overlay path or mode')
        asset = recipe.parent / src
        if not asset.is_file() or asset.is_symlink() or asset.resolve() != asset:
            raise ValueError('template overlay source must be regular without parent links')
        targets.add(str(dst))
        sources[str(src)] = {'sha256': sha(asset), 'bytes': asset.stat().st_size}
    common = [ROOT / 'platform/scripts/prepare-xenova-guests.py',
              ROOT / 'platform/guests/common/prepare_rootfs.py',
              ROOT / 'platform/guests/common/guest_identity.py',
              ROOT / 'platform/guests/common/prepare_instance.py',
              ROOT / 'platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_guest_profile.py', Path(__file__).resolve()]
    if 'guest_agent' in meta:
        common += [Path(guest_agent_template.__file__).resolve(), *guest_agent_template.source_files()]
    if 'guest_display' in meta:
        common += [Path(guest_display_template.__file__).resolve()]
    preparation = {'platform': 'xenova', 'recipe': meta, 'recipe_files': sources,
                   'preparer_files': {str(p.relative_to(ROOT)): sha(p) for p in common}}
    preparation['source_sha256'] = hashlib.sha256(
        json.dumps(preparation, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return preparation


def apply(root, recipe, source_id):
    """검증한 overlay를 새 OS 작업 사본에만 쓴다. 외부 부모 링크를 따라 쓰지 않는다."""
    preparation = source_info(recipe, source_id)
    for entry in preparation['recipe']['files']:
        target = inside(root, entry['target'], follow_final=False)
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError('template overlay target is linked/non-regular')
        target.write_bytes((Path(recipe).parent / entry['source']).read_bytes())
        target.chmod(entry['mode'])
    return preparation
