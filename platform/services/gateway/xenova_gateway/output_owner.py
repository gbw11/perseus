"""Single device owner: command expiry still attempts STOP during actor fsync.

No second serial writer is created. Blocked physical I/O remains a device limit;
the live owner is never replaced or closed before its termination is confirmed.
"""
from dataclasses import replace
import threading
import time

from .frame import DriveFrame, StopFrame, STOP_SAFE_STOP, STOP_SHUTDOWN, encode
from .sink import SinkError


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
        self.thread = threading.Thread(target=self._loop, name='gateway-device-owner', daemon=True)
        self.thread.start()

    def _write(self, frame, now):
        self.counter = (self.counter+1) & 0xff
        frame = replace(frame, counter=self.counter)
        count = self.sink.write(frame, now)
        if count != len(encode(frame)):
            raise SinkError('partial owned serial submission')
        self.last = frame
        return SubmissionReceipt(count,frame,getattr(count,'metadata',None))

    def device_view(self):return dict(self._device_view)

    def feedback_events(self):
        import queue
        result=[]
        if self.device is not None:
            for _ in range(8):
                try:result.append(self.device.events.get_nowait())
                except queue.Empty:break
        return result

    def write(self, frame, now):
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
            self.condition.notify()
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
                    self.condition.notify()
            raise SinkError('device submission outcome unconfirmed')
        if isinstance(result[0], BaseException):
            raise SinkError('owned serial submission failed') from result[0]
        return result[0]

    def _loop(self):
        try:
            while True:
                if self.device is not None:
                    try:
                        self.device.poll(self.clock());self._device_view=dict(self.device.snapshot)
                    except (OSError,SinkError) as exc:
                        if self.first_failure is None:self.first_failure=exc
                        self._device_view={**self._device_view,'ready':False,'fault':'DEVICE_LINK_UNAVAILABLE',
                                           'accepted':None,'applied':None,'physical_stop':None}
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
                    else:
                        delay = .01 if self.expiry is None else max(.0001, min(.01, self.expiry-now))
                        self.condition.wait(delay); continue
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
        finally:
            self.sink.close()

    def close(self):
        with self.condition:
            self.closing = True
            self.condition.notify()
        self.thread.join(.5)
        if self.thread.is_alive():
            raise SinkError('device owner termination unconfirmed')
