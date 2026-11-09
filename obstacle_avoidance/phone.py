"""
手機模式啟動器：一鍵啟動「辨識後端 + Cloudflare https 通道」，電腦上顯示 QR code 給手機掃描。

    python phone.py

流程：
1. 產生一組隨機存取權杖（只有掃到 QR code 的手機能用）
2. 啟動 server.py（模型跑在這台電腦）
3. 啟動 cloudflared 免費通道，取得 https://xxxx.trycloudflare.com 網址
4. 打開 http://localhost:8010/phone.html 顯示 QR code
手機掃描後即可使用，並可「加入主畫面」安裝成 App。按 Ctrl+C 結束。

注意：免費通道每次啟動網址都不同，已安裝的 App 會連到舊網址；
每次啟動後請重新掃描 QR code（要固定網址，見 README「固定網址」）。
"""

import argparse
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CLOUDFLARED = ROOT / "tools" / "cloudflared.exe"
PUBLIC_URL_FILE = ROOT / "output" / "public_url.json"
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def port_free(port):
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def wait_ready(port, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as r:
                if json.load(r).get("ready"):
                    return True
        except OSError:
            pass
        time.sleep(1)
    return False


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)  # 輸出到檔案或管線時也立即顯示
    ap = argparse.ArgumentParser(description="手機模式：後端 + https 通道")
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--no-browser", action="store_true", help="不要自動打開 QR code 頁面")
    args = ap.parse_args()

    if not CLOUDFLARED.exists():
        print(f"找不到 {CLOUDFLARED}\n請先執行 python setup_assets.py 下載")
        return 1
    if not port_free(args.port):
        print(f"埠 {args.port} 已被占用：請先關掉正在執行的 server.py，或用 --port 換一個埠")
        return 1

    token = secrets.token_urlsafe(9)
    env = {**os.environ, "OA_ACCESS_TOKEN": token, "PYTHONIOENCODING": "utf-8"}
    PUBLIC_URL_FILE.parent.mkdir(exist_ok=True)
    PUBLIC_URL_FILE.unlink(missing_ok=True)

    print("① 啟動辨識後端（載入模型約 10~20 秒）…")
    server = subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--port", str(args.port)], cwd=ROOT, env=env)
    tunnel = None
    try:
        if not wait_ready(args.port):
            print("後端啟動失敗，請看上方錯誤訊息")
            return 1

        print("② 建立 https 通道（Cloudflare）…")
        tunnel = subprocess.Popen(
            [str(CLOUDFLARED), "tunnel", "--url", f"http://localhost:{args.port}", "--no-autoupdate"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
        )
        url = None
        t0 = time.time()
        for line in tunnel.stdout:
            m = URL_RE.search(line)
            if m:
                url = m.group(0)
                break
            if time.time() - t0 > 60:
                break
        if not url:
            print("無法建立通道：請確認電腦有網路，或稍後再試")
            return 1
        # 之後的 cloudflared 輸出丟掉，避免管線塞滿
        threading.Thread(target=lambda: [None for _ in tunnel.stdout], daemon=True).start()

        phone_url = f"{url}/m/?k={token}"
        PUBLIC_URL_FILE.write_text(json.dumps({"url": url, "phone_url": phone_url}, ensure_ascii=False), encoding="utf-8")
        local_page = f"http://localhost:{args.port}/phone.html"
        print("\n" + "=" * 60)
        print("③ 用手機掃描電腦畫面上的 QR code，或在手機瀏覽器輸入：")
        print(f"   {phone_url}")
        print(f"   （QR code 頁面：{local_page}）")
        print("   通道剛建立時可能要等 10~30 秒才連得上")
        print("=" * 60)
        print("按 Ctrl+C 結束\n")
        if not args.no_browser:
            webbrowser.open(local_page)
        server.wait()
    except KeyboardInterrupt:
        print("\n結束中…")
    finally:
        PUBLIC_URL_FILE.unlink(missing_ok=True)
        for p in (tunnel, server):
            if p and p.poll() is None:
                p.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
