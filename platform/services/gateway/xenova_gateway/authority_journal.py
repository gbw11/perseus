"""동일 Authority의 typed journal owner 하나. actor/STOP은 fsync lock을 기다리지 않는다.

원 제안·기한·CAS는 인계 뒤 불변이며 결과를 성공으로 추측하지 않는다. Scene과 operator는
같은 worker1/job1/result1을 공유하고 자신의 결과만 가져간다. 별도 권위 파일은 없다.
"""
from dataclasses import dataclass
import json
import queue
import threading


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')


@dataclass(frozen=True,slots=True)
class AuthorityProposal:
    key: str
    expected: bytes
    changes: bytes
    issued_us: int
    expires_us: int
    operation: str
    domain: str = 'operator'

    def __post_init__(self):
        if (self.domain!='operator' or type(self.key) is not str or len(self.key)>100
                or type(self.expected) is not bytes or type(self.changes) is not bytes
                or len(self.expected)>65536 or len(self.changes)>65536
                or not 0<=self.issued_us<self.expires_us<2**63
                or canonical(json.loads(self.expected))!=self.expected
                or canonical(json.loads(self.changes))!=self.changes):
            raise ValueError('bounded original Authority proposal required')


@dataclass(frozen=True,slots=True)
class AuthorityAck:
    proposal: AuthorityProposal
    completed_us: int


@dataclass(frozen=True,slots=True)
class JournalResult:
    proposal: object
    ack: object
    unknown: bool
    error: str | None


class AuthorityJournalWorker:
    def __init__(self,store,clock_us):
        self.store,self.clock_us=store,clock_us
        self._jobs=queue.Queue(maxsize=1);self._results=queue.Queue(maxsize=1)
        self._busy=threading.Event();self._stop=threading.Event()
        self.thread=threading.Thread(target=self._run,name='gateway-authority-journal',daemon=False)
        self.thread.start()

    @property
    def busy(self):return self._busy.is_set()

    def submit(self,proposal):
        from .scene_authority import SceneJournalProposal
        if (type(proposal) not in (AuthorityProposal,SceneJournalProposal) or self._stop.is_set()
                or self.busy or not self._results.empty()):raise ValueError('one unresolved Authority job')
        self._busy.set()
        try:self._jobs.put_nowait(proposal)
        except BaseException:self._busy.clear();raise

    def _execute(self,proposal):
        from .authority import AuthorityError
        from .scene_authority import SceneJournalProposal,execute_scene_journal
        if type(proposal) is SceneJournalProposal:
            return execute_scene_journal(self.store,proposal,clock_us=self.clock_us)
        with self.store._lock:
            now=self.clock_us()
            if (self.store.error or not proposal.issued_us<=now<proposal.expires_us
                    or canonical(self.store.value)!=proposal.expected):
                raise AuthorityError('original Authority proposal context/deadline changed')
            self.store.update(**json.loads(proposal.changes))
            return AuthorityAck(proposal,self.clock_us())

    def _run(self):
        from .authority import AuthorityError
        from .scene_authority import SceneJournalProposal
        while not self._stop.is_set() or not self._jobs.empty():
            try:proposal=self._jobs.get(timeout=.05)
            except queue.Empty:continue
            try:result=JournalResult(proposal,self._execute(proposal),False,None)
            except BaseException as error:
                if type(proposal) is SceneJournalProposal:
                    current=self.store.value.get('current_scene')
                    written=current is not None and canonical(current)==proposal.record
                else:
                    written=canonical(self.store.value)!=proposal.expected and all(
                        self.store.value.get(k)==v for k,v in json.loads(proposal.changes).items())
                result=JournalResult(proposal,None,bool(self.store.error or written or not isinstance(error,AuthorityError)),type(error).__name__)
            try:self._results.put_nowait(result)
            finally:self._busy.clear()  # original lock has unwound; result1 is published first

    def take(self,domain='scene'):
        # Only the actor removes results. Peek prevents one owner stealing another's ACK.
        with self._results.mutex:
            if not self._results.queue:return None
            if getattr(self._results.queue[0].proposal,'domain','scene')!=domain:return None
        try:return self._results.get_nowait()
        except queue.Empty:return None

    def close(self):
        self._stop.set();self.thread.join(1.)
        if self.thread.is_alive():raise RuntimeError('AUTHORITY_JOURNAL_OWNER_TERMINATION_UNCONFIRMED')
