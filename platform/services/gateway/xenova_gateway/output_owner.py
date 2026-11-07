"""Single device owner: command expiry still attempts STOP during actor fsync.

No second serial writer is created. Blocked physical I/O remains a device limit;
the live owner is never replaced or closed before its termination is confirmed.
"""
from dataclasses import replace
import os
import select
import threading
import time

from .frame import DriveFrame, StopFrame, STOP_SAFE_STOP, STOP_SHUTDOWN, encode
from .sink import SinkError


class StopSubmission:
    """Bounded reference to the original STOP and its actual completion."""

    def __init__(self, event, result, owner, reason='call_budget_exhausted'):
        self.event = event
        self.result = result
        self.owner = owner
        self.reason = reason

    def outcome(self):
        if not self.event.is_set():
            if not self.owner.is_alive():
                raise SinkError("device owner terminated before submission confirmation")
            return None
        value = self.result[0]
        if isinstance(value, BaseException):
            raise SinkError('owned serial submission failed') from value
        return value


class PendingStop(SinkError):
    def __init__(self, submission):
        super().__init__('device submission outcome unconfirmed')
        self.submission = submission


class SubmissionReceipt(int):
    def __new__(cls, count, frame,metadata=None):
        value = super().__new__(cls, count)
        value.frame = frame
        value.metadata={} if metadata is None else dict(metadata)
        return value


class OutputOwner:
    def __init__(self, sink, *, command_timeout_s, clock=time.monotonic):
        if type(command_timeout_s) not in (int, float) or not 0 < command_timeout_s <= 1:
            raise ValueError('existing finite command timeout required')
        self.sink, self.clock, self.timeout = sink, clock, command_timeout_s
        self.condition = threading.Condition()
        self.pending = None
        self.closing = False
        self.expiry = None
        self.counter = 0
        self.last = None
        self.watchdog_stops = 0
        self.first_failure = None
        device=sink
        while hasattr(device,'sink'):device=device.sink
        self.device=device if callable(getattr(device,'poll',None)) else None
        self._device_view={'feedback_supported':False,'ready':True,'fault':None,
                           'accepted':None,'applied':None,'physical_stop':None}
        if self.device is not None:self._device_view=dict(self.device.snapshot)
        self._feedback_timeout=getattr(self.device,'timeout',None)
        sensor=getattr(self.device,'odometry',None)
        self._vehicle_maximum_age_us=getattr(getattr(sensor,'config',None),'maximum_sample_age_us',None)
        self._read_fd=getattr(self.device,'_write_fd',None) if os.name=='posix' else None
        self._owned_poll=getattr(self.device,'_poll_owned',None)
        self._wake_read,self._wake_write=(os.pipe2(os.O_NONBLOCK|os.O_CLOEXEC)
            if self._read_fd is not None else (None,None))
        self.thread = threading.Thread(target=self._loop, name='gateway-device-owner', daemon=True)
        try:self.thread.start()
        except BaseException:
            self._close_wakeup();raise

    def _wake(self):
        self.condition.notify()
        if self._wake_write is not None:
            try:os.write(self._wake_write,b'\0')
            except BlockingIOError:pass  # A full finite pipe already carries a wakeup.

    def _wait_io(self,delay):
        """One serial owner waits for RX or a new STOP; no second reader/writer."""
        ready,_,_=select.select([self._wake_read,self._read_fd],[],[],delay)
        if self._wake_read in ready:
            try:os.read(self._wake_read,4096)
            except BlockingIOError:pass
        return self._read_fd in ready

    def _close_wakeup(self):
        with self.condition:
            for fd in (self._wake_read,self._wake_write):
                if fd is not None:os.close(fd)
            self._wake_read=self._wake_write=None

    def _write(self, frame, now):
        self.counter = (self.counter+1) & 0xff
        frame = replace(frame, counter=self.counter)
        count = self.sink.write(frame, now)
        if count != len(encode(frame)):
            raise SinkError('partial owned serial submission')
        self.last = frame
        if self.device is not None:
            # Expose the new command reference before waking its caller.
            # The previous command's ACK must not appear applied to this one.
            self._device_view = dict(self.device.snapshot)
        return SubmissionReceipt(count,frame,getattr(count,'metadata',None))

    def device_view(self):
        # Recheck only the immutable published sample and fixed age budgets.
        # A delayed owner must not lend its last snapshot fresh validity.
        view=dict(self._device_view);now=self.clock()
        observed=view.get('observed_monotonic')
        if self._feedback_timeout is not None and observed is not None and not 0<=now-observed<=self._feedback_timeout:
            for key in ('accepted','applied','physical_stop','reported_velocity_mps','reported_steering_rad'):
                view[key]=None
        sample=view.get('vehicle_sample')
        if sample is not None and self._vehicle_maximum_age_us is not None:
            age=int(now*1000000)-sample['pi_sample_us']
            if age<0 or age+sample['clock_error_us']>self._vehicle_maximum_age_us:
                view['vehicle_sample']=None
        return view

    def drive_ready_at(self,frame):
        if self.first_failure is not None:return 0.
        return getattr(self.sink,'drive_ready_at',lambda f:0.)(frame)

    def feedback_events(self):
        import queue
        result=[]
        if self.device is not None:
            for _ in range(8):
                try:result.append(self.device.events.get_nowait())
                except queue.Empty:break
        return result

    def _enqueue(self, frame, now):
        if isinstance(frame,DriveFrame) and self.first_failure is not None:
            raise SinkError('device owner fault; new DRIVE prohibited') from self.first_failure
        if isinstance(frame, DriveFrame) and not 0 <= self.clock()-now < self.timeout:
            raise SinkError('expired actor submission')
        event = threading.Event()
        result = []
        cancelled=threading.Event()
        item=(frame,now,event,result,cancelled)
        with self.condition:
            if self.closing:
                raise SinkError('device owner unavailable/busy')
            if self.pending is not None:
                old=self.pending
                if isinstance(frame,StopFrame) and isinstance(old[0],StopFrame):
                    event,result=old[2:4]
                elif isinstance(frame,StopFrame):
                    old[4].set();old[3].append(SinkError('superseded by STOP'));old[2].set()
                    self.pending=item
                else:raise SinkError('device owner unavailable/busy')
            else:self.pending=item
            self._wake()
        return item, event, result, cancelled

    def submit_stop(self, frame, now):
        """Queue one STOP without waiting in the actor for a thread wakeup."""
        if not isinstance(frame, StopFrame):
            raise TypeError('STOP frame required')
        _, event, result, _ = self._enqueue(frame, now)
        return StopSubmission(event, result, self.thread, reason='queued')

    def write(self, frame, now):
        item, event, result, cancelled = self._enqueue(frame, now)
        if not event.wait(.01):
            cancelled.set()
            if isinstance(frame,DriveFrame):
                error=SinkError('device submission outcome unconfirmed')
                if self.first_failure is None:self.first_failure=error
                with self.condition:
                    # Retain a mandatory STOP even if the actor timed out while
                    # the previous write was still owned/in flight.
                    stop=(StopFrame(frame.generation,0,STOP_SAFE_STOP),self.clock(),
                          threading.Event(),[],threading.Event())
                    if self.pending is item or self.pending is None:self.pending=stop
                    self._wake()
            if isinstance(frame, StopFrame):
                raise PendingStop(StopSubmission(event, result, self.thread))
            raise SinkError('device submission outcome unconfirmed')
        if isinstance(result[0], BaseException):
            raise SinkError('owned serial submission failed') from result[0]
        return result[0]

    def _poll_device(self,*,read_ready=False):
        try:
            if self._read_fd is not None and self._owned_poll is not None:
                self._owned_poll(self.clock(),read_ready=read_ready)
            else:self.device.poll(self.clock())
            self._device_view = dict(self.device.snapshot)
        except (OSError, SinkError) as exc:
            if self.first_failure is None:
                self.first_failure = exc
            # HUP stays readable. Return to bounded timed checks after link
            # failure rather than spinning on that permanently ready fd.
            self._read_fd=None
            failed=getattr(self.device,'link_failed',None)
            if failed is not None:
                failed()
                self._device_view=dict(self.device.snapshot)
            self._device_view = {**self._device_view, 'ready': False, 'fault': 'DEVICE_LINK_UNAVAILABLE',
                                 'accepted': None, 'applied': None, 'physical_stop': None}
            if 'vehicle_sample' in self._device_view:self._device_view['vehicle_sample']=None

    def _loop(self):
        poll_due = self.device is not None
        read_ready = False
        try:
            while True:
                # Consume the previous wait's hint even if a cancelled command
                # or higher-priority STOP makes this iteration skip RX.
                observed_ready=read_ready;read_ready=False
                wait_io=False
                with self.condition:
                    now = self.clock()
                    expired = self.expiry is not None and now >= self.expiry
                    item = self.pending
                    if expired:
                        frame = StopFrame(self.last.generation, 0, STOP_SAFE_STOP)
                        self.expiry = None
                        self.watchdog_stops += 1
                    elif item is not None:
                        self.pending = None
                        frame, submitted, event, result, cancelled = item
                        if isinstance(frame, DriveFrame) and (cancelled.is_set() or not 0 <= now-submitted < self.timeout):
                            result.append(SinkError('queued command expired')); event.set(); continue
                    elif self.closing:
                        if isinstance(self.last, DriveFrame):
                            frame = StopFrame(self.last.generation, 0, STOP_SHUTDOWN)
                        else:
                            break
                    elif poll_due:
                        frame = None
                    else:
                        delay = .01 if self.expiry is None else max(.0001, min(.01, self.expiry-now))
                        if self._read_fd is None:
                            self.condition.wait(delay)
                            poll_due = self.device is not None
                            continue
                        wait_io=True
                if wait_io:
                    read_ready=bool(self._wait_io(delay))
                    poll_due=True
                    continue
                # A hint belongs to this one poll, not a later timer/write.
                # Queued STOP/expiry still wins over RX and discards the hint.
                if frame is None:
                    self._poll_device(read_ready=observed_ready)
                    poll_due = False
                    continue
                try:
                    receipt = self._write(frame, now)
                    if isinstance(frame, DriveFrame):
                        self.expiry = submitted+self.timeout
                    else:
                        self.expiry = None
                    outcome = receipt
                except BaseException as exc:
                    if self.first_failure is None:
                        self.first_failure = exc
                    self.expiry = None
                    outcome = exc
                if not expired and item is not None:
                    result.append(outcome); event.set()
                if self.closing and item is None:
                    break
                # Complete the queued output before parsing feedback or
                # sending an odometry probe. Poll after each write so input
                # still progresses when commands arrive continuously.
                if self.device is not None:
                    self._poll_device()
                    poll_due = False
        finally:
            self._close_wakeup()
            self.sink.close()

    def close(self):
        with self.condition:
            self.closing = True
            self._wake()
        self.thread.join(.5)
        if self.thread.is_alive():
            raise SinkError('device owner termination unconfirmed')
