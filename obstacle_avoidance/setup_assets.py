"""
下載不放進 git 的大檔案（第一次 clone 後執行一次）：

    python setup_assets.py

- camera/models/depth_anything_v2_small.onnx  約 99 MB（Hugging Face，Apache-2.0）
- tools/cloudflared.exe                        約 55 MB（手機模式的 https 通道，僅 Windows）
已存在的檔案會略過。
"""

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = [
    ("深度模型 Depth Anything V2 Small",
     "https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model.onnx",
     ROOT / "camera" / "models" / "depth_anything_v2_small.onnx"),
    ("Cloudflare 通道 cloudflared",
     "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe",
     ROOT / "tools" / "cloudflared.exe"),
]


def download(url, dest):
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("content-length") or 0)
        done = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done / 1e6:.0f} / {total / 1e6:.0f} MB", end="", flush=True)
    print()
    tmp.replace(dest)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for name, url, dest in ASSETS:
        if dest.exists():
            print(f"✔ {name}：已存在（{dest.relative_to(ROOT)}）")
            continue
        if dest.suffix == ".exe" and sys.platform != "win32":
            print(f"- {name}：非 Windows，請自行安裝 cloudflared（https://github.com/cloudflare/cloudflared）")
            continue
        print(f"↓ {name} → {dest.relative_to(ROOT)}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        download(url, dest)
    print("完成")


if __name__ == "__main__":
    main()
