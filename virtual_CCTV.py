import socket
import threading
import uuid
import subprocess
import time
import os
import logging
import re
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone

# =====================================================================
#                        USER CONFIGURATION
# =====================================================================
DEVICE_IP = "192.168.1.12"     # Your machine's LAN IP address
HTTP_PORT_BASE = 8080          # First ONVIF HTTP port. Each simulated brand
                                # device gets its own port: base, base+1, base+2 ...

MEDIAMTX_IP = DEVICE_IP
MEDIAMTX_PORT = 554             # MediaMTX RTSP port (all devices push here)

USERNAME = "admin"
PASSWORD = "admin@123"
WS_DISCOVERY_PORT = 3702

# CRITICAL FOR 50+ MAIN STREAMS:
# Set to True to copy codecs without re-encoding (uses virtually 0% CPU per stream).
# Set to False only if your videos are not H.264 and require active re-encoding.
USE_STREAM_COPY = True

# ---------------------------------------------------------------------
# BRAND SIMULATION
# ---------------------------------------------------------------------
# Pick ONE brand -> every channel is simulated as that single brand/device.
# Pick a LIST of brands -> spins up one independent virtual NVR "device"
# per brand (own ONVIF port, own UUID, own device info, own RTSP path
# scheme) so you can test how your client behaves against each vendor's
# real-world path conventions, side by side, in one run.
#
# Supported keys: "hikvision", "dahua", "cpplus", "uniview", "generic"
SIMULATED_BRANDS = ["hikvision", "dahua", "cpplus", "generic"]

# How VIDEO_FILES gets divided across the brands in SIMULATED_BRANDS
# when more than one brand is listed:
#   "round_robin" -> files dealt out 1,2,3,4,1,2,3,4... one to each brand in turn
#   "split_even"  -> files chunked into contiguous blocks, one block per brand
DISTRIBUTION_MODE = "round_robin"

# Emit a second, lower-resolution "sub stream" per channel, exactly like
# real NVRs expose a main + sub profile. Sub streams must be re-encoded
# (can't be stream-copied) because they're downscaled.
ENABLE_SUBSTREAM = True
SUBSTREAM_WIDTH = 640
SUBSTREAM_HEIGHT = 360
SUBSTREAM_BITRATE_KBPS = 512
SUBSTREAM_FPS = 12

# Each entry is either a plain path (auto-distributed across
# SIMULATED_BRANDS per DISTRIBUTION_MODE), or a dict that pins the file
# to one specific brand regardless of distribution mode:
#   {"path": "videos/entry_1.mp4", "brand": "dahua"}
VIDEO_FILES = [
    Path("videos/entry_1.mp4"),
    Path("videos/build/3.mp4"),
    Path("videos/Table_CleanAlert_1.mp4"),
    Path("videos/entry_exit_1.mp4"),
    Path("videos/build/20260506_164130_Camera_192.168.0.112_-_Ch2.mp4"),
    Path("videos/build/20260506_165232_Camera_192.168.0.115_-_Ch56.mp4"),
    Path("videos/build/20260727_152351_Camera_192.168.0.128_-_Ch0.mp4"),
    Path("videos/build/20260727_153316_Camera_192.168.0.114_-_Ch1.mp4"),
    Path("videos/build/20260727_153514_Camera_192.168.0.121_-_Ch1.mp4"),
    Path("videos/build/20260727_153741_Camera_192.168.0.122_-_Ch0.mp4"),
    Path("videos/build/20260727_155148_Camera_192.168.0.127_-_Ch1.mp4"),
    Path("videos/build/20260727_160408_Camera_192.168.0.122_-_Ch0.mp4"),
    Path("videos/build/20260727_163210_Camera_192.168.0.114_-_Ch1.mp4"),
    Path("videos/build/20260727_164459_Camera_192.168.0.114_-_Ch1.mp4"),
    Path("videos/build/20260727_174058_Camera_192.168.0.122_-_Ch0.mp4"),

    # Add more paths here... (plain Path, or {"path": ..., "brand": "..."})
]
# =====================================================================

# ---------------------------------------------------------------------
# BRAND RTSP PATH CONVENTIONS
# ---------------------------------------------------------------------
# {ch} is substituted with the 1-indexed channel number *for that device*.
# These mirror the real-world path schemes each vendor's firmware uses,
# so a client written against real hardware should behave the same way
# against these virtual streams.
BRAND_PROFILES = {
    "hikvision": {
        "display_name": "HIKVISION",
        "manufacturer": "Hikvision Digital Technology",
        "model": "DS-7608NI-K2",
        "main_path": "/Streaming/Channels/{ch}01",
        "sub_path": "/Streaming/Channels/{ch}02",
    },
    "dahua": {
        # NOTE: real Dahua devices use a QUERY STRING to pick channel/stream
        # (?channel=N&subtype=0|1). MediaMTX splits "?..." off as a query and
        # matches paths on what's left, so every channel/subtype combo would
        # collide onto the single literal path "cam/realmonitor" and fight
        # over the same publish slot. We keep the recognizable "cam/realmonitor"
        # prefix but push channel/subtype as path segments instead so each
        # channel gets its own real mount point through MediaMTX.
        "display_name": "Dahua",
        "manufacturer": "Zhejiang Dahua Technology",
        "model": "NVR4108HS-8P-4KS2",
        "main_path": "/cam/realmonitor/ch{ch}/main",
        "sub_path": "/cam/realmonitor/ch{ch}/sub",
    },
    "cpplus": {
        # Most CP Plus NVR/DVR ranges run Dahua-derived firmware and share
        # the same realmonitor scheme - same MediaMTX query-string caveat
        # as above applies here too.
        "display_name": "CP-PLUS",
        "manufacturer": "Aditya Infotech (CP Plus)",
        "model": "CP-UNR-2K41S2",
        "main_path": "/cam/realmonitor/ch{ch}/main",
        "sub_path": "/cam/realmonitor/ch{ch}/sub",
    },
    "uniview": {
        "display_name": "Uniview",
        "manufacturer": "Zhejiang Uniview Technologies",
        "model": "NVR301-08S3",
        "main_path": "/media/video{ch}",
        "sub_path": "/media/video{ch}/sub",
    },
    "generic": {
        # Common path scheme used by many generic/OEM ONVIF IP cameras.
        "display_name": "Generic-ONVIF",
        "manufacturer": "Generic",
        "model": "Generic-NVR",
        "main_path": "/h264/ch{ch}/main/av_stream",
        "sub_path": "/h264/ch{ch}/sub/av_stream",
    },
}

# Ensure logs directory exists
Path("logs").mkdir(exist_ok=True)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("VCCCTV-NVR")


def _normalize_video_files(video_files):
    """Turn each VIDEO_FILES entry into {'path': Path, 'brand': str|None}."""
    normalized = []
    for entry in video_files:
        if isinstance(entry, dict):
            raw_path = entry.get("path")
            brand = entry.get("brand")
            if brand and brand not in BRAND_PROFILES:
                logger.warning(f"Unknown brand '{brand}' for {raw_path}; will auto-assign instead.")
                brand = None
        else:
            raw_path = entry
            brand = None
        clean_path = Path(str(raw_path).strip("'\""))
        normalized.append({"path": clean_path, "brand": brand})
    return normalized


def _build_devices(video_files, brands, distribution_mode):
    """
    Returns a list of "device" dicts, one per simulated brand, each with
    its own channel list (main + optional sub stream per channel).
    """
    if isinstance(brands, str):
        brands = [brands]
    brands = [b for b in brands if b in BRAND_PROFILES] or ["generic"]

    entries = _normalize_video_files(video_files)

    # Split off entries pinned to a specific brand; distribute the rest.
    pinned = [e for e in entries if e["brand"] is not None]
    unpinned = [e for e in entries if e["brand"] is None]

    buckets = {b: [] for b in brands}
    for e in pinned:
        buckets[e["brand"]].append(e["path"])

    if distribution_mode == "split_even" and len(brands) > 1:
        chunk_size = max(1, -(-len(unpinned) // len(brands)))  # ceil division
        for i, b in enumerate(brands):
            chunk = unpinned[i * chunk_size:(i + 1) * chunk_size]
            buckets[b].extend(p["path"] for p in chunk)
    else:
        # round_robin (also used as the fallback for single-brand runs)
        for i, e in enumerate(unpinned):
            b = brands[i % len(brands)]
            buckets[b].append(e["path"])

    devices = []
    for idx, brand in enumerate(brands):
        video_paths = buckets[brand]
        if not video_paths:
            continue
        profile = BRAND_PROFILES[brand]
        http_port = HTTP_PORT_BASE + idx
        device_uuid = f"urn:uuid:{uuid.uuid4()}"
        device_name = f"VirtualCCTV-{profile['display_name']}"

        streams = []
        for i, video_path in enumerate(video_paths, start=1):
            channel = i
            main_path = profile["main_path"].format(ch=channel)
            main_push = f"rtsp://{USERNAME}:{PASSWORD}@{MEDIAMTX_IP}:{MEDIAMTX_PORT}{main_path}"
            main_public = f"rtsp://{USERNAME}:{PASSWORD}@{DEVICE_IP}:{MEDIAMTX_PORT}{main_path}"

            channel_streams = {
                "channel": channel,
                "video_path": video_path,
                "profile_name": f"Channel_{channel}",
                "main": {
                    "profile_token": f"Profile_{channel}_main",
                    "kind": "main",
                    "push_url": main_push,
                    "public_url": main_public,
                    "width": 1920,
                    "height": 1080,
                },
            }

            if ENABLE_SUBSTREAM:
                sub_path = profile["sub_path"].format(ch=channel)
                sub_push = f"rtsp://{USERNAME}:{PASSWORD}@{MEDIAMTX_IP}:{MEDIAMTX_PORT}{sub_path}"
                sub_public = f"rtsp://{USERNAME}:{PASSWORD}@{DEVICE_IP}:{MEDIAMTX_PORT}{sub_path}"
                channel_streams["sub"] = {
                    "profile_token": f"Profile_{channel}_sub",
                    "kind": "sub",
                    "push_url": sub_push,
                    "public_url": sub_public,
                    "width": SUBSTREAM_WIDTH,
                    "height": SUBSTREAM_HEIGHT,
                }

            streams.append(channel_streams)

        devices.append({
            "brand": brand,
            "display_name": profile["display_name"],
            "manufacturer": profile["manufacturer"],
            "model": profile["model"],
            "http_port": http_port,
            "device_uuid": device_uuid,
            "device_name": device_name,
            "streams": streams,
        })

    return devices


DEVICES = _build_devices(VIDEO_FILES, SIMULATED_BRANDS, DISTRIBUTION_MODE)

total_channels = sum(len(d["streams"]) for d in DEVICES)
print(f"Virtual CCTV NVR System v9.0 -- Multi-Brand ONVIF/RTSP Emulation "
      f"({len(DEVICES)} device(s), {total_channels} channel(s) configured)")
for d in DEVICES:
    logger.info(f"  Device [{d['display_name']}] on HTTP {d['http_port']} -> {len(d['streams'])} channel(s)")


class RTSPStreamer(threading.Thread):
    def __init__(self, video_path, push_url, ffmpeg_log_path, use_copy=True,
                 width=None, height=None, bitrate_kbps=None, fps=None):
        super().__init__()
        self.video_path = str(video_path)
        self.push_url = push_url
        self.proc = None
        self.ffmpeg_log_path = ffmpeg_log_path
        self.running = True
        self.use_copy = use_copy
        self.width = width
        self.height = height
        self.bitrate_kbps = bitrate_kbps
        self.fps = fps

    def run(self):
        if not os.path.exists(self.video_path):
            logger.error(f"Video file not found: {self.video_path}")
            return

        ffmpeg_cmd = [
            "ffmpeg", "-re", "-stream_loop", "-1", "-i", self.video_path
        ]

        if self.use_copy:
            # Zero-CPU stream copy (ideal for high channel-count main streams)
            ffmpeg_cmd.extend(["-c:v", "copy"])
        else:
            # Active re-encode - used for sub-streams (need downscaling) or
            # when source isn't H.264.
            width = self.width or 640
            height = self.height or 480
            bitrate = self.bitrate_kbps or 1000
            fps = self.fps or 25
            ffmpeg_cmd.extend([
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-preset", "veryfast",
                "-tune", "zerolatency",
                "-vf", f"scale={width}:{height}",
                "-r", str(fps),
                "-b:v", f"{bitrate}k",
            ])

        ffmpeg_cmd.extend([
            "-an",
            "-f", "rtsp",
            "-rtsp_transport", "tcp",
            self.push_url,
        ])

        logger.info(f"Starting FFmpeg push -> {self.push_url}")

        with open(self.ffmpeg_log_path, "w") as ffmpeg_log_file:
            self.proc = subprocess.Popen(
                ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=ffmpeg_log_file
            )

        while self.running and self.proc.poll() is None:
            time.sleep(1)

        if self.running:
            logger.warning(f"RTSP push process for {self.push_url} stopped unexpectedly. Check logs: {self.ffmpeg_log_path}")
        else:
            logger.info(f"RTSP push process for {self.push_url} stopped.")

    def stop(self):
        logger.info(f"Stopping RTSP push process for {self.push_url}...")
        self.running = False
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def make_onvif_handler(device):
    """Builds an ONVIFHandler class bound to a specific simulated device."""

    class ONVIFHandler(BaseHTTPRequestHandler):
        DEVICE = device

        def do_POST(self):
            if self.path != "/onvif/device_service":
                self.send_error(404, "Not Found")
                return
            content_len = int(self.headers.get('Content-Length', 0))
            req_xml = self.rfile.read(content_len).decode(errors="ignore")

            if any(x in req_xml for x in ["GetCapabilities", "GetDeviceInformation", "GetProfiles", "GetStreamUri", "GetSystemDateAndTime"]):
                self._reply_xml(self._soap_response(req_xml))
                return
            self.send_error(501, "Not Implemented")

        def _reply_xml(self, xml):
            data = xml.encode('utf-8')
            self.send_response(200)
            self.send_header("Content-Type", "application/soap+xml; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _soap_response(self, req_xml):
            dev = self.DEVICE
            xaddr = f"http://{DEVICE_IP}:{dev['http_port']}/onvif/device_service"

            if "GetCapabilities" in req_xml:
                return f'''<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body><tds:GetCapabilitiesResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl"><tds:Capabilities><tt:Device xmlns:tt="http://www.onvif.org/ver10/schema"><tt:XAddr>{xaddr}</tt:XAddr></tt:Device><tt:Media xmlns:tt="http://www.onvif.org/ver10/schema"><tt:XAddr>{xaddr}</tt:XAddr><tt:StreamingCapabilities><tt:RTP_TCP>true</tt:RTP_TCP></tt:StreamingCapabilities></tt:Media></tds:Capabilities></tds:GetCapabilitiesResponse></soap:Body></soap:Envelope>'''

            elif "GetDeviceInformation" in req_xml:
                return f'''<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body><tds:GetDeviceInformationResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl"><tds:Manufacturer>{dev['manufacturer']}</tds:Manufacturer><tds:Model>{dev['model']}</tds:Model><tds:FirmwareVersion>V9.0</tds:FirmwareVersion><tds:SerialNumber>NVR-2024-{dev['brand'].upper()}</tds:SerialNumber><tds:HardwareId>VCCTV-{dev['brand'].upper()}-01</tds:HardwareId></tds:GetDeviceInformationResponse></soap:Body></soap:Envelope>'''

            elif "GetProfiles" in req_xml:
                profiles_xml = ""
                for s in dev["streams"]:
                    for kind in ("main", "sub"):
                        if kind not in s:
                            continue
                        sc = s[kind]
                        profiles_xml += f'''<trt:Profiles token="{sc['profile_token']}" fixed="true"><tt:Name>{s['profile_name']}_{kind}</tt:Name><tt:VideoSourceConfiguration token="VSC_{s['channel']}_{kind}"><tt:Name>VSC_{s['channel']}_{kind}</tt:Name><tt:UseCount>1</tt:UseCount><tt:SourceToken>VST_{s['channel']}</tt:SourceToken><tt:Bounds x="0" y="0" width="{sc['width']}" height="{sc['height']}"/></tt:VideoSourceConfiguration><tt:VideoEncoderConfiguration token="VEC_{s['channel']}_{kind}"><tt:Name>VEC_{s['channel']}_{kind}</tt:Name><tt:UseCount>1</tt:UseCount><tt:Encoding>H264</tt:Encoding><tt:Resolution><tt:Width>{sc['width']}</tt:Width><tt:Height>{sc['height']}</tt:Height></tt:Resolution><tt:Quality>4</tt:Quality><tt:RateControl><tt:FrameRateLimit>25</tt:FrameRateLimit><tt:BitrateLimit>1000</tt:BitrateLimit></tt:RateControl></tt:VideoEncoderConfiguration></trt:Profiles>'''
                return f'''<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body><trt:GetProfilesResponse xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">{profiles_xml}</trt:GetProfilesResponse></soap:Body></soap:Envelope>'''

            elif "GetStreamUri" in req_xml:
                match = re.search(r'<trt:ProfileToken>([^<]+)</trt:ProfileToken>', req_xml)
                chosen = None
                if match:
                    token = match.group(1)
                    for s in dev["streams"]:
                        for kind in ("main", "sub"):
                            if kind in s and s[kind]["profile_token"] == token:
                                chosen = s[kind]
                                break
                        if chosen:
                            break
                if not chosen:
                    logger.warning("Client requested a stream URI without a valid ProfileToken. Defaulting to first channel's main stream.")
                    if dev["streams"]:
                        chosen = dev["streams"][0]["main"]

                url = chosen["public_url"] if chosen else ""
                return f'''<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body><trt:GetStreamUriResponse xmlns:trt="http://www.onvif.org/ver10/media/wsdl"><trt:MediaUri><tt:Uri xmlns:tt="http://www.onvif.org/ver10/schema">{url}</tt:Uri><tt:InvalidAfterConnect>false</tt:InvalidAfterConnect><tt:InvalidAfterReboot>false</tt:InvalidAfterReboot><tt:Timeout>PT60S</tt:Timeout></trt:MediaUri></trt:GetStreamUriResponse></soap:Body></soap:Envelope>'''

            elif "GetSystemDateAndTime" in req_xml:
                now = datetime.now(timezone.utc)
                return f'''<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body><tds:GetSystemDateAndTimeResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema"><tds:SystemDateAndTime><tt:UTCDateTime><tt:Time><tt:Hour>{now.hour}</tt:Hour><tt:Minute>{now.minute}</tt:Minute><tt:Second>{now.second}</tt:Second></tt:Time><tt:Date><tt:Year>{now.year}</tt:Year><tt:Month>{now.month}</tt:Month><tt:Day>{now.day}</tt:Day></tt:Date></tt:UTCDateTime></tds:SystemDateAndTime></tds:GetSystemDateAndTimeResponse></soap:Body></soap:Envelope>'''
            return ''

        def log_message(self, format, *args):
            # Disable default HTTP server logging to keep console clean
            return

    return ONVIFHandler


def wsdiscovery_responder(devices):
    """One UDP responder that answers Probe requests with a ProbeMatch
    for EVERY simulated device (so multi-brand setups all show up in an
    ONVIF discovery scan at once)."""
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    udp.bind(('', WS_DISCOVERY_PORT))
    udp.setsockopt(
        socket.IPPROTO_IP,
        socket.IP_ADD_MEMBERSHIP,
        socket.inet_aton("239.255.255.250") + socket.inet_aton("0.0.0.0")
    )
    logger.info(f"WS-Discovery responder started on UDP {WS_DISCOVERY_PORT} for {len(devices)} device(s)")
    while True:
        try:
            data, addr = udp.recvfrom(8192)
            if b"Probe" in data:
                try:
                    probe_id = re.search(r"<(?:\w+:)?MessageID[^>]*>([^<]+)</(?:\w+:)?MessageID>", data.decode()).group(1)
                except Exception:
                    probe_id = ""

                for dev in devices:
                    resp = f"""<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
                                xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"
                                xmlns:wsd="http://schemas.xmlsoap.org/ws/2005/04/discovery"
                                xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
                                <soap:Header>
                                    <wsa:MessageID>urn:uuid:{uuid.uuid4()}</wsa:MessageID>
                                    <wsa:RelatesTo>{probe_id}</wsa:RelatesTo>
                                    <wsa:To>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</wsa:To>
                                    <wsa:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/ProbeMatches</wsa:Action>
                                </soap:Header>
                                <soap:Body>
                                    <wsd:ProbeMatches>
                                        <wsd:ProbeMatch>
                                            <wsa:EndpointReference><wsa:Address>{dev['device_uuid']}</wsa:Address></wsa:EndpointReference>
                                            <wsd:Types>dn:NetworkVideoTransmitter</wsd:Types>
                                            <wsd:Scopes>onvif://www.onvif.org/name/{dev['device_name']}</wsd:Scopes>
                                            <wsd:XAddrs>http://{DEVICE_IP}:{dev['http_port']}/onvif/device_service</wsd:XAddrs>
                                            <wsd:MetadataVersion>1</wsd:MetadataVersion>
                                        </wsd:ProbeMatch>
                                    </wsd:ProbeMatches>
                                </soap:Body>
                            </soap:Envelope>""".encode()
                    udp.sendto(resp, addr)
        except Exception:
            break


if __name__ == "__main__":
    logger.info("=" * 70)
    logger.info(f"Virtual CCTV NVR System ({len(DEVICES)} device(s) | {total_channels} channel(s) | "
                f"Substream: {ENABLE_SUBSTREAM} | Main Stream Copy: {USE_STREAM_COPY})")
    logger.info("=" * 70)
    logger.info(f"Device IP: {DEVICE_IP}")
    logger.info(f"Pushing to MediaMTX at: {MEDIAMTX_IP}:{MEDIAMTX_PORT}")
    logger.info("-" * 70)

    if not DEVICES:
        logger.warning("No video files / brands configured! Exiting.")
        exit(1)

    streamer_threads = []
    http_servers = []

    for dev in DEVICES:
        logger.info(f"Device: {dev['display_name']} (HTTP {dev['http_port']}, UUID {dev['device_uuid']})")
        for s in dev["streams"]:
            logger.info(f"  Channel {s['channel']} ({s['profile_name']}) -> {s['video_path']}")

            main = s["main"]
            log_path = Path("logs") / f"ffmpeg_{dev['brand']}_ch{s['channel']}_main.log"
            streamer = RTSPStreamer(
                s["video_path"], main["push_url"], log_path,
                use_copy=USE_STREAM_COPY,
            )
            streamer.daemon = True
            streamer.start()
            streamer_threads.append(streamer)
            logger.info(f"    main -> {main['push_url']}")

            if "sub" in s:
                sub = s["sub"]
                sub_log_path = Path("logs") / f"ffmpeg_{dev['brand']}_ch{s['channel']}_sub.log"
                sub_streamer = RTSPStreamer(
                    s["video_path"], sub["push_url"], sub_log_path,
                    use_copy=False,  # sub-stream is always re-encoded (downscaled)
                    width=SUBSTREAM_WIDTH, height=SUBSTREAM_HEIGHT,
                    bitrate_kbps=SUBSTREAM_BITRATE_KBPS, fps=SUBSTREAM_FPS,
                )
                sub_streamer.daemon = True
                sub_streamer.start()
                streamer_threads.append(sub_streamer)
                logger.info(f"    sub  -> {sub['push_url']}")

    ws_thread = threading.Thread(target=wsdiscovery_responder, args=(DEVICES,), daemon=True)
    ws_thread.start()

    logger.info("Starting ONVIF HTTP server(s)...")
    for dev in DEVICES:
        handler_cls = make_onvif_handler(dev)
        server = HTTPServer(("0.0.0.0", dev["http_port"]), handler_cls)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        http_servers.append(server)
        logger.info(f"  ONVIF service for {dev['display_name']} listening on 0.0.0.0:{dev['http_port']}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\nShutting down the system...")
        for s in streamer_threads:
            s.stop()
        for server in http_servers:
            server.shutdown()
            server.server_close()
        logger.info("Shutdown complete.")