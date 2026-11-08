"""Scene이 보유하는 원 atlas 참조 carrier. 본문이나 scene/내구 사용권은 보관하지 않는다.

MapperAtlas만 원 CP/full LOAD를 검산해 생성한다. 독립 weak-owner pin은 원 request의
copy32 pin을 넓히지 않으며 carrier가 실제로 살아 있는 동안 full128을 pool에서 계수한다.
"""

from dataclasses import dataclass,field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..mapping.atlas import AtlasOwnerPin,AtlasRef
    from ..mapping.map_lattice import MapLattice
    from ..storage.contracts import StorageJob


@dataclass(frozen=True,slots=True,weakref_slot=True,init=False)
class SceneAtlasArchive:
    map_epoch: str
    lattice: 'MapLattice'
    checkpoint_job: 'StorageJob'
    checkpoint_sha256: str
    checkpoint_digest: str
    manifest_sha256: str
    checkpoint_ref: bytes | None
    tile_references: tuple[tuple[int,int,int,str],...]
    pool_refs: tuple['AtlasRef',...]
    pin: 'AtlasOwnerPin'=field(repr=False,compare=False)
    _owner: object=field(repr=False,compare=False)

    def __new__(cls):raise TypeError('SceneAtlasArchive requires an actual MapperAtlas producer')

    @classmethod
    def _prepared(cls,*,map_epoch,lattice,checkpoint_job,checkpoint_sha256,checkpoint_digest,
                  manifest_sha256,checkpoint_ref,tile_references,pool_refs,owner):
        result=object.__new__(cls)
        for name,value in dict(map_epoch=map_epoch,lattice=lattice,checkpoint_job=checkpoint_job,
                checkpoint_sha256=checkpoint_sha256,checkpoint_digest=checkpoint_digest,
                manifest_sha256=manifest_sha256,checkpoint_ref=checkpoint_ref,tile_references=tile_references,
                pool_refs=pool_refs,pin=None,_owner=owner).items():object.__setattr__(result,name,value)
        return result
