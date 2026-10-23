"""webcam·video 프레임 루프. 메인 스레드에서 돈다 (OpenCV 미리보기 창은 메인 스레드에서만 안전하다).

- webcam: 장치 번호로 연다. 프레임을 못 읽으면 잠깐 쉬고 다시 시도한다 (그동안 결과가 오래돼 발행이 멈춘다).
- video: 파일 끝에서 처음으로 돌아가 반복한다. 파일 FPS 에 맞춰 재생해 실제 시간보다 빨리 돌지 않게 한다.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from .config import PerceptionSettings
from .publisher import LatestResult

log = logging.getLogger("xenova_perception")

WINDOW = "xenova-perception (q: quit)"


def open_capture(settings: PerceptionSettings):
    import cv2

    if settings.source == "webcam":
        capture = cv2.VideoCapture(settings.device)
        what = f"webcam {settings.device}"
    else:
        capture = cv2.VideoCapture(settings.video_path)
        what = f"video {settings.video_path}"
    if not capture.isOpened():
        raise RuntimeError(f"{what} 를 열 수 없다")
    return capture, what


def run_frames(
    settings: PerceptionSettings,
    latest: LatestResult,
    stop: threading.Event,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """프레임을 읽어 인식하고 latest 에 넣는다. 처리한 프레임 수를 돌려준다."""
    import cv2

    from .detect import Detector, draw

    capture, what = open_capture(settings)
    detector = Detector(settings)
    fps = capture.get(cv2.CAP_PROP_FPS) if settings.source == "video" else 0.0
    frame_period = 1.0 / fps if fps and 1.0 <= fps <= 240.0 else 0.0
    log.info("%s 열림 (%s)", what, f"{fps:.1f} fps 로 반복 재생" if frame_period else "실시간")
    frames = 0
    next_frame = clock()
    try:
        while not stop.is_set():
            ok, frame = capture.read()
            if not ok:
                if settings.source == "video" and frames > 0:
                    capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                log.warning("%s 프레임 읽기 실패, 0.2 s 뒤 다시", what)
                time.sleep(0.2)
                continue
            now = clock()
            result = detector.detect(frame, now)
            latest.put(result)
            frames += 1
            if settings.show:
                h, w = frame.shape[:2]
                cv2.imshow(WINDOW, draw(frame, result, detector.projector(w, h), settings))
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    stop.set()
            if frame_period:
                next_frame += frame_period
                delay = next_frame - clock()
                if delay > 0:
                    time.sleep(delay)
                elif delay < -frame_period:
                    next_frame = clock()  # 처리가 느리면 따라잡지 않고 다시 센다
    finally:
        capture.release()
        if settings.show:
            cv2.destroyAllWindows()
    return frames
