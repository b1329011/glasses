"""
把 OpenStreetMap 的台北車站地下資料匯入 data/taipei_main_station.json
====================================
資料來源：OpenStreetMap contributors（ODbL 授權，使用時需標示出處）

用法：
    # 用已經存好的擷取檔（data/osm_raw_taipei_main.json）重建
    python tools/osm_import.py

    # 直接從 OpenStreetMap 重新抓最新資料（需要能連 api.openstreetmap.org）
    python tools/osm_import.py --fetch

會做的事：
    - 出口（Y/Z/K/M/R + 台北車站東西南北門）：加上座標，重複的點取平均
    - 店家／餐廳／詢問處：加上座標、所屬區域、最近的兩個出口
    - Y 區的服飾／百貨／電子／美食區：當作「區塊」，播報時說得出在哪一區
    - 手動整理的資料（有 source 的出口說明、地標）會保留，不會被覆蓋
"""

import argparse
import datetime
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(HERE), "data")
RAW_PATH = os.path.join(DATA_DIR, "osm_raw_taipei_main.json")
DB_PATH = os.path.join(DATA_DIR, "taipei_main_station.json")
BBOX = (121.5100, 25.0455, 121.5210, 25.0515)   # minlon, minlat, maxlon, maxlat

EXIT_REF_RE = re.compile(r"^([YZKMR])(\d+)u?$")
GATE_REF_RE = re.compile(r"^[東西南北]\d門$")

# 這些不是招牌、或使用者看不到，不放進店家
SKIP_KINDS = {"lock", "locker", "parking", "parking_entrance", "vending_machine"}
SKIP_NAME_WORDS = ["付費區", "驗票閘門", "變電所", "置物櫃", "停車場"]
AREA_PREFIX = "Y區地下街-"
MALL_SKIP = {"站前地下街", "台北地下街", "東森廣場K區地下街", "中山地下街", "Miki Plaza"}
HELP_KINDS = {"information", "police"}


def dist_m(a_lat, a_lon, b_lat, b_lon) -> float:
    k = 111320.0
    return math.hypot((a_lat - b_lat) * k, (a_lon - b_lon) * k * math.cos(math.radians(a_lat)))


# ------------------------------------------------------------
# 取得原始資料
# ------------------------------------------------------------
def fetch_raw() -> dict:
    import requests
    url = "https://api.openstreetmap.org/api/0.6/map.json?bbox=" + ",".join(map(str, BBOX))
    print(f"下載 {url} ...")
    resp = requests.get(url, timeout=120, headers={"User-Agent": "glasses-navigation-student-project"})
    resp.raise_for_status()
    elements = resp.json()["elements"]
    nodes = {e["id"]: e for e in elements if e["type"] == "node"}

    def center(e):
        if e["type"] == "node":
            return e["lat"], e["lon"]
        pts = [nodes[i] for i in e.get("nodes", []) if i in nodes]
        if not pts:
            return None, None
        return sum(p["lat"] for p in pts) / len(pts), sum(p["lon"] for p in pts) / len(pts)

    exits, pois = [], []
    for e in elements:
        t = e.get("tags") or {}
        ref = t.get("ref", "")
        if ref and (t.get("entrance") or t.get("railway") == "subway_entrance") and \
                (EXIT_REF_RE.match(ref) or GATE_REF_RE.match(ref)):
            exits.append([ref, e["lat"], e["lon"], t.get("description") or t.get("name") or "",
                          t.get("wheelchair", ""), t.get("level", "")])
        elif t.get("name") and (t.get("shop") or t.get("amenity") or t.get("tourism") or "-" in t.get("level", "")) \
                and not t.get("highway") and not t.get("railway") and not t.get("entrance"):
            if "-" not in t.get("level", ""):
                continue   # 只要地下的
            lat, lon = center(e)
            pois.append([t["name"], t.get("name:en", ""), t.get("shop") or t.get("amenity") or t.get("tourism") or "",
                         t.get("level", ""), lat, lon])
    raw = {"_source": f"OpenStreetMap contributors (ODbL), api.openstreetmap.org, {datetime.date.today()} 擷取；bbox "
                      + ",".join(map(str, BBOX)),
           "_format": {"exits": "[ref, lat, lon, description, wheelchair, level]",
                       "pois": "[name, name_en, kind, level, lat, lon]"},
           "exits": exits, "pois": pois}
    with open(RAW_PATH, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False)
    print(f"已存 {RAW_PATH}：{len(exits)} 個出口點、{len(pois)} 個地下地點")
    return raw


# ------------------------------------------------------------
# 轉換 + 合併
# ------------------------------------------------------------
def build(raw: dict, db: dict) -> dict:
    src = "OpenStreetMap"

    # ---- 出口：同編號多個點取平均 ----
    pts = {}
    for ref, lat, lon, desc, wheelchair, _level in raw["exits"]:
        m = EXIT_REF_RE.match(ref)
        eid = f"{m.group(1)}{int(m.group(2))}" if m else ref
        p = pts.setdefault(eid, {"lat": [], "lon": [], "desc": "", "wheelchair": ""})
        p["lat"].append(lat)
        p["lon"].append(lon)
        if desc and not p["desc"]:
            p["desc"] = re.sub(r"^[A-Z]\d+出口\s*", "", desc).strip("()（） ")
        if wheelchair in ("yes", "limited"):
            p["wheelchair"] = wheelchair

    exits = {e["id"]: e for e in db.get("exits", [])}
    for eid, p in pts.items():
        e = exits.get(eid) or {"id": eid, "zone": eid[0] if EXIT_REF_RE.match(eid) else "T",
                               "side": "", "leads_to": "", "verified": True, "source": src}
        if GATE_REF_RE.match(eid):
            e["zone"] = "T"
            e["leads_to"] = e.get("leads_to") or f"台北車站{eid}"
        e["lat"] = round(sum(p["lat"]) / len(p["lat"]), 7)
        e["lon"] = round(sum(p["lon"]) / len(p["lon"]), 7)
        if p["desc"] and p["desc"] not in ("M1", "M4") and not e.get("leads_to"):
            e["leads_to"] = p["desc"]
        if p["wheelchair"]:
            e["wheelchair"] = p["wheelchair"]
        if src not in e.get("source", ""):
            e["source"] = (e.get("source", "") + "；" + src).strip("；")
        e["verified"] = True
        exits[eid] = e

    def sort_key(eid):
        m = re.match(r"^([A-Z])(\d+)$", eid)
        return (0, m.group(1), int(m.group(2))) if m else (1, eid, 0)

    db["exits"] = [exits[k] for k in sorted(exits, key=sort_key)]
    letter_exits = [e for e in db["exits"] if "lat" in e and e["zone"] != "T"]
    all_exits = [e for e in db["exits"] if "lat" in e]

    def nearest(lat, lon, pool, n):
        return sorted(pool, key=lambda e: dist_m(lat, lon, e["lat"], e["lon"]))[:n]

    # ---- 店家 / 區塊 / 求助點 ----
    manual = [s for s in db.get("shops", []) if s.get("source", "").find(src) < 0]
    shops, areas, help_points = [], [], []
    for name, en, kind, level, lat, lon in raw["pois"]:
        if lat is None or kind in SKIP_KINDS or any(w in name for w in SKIP_NAME_WORDS) or name in MALL_SKIP:
            continue
        if name.startswith(AREA_PREFIX):
            areas.append({"name": name.replace(AREA_PREFIX, "台北地下街") , "zone": "Y",
                          "lat": round(lat, 7), "lon": round(lon, 7), "source": src})
            continue
        zone = nearest(lat, lon, all_exits, 1)[0]["zone"]
        item = {"name": name, "aliases": [en] if en and en != name else [], "zone": zone, "kind": kind,
                "level": level, "lat": round(lat, 7), "lon": round(lon, 7),
                "near_exits": [e["id"] for e in nearest(lat, lon, letter_exits, 2)],
                "verified": True, "source": src}
        if kind in HELP_KINDS:
            help_points.append(item)
        shops.append(item)

    # 手動整理、OSM 沒有的地標保留（同名的就以 OSM 座標為準，但保留手動的別名與說明）
    osm_names = {s["name"] for s in shops}
    for m in manual:
        if m["name"] in osm_names:
            for s in shops:
                if s["name"] == m["name"]:
                    s["aliases"] = sorted(set(s["aliases"]) | set(m.get("aliases", [])))
                    if m.get("note"):
                        s["note"] = m["note"]
        else:
            shops.append(m)

    db["shops"] = shops
    db["areas"] = areas
    db["help_points"] = help_points
    db.setdefault("zones", {})["T"] = {
        "name": "台北車站（台鐵、高鐵大廳一帶）",
        "where": "台北車站本體，東西南北各有出入門",
        "layout": "B1 台鐵、高鐵進出站口；B3 捷運、台鐵、高鐵售票與驗票",
        "help_desk": "詢問處",
        "source": src,
    }
    db["version"] = str(datetime.date.today())
    db["osm"] = {"attribution": "© OpenStreetMap contributors（ODbL）", "raw": os.path.basename(RAW_PATH),
                 "extracted": raw.get("_source", "")}
    return db


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fetch", action="store_true", help="從 OpenStreetMap 重新下載")
    args = ap.parse_args()

    raw = fetch_raw() if args.fetch else json.load(open(RAW_PATH, encoding="utf-8"))
    db = json.load(open(DB_PATH, encoding="utf-8"))
    db = build(raw, db)
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=1)
    n_xy = sum(1 for e in db["exits"] if "lat" in e)
    print(f"已更新 {DB_PATH}")
    print(f"  出口 {len(db['exits'])} 個（{n_xy} 個有座標）、店家/地點 {len(db['shops'])} 個、"
          f"區塊 {len(db['areas'])} 個、求助點 {len(db['help_points'])} 個")


if __name__ == "__main__":
    sys.exit(main())
