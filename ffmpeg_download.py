import os
import zipfile
import tempfile
import urllib.request
from pathlib import Path


def ensure_ffmpeg():
    """
    Ensures ffmpeg.exe exists in the current directory.
    Downloads it if missing.
    """

    ffmpeg_path = Path("ffmpeg.exe")

    if ffmpeg_path.exists():
        print("✓ ffmpeg.exe already present")
        return ffmpeg_path

    print("Downloading FFmpeg...")

    url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "ffmpeg.zip"

        urllib.request.urlretrieve(url, zip_path)

        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)

        for root, _, files in os.walk(tmp):
            if "ffmpeg.exe" in files:
                src = Path(root) / "ffmpeg.exe"
                ffmpeg_path.write_bytes(src.read_bytes())
                print("✓ Downloaded ffmpeg.exe")
                return ffmpeg_path

    raise RuntimeError("Could not find ffmpeg.exe in downloaded archive.")



ensure_ffmpeg()