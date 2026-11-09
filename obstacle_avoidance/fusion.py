"""
融合兩個偵測器的輸出，產生 Navigation Agent 的事件訊息。

規則：
- 最終等級取兩者較高者（critical > warning > none）
- 兩者同等級且都有危險時，obstacle_type = "multiple"，語音合併提示
- critical → 強震動；warning → 輕震動；none → 不發警報，只記錄（event_type = "status_log"）

fuse()          模擬驗證用：深度相機 ROI 距離 + 手杖 ToF
fuse_hazards()  鏡頭即時偵測用：前方障礙物、懸空障礙物、斷崖、樓梯等多種來源
"""

from datetime import datetime, timezone

AGENT_NAME = "navigation_agent"
LEVEL_RANK = {"none": 0, "warning": 1, "critical": 2}
VIBRATION = {"critical": "strong", "warning": "light", "none": None}

AUDIO = {
    ("hanging_obstacle", "critical"): "前方有懸空障礙物，請注意",
    ("hanging_obstacle", "warning"): "前方約{distance:.1f}公尺有懸空障礙物",
    ("ground_drop", "critical"): "前方地面有大落差，請立即停下",
    ("ground_drop", "warning"): "前方有階梯或落差，請放慢腳步",
    ("multiple", "critical"): "前方有懸空障礙物，且地面有落差，請立即停下",
    ("multiple", "warning"): "前方有懸空障礙物與地面落差，請放慢腳步",
}

# 鏡頭即時偵測的語音；{direction} 為「左前方／正前方／右前方」，{label} 為物體名稱
CAMERA_AUDIO = {
    ("front_obstacle", "critical"): "{direction}有{label}，請停下",
    ("front_obstacle", "warning"): "{direction}約{distance:.1f}公尺有{label}",
    ("hanging_obstacle", "critical"): "前方有懸空障礙物，請注意頭部",
    ("hanging_obstacle", "warning"): "前方約{distance:.1f}公尺有懸空障礙物",
    ("cliff", "critical"): "前方有斷崖或大落差，請立即停下",
    ("cliff", "warning"): "前方約{distance:.1f}公尺地面有大落差",
    ("stairs_down", "critical"): "前方有向下樓梯，請停下",
    ("stairs_down", "warning"): "前方約{distance:.1f}公尺有向下樓梯",
    ("stairs_up", "critical"): "前方有向上樓梯",
    ("stairs_up", "warning"): "前方約{distance:.1f}公尺有向上樓梯",
    ("step_down", "critical"): "前方地面有落差，請小心",
    ("step_down", "warning"): "前方約{distance:.1f}公尺地面有落差",
    ("step_up", "critical"): "前方有台階，請抬腳",
    ("step_up", "warning"): "前方約{distance:.1f}公尺有台階",
}
# 同等級時的優先順序：會跌倒／墜落的地面危險優先
HAZARD_PRECEDENCE = ["cliff", "stairs_down", "step_down", "front_obstacle", "hanging_obstacle", "stairs_up", "step_up"]


def _iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_alert(event: dict) -> bool:
    return event["event_type"] == "obstacle_alert"


def fuse(hanging_result: dict, drop_result: dict, timestamp: str = None) -> dict:
    """融合懸空障礙物與地面落差偵測結果。timestamp 為 ISO 8601 字串，省略時用現在時間。"""
    h_level = hanging_result["level"]
    g_level = drop_result["level"]
    h_rank, g_rank = LEVEL_RANK[h_level], LEVEL_RANK[g_level]
    priority = h_level if h_rank >= g_rank else g_level

    if priority == "none":
        obstacle_type = None
    elif h_rank == g_rank:
        obstacle_type = "multiple"
    elif h_rank > g_rank:
        obstacle_type = "hanging_obstacle"
    else:
        obstacle_type = "ground_drop"

    data = {"obstacle_type": obstacle_type}
    if obstacle_type in ("hanging_obstacle", "multiple") or priority == "none":
        data["distance"] = None if hanging_result["distance"] is None else round(hanging_result["distance"], 2)
    if obstacle_type in ("ground_drop", "multiple") or priority == "none":
        data["drop_height_cm"] = drop_result.get("drop_cm")

    audio = None
    if priority != "none":
        audio = AUDIO[(obstacle_type, priority)].format(distance=hanging_result["distance"] or 0)

    return {
        "agent": AGENT_NAME,
        "timestamp": timestamp or _iso_now(),
        "event_type": "obstacle_alert" if priority != "none" else "status_log",
        "priority": priority,
        "data": data,
        "action_required": {
            "vibration": VIBRATION[priority],
            "audio_message": audio,
        },
    }


def fuse_hazards(hazards: list, timestamp: str = None) -> dict:
    """
    融合鏡頭即時偵測的多個危險來源。
    hazards: [{"type": ..., "level": ..., "distance": m, "label": 物體名稱?, "direction": 方位?, ...}]
    最高等級者決定 priority 與語音；其餘有危險的來源列在 data.others。
    """
    active = [h for h in hazards if h["level"] != "none"]
    active.sort(key=lambda h: (-LEVEL_RANK[h["level"]], HAZARD_PRECEDENCE.index(h["type"])))
    top = active[0] if active else None
    priority = top["level"] if top else "none"

    data = {"obstacle_type": top["type"] if top else None}
    audio = None
    if top:
        data["distance"] = top.get("distance")
        for k in ("label", "direction", "height_change_m", "steps"):
            if top.get(k) is not None:
                data[k] = top[k]
        data["others"] = [{"type": h["type"], "level": h["level"], "distance": h.get("distance")} for h in active[1:]]
        audio = CAMERA_AUDIO[(top["type"], priority)].format(
            distance=top.get("distance") or 0, label=top.get("label") or "障礙物", direction=top.get("direction") or "前方")

    return {
        "agent": AGENT_NAME,
        "timestamp": timestamp or _iso_now(),
        "event_type": "obstacle_alert" if top else "status_log",
        "priority": priority,
        "data": data,
        "action_required": {
            "vibration": VIBRATION[priority],
            "audio_message": audio,
        },
    }
