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
from datetime import datetime, timezone


# ----------- USER CONFIG -----------
DEVICE_IP = "192.168.1.100"
HTTP_PORT = 8080
RTSP_PORT = 554
USERNAME = "admin"
PASSWORD = "admin@123"
INPUT_FILE = Path("videos/Shoplifting (3).mp4")
DEVICE_NAME = "VirtualCCTV"
PROFILE_TOKEN = "Profile_1"
DEVICE_UUID = f"urn:uuid:{uuid.uuid4()}"
WS_DISCOVERY_PORT = 3702

RTSP_MAIN = f"rtsp://{USERNAME}:{PASSWORD}@{DEVICE_IP}:{RTSP_PORT}/Streaming/Channels/101"


# --------- Logging Setup ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("VCCCTV")

print("Virtual CCTV System v4.0 -- ONVIF/RTSP Emulation compatible with Kotlin client")
logger.info("Starting Virtual CCTV System...")


# --------- RTSP Streamer ----------
class RTSPStreamer(threading.Thread):
    def __init__(self, video_path, rtsp_url, fps=25, width=640, height=480):
        super().__init__()
        self.video_path = video_path
        self.rtsp_url = rtsp_url
        self.fps = fps
        self.width = width
        self.height = height
        self.proc = None
        self.running = True

    def run(self):
        if not os.path.exists(self.video_path):
            logger.error(f"Video file not found: {self.video_path}")
            return
        cap = cv2.VideoCapture(str(self.video_path))
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
            ffmpeg_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

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
            except Exception as e:
                logger.error(f"RTSP streaming stopped: {e}")
                break
            time.sleep(frame_time)

        try:
            self.proc.terminate()
        except Exception:
            pass
        cap.release()
        logger.info("RTSP streaming stopped.")


# --------- ONVIF HTTP Handler WITHOUT Digest Authentication (discovery compatible) ---------

class ONVIFHandler(BaseHTTPRequestHandler):

    def do_POST(self):
        logger.info(f"ONVIF POST from {self.client_address[0]} {self.path}")
        if self.path != "/onvif/device_service":
            self.send_error(404, "Not Found")
            return

        content_len = int(self.headers.get('Content-Length', 0))
        req_xml = self.rfile.read(content_len).decode(errors="ignore")

        # Handle discovery and media queries with no auth required
        if ("GetCapabilities" in req_xml or
            "GetDeviceInformation" in req_xml or
            "GetProfiles" in req_xml or
            "GetStreamUri" in req_xml or
            "GetSystemDateAndTime" in req_xml):
            response_xml = self._soap_response(req_xml)
            self._reply_xml(response_xml)
            return

        # Otherwise, respond with 501 Not Implemented as default
        self.send_error(501, "Not Implemented")

    def _reply_xml(self, xml):
        data = xml.encode('utf-8')
        self.send_response(200)
        self.send_header("Content-Type", "application/soap+xml; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _soap_response(self, req_xml):
        logger.debug(f"Received SOAP req: {req_xml[:150]}...")
        if "GetCapabilities" in req_xml:
            xaddr = f"http://{DEVICE_IP}:{HTTP_PORT}/onvif/device_service"
            return f'''<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>
<tds:GetCapabilitiesResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
<tds:Capabilities>
<tt:Device xmlns:tt="http://www.onvif.org/ver10/schema"><tt:XAddr>{xaddr}</tt:XAddr></tt:Device>
<tt:Media xmlns:tt="http://www.onvif.org/ver10/schema">
<tt:XAddr>{xaddr}</tt:XAddr>
<tt:StreamingCapabilities>
<tt:RTPMulticast>false</tt:RTPMulticast>
<tt:RTP_TCP>true</tt:RTP_TCP>
<tt:RTP_RTSP_TCP>true</tt:RTP_RTSP_TCP>
</tt:StreamingCapabilities>
</tt:Media>
</tds:Capabilities>
</tds:GetCapabilitiesResponse></soap:Body></soap:Envelope>'''

        elif "GetDeviceInformation" in req_xml:
            return f'''<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>
<tds:GetDeviceInformationResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
<tds:Manufacturer>Hikvision</tds:Manufacturer>
<tds:Model>{DEVICE_NAME}</tds:Model>
<tds:FirmwareVersion>V1.2.3</tds:FirmwareVersion>
<tds:SerialNumber>1234567890</tds:SerialNumber>
<tds:HardwareId>VIRTUALID</tds:HardwareId>
</tds:GetDeviceInformationResponse></soap:Body></soap:Envelope>'''

        elif "GetProfiles" in req_xml:
            return f'''<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>
<trt:GetProfilesResponse xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
<trt:Profiles token="{PROFILE_TOKEN}" fixed="true">
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
</tt:VideoEncoderConfiguration>
</trt:Profiles>
</soap:Body></soap:Envelope>'''

        elif "GetStreamUri" in req_xml:
            return f'''<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>
<trt:GetStreamUriResponse xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
<trt:MediaUri>
<tt:Uri xmlns:tt="http://www.onvif.org/ver10/schema">{RTSP_MAIN}</tt:Uri>
<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>
<tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>
<tt:Timeout>PT60S</tt:Timeout>
</trt:MediaUri>
</trt:GetStreamUriResponse></soap:Body></soap:Envelope>'''

        elif "GetSystemDateAndTime" in req_xml:
            now = datetime.now(timezone.utc)
            return f'''<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">
<soap:Body>
<tds:GetSystemDateAndTimeResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:tt="http://www.onvif.org/ver10/schema">
<tds:SystemDateAndTime><tt:UTCDateTime>
<tt:Time><tt:Hour>{now.hour}</tt:Hour><tt:Minute>{now.minute}</tt:Minute><tt:Second>{now.second}</tt:Second></tt:Time>
<tt:Date><tt:Year>{now.year}</tt:Year><tt:Month>{now.month}</tt:Month><tt:Day>{now.day}</tt:Day></tt:Date>
</tt:UTCDateTime></tds:SystemDateAndTime>
</tds:GetSystemDateAndTimeResponse>
</soap:Body>
</soap:Envelope>'''

        # Default fallback for unsupported operations
        return f'''<?xml version="1.0"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">
<soap:Body>
<soap:Fault>
<soap:Code><soap:Value>soap:Sender</soap:Value></soap:Code>
<soap:Reason><soap:Text>Not Supported</soap:Text></soap:Reason>
</soap:Fault>
</soap:Body>
</soap:Envelope>'''

    # Override log_message to suppress default server console logs
    def log_message(self, format, *args):
        return


# --------- WS-Discovery Responder ---------
def wsdiscovery_responder():
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        udp.setsockopt(socket.SOL_SOCKET, 25, str('en0').encode())
    except Exception:
        pass
    udp.bind(('', WS_DISCOVERY_PORT))
    mreq = socket.inet_aton("239.255.255.250") + socket.inet_aton("0.0.0.0")
    udp.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    logger.info("WS-Discovery responder started on UDP 3702")

    while True:
        data, addr = udp.recvfrom(8192)
        if b"Probe" in data:
            probe_id = ""
            try:
                probe_id_re = re.search(
                    r"<(?:\w+:)?MessageID[^>]*>([^<]+)</(?:\w+:)?MessageID>",
                    data.decode(errors="ignore"),
                )
                if probe_id_re:
                    probe_id = probe_id_re.group(1)
            except Exception:
                pass

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


# --------- System Entry Point ---------
if __name__ == "__main__":
    logger.info("=" * 59)
    logger.info("Virtual CCTV System Started (Kotlin Compatible ONVIF + RTSP)")
    logger.info("=" * 59)
    logger.info(f"Device IP: {DEVICE_IP}")
    logger.info(f"HTTP Port: {HTTP_PORT}")
    logger.info(f"RTSP Port: {RTSP_PORT}")
    logger.info(f"Username: {USERNAME}")
    logger.info(f"Password: {PASSWORD}")
    logger.info(f"RTSP streaming URL: {RTSP_MAIN}")
    logger.info(f"ONVIF URL: http://{DEVICE_IP}:{HTTP_PORT}/onvif/device_service")

    streamer = RTSPStreamer(str(INPUT_FILE), RTSP_MAIN, fps=25, width=640, height=480)
    streamer.daemon = True
    streamer.start()

    threading.Thread(target=wsdiscovery_responder, daemon=True).start()

    server = HTTPServer(("0.0.0.0", HTTP_PORT), ONVIFHandler)
    server.serve_forever()
