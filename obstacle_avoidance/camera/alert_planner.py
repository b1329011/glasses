"""
警示規劃：從每一幀的所有偵測結果中，挑出「現在最該講的一件事」，並決定要不要開口。

為什麼需要它：每一幀都可能有好幾個物體、距離也會小幅跳動，逐幀播報會太吵。這裡做三件事：

1. 分類與排序
   每個候選危險有「距離區間」與「類別優先順序」：
       區間：stop（< 1.2 m，直接說「請停下」）> near（1.2~2.5 m）> ahead（2.5~5.5 m 提早告知）
       類別：落差 > 樓梯／手扶梯 > 車輛 > 牆壁 > 懸空物 > 行人 > 固定物 > 一般物品 > 未知障礙 > 台階
   先比區間、再比「是否擋在路上」、再比類別、最後比距離；只有一個第一名。
   左右 1 m 以外、或不在分類表裡的東西（杯子、筆電…）一律不警示。

2. 用語（依區間與位置）
       正前方：「前方 3 公尺有行人」
       左右側：「左前方 3 公尺有機車，稍微往右走」
       頭部高：「上方 2 公尺有招牌，注意頭部」
       樓梯：  「前方有手扶梯，請小心」
       太近或大落差：「請停下」

3. 節流（只講要緊的）
   畫面下方會列出所有看到的東西；語音只在以下時機開口：
   - 「請停下」：進入 stop 時立刻講，持續時每 3 秒重複
   - near 區間的新目標、或目標從 ahead 靠近到 near（ahead 預設只列在畫面，SPEAK_AHEAD 可改）
   兩次播報至少間隔 4 秒（請停下與「變得更近」不受限）；
   10 秒內唸過「同類東西、同方向」就不再唸（偵測框閃一下、編號換了也不會重唸）；
   near 同一目標每 10 秒最多一次；目標消失 6 秒後再出現才重新播報；距離區間有遲滯。
"""

import math
import time
from collections import deque

from camera import config as cfg

ZONE_RANK = {"none": 0, "ahead": 1, "near": 2, "stop": 3}
ZONE_PRIORITY = {"stop": "critical", "near": "warning", "ahead": "notice", "none": "none"}
ZONE_VIBRATION = {"stop": "strong", "near": "medium", "ahead": "light", "none": None}


def speak_distance(d):
    """唸起來順的距離：四捨五入到 0.5 公尺，整數不帶小數。"""
    r = max(0.5, round(d * 2) / 2)
    return f"{int(r)}" if r == int(r) else f"{r:.1f}"


def direction_of(lateral, forward):
    ang = math.degrees(math.atan2(lateral, max(forward, 0.1)))
    if ang < -cfg.SIDE_ANGLE_DEG:
        return "left"
    if ang > cfg.SIDE_ANGLE_DEG:
        return "right"
    return "front"


DIR_ZH = {"left": "左前方", "right": "右前方", "front": "前方"}


class Candidate:
    """一個候選危險。key 用來辨識「同一個目標」（物體追蹤編號或幾何類型）。"""

    def __init__(self, key, category, type_, label, distance, direction="front", blocks_path=True,
                 overhead=False, height_change=None, free_side=None):
        self.key = key
        self.category = category
        self.type = type_
        self.label = label
        self.distance = distance
        self.direction = direction
        self.blocks_path = blocks_path
        self.overhead = overhead
        self.height_change = height_change
        self.free_side = free_side
        self.zone = "none"

    @property
    def priority(self):
        return cfg.CATEGORY_PRIORITY.get(self.category, 0)

    def sort_key(self):
        # 同一距離區間：擋在路上的優先 → 類別優先順序 → 越近越優先
        return (ZONE_RANK[self.zone], self.blocks_path, self.priority, -self.distance)

    def as_dict(self):
        return {"key": self.key, "category": self.category, "type": self.type, "label": self.label,
                "distance": round(self.distance, 2), "direction": self.direction, "zone": self.zone,
                "blocks_path": self.blocks_path}


def raw_zone(c):
    """不考慮遲滯時的距離區間。"""
    d = c.distance
    if c.category == "drop":
        # 大落差：2 m 內直接停；更遠就提早告知
        if d <= cfg.DROP_STOP_DISTANCE_M:
            return "stop"
    elif c.blocks_path and d < cfg.STOP_DISTANCE_M and c.category not in ("step",):
        if c.category == "stairs" and c.type in ("stairs_up", "escalator", "stairs"):
            return "near"  # 往上的樓梯、手扶梯靠近時提醒小心即可，不叫停
        return "stop"
    if d < cfg.NEAR_DISTANCE_M:
        return "near"
    if d <= cfg.AHEAD_DISTANCE_M:
        return "ahead"
    return "none"


def message_for(c):
    """依區間與位置組出語音文字。"""
    if c.zone == "stop":
        return "請停下"
    d = speak_distance(c.distance)
    if c.category == "drop":
        return f"前方{d}公尺地面有大落差，請小心"
    if c.category == "stairs":
        return f"{DIR_ZH[c.direction]}有{c.label}，請小心"
    if c.category == "step":
        return f"前方{d}公尺{'有台階' if c.type == 'step_up' else '地面有落差'}，請小心"
    if c.overhead:
        return f"上方{d}公尺有{c.label}，注意頭部"
    if c.direction == "left":
        return f"左前方{d}公尺有{c.label}，稍微往右走"
    if c.direction == "right":
        return f"右前方{d}公尺有{c.label}，稍微往左走"
    msg = f"前方{d}公尺有{c.label}"
    if c.zone == "near" and c.free_side:
        msg += f"，請從{'左' if c.free_side == 'left' else '右'}側繞過"
    return msg


class AlertPlanner:
    """每條連線一個：保存遲滯、確認與播報紀錄。"""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.zones = {}                       # key → 目前區間（含遲滯）
        self.seen = {}                        # key → 最近一次看到的時間
        self.history = {}                     # key → 最近幾幀是否出現（確認用）
        self.announced = {}                   # key → {zone, distance, time}
        self.recent = {}                      # (名稱, 方向) → {zone, distance, time}，跨編號去重
        self.last_speech = -1e9
        self.last_top_zone = "none"
        self.speak_ahead = cfg.SPEAK_AHEAD

    def reset(self):
        speak_ahead = self.speak_ahead
        self.__init__(self.clock)
        self.speak_ahead = speak_ahead

    def _zone_with_hysteresis(self, c):
        new = raw_zone(c)
        old = self.zones.get(c.key, "none")
        if ZONE_RANK[new] < ZONE_RANK[old]:
            # 要降級時，距離必須再退開 ZONE_HYSTERESIS_M 才算數
            c2 = Candidate(c.key, c.category, c.type, c.label, max(0.0, c.distance - cfg.ZONE_HYSTERESIS_M),
                           c.direction, c.blocks_path, c.overhead)
            if ZONE_RANK[raw_zone(c2)] >= ZONE_RANK[old]:
                new = old
        self.zones[c.key] = new
        return new

    def _confirmed(self, key, present_keys):
        """最近 HAZARD_WINDOW_FRAMES 幀中出現至少 HAZARD_CONFIRM_FRAMES 幀才算數。"""
        hist = self.history.setdefault(key, deque(maxlen=cfg.HAZARD_WINDOW_FRAMES))
        hist.append(key in present_keys)
        return sum(hist) >= cfg.HAZARD_CONFIRM_FRAMES

    def plan(self, candidates, timestamp=None):
        """回傳 (event, ranked_candidates)。event 為 Navigation Agent 格式，多了 speak / urgency。"""
        now = self.clock()
        present = {c.key for c in candidates}
        for key, hist in self.history.items():
            if key not in present:
                hist.append(False)
        for c in candidates:
            self.seen[c.key] = now
            c.zone = self._zone_with_hysteresis(c)
        active = [c for c in candidates if c.zone != "none" and self._confirmed(c.key, present)]
        active.sort(key=lambda c: c.sort_key(), reverse=True)
        top = active[0] if active else None

        speak = False
        reason = None
        if top:
            speak, reason = self._should_speak(top, now)
            if speak:
                record = {"zone": top.zone, "distance": top.distance, "time": now}
                self.announced[top.key] = record
                self.recent[(top.label, top.direction)] = record
                self.last_speech = now
        self.last_top_zone = top.zone if top else "none"
        # 很久沒看到的目標，忘掉播報紀錄（再出現時重新播報）
        for key in [k for k, t in self.seen.items() if now - t > cfg.FORGET_AFTER_S]:
            for d in (self.seen, self.announced, self.zones, self.history):
                d.pop(key, None)
        for sig in [s for s, r in self.recent.items() if now - r["time"] > cfg.DEDUPE_S]:
            del self.recent[sig]

        return self._event(top, active, speak, reason, timestamp), active

    def _should_speak(self, c, now):
        prev = self.announced.get(c.key)
        if c.zone == "stop":
            if self.last_top_zone != "stop" or not prev or prev["zone"] != "stop":
                return True, "進入停止距離"
            return (now - prev["time"] >= cfg.REPEAT_STOP_S), "持續太近"
        if c.zone == "ahead" and not self.speak_ahead:
            return False, None  # 提早告知只列在畫面上

        escalated = prev is not None and ZONE_RANK[c.zone] > ZONE_RANK[prev["zone"]]
        if prev is None:
            # 編號是新的，但 10 秒內唸過同類、同方向、距離差不多的東西 → 視為同一個，不重唸
            seen = self.recent.get((c.label, c.direction))
            if seen and ZONE_RANK[seen["zone"]] >= ZONE_RANK[c.zone] and seen["distance"] - c.distance < 1.0:
                self.announced[c.key] = dict(seen)
                return False, None
        if not escalated and now - self.last_speech < cfg.MIN_SPEECH_GAP_S:
            return False, None  # 剛講過話，先等一下（下一幀若仍是第一名會再判斷）

        if prev is None:
            return True, "新目標"
        if escalated:
            return True, "更靠近了"
        if c.zone == "near" and now - prev["time"] >= cfg.REPEAT_NEAR_S:
            return True, "仍在附近"
        if c.zone == "ahead" and prev["distance"] - c.distance >= cfg.AHEAD_REANNOUNCE_DROP_M:
            return True, "持續接近"
        return False, None

    @staticmethod
    def _event(top, active, speak, reason, timestamp):
        from fusion import AGENT_NAME, _iso_now

        zone = top.zone if top else "none"
        data = {"obstacle_type": top.type if top else None}
        if top:
            data.update({
                "category": top.category, "label": top.label, "distance": round(top.distance, 2),
                "direction": top.direction, "zone": zone,
                "others": [c.as_dict() for c in active[1:4]],
            })
            if top.height_change is not None:
                data["height_change_m"] = top.height_change
        return {
            "agent": AGENT_NAME,
            "timestamp": timestamp or _iso_now(),
            "event_type": "obstacle_alert" if top else "status_log",
            "priority": ZONE_PRIORITY[zone],
            "data": data,
            "action_required": {
                "vibration": ZONE_VIBRATION[zone],
                "urgency": ZONE_RANK[zone],              # 0~3，前端依此決定震動節奏
                "audio_message": message_for(top) if top else None,
                "speak": speak,                          # 這一幀要不要唸出來（已節流）
                "speak_reason": reason,
            },
        }
