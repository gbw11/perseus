"""반환 메시지용 합성 원본. 기본50us는 시험값이며 실제 전송 예산이 아니다."""

from slot_message_fixtures import chain, packet
from xenova_vehicle.runtime.slot_cleanup import CleanupTimeLimits, ReleaseMessage


def acquired():
    return chain()['ACQUIRED']


def cleanup_limits(budget=50):
    return CleanupTimeLimits(budget)


def release_fields(parent=None, *, completed=None, budget=50, sequence=1):
    parent = acquired() if parent is None else parent
    completed = parent.expires_us + 100 if completed is None else completed
    value = parent.fields
    value['payload']['op'] = 'RELEASE'
    value['sender_instance'] = parent.context.reader.worker_instance
    value['message_sequence'] = str(sequence)
    value['issued_j_us'] = str(completed)
    value['expires_j_us'] = str(completed + budget)
    return value


def make(parent=None, *, fields=None, budget=50, limits=None):
    parent = acquired() if parent is None else parent
    selected = cleanup_limits(budget) if limits is None else limits
    fields = release_fields(parent, budget=selected.transport_budget_us) if fields is None else fields
    return ReleaseMessage(packet(fields), parent, limits=selected)
