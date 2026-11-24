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

有座標的資料（來自 OpenStreetMap，tools/osm_import.py 匯入）可以多做：
    - 最近的出口用實際距離排序，說「約幾公尺」
    - 附近 40 公尺內的其他店家、最近的詢問處
    - 手機傳來羅盤方向（heading）時，說出口在「左前方／右手邊…」
      （地下的鋼筋結構會干擾羅盤，所以只在前端回報可信的方向時才用）
    - 同名連鎖店（7-Eleven、國泰世華 ATM…）有好幾家時，用畫面裡其他招牌判斷是哪一家，
      判斷不出來就請使用者再對準別的招牌，不亂猜
"""

import json
import math
import os
import re
import unicodedata

DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "taipei_main_station.json")

CONFIRM_FRAMES = 2      # 同一個位置線索要在幾張影格出現才算數
NEAR_SHOP_M = 40        # 「附近還有」的範圍
EXIT_REPORT_M = 200     # 超過這個距離的出口就不報了
AMBIG_RADIUS_M = 60     # 同名店家：離其他線索這麼近才算是那一家
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
        # 每家店給一個唯一 id（同名連鎖店會有好幾筆）
        for i, shop in enumerate(db.get("shops", [])):
            shop["_id"] = i
        _db_cache.update(mtime=mtime, db=db)
    return _db_cache["db"]


def _norm(s: str) -> str:
    """全形轉半形、去空白、轉大寫、臺→台，讓 OCR 結果和資料好比對。"""
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("臺", "台")
    return re.sub(r"\s+", "", s).upper()


def dist_m(a_lat, a_lon, b_lat, b_lon) -> float:
    k = 111320.0
    return math.hypot((a_lat - b_lat) * k, (a_lon - b_lon) * k * math.cos(math.radians(a_lat)))


def bearing_deg(a_lat, a_lon, b_lat, b_lon) -> float:
    """從 a 看 b 的方位角（0=北，順時針）。"""
    dy = b_lat - a_lat
    dx = (b_lon - a_lon) * math.cos(math.radians(a_lat))
    return (math.degrees(math.atan2(dx, dy)) + 360) % 360


def relative_dir(target_bearing: float, heading: float) -> str:
    """目標方位角 vs 使用者面向 → 「前方／右前方／右手邊…」"""
    d = (target_bearing - heading + 360) % 360
    names = ["前方", "右前方", "右手邊", "右後方", "後方", "左後方", "左手邊", "左前方"]
    return names[int(((d + 22.5) % 360) // 45)]


def _has_xy(x) -> bool:
    return x is not None and x.get("lat") is not None and x.get("lon") is not None


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

        # 店家 / 地標招牌（同名連鎖店會對到好幾筆，一起當候選）
        for shop in db.get("shops", []):
            names = [shop["name"]] + list(shop.get("aliases", []))
            for n in names:
                nn = _norm(n)
                if len(nn) >= 2 and (nn in t or (len(t) >= 3 and t in nn and len(t) >= len(nn) * 0.6)):
                    key = ("shop", shop["name"])
                    if key in seen:
                        cands = next(c for c in clues if c["kind"] == "shop" and c["key"] == shop["name"])["candidates"]
                        if all(x["_id"] != shop["_id"] for x in cands):   # 中英文都對到同一家，不重複算
                            cands.append(shop)
                    else:
                        seen.add(key)
                        clues.append({"kind": "shop", "key": shop["name"], "zone": shop.get("zone"),
                                      "weight": 3, "text": str(text), "candidates": [shop]})
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
        extra = f"，{e['note']}" if e.get("note") else ""
        return f"{eid}出口（{e['leads_to']}{extra}）"
    return f"{eid}出口"


def _round_m(d: float) -> int:
    return max(10, int(round(d / 10.0)) * 10)


def describe(anchor: dict, db: dict = None, heading: float = None, kind: str = "shop") -> dict:
    """
    把確認過的位置轉成要播報的話，以及給「找人幫忙」用的位置短句。

    anchor: 店家（db["shops"] 的一筆）或出口（db["exits"] 的一筆）
    heading: 使用者面向的方位角（0=北），有的話出口會說「在你左前方」
    """
    db = db or load_db()
    zone = db.get("zones", {}).get(anchor.get("zone") or "", {})
    zone_name = zone.get("name", "台北車站一帶")

    if kind == "exit":
        eid = anchor["id"]
        parts = [f"附近有{eid}出口的指標，你大概在{zone_name}"]
        if anchor.get("leads_to"):
            parts.append(f"{eid}出口通往{anchor['leads_to']}")
        parts.append("指標可能是指向遠一點的出口，可以再對準店家招牌確認")
        return {"text": "，".join(parts) + "。", "short": f"{zone_name}，{eid}出口指標附近",
                "zone": anchor.get("zone"), "key": eid}

    name = anchor["name"]
    parts = [f"你在{zone_name}，{name}附近"]

    if _has_xy(anchor):
        lat, lon = anchor["lat"], anchor["lon"]
        # 所在區塊（例如台北地下街美食區）
        areas = [a for a in db.get("areas", []) if _has_xy(a) and dist_m(lat, lon, a["lat"], a["lon"]) < 120]
        if areas:
            area = min(areas, key=lambda a: dist_m(lat, lon, a["lat"], a["lon"]))
            parts[0] = f"你在{area['name']}，{name}附近"

        # 最近的出口（英文字母編號的，不含車站東西南北門）＋相對方向
        exits = sorted((e for e in db.get("exits", []) if _has_xy(e) and e.get("zone") != "T"),
                       key=lambda e: dist_m(lat, lon, e["lat"], e["lon"]))[:2]
        exit_bits = []
        for e in exits:
            d = dist_m(lat, lon, e["lat"], e["lon"])
            if d > EXIT_REPORT_M:
                continue
            bit = f"{_exit_phrase(db, e['id'])}約{_round_m(d)}公尺"
            if heading is not None:
                bit += f"，在你{relative_dir(bearing_deg(lat, lon, e['lat'], e['lon']), heading)}"
            exit_bits.append(bit)
        if exit_bits:
            parts.append("最近的出口：" + "；".join(exit_bits))

        # 附近其他店家（不同名、40 公尺內、最近的 3 家）
        near, seen_names = [], {name}
        for s in sorted((x for x in db.get("shops", []) if _has_xy(x)),
                        key=lambda x: dist_m(lat, lon, x["lat"], x["lon"])):
            if s["name"] in seen_names or s.get("kind") in ("information", "police"):
                continue
            if dist_m(lat, lon, s["lat"], s["lon"]) > NEAR_SHOP_M:
                break
            seen_names.add(s["name"])
            near.append(s["name"])
            if len(near) == 3:
                break
        if near:
            parts.append("附近還有" + "、".join(near))

        # 最近的詢問處
        helps = [h for h in db.get("help_points", []) if _has_xy(h)]
        if helps:
            h = min(helps, key=lambda x: dist_m(lat, lon, x["lat"], x["lon"]))
            d = dist_m(lat, lon, h["lat"], h["lon"])
            if d < 250:
                bit = f"最近的{h['name']}約{_round_m(d)}公尺"
                if heading is not None:
                    bit += f"，在你{relative_dir(bearing_deg(lat, lon, h['lat'], h['lon']), heading)}"
                parts.append(bit)
    else:
        # 沒有座標的手動資料：用資料裡寫的 near_exits
        exits = anchor.get("near_exits") or []
        if exits:
            parts.append("靠近" + "、".join(_exit_phrase(db, e) for e in exits))
        neighbors = [x["name"] for x in db["shops"]
                     if x["name"] != name and x.get("zone") == anchor.get("zone")
                     and set(x.get("near_exits") or []) & set(exits)]
        if neighbors:
            parts.append("附近還有" + "、".join(list(dict.fromkeys(neighbors))[:3]))
    if anchor.get("note"):
        parts.append(anchor["note"])
    return {"text": "，".join(parts) + "。", "short": f"{zone_name}的{name}附近",
            "zone": anchor.get("zone"), "key": name,
            "lat": anchor.get("lat"), "lon": anchor.get("lon")}


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
        self.ambig_told = set()

    @property
    def done(self) -> bool:
        return self.result is not None

    def intro_text(self) -> str:
        return "好的，請慢慢左右轉頭，讓鏡頭看看附近的店家招牌，我認出來就告訴你。"

    @staticmethod
    def _resolve(cands: list, others: list):
        """
        同名店家有好幾家時，用其他線索（別家招牌、出口編號）的位置挑最近的那一家。
        挑不出來回傳 None（不亂猜）。
        """
        with_xy = [c for c in cands if _has_xy(c)]
        if len(cands) == 1:
            return cands[0]
        if not with_xy or not others:
            return None
        best, best_d = None, None
        for c in with_xy:
            d = min(dist_m(c["lat"], c["lon"], o["lat"], o["lon"]) for o in others)
            if best_d is None or d < best_d:
                best, best_d = c, d
        return best if best_d is not None and best_d <= AMBIG_RADIUS_M else None

    def process(self, ocr_items: list, db: dict = None, heading: float = None) -> dict:
        """
        吃一張影格的 OCR 結果（heading：使用者面向，0=北，可為 None）。
        Returns: {"speak": bool, "text": str, "done": bool, "clues": [...], "result": {...}|None}
        """
        db = db or load_db()
        if self.done:
            return {"speak": False, "text": "", "done": True, "clues": [], "result": self.result}
        self.count += 1
        clues = find_clues(ocr_items, db)
        self.frames.append(clues)
        self.frames = self.frames[-WINDOW_FRAMES:]
        out = {"speak": False, "text": "", "done": False, "clues": clues, "result": None}

        # 最近幾張影格裡，每個線索出現幾次
        tally = {}
        for frame in self.frames:
            for c in frame:
                t = tally.setdefault((c["kind"], c["key"]), {"clue": c, "hits": 0})
                t["hits"] += 1
        confirmed = [t["clue"] for t in tally.values() if t["hits"] >= CONFIRM_FRAMES]

        # 其他線索的位置：唯一的店家、有座標的出口（用來判斷同名連鎖店是哪一家）
        anchors_xy = []
        for t in tally.values():
            c = t["clue"]
            if c["kind"] == "shop" and len(c["candidates"]) == 1 and _has_xy(c["candidates"][0]):
                anchors_xy.append(c["candidates"][0])
            elif c["kind"] == "exit" and _has_xy(db["_exit_map"].get(c["key"].upper())):
                anchors_xy.append(db["_exit_map"][c["key"].upper()])

        # 先看店家（強線索）
        shop_clues = sorted((c for c in confirmed if c["kind"] == "shop"),
                            key=lambda c: len(c["candidates"]))       # 唯一的店優先
        for c in shop_clues:
            others = [x for x in anchors_xy if x.get("name") != c["key"]]
            shop = self._resolve(c["candidates"], others)
            if shop is not None:
                self.result = describe(shop, db, heading=heading)
                out.update(speak=True, text=self.result["text"], done=True, result=self.result)
                return out
            if c["key"] not in self.ambig_told:
                self.ambig_told.add(c["key"])
                out.update(speak=True, text=f"看到{c['key']}，不過台北車站有{len(c['candidates'])}家，"
                                            "請再對準旁邊其他店家的招牌，我才知道是哪一家。")
                return out

        # 只有出口指標（弱線索）
        exit_clues = [c for c in confirmed if c["kind"] == "exit" and c["key"].upper() in db["_exit_map"]]
        if exit_clues and not shop_clues:
            e = db["_exit_map"][exit_clues[0]["key"].upper()]
            self.result = describe(e, db, kind="exit")
            out.update(speak=True, text=self.result["text"], done=True, result=self.result)
            return out

        if self.count >= GIVE_UP_FRAMES and not self.hinted:
            self.hinted = True
            out.update(speak=True, text="還認不出附近的店家，試著靠近一點對準招牌，或說「找人幫忙」。")
        return out


if __name__ == "__main__":
    db = load_db()
    n_xy = sum(1 for e in db["exits"] if _has_xy(e))
    print(f"資料：{len(db['zones'])} 區、{len(db['exits'])} 個出口（{n_xy} 個有座標）、{len(db['shops'])} 個店家/地點")
    for frames, heading in [
        ([[("麵屋武藏", 0.9)], [("麵屋武藏", 0.9)]], 0),
        ([[("7-ELEVEN", 0.9)], [("7-ELEVEN", 0.9)], [("7-ELEVEN", 0.9), ("JINS", 0.9)], [("JINS", 0.8)]], None),
        ([[("名捷印尼餐廳", 0.9)], [("名捷印尼餐廳", 0.9)]], 270),
    ]:
        s = LocateSession()
        for f in frames:
            r = s.process(f, heading=heading)
            if r["text"]:
                print("🔊", r["text"])
        print("---")
