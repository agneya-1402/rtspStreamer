import cv2
import subprocess
import time
import threading
import os
from pathlib import Path

class RTSPStreamer:
    def __init__(self, video_path, rtsp_url, fps=25, stream_id=1):
        self.video_path = video_path
        self.rtsp_url = rtsp_url
        self.fps = fps
        self.stream_id = stream_id
        self.running = False
        self.thread = None
        self.process = None
        self.cap = None
        
    def start_stream(self):
        """Start the RTSP streaming in a separate thread"""
        if self.running:
            print(f"Stream {self.stream_id} is already running")
            return
            
        self.running = True
        self.thread = threading.Thread(target=self._stream_loop, daemon=True)
        self.thread.start()
        print(f"Started stream {self.stream_id}: {self.video_path} -> {self.rtsp_url}")
        
    def stop_stream(self):
        """Stop the RTSP streaming"""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        self._cleanup()
        print(f"Stopped stream {self.stream_id}")
        
    def _stream_loop(self):
        """Main streaming loop"""
        try:
            # Check if video file exists
            if not os.path.exists(self.video_path):
                print(f"Error: Video file not found for stream {self.stream_id}: {self.video_path}")
                return
                
            # OpenCV video capture
            self.cap = cv2.VideoCapture(self.video_path)
            if not self.cap.isOpened():
                print(f"Error: Cannot open video file for stream {self.stream_id}: {self.video_path}")
                return
                
            width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            
            # FFmpeg command for H.264 streaming with 480p output
            ffmpeg_cmd = [
                'ffmpeg',
                '-re',
                '-f', 'rawvideo',
                '-pix_fmt', 'bgr24',
                '-s', f'{width}x{height}',
                '-r', str(self.fps),
                '-i', '-',
                '-vf', 'scale=640:480,format=yuv420p',  # Scale to 480p
                '-c:v', 'libx264',
                '-profile:v', 'baseline',
                '-level:v', '3.1',
                '-preset', 'ultrafast',
                '-tune', 'zerolatency',
                '-g', '30',  # GOP size
                '-keyint_min', '30',
                '-sc_threshold', '0',
                '-b:v', '1000k',  # Bitrate for 480p
                '-maxrate', '1200k',
                '-bufsize', '2000k',
                '-f', 'rtsp',
                '-rtsp_transport', 'tcp',
                self.rtsp_url
            ]
            
            # Start FFmpeg subprocess
            self.process = subprocess.Popen(
                ffmpeg_cmd, 
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            frame_time = 1.0 / self.fps
            
            # Main streaming loop
            while self.running and self.cap.isOpened():
                # Loop video when it ends
                if self.cap.get(cv2.CAP_PROP_POS_FRAMES) >= self.cap.get(cv2.CAP_PROP_FRAME_COUNT) - 1:
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                
                ret, frame = self.cap.read()
                if not ret:
                    # If we can't read, restart from beginning
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                
                try:
                    # Send frame to FFmpeg
                    if self.process.poll() is None:  # Process is still running
                        self.process.stdin.write(frame.tobytes())
                        time.sleep(frame_time)
                    else:
                        print(f"FFmpeg process died for stream {self.stream_id}")
                        break
                        
                except BrokenPipeError:
                    print(f"Broken pipe for stream {self.stream_id}")
                    break
                except Exception as e:
                    print(f"Error in stream {self.stream_id}: {e}")
                    break
                    
        except Exception as e:
            print(f"Error in stream {self.stream_id}: {e}")
        finally:
            self._cleanup()
            
    def _cleanup(self):
        """Clean up resources"""
        if self.cap:
            self.cap.release()
            self.cap = None
            
        if self.process:
            try:
                self.process.stdin.close()
                self.process.terminate()
                self.process.wait(timeout=5)
            except:
                self.process.kill()
            self.process = None

class MultiStreamManager:
    def __init__(self):
        self.streamers = []
        
    def add_stream(self, video_path, rtsp_url, fps=25):
        """Add a new stream configuration"""
        stream_id = len(self.streamers) + 1
        streamer = RTSPStreamer(video_path, rtsp_url, fps, stream_id)
        self.streamers.append(streamer)
        return streamer
        
    def start_all_streams(self):
        """Start all configured streams"""
        print(f"Starting {len(self.streamers)} streams...")
        for streamer in self.streamers:
            streamer.start_stream()
            time.sleep(0.5)  # Small delay between starts
            
    def stop_all_streams(self):
        """Stop all streams"""
        print("Stopping all streams...")
        for streamer in self.streamers:
            streamer.stop_stream()
            
    def stream_status(self):
        """Print status of all streams"""
        print("\n=== Stream Status ===")
        for streamer in self.streamers:
            status = "Running" if streamer.running else "Stopped"
            print(f"Stream {streamer.stream_id}: {status} - {streamer.rtsp_url}")

def main():
    # Create stream manager
    manager = MultiStreamManager()
    
    # Configure 6 streams
    # You can use the same video file for multiple streams or different files
    stream_configs = [
        {
            "video_path": "videos/3.mp4",
            "rtsp_url": "rtsp://192.168.1.100:8554/stream1",
            "fps": 25
        },
        {
            "video_path": "videos/Normal (6).mp4",  # Can reuse same video
            "rtsp_url": "rtsp://192.168.1.100:8554/stream2",
            "fps": 25
        },
        {
            "video_path": "videos/shoplift.mp4",
            "rtsp_url": "rtsp://192.168.1.100:8554/stream3",
            "fps": 25
        },
        {
            "video_path": "videos/Shoplifting (1).mp4",
            "rtsp_url": "rtsp://192.168.1.100:8554/stream4",
            "fps": 25
        },
        {
            "video_path": "videos/Shoplifting (3).mp4",
            "rtsp_url": "rtsp://192.168.1.100:8554/stream5",
            "fps": 25
        },
        {
            "video_path": "videos/Shoplifting (55).mp4",
            "rtsp_url": "rtsp://192.168.1.100:8554/stream6",
            "fps": 25
        }
    ]
    
    # Add all streams to manager
    for config in stream_configs:
        manager.add_stream(
            config["video_path"],
            config["rtsp_url"],
            config["fps"]
        )
    
    try:
        # Start all streams
        manager.start_all_streams()
        
        # Keep running and show status
        while True:
            time.sleep(10)
            manager.stream_status()
            
    except KeyboardInterrupt:
        print("\nReceived interrupt signal...")
    finally:
        manager.stop_all_streams()
        print("All streams stopped. Exiting...")

if __name__ == "__main__":
    main()