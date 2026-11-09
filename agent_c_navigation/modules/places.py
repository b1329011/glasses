"""
地點消歧模組
====================================
版本說明：
  - 沒有設定 GOOGLE_MAPS_API_KEY → 走假資料（原本demo版本）
  - 有設定 → 真的呼叫 Google Places API (New) 的 Text Search 做地點搜尋

申請金鑰：https://console.cloud.google.com
  1. 建立專案 → 啟用「Places API (New)」
  2. 憑證 → 建立API金鑰
  3. 帳單 → 綁信用卡（有免費額度，測試用量通常不會被扣款）

拿到後在 .env 新增：
    GOOGLE_MAPS_API_KEY=你的金鑰
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from dotenv import load_dotenv
from modules import cache

load_dotenv()

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
USE_REAL_PLACES = GOOGLE_MAPS_API_KEY is not None

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# 假資料（沒有金鑰時的fallback，也方便快速測試邏輯不用等真實API）
MOCK_PLACES_DB = {
    "台北車站": [
        {"name": "台北車站", "address": "台北市中正區北平西路3號", "lat": 25.0478, "lng": 121.5170}
    ],
    "市政府": [
        {"name": "台北市政府", "address": "台北市信義區市府路1號", "lat": 25.0375, "lng": 121.5637}
    ],
    "診所": [
        {"name": "天成診所（中山店）", "address": "台北市中山區中山北路2段", "lat": 25.0523, "lng": 121.5219},
        {"name": "天成診所（大安店）", "address": "台北市大安區敦化南路", "lat": 25.0329, "lng": 121.5498},
    ],
}


def _search_place_mock(query: str) -> list:
    """假資料版本：簡單模糊比對"""
    for key, results in MOCK_PLACES_DB.items():
        if key in query:
            return results
    return []


def _search_place_real(query: str, location_bias: dict = None) -> list:
    """
    真實版本：呼叫 Google Places API (New) 的 Text Search。

    Args:
        query: 使用者說的地點名稱（可以是模糊、口語化的）
        location_bias: 選填，{"lat": ..., "lng": ...} 讓搜尋結果優先偏向這個座標附近
                       （例如用使用者目前GPS位置，避免搜到外縣市同名地點）

    Returns:
        list: 統一格式的地點清單 [{"name", "address", "lat", "lng"}, ...]
    """
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
        # FieldMask 是必填，只要求我們需要的欄位，可以控制花費
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.location",
    }
    body = {
        "textQuery": query,
        "languageCode": "zh-TW",
        "maxResultCount": 5,
    }
    if location_bias:
        body["locationBias"] = {
            "circle": {
                "center": {"latitude": location_bias["lat"], "longitude": location_bias["lng"]},
                "radius": 20000.0,  # 20公里內優先
            }
        }

    resp = requests.post(PLACES_SEARCH_URL, headers=headers, json=body, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    results = []
    for place in data.get("places", []):
        results.append({
            "name": place.get("displayName", {}).get("text", "未知地點"),
            "address": place.get("formattedAddress", ""),
            "lat": place.get("location", {}).get("latitude"),
            "lng": place.get("location", {}).get("longitude"),
        })
    return results


def search_place(query: str, location_bias: dict = None) -> list:
    """
    對外統一介面，main.py呼叫這個，不用管背後是真實API還是假資料。

    加入本地快取：地點座標基本上不會變，快取設定30天過期（給地點偶爾
    可能歇業、搬遷這種情況留一點更新空間，但不用每次都重查）。
    這對「使用者常去的地點」特別有幫助——問過一次「去公司」之後，
    之後再問就不用再打API，也不用等網路來回時間。
    """
    cache_key = (query, location_bias.get("lat") if location_bias else None,
                 location_bias.get("lng") if location_bias else None)
    cached = cache.get("places", cache_key, ttl_seconds=30 * 24 * 3600)
    if cached is not None:
        return cached

    if USE_REAL_PLACES:
        try:
            results = _search_place_real(query, location_bias)
        except Exception as e:
            print(f"   [警告] Google Places API 呼叫失敗，改用假資料：{e}")
            results = _search_place_mock(query)
    else:
        results = _search_place_mock(query)

    if results:  # 只快取有結果的查詢，避免把「查無資料」的暫時性問題也存成永久快取
        cache.set("places", cache_key, results)

    return results


def disambiguate(query: str, location_bias: dict = None) -> dict:
    """
    處理地點消歧邏輯：
    - 0筆結果 → 詢問使用者重新描述
    - 1筆結果 → 直接使用
    - 多筆結果 → 產生語音確認問句
    """
    results = search_place(query, location_bias)

    if len(results) == 0:
        return {
            "status": "not_found",
            "place": None,
            "voice_prompt": f"找不到「{query}」這個地點，可以再說一次詳細一點嗎？"
        }
    elif len(results) == 1:
        return {
            "status": "found",
            "place": results[0],
            "voice_prompt": None
        }
    else:
        names = "、".join([f"{r['name']}（{r['address']}）" for r in results])
        return {
            "status": "ambiguous",
            "place": results,
            "voice_prompt": f"找到{len(results)}個「{query}」，分別是：{names}。你是要去哪一個？"
        }


if __name__ == "__main__":
    print(f"目前模式：{'真實Google Places API' if USE_REAL_PLACES else '假資料（未偵測到GOOGLE_MAPS_API_KEY）'}\n")

    test_queries = ["台北車站", "天成診所", "不存在的奇怪地方"]
    for q in test_queries:
        print(f"查詢：{q}")
        print(disambiguate(q))
        print()