"""
路線規劃模組
====================================
版本說明：
  - 沒有設定 GOOGLE_MAPS_API_KEY → 走假資料候選池（原本demo版本）
  - 有設定 → 真的呼叫 Google Routes API 取得真實候選路線

跟 places.py 共用同一支 GOOGLE_MAPS_API_KEY。

=== 這版修正了什麼（相較於上一版）===
上一版對TRANSIT模式只簡單抓「總時間」，沒有解析真正的搭乘細節，
導致語音摘要講不出「搭哪條線、去哪個站上車」，而且候車時間固定顯示0分鐘
（因為根本沒去讀取真實班距資料），讓使用者聽起來像「車已經在你面前」，
這是誤導的資訊，對視障使用者尤其不能接受。

這版改成解析 transitDetails 底下的完整欄位：
  - stopDetails.departureStop.name → 從哪一站上車
  - transitLine.name / nameShort    → 路線名稱（例如「板南線」「264」）
  - transitLine.vehicle.type        → 交通工具種類（公車/捷運/火車）
  - headsign                        → 往哪個方向（終點站，用來跟使用者確認方向對不對）
  - headway                         → 平均班距秒數，換算成分鐘當作候車時間估計
"""

import os

import requests
from dotenv import load_dotenv

load_dotenv()

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")
USE_REAL_ROUTES = GOOGLE_MAPS_API_KEY is not None

ROUTES_API_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"

# 交通工具類型英轉中對照表
VEHICLE_TYPE_MAP = {
    "BUS": "公車",
    "SUBWAY": "捷運",
    "METRO_RAIL": "捷運",
    "RAIL": "火車",
    "HEAVY_RAIL": "火車",
    "COMMUTER_TRAIN": "火車",
    "HIGH_SPEED_TRAIN": "高鐵",
    "TRAM": "輕軌",
    "FERRY": "渡輪",
}

# 台灣捷運路線英文名稱 → 中文對照表
# 原因：Google Routes API 的路線名稱直接取自交通單位原始資料庫（GTFS），
#      不受 languageCode 參數影響，台灣捷運在Google資料庫裡登記的是英文名稱，
#      所以要自己做對照表翻譯，而不是靠API參數解決
LINE_NAME_TRANSLATION = {
    "Tamsui-Xinyi Line": "淡水信義線",
    "Songshan-Xindian Line": "松山新店線",
    "Zhonghe-Xinlu Line": "中和新蘆線",
    "Bannan Line": "板南線",
    "Wenhu Line": "文湖線",
    "Circular Line": "環狀線",
    "Taoyuan Airport MRT": "機場捷運",
    "Airport MRT": "機場捷運",
}


# 捷運終點站英文 → 中文（Google 的 headsign 常是英文，念出來要中文）
HEADSIGN_TRANSLATION = {
    "Tamsui": "淡水", "Beitou": "北投", "Xiangshan": "象山", "Daan": "大安",
    "Dingpu": "頂埔", "Far Eastern Hospital": "亞東醫院", "Nangang Exhibition Center": "南港展覽館",
    "Kunyang": "昆陽", "Songshan": "松山", "Xindian": "新店", "Taipower Building": "台電大樓",
    "Nanshijiao": "南勢角", "Huilong": "迴龍", "Luzhou": "蘆洲", "Taipei Zoo": "動物園",
    "Taipei Main Station": "台北車站", "Laojie River": "老街溪", "Huanbei": "環北",
    "Xinbeitou": "新北投", "Dapinglin": "大坪林", "New Taipei Industrial Park": "新北產業園區",
}


def _translate_headsign(h: str) -> str:
    if not h:
        return h
    for en, zh in sorted(HEADSIGN_TRANSLATION.items(), key=lambda kv: -len(kv[0])):
        if en.lower() in h.lower():
            return zh
    return h


def _translate_line_name(name: str) -> str:
    """把英文捷運路線名稱翻成中文，查不到就照原樣回傳"""
    return LINE_NAME_TRANSLATION.get(name, name)

# 假資料候選池（沒有金鑰時的fallback）
MOCK_ROUTE_POOL = [
    {
        "mode": "捷運",
        "summary": "走路3分鐘到市政府站，板南線往台北車站方向",
        "walk_to_station_min": 3,
        "wait_min": 4,
        "total_min": 22,
        "landmark": "經過信義路口的全家便利商店",
        "steps_before": [
            {"instruction": "沿市府路走向市政府站入口", "distance_m": 200, "voice_text": "走約286步後，沿市府路走向市政府站入口"},
        ],
        "departure_stop": "市政府站",
        "arrival_stop": "台北車站",
        "line_name": "板南線",
        "headsign": "台北車站",
        "stop_count": 4,
        "steps_after": [
            {"instruction": "出站後直走即為目的地", "distance_m": 100, "voice_text": "走約143步後，出站後直走即為目的地"},
        ],
    },
    {
        "mode": "公車",
        "summary": "走路2分鐘到市府站牌，搭264路公車",
        "walk_to_station_min": 2,
        "wait_min": 6,
        "total_min": 28,
        "landmark": "站牌在麥當勞旁邊",
        "route_name": "264",
        "steps_before": [
            {"instruction": "走到麥當勞旁邊的站牌", "distance_m": 140, "voice_text": "走約200步後，走到麥當勞旁邊的站牌"},
        ],
        "departure_stop": "市府轉運站",
        "arrival_stop": "台北車站",
        "line_name": "264",
        "headsign": "台北車站",
        "stop_count": 6,
        "steps_after": [
            {"instruction": "下車後直走即為目的地", "distance_m": 80, "voice_text": "走約114步後，下車後直走即為目的地"},
        ],
    },
    {
        # 轉乘示範：板南線到台北車站，站內轉淡水信義線（會觸發台北車站指標導引）
        "mode": "捷運",
        "summary": "走路3分鐘到市政府站，搭捷運板南線往頂埔方向，在台北車站轉乘捷運淡水信義線往淡水方向",
        "walk_to_station_min": 3,
        "wait_min": 4,
        "total_min": 30,
        "landmark": "上車地點：市政府站",
        "steps_before": [
            {"instruction": "沿市府路走向市政府站入口", "distance_m": 200, "voice_text": "走約286步後，沿市府路走向市政府站入口"},
        ],
        "departure_stop": "市政府站",
        "arrival_stop": "台北車站",
        "line_name": "板南線",
        "headsign": "頂埔",
        "stop_count": 4,
        "transit_legs": [
            {"mode": "捷運", "line_name": "板南線", "headsign": "頂埔", "departure_stop": "市政府站",
             "arrival_stop": "台北車站", "stop_count": 4, "wait_min": 4,
             "steps_before": [
                 {"instruction": "沿市府路走向市政府站入口", "distance_m": 200, "voice_text": "走約286步後，沿市府路走向市政府站入口"},
             ]},
            {"mode": "捷運", "line_name": "淡水信義線", "headsign": "淡水", "departure_stop": "台北車站",
             "arrival_stop": "雙連站", "stop_count": 2, "wait_min": 3, "steps_before": []},
        ],
        "steps_after": [
            {"instruction": "出站後直走即為目的地", "distance_m": 120, "voice_text": "走約171步後，出站後直走即為目的地"},
        ],
    },
    {
        "mode": "步行",
        "summary": "全程步行，會經過信義商圈",
        "walk_to_station_min": 0,
        "wait_min": 0,
        "total_min": 35,
        "landmark": "沿著市府路直走即可",
        "steps": [
            {"instruction": "沿市府路直走", "distance_m": 300, "voice_text": "走約430步後，沿市府路直走", "end_lat": 25.0350, "end_lng": 121.5600},
            {"instruction": "在忠孝東路口右轉", "distance_m": 150, "voice_text": "走約214步後，在忠孝東路口右轉", "end_lat": 25.0400, "end_lng": 121.5550},
            {"instruction": "直走至目的地", "distance_m": 100, "voice_text": "走約143步後，直走至目的地", "end_lat": 25.0478, "end_lng": 121.5170},
        ],
    },
]


def _parse_duration_seconds(duration_str: str) -> int:
    """把 Google 回傳的 "123s" 這種格式轉成整數秒"""
    if not duration_str:
        return 0
    return int(str(duration_str).rstrip("s"))


def _parse_transit_leg(step: dict) -> dict:
    """把一個 TRANSIT step 解析成一段搭乘資訊。"""
    details = step["transitDetails"]
    line_info = details.get("transitLine", {})
    vehicle_label = VEHICLE_TYPE_MAP.get(line_info.get("vehicle", {}).get("type", ""), "大眾運輸")
    line_name = line_info.get("nameShort") or line_info.get("name") or "未知路線"
    # 捷運的 nameShort 可能只是代碼（BL/R），有完整名稱時優先用完整名稱翻譯（公車維持路線號碼）
    if vehicle_label != "公車" and line_info.get("name") and len(line_name) <= 3:
        line_name = line_info["name"]
    line_name = _translate_line_name(line_name)

    # headway是「平均班距」，用來估計候車時間（不是即時到站時間，
    # 真正即時到站時間是靠 transit.py 呼叫TDX API取得，這裡只是沒有TDX資料時的估計值）
    headway_sec = details.get("headway", None)
    wait_min = round(int(str(headway_sec).rstrip("s")) / 60) if headway_sec else 5

    leg = {
        "mode": vehicle_label,
        "line_name": line_name,
        "headsign": _translate_headsign(details.get("headsign", "")),
        "departure_stop": details.get("stopDetails", {}).get("departureStop", {}).get("name", "附近站牌"),
        "arrival_stop": details.get("stopDetails", {}).get("arrivalStop", {}).get("name", "目的地站"),
        "stop_count": details.get("stopCount", None),
        "wait_min": wait_min,
        "steps_before": [],   # 走到這一段上車處的步驟（第一段＝從起點走過去；之後＝轉乘步行）
    }
    if vehicle_label == "公車":
        leg["route_name"] = line_name
    return leg


def _extract_transit_summary(route_data: dict) -> dict:
    """相容舊呼叫：只取第一條路線。"""
    routes = route_data.get("routes", [])
    return _transit_route_to_dict(routes[0]) if routes else None


def _transit_route_to_dict(route: dict) -> dict:
    """
    從 Routes API 的完整回應中，解析出完整的大眾運輸搭乘資訊，
    包含上車前走路段、每一段搭乘細節（站名/路線/方向/站數，含轉乘）、下車後走路段，
    這樣才有足夠資訊做完整的逐步語音導航（不只是摘要）。

    回傳格式：
      - 最上層的 departure_stop / line_name / headsign ... 是「第一段」的資訊（舊程式照用）
      - transit_legs：所有搭乘段（有轉車時 >1 段），每段的 steps_before 是走到該段上車處的步驟
      - steps_after：最後一段下車後走到目的地的步驟

    Returns:
        dict: 統一格式的路線資訊，找不到搭乘細節時回傳 None（呼叫端會fallback）
    """
    total_min = round(_parse_duration_seconds(route.get("duration", "0s")) / 60)

    legs = route.get("legs", [])
    if not legs:
        return None

    # 依序切段：遇到 TRANSIT 就開一段新的搭乘，中間的 WALK 歸給「下一段的上車前步行」
    transit_legs = []
    pending_walk = []
    first_walk_raw = []
    for step in legs[0].get("steps", []):
        if step.get("travelMode") == "TRANSIT" and "transitDetails" in step:
            leg = _parse_transit_leg(step)
            leg["steps_before"] = _parse_walk_step_list(pending_walk)
            if not transit_legs:
                first_walk_raw = pending_walk
            transit_legs.append(leg)
            pending_walk = []
        else:
            pending_walk.append(step)

    if not transit_legs:
        return None

    first = transit_legs[0]
    walk_min = round(sum(_parse_duration_seconds(s.get("staticDuration", "0s")) for s in first_walk_raw) / 60)

    summary = f"走路{walk_min}分鐘到{first['departure_stop']}，搭{first['mode']}{first['line_name']}"
    if first["headsign"]:
        summary += f"往{first['headsign']}方向"
    for prev, leg in zip(transit_legs, transit_legs[1:]):
        summary += f"，在{prev['arrival_stop']}轉乘{leg['mode']}{leg['line_name']}"
        if leg["headsign"]:
            summary += f"往{leg['headsign']}方向"

    result = {
        "mode": first["mode"],
        "summary": summary,
        "walk_to_station_min": walk_min,
        "wait_min": first["wait_min"],
        "total_min": total_min,
        "landmark": f"上車地點：{first['departure_stop']}",
        # 以下是逐步語音導航要用的完整資訊（第一段，向下相容）
        "steps_before": first["steps_before"],
        "departure_stop": first["departure_stop"],
        "arrival_stop": first["arrival_stop"],
        "line_name": first["line_name"],
        "headsign": first["headsign"],
        "stop_count": first["stop_count"],
        "steps_after": _parse_walk_step_list(pending_walk),
        "transit_legs": transit_legs,
        "modes": [l["mode"] for l in transit_legs],
    }

    if first["mode"] == "公車":
        result["route_name"] = first["line_name"]

    return result


def get_transit_legs(route: dict) -> list:
    """
    取得路線的所有搭乘段。舊格式（沒有 transit_legs，例如假資料）就用最上層欄位組出單一段，
    讓呼叫端不用分兩種情況處理。
    """
    if route.get("transit_legs"):
        return route["transit_legs"]
    if not route.get("departure_stop"):
        return []
    keys = ("mode", "line_name", "headsign", "departure_stop", "arrival_stop",
            "stop_count", "wait_min", "route_name", "steps_before")
    return [{k: route.get(k) for k in keys if k in route}]


def _parse_walk_step_list(step_list: list) -> list:
    """
    把一串「原始WALK步驟資料」解析成語音播報用的步驟清單。
    這個函式被兩種情境共用：
      1. 純步行路線（步驟就是整條路線的所有step）
      2. 大眾運輸路線裡「走到站牌」或「下車後走到目的地」的片段
         （步驟只是完整route裡的一小段WALK step）

    這版加入每個步驟終點的座標（end_lat/end_lng），讓前端可以：
      - 在地圖上畫出這條路線
      - 持續比對使用者GPS位置，快到這個點時才自動報下一句，
        而不是像之前那樣「不管使用者講什麼內容都當作推進信號」

    Returns:
        list: [{"instruction", "distance_m", "voice_text", "end_lat", "end_lng"}, ...]
    """
    steps = []
    for step in step_list:
        instruction = step.get("navigationInstruction", {}).get("instructions", "")
        distance_m = step.get("distanceMeters", 0)

        if not instruction:
            continue

        approx_steps = round(distance_m / 0.7) if distance_m else 0

        if approx_steps > 0:
            voice_text = f"走約{approx_steps}步後，{instruction}"
        else:
            voice_text = instruction

        end_loc = step.get("endLocation", {}).get("latLng", {})

        steps.append({
            "instruction": instruction,
            "distance_m": distance_m,
            "voice_text": voice_text,
            "end_lat": end_loc.get("latitude"),
            "end_lng": end_loc.get("longitude"),
        })

    return steps


def _extract_walk_steps(route: dict) -> list:
    """純步行路線專用：解析整條路線的所有步驟（內部呼叫共用函式）"""
    legs = route.get("legs", [])
    if not legs:
        return []
    return _parse_walk_step_list(legs[0].get("steps", []))


def _compute_route_real(origin: dict, destination: dict, travel_mode: str, allowed_modes: list = None) -> list:
    """
    呼叫 Google Routes API，回傳這種交通方式的所有候選路線（大眾運輸會要替代路線）。

    Args:
        origin/destination: {"lat": float, "lng": float}
        travel_mode: "WALK" 或 "TRANSIT"
        allowed_modes: 大眾運輸限定的工具，例如 ["SUBWAY", "TRAIN", "LIGHT_RAIL", "RAIL"]（只搭軌道）或 ["BUS"]
    """
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": (
            "routes.duration,"
            "routes.legs.steps.travelMode,"
            "routes.legs.steps.staticDuration,"
            "routes.legs.steps.distanceMeters,"
            "routes.legs.steps.navigationInstruction,"
            "routes.legs.steps.endLocation,"
            "routes.legs.steps.transitDetails.stopDetails,"
            "routes.legs.steps.transitDetails.transitLine,"
            "routes.legs.steps.transitDetails.headsign,"
            "routes.legs.steps.transitDetails.headway,"
            "routes.legs.steps.transitDetails.stopCount"
        ),
    }
    body = {
        "origin": {"location": {"latLng": {"latitude": origin["lat"], "longitude": origin["lng"]}}},
        "destination": {"location": {"latLng": {"latitude": destination["lat"], "longitude": destination["lng"]}}},
        "travelMode": travel_mode,
        "languageCode": "zh-TW",
    }
    if travel_mode == "TRANSIT":
        body["computeAlternativeRoutes"] = True
        if allowed_modes:
            body["transitPreferences"] = {"allowedTravelModes": allowed_modes}

    resp = requests.post(ROUTES_API_URL, headers=headers, json=body, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    routes = data.get("routes", [])
    if travel_mode == "WALK":
        if not routes:
            return []
        route = routes[0]
        total_min = round(_parse_duration_seconds(route.get("duration", "0s")) / 60)
        distance_m = sum(s.get("distanceMeters", 0) for leg in route.get("legs", []) for s in leg.get("steps", []))

        # 解析每一段的轉彎指示，組成逐步導航要用的步驟清單
        walk_steps = _extract_walk_steps(route)

        return [{
            "mode": "步行",
            "modes": ["步行"],
            "summary": "全程步行前往目的地",
            "walk_to_station_min": 0,
            "wait_min": 0,
            "total_min": total_min,
            "distance_m": distance_m,
            "landmark": "",
            "steps": walk_steps,  # 逐步導航要用的完整步驟清單
        }]

    # TRANSIT模式：每條候選路線都解析完整搭乘細節
    return [r for r in (_transit_route_to_dict(route) for route in routes) if r]


# ============================================================
# 交通方式偏好：「搭捷運」「搭公車」「走路」…
# ============================================================
# 使用者講法 → 路線裡要出現的交通工具（mode 用 VEHICLE_TYPE_MAP 的中文）
MODE_PREFERENCE_KEYWORDS = [
    ("捷運", ["捷運", "地鐵", "MRT", "機捷", "機場捷運", "輕軌"]),
    ("高鐵", ["高鐵"]),
    ("火車", ["火車", "台鐵", "臺鐵", "區間車", "自強號", "莒光號"]),
    ("公車", ["公車", "巴士", "客運", "公交"]),
    ("步行", ["走路", "步行", "用走的", "走過去", "走去"]),
    ("大眾運輸", ["搭車", "坐車", "大眾運輸"]),
]

# 偏好 → 向 Google 要路線時限定的交通工具
ALLOWED_MODES_FOR = {
    "捷運": ["SUBWAY", "LIGHT_RAIL"],
    "火車": ["TRAIN", "RAIL"],
    "高鐵": ["TRAIN", "RAIL"],
    "公車": ["BUS"],
}

MAX_WALK_MIN = 40   # 全程步行超過這個時間就不推薦（除非只剩走路）


def detect_mode_preference(text: str):
    """從一句話找出使用者想搭的交通方式；沒有就回傳 None。"""
    t = (text or "").upper()
    for mode, kws in MODE_PREFERENCE_KEYWORDS:
        if any(k.upper() in t for k in kws):
            return mode
    return None


def route_matches(route: dict, pref: str) -> bool:
    modes = route.get("modes") or [route.get("mode")]
    if pref == "步行":
        return modes == ["步行"]
    if pref == "大眾運輸":
        return modes != ["步行"]
    return pref in modes


def modes_phrase(route: dict, with_verb: bool = False) -> str:
    """路線用到的交通方式，例如「公車」「公車轉捷運」「捷運轉火車」；with_verb=True 會加「搭」（步行除外）。"""
    modes = route.get("modes") or [route.get("mode")]
    if modes == ["步行"]:
        return "步行"
    if with_verb:
        return "搭" + modes_phrase(route)
    out = []
    for m in modes:
        if not out or out[-1] != m:
            out.append(m)
    if len(modes) > 1 and len(out) == 1:
        return f"{out[0]}（轉乘{len(modes) - 1}次）"
    return "轉".join(out)


class RouteSession:
    """管理一次導航請求的候選路線池，處理「換一個」與「搭捷運／搭公車／走路」的切換。"""

    def __init__(self, origin: dict, destination: dict):
        self.origin = origin
        self.destination = destination

        if USE_REAL_ROUTES:
            self.candidates = self._fetch_real_candidates()
        else:
            self.candidates = self._rank([dict(r) for r in MOCK_ROUTE_POOL])

        self.current_index = 0

    # ---------- 取得候選 ----------
    @staticmethod
    def _key(r: dict) -> tuple:
        legs = r.get("transit_legs") or []
        return (tuple(r.get("modes") or [r.get("mode")]),
                tuple((l.get("line_name"), l.get("departure_stop")) for l in legs))

    def _rank(self, routes: list) -> list:
        """去掉重複的、太久的走路，依時間排序。"""
        uniq, seen = [], set()
        for r in routes:
            r.setdefault("modes", [l["mode"] for l in r.get("transit_legs", [])] or [r.get("mode")])
            k = self._key(r)
            if k not in seen:
                seen.add(k)
                uniq.append(r)
        transit = [r for r in uniq if r["modes"] != ["步行"]]
        walk = [r for r in uniq if r["modes"] == ["步行"]]
        # 走路太久就不放進候選（只剩走路時例外）
        walk = [r for r in walk if r["total_min"] <= MAX_WALK_MIN] or ([] if transit else walk)
        return sorted(transit + walk, key=lambda r: r["total_min"])

    def _fetch(self, travel_mode: str, allowed: list = None) -> list:
        try:
            return _compute_route_real(self.origin, self.destination, travel_mode, allowed)
        except Exception as e:  # noqa: BLE001
            print(f"   [警告] Routes API 呼叫失敗（{travel_mode} {allowed or ''}）：{e}")
            return []

    def _fetch_real_candidates(self) -> list:
        # 一般大眾運輸（含替代路線）＋只搭軌道＋步行
        found = self._fetch("TRANSIT") + self._fetch("TRANSIT", ["SUBWAY", "TRAIN", "LIGHT_RAIL", "RAIL"]) \
            + self._fetch("WALK")
        if not found:
            print("   [提醒] 真實路線全部取得失敗，改用假資料候選池")
            return self._rank([dict(r) for r in MOCK_ROUTE_POOL])
        return self._rank(found)

    # ---------- 切換 ----------
    def get_current_route(self) -> dict:
        return self.candidates[self.current_index]

    def next_route(self) -> dict:
        self.current_index = (self.current_index + 1) % len(self.candidates)
        return self.get_current_route()

    def choose_mode(self, pref: str):
        """
        切到符合偏好的路線（最快的那條）。候選裡沒有時，真實模式會再向 Google 查一次限定交通工具的路線。
        找不到回傳 None。
        """
        matches = [i for i, r in enumerate(self.candidates) if route_matches(r, pref)]
        if not matches and USE_REAL_ROUTES:
            extra = self._fetch("WALK") if pref == "步行" else \
                self._fetch("TRANSIT", ALLOWED_MODES_FOR.get(pref))
            extra = [r for r in extra if route_matches(r, pref)]
            if extra:
                cur = self.get_current_route()
                pool = self.candidates + extra
                for r in pool:
                    r.setdefault("modes", [r.get("mode")])
                uniq, seen = [], set()
                for r in pool:
                    if self._key(r) not in seen:
                        seen.add(self._key(r))
                        uniq.append(r)
                self.candidates = sorted(uniq, key=lambda r: r["total_min"])
                self.current_index = self.candidates.index(cur)
                matches = [i for i, r in enumerate(self.candidates) if route_matches(r, pref)]
        if not matches:
            return None
        self.current_index = min(matches, key=lambda i: self.candidates[i]["total_min"])
        return self.get_current_route()

    def change_mode(self):
        """「我想改搭車」「改用走的」這類沒指定工具的講法：換到交通方式不一樣的下一條。"""
        cur = tuple(self.get_current_route().get("modes") or [])
        n = len(self.candidates)
        for step in range(1, n):
            i = (self.current_index + step) % n
            if tuple(self.candidates[i].get("modes") or []) != cur:
                self.current_index = i
                return self.get_current_route()
        return self.next_route()

    # ---------- 語音 ----------
    def options_hint(self) -> str:
        """告訴使用者還能怎麼選：有幾個方案、候選裡有哪些交通方式。"""
        n = len(self.candidates)
        kinds = []
        for r in self.candidates:
            for m in (r.get("modes") or []):
                if m not in kinds and m != "步行":
                    kinds.append(m)
        tips = "、".join(f"「搭{m}」" for m in kinds[:3])
        if n <= 1:
            return "目前只找到這個方案"
        hint = f"共有{n}個方案，可以說「換一個」"
        if tips:
            hint += f"，或直接說{tips}"
        return hint

    def voice_summary(self, route: dict = None, with_hint: bool = None) -> str:
        """with_hint 沒指定時：第一次講才附上「共有幾個方案、可以怎麼選」，之後切換就不重複，比較好聽。"""
        r = route or self.get_current_route()
        if with_hint is None:
            with_hint = not getattr(self, "_hint_told", False)
        self._hint_told = True
        hint = f"。{self.options_hint()}" if with_hint else ""
        if (r.get("modes") or [r["mode"]]) == ["步行"]:
            far = "，路程比較遠，建議搭車" if r["total_min"] > MAX_WALK_MIN else ""
            return f"建議步行，{r['summary']}，全程約{r['total_min']}分鐘{far}{hint}。要開始嗎？"
        return (
            f"建議搭{modes_phrase(r)}，{r['summary']}，"
            f"預計{r['wait_min']}分鐘後到站，全程約{r['total_min']}分鐘{hint}。要開始嗎？"
        )


if __name__ == "__main__":
    print(f"目前模式：{'真實Google Routes API' if USE_REAL_ROUTES else '假資料（未偵測到GOOGLE_MAPS_API_KEY）'}\n")

    session = RouteSession(
        origin={"lat": 25.033, "lng": 121.564},
        destination={"lat": 25.0478, "lng": 121.5170},
    )
    print("初始推薦：", session.voice_summary())
    print("完整資料：", session.get_current_route())
    print()
    print("換一個後：", session.voice_summary(session.next_route()))
    print("完整資料：", session.get_current_route())