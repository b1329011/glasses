"""
即時運輸資訊模組
====================================
版本說明：
  - 沒有設定 TDX_CLIENT_ID / TDX_CLIENT_SECRET → 走模擬資料（原本demo版本）
  - 有設定 → 真的呼叫 TDX API 取得即時公車到站時間

申請TDX帳號：https://tdx.transportdata.tw （完全免費，但審核可能需要幾天）
申請完在「會員中心」建立應用程式，取得 Client Id / Client Secret

拿到後，在 .env 檔案新增：
    TDX_CLIENT_ID=你的Client ID
    TDX_CLIENT_SECRET=你的Client Secret

=== TDX 認證機制說明 ===
TDX用OAuth2 Client Credentials流程：
  1. 先用 Client ID + Client Secret 換一個 access_token（有效期通常24小時）
  2. 之後每次呼叫資料API，都要在 Header 帶上這個 token
這個模組會自動處理換token、快取token、過期自動重新換發，你不用管這段細節。
"""

import os
import time
import random

import requests
from dotenv import load_dotenv

load_dotenv()

TDX_CLIENT_ID = os.getenv("TDX_CLIENT_ID")
TDX_CLIENT_SECRET = os.getenv("TDX_CLIENT_SECRET")
USE_REAL_TDX = TDX_CLIENT_ID is not None and TDX_CLIENT_SECRET is not None

TDX_AUTH_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
TDX_API_BASE = "https://tdx.transportdata.tw/api/basic"

# 簡單的token快取，避免每次呼叫都重新換發
_token_cache = {"access_token": None, "expires_at": 0}


def _get_access_token() -> str:
    """
    向TDX換取access token，並快取起來直到過期前才重新換發。
    """
    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["expires_at"]:
        return _token_cache["access_token"]

    resp = requests.post(
        TDX_AUTH_URL,
        headers={"content-type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": TDX_CLIENT_ID,
            "client_secret": TDX_CLIENT_SECRET,
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    _token_cache["access_token"] = data["access_token"]
    # 提前60秒視為過期，避免邊界情況卡到過期瞬間
    _token_cache["expires_at"] = now + data.get("expires_in", 86400) - 60

    return _token_cache["access_token"]


def search_bus_routes(city: str, keyword: str) -> list:
    """
    輔助工具：查詢某縣市底下，路線名稱包含關鍵字的公車路線清單。
    用途：先用這個函式確認TDX資料庫裡「正確的路線名稱字串」長什麼樣子，
         再把正確字串拿去 get_realtime_wait_time() 查即時到站，
         避免因為路線名稱格式猜錯（大小寫、前綴、縣市不對）而查無資料。

    Args:
        city: 縣市英文代碼，例如 "Taipei"、"NewTaipei"、"Taoyuan"
        keyword: 路線名稱關鍵字，例如 "264"

    Returns:
        list: 符合的路線資料（含TDX資料庫裡的正確RouteName）
    """
    if not USE_REAL_TDX:
        print("   [提醒] 尚未設定TDX金鑰，無法查詢真實路線清單")
        return []

    token = _get_access_token()
    url = f"{TDX_API_BASE}/v2/Bus/Route/City/{city}"

    resp = requests.get(
        url,
        headers={"authorization": f"Bearer {token}"},
        params={
            "$filter": f"contains(RouteName/Zh_tw,'{keyword}')",
            "$format": "JSON",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _get_bus_eta_real(city: str, route_name: str) -> list:
    """
    真實版：呼叫TDX公車動態API，取得指定路線在各站牌的預估到站時間。

    Args:
        city: 城市名稱，例如 "Taipei"
        route_name: 公車路線名稱，例如 "264"

    Returns:
        list: 各站牌的到站資訊（原始TDX回傳格式）
    """
    token = _get_access_token()
    url = f"{TDX_API_BASE}/v2/Bus/EstimatedTimeOfArrival/City/{city}/{route_name}"

    resp = requests.get(
        url,
        headers={"authorization": f"Bearer {token}"},
        params={"$format": "JSON"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def get_realtime_wait_time(mode: str, base_wait_min: int, city: str = None, route_name: str = None) -> dict:
    """
    對外統一介面：取得即時到站時間。
    有設定TDX金鑰且提供route_name時走真實API，否則走模擬資料。

    city 若未指定，會依序嘗試常見縣市（因為使用者可能導航去任何縣市，
    路線代碼在不同縣市系統裡是分開登記的，不能只查台北市），
    找到第一個有回傳資料的縣市就使用。

    Returns:
        dict: {"wait_min": int, "is_delayed": bool, "note": str}
    """
    CANDIDATE_CITIES = ["Taipei", "NewTaipei", "Taoyuan", "Taichung", "Tainan", "Kaohsiung"]

    if USE_REAL_TDX and route_name and mode in ("公車", "bus"):
        cities_to_try = [city] if city else CANDIDATE_CITIES
        for try_city in cities_to_try:
            try:
                eta_data = _get_bus_eta_real(try_city, route_name)
                if eta_data:
                    estimate_seconds = eta_data[0].get("EstimateTime")
                    if estimate_seconds is not None:
                        wait_min = max(1, round(estimate_seconds / 60))
                        return {
                            "wait_min": wait_min,
                            "is_delayed": False,
                            "note": "",
                        }
            except Exception as e:
                print(f"   [警告] TDX API 呼叫失敗（{try_city}）：{e}")
                continue

        print(f"   [提醒] TDX在常見縣市中都查無「{route_name}」路線即時資料，改用模擬資料")

    # 模擬資料（fallback，也是還沒申請TDX金鑰前的預設行為）
    delay = random.choice([0, 0, 0, 2, 3, -1])
    actual_wait = max(1, base_wait_min + delay)
    is_delayed = delay > 1
    note = f"（{mode}目前略為誤點）" if is_delayed else ""

    return {"wait_min": actual_wait, "is_delayed": is_delayed, "note": note}


def apply_realtime_to_route(route: dict) -> dict:
    """把即時資料覆蓋進路線資訊裡"""
    if route["mode"] == "步行":
        return route

    # route_name 若有真實路線代碼可從 routing.py 帶進來，demo先用route裡沒有時走模擬
    route_name = route.get("route_name")
    realtime = get_realtime_wait_time(route["mode"], route["wait_min"], route_name=route_name)
    updated_route = dict(route)
    updated_route["wait_min"] = realtime["wait_min"]
    updated_route["realtime_note"] = realtime["note"]
    return updated_route


if __name__ == "__main__":
    print(f"目前模式：{'真實TDX API' if USE_REAL_TDX else '模擬資料（未偵測到TDX金鑰）'}\n")

    if USE_REAL_TDX:
        # 步驟1：先查路線清單，確認TDX資料庫裡「264」的正確登記名稱跟所屬縣市
        print("先查詢路線清單，確認正確路線名稱（會分別查台北市跟新北市）：")
        for city in ["Taipei", "NewTaipei"]:
            routes = search_bus_routes(city, "264")
            if routes:
                print(f"\n【{city}】找到符合的路線：")
                for r in routes:
                    print(f"  路線名稱：{r.get('RouteName', {}).get('Zh_tw')}　所屬縣市：{city}")
            else:
                print(f"\n【{city}】查無符合「264」的路線")

        print("\n" + "=" * 40)
        print("已知264路屬於新北市，測試查詢新北264路公車即時到站：")
        result = get_realtime_wait_time("公車", base_wait_min=6, city="NewTaipei", route_name="264")
        print(result)
    else:
        test_route = {"mode": "捷運", "wait_min": 4, "total_min": 22}
        for _ in range(3):
            print(apply_realtime_to_route(test_route))