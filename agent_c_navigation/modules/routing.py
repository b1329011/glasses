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


def _extract_transit_summary(route_data: dict) -> dict:
    """
    從 Routes API 的完整回應中，解析出完整的大眾運輸搭乘資訊，
    包含上車前走路段、搭乘細節（站名/路線/方向/站數）、下車後走路段，
    這樣才有足夠資訊做完整的逐步語音導航（不只是摘要）。

    Returns:
        dict: 統一格式的路線資訊，找不到搭乘細節時回傳 None（呼叫端會fallback）
    """
    routes = route_data.get("routes", [])
    if not routes:
        return None

    route = routes[0]
    total_min = round(_parse_duration_seconds(route.get("duration", "0s")) / 60)

    legs = route.get("legs", [])
    if not legs:
        return None

    all_steps = legs[0].get("steps", [])

    # 把整條路線切成三段：上車前的走路段、大眾運輸段、下車後的走路段
    steps_before = []
    transit_step = None
    steps_after = []

    for step in all_steps:
        if step.get("travelMode") == "TRANSIT" and "transitDetails" in step and transit_step is None:
            transit_step = step
        elif transit_step is None:
            steps_before.append(step)
        else:
            steps_after.append(step)

    if transit_step is None:
        return None

    walk_before_list = _parse_walk_step_list(steps_before)
    walk_after_list = _parse_walk_step_list(steps_after)
    walk_min = round(sum(_parse_duration_seconds(s.get("staticDuration", "0s")) for s in steps_before) / 60)

    details = transit_step["transitDetails"]
    departure_stop = details.get("stopDetails", {}).get("departureStop", {}).get("name", "附近站牌")
    arrival_stop = details.get("stopDetails", {}).get("arrivalStop", {}).get("name", "目的地站")
    line_info = details.get("transitLine", {})
    line_name = line_info.get("nameShort") or line_info.get("name") or "未知路線"
    line_name = _translate_line_name(line_name)
    vehicle_type_raw = line_info.get("vehicle", {}).get("type", "")
    vehicle_label = VEHICLE_TYPE_MAP.get(vehicle_type_raw, "大眾運輸")
    headsign = details.get("headsign", "")
    stop_count = details.get("stopCount", None)

    # headway是「平均班距」，用來估計候車時間（不是即時到站時間，
    # 真正即時到站時間是靠 transit.py 呼叫TDX API取得，這裡只是沒有TDX資料時的估計值）
    headway_sec = details.get("headway", None)
    wait_min = round(int(str(headway_sec).rstrip("s")) / 60) if headway_sec else 5

    summary = f"走路{walk_min}分鐘到{departure_stop}，搭{vehicle_label}{line_name}"
    if headsign:
        summary += f"往{headsign}方向"

    result = {
        "mode": vehicle_label,
        "summary": summary,
        "walk_to_station_min": walk_min,
        "wait_min": wait_min,
        "total_min": total_min,
        "landmark": f"上車地點：{departure_stop}",
        # 以下是逐步語音導航要用的完整資訊
        "steps_before": walk_before_list,   # 走到站牌/月台的步驟
        "departure_stop": departure_stop,   # 上車站名
        "arrival_stop": arrival_stop,       # 下車站名
        "line_name": line_name,             # 路線名稱
        "headsign": headsign,               # 往哪個方向
        "stop_count": stop_count,           # 中途共幾站
        "steps_after": walk_after_list,     # 下車後走到目的地的步驟
    }

    if vehicle_label == "公車":
        result["route_name"] = line_name

    return result


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


def _compute_route_real(origin: dict, destination: dict, travel_mode: str) -> dict:
    """
    呼叫 Google Routes API 取得單一交通方式的路線。

    Args:
        origin/destination: {"lat": float, "lng": float}
        travel_mode: "WALK" 或 "TRANSIT"
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

    resp = requests.post(ROUTES_API_URL, headers=headers, json=body, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    if travel_mode == "WALK":
        routes = data.get("routes", [])
        if not routes:
            return None
        route = routes[0]
        total_min = round(_parse_duration_seconds(route.get("duration", "0s")) / 60)

        # 解析每一段的轉彎指示，組成逐步導航要用的步驟清單
        walk_steps = _extract_walk_steps(route)

        return {
            "mode": "步行",
            "summary": "全程步行前往目的地",
            "walk_to_station_min": 0,
            "wait_min": 0,
            "total_min": total_min,
            "landmark": "",
            "steps": walk_steps,  # 逐步導航要用的完整步驟清單
        }

    # TRANSIT模式：解析完整搭乘細節
    result = _extract_transit_summary(data)
    return result


class RouteSession:
    """管理一次導航請求的候選路線池，處理「換一個」的切換邏輯。"""

    def __init__(self, origin: dict, destination: dict):
        self.origin = origin
        self.destination = destination

        if USE_REAL_ROUTES:
            self.candidates = self._fetch_real_candidates()
        else:
            self.candidates = sorted(MOCK_ROUTE_POOL, key=lambda r: r["total_min"])

        self.current_index = 0

    def _fetch_real_candidates(self) -> list:
        candidates = []
        for mode in ["TRANSIT", "WALK"]:
            try:
                result = _compute_route_real(self.origin, self.destination, mode)
                if result:
                    candidates.append(result)
                else:
                    print(f"   [提醒] {mode} 路線查無搭乘細節可解析")
            except Exception as e:
                print(f"   [警告] Routes API 呼叫失敗（{mode}）：{e}")

        if not candidates:
            print("   [提醒] 真實路線全部取得失敗，改用假資料候選池")
            return sorted(MOCK_ROUTE_POOL, key=lambda r: r["total_min"])

        return sorted(candidates, key=lambda r: r["total_min"])

    def get_current_route(self) -> dict:
        return self.candidates[self.current_index]

    def next_route(self) -> dict:
        self.current_index = (self.current_index + 1) % len(self.candidates)
        return self.get_current_route()

    def voice_summary(self, route: dict = None) -> str:
        r = route or self.get_current_route()
        if r["mode"] == "步行":
            return f"建議{r['mode']}，{r['summary']}，全程約{r['total_min']}分鐘，要開始嗎？"
        return (
            f"建議搭{r['mode']}，{r['summary']}，"
            f"預計{r['wait_min']}分鐘後到站，全程約{r['total_min']}分鐘，要開始嗎？"
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