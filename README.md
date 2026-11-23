# BBS-Cam

Use an iPhone as a webcam on Windows. No iOS app, no Xcode: the phone opens a page in Safari,
streams its camera over your LAN via WebRTC, and the PC pushes the frames into the
**OBS Virtual Camera** device that Zoom, Discord, Teams, OBS, browsers, etc. can select.

```
iPhone Safari ──WebRTC (LAN)──▶ bbs_cam (Python, aiortc) ──pyvirtualcam──▶ "OBS Virtual Camera"
```

## Requirements
- Windows 10/11, Python 3.10+
- A virtual camera driver. Recommended: **Unity Capture** (github.com/schellingb/UnityCapture,
  run `Install\Install.bat` as administrator). BBS-Cam outputs to *Unity Video Capture*, which
  leaves OBS's own virtual camera free for scenes/overlays. Without it, BBS-Cam falls back to
  the OBS Virtual Camera (OBS must then be closed or not using its virtual camera).
- iPhone and PC on the same Wi-Fi/LAN

## Run
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m bbs_cam
```
Scan the QR code in the terminal with the iPhone camera (or type the https URL into Safari).
Safari warns the certificate isn't trusted → **Show Details → visit this website**. Tap
**Start streaming**, allow camera access, then pick **OBS Virtual Camera** in your app.

### Options
| Flag | Default | Meaning |
|---|---|---|
| `--width/--height` | 1280x720 | Virtual camera resolution (use 1920x1080 with the 1080p phone setting) |
| `--fps` | 30 | Virtual camera frame rate |
| `--port` | 8443 | HTTPS port |
| `--ip` | auto | LAN IP for the URL/cert if auto-detect picks the wrong adapter |
| `--mirror` | off | Mirror output (also toggleable from the phone) |
| `--backend` | unitycapture, then obs | Force one virtual camera driver |
| `--no-vcam` | off | Run without a virtual camera (testing) |

## Notes
- Hold the phone sideways for a normal 16:9 picture; portrait is letterboxed.
- Keep the page in the foreground. The page requests a screen wake lock; if your iOS version
  doesn't honor it, set Auto-Lock to Never while streaming.
- Windows Firewall: allow Python on **Private** networks when prompted (WebRTC uses random UDP ports).
- **Overlays in Discord:** iPhone → BBS-Cam → *Unity Video Capture* → OBS scene (add a
  *Video Capture Device* source set to Unity Video Capture, plus your overlays) →
  **Start Virtual Camera** → pick *OBS Virtual Camera* in Discord.
- Optional: remove the certificate warning permanently by opening `https://<pc-ip>:8443/cert`
  on the phone, installing the profile (Settings → Profile Downloaded), then enabling it under
  Settings → General → About → Certificate Trust Settings. The cert regenerates if your PC's IP changes.
- `/status` on the server returns JSON with live/connection/resolution info.
