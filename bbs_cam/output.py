"""Virtual-camera output.

A dedicated thread pushes frames to the virtual camera at a fixed rate. The
WebRTC side only drops the newest decoded frame into `FrameBus`; the output
thread always sends *something* (latest frame or a standby card), so apps like
Zoom/Discord never see the camera freeze or disappear between connections.
"""
from __future__ import annotations

import logging
import threading
import time

import numpy as np

log = logging.getLogger("bbs-cam.output")

STALE_AFTER_S = 1.5  # no new frame for this long -> show standby card


class FrameBus:
    """Single-slot, latest-wins frame handoff between asyncio and the output thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._stamp = 0.0
        self.frames_in = 0

    def put(self, frame: np.ndarray) -> None:
        with self._lock:
            self._frame = frame
            self._stamp = time.monotonic()
            self.frames_in += 1

    def get(self) -> tuple[np.ndarray | None, float]:
        with self._lock:
            return self._frame, self._stamp


def standby_frame(width: int, height: int) -> np.ndarray:
    """Dark card with a blue/orange bar so 'no signal' is obvious at a glance."""
    frame = np.full((height, width, 3), 24, dtype=np.uint8)
    bar_h = max(4, height // 60)
    y0 = height // 2 - bar_h // 2
    half = width // 2
    frame[y0:y0 + bar_h, width // 4:half] = (40, 120, 255)      # blue
    frame[y0:y0 + bar_h, half:width - width // 4] = (255, 140, 0)  # orange
    return frame


class NullCamera:
    """Stand-in used with --no-vcam (testing / debugging without OBS)."""

    device = "null (no virtual camera)"

    def __init__(self, width: int, height: int, fps: int) -> None:
        self.width, self.height, self.fps = width, height, fps
        self._next = time.monotonic()
        self.frames_sent = 0

    def send(self, frame: np.ndarray) -> None:
        assert frame.shape == (self.height, self.width, 3), frame.shape
        self.frames_sent += 1

    def sleep_until_next_frame(self) -> None:
        self._next += 1.0 / self.fps
        delay = self._next - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            self._next = time.monotonic()

    def close(self) -> None:
        pass


def open_camera(width: int, height: int, fps: int, backend: str | None, disabled: bool):
    if disabled:
        return NullCamera(width, height, fps)
    import pyvirtualcam  # imported lazily so --no-vcam works without OBS

    from pyvirtualcam import PixelFormat

    kwargs = dict(width=width, height=height, fps=fps, fmt=PixelFormat.RGB)
    if backend:
        kwargs["backend"] = backend
    return pyvirtualcam.Camera(**kwargs)


class OutputThread(threading.Thread):
    def __init__(self, cam, bus: FrameBus) -> None:
        super().__init__(name="vcam-output", daemon=True)
        self.cam = cam
        self.bus = bus
        self._stop = threading.Event()
        self._standby = standby_frame(cam.width, cam.height)
        self.live = False

    def run(self) -> None:
        log.info("Virtual camera output started on %s", self.cam.device)
        while not self._stop.is_set():
            frame, stamp = self.bus.get()
            live = frame is not None and (time.monotonic() - stamp) < STALE_AFTER_S
            if live != self.live:
                self.live = live
                log.info("Signal %s", "LIVE" if live else "lost -> standby")
            try:
                self.cam.send(frame if live else self._standby)
            except Exception:  # never let a single bad frame kill the camera
                log.exception("Failed to send frame")
            self.cam.sleep_until_next_frame()

    def stop(self) -> None:
        self._stop.set()
