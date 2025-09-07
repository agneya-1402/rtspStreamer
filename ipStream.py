#!/usr/bin/env python3
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

# ====== CONFIG ======
INPUT_FILE = Path("videos/6.mp4")            # Change if needed
RTSP_URL   = "rtsp://admin:admin123@192.168.1.111:8554/Streaming/Channels/101"
USE_TCP    = True                            # RTSP over TCP for reliability
LOOP_INPUT = True                            # Loop the file forever
LOG_FILE   = "ffmpeg_stream.log"             # FFmpeg log file
# ====================

def wait_port(host: str, port: int, timeout: float = 5.0) -> bool:
    """Return True if TCP port is reachable within timeout."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection((host, port), timeout=1.5):
                return True
        except OSError:
            time.sleep(0.25)
    return False

def build_ffmpeg_cmd(input_file: Path, rtsp_url: str, use_tcp: bool, loop_input: bool) -> list:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-re",                              # read input at native rate
    ]
    if loop_input:
        cmd += ["-stream_loop", "-1"]       # loop file forever (requires -re)
    cmd += [
        "-i", str(input_file),

        # Video
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-profile:v", "baseline",
        "-level:v", "3.1",
        "-pix_fmt", "yuv420p",
        "-g", "48",                         # ~2s keyframe at 24fps; adjust if needed

        # Audio
        "-c:a", "aac",
        "-ar", "44100",
        "-b:a", "128k",

        # Mux/Output
        "-f", "rtsp",
    ]
    if use_tcp:
        cmd += ["-rtsp_transport", "tcp"]
    cmd += [rtsp_url]
    return cmd

def run():
    # Sanity checks
    if not INPUT_FILE.exists():
        print(f"ERROR: input file not found: {INPUT_FILE}", file=sys.stderr)
        sys.exit(1)

    # Extract host:port from RTSP URL for reachability check
    # Expected form: rtsp://user:pass@host:port/path
    try:
        host_port = RTSP_URL.split("@", 1)[1].split("/", 1)[0]
        host, port = host_port.split(":")
        port = int(port)
    except Exception:
        print("WARNING: couldn’t parse host/port from RTSP_URL; skipping reachability check.")

    else:
        print(f"Checking MediaMTX reachability at {host}:{port} ...")
        if not wait_port(host, port, timeout=5):
            print(f"WARNING: {host}:{port} not reachable now. "
                  "I’ll start anyway and ffmpeg will retry on its own.")

    # FFmpeg detailed log
    os.environ["FFREPORT"] = f"file={LOG_FILE}:level=32"

    ffmpeg_cmd = build_ffmpeg_cmd(INPUT_FILE, RTSP_URL, USE_TCP, LOOP_INPUT)
    print("Launching FFmpeg:\n  " + " ".join(ffmpeg_cmd))

    proc = None
    stop = False

    def handle_sigint(sig, frame):
        nonlocal stop, proc
        print("\nStopping ...")
        stop = True
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception:
                pass

    signal.signal(signal.SIGINT, handle_sigint)
    signal.signal(signal.SIGTERM, handle_sigint)

    backoff = 1.0
    while not stop:
        try:
            proc = subprocess.Popen(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            # Stream ffmpeg output to console and log file
            with open(LOG_FILE, "a", buffering=1) as lf:
                for line in proc.stdout:
                    print(line, end="")
                    lf.write(line)

            code = proc.wait()
            if stop:
                break

            print(f"\nFFmpeg exited with code {code}. Restarting in {backoff:.1f}s ...")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)  # exponential backoff up to 30s

        except Exception as e:
            print(f"ERROR: {e}")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    print("Exited.")

if __name__ == "__main__":
    run()
