"""
本地快取模組（邊緣運算概念的具體實作）
====================================
概念：能在裝置端（手機/眼鏡）解決的問題，不要每次都丟給雲端AI，
     這樣可以達到三個好處：
       1. 省成本：減少API呼叫次數（尤其Gemini免費版有額度限制，
          你之前測試就撞到過「一分鐘限5次」的牆）
       2. 降延遲：本地查表比網路來回快很多，對即時語音互動的
          使用體驗差很多（使用者不用等2-3秒才聽到回應）
       3. 離線韌性：即使網路不穩，常用的功能還是能運作

不是所有東西都適合快取，設計原則：
  - 「答案不太會變」的資料 → 適合長期快取（例如：使用者常去的地點）
  - 「答案會變但變化不快」的資料 → 適合短期快取，設定TTL自動過期
    （例如：路線規劃，路況會變，但5分鐘內問兩次答案通常一樣）
  - 「定義上就是要即時」的資料 → 完全不快取（例如：公車即時到站時間）

這個模組用簡單的JSON檔案存快取（不需要額外安裝資料庫），
存放在 modules/.cache/ 資料夾底下，跟你的程式碼放在一起，
不需要另外架設Redis之類的快取伺服器，適合demo/畢業專題規模。
"""

import os
import json
import time
import hashlib

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_file_path(namespace: str) -> str:
    """每種資料類型（namespace）存成獨立的快取檔案，避免互相干擾"""
    return os.path.join(CACHE_DIR, f"{namespace}.json")


def _load_cache(namespace: str) -> dict:
    path = _cache_file_path(namespace)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(namespace: str, data: dict):
    path = _cache_file_path(namespace)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _make_key(*args) -> str:
    """把任意參數組合成一個固定長度的快取鍵值（用hash避免特殊字元造成檔名問題）"""
    raw = "|".join(str(a) for a in args)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def get(namespace: str, key_parts, ttl_seconds: int = None):
    """
    從快取讀取資料。

    Args:
        namespace: 資料類型，例如 "nlu"、"places"、"routing"
        key_parts: 用來組成鍵值的內容（例如使用者說的那句話）
        ttl_seconds: 存活時間（秒），None代表永久有效（適合「不太會變」的資料，
                     例如地點座標）；有設定的話，超過這個時間視為過期，
                     不使用快取值，強制重新查詢（適合「會變但變化不快」的資料，
                     例如路線規劃）

    Returns:
        快取的值，找不到或已過期時回傳 None
    """
    cache = _load_cache(namespace)
    key = _make_key(*key_parts) if isinstance(key_parts, (list, tuple)) else _make_key(key_parts)

    entry = cache.get(key)
    if entry is None:
        return None

    if ttl_seconds is not None:
        age = time.time() - entry.get("_cached_at", 0)
        if age > ttl_seconds:
            return None  # 過期了，當作沒快取過

    return entry.get("value")


def set(namespace: str, key_parts, value):
    """把資料存進快取"""
    cache = _load_cache(namespace)
    key = _make_key(*key_parts) if isinstance(key_parts, (list, tuple)) else _make_key(key_parts)

    cache[key] = {
        "value": value,
        "_cached_at": time.time(),
    }
    _save_cache(namespace, cache)


def clear(namespace: str = None):
    """
    清除快取。
    namespace 指定時只清那一類，不指定則全部清空（開發測試時很有用，
    避免舊的假資料快取干擾新的真實API測試結果）
    """
    if namespace:
        path = _cache_file_path(namespace)
        if os.path.exists(path):
            os.remove(path)
    else:
        for fname in os.listdir(CACHE_DIR):
            os.remove(os.path.join(CACHE_DIR, fname))


def stats() -> dict:
    """列出目前各類快取存了幾筆資料，方便debug跟寫報告時統計"""
    result = {}
    for fname in os.listdir(CACHE_DIR):
        namespace = fname.replace(".json", "")
        cache = _load_cache(namespace)
        result[namespace] = len(cache)
    return result


if __name__ == "__main__":
    # 快速測試快取的基本讀寫功能
    print("測試快取模組：\n")

    set("test", "台北車站", {"lat": 25.0478, "lng": 121.517})
    print("寫入快取：test/台北車站")

    result = get("test", "台北車站")
    print(f"讀取快取：{result}")

    print(f"\n目前快取統計：{stats()}")

    clear("test")
    print("已清除測試用快取")