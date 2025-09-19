# virtual_cctv_auth_log.py

import cv2
import socket
import threading
import uuid
import subprocess
import time
import os
import logging
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
import re
import hashlib
from datetime import datetime, timezone

# --------- USER CONFIG ----------
DEVICE_IP = "192.168.1.100"
HTTP_PORT = 8080           # 80 or 8080 (root required for 80)
RTSP_PORT = 554
USERNAME = "admin"
PASSWORD = "admin@123"
INPUT_FILE = Path("videos/Shoplifting (3).mp4")
DEVICE_NAME = "VirtualCCTV"
PROFILE_TOKEN = "Profile_1"
REALM = "onvif"
DEVICE_UUID = f"urn:uuid:{uuid.uuid4()}"
WS_DISCOVERY_PORT = 3702
RTSP_MAIN = f"rtsp://{USERNAME}:{PASSWORD}@{DEVICE_IP}:{RTSP_PORT}/Streaming/Channels/101"
RTSP_SUB  = f"rtsp://{USERNAME}:{PASSWORD}@{DEVICE_IP}:{RTSP_PORT}/Streaming/Channels/102"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("VCCCTV")

print("Virtual CCTV System v1.0 -- Professional ONVIF/RTSP Emulation")
print("========================")
logger.info("Starting Virtual CCTV System...")

# --------- RTSP STREAMER ----------
class RTSPStreamer:
    def __init__(self, video_path, rtsp_url, fps=25, width=640, height=480):
        self.video_path = video_path
        self.rtsp_url = rtsp_url
        self.fps = fps
        self.width = width
        self.height = height
        self.proc = None
        self.running = False

    def run(self):
        if not os.path.exists(self.video_path):
            logger.error(f"Video file not found: {self.video_path}")
            return
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            logger.error(f"Cannot open video file: {self.video_path}")
            return
        ffmpeg_cmd = [
            "ffmpeg", "-re", "-stream_loop", "-1", "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{self.width}x{self.height}", "-r", str(self.fps), "-i", "-",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "baseline", "-level:v", "3.1",
            "-preset", "ultrafast", "-tune", "zerolatency", "-g", "30", "-f", "rtsp",
            "-rtsp_transport", "tcp", self.rtsp_url
        ]
        self.proc = subprocess.Popen(
            ffmpeg_cmd, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self.running = True
        frame_time = 1.0 / self.fps
        logger.info(f"Started RTSP stream: {self.rtsp_url}")
        while self.running:
            ret, frame = cap.read()
            if not ret:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            frame = cv2.resize(frame, (self.width, self.height))
            try:
                self.proc.stdin.write(frame.tobytes())
            except:
                break
            time.sleep(frame_time)
        try:
            self.proc.terminate()
        except:
            pass
        cap.release()
        logger.info(f"RTSP streaming stopped.")

# --------- ONVIF DEVICE SERVICE (with Digest Auth) ----------
def parse_http_digest(auth_header):
    vals = {}
    for item in re.findall(r'(\w+)="([^"]+)"', auth_header):
        vals[item[0]] = item[1]
    return vals

def digest_md5(data): return hashlib.md5(data.encode('utf-8')).hexdigest()

class DigestONVIFHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        logger.info(f"ONVIF POST from {self.client_address} {self.path}")
        if self.path == "/onvif/device_service":
            auth = self.headers.get("Authorization")
            content_len = int(self.headers.get('Content-Length', 0))
            req_xml = self.rfile.read(content_len).decode(errors="ignore")
            # ENFORCE Digest on all POSTs except probe/hello
            if not (auth and auth.startswith("Digest ")):
                logger.info(f"ONVIF Digest challenge sent.")
                return self._send_digest_challenge()
            vals = parse_http_digest(auth)
            if vals.get("username") != USERNAME or vals.get("realm") != REALM:
                logger.info(f"ONVIF Digest wrong username/realm, challenge sent.")
                return self._send_digest_challenge()
            ha1 = digest_md5(f"{USERNAME}:{REALM}:{PASSWORD}")
            ha2 = digest_md5(f"{self.command}:{vals['uri']}")
            valid_resp = digest_md5(f"{ha1}:{vals['nonce']}:{vals.get('nc','')}:{vals.get('cnonce','')}:{vals.get('qop','')}:{ha2}")
            if vals.get("response") != valid_resp:
                logger.info(f"ONVIF Digest mismatched response hash, challenge sent.")
                return self._send_digest_challenge()
            # DIGEST AUTH PASSED
            logger.info(f"ONVIF Digest success, responding to {self.client_address}")
            return self._reply_xml(self._soap_response(req_xml))
        else:
            self.send_error(404, "Not Found")

    def log_message(self, fmt, *a): return

    def _send_digest_challenge(self):
        nonce = hashlib.md5(os.urandom(8) + str(time.time()).encode()).hexdigest()
        self.send_response(401)
        self.send_header("WWW-Authenticate",
            f'Digest realm="{REALM}", nonce="{nonce}", algorithm=MD5, qop="auth"')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _reply_xml(self, xml):
        data = xml.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/soap+xml; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _soap_response(self, req_xml):
        logger.info(f"ONVIF SOAP: {req_xml[:100]}...")  # Print first 100 chars for debug
        if "GetCapabilities" in req_xml:
            xaddr = f"http://{DEVICE_IP}:{HTTP_PORT}/onvif/device_service"
            return f"""<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>
<tds:GetCapabilitiesResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
<tds:Capabilities>
<tt:Device xmlns:tt="http://www.onvif.org/ver10/schema"><tt:XAddr>{xaddr}</tt:XAddr></tt:Device>
<tt:Media xmlns:tt="http://www.onvif.org/ver10/schema"><tt:XAddr>{xaddr}</tt:XAddr>
<tt:StreamingCapabilities>
<tt:RTPMulticast>false</tt:RTPMulticast><tt:RTP_TCP>true</tt:RTP_TCP><tt:RTP_RTSP_TCP>true</tt:RTP_RTSP_TCP>
</tt:StreamingCapabilities></tt:Media>
</tds:Capabilities></tds:GetCapabilitiesResponse></soap:Body></soap:Envelope>"""
        if "GetDeviceInformation" in req_xml:
            return f"""<?xml version="1.0"?><soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>
<tds:GetDeviceInformationResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
<tds:Manufacturer>Hikvision</tds:Manufacturer>
<tds:Model>{DEVICE_NAME}</tds:Model>
<tds:FirmwareVersion>V1.2.3</tds:FirmwareVersion>
<tds:SerialNumber>1234567890</tds:SerialNumber>
<tds:HardwareId>VIRTUALID</tds:HardwareId>
</tds:GetDeviceInformationResponse></soap:Body></soap:Envelope>"""
        if "GetProfiles" in req_xml:
            return f"""<?xml version="1.0"?><soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>
<trt:GetProfilesResponse xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
<trt:Profiles token="Profile_1" fixed="true">
<tt:Name>MainStream</tt:Name>
<tt:VideoSourceConfiguration token="VideoSourceConfig_1">
<tt:Name>VideoSource_Main</tt:Name>
<tt:UseCount>1</tt:UseCount>
<tt:SourceToken>VideoSourceToken_1</tt:SourceToken>
<tt:Bounds x="0" y="0" width="640" height="480"/>
</tt:VideoSourceConfiguration>
<tt:VideoEncoderConfiguration token="EncoderConfig_1">
<tt:Name>Encoder_Main</tt:Name>
<tt:UseCount>1</tt:UseCount>
<tt:Encoding>H264</tt:Encoding>
<tt:Resolution><tt:Width>640</tt:Width><tt:Height>480</tt:Height></tt:Resolution>
<tt:Quality>4</tt:Quality>
<tt:RateControl><tt:FrameRateLimit>25</tt:FrameRateLimit><tt:BitrateLimit>1000</tt:BitrateLimit></tt:RateControl>
</tt:VideoEncoderConfiguration></trt:Profiles>
</trt:GetProfilesResponse></soap:Body></soap:Envelope>"""
        if "GetStreamUri" in req_xml:
            return f"""<?xml version="1.0"?><soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>
<trt:GetStreamUriResponse xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
<trt:MediaUri>
<tt:Uri xmlns:tt="http://www.onvif.org/ver10/schema">{RTSP_MAIN}</tt:Uri>
<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>
<tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>
<tt:Timeout>PT60S</tt:Timeout>
</trt:MediaUri>
</trt:GetStreamUriResponse></soap:Body></soap:Envelope>"""
        if "GetSystemDateAndTime" in req_xml:
            now = datetime.now(timezone.utc)
            return f"""<?xml version="1.0"?><soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>
<tds:GetSystemDateAndTimeResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
<tds:SystemDateAndTime><tt:UTCDateTime>
<tt:Time><tt:Hour>{now.hour}</tt:Hour><tt:Minute>{now.minute}</tt:Minute><tt:Second>{now.second}</tt:Second></tt:Time>
<tt:Date><tt:Year>{now.year}</tt:Year><tt:Month>{now.month}</tt:Month><tt:Day>{now.day}</tt:Day></tt:Date>
</tt:UTCDateTime></tds:SystemDateAndTime></tds:GetSystemDateAndTimeResponse></soap:Body></soap:Envelope>"""
        return f"""<?xml version="1.0"?><soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">
<soap:Body><soap:Fault>
<soap:Code><soap:Value>soap:Sender</soap:Value></soap:Code>
<soap:Reason><soap:Text>Not Supported</soap:Text></soap:Reason>
</soap:Fault></soap:Body></soap:Envelope>"""

# ---- ONVIF WS-Discovery MULTICAST ----
def wsdiscovery_responder():
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try: udp.setsockopt(socket.SOL_SOCKET, 25, str('en0').encode())
    except: pass
    udp.bind(('', WS_DISCOVERY_PORT))
    mreq = socket.inet_aton("239.255.255.250") + socket.inet_aton('0.0.0.0')
    udp.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    logger.info("WS-Discovery responder started on UDP 3702")
    while True:
        data, addr = udp.recvfrom(8192)
        if b'Probe' in data:
            probe_id = ""
            try:
                probe_id_re = re.search(r'<(?:\w+:)?MessageID[^>]*>([^<]+)</(?:\w+:)?MessageID>', data.decode(errors="ignore"))
                if probe_id_re: probe_id = probe_id_re.group(1)
            except: pass
            logger.info(f"WS-Discovery probe from {addr[0]} (MessageID: {probe_id})")
            response = f"""<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
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
<wsa:EndpointReference>
<wsa:Address>{DEVICE_UUID}</wsa:Address>
</wsa:EndpointReference>
<wsd:Types>dn:NetworkVideoTransmitter</wsd:Types>
<wsd:Scopes>
onvif://www.onvif.org/type/NetworkVideoTransmitter
onvif://www.onvif.org/type/video_encoder
onvif://www.onvif.org/hardware/Hikvision
onvif://www.onvif.org/Profile/Streaming
onvif://www.onvif.org/location/1
</wsd:Scopes>
<wsd:XAddrs>http://{DEVICE_IP}:{HTTP_PORT}/onvif/device_service</wsd:XAddrs>
<wsd:MetadataVersion>1</wsd:MetadataVersion>
</wsd:ProbeMatch>
</wsd:ProbeMatches>
</soap:Body></soap:Envelope>"""
            udp.sendto(response.encode(), addr)
            logger.info(f"Sent WS-Discovery response to {addr[0]}")

if __name__ == "__main__":
    # System startup prints
    logger.info("="*59)
    logger.info("Virtual CCTV System Started Successfully!")
    logger.info("="*59)
    logger.info(f"Device IP: {DEVICE_IP}")
    logger.info(f"HTTP Port: {HTTP_PORT}")
    logger.info(f"RTSP Port: {RTSP_PORT}")
    logger.info(f"Username: {USERNAME}")
    logger.info(f"Password: {PASSWORD}")
    logger.info("RTSP URLs:")
    logger.info(f"  Main: {RTSP_MAIN}")
    logger.info(f"  Sub : {RTSP_SUB}")
    logger.info(f"ONVIF: http://{DEVICE_IP}:{HTTP_PORT}/onvif/device_service")

    # Start RTSP streamer and WS-Discovery
    streamer = RTSPStreamer(str(INPUT_FILE), RTSP_MAIN, fps=25, width=640, height=480)
    threading.Thread(target=streamer.run, daemon=True).start()
    threading.Thread(target=wsdiscovery_responder, daemon=True).start()

    # Start ONVIF server—digest auth for POSTs (discovery always works)
    onvif_server = HTTPServer(('0.0.0.0', HTTP_PORT), DigestONVIFHandler)
    onvif_server.serve_forever()
