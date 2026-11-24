"""
車站指標導引模組（Agent C 導航 × sign_detector 指標辨識）
====================================
任何捷運／火車／高鐵站都適用：把導航的搭乘資訊（線名、往哪、普通車/直達車）轉成要依序找的指標，
例如「搭捷運機場線普通車往台北車站方向，在新北產業園區轉乘捷運環狀線往大坪林站方向」→
    體育大學站：「機場捷運」→「往台北車站」（提醒搭普通車）
    新北產業園區站（站內轉乘）：「環狀線」→「往大坪林」
以下原本是以台北車站為例的說明：

台北車站是捷運淡水信義線、板南線、機場捷運、台鐵、高鐵交會的大站，
地下通道複雜、GPS 在站內幾乎失效，所以進到台北車站後改用「看指標牌」導引：

    導航（routing.py）知道：要搭哪條線、往哪個方向、在哪裡轉車、在哪裡下車
        ↓  轉成「要依序找的指標」
    本模組產生指標序列，例如：
        進站搭淡水信義線往淡水 → ["淡水信義線", "往淡水"]
        板南線下車轉淡水信義線 → ["淡水信義線", "往象山"]
        轉台鐵 / 高鐵           → ["台鐵", "往樹林"] / ["高鐵", "往左營"]
        下車出站               → ["出口"]
        ↓
    眼鏡鏡頭每張影格 + 目前要找的指標 → sign_detector /api/navigate
        ↓
    回傳「淡水信義線在往左的方向」，連續穩定後才播報（避免畫面晃動時亂報）

什麼時候算「在台北車站」：
    1. 路線上的站名是台北車站（departure_stop / arrival_stop），或
    2. GPS 進入台北車站周圍地理圍欄（GEOFENCE_RADIUS_M）
    且當下要進出的是軌道運輸（捷運/火車/高鐵）——公車在台北車站站牌上下車不用進站。

使用者確認「到了」才前進到下一個指標（比照「上車了/下車了」的語音確認設計），
因為單靠影像無法可靠判斷「已經走到那條線的月台區」。
"""

import math
import re
import time

# ============================================================
# 台北車站判定
# ============================================================
TAIPEI_MAIN_CENTER = (25.0478, 121.5170)
GEOFENCE_RADIUS_M = 350  # 涵蓋捷運/台鐵/高鐵大廳；最近的其他捷運站（北門、善導寺）都在 600m 以外

TAIPEI_MAIN_NAMES = ["台北車站", "臺北車站", "台北火車站", "臺北火車站", "taipei main station", "taipei main"]
# 台鐵/高鐵在 Google 資料裡可能只寫「臺北」「Taipei」，這些要完全相等才算（避免「台北101」誤判）
TAIPEI_MAIN_EXACT = ["台北", "臺北", "台北站", "臺北站", "taipei", "taipei station"]

RAIL_MODES = ("捷運", "火車", "高鐵", "輕軌")


def _haversine_m(lat1, lng1, lat2, lng2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def is_taipei_main_name(name: str) -> bool:
    if not name:
        return False
    n = name.strip().lower()
    return n in TAIPEI_MAIN_EXACT or any(k in n for k in TAIPEI_MAIN_NAMES)


def in_taipei_main_geofence(lat, lng) -> bool:
    if lat is None or lng is None:
        return False
    return _haversine_m(lat, lng, *TAIPEI_MAIN_CENTER) <= GEOFENCE_RADIUS_M


def is_at_taipei_main(stop_name: str = None, lat=None, lng=None) -> bool:
    """站名是台北車站，或 GPS 落在台北車站地理圍欄內。"""
    return is_taipei_main_name(stop_name) or in_taipei_main_geofence(lat, lng)


# ============================================================
# 路線名稱 / 方向（終點站）正規化
# ============================================================
# 雙北＋機場捷運的路線（指標牌上的中文線名 → Google/口語可能出現的寫法）
LINE_ALIASES = {
    "文湖線": ["文湖線", "Wenhu Line", "Wenhu", "棕線", "BR"],
    "淡水信義線": ["淡水信義線", "淡水線", "信義線", "Tamsui-Xinyi Line", "Tamsui-Xinyi", "紅線", "R"],
    "松山新店線": ["松山新店線", "松山線", "新店線", "Songshan-Xindian Line", "Songshan-Xindian", "綠線", "G"],
    "中和新蘆線": ["中和新蘆線", "中和線", "新蘆線", "Zhonghe-Xinlu Line", "Zhonghe-Xinlu", "橘線", "O"],
    "板南線": ["板南線", "Bannan Line", "Bannan", "藍線", "BL"],
    "環狀線": ["環狀線", "Circular Line", "Circular", "黃線", "Y"],
    "機場捷運": ["機場捷運", "桃園機場捷運", "機捷", "機場線", "Taoyuan Airport MRT", "Airport MRT", "Airport Line", "A"],
    "淡海輕軌": ["淡海輕軌", "Danhai LRT", "Danhai"],
    "安坑輕軌": ["安坑輕軌", "Ankeng LRT", "Ankeng"],
}

# 指標牌上寫的方向（終點站），以及會出現在 headsign 的區間車/英文寫法。
# 表裡沒有的線或方向，會直接用 headsign（去掉「站」字），例如「往大坪林」。
LINE_TERMINI = {
    "淡水信義線": {
        "淡水": ["淡水", "Tamsui", "北投", "Beitou"],
        "象山": ["象山", "Xiangshan", "大安", "Daan"],
    },
    "板南線": {
        "頂埔": ["頂埔", "Dingpu", "亞東醫院", "Far Eastern Hospital"],
        "南港展覽館": ["南港展覽館", "Nangang Exhibition Center", "昆陽", "Kunyang", "南港"],
    },
    "機場捷運": {
        "台北車站": ["台北車站", "臺北車站", "台北", "臺北", "Taipei"],
        "機場": ["機場", "Airport", "航廈", "Terminal"],
        "老街溪": ["老街溪", "Laojie River", "環北", "Huanbei", "中壢"],
    },
}

# 機場捷運分普通車／直達車，月台或候車位置不同，要特別提醒
SERVICE_TYPES = ["普通車", "直達車"]


def normalize_line(line_name: str):
    """把路線名稱（中英文、代碼）轉成指標牌上的中文線名；不是台北車站的線回傳 None。"""
    if not line_name:
        return None
    s = line_name.strip()
    for canonical, aliases in LINE_ALIASES.items():
        for a in aliases:
            # 單字母代碼（R/BL/A）要完全相等，避免誤配
            if (len(a) <= 2 and s.upper() == a.upper()) or (len(a) > 2 and a.lower() in s.lower()):
                return canonical
    return None


def _strip_station(name: str) -> str:
    n = (name or "").strip()
    for p in ("捷運", "臺鐵", "台鐵", "高鐵"):
        if n.startswith(p) and len(n) > len(p) + 1:
            n = n[len(p):]
    if n.endswith("站") and len(n) > 2 and not n.endswith("車站"):
        n = n[:-1]
    return n


def normalize_terminus(line: str, headsign: str, departure_stop: str = None):
    """
    把 headsign（可能是區間車終點或英文）轉成指標牌上寫的方向，例如：
        板南線 + "Dingpu" → 頂埔；機場捷運 + "台北車站" → 台北車站；環狀線 + "大坪林站" → 大坪林
    headsign 就是上車站本身（資料怪怪的）時回傳 None。
    """
    if not headsign:
        return None
    termini = LINE_TERMINI.get(line, {})
    for sign_name, aliases in termini.items():
        if any(a.lower() in headsign.lower() for a in aliases):
            return sign_name
    t = _strip_station(headsign)
    if departure_stop and _strip_station(departure_stop) == t:
        return None
    return t or None


def service_type(line_name: str):
    """機場捷運「普通車／直達車」。"""
    return next((x for x in SERVICE_TYPES if x in (line_name or "")), None)


# ============================================================
# 指標序列規劃
# ============================================================
def _step(target: str, prompt: str) -> dict:
    return {"target": target, "prompt": prompt}


def plan_for_boarding(leg: dict) -> list:
    """
    要在車站「上車」某一段軌道運輸時，依序要找的指標（交給 sign_detector 的 target）。

    例：機場線普通車往台北車站 → ["機場捷運", "往台北車站"]（並提醒搭普通車）
        環狀線往大坪林站       → ["環狀線", "往大坪林"]

    Args:
        leg: 一段搭乘資訊 {mode, line_name, headsign, departure_stop, ...}
    """
    mode = leg.get("mode", "")
    headsign = leg.get("headsign", "")
    dep = leg.get("departure_stop", "")

    if mode in ("火車", "高鐵"):
        name = "台鐵" if mode == "火車" else "高鐵"
        steps = [_step(name, f"先找「{name}」的指標")]
        t = normalize_terminus("", headsign, dep)
        if t:
            steps.append(_step(f"往{t}", f"到{name}大廳後，找「往{t}」方向的月台指標"))
        return steps

    # 捷運 / 輕軌
    line = normalize_line(leg.get("line_name", "")) or leg.get("line_name", "")
    steps = []
    if line:
        steps.append(_step(line, f"先找「{line}」的指標"))
    t = normalize_terminus(line, headsign, dep)
    if t:
        svc = service_type(leg.get("line_name", ""))
        note = f"，要搭{svc}" if svc else ""
        steps.append(_step(f"往{t}", f"接著找「往{t}」方向的月台指標{note}"))
    return steps


def plan_for_exit() -> list:
    """在車站下車、要出站時要找的指標。"""
    return [_step("出口", "找「出口」的指標")]


def needs_boarding_guidance(leg: dict, lat=None, lng=None) -> bool:
    """這一段要進站搭軌道運輸（捷運、火車、高鐵、輕軌）→ 用指標找月台。任何車站都適用。"""
    return bool(leg) and leg.get("mode") in RAIL_MODES


def needs_exit_guidance(leg: dict) -> bool:
    """這一段下了軌道運輸（下車後要出站或轉公車）→ 用指標找出口。"""
    return bool(leg) and leg.get("mode") in RAIL_MODES


# ============================================================
# 導引 session：管理目前找到第幾個指標 + 播報穩定化
# ============================================================
ADVANCE_KEYWORDS = ["到了", "找到了", "下一步", "下一個", "跳過"]
REPEAT_KEYWORDS = ["再說一次", "重複", "在哪"]

CONFIRM_FRAMES = 2        # 連續幾張影格方向一致才播報
REPEAT_AFTER_SEC = 8.0    # 同一句話隔多久可以再提醒一次
MISS_HINT_FRAMES = 6      # 連續幾張沒看到目標，提示使用者轉頭找


class SignGuidanceSession:
    """
    一次台北車站室內導引。

    plan: [{"target": "淡水信義線", "prompt": "先找「淡水信義線」的指標"}, ...]
    purpose: "board"（找月台上車）/ "exit"（出站）— 給呼叫端決定結束後要做什麼
    """

    def __init__(self, plan: list, purpose: str, reason: str = ""):
        self.plan = plan
        self.purpose = purpose
        self.reason = reason
        self.index = 0
        self._last_direction = None
        self._same_count = 0
        self._miss_count = 0
        self._last_spoken = None
        self._last_spoken_at = 0.0

    # ---------- 狀態 ----------
    @property
    def done(self) -> bool:
        return self.index >= len(self.plan)

    @property
    def current_target(self):
        return None if self.done else self.plan[self.index]["target"]

    def _reset_stabilizer(self):
        self._last_direction = None
        self._same_count = 0
        self._miss_count = 0
        self._last_spoken = None
        self._last_spoken_at = 0.0

    # ---------- 語音文字 ----------
    def intro_text(self) -> str:
        head = self.reason or "已進入台北車站"
        targets = "、".join(f"「{s['target']}」" for s in self.plan)
        return (f"{head}，接下來用指標牌帶你走，會依序找{targets}。"
                f"請把鏡頭對準前方的指標牌。{self.step_text()}")

    def step_text(self) -> str:
        if self.done:
            return ""
        prompt = self.plan[self.index]["prompt"]
        tail = "，走到之後請說「到了」" if self.index < len(self.plan) - 1 else "，到了之後請說「到了」"
        return f"第{self.index + 1}步，{prompt}{tail}"

    # ---------- 使用者語音 ----------
    def advance(self) -> str:
        """使用者說「到了」：前進到下一個指標。回傳要講的話（走完時回傳空字串）。"""
        self.index += 1
        self._reset_stabilizer()
        return "" if self.done else f"好的。{self.step_text()}"

    def repeat_text(self) -> str:
        if self._last_spoken:
            return f"{self._last_spoken}。{self.step_text()}"
        return self.step_text()

    # ---------- 影像辨識結果 ----------
    def process_result(self, result: dict, now: float = None) -> dict:
        """
        吃一次 sign_detector 回傳，決定要不要播報。

        Returns:
            {"speak": bool, "text": str, "found": bool, "direction": str|None, "target": str}
        """
        now = time.time() if now is None else now
        out = {"speak": False, "text": "", "found": bool(result.get("found")),
               "direction": result.get("direction"), "target": self.current_target}

        if result.get("error"):
            out["error"] = result["error"]
            return out

        if not result.get("found") or not result.get("direction"):
            self._miss_count += 1
            self._same_count = 0
            self._last_direction = None
            if self._miss_count == MISS_HINT_FRAMES or \
                    (self._miss_count > MISS_HINT_FRAMES and self._miss_count % (MISS_HINT_FRAMES * 2) == 0):
                out.update(speak=True, text=f"還沒看到「{self.current_target}」的指標，請慢慢左右轉頭找找看")
            return out

        self._miss_count = 0
        direction = result["direction"]
        if direction == self._last_direction:
            self._same_count += 1
        else:
            self._last_direction = direction
            self._same_count = 1

        if self._same_count < CONFIRM_FRAMES:
            return out

        sentence = result.get("sentence") or f"{self.current_target}在{direction}的方向"
        if sentence != self._last_spoken or now - self._last_spoken_at >= REPEAT_AFTER_SEC:
            self._last_spoken = sentence
            self._last_spoken_at = now
            out.update(speak=True, text=sentence)
        return out


# ============================================================
# CLI 版導引（main.py / transit_navigation.py 用）
# ============================================================
def run_cli_guidance(session: "SignGuidanceSession", speak, listen, cancel_keywords) -> bool:
    """
    文字模擬版：輸入圖片路徑送去辨識；直接按 Enter 用模擬結果；
    說「到了」前進；說「取消」結束。

    Returns:
        True 走完全部指標，False 使用者取消
    """
    import base64
    import os

    from modules import sign_client

    speak(session.intro_text())
    service_ok = sign_client.is_available()
    if not service_ok:
        print(f"   [提醒] 指標辨識服務未連線（{sign_client.SIGN_DETECTOR_URL}），按 Enter 會用模擬結果")
    print("   [操作] 輸入圖片路徑＝拍一張；Enter＝模擬一張影格；「到了」＝下一步；「取消」＝結束")

    while not session.done:
        text = listen().strip()
        if any(k in text for k in cancel_keywords):
            return False
        if any(k in text for k in ADVANCE_KEYWORDS):
            msg = session.advance()
            if msg:
                speak(msg)
            continue
        if any(k in text for k in REPEAT_KEYWORDS):
            speak(session.repeat_text())
            continue

        path = text.strip('"').strip("'")
        if path and os.path.isfile(path):
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            result = sign_client.navigate(b64, session.current_target)
            # 單張照片就直接播報，不用等連續穩定
            if result.get("found"):
                speak(result.get("sentence") or f"{session.current_target}在{result.get('direction')}的方向")
            else:
                speak(result.get("error") or result.get("message") or f"畫面中找不到「{session.current_target}」")
            continue

        result = sign_client.mock_navigate(session.current_target) if (not path) else \
            {"found": False, "error": f"找不到圖片檔：{path}"}
        if result.get("error"):
            speak(result["error"])
            continue
        out = session.process_result(result)
        if out["speak"]:
            speak(out["text"])
        else:
            print(f"   [影格] {'看到' if out['found'] else '沒看到'}「{out['target']}」"
                  f"{('，' + out['direction']) if out['direction'] else ''}（尚未穩定，不播報）")
    return True


if __name__ == "__main__":
    leg = {"mode": "捷運", "line_name": "Tamsui-Xinyi Line", "headsign": "Beitou", "departure_stop": "台北車站"}
    print("需要進站導引：", needs_boarding_guidance(leg))
    print("指標序列：", plan_for_boarding(leg))
    print("出站：", plan_for_exit())
    s = SignGuidanceSession(plan_for_boarding(leg), "board")
    print(s.intro_text())
