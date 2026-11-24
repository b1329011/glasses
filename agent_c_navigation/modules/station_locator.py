"""
台北車站室內定位：讀招牌 → 報「你大概在哪、附近有什麼、靠近哪個出口」
====================================
不做一步一步的路線導引（全程掃地圖太累），只回答使用者最需要的三件事：
    1. 我現在大概在哪一區（台北地下街 Y 區、站前地下街 Z 區…）
    2. 附近有哪些店家／地標（讓使用者自己對照、或問路人時講得出來）
    3. 靠近哪個出口、那個出口通往哪裡

定位方式：只用鏡頭。眼鏡每張影格做 OCR，比對 data/taipei_main_station.json：
    - 店家招牌（「新光三越」「木棉花」…）→ 強線索
    - 出口編號（指標牌上的「Y13」「Z4」「M3」）→ 弱線索（指標可能指向遠處的出口）
連續幾張影格都指向同一個地方才播報，播一次就停，不要一直講。

另外提供「找人幫忙」：組一段可以念給路人聽／顯示在螢幕上的求助句。
"""

import json
import os
import re
import unicodedata

DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "taipei_main_station.json")

CONFIRM_FRAMES = 2      # 同一個位置線索要在幾張影格出現才算數
WINDOW_FRAMES = 5       # 在最近幾張影格內累計
GIVE_UP_FRAMES = 12     # 掃了這麼多張都沒線索，就提示使用者
MIN_OCR_CONF = 0.5

EXIT_RE = re.compile(r"(?<![A-Z0-9])([YZKRM])\s*-?\s*(\d{1,2})(?!\d)")

LOCATE_KEYWORDS = ["我在哪", "這裡是哪", "我現在在哪", "附近有什麼", "附近有甚麼", "最近的出口", "哪個出口"]
HELP_KEYWORDS = ["找人幫忙", "幫我找人", "求助", "需要幫忙"]


# ============================================================
# 資料
# ============================================================
_db_cache = {"mtime": None, "db": None}


def load_db(path: str = DATA_PATH) -> dict:
    """讀店家/出口資料；檔案改過會自動重讀（方便實地蒐集時邊改邊測）。"""
    mtime = os.path.getmtime(path)
    if _db_cache["db"] is None or _db_cache["mtime"] != mtime:
        with open(path, "r", encoding="utf-8") as f:
            db = json.load(f)
        db["_exit_map"] = {e["id"].upper(): e for e in db.get("exits", [])}
        _db_cache.update(mtime=mtime, db=db)
    return _db_cache["db"]


def _norm(s: str) -> str:
    """全形轉半形、去空白、轉大寫、臺→台，讓 OCR 結果和資料好比對。"""
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("臺", "台")
    return re.sub(r"\s+", "", s).upper()


# ============================================================
# 單張影格：從 OCR 文字找位置線索
# ============================================================
def find_clues(ocr_items: list, db: dict = None) -> list:
    """
    Args:
        ocr_items: [{"text": str, "conf": float}, ...] 或 [(text, conf), ...]
    Returns:
        [{"kind": "shop"|"exit", "key": 店名或出口編號, "zone": "Y", "weight": int, "text": OCR原文}, ...]
    """
    db = db or load_db()
    clues, seen = [], set()
    for item in ocr_items:
        text, conf = (item.get("text"), item.get("conf", 1.0)) if isinstance(item, dict) else item
        if not text or (conf is not None and conf < MIN_OCR_CONF):
            continue
        t = _norm(str(text))

        # 店家 / 地標招牌
        for shop in db.get("shops", []):
            names = [shop["name"]] + list(shop.get("aliases", []))
            for n in names:
                nn = _norm(n)
                if len(nn) >= 2 and (nn in t or (len(t) >= 3 and t in nn and len(t) >= len(nn) * 0.6)):
                    key = ("shop", shop["name"])
                    if key not in seen:
                        seen.add(key)
                        clues.append({"kind": "shop", "key": shop["name"], "zone": shop.get("zone"),
                                      "weight": 3, "text": str(text)})
                    break

        # 出口編號（指標牌上的 Y13、Z4…）
        for m in EXIT_RE.finditer(t):
            eid = f"{m.group(1)}{int(m.group(2))}"
            key = ("exit", eid)
            if key not in seen:
                seen.add(key)
                clues.append({"kind": "exit", "key": eid, "zone": m.group(1), "weight": 1, "text": str(text)})
    return clues


# ============================================================
# 組語音描述
# ============================================================
def _exit_phrase(db: dict, eid: str) -> str:
    e = db["_exit_map"].get(eid.upper())
    if e and e.get("leads_to"):
        return f"{eid}出口（{e['leads_to']}）"
    return f"{eid}出口"


def describe(clue: dict, db: dict = None) -> dict:
    """把確認過的線索轉成要播報的話，以及給「找人幫忙」用的位置短句。"""
    db = db or load_db()
    zone = db.get("zones", {}).get(clue.get("zone") or "", {})
    zone_name = zone.get("name", "台北車站一帶")

    if clue["kind"] == "shop":
        shop = next(s for s in db["shops"] if s["name"] == clue["key"])
        parts = [f"你在{zone_name}，{shop['name']}附近"]
        exits = shop.get("near_exits") or []
        if exits:
            parts.append("靠近" + "、".join(_exit_phrase(db, e) for e in exits))
        # 同一區、共用出口的其他店家（讓使用者有東西可以對照）
        neighbors = [s["name"] for s in db["shops"]
                     if s["name"] != shop["name"] and s.get("zone") == shop.get("zone")
                     and set(s.get("near_exits") or []) & set(exits)]
        if neighbors:
            parts.append("附近還有" + "、".join(neighbors[:3]))
        if shop.get("note"):
            parts.append(shop["note"])
        short = f"{zone_name}的{shop['name']}附近"
    else:
        eid = clue["key"]
        parts = [f"附近有{eid}出口的指標，你大概在{zone_name}"]
        e = db["_exit_map"].get(eid.upper())
        if e and e.get("leads_to"):
            parts.append(f"{eid}出口通往{e['leads_to']}")
        parts.append("指標可能是指向遠一點的出口，可以再對準店家招牌確認")
        short = f"{zone_name}，{eid}出口指標附近"

    if zone.get("help_desk") and "待" not in zone["help_desk"]:
        parts.append(f"需要協助可以找{zone['help_desk']}")
    return {"text": "，".join(parts) + "。", "short": short, "zone": clue.get("zone"), "key": clue["key"]}


def help_message(location_short: str = None, destination: str = None) -> dict:
    """「找人幫忙」：念出來＋大字顯示在螢幕上，給路人或站務人員看。"""
    where = f"我現在在{location_short}，" if location_short else ""
    want = f"想去{destination}" if destination else "想找最近的出口"
    card = f"您好，我是視障者。{where}{want}，可以請您帶我過去，或告訴我怎麼走嗎？謝謝！"
    speak = ("好的，我把求助訊息放大顯示在螢幕上了，你可以把手機拿給旁邊的人或站務人員看。"
             "台北車站的捷運站、台鐵高鐵大廳都有服務台。")
    return {"card": card, "speak": speak}


# ============================================================
# 連續影格：累計線索，穩定後播一次就停
# ============================================================
class LocateSession:
    def __init__(self):
        self.frames = []       # 最近幾張影格的線索
        self.count = 0
        self.result = None     # 確認後的 describe() 結果
        self.hinted = False

    @property
    def done(self) -> bool:
        return self.result is not None

    def intro_text(self) -> str:
        return "好的，請慢慢左右轉頭，讓鏡頭看看附近的店家招牌，我認出來就告訴你。"

    def process(self, ocr_items: list, db: dict = None) -> dict:
        """
        吃一張影格的 OCR 結果。
        Returns: {"speak": bool, "text": str, "done": bool, "clues": [...], "result": {...}|None}
        """
        db = db or load_db()
        self.count += 1
        clues = find_clues(ocr_items, db)
        self.frames.append(clues)
        self.frames = self.frames[-WINDOW_FRAMES:]
        out = {"speak": False, "text": "", "done": False, "clues": clues, "result": None}

        # 最近幾張影格裡，每個線索出現幾次、加權分數多少
        tally = {}
        for frame in self.frames:
            for c in frame:
                k = (c["kind"], c["key"])
                t = tally.setdefault(k, {"clue": c, "hits": 0, "score": 0})
                t["hits"] += 1
                t["score"] += c["weight"]
        confirmed = [t for t in tally.values() if t["hits"] >= CONFIRM_FRAMES]
        if confirmed:
            best = max(confirmed, key=lambda t: (t["clue"]["kind"] == "shop", t["score"]))
            self.result = describe(best["clue"], db)
            out.update(speak=True, text=self.result["text"], done=True, result=self.result)
            return out

        if self.count >= GIVE_UP_FRAMES and not self.hinted:
            self.hinted = True
            out.update(speak=True, text="還認不出附近的店家，試著靠近一點對準招牌，或說「找人幫忙」。")
        return out


if __name__ == "__main__":
    db = load_db()
    print(f"資料：{len(db['zones'])} 區、{len(db['exits'])} 個出口、{len(db['shops'])} 個店家/地標")
    s = LocateSession()
    print(s.intro_text())
    for frame in [[("新光三越 台北站前店", 0.9)], [("往 Z4 出口", 0.8), ("新光三越", 0.92)]]:
        print(s.process(frame)["text"] or "（還沒確定）")
    print(help_message(s.result["short"], "台鐵大廳")["card"])
