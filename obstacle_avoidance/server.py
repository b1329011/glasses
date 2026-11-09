"""
鏡頭即時偵測後端（FastAPI）

    python server.py                 啟動後開 http://localhost:8010/live.html
                                     （預設 8010，避開指標辨識 sign_detector 後端的 8000）
    python server.py --host 0.0.0.0  讓同網段的手機連線（手機瀏覽器開鏡頭需要 https，見 README）

WebSocket：
    /ws/camera     瀏覽器送 JPEG 影格 → 回傳分析結果（單眼 AI 深度）
    /ws/depthcam   後端直接讀 RealSense 深度相機 → 推送影格與分析結果
兩者都可以送文字訊息 {"type": "config", "height_m":..., "pitch_deg":..., "hfov_deg":...} 調整相機幾何。
"""

import argparse
import asyncio
import base64
import json
import os
import secrets
import socket
import sys
from pathlib import Path

import cv2
import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from camera import config as cfg
from camera.scene_analyzer import Models, SceneAnalyzer

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="障礙物避障模組：鏡頭即時偵測")
models: Models | None = None

# 手機模式（phone.py）透過公開通道對外時，用存取權杖擋掉其他人；本機直接執行時不檢查
ACCESS_TOKEN = os.environ.get("OA_ACCESS_TOKEN") or None
PUBLIC_URL_FILE = ROOT / "output" / "public_url.json"


async def _authorized(ws: WebSocket) -> bool:
    if ACCESS_TOKEN and not secrets.compare_digest(ws.query_params.get("token", ""), ACCESS_TOKEN):
        await ws.close(code=4401, reason="invalid token")
        return False
    return True


@app.get("/api/public-url")
def public_url(request: Request):
    """給電腦上的 QR code 頁面用：只回應本機、而且不是經由通道進來的請求（通道請求帶有 cf-ray 標頭）。"""
    if request.client.host not in ("127.0.0.1", "::1") or "cf-ray" in request.headers:
        raise HTTPException(status_code=403)
    if not PUBLIC_URL_FILE.exists():
        return {"url": None}
    return json.loads(PUBLIC_URL_FILE.read_text(encoding="utf-8"))


@app.on_event("startup")
def load_models():
    global models
    print("載入模型中（物體偵測 + Depth Anything V2 Small）…")
    models = Models()
    warm = np.zeros((240, 320, 3), np.uint8)  # 暖機：第一次推論特別慢
    models.depth.predict(warm)
    models.detect_objects(warm)
    print(f"模型載入完成：{models.detector.name}")


@app.get("/api/status")
def status():
    try:
        import pyrealsense2  # noqa: F401
        realsense = True
    except ImportError:
        realsense = False
    return {
        "ready": models is not None,
        "realsense_installed": realsense,
        "defaults": {"height_m": cfg.CAMERA_HEIGHT_M, "pitch_deg": cfg.CAMERA_PITCH_DEG, "hfov_deg": cfg.CAMERA_HFOV_DEG},
    }


def _apply_config(analyzer, text):
    """處理前端的文字訊息：調整相機幾何／深度品質，或已知距離校正。"""
    try:
        msg = json.loads(text)
    except json.JSONDecodeError:
        return None
    if msg.get("type") == "config":
        if any(k in msg for k in ("height_m", "pitch_deg", "hfov_deg")):
            # live=True：手機陀螺儀連續更新傾角，不重置校正與平滑
            analyzer.set_geometry(msg.get("height_m"), msg.get("pitch_deg"), msg.get("hfov_deg"),
                                  reset=not msg.get("live"))
        if msg.get("depth_quality"):
            analyzer.set_depth_quality(msg["depth_quality"])
        if "speak_ahead" in msg:
            analyzer.planner.speak_ahead = bool(msg["speak_ahead"])
        return {"type": "config", "geometry": analyzer.geometry_dict(), "depth_quality": analyzer.depth_quality}
    if msg.get("type") == "calibrate_distance":
        try:
            res = analyzer.calibrate_distance(float(msg["x"]), float(msg["y"]), float(msg["distance_m"]))
        except (KeyError, TypeError, ValueError):
            res = {"ok": False, "message": "校正參數錯誤"}
        return {"type": "calibrated", **res}
    return None


@app.websocket("/ws/camera")
async def ws_camera(ws: WebSocket):
    await ws.accept()
    if not await _authorized(ws):
        return
    analyzer = SceneAnalyzer(models)
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("text"):
                ack = _apply_config(analyzer, msg["text"])
                if ack:
                    await ws.send_json(ack)
                continue
            data = msg.get("bytes")
            if not data:
                continue
            frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                await ws.send_json({"type": "error", "message": "影像解碼失敗"})
                continue
            result = await asyncio.to_thread(analyzer.process, frame)
            result["type"] = "result"
            await ws.send_json(result)
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/depthcam")
async def ws_depthcam(ws: WebSocket):
    await ws.accept()
    if not await _authorized(ws):
        return
    from camera.depth_source import RealSenseSource
    try:
        source = await asyncio.to_thread(RealSenseSource)
    except RuntimeError as e:
        await ws.send_json({"type": "error", "message": str(e)})
        await ws.close()
        return
    analyzer = SceneAnalyzer(models)
    analyzer.set_geometry(hfov_deg=source.hfov_deg)

    async def receive_config():
        while True:
            ack = _apply_config(analyzer, await ws.receive_text())
            if ack:
                await ws.send_json(ack)

    receiver = asyncio.create_task(receive_config())
    try:
        while not receiver.done():
            color, depth = await asyncio.to_thread(source.read)
            result = await asyncio.to_thread(analyzer.process, color, depth)
            ok, buf = cv2.imencode(".jpg", color, [cv2.IMWRITE_JPEG_QUALITY, 75])
            result["frame"] = "data:image/jpeg;base64," + base64.b64encode(buf).decode() if ok else None
            result["type"] = "result"
            await ws.send_json(result)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        receiver.cancel()
        source.close()


@app.middleware("http")
async def no_stale_frontend(request: Request, call_next):
    """前端檔案加上 no-cache：瀏覽器可以快取，但每次都要先問伺服器有沒有新版（改完程式手機才會更新）。"""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")


def _port_free(host, port):
    """在載入模型前先檢查埠，避免等了十幾秒才發現衝突。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="鏡頭即時偵測後端")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8010)),
                    help="預設 8010（8000 留給 sign_detector 後端）；也可用環境變數 PORT 指定")
    ap.add_argument("--ssl-certfile", help="https 憑證（手機連線用）")
    ap.add_argument("--ssl-keyfile")
    args = ap.parse_args()
    if not _port_free(args.host, args.port):
        print(f"埠 {args.port} 已被占用：可能本程式已在執行（直接開網址即可），或是其他程式在用。")
        print(f"可改用其他埠，例如：python server.py --port {args.port + 1}")
        sys.exit(1)
    scheme = "https" if args.ssl_certfile else "http"
    print(f"啟動後請用瀏覽器開啟 {scheme}://localhost:{args.port}/live.html")
    uvicorn.run(app, host=args.host, port=args.port, ssl_certfile=args.ssl_certfile, ssl_keyfile=args.ssl_keyfile)
