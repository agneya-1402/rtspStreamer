import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
import re

DEVICE_NAME = "PythonSimCam"
RTSP_URL = "rtsp://192.168.1.111:8554/stream1"
SERVER_IP = "192.168.1.111"
HTTP_PORT = 10000
XADDR = f"http://{SERVER_IP}:{HTTP_PORT}/onvif/device_service"
DEVICE_UUID = f"urn:uuid:{uuid.uuid4()}"

def make_probe_response(relatesto):
    probe_uuid = f"urn:uuid:{uuid.uuid4()}"
    response = f'''<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope" xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing" xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
 <SOAP-ENV:Header>
  <wsa:MessageID>{probe_uuid}</wsa:MessageID>
  <wsa:RelatesTo>{relatesto}</wsa:RelatesTo>
  <wsa:To SOAP-ENV__mustUnderstand="1">http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</wsa:To>
  <wsa:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/ProbeMatches</wsa:Action>
 </SOAP-ENV:Header>
 <SOAP-ENV:Body>
  <dn:ProbeMatches>
   <dn:ProbeMatch>
    <wsa:EndpointReference>
     <wsa:Address>{DEVICE_UUID}</wsa:Address>
    </wsa:EndpointReference>
    <dn:Types>dn:NetworkVideoTransmitter</dn:Types>
    <dn:Scopes>onvif://www.onvif.org/type/video_encoder onvif://www.onvif.org/name/{DEVICE_NAME} rtsp://192.168.1.111:8554/stream1</dn:Scopes>
    <dn:XAddrs>{XADDR}</dn:XAddrs>
    <dn:MetadataVersion>1</dn:MetadataVersion>
   </dn:ProbeMatch>
  </dn:ProbeMatches>
 </SOAP-ENV:Body>
</SOAP-ENV:Envelope>'''
    # Use double underscores for mustUnderstand since colons in attribute names are not supported.
    return response.replace("SOAP-ENV__mustUnderstand", "SOAP-ENV:mustUnderstand").encode()


def extract_probe_id(xml):
    # Accept both <MessageID>...</MessageID> and <wsa:MessageID>...</wsa:MessageID>
    match = re.search(r"<(?:\w+:)?MessageID>(.*?)</(?:\w+:)?MessageID>", xml)

    return match.group(1) if match else ""


def wsdiscovery_responder():
    UDP_IP = "239.255.255.250"
    UDP_PORT = 3702
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('', UDP_PORT))
    mreq = socket.inet_aton(UDP_IP) + socket.inet_aton("0.0.0.0")
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    print("Listening for WS-Discovery probes on UDP 3702...")
    while True:
        data, addr = sock.recvfrom(8192)
        if b'Probe' in data:
            try:
                probe_str = data.decode(errors="ignore")
                probe_id = extract_probe_id(probe_str)
                print("Received probe from", addr, "MessageID:", probe_id)
                messageid_match = re.search(r"<wsa:MessageID>(.*?)</wsa:MessageID>", probe_str)
                probe_id = messageid_match.group(1) if messageid_match else ""
                print("Raw probe XML:", probe_str)
                resp_xml = make_probe_response(probe_id)
                sock.sendto(resp_xml, addr)
                print("Sent WS-Discovery probe response.")
                
            except Exception as e:
                print("Error parsing probe:", e)

class SimpleONVIFHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('Content-Length'))
        request_xml = self.rfile.read(length)
        # Identify request by method name in XML payload
        if b'GetCapabilities' in request_xml:
            # Respond with ONVIF Media service XAddr
            response_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
  <s:Body>
    <tds:GetCapabilitiesResponse xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
      <tds:Capabilities>
        <tt:Media xmlns:tt="http://www.onvif.org/ver10/schema">
          <tt:XAddr>http://%s:%d/onvif/device_service</tt:XAddr>
        </tt:Media>
      </tds:Capabilities>
    </tds:GetCapabilitiesResponse>
  </s:Body>
</s:Envelope>''' % (SERVER_IP, HTTP_PORT)
        elif b'GetProfiles' in request_xml:
            # Define a fake video profile for streaming (Profile S is sufficient)
            response_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
  <s:Body>
    <trt:GetProfilesResponse xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
      <trt:Profiles fixed="true" token="Profile_1">
        <tt:Name xmlns:tt="http://www.onvif.org/ver10/schema">DefaultProfile</tt:Name>
      </trt:Profiles>
    </trt:GetProfilesResponse>
  </s:Body>
</s:Envelope>'''
        elif b'GetStreamUri' in request_xml:
            # Respond with your RTSP stream URI
            response_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">
  <s:Body>
    <trt:GetStreamUriResponse xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
      <trt:MediaUri>
        <tt:Uri xmlns:tt="http://www.onvif.org/ver10/schema">%s</tt:Uri>
        <tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>
        <tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>
        <tt:Timeout>PT60S</tt:Timeout>
      </trt:MediaUri>
    </trt:GetStreamUriResponse>
  </s:Body>
</s:Envelope>''' % RTSP_URL
        else:
            # Unknown ONVIF request
            response_xml = '''<?xml version="1.0" encoding="UTF-8"?><Envelope xmlns="http://www.w3.org/2003/05/soap-envelope"><Body>OK</Body></Envelope>'''
        self.send_response(200)
        self.send_header("Content-Type", "application/soap+xml")
        self.send_header("Content-Length", str(len(response_xml.encode())))
        self.end_headers()
        self.wfile.write(response_xml.encode())

    def log_message(self, fmt, *args): return


def run_http_server():
    server = HTTPServer(('0.0.0.0', HTTP_PORT), SimpleONVIFHandler)
    print(f"ONVIF DeviceService running at http://{SERVER_IP}:{HTTP_PORT}/onvif/device_service")
    server.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=wsdiscovery_responder, daemon=True).start()
    run_http_server()
