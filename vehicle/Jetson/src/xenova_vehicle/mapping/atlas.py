"""단일 compute owner의 유한 LIVE64/EVIDENCE64 block pool.

두 종류의 실제 block을 모두320 version/80MiB에 센다. lease는 본문을 복제하지
않는다. 원 관측/시계/SQL 내구성/사용권은 caller 책임이며 mark_clean은 실제 원
receipt를 검산한 caller 전용이다. public ref/delta는 본문을 pin하지 않는 metadata다.
caller는 ROI/codec peak를 workspace로 예약하고 lease 밖으로 bytes를 보관하지 않는다.
LIVE는 비영속 cache라 dirty32에 세지 않는다. EVIDENCE만 실제 저장 역할의
dirty32/checkpoint32/frozen128을 차지하며 LIVE도 합계 version/data에는 항상 센다.
LIVE의 cache clean은 내구성/FREE 자격이 아니다. 관련 EVIDENCE가 dirty/leased이면
logical key 전체 eviction도 금지한다. LIVE/EVIDENCE의 의미상 완전 대응은 caller 책임이다.
"""

from dataclasses import dataclass,field
from hashlib import sha256
from threading import get_ident
from queue import SimpleQueue,Empty
from weakref import ref as weakref

TILE_BYTES=64*64*64
MAX_DATA_BYTES=80*1024*1024
KINDS=frozenset(('LIVE64','EVIDENCE64'))
PURPOSE_CAPS={'READ':320,'CHECKPOINT':32,'FROZEN':128}


class AtlasError(ValueError):
    """원 ref/후보/lease 또는 완전한 tile 형식이 맞지 않는다."""


class AtlasResource(RuntimeError):
    """고정 pool 여유가 없다. 새 pool/dirty eviction으로 우회하지 않는다."""


def _key(value):
    if (type(value) is not tuple or len(value)!=2 or any(type(v) is not int or not -(1<<31)<=v<(1<<31) for v in value)):
        raise AtlasError('signed int32 tile key required')
    return value


def _kind(value):
    if type(value) is not str or value not in KINDS:raise AtlasError('LIVE64 or EVIDENCE64 required')
    return value


def _limit(value,maximum,*,zero=False):
    if type(value) is not int or not (0 if zero else 1)<=value<=maximum:raise AtlasError('bounded atlas capacity required')
    return value


@dataclass(frozen=True)
class AtlasRef:
    """로컬 block 식별자다. DB revision/관측 sequence/source ID가 아니다."""
    kind: str
    key: tuple
    serial: int
    sha256: str
    _owner: object=field(repr=False,compare=False)


@dataclass(frozen=True)
class AtlasUpdate:
    """원 expected block을 계승한 완전한 본문이다. 부분 patch는 받지 않는다."""
    kind: str
    key: tuple
    payload: bytes
    expected: AtlasRef | None


@dataclass(frozen=True)
class AtlasDelta:
    generation: int
    serial: int
    refs: tuple
    evicted_keys: tuple
    _owner: object=field(repr=False,compare=False)


@dataclass(frozen=True)
class AtlasLease:
    purpose: str
    refs: tuple
    serial: int
    _owner: object=field(repr=False,compare=False)


@dataclass(frozen=True)
class AtlasOwnerPin:
    """원 immutable owner의 생존 동안 본문을 계수한다. SQL/native 정리 진술이 아니다."""
    refs: tuple
    serial: int
    _owner: object=field(repr=False,compare=False)


@dataclass(frozen=True)
class _WeakOwner:
    handle: AtlasOwnerPin
    reference: object
    identity: int


@dataclass(frozen=True)
class _Block:
    ref: AtlasRef
    payload: bytes


@dataclass(frozen=True)
class _State:
    current: dict
    blocks: dict
    dirty: frozenset
    data_bytes: int


@dataclass(frozen=True)
class _Prepared:
    delta: AtlasDelta
    state: _State
    original_state: _State
    expected: tuple
    added_versions: int


class AtlasPool:
    """원 후보1개와 용도별 lease1개. accept는 작은 검산과 참조 교환만 한다.

    각 cap은 동시 최대 보장이 아니다. 같은 key의 LIVE/EVIDENCE도 두 version이다.
    reserved_workspace_bytes는 실제 동시 ROI/codec 배열의 예약이다. prepare 입력을
    만들기 전 ensure_capacity()를 호출하고 그 사이 다른 allocation을 하지 않아야 한다.
    예약된 작업공간 밖에서 생성한 입력 본문의 peak는 이 pool이 대신 보장하지 않는다.
    """
    def __init__(self,*,reserved_workspace_bytes,max_live_keys=128,max_versions=320,
                 max_data_bytes=MAX_DATA_BYTES,max_dirty=32,max_frozen=128):
        self.max_live_keys=_limit(max_live_keys,128);self.max_versions=_limit(max_versions,320)
        self.max_data_bytes=_limit(max_data_bytes,MAX_DATA_BYTES);self.max_dirty=_limit(max_dirty,32)
        self.max_frozen=_limit(max_frozen,128)
        self.reserved_workspace_bytes=_limit(reserved_workspace_bytes,self.max_data_bytes,zero=True)
        self._thread=get_ident();self._token=object();self._generation=self._serial=0
        self._state=_State({}, {},frozenset(),0);self._staged=self._retiring=None;self._leases={};self._pins={}
        self._owners={};self._owner_index={};self._owner_releases=SimpleQueue()

    def _owner(self):
        if get_ident()!=self._thread:raise RuntimeError('atlas belongs to its original compute owner')

    def _next(self):
        self._serial+=1;return self._serial

    def _block(self,ref):
        if type(ref) is not AtlasRef or ref._owner is not self._token:raise AtlasError('original atlas ref required')
        block=self._state.blocks.get(ref.serial)
        if block is None or block.ref is not ref:raise AtlasError('original block has retired')
        return block

    def current(self,kind,key):
        self._owner();return self._state.current.get((_kind(kind),_key(key)))

    @property
    def generation(self):
        self._owner();return self._generation

    def ensure_capacity(self,new_blocks):
        """본문 생성 전 old+new 실제 peak 검사. 계획된 eviction은 선차감하지 않는다."""
        self._owner();_limit(new_blocks,32,zero=True)
        blocks=self._charged_blocks(staged=True)
        if len(blocks)+new_blocks>self.max_versions:raise AtlasResource('VERSIONS')
        if sum(len(block.payload) for block in blocks.values())+new_blocks*TILE_BYTES+self.reserved_workspace_bytes>self.max_data_bytes:
            raise AtlasResource('DATA_BYTES')

    def _charged_blocks(self,*,staged=False):
        blocks={} if self._retiring is None else dict(self._retiring.blocks)
        blocks.update(self._state.blocks)
        if staged and self._staged is not None:blocks.update(self._staged.state.blocks)
        return blocks

    def drain_retired(self):
        """최종 채택 lock 밖의 compute 유지/prepare에서만 이전 state를 해제한다.

        배출 전에는 제거된 원 block도 version/data에 포함한다. 현재 ref/dirty/lease와
        generation은 바꾸지 않으며 SQL 완료나 native 정리 사실을 만들지 않는다.
        """
        self._owner();retired=self._retiring
        self._retiring=None
        return retired is not None

    def prepare(self,updates):
        """변경한 full tile만 준비한다. 원본+후보의 물리 peak도 고정 cap 안이다."""
        self._owner()
        if self._staged is not None:raise AtlasResource('ORIGINAL_DELTA_PENDING')
        self.drain_retired()
        if type(updates) is not tuple or not 1<=len(updates)<=32:raise AtlasError('bounded nonempty update batch required')
        seen=set();expected=[]
        for update in updates:
            if type(update) is not AtlasUpdate:raise AtlasError('exact full-tile update required')
            tag=(_kind(update.kind),_key(update.key))
            if tag in seen or type(update.payload) is not bytes or len(update.payload)!=TILE_BYTES:
                raise AtlasError('unique complete immutable tile bytes required')
            original=self._state.current.get(tag)
            if update.expected is not original:raise AtlasError('full replacement changed its original current ref')
            seen.add(tag);expected.append((tag,original))
        self.ensure_capacity(len(updates))
        current=dict(self._state.current);blocks=dict(self._state.blocks);dirty=set(self._state.dirty);refs=[]
        for update in updates:
            ref=AtlasRef(update.kind,update.key,self._next(),sha256(update.payload).hexdigest(),self._token)
            blocks[ref.serial]=_Block(ref,update.payload);refs.append(ref);current[(ref.kind,ref.key)]=ref
            if update.kind=='EVIDENCE64':dirty.add(ref.serial)
            old=update.expected
            if old is not None and not self._pins.get(old.serial):
                # 같은 영역의 complete 후계만 coalesce한다. dirty cache eviction과 다르다.
                blocks.pop(old.serial);dirty.discard(old.serial)
        active={ref.serial for ref in current.values()}
        for identity in tuple(blocks):
            if identity not in active and identity not in dirty and not self._pins.get(identity):blocks.pop(identity)
        evicted=[];keys={ref.key for ref in current.values()};protected={u.key for u in updates}
        if len(keys)>self.max_live_keys:
            candidates=sorted((max(ref.serial for ref in current.values() if ref.key==key),key) for key in keys-protected)
            for _,key in candidates:
                owned=tuple(block for block in blocks.values() if block.ref.key==key)
                if any(block.ref.serial in dirty or self._pins.get(block.ref.serial) for block in owned):continue
                for tag,ref in tuple(current.items()):
                    if ref.key==key:current.pop(tag)
                for block in owned:blocks.pop(block.ref.serial)
                evicted.append(key);keys.remove(key)
                if len(keys)<=self.max_live_keys:break
            if len(keys)>self.max_live_keys:raise AtlasResource('NO_CLEAN_UNLEASED_KEY')
        if len(dirty)>self.max_dirty:raise AtlasResource('DIRTY_VERSIONS')
        delta=AtlasDelta(self._generation,self._next(),tuple(refs),tuple(evicted),self._token)
        state=_State(current,blocks,frozenset(dirty),sum(len(block.payload) for block in blocks.values()))
        self._staged=_Prepared(delta,state,self._state,tuple(expected),len(updates));return delta

    def accept(self,delta):
        """바깥 current/source 검산 뒤 원 generation/ref만 비교하고 참조를 교환한다."""
        self.validate(delta);prepared=self._staged
        self._retiring=prepared.original_state
        self._state=prepared.state;self._staged=None;self._generation+=1

    def validate(self,delta):
        """원 후보의 작은 final gate다. 상태/lease/본문을 바꾸지 않는다."""
        self._owner();prepared=self._staged
        if (prepared is None or self._retiring is not None or delta is not prepared.delta or delta.generation!=self._generation
                or any(self._state.current.get(tag) is not ref for tag,ref in prepared.expected)):
            raise AtlasError('original atlas delta was fenced')

    def _query_state(self,delta):
        if delta is None:return self._state
        self.validate(delta);return self._staged.state

    def current_refs(self,kind=None,*,delta=None):
        """최대256개 current ref다. body를 공개하거나 lease를 생성하지 않는다."""
        self._owner()
        if kind is not None:_kind(kind)
        state=self._query_state(delta)
        return tuple(sorted((ref for ref in state.current.values() if kind is None or ref.kind==kind),
            key=lambda ref:(ref.key,ref.kind)))

    def retained_refs(self,kind=None,*,delta=None):
        """current와 lease/cache가 보존하는 최대320개 소비 가능한 원 ref다.

        해제 대기 state만 가진 block은 물리 계수에만 남고 새 lease 대상은 아니다.
        """
        self._owner()
        if kind is not None:_kind(kind)
        state=self._query_state(delta)
        return tuple(block.ref for _,block in sorted(state.blocks.items()) if kind is None or block.ref.kind==kind)

    def dirty_refs(self):
        self._owner()
        return tuple(self._state.blocks[identity].ref for identity in sorted(self._state.dirty))

    def coalesce_unleased(self):
        """lease가 끝난 원 full predecessor만 retire한다. current dirty는 clean으로 바꾸지 않는다."""
        self._owner()
        if self._staged is not None:raise AtlasResource('ORIGINAL_DELTA_PENDING')
        current_ids={ref.serial for ref in self._state.current.values()}
        removed=tuple(block.ref for identity,block in self._state.blocks.items()
            if identity not in current_ids and not self._pins.get(identity)
            and (block.ref.kind,block.ref.key) in self._state.current)
        if removed:
            identities={ref.serial for ref in removed}
            blocks={identity:block for identity,block in self._state.blocks.items() if identity not in identities}
            self._state=_State(self._state.current,blocks,self._state.dirty.difference(identities),
                sum(len(block.payload) for block in blocks.values()));self._generation+=1
        return removed

    def cancel(self,delta):
        self._owner()
        if self._staged is None or delta is not self._staged.delta:raise AtlasError('original atlas delta required')
        self._staged=None

    def lease(self,refs,*,purpose):
        self._owner()
        if purpose not in PURPOSE_CAPS or purpose in self._leases:raise AtlasResource('LEASE_PURPOSE_BUSY')
        cap=self.max_frozen if purpose=='FROZEN' else PURPOSE_CAPS[purpose]
        if type(refs) is not tuple or not 1<=len(refs)<=cap or len({r.serial for r in refs if type(r) is AtlasRef})!=len(refs):
            raise AtlasError('unique bounded original lease refs required')
        for ref in refs:self._block(ref)
        if purpose!='READ' and any(ref.kind!='EVIDENCE64' for ref in refs):
            raise AtlasError('checkpoint/frozen leases require original EVIDENCE64 refs')
        lease=AtlasLease(purpose,refs,self._next(),self._token);self._leases[purpose]=lease
        for ref in refs:self._pins[ref.serial]=self._pins.get(ref.serial,0)+1
        self._generation+=1;return lease

    def payload(self,ref,*,lease):
        """lease 중에만 원 bytes를 빌린다. 밖에 보관하거나 새 관측으로 재포장하지 않는다."""
        self._owner()
        if (type(lease) is not AtlasLease or self._leases.get(lease.purpose) is not lease
                or not any(original is ref for original in lease.refs)):raise AtlasError('actual original read lease required')
        return self._block(ref).payload

    def release(self,lease):
        self._owner()
        if type(lease) is not AtlasLease or self._leases.get(lease.purpose) is not lease:raise AtlasError('original live lease required')
        for ref in lease.refs:
            remaining=self._pins[ref.serial]-1
            if remaining:self._pins[ref.serial]=remaining
            else:self._pins.pop(ref.serial)
        self._leases.pop(lease.purpose);self._generation+=1

    def retain_owner(self,owner,refs):
        """최대16 weak owner/각128 원 refs. 같은 owner+refs는 동일 원 pin을 반환한다.

        owner는 weakref를 지원하는 immutable 원 request/SourceComplete다. callback은
        작은 token만 넣는다. 등록 owner가16개 이하이고 drain 전에는 등록을 제거하지
        않으므로 callback queue도16개 이하로 제한된다. compute만 실제 pin을 해제한다.
        """
        self._owner()
        if type(refs) is not tuple or len(refs)>128:raise AtlasError('bounded original owner refs required')
        for original in refs:self._block(original)
        canonical=tuple(sorted(refs,key=lambda original:original.serial))
        if len({original.serial for original in canonical})!=len(canonical):raise AtlasError('unique original owner refs required')
        if not canonical:return None
        identity=id(owner);known=self._owner_index.get(identity)
        if known is not None:
            previous=self._owners[known]
            if previous.reference() is owner:
                if len(canonical)!=len(previous.handle.refs) or any(a is not b for a,b in zip(canonical,previous.handle.refs)):
                    raise AtlasError('same original owner changed its refs')
                return previous.handle
        if len(self._owners)>=16:raise AtlasResource('OWNER_PINS')
        serial=self._next();queue=self._owner_releases
        try:reference=weakref(owner,lambda _,token=serial,target=queue:target.put(token))
        except TypeError as error:raise AtlasError('original owner must support weak references') from error
        handle=AtlasOwnerPin(canonical,serial,self._token)
        self._owners[serial]=_WeakOwner(handle,reference,identity);self._owner_index[identity]=serial
        for original in canonical:self._pins[original.serial]=self._pins.get(original.serial,0)+1
        self._generation+=1;return handle

    def preflight_owner(self,owner,refs,*,delta=None):
        """단일 owner가 accept 직전 pin의 통상 실패를 검산한다. 등록/해제는 하지 않는다."""
        self._owner();state=self._query_state(delta)
        if type(refs) is not tuple or len(refs)>128:raise AtlasError('bounded original owner refs required')
        for original in refs:
            block=state.blocks.get(original.serial) if type(original) is AtlasRef else None
            if block is None or block.ref is not original or original._owner is not self._token:
                raise AtlasError('original prospective owner refs required')
        canonical=tuple(sorted(refs,key=lambda original:original.serial))
        if len({original.serial for original in canonical})!=len(canonical):raise AtlasError('unique original owner refs required')
        if not canonical:return
        known=self._owner_index.get(id(owner))
        if known is not None and self._owners[known].reference() is owner:
            previous=self._owners[known].handle.refs
            if len(canonical)!=len(previous) or any(a is not b for a,b in zip(canonical,previous)):
                raise AtlasError('same original owner changed its refs')
            return
        if len(self._owners)>=16:raise AtlasResource('OWNER_PINS')
        try:weakref(owner)
        except TypeError as error:raise AtlasError('original owner must support weak references') from error

    def drain_owner_releases(self):
        """실제 GC 통지를 compute에서만 소비한다. SQL 완료/새 clean을 만들지 않는다."""
        self._owner();released=[]
        while True:
            try:serial=self._owner_releases.get_nowait()
            except Empty:break
            previous=self._owners.get(serial)
            if previous is None or previous.reference() is not None:continue
            self._owners.pop(serial)
            if self._owner_index.get(previous.identity)==serial:self._owner_index.pop(previous.identity)
            for original in previous.handle.refs:
                remaining=self._pins[original.serial]-1
                if remaining:self._pins[original.serial]=remaining
                else:self._pins.pop(original.serial)
            released.append(previous.handle)
        if released:self._generation+=1
        return tuple(released)

    def mark_clean(self,refs):
        """검산한 원 receipt의 refs만 clean 처리한다. 최신 current를 추정하지 않는다."""
        self._owner()
        if type(refs) is not tuple or not 1<=len(refs)<=320:raise AtlasError('bounded original committed refs required')
        for ref in refs:self._block(ref)
        if any(ref.kind!='EVIDENCE64' for ref in refs):raise AtlasError('only original EVIDENCE64 receipts can mark clean')
        dirty=self._state.dirty.difference(ref.serial for ref in refs)
        if dirty!=self._state.dirty:
            self._state=_State(self._state.current,self._state.blocks,dirty,self._state.data_bytes);self._generation+=1

    def evict_clean(self,keys):
        """미사용 clean/unleased logical key만 제거한다. dirty/HWM/source reset은 없다."""
        self._owner()
        if self._staged is not None:raise AtlasResource('ORIGINAL_DELTA_PENDING')
        if type(keys) is not tuple or not 1<=len(keys)<=128:raise AtlasError('bounded unique eviction keys required')
        for key in keys:_key(key)
        if len(set(keys))!=len(keys):raise AtlasError('unique eviction keys required')
        selected=tuple(block for block in self._state.blocks.values() if block.ref.key in keys)
        if not selected or any(not any(block.ref.key==key for block in selected) for key in keys):raise AtlasError('original cached keys required')
        if any(block.ref.serial in self._state.dirty or self._pins.get(block.ref.serial) for block in selected):raise AtlasResource('DIRTY_OR_LEASED_KEY')
        remove={block.ref.serial for block in selected};current={tag:ref for tag,ref in self._state.current.items() if ref.serial not in remove}
        blocks={identity:block for identity,block in self._state.blocks.items() if identity not in remove}
        self._state=_State(current,blocks,self._state.dirty,sum(len(block.payload) for block in blocks.values()))
        self._generation+=1;return tuple(block.ref for block in selected)

    def stats(self):
        self._owner();blocks=self._charged_blocks();all_blocks=self._charged_blocks(staged=True)
        staged=len(all_blocks)-len(blocks);data_bytes=sum(len(block.payload) for block in blocks.values())
        retired=set(blocks).difference(self._state.blocks)
        return dict(live_keys=len({ref.key for ref in self._state.current.values()}),versions=len(blocks),
            live_versions=sum(block.ref.kind=='LIVE64' for block in blocks.values()),
            evidence_versions=sum(block.ref.kind=='EVIDENCE64' for block in blocks.values()),
            staged_versions=staged,data_bytes=data_bytes,staged_bytes=staged*TILE_BYTES,
            retired_versions=len(retired),retired_bytes=sum(len(blocks[identity].payload) for identity in retired),
            retiring_state=self._retiring is not None,
            reserved_workspace_bytes=self.reserved_workspace_bytes,
            charged_bytes=data_bytes+staged*TILE_BYTES+self.reserved_workspace_bytes,
            dirty_versions=len(self._state.dirty),dirty_evidence_versions=len(self._state.dirty),
            frozen_refs=0 if 'FROZEN' not in self._leases else len(self._leases['FROZEN'].refs),
            owner_pins=len(self._owners),owner_pin_refs=sum(len(item.handle.refs) for item in self._owners.values()),
            owner_pinned_versions=len({original.serial for item in self._owners.values() for original in item.handle.refs}),
            active_leases=len(self._leases),generation=self._generation)
