"""Convert decoded WebRTC frames into fixed-size RGB arrays for the virtual camera."""
from __future__ import annotations

import numpy as np
from av import VideoFrame


def fit_frame(frame: VideoFrame, out_w: int, out_h: int, mirror: bool = False) -> np.ndarray:
    """Scale to fit inside out_w x out_h preserving aspect ratio, letterboxing the rest.

    The phone may send portrait or landscape, and Safari changes resolution on
    the fly when bandwidth dips, but the virtual camera needs a constant size.
    """
    src_w, src_h = frame.width, frame.height
    scale = min(out_w / src_w, out_h / src_h)
    # Even dimensions keep swscale happy with yuv420p sources.
    new_w = max(2, int(src_w * scale) // 2 * 2)
    new_h = max(2, int(src_h * scale) // 2 * 2)

    img = frame.to_ndarray(format="rgb24", width=new_w, height=new_h)
    if mirror:
        img = img[:, ::-1]

    if new_w == out_w and new_h == out_h:
        return np.ascontiguousarray(img)

    canvas = np.zeros((out_h, out_w, 3), dtype=np.uint8)
    x0 = (out_w - new_w) // 2
    y0 = (out_h - new_h) // 2
    canvas[y0:y0 + new_h, x0:x0 + new_w] = img
    return canvas
