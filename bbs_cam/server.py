"""BBS-Cam: use an iPhone as a Windows webcam.

iPhone (Safari) --WebRTC over LAN--> this server --pyvirtualcam--> "OBS Virtual Camera"
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import ssl
import time
from pathlib import Path

from aiohttp import web
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError

from .certs import ensure_cert
from .output import FrameBus, OutputThread, open_camera
from .video import fit_frame

log = logging.getLogger("bbs-cam")

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA_DIR = ROOT.parent / ".bbs-cam"


def lan_ip() -> str:
    """Best guess at the LAN address the phone should connect to (no packets are sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class CamState:
    def __init__(self, args: argparse.Namespace, bus: FrameBus) -> None:
        self.args = args
        self.bus = bus
        self.pc: RTCPeerConnection | None = None
        self.mirror = args.mirror
        self.source = "-"
        self.connected_at = 0.0


async def consume_video(state: CamState, track) -> None:
    w, h = state.args.width, state.args.height
    first = True
    while True:
        try:
            frame = await track.recv()
        except MediaStreamError:
            log.info("Video track ended")
            return
        if first:
            log.info("First frame: %dx%d", frame.width, frame.height)
            first = False
        state.source = f"{frame.width}x{frame.height}"
        # Scaling/colour conversion is CPU work; keep it off the event loop.
        img = await asyncio.to_thread(fit_frame, frame, w, h, state.mirror)
        state.bus.put(img)


async def close_pc(state: CamState) -> None:
    if state.pc is not None:
        pc, state.pc = state.pc, None
        await pc.close()


# ---------------------------------------------------------------- routes

async def index(request: web.Request) -> web.Response:
    return web.FileResponse(STATIC / "index.html")


async def offer(request: web.Request) -> web.Response:
    state: CamState = request.app["state"]
    params = await request.json()
    desc = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
    if "mirror" in params:
        state.mirror = bool(params["mirror"])

    # One phone at a time: a new connection replaces the old one.
    await close_pc(state)

    # LAN only -> no STUN/TURN; host candidates are all we need.
    pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    state.pc = pc
    state.connected_at = time.monotonic()
    peer = request.remote

    @pc.on("connectionstatechange")
    async def on_state() -> None:
        log.info("Peer %s connection: %s", peer, pc.connectionState)
        if pc.connectionState in ("failed", "closed"):
            if state.pc is pc:
                await close_pc(state)

    @pc.on("track")
    def on_track(track) -> None:
        if track.kind == "video":
            log.info("Receiving video from %s", peer)
            asyncio.ensure_future(consume_video(state, track))

    await pc.setRemoteDescription(desc)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)  # aiortc gathers all candidates here (non-trickle)
    return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})


async def settings(request: web.Request) -> web.Response:
    state: CamState = request.app["state"]
    params = await request.json()
    if "mirror" in params:
        state.mirror = bool(params["mirror"])
    return web.json_response({"mirror": state.mirror})


async def hangup(request: web.Request) -> web.Response:
    await close_pc(request.app["state"])
    return web.json_response({"ok": True})


async def status(request: web.Request) -> web.Response:
    state: CamState = request.app["state"]
    out: OutputThread = request.app["output"]
    return web.json_response({
        "live": out.live,
        "connection": state.pc.connectionState if state.pc else "none",
        "source": state.source,
        "output": f"{state.args.width}x{state.args.height}@{state.args.fps}",
        "device": out.cam.device,
        "frames_in": state.bus.frames_in,
        "mirror": state.mirror,
    })


async def cert_download(request: web.Request) -> web.Response:
    # Served with the profile MIME type so iOS offers to install it.
    return web.Response(
        body=request.app["cert_path"].read_bytes(),
        content_type="application/x-x509-ca-cert",
        headers={"Content-Disposition": 'attachment; filename="bbs-cam.crt"'},
    )


async def on_shutdown(app: web.Application) -> None:
    await close_pc(app["state"])
    app["output"].stop()


def print_banner(url: str, device: str, args: argparse.Namespace) -> None:
    line = "=" * 60
    print(f"\n{line}\n  BBS-Cam running")
    print(f"  Virtual camera : {device}  ({args.width}x{args.height} @ {args.fps}fps)")
    print(f"  Open on iPhone : {url}\n{line}")
    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.print_ascii(invert=True)
    except Exception:  # QR is a convenience; never block startup on it
        pass
    print("Safari will warn the certificate isn't trusted: Show Details -> visit this website.")
    print("Press Ctrl+C to stop.\n")


def build_app(args: argparse.Namespace, cam) -> web.Application:
    bus = FrameBus()
    out = OutputThread(cam, bus)
    app = web.Application()
    app["state"] = CamState(args, bus)
    app["output"] = out
    app.router.add_get("/", index)
    app.router.add_post("/offer", offer)
    app.router.add_post("/settings", settings)
    app.router.add_post("/hangup", hangup)
    app.router.add_get("/status", status)
    app.router.add_get("/cert", cert_download)
    app.router.add_static("/static/", STATIC)
    app.on_shutdown.append(on_shutdown)
    return app


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Use your iPhone as a Windows webcam.")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--port", type=int, default=8443)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--ip", help="LAN IP to put in the URL/cert (auto-detected if omitted)")
    p.add_argument("--backend", help="pyvirtualcam backend: obs or unitycapture (default: first found)")
    p.add_argument("--mirror", action="store_true", help="Mirror the output horizontally")
    p.add_argument("--no-vcam", action="store_true", help="Don't open a virtual camera (testing)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("aioice", "aiortc", "aiohttp.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    ip = args.ip or lan_ip()
    cert_path, key_path = ensure_cert(DATA_DIR, ip)
    ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ssl_ctx.load_cert_chain(cert_path, key_path)

    try:
        cam = open_camera(args.width, args.height, args.fps, args.backend, args.no_vcam)
    except RuntimeError as e:
        raise SystemExit(
            f"\nCould not open a virtual camera: {e}\n"
            "Install OBS Studio (28+), open it once, click 'Start Virtual Camera' then "
            "'Stop Virtual Camera', close OBS, and try again.\n"
            "Also make sure OBS itself isn't currently using its virtual camera."
        )

    app = build_app(args, cam)
    app["cert_path"] = cert_path
    app["output"].start()

    url = f"https://{ip}:{args.port}/"
    print_banner(url, cam.device, args)
    try:
        web.run_app(app, host=args.host, port=args.port, ssl_context=ssl_ctx, print=None)
    finally:
        app["output"].stop()
        cam.close()


if __name__ == "__main__":
    main()
