"""장애 판정 (S15P31E110-124).

입력은 한 주기의 관측(플랫폼 의도 + Xen 상태), 생존 신호 표(agent.heartbeat·app.heartbeat), 관측 경로 상태(필터별 구독 승인)이고
출력은 장애 한 건당 정확히 하나의 Judgment 다.

판정은 VM 마다 세 가지 중 하나다.
- FAILED(reason): 재시작이 필요하다.
- OK: 건강하거나(생존 신호 신선) 복구 대상이 아니다(의도 stopped·unknown, 보관본). 열린 장애(episode)를 닫는다.
- UNKNOWN: 아직 모른다. 유예 중(새 도메인의 첫 신호 전, 도메인 부재 유예), 구독 경로가 안 열렸거나 열린 지 한 창이 안 됨, paused·stopping.
  열린 episode 는 그대로 둔다. 그래서 복구 재시작 중(도메인 사라짐 → 새 domid → 첫 신호)에는 같은 episode 가 이어진다.

규칙
- 대상: lifecycle REGISTERED, 적용 가능한 의도가 running 인 VM 만. 사용자가 끈 VM(stopped)은 절대 대상이 아니다.
- DOMAIN_CRASHED: xl 상태 crashed 면 즉시.
- DOMAIN_ABSENT: 도메인이 없는 채로 absent_grace_s 가 지나면. 플랫폼 start·복구가 진행 중일 수 있어 유예를 둔다.
- HEARTBEAT_LOST (Linux): 실행 중인데 agent.heartbeat 가 heartbeat_lost_s 동안 없으면. 새 domid 뒤 첫 신호를 받기 전까지는
  boot_grace_s 동안 판정하지 않고, 그 부팅 이전의 신호는 세지 않는다. 첫 신호를 받은 뒤에는 유예 없이 창(window)으로만 본다.
- APP_HEARTBEAT_LOST (Zephyr, 시나리오 D8): app.heartbeat 로 같은 규칙(app_heartbeat_lost_s, zephyr_boot_grace_s).
  대상은 템플릿 capability 로 app.heartbeat 를 보낸다고 선언된 VM(app_heartbeat_expected), 또는 한 번이라도 신호를 받아 armed 된 VM 이다.
  armed 는 dom0(ArmingStore)에 남겨 Worker 재시작 뒤에도 유지한다. 앱이 없는 starter 펌웨어는 둘 다 아니라 Xen 상태로만 본다.
- 관측 경로: 필터별 구독이 승인되지 않았거나(연결만으로는 부족, ACL 거부 포함) 승인된 지 한 창이 안 됐으면 그 신호의 손실은 UNKNOWN.
  브로커·ACL 문제는 VM 장애가 아니다. crashed·absent 는 Xen 관측이라 무관하다. 신호가 신선하면 경로 상태와 무관하게 OK 다.
- 전달 근거는 두 겹이다. 채널(승인 뒤 어느 VM 이든 메시지가 옴)은 브로커가 그 채널을 지금 전달한다는 공통 근거일 뿐이고,
  대상 VM 의 손실 판정 자격은 그 VM 의 수신 이력(ArmingStore, Linux agent_delivered·Zephyr armed)이다. 다른 VM 의 수신으로
  대상 VM 을 판정하지 않는다. 이력이 없는 VM(계정·ACL 오류·배포 누락·첫 부팅 정체를 구별할 수 없음)은 UNKNOWN 이라 첫 부팅부터
  멈춘 VM 은 자동 복구하지 않는다. 이력은 현재 신호가 아니다: 이전 부팅의 신호는 여전히 세지 않고 boot grace 뒤에 판정한다.
  한 번 받은 뒤 그 VM 의 ACL 만 바뀌는 경우는 생존 신호만으로 구별할 수 없다(episode 당 시도 상한으로 묶인다).
- 채널 증거는 구독 승인 뒤 첫 수신으로 한 번 세우고 구독이 끊길 때까지 유지하므로, 나중에 브로커·브리지·ACL 같은 공통 경로가
  막혀 모든 VM 이 함께 조용해지는 것은 알지 못한다. 그래서 손실을 판정하기 직전에 같은 채널의 동료 발행자(대상이 아닌 등록 VM 중
  의도 running·Xen running 이고 지금 실행에서 그 채널 메시지를 받은 적이 있는 VM)를 본다. 동료 자격은 채널 전달 증거라 기능 건강과
  다르다: 주행 VM 은 앱 하나만 보내도 동료다(그 VM 자체의 건강은 두 앱이 모두 있어야 한다). 하나라도 신선하면 채널이 지금 전달하므로 판정한다.
  동료가 모두 조용하면 공통 경로를 의심해 보류한다(UNKNOWN). 동료가 없으면(단독 발행자) 둘을 구별할 수 없어 기본은 보류이고,
  채널별 단독 발행자 정책(agent_sole_publisher·app_sole_publisher)이 trust 일 때만 판정한다. crashed·absent 는 Xen 관측이라 이 규칙과 무관하다.
- HEALTHY 는 Xen 실행 + 생존 신호가 신선하다는 뜻이다. CPU·메모리·앱 기능까지 정상이라는 뜻이 아니다.
- 판정을 내기 직전에 confirm(플랫폼 get)으로 의도와 실행 상태를 다시 읽고 그 관측으로 다시 판정한다. 캐시된 의도가 오래됐거나
  관측 사이에 VM 이 시작됐으면 판정하지 않고, 건강 스냅샷도 재확인한 관측으로 낸다.
- 장애 한 건(episode)에 Judgment 는 하나다. 사유가 바뀌어도(HEARTBEAT_LOST → DOMAIN_ABSENT) 다시 내지 않고 건강 스냅샷에만 반영한다.
  복구 정책(125)은 episode 가 닫힐 때까지 같은 recovery_id 로 재시도하며, 현재 상태는 health()·open_recovery() 로 본다.
- 플랫폼 의도를 이번 주기에 읽지 못했으면(intent_known=False) crashed·absent 를 포함해 아무것도 판정하지 않고 열린 episode 도 그대로 둔다.
  건강은 OBSERVATION_UNAVAILABLE 이다. 등록 목록은 마지막으로 읽은 것을 유지하므로 보관·삭제로 해석하지 않는다.
- 시간은 전부 단조 시계다.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
from uuid import UUID, uuid4

from ..errors import Dom0ManagerError
from ..models import VmState
from .arming import ArmingState, ArmingStore
from .config import RecoverySettings
from .heartbeats import AppHeartbeatTable, HeartbeatTable
from .model import FailureReason, HealthState, Judgment, VmHealth, VmObservation
from .mqtt import LinkState

log = logging.getLogger(__name__)

# 판정 직전 재확인. None 이면 더 이상 대상이 아니다(보관·미등록). Dom0ManagerError 면 이번 주기는 보류한다
Confirm = Callable[[UUID], VmObservation | None]
AGENT, APP = "agent", "app"


class Verdict(str, Enum):
    OK = "ok"
    UNKNOWN = "unknown"
    FAILED = "failed"


@dataclass
class _Track:
    domid: int | None = None
    execution_id: str | None = None
    domain_since: float | None = None  # 현재 domid 를 처음 본 시각
    signaled: bool = False  # 이번 부팅에서 생존 신호를 받은 적이 있다 (받으면 boot grace 끝)
    absent_since: float | None = None  # 도메인이 없는 것을 처음 본 시각
    episode: Judgment | None = None  # 진행 중 장애 (최초 판정)
    current_reason: FailureReason | None = None  # 지금 보이는 사유 (episode 안에서 바뀔 수 있다)
    app_armed: bool = False  # Zephyr: app.heartbeat 를 받은 적이 있다 (ArmingStore 로 영속)
    held: str | None = None  # 손실 판정을 보류한 이유(silent: 동료도 모두 조용, sole: 동료 없음). 바뀔 때만 로그한다
    health: VmHealth | None = None


class Watcher:
    def __init__(
        self,
        settings: RecoverySettings,
        heartbeats: HeartbeatTable,
        app_heartbeats: AppHeartbeatTable | None = None,
        *,
        confirm: Confirm | None = None,
        arming: ArmingStore | None = None,
        clock: Callable[[], float] = time.monotonic,
        new_id: Callable[[], UUID] = uuid4,
    ) -> None:
        self._settings = settings
        self._heartbeats = heartbeats
        # 빈 표는 len() 이 0 이라 falsy 다. `or` 로 고르면 호출자가 준 표를 버린다
        self._app_heartbeats = app_heartbeats if app_heartbeats is not None else AppHeartbeatTable()
        self._confirm = confirm
        self._arming = arming
        armed_state = arming.load() if arming is not None else ArmingState()
        self._armed: dict[UUID, str] = armed_state.armed
        self._agent_delivered: set[UUID] = set(armed_state.agent_delivered)
        # 저장 파일이 깨졌다: capability 선언도 armed 기록도 없는 Zephyr VM 은 대상 여부를 알 수 없다. HEALTHY 로 꾸미지 않는다
        self._history_lost = armed_state.history_lost
        self._clock = clock
        self._new_id = new_id
        self._tracks: dict[UUID, _Track] = {}
        self._link = LinkState.down()
        self._link_since: dict[str, float | None] = {AGENT: None, APP: None}
        # 승인 뒤 그 채널에서 메시지를 하나라도 받았는가. Mosquitto 는 읽기 ACL 에 막힌 구독도 SUBACK 성공으로 답하므로
        # 승인만 믿고 "안 온다"를 판정하면 ACL 오설정이 그대로 VM 재시작으로 이어진다. 실제 수신이 경로의 증거다.
        # 어느 VM 의 수신이든 되므로 채널 공통 근거다. 대상 VM 의 근거는 VM 별 수신 이력(_delivered)이다
        self._link_proven: dict[str, bool] = {AGENT: False, APP: False}
        self._link_warned: dict[str, bool] = {AGENT: False, APP: False}
        self._removed: list[UUID] = []  # 지난 removed_vm_ids() 이후 등록에서 사라진 VM
        self._observed: dict[UUID, VmObservation] = {}  # 이번 주기의 관측. 동료 발행자를 고르는 데 쓴다
        if self._armed or self._agent_delivered:
            log.info("restored delivery history from %s: %d Zephyr VM(s), %d Linux VM(s)",
                     arming.path if arming else "-", len(self._armed), len(self._agent_delivered))
        if self._history_lost:
            log.error("armed history lost: undeclared Zephyr VMs are reported OBSERVATION_UNAVAILABLE until they signal; preserve %s and its evidence", arming.path if arming else "-")

    # ---- 판정 ----

    def tick(self, observations: list[VmObservation], now: float | None = None, *, link: LinkState | None = None) -> list[Judgment]:
        now = self._clock() if now is None else now
        self._track_link(LinkState.up() if link is None else link, now)
        self._observed = {obs.vm_id: obs for obs in observations}
        # 판정보다 먼저 모든 VM 의 실행 세대(domid·execution)를 갱신한다. 동료 증거를 고를 때 뒤에 평가될 VM 의 이전 실행 상태를 쓰지 않게 한다
        for obs in observations:
            self._track_domain(self._tracks.setdefault(obs.vm_id, _Track()), obs, now)
        judgments: list[Judgment] = []
        seen: set[UUID] = set()
        for obs in observations:
            seen.add(obs.vm_id)
            track = self._tracks.setdefault(obs.vm_id, _Track())
            verdict, reason, signal_age = self._evaluate(track, obs, now)
            if verdict == Verdict.FAILED and track.episode is None:
                # 판정 직전 재확인: 의도(사용자 stop)와 실행 상태(그 사이 시작·복구)를 다시 읽고 그 관측으로 다시 판정한다
                confirmed = self._confirm_target(obs, reason)
                if confirmed is not None:
                    obs = confirmed
                    verdict, reason, signal_age = self._evaluate(track, obs, now)
                else:
                    verdict, reason = Verdict.UNKNOWN, None  # 재확인 실패(잠금): 이번 주기는 보류
            if verdict == Verdict.OK:
                if track.episode is not None:
                    log.info("vm %s: recovery %s closed, VM healthy or no longer a target", obs.vm_id, track.episode.recovery_id)
                track.episode = None
                track.current_reason = None
            elif verdict == Verdict.FAILED:
                assert reason is not None
                if track.episode is None:
                    track.episode = Judgment(
                        recovery_id=self._new_id(), vm_id=obs.vm_id, reason=reason, detected_monotonic=now, guest_kind=obs.guest_kind,
                        desired_state=obs.desired_state, intent_revision=obs.intent_revision, domid=obs.domid, xen_state=obs.xen_state,
                        heartbeat_age_s=signal_age,
                    )
                    judgments.append(track.episode)
                elif track.current_reason != reason:
                    log.info("vm %s: failure reason %s -> %s within recovery %s", obs.vm_id, track.current_reason, reason.value, track.episode.recovery_id)
                track.current_reason = reason
            else:
                track.current_reason = None  # 유예·관측 불가: 사유는 비우고 episode 는 유지
            track.health = self._health(track, obs, verdict, reason, signal_age, now)
        for vm_id in list(self._tracks):
            if vm_id not in seen:  # 등록이 사라진(보관·삭제) VM
                del self._tracks[vm_id]
                self._removed.append(vm_id)
                self._heartbeats.forget(vm_id)
                self._app_heartbeats.forget(vm_id)
                had_app = self._armed.pop(vm_id, None) is not None
                had_agent = vm_id in self._agent_delivered
                self._agent_delivered.discard(vm_id)
                if (had_app or had_agent) and self._arming is not None:
                    self._save_armed()
        return judgments

    def _evaluate(self, track: _Track, obs: VmObservation, now: float) -> tuple[Verdict, FailureReason | None, float | None]:
        self._track_domain(track, obs, now)
        signal_age = self._signal_age(track, obs, now)
        verdict, reason = self._judge(track, obs, signal_age, now)
        return verdict, reason, signal_age

    def _confirm_target(self, obs: VmObservation, reason: FailureReason | None) -> VmObservation | None:
        """플랫폼 get 으로 다시 읽은 관측. 등록이 없으면 대상 아님으로, 실패하면 None(보류)."""
        if self._confirm is None:
            return obs
        try:
            fresh = self._confirm(obs.vm_id)
        except Dom0ManagerError as exc:
            log.warning("vm %s: %s suspected but could not be confirmed (%s); retrying next cycle", obs.vm_id, reason.value if reason else "?", exc.message)
            return None
        if fresh is None:
            log.info("vm %s: %s suspected but the VM is no longer registered; not judged", obs.vm_id, reason.value if reason else "?")
            return replace(obs, lifecycle="GONE", intent_applicable=False, present=False, xen_state=None, domid=None)
        if not fresh.eligible:
            log.info("vm %s: %s suspected but no longer eligible (desired=%s); not judged", obs.vm_id, reason.value if reason else "?", fresh.desired_state)
        return fresh

    def _track_link(self, link: LinkState, now: float) -> None:
        reconnected = link.connection is not None and self._link.connection is not None and link.connection != self._link.connection
        for channel, up, was in ((AGENT, link.agent, self._link.agent), (APP, link.app, self._link.app)):
            if up and was and reconnected:
                # 두 주기 사이에 끊겼다 다시 붙었다. 승인은 그대로여도 끊기기 전 수신은 지금 연결의 전달 증거가 아니다
                self._link_since[channel] = now
                self._link_proven[channel] = False
                self._link_warned[channel] = False
                log.warning("%s liveness path reconnected; loss judgments resume after the first message and one signal window", channel)
            elif up and not was:
                self._link_since[channel] = now
                self._link_proven[channel] = False
                self._link_warned[channel] = False
                log.info("%s liveness path granted; loss judgments resume after the first message and one signal window", channel)
            elif not up and was:
                self._link_since[channel] = None
                self._link_proven[channel] = False
                log.warning("%s liveness path lost (not connected or subscription not granted); loss judgments suspended", channel)
        self._link = link
        for channel in (AGENT, APP):  # 수신 증거는 매 주기 확인해 첫 수신 때 바로 남긴다 (VM 이 조용할 때만 보면 로그가 늦다)
            if self._link_since[channel] is not None and not self._link_proven[channel]:
                self._check_delivery(channel)

    def _check_delivery(self, channel: str) -> bool:
        """승인 뒤 그 채널로 메시지를 받았으면 브로커가 그 채널을 전달하는 것이다. 어느 VM 의 메시지든 되므로 특정 VM 의 근거는 아니다."""
        since = self._link_since[channel]
        if since is None:
            return False
        if not self._link_proven[channel]:
            table = self._heartbeats if channel == AGENT else self._app_heartbeats
            last = table.last_received_monotonic()
            if last is not None and last >= since:
                self._link_proven[channel] = True
                log.info("%s liveness path delivering: first message received since grant", channel)
        return self._link_proven[channel]

    def _link_ready(self, channel: str, now: float, window_s: float) -> bool:
        """'안 온다'를 믿을 조건: 구독이 승인됐고, 승인 뒤 그 채널에서 메시지를 실제로 받은 적이 있고, 승인 뒤 한 창(window) 이 지났다.
        승인만으로는 브로커가 전달하는지 알 수 없다(Mosquitto 는 읽기 ACL 거부를 SUBACK 로 알리지 않는다)."""
        since = self._link_since[channel]
        if since is None:
            return False
        if not self._check_delivery(channel):
            if now - since >= window_s and not self._link_warned[channel]:
                self._link_warned[channel] = True
                log.warning(
                    "%s liveness path granted but nothing received for %.0fs; the broker may be filtering deliveries "
                    "(Mosquitto does not refuse SUBACK for ACL-denied reads). Loss judgments stay suspended", channel, now - since,
                )
            return False
        return now - since >= window_s

    def _track_domain(self, track: _Track, obs: VmObservation, now: float) -> None:
        if obs.present:
            if track.domid != obs.domid or (obs.execution_id is not None and track.execution_id!=obs.execution_id):
                track.domid = obs.domid
                track.execution_id=obs.execution_id
                track.domain_since = now
                track.signaled = False
            track.absent_since = None
        else:
            if track.absent_since is None:
                track.absent_since = now
            track.domid = None
            track.domain_since = None
            track.signaled = False

    def _liveness(self, track: _Track, obs: VmObservation) -> tuple[str, float, float, FailureReason] | None:
        """(채널, 창, 유예, 사유). 생존 신호가 없는 종류면 None."""
        s = self._settings
        if obs.guest_kind == "linux":
            return AGENT, s.heartbeat_lost_s, s.boot_grace_s, FailureReason.HEARTBEAT_LOST
        if obs.guest_kind == "zephyr" and (track.app_armed or obs.app_heartbeat_expected is True):
            return APP, s.app_heartbeat_lost_s, s.zephyr_boot_grace_s, FailureReason.APP_HEARTBEAT_LOST
        return None

    def _target_unknown(self, track: _Track, obs: VmObservation) -> bool:
        """Zephyr 인데 앱 펌웨어인지 starter 인지 알 수 없다: capability 값이 잘못됐거나, 선언이 없고 armed 기록도 잃었다.
        신호를 한 번이라도 받아 armed 면 관측된 사실이 우선이다."""
        if obs.guest_kind != "zephyr" or track.app_armed:
            return False
        return obs.capability_error is not None or (obs.app_heartbeat_expected is None and self._history_lost)

    def _delivered(self, track: _Track, obs: VmObservation, channel: str) -> bool:
        """이 VM 의 신호가 실제로 전달된 적이 있는가(손실 판정 자격). 현재 실행의 건강 근거가 아니다."""
        return obs.vm_id in self._agent_delivered if channel == AGENT else track.app_armed

    def _save_armed(self) -> None:
        if self._arming.save(self._armed, self._agent_delivered) is False:
            self._history_lost = True
            log.error("armed history not saved in %s; undeclared targets remain observation-unavailable", self._arming.path)

    def _signal_age(self, track: _Track, obs: VmObservation, now: float) -> float | None:
        if obs.guest_kind == "zephyr":
            track.app_armed = track.app_armed or obs.vm_id in self._armed
            beat = self._app_heartbeats.last(obs.vm_id)
            if obs.expected_app_instances is not None:
                pair=self._app_heartbeats.supervision_pair(obs.vm_id,obs.expected_app_boot_id,obs.expected_app_instances)
                beat=min(pair,key=lambda value:value.received_monotonic) if pair else None
            if beat is not None and not track.app_armed:
                track.app_armed = True
                self._armed[obs.vm_id] = beat.app_id
                if self._arming is not None:
                    self._save_armed()
                log.info("vm %s armed for app.heartbeat liveness (app %s)", obs.vm_id, beat.app_id)
        else:
            beat = self._heartbeats.last(obs.vm_id)
            if beat is not None and obs.vm_id not in self._agent_delivered:
                self._agent_delivered.add(obs.vm_id)
                if self._arming is not None:
                    self._save_armed()
                log.info("vm %s armed for agent.heartbeat loss judgments (first delivery observed)", obs.vm_id)
        if beat is None:
            return None
        if obs.expected_app_instances is None and obs.expected_app_boot_id is not None and (beat.boot_id!=obs.expected_app_boot_id or
            getattr(beat,'instance_id',None)!=obs.expected_app_instance_id):return None
        if track.domain_since is not None and beat.received_monotonic < track.domain_since:
            return None  # 이전 부팅의 신호. 새 부팅에서는 아직 한 번도 받지 않은 것과 같다
        if track.domain_since is not None:
            track.signaled = True
        return max(0.0, now - beat.received_monotonic)

    def _judge(self, track: _Track, obs: VmObservation, signal_age: float | None, now: float) -> tuple[Verdict, FailureReason | None]:
        if not obs.intent_known:
            return Verdict.UNKNOWN, None  # 의도를 못 읽었다: 오래된 의도로 재시작하지도, 대상 아님으로 episode 를 닫지도 않는다
        if not obs.eligible:
            return Verdict.OK, None
        if not obs.present:
            assert track.absent_since is not None
            if now - track.absent_since >= self._settings.absent_grace_s:
                return Verdict.FAILED, FailureReason.DOMAIN_ABSENT
            return Verdict.UNKNOWN, None
        if obs.xen_state == VmState.crashed:
            return Verdict.FAILED, FailureReason.DOMAIN_CRASHED
        if obs.xen_state != VmState.running:
            return Verdict.UNKNOWN, None  # paused·stopping 은 장애가 아니지만 건강하지도 않다
        if obs.execution_binding_available is False:return Verdict.UNKNOWN,None
        if obs.expected_app_instances is not None:
            pair=self._app_heartbeats.supervision_pair(obs.vm_id,obs.expected_app_boot_id,obs.expected_app_instances)
            if pair is None:return Verdict.UNKNOWN,None
            # Fresh functional restrictions are not VM death. Stale supervision
            # still follows existing delivery/grace/common-path loss safeguards.
            if all(0<=now-value.received_monotonic<=.3 for value in pair):
                healthy=lambda value: (value.phase=='WAITING' and value.readiness=='QUALIFIED') or (
                    value.phase=='EXECUTING' and value.readiness=='READY' and value.work_at is not None and 0<=now-value.work_at<=.3)
                if not all(healthy(value) for value in pair):return Verdict.UNKNOWN,None
            elif signal_age is not None and signal_age<self._settings.app_heartbeat_lost_s:
                return Verdict.UNKNOWN,None
        liveness = self._liveness(track, obs)
        if liveness is None:
            if self._target_unknown(track, obs):
                return Verdict.UNKNOWN, None
            return Verdict.OK, None  # 생존 신호가 없는 종류: Xen 실행이 전부다
        channel, window, grace, reason = liveness
        if obs.execution_binding_available is True and not track.signaled:
            # Never-received app traffic cannot distinguish startup failure
            # from a per-VM ACL/delivery failure. Xen crash/absence stays above.
            return Verdict.UNKNOWN,None
        if signal_age is not None and signal_age < window:
            track.held = None
            return Verdict.OK, None
        if not self._delivered(track, obs, channel):
            # 이 VM 의 신호를 받은 적이 없다: 계정·ACL·배포 문제와 부팅 정체를 구별할 수 없다. 다른 VM 의 수신은 근거가 아니다
            return Verdict.UNKNOWN, None
        if not self._link_ready(channel, now, window):
            return Verdict.UNKNOWN, None
        assert track.domain_since is not None
        if not track.signaled and now - track.domain_since < grace:
            return Verdict.UNKNOWN, None
        if self._channel_hold(track, obs, channel, window, now) is not None:
            return Verdict.UNKNOWN, None
        return Verdict.FAILED, reason

    def _channel_hold(self, track: _Track, obs: VmObservation, channel: str, window: float, now: float) -> str | None:
        """대상의 신호 손실을 공통 경로 장애와 구별할 수 없으면 그 이유(silent·sole). None 이면 판정해도 된다."""
        fresh = self._settings.agent_peer_fresh_s if channel == AGENT else self._settings.app_peer_fresh_s
        evidence = self._peer_evidence(obs.vm_id, channel, fresh, now)
        sole = self._settings.agent_sole_publisher if channel == AGENT else self._settings.app_sole_publisher
        if evidence == "fresh" or (evidence == "none" and sole == "trust"):
            hold = None
        else:
            hold = "silent" if evidence == "silent" else "sole"
        if hold != track.held:
            if hold == "silent":
                log.warning("vm %s: %s signal lost together with every peer publisher; common path suspected, loss judgment held", obs.vm_id, channel)
            elif hold == "sole":
                log.warning("vm %s: %s signal lost and no peer publisher can tell a VM fault from a common path fault; loss judgment held "
                            "(XENOVA_RECOVERY_%s_SOLE_PUBLISHER=trust judges sole publishers)", obs.vm_id, channel, channel.upper())
            elif track.held is not None:
                log.info("vm %s: a peer %s publisher is fresh again; loss judgment resumes", obs.vm_id, channel)
            track.held = hold
        return hold

    def _peer_evidence(self, vm_id: UUID, channel: str, fresh_s: float, now: float) -> str:
        """같은 채널 동료 발행자의 신선도. fresh: 하나라도 fresh_s 안, silent: 동료는 있으나 모두 그 밖, none: 동료 없음.
        동료는 지금 실행(tick 첫 단계에서 갱신한 domid·execution, domain_since 이후)에서 그 채널의 유효 메시지를 받은 VM 이다.
        그 메시지가 곧 동료의 전달 경로 증거라 기능 건강(signaled)·수신 이력(_delivered)은 따로 요구하지 않는다."""
        silent = False
        for peer_id, peer in self._observed.items():
            if peer_id == vm_id or not peer.eligible or not peer.present or peer.xen_state != VmState.running:
                continue
            track = self._tracks.get(peer_id)
            if track is None or track.domain_since is None or track.domid != peer.domid:
                continue
            if peer.execution_id is not None and track.execution_id != peer.execution_id:
                continue
            liveness = self._liveness(track, peer)
            if liveness is None or liveness[0] != channel:
                continue
            received = self._last_received(peer, channel)
            if received is None or received < track.domain_since:
                continue  # 지금 실행에서 이 채널 메시지를 받은 적이 없다(부팅 중이거나 이전 실행의 신호뿐): 동료가 아니다
            if now - received < fresh_s:
                return "fresh"
            silent = True
        return "silent" if silent else "none"

    def _last_received(self, obs: VmObservation, channel: str) -> float | None:
        """그 VM 이 이 채널로 보낸 마지막 신호의 수신 시각(채널 전달 증거). 현재 binding 이 있으면 그 binding 의 앱 메시지 중 가장 최근이다.
        기능 건강(_signal_age)은 두 앱 중 오래된 쪽을 보지만, 채널이 지금 전달하는지는 한 앱이라도 오면 된다."""
        if channel == AGENT:
            beat = self._heartbeats.last(obs.vm_id)
        elif obs.expected_app_instances is not None:
            beat = self._app_heartbeats.latest_bound(obs.vm_id, obs.expected_app_boot_id, obs.expected_app_instances)
        else:
            beat = self._app_heartbeats.last(obs.vm_id)
        return beat.received_monotonic if beat is not None else None

    # ---- 건강 스냅샷 ----

    def _health(self, track: _Track, obs: VmObservation, verdict: Verdict, reason: FailureReason | None, signal_age: float | None, now: float) -> VmHealth:
        if not obs.intent_known:
            state = HealthState.OBSERVATION_UNAVAILABLE
        elif verdict == Verdict.FAILED:
            state = HealthState.UNHEALTHY
        elif not obs.present:
            state = HealthState.STARTING if obs.eligible else HealthState.STOPPED
        elif obs.xen_state != VmState.running:
            state = HealthState.UNHEALTHY
        elif obs.execution_binding_available is False:
            state = HealthState.OBSERVATION_UNAVAILABLE
        elif obs.expected_app_instances is not None and verdict==Verdict.UNKNOWN:
            state = HealthState.OBSERVATION_UNAVAILABLE
        elif (liveness := self._liveness(track, obs)) is None:
            # 앱 신호가 선언되지도 관측되지도 않은 Zephyr: Xen 실행만 근거. armed 기록을 잃었으면 대상 여부를 모르므로 관측 불가
            state = HealthState.OBSERVATION_UNAVAILABLE if self._target_unknown(track, obs) else HealthState.HEALTHY
        else:
            channel, window, grace, _ = liveness
            channel_up = self._link.agent if channel == AGENT else self._link.app
            if signal_age is not None and signal_age < window:
                state = HealthState.HEALTHY
            elif not channel_up:
                state = HealthState.OBSERVATION_UNAVAILABLE
            elif not track.signaled and track.domain_since is not None and now - track.domain_since < grace:
                state = HealthState.STARTING
            elif obs.execution_binding_available is True and not track.signaled:
                state = HealthState.OBSERVATION_UNAVAILABLE
            elif not self._delivered(track, obs, channel):
                state = HealthState.OBSERVATION_UNAVAILABLE
            elif not self._link_ready(channel, now, window):
                state = HealthState.OBSERVATION_UNAVAILABLE
            elif self._channel_hold(track, obs, channel, window, now) is not None:
                state = HealthState.OBSERVATION_UNAVAILABLE
            else:
                state = HealthState.UNHEALTHY
        previous = track.health
        xen_state = obs.xen_state.value if obs.xen_state else None
        changed = previous is None or (previous.state, previous.desired_state, previous.xen_state, previous.reason) != (state, obs.desired_state, xen_state, reason)
        return VmHealth(
            vm_id=obs.vm_id,
            state=state,
            desired_state=obs.desired_state,
            xen_state=xen_state,
            heartbeat_age_s=signal_age,
            reason=reason,
            manager_seq=1 if previous is None else previous.manager_seq + 1 if changed else previous.manager_seq,
            intent_known=obs.intent_known,
        )

    def health(self, vm_id: UUID) -> VmHealth | None:
        track = self._tracks.get(vm_id)
        return track.health if track else None

    def healths(self) -> list[VmHealth]:
        return [t.health for t in self._tracks.values() if t.health is not None]

    def removed_vm_ids(self) -> list[UUID]:
        """지난 호출 이후 등록에서 사라진 VM. Worker 가 retained 건강 스냅샷을 지우는 데 쓴다."""
        removed, self._removed = self._removed, []
        return removed

    def open_recovery(self, vm_id: UUID) -> Judgment | None:
        """진행 중 장애의 최초 판정. 복구 정책(125)이 재시도 때 같은 recovery_id 를 쓰는 근거다."""
        track = self._tracks.get(vm_id)
        return track.episode if track else None

    def armed(self) -> dict[UUID, str]:
        return dict(self._armed)

    def agent_delivered(self) -> frozenset[UUID]:
        return frozenset(self._agent_delivered)

    @property
    def history_lost(self) -> bool:
        return self._history_lost
