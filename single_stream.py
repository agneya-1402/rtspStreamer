# Change the RTSP source (camera or encoder) to output H.264 video at a lower, more compatible profile/level.
#Profile: Baseline or Main (avoid High or custom profiles for mobile playback)
# Level: ≤4.1 for 1080p, ≤3.1 for 720p, and keep the frame rate/resolution within common ranges. use 480p 


import cv2
import subprocess
import time

VIDEO_PATH = "videos/Shoplifting (3).mp4"  # your MP4 file
RTSP_URL = "rtsp://192.168.1.111:8554/stream1"  # local RTSP server
FPS = 25  # match your video file fps

# OpenCV video capture
cap = cv2.VideoCapture(VIDEO_PATH)
width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

# FFmpeg command to send video to RTSP server
ffmpeg_cmd = [
    'ffmpeg',
    '-re',
    '-f', 'rawvideo',
    '-pix_fmt', 'bgr24',
    '-s', f'{width}x{height}',
    '-r', str(FPS),
    '-i', '-',
    '-vf', 'scale=640:480,format=yuv420p',  # 👈 Fix: scale + convert color
    '-c:v', 'libx264',
    '-profile:v', 'baseline',
    '-level:v', '3.1',
    '-preset', 'ultrafast',
    '-tune', 'zerolatency',
    '-f', 'rtsp',
    RTSP_URL
]

# Start FFmpeg subprocess
process = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE)

try:
    while True:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # loop to start
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            # Send raw frame to FFmpeg
            process.stdin.write(frame.tobytes())
            time.sleep(1 / FPS)

except KeyboardInterrupt:
    print("Stopping stream...")

finally:
    cap.release()
    process.stdin.close()
    process.wait()
