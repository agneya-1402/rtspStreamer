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
DEVICE_IP = "192.168.1.10"  # Your machine's LAN IP address
HTTP_PORT = 8080             # ONVIF HTTP port

MEDIAMTX_IP = DEVICE_IP
MEDIAMTX_PORT = 554          # MediaMTX RTSP port

USERNAME = "admin"
PASSWORD = "admin@123"
DEVICE_NAME = "VirtualCCTV-NVR"
DEVICE_UUID = f"urn:uuid:{uuid.uuid4()}"
WS_DISCOVERY_PORT = 3702

# CRITICAL FOR 50+ STREAMS: 
# Set to True to copy codecs without re-encoding (uses virtually 0% CPU per stream).
# Set to False only if your videos are not H.264 and require active re-encoding.
USE_STREAM_COPY = True 

# Add as many paths as you want (1, 2, 50, or more).
# The system will automatically generate a unique channel and ONVIF profile for each.
VIDEO_FILES = [
    Path("videos/entry_1.mp4"),
    Path("videos/build/3.mp4"),
    Path("videos/entry_exit_4.mp4"),
    Path("videos/entry_exit_1.mp4"),
    Path("videos/2_people.mp4"),
    Path("videos/4_people.mp4"),
    Path("videos/12_people.mp4"),
    Path("videos/build/20260727_153316_Camera_192.168.0.114_-_Ch1.mp4"),
    Path("videos/build/20260727_153514_Camera_192.168.0.121_-_Ch1.mp4"),
    Path("videos/build/20260727_153741_Camera_192.168.0.122_-_Ch0.mp4"),
    Path("videos/build/20260727_155148_Camera_192.168.0.127_-_Ch1.mp4"),
    Path("videos/build/20260727_160408_Camera_192.168.0.122_-_Ch0.mp4"),
    Path("videos/build/20260727_163210_Camera_192.168.0.114_-_Ch1.mp4"),
    Path("videos/build/20260727_164459_Camera_192.168.0.114_-_Ch1.mp4"),
    Path("videos/build/20260727_174058_Camera_192.168.0.122_-_Ch0.mp4"),

    # Add more paths here...
]
# =====================================================================

# Ensure logs directory exists
Path("logs").mkdir(exist_ok=True)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("VCCCTV-NVR")

# Dynamically build RTSP streams based on the length of VIDEO_FILES
STREAMS = []
for i, raw_path in enumerate(VIDEO_FILES, start=1):
    # Clean up any accidental wrapping quotes in strings
    clean_path = Path(str(raw_path).strip("'\""))
    channel = i
    channel_code = (channel * 100) + 1  # Generates 101, 201, 301, ..., 5001, etc.
    rtsp_path = f"/Streaming/Channels/{channel_code}"
    
    push_url = f"rtsp://{USERNAME}:{PASSWORD}@{MEDIAMTX_IP}:{MEDIAMTX_PORT}{rtsp_path}"
    public_url = f"rtsp://{USERNAME}:{PASSWORD}@{DEVICE_IP}:{MEDIAMTX_PORT}{rtsp_path}"
    
    STREAMS.append({
        "channel": channel,
        "video_path": clean_path,
        "profile_token": f"Profile_{channel}",
        "profile_name": f"Channel_{channel}",
        "push_url": push_url,
        "public_url": public_url,
    })

print(f"Virtual CCTV NVR System v8.0 -- ONVIF/RTSP Emulation ({len(STREAMS)} Channels Configured)")
logger.info(f"Starting Virtual CCTV NVR System with {len(STREAMS)} dynamic streams...")

class RTSPStreamer(threading.Thread):
    def __init__(self, video_path, push_url, ffmpeg_log_path, use_copy=True):
        super().__init__()
        self.video_path = str(video_path)
        self.push_url = push_url
        self.proc = None
        self.ffmpeg_log_path = ffmpeg_log_path
        self.running = True
        self.use_copy = use_copy

    def run(self):
        if not os.path.exists(self.video_path):
            logger.error(f"Video file not found: {self.video_path}")
            return

        # Base FFmpeg command
        ffmpeg_cmd = [
            "ffmpeg", "-re", "-stream_loop", "-1", "-i", self.video_path
        ]

        if self.use_copy:
            # Zero-CPU stream copy (Ideal for 50+ streams of MP4/H264 files)
            ffmpeg_cmd.extend(["-c:v", "copy"])
        else:
            # Active re-encoding (CPU intensive)
            ffmpeg_cmd.extend([
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-preset", "veryfast",
                "-tune", "zerolatency",
                "-s", "640x480",
                "-b:v", "1000k"
            ])

        ffmpeg_cmd.extend([
            "-an",
            "-f", "rtsp",
            "-rtsp_transport", "tcp",
            self.push_url,
        ])

        logger.info(f"Starting FFmpeg push for Channel -> {self.push_url}")

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

class ONVIFHandler(BaseHTTPRequestHandler):
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
        if "GetCapabilities" in req_xml:
            xaddr = f"http://{DEVICE_IP}:{HTTP_PORT}/onvif/device_service"
            return f'''<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body><tds:GetCapabilitiesResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl"><tds:Capabilities><tt:Device xmlns:tt="http://www.onvif.org/ver10/schema"><tt:XAddr>{xaddr}</tt:XAddr></tt:Device><tt:Media xmlns:tt="http://www.onvif.org/ver10/schema"><tt:XAddr>{xaddr}</tt:XAddr><tt:StreamingCapabilities><tt:RTP_TCP>true</tt:RTP_TCP></tt:StreamingCapabilities></tt:Media></tds:Capabilities></tds:GetCapabilitiesResponse></soap:Body></soap:Envelope>'''
        elif "GetDeviceInformation" in req_xml:
            return f'''<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body><tds:GetDeviceInformationResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl"><tds:Manufacturer>VirtualCCTV</tds:Manufacturer><tds:Model>{DEVICE_NAME}</tds:Model><tds:FirmwareVersion>V8.0</tds:FirmwareVersion><tds:SerialNumber>NVR-2024</tds:SerialNumber><tds:HardwareId>VCCTV-NVR-01</tds:HardwareId></tds:GetDeviceInformationResponse></soap:Body></soap:Envelope>'''
        elif "GetProfiles" in req_xml:
            profiles_xml = ""
            for s in STREAMS:
                profiles_xml += f'''<trt:Profiles token="{s['profile_token']}" fixed="true"><tt:Name>{s['profile_name']}</tt:Name><tt:VideoSourceConfiguration token="VSC_{s['channel']}"><tt:Name>VSC_{s['channel']}</tt:Name><tt:UseCount>1</tt:UseCount><tt:SourceToken>VST_{s['channel']}</tt:SourceToken><tt:Bounds x="0" y="0" width="640" height="480"/></tt:VideoSourceConfiguration><tt:VideoEncoderConfiguration token="VEC_{s['channel']}"><tt:Name>VEC_{s['channel']}</tt:Name><tt:UseCount>1</tt:UseCount><tt:Encoding>H264</tt:Encoding><tt:Resolution><tt:Width>640</tt:Width><tt:Height>480</tt:Height></tt:Resolution><tt:Quality>4</tt:Quality><tt:RateControl><tt:FrameRateLimit>25</tt:FrameRateLimit><tt:BitrateLimit>1000</tt:BitrateLimit></tt:RateControl></tt:VideoEncoderConfiguration></trt:Profiles>'''
            return f'''<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body><trt:GetProfilesResponse xmlns:trt="http://www.onvif.org/ver10/media/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">{profiles_xml}</trt:GetProfilesResponse></soap:Body></soap:Envelope>'''
        elif "GetStreamUri" in req_xml:
            match = re.search(r'<trt:ProfileToken>([^<]+)</trt:ProfileToken>', req_xml)
            stream = STREAMS[0] if STREAMS else None
            if match and STREAMS:
                token = match.group(1)
                stream = next((s for s in STREAMS if s['profile_token'] == token), STREAMS[0])
            else:
                logger.warning("Client requested a stream URI without a valid ProfileToken. Defaulting to first stream.")
            
            url = stream['public_url'] if stream else ""
            return f'''<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body><trt:GetStreamUriResponse xmlns:trt="http://www.onvif.org/ver10/media/wsdl"><trt:MediaUri><tt:Uri xmlns:tt="http://www.onvif.org/ver10/schema">{url}</tt:Uri><tt:InvalidAfterConnect>false</tt:InvalidAfterConnect><tt:InvalidAfterReboot>false</tt:InvalidAfterReboot><tt:Timeout>PT60S</tt:Timeout></trt:MediaUri></trt:GetStreamUriResponse></soap:Body></soap:Envelope>'''
        elif "GetSystemDateAndTime" in req_xml:
            now = datetime.now(timezone.utc)
            return f'''<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body><tds:GetSystemDateAndTimeResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema"><tds:SystemDateAndTime><tt:UTCDateTime><tt:Time><tt:Hour>{now.hour}</tt:Hour><tt:Minute>{now.minute}</tt:Minute><tt:Second>{now.second}</tt:Second></tt:Time><tt:Date><tt:Year>{now.year}</tt:Year><tt:Month>{now.month}</tt:Month><tt:Day>{now.day}</tt:Day></tt:Date></tt:UTCDateTime></tds:SystemDateAndTime></tds:GetSystemDateAndTimeResponse></soap:Body></soap:Envelope>'''
        return ''

    def log_message(self, format, *args):
        # Disable default HTTP server logging to keep console clean
        return

def wsdiscovery_responder():
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    udp.bind(('', WS_DISCOVERY_PORT))
    udp.setsockopt(
        socket.IPPROTO_IP,
        socket.IP_ADD_MEMBERSHIP,
        socket.inet_aton("239.255.255.250") + socket.inet_aton("0.0.0.0")
    )
    logger.info(f"WS-Discovery responder started on UDP {WS_DISCOVERY_PORT}")
    while True:
        try:
            data, addr = udp.recvfrom(8192)
            if b"Probe" in data:
                try:
                    probe_id = re.search(r"<(?:\w+:)?MessageID[^>]*>([^<]+)</(?:\w+:)?MessageID>", data.decode()).group(1)
                except Exception:
                    probe_id = ""
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
                                        <wsa:EndpointReference><wsa:Address>{DEVICE_UUID}</wsa:Address></wsa:EndpointReference>
                                        <wsd:Types>dn:NetworkVideoTransmitter</wsd:Types>
                                        <wsd:Scopes>onvif://www.onvif.org/name/{DEVICE_NAME}</wsd:Scopes>
                                        <wsd:XAddrs>http://{DEVICE_IP}:{HTTP_PORT}/onvif/device_service</wsd:XAddrs>
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
    logger.info(f"Virtual CCTV NVR System ({len(STREAMS)} Channels | Stream Copy: {USE_STREAM_COPY})")
    logger.info("=" * 70)
    logger.info(f"Device IP: {DEVICE_IP}")
    logger.info(f"ONVIF HTTP Port: {HTTP_PORT}")
    logger.info(f"Pushing to MediaMTX at: {MEDIAMTX_IP}:{MEDIAMTX_PORT}")
    logger.info("-" * 70)

    if not STREAMS:
        logger.warning("No video files configured in VIDEO_FILES! Exiting.")
        exit(1)

    streamer_threads = []
    for stream in STREAMS:
        logger.info(f"Preparing Channel {stream['channel']} ({stream['profile_name']}) -> {stream['video_path']}")
        log_path = Path("logs") / f"ffmpeg_channel_{stream['channel']}.log"
        streamer = RTSPStreamer(stream['video_path'], stream['push_url'], log_path, use_copy=USE_STREAM_COPY)
        streamer.daemon = True
        streamer.start()
        streamer_threads.append(streamer)

    ws_thread = threading.Thread(target=wsdiscovery_responder, daemon=True)
    ws_thread.start()

    logger.info("Starting ONVIF HTTP server...")
    server = HTTPServer(("0.0.0.0", HTTP_PORT), ONVIFHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("\nShutting down the system...")
        for s in streamer_threads:
            s.stop()
        server.server_close()
        logger.info("Shutdown complete.")