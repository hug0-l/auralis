#!/usr/bin/env python3
"""
qa/verify_app.py — end-to-end acceptance check for the Auralis alpha.

Starts the server, exercises every user-visible flow, and prints a PASS/FAIL report.
Usage:  python qa/verify_app.py [--port 8741] [--keep]
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import signal
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return ok


def http(method: str, url: str, payload=None, raw=False, timeout=120):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        if raw:
            return body
        return json.loads(body.decode())


def make_wav(seconds=2.0, sr=48000, ch=2) -> bytes:
    """A test signal: 1 kHz tone + 50 Hz hum + a click + white noise."""
    n = int(seconds * sr)
    frames = bytearray()
    for i in range(n):
        t = i / sr
        click = 0.9 if (0.5 * sr < i < 0.5 * sr + 40) else 0.0
        for c in range(ch):
            v = 0.25 * math.sin(2 * math.pi * 1000 * t)
            v += 0.05 * math.sin(2 * math.pi * 50 * t)
            v += click
            v += 0.002 * ((i * 1103515245 + c * 12345) % 2048 / 1024.0 - 1.0)
            v = max(-1.0, min(1.0, v))
            frames += struct.pack("<h", int(v * 32767))
    hdr = b"RIFF" + struct.pack("<I", 36 + len(frames)) + b"WAVEfmt "
    hdr += struct.pack("<IHHIIHH", 16, 1, ch, sr, sr * ch * 2, ch * 2, 16)
    hdr += b"data" + struct.pack("<I", len(frames))
    return hdr + bytes(frames)


def upload(url: str, wav: bytes, name="test.wav"):
    boundary = "----auralisqa"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        "Content-Type: audio/wav\r\n\r\n"
    ).encode() + wav + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def wav_frames(data: bytes) -> int:
    """Count the audio frames in a RIFF/WAVE payload.

    The server writes 24-bit PCM, so the frame count must come from the
    header (channels x bits-per-sample), not from a fixed byte size.
    """
    pos, fmt, dlen = 12, None, None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        csz = struct.unpack_from("<I", data, pos + 4)[0]
        if cid == b"fmt ":
            fmt = struct.unpack_from("<HHIIHH", data, pos + 8)
        elif cid == b"data":
            dlen = csz
            break
        pos += 8 + csz + (csz & 1)
    if fmt is None or dlen is None:
        return 0
    channels = fmt[1] or 1
    bits = fmt[5] or 16
    return dlen // max(1, channels * bits // 8)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8741)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    base = f"http://127.0.0.1:{args.port}"
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    env["PYTHONUNBUFFERED"] = "1"

    print("Auralis alpha — end-to-end acceptance\n")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "aurserver.app:app", "--port", str(args.port),
         "--log-level", "warning"],
        cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        # wait for health
        up = False
        for _ in range(120):
            if proc.poll() is not None:
                break
            try:
                urllib.request.urlopen(base + "/api/health", timeout=2).read()
                up = True
                break
            except Exception:
                time.sleep(0.5)
        if not up:
            out = b""
            try:
                out = proc.stdout.read() if proc.stdout else b""
            except Exception:
                pass
            print(out.decode(errors="replace")[-4000:])
            check("server starts", False, "server did not become healthy")
            return 1
        check("server starts", True, base)

        # 1. module catalogue
        mods = http("GET", base + "/api/modules")
        ml = mods.get("modules", [])
        check("module catalogue", len(ml) >= 20, f"{len(ml)} modules")
        cats = sorted({m["category"] for m in ml})
        check("categories", set(cats) >= {"Repair", "Utility"}, ",".join(cats))
        if ml:
            p0 = ml[0].get("params", [])
            check("module params exposed", len(p0) > 0,
                  f"{ml[0]['display_name']}: {len(p0)} params")
            check("param descriptors complete",
                  all({"name", "type", "default", "minimum", "maximum"} <= set(p) for p in p0))

        # 2. upload + analyse
        wav = make_wav()
        up_info = upload(base + "/api/upload", wav)
        aid = up_info.get("asset_id")
        check("upload", bool(aid),
              f"{up_info.get('frames')} frames @ {up_info.get('sample_rate')} Hz")
        check("upload metadata", up_info.get("channels") == 2 and up_info.get("duration", 0) > 1.5,
              f"{up_info.get('channels')} ch, {up_info.get('duration'):.2f}s")

        an = http("POST", base + "/api/analyze", {"asset_id": aid})
        spec = an.get("spectrogram")
        wave = an.get("waveform")
        check("spectrogram", bool(spec) and len(spec) > 10 and len(spec[0]) > 10,
              f"{len(spec)} bins x {len(spec[0])} frames" if spec else "missing")
        check("waveform envelope", bool(wave) and len(wave) > 100, f"{len(wave)} buckets")
        check("analysis axes", an.get("freq_bins", 0) > 0 and an.get("frames", 0) > 0)

        # 3. chain editing
        chain = http("GET", base + "/api/chain")
        check("chain starts empty", len(chain.get("chain", [])) == 0)

        c1 = http("POST", base + "/api/chain", {"code": "AUR_DEHUM"})
        check("add module", len(c1.get("chain", [])) == 1,
              c1.get("chain", [{}])[0].get("display_name", "?"))
        c2 = http("POST", base + "/api/chain", {"code": "AUR_DECLICK"})
        check("add second module", len(c2.get("chain", [])) == 2)
        c3 = http("POST", base + "/api/chain/reorder", {"from": 1, "to": 0})
        names = [s["code"] for s in c3.get("chain", [])]
        check("reorder", names[:2] == ["AUR_DECLICK", "AUR_DEHUM"], " -> ".join(names))

        bp = http("POST", base + "/api/chain/0/bypass", {"bypass": True})
        check("bypass", bp.get("chain", [{}])[0].get("bypass") is True)

        pr = http("GET", base + "/api/chain/0/params")
        check("param list", len(pr.get("params", [])) > 0, f"{len(pr.get('params', []))} params")
        check("param values", isinstance(pr.get("values"), dict) and len(pr["values"]) > 0)

        # set a parameter that the module at index 0 actually owns, using a
        # value inside its declared range (the core clamps out-of-range values)
        descs = pr.get("params", [])
        target = None
        value = None
        for d in descs:
            if d.get("name") in ("Base Frequency", "Sensitivity", "Gain"):
                target = d["name"]
                lo, hi = float(d.get("minimum", 0.0)), float(d.get("maximum", 1.0))
                value = lo + (hi - lo) * 0.5
                break
        check("a settable parameter exists", target is not None,
              str([d.get("name") for d in descs][:5]))
        if target is not None:
            sv = http("POST", base + "/api/chain/0/param",
                      {"name": target, "value": value})
            check("set param", sv.get("ok") is True)
            got = http("GET", base + "/api/chain/0/params")["values"].get(target)
            check("param persisted", got is not None and abs(float(got) - value) < 1e-6,
                  f"{target} = {got} (expected {value})")

        http("POST", base + "/api/chain/0/bypass", {"bypass": False})

        # 4. render
        rr = http("POST", base + "/api/render", {"asset_id": aid})
        out_id = rr.get("asset_id")
        check("render", bool(out_id), rr.get("url", ""))
        if out_id:
            audio = http("GET", base + f"/api/audio/{out_id}", raw=True)
            check("render is wav", audio[:4] == b"RIFF" and len(audio) > 1000,
                  f"{len(audio)} bytes")
            frames = wav_frames(audio)
            check("render length preserved", abs(frames - up_info["frames"]) <= 4,
                  f"{frames} vs {up_info['frames']}")

        # 5. compare
        try:
            cp = http("POST", base + "/api/compare",
                      {"asset_id": aid, "chain_a": [], "chain_b": [{"code": "AUR_DEHUM"}]})
            check("compare", bool(cp.get("a")) and bool(cp.get("b")))
        except urllib.error.HTTPError as e:
            check("compare", False, f"HTTP {e.code}")

        # 6. presets
        try:
            ps = http("GET", base + "/api/presets")
            check("preset list", isinstance(ps.get("presets"), list),
                  f"{len(ps.get('presets', []))} presets")
        except urllib.error.HTTPError as e:
            check("preset list", False, f"HTTP {e.code}")

        # 7. static UI
        try:
            html = http("GET", base + "/", raw=True).decode(errors="replace")
            check("index served", "Auralis" in html and "spectrogram" in html.lower(),
                  f"{len(html)} bytes")
            for asset in ("/css/tokens.css", "/css/app.css", "/js/app.js", "/js/api.js"):
                try:
                    body = http("GET", base + asset, raw=True)
                    check(f"asset {asset}", len(body) > 100, f"{len(body)} bytes")
                except Exception as e:
                    check(f"asset {asset}", False, str(e)[:80])
        except Exception as e:
            check("index served", False, str(e)[:120])

    finally:
        if not args.keep:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{'=' * 60}\n  {passed}/{total} checks passed")
    if passed != total:
        print("  failures:")
        for n, ok, d in RESULTS:
            if not ok:
                print(f"    - {n}: {d}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
