"""compute의 원 발행 후보를 M-main에 넘기는 최신 두 참조 포트.

원 bytes 검산은 보고서 생성 때 한다. 포트는 기존 adoption lock만 공유하며 actor·
시계·thread를 소유하지 않는다. 채택은 송신/종단 승인과 다르며 만료를 갱신하지 않는다.
"""

from dataclasses import dataclass
from hashlib import sha256

from .mapper_mapping import MappingContextPort
from ..storage.contracts import StorageContext, digest, uint


_CAPS = {'WORLD_CANDIDATE': 32768, 'SURVEY_SUMMARY': 512}


@dataclass(frozen=True, slots=True)
class PublicationReport:
    """계산 단계에서 고정한 원 문맥·bytes·절대 기한. constructor는 현재 권한을 발급하지 않는다."""
    context: StorageContext
    kind: str
    raw: bytes
    issued_us: int
    expires_us: int
    publication_sha256: str
    grant_sha256: str | None = None

    def __post_init__(self):
        if type(self.context) is not StorageContext or type(self.kind) is not str or self.kind not in _CAPS:
            raise ValueError('actual original publication context and supported report kind required')
        if type(self.raw) is not bytes or not 0 < len(self.raw) <= _CAPS[self.kind]:
            raise ValueError('immutable bounded original publication bytes required')
        uint(self.issued_us, positive=True); uint(self.expires_us, positive=True)
        if not self.issued_us < self.expires_us <= self.issued_us+5_000_000:
            raise ValueError('original publication deadline already ended')
        digest(self.publication_sha256)
        if sha256(self.context.source_binding).hexdigest() != self.publication_sha256:
            raise ValueError('publication SHA differs from original context bytes')
        if self.grant_sha256 is not None: digest(self.grant_sha256)
        if self.kind == 'WORLD_CANDIDATE' and self.grant_sha256 is not None:
            raise ValueError('World candidate does not carry a survey grant')
        if self.kind == 'SURVEY_SUMMARY' and self.grant_sha256 is None:
            raise ValueError('survey summary must retain its original grant SHA')


class MapperPublicationPort:
    """World1/summary1. 큰 hash/encode나 자체 clock 없이 같은 짧은 adoption lock을 쓴다."""
    def __init__(self, context_port):
        if type(context_port) is not MappingContextPort:
            raise ValueError('actual shared mapping context port required')
        self._context_port = context_port
        self._slots = {kind: None for kind in _CAPS}

    @property
    def context_port(self):
        return self._context_port

    @property
    def adoption_lock(self):
        return self.context_port.adoption_lock

    def offer(self, report):
        """같은 원 context일 때만 참조를 바꾼다. 최종 compute guard 안에서 재진입 가능하다."""
        if type(report) is not PublicationReport:
            raise ValueError('actual immutable publication report required')
        with self.adoption_lock:
            if self.context_port.read() is not report.context: return False
            self._slots[report.kind] = report
            return True

    def peek(self, kind, now_us):
        """실제 main 시각에 원 문맥/기한을 재확인한다. 실패 후보는 재발급 없이 버린다."""
        if type(kind) is not str or kind not in _CAPS: raise ValueError('unsupported publication kind')
        uint(now_us)
        with self.adoption_lock:
            report = self._slots[kind]
            if report is not None and (self.context_port.read() is not report.context
                    or not report.issued_us <= now_us < report.expires_us):
                self._slots[kind] = None
                return None
            return report

    def acknowledge(self, report):
        """link가 원 packet을 수락한 뒤에만 호출한다. 과거 ACK가 새 후보를 비우지 않는다."""
        if type(report) is not PublicationReport: raise ValueError('actual original publication report required')
        with self.adoption_lock:
            if self._slots[report.kind] is not report: return False
            self._slots[report.kind] = None
            return True
