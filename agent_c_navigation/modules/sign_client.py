"""
指標牌辨識服務客戶端（呼叫 sign_detector 後端）
====================================
sign_detector（webapp/backend，FastAPI）已經提供「目標導向導航」API：
    POST /api/navigate  { image_base64, target, ... }
    → { found, sentence, direction, matched_text, via, ... }
    例：target="淡水信義線" → sentence="淡水信義線在往左的方向"
        target="往淡水"     → 在「捷運方向」牌裡找「淡水」，回報旁邊箭頭方向
        target="出口"/"台鐵"/"高鐵" → 直接用 YOLO 圖標，免 OCR

兩種呼叫方式：
  1. 同一個程式（合併後端，預設）：webapp/backend/main.py 啟動時呼叫
     set_local_navigator(navigate)，直接呼叫函式，不經過 HTTP
  2. HTTP：Agent C 單獨啟動（agent_c_navigation/app.py）時，呼叫 SIGN_DETECTOR_URL
     （之後指標辨識搬到眼鏡端/有 GPU 的機器，也是走這條）

設定（.env）：
    SIGN_DETECTOR_URL=http://127.0.0.1:8000   # sign_detector 後端位址（預設值）
    SIGN_DETECTOR_MOCK=1                       # 不接真實服務，回傳模擬結果（展示/測試用）
"""

import os
import random
import time

import requests
from dotenv import load_dotenv

load_dotenv()

SIGN_DETECTOR_URL = os.getenv("SIGN_DETECTOR_URL", "http://127.0.0.1:8000").rstrip("/")
USE_MOCK = os.getenv("SIGN_DETECTOR_MOCK", "").strip() in ("1", "true", "True", "yes")

# 連續/即時導航用的參數，比照 sign_detector 前端 NavView.vue 的設定（求快）
DEFAULT_NAV_PARAMS = {
    "conf": 0.25,
    "iou": 0.45,
    "imgsz": 640,
    "no_full_ocr": True,   # 跳過整張 OCR 保底，避免卡在慢 OCR
}

_health_cache = {"ok": None, "checked_at": 0.0}

# 合併後端時由 webapp/backend/main.py 注入的 /api/navigate 函式（payload dict → result dict）
_local_navigator = None


def set_local_navigator(fn) -> None:
    """合併後端用：直接呼叫同一個程式裡的指標辨識，不經過 HTTP。"""
    global _local_navigator
    _local_navigator = fn


def is_available(cache_sec: float = 30.0) -> bool:
    """檢查 sign_detector 服務是否在線（結果快取 cache_sec 秒）。"""
    if USE_MOCK or _local_navigator is not None:
        return True
    now = time.time()
    if _health_cache["ok"] is not None and now - _health_cache["checked_at"] < cache_sec:
        return _health_cache["ok"]
    try:
        resp = requests.get(f"{SIGN_DETECTOR_URL}/api/health", timeout=2)
        ok = resp.ok
    except requests.RequestException:
        ok = False
    _health_cache.update(ok=ok, checked_at=now)
    return ok


def mock_navigate(target: str) -> dict:
    """模擬辨識結果：約 7 成機率「看到」目標並給一個方向。"""
    if random.random() < 0.3:
        return {"found": False, "target": target, "direction": None, "sentence": None,
                "message": f"畫面中找不到「{target}」", "mock": True}
    direction = random.choice(["往左", "往右", "往前"])
    return {"found": True, "target": target, "matched_text": target, "via": "mock",
            "direction": direction, "sentence": f"{target}在{direction}的方向", "mock": True}


def navigate(image_base64: str, target: str, **params) -> dict:
    """
    把一張影像與「要找的目標」送給 sign_detector，回傳辨識結果。

    Args:
        image_base64: data URL 或純 base64 的 JPEG
        target: 要找的目標，例如「淡水信義線」「往淡水」「出口」「台鐵」
        params: 覆寫 DEFAULT_NAV_PARAMS（conf / imgsz / no_full_ocr ...）

    Returns:
        dict: sign_detector 的回傳（found / sentence / direction ...）；
              連線失敗時回傳 {"found": False, "error": "..."}，呼叫端不用自己 try/except
    """
    if USE_MOCK:
        return mock_navigate(target)

    body = {**DEFAULT_NAV_PARAMS, **params, "image_base64": image_base64, "target": target}
    body.pop("annotated", None)

    if _local_navigator is not None:
        try:
            data = dict(_local_navigator(body))
        except Exception as e:  # noqa: BLE001  （FastAPI 的 HTTPException 帶 detail）
            return {"found": False, "target": target, "error": f"指標辨識失敗：{getattr(e, 'detail', None) or e}"}
        data.pop("annotated_base64", None)
        return data
    try:
        resp = requests.post(f"{SIGN_DETECTOR_URL}/api/navigate", json=body, timeout=15)
        if not resp.ok:
            detail = ""
            try:
                detail = resp.json().get("detail", "")
            except ValueError:
                pass
            return {"found": False, "target": target, "error": f"HTTP {resp.status_code} {detail}".strip()}
        data = resp.json()
        # 標註影像很大，Agent C 用不到，丟掉避免一路傳回手機
        data.pop("annotated_base64", None)
        _health_cache.update(ok=True, checked_at=time.time())
        return data
    except requests.RequestException as e:
        _health_cache.update(ok=False, checked_at=time.time())
        return {"found": False, "target": target, "error": f"無法連線指標辨識服務（{SIGN_DETECTOR_URL}）：{e}"}


if __name__ == "__main__":
    import base64
    import sys

    print(f"sign_detector 位址：{SIGN_DETECTOR_URL}　模擬模式：{USE_MOCK}")
    print(f"服務在線：{is_available()}")
    if len(sys.argv) >= 3:
        with open(sys.argv[1], "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        print(navigate(b64, sys.argv[2]))
    else:
        print("用法：python3 modules/sign_client.py 圖片路徑 目標（例：淡水信義線）")
