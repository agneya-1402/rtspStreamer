import cv2
import concurrent.futures
import time
import urllib.parse

def check_rtsp_stream(rtsp_url, timeout=2):
    cap = cv2.VideoCapture(rtsp_url)
    cap.set(cv2.CAP_PROP_POS_MSEC, 1)
    start_time = time.time()
    while not cap.isOpened() and (time.time() - start_time) < timeout:
        time.sleep(0.1)
        cap = cv2.VideoCapture(rtsp_url)
        cap.set(cv2.CAP_PROP_POS_MSEC, 1)

    if cap.isOpened():
        print(f"✅ Found accessible stream: {rtsp_url}")
        cap.release()
        return rtsp_url
    else:
        if cap:
            cap.release()
        return None

def scan_rtsp_streams(base_ip_prefix, start_octet, end_octet, common_ports, common_paths, credentials):
    found_streams = []
    print(f"Starting RTSP scan for {base_ip_prefix}.{start_octet}-{end_octet}...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = []
        for i in range(start_octet, end_octet+1):
            ip_address = f"{base_ip_prefix}.{i}"
            for port in common_ports:
                for path in common_paths:
                    # Try without credentials first
                    rtsp_url = f"rtsp://{ip_address}:{port}{path}"
                    futures.append(executor.submit(check_rtsp_stream, rtsp_url))

                    # Try all credential combinations
                    for username, password in credentials:
                        if username == "" and password == "":
                            continue # Already checked no-auth case above
                        # Encode special characters in the password
                        encoded_password = urllib.parse.quote(password, safe='')
                        rtsp_url = f"rtsp://{username}:{encoded_password}@{ip_address}:{port}{path}"
                        futures.append(executor.submit(check_rtsp_stream, rtsp_url))

        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                found_streams.append(result)

    return found_streams

if __name__ == "__main__":
    BASE_IP_PREFIX = "192.168.1"
    SCAN_START_OCTET = 1
    SCAN_END_OCTET = 20

    COMMON_RTSP_PORTS = [554, 8080, 8554]

    COMMON_RTSP_PATHS = [
        "/stream",
        "/h264_pcm.sdp",
        "/live/ch0",
        "/axis-media/media.amp",
        "/onvif/profile1/media.smp",
        "/live/ch00_0",
        "/ch0_0.unicast",
        "/video.mp4",
        "/cam/realmonitor?channel=1&subtype=0",
        "/live/0/0/0",
        "/Streaming/Channels/101",
        "/ISAPI/Streaming/Channels/1/live"
    ]

    # List of (username, password) tuples
    CREDENTIALS = [
        ("", ""), # No authentication
        #("admin", "admin"),
        #("admin", "admin123"),
        #("admin", "12345"),
        #("admin", "password"),
        ("admin", "admin@123"),
        ("admin", "myhomne@123"),
        #("user", "user123"),
        #("root", "root"),
        #("guest", "guest")
    ]

    print("RTSP Camera Stream Scanner")
    print("--------------------------")
    print(f"Scanning network: {BASE_IP_PREFIX}.{SCAN_START_OCTET}-{SCAN_END_OCTET}")
    print(f"Checking ports: {COMMON_RTSP_PORTS}")
    print(f"Checking paths: {COMMON_RTSP_PATHS}")
    print(f"Trying credential combinations: {CREDENTIALS}\n")

    accessible_streams = scan_rtsp_streams(
        BASE_IP_PREFIX,
        SCAN_START_OCTET,
        SCAN_END_OCTET,
        COMMON_RTSP_PORTS,
        COMMON_RTSP_PATHS,
        CREDENTIALS
    )

    print("\n--- Scan Complete ---")
    if accessible_streams:
        print("Found the following accessible RTSP streams:")
        for stream_url in accessible_streams:
            print(f"- {stream_url}")
    else:
        print("No accessible RTSP streams found in the specified range with common settings.")
    print("---------------------")
    print("NOTE: If no streams were found, try adjusting the IP range, common ports, or common paths.")
    print("You may also need to check firewall settings or camera authentication (this script does not handle advanced authentication).")
