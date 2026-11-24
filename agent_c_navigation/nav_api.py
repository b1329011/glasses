"""
Agent C 語音導航 API（FastAPI router）
====================================================
這支原本是獨立的 Flask 伺服器，現在改成 FastAPI 的 APIRouter，可以：
  1. 掛進 sign_detector 後端（webapp/backend/main.py），兩個後端合成一個 → 一般用法
  2. 用 agent_c_navigation/app.py 單獨啟動（只測導航、不需要 YOLO）

安全性重點：
    Gemini/Google Maps/TDX的金鑰只存在伺服器端（讀取 agent_c_navigation/.env），
    手機端的網頁完全不會接觸到任何金鑰。

設計說明：
    因為網頁是「一問一答」的請求模式（不像CLI可以用while迴圈一直等），
    這裡用一個簡單的伺服器端狀態機（SESSION字典）記錄「現在對話進行到哪一步」，
    每次收到手機傳來的一句話，就依照目前所在的階段(stage)決定怎麼回應、
    要不要換到下一個階段。這個demo只設計給單一使用者使用（適合展示/測試），
    真正多人使用的正式系統，會需要每個使用者有自己獨立的session（例如用cookie
    或token區分），這學期先不用做到這裡。

端點（掛載前綴由呼叫端決定，合併後端是 /api/agent-c）：
    POST /command           語音指令
    POST /segment_complete  前端 GPS 判斷走完一段路
    POST /position          走向上車處時回報 GPS（進入台北車站範圍自動切換指標導引）
    POST /sign_frame        車站指標導引的鏡頭影格（找線名、往哪個方向、出口）
    POST /locate_frame      台北車站室內定位的鏡頭影格（讀招牌 → 報位置、附近店家、出口）
    POST /reset             強制重來

台北車站室內定位（「我在哪裡」「附近有什麼」「找人幫忙」）跟導航流程分開：
任何階段都能說，不會打斷正在進行的導航。
"""

import os
import sys

from dotenv import load_dotenv
from fastapi import APIRouter, Body

# 不管從哪個資料夾啟動，都能 import 到 agent_c_navigation/modules 並讀到這裡的 .env
AGENT_C_DIR = os.path.dirname(os.path.abspath(__file__))
if AGENT_C_DIR not in sys.path:
    sys.path.insert(0, AGENT_C_DIR)
load_dotenv(os.path.join(AGENT_C_DIR, ".env"))


from modules import nlu, places, routing, transit, station_guide, sign_client, station_locator

router = APIRouter(tags=["Agent C 語音導航"])

CONFIRM_START_KEYWORDS = ["開始", "好", "好的", "好啊", "可以", "對", "走吧", "出發", "ok", "OK"]
CANCEL_NAV_KEYWORDS = ["取消", "停止", "不要導航了"]

# 單一使用者的對話狀態（demo規模；正式多人系統要換成per-user session）
SESSION = {
    "stage": "idle",
    "candidates": [],
    "chosen_place": None,
    "route_session": None,
    "current_route": None,
    "legs": [],          # 所有搭乘段（有轉車時 >1 段）
    "leg_index": 0,      # 目前進行到第幾段
    "guide": None,       # 車站指標導引 session（station_guide.SignGuidanceSession）
}

# 台北車站室內定位：跟導航狀態分開，取消導航不會清掉「最後確認的位置」
LOCATE = {
    "session": None,     # station_locator.LocateSession（掃描中）
    "last": None,        # 最後一次確認的位置 describe() 結果
}
STOP_LOCATE_KEYWORDS = ["不用找了", "停止找", "不用了", "關掉鏡頭"]


def _reset_session():
    SESSION["stage"] = "idle"
    SESSION["candidates"] = []
    SESSION["chosen_place"] = None
    SESSION["route_session"] = None
    SESSION["current_route"] = None
    SESSION["legs"] = []
    SESSION["leg_index"] = 0
    SESSION["guide"] = None


def _start_route_session(chosen_place: dict, lat: float, lng: float) -> str:
    """建立路線候選池、套即時資訊，回傳語音摘要，並把狀態切到「等待確認」"""
    origin = {"lat": lat, "lng": lng}
    session = routing.RouteSession(origin=origin, destination=chosen_place)
    # 一開始就指定交通方式（「搭捷運去板橋」）：直接挑那種路線
    pref, prefix = SESSION.pop("mode_pref", None), ""
    if pref and session.choose_mode(pref) is None:
        prefix = f"沒有找到搭{pref}的路線。"
    current_route = transit.apply_realtime_to_route(session.get_current_route())

    SESSION["chosen_place"] = chosen_place
    SESSION["route_session"] = session
    SESSION["current_route"] = current_route
    SESSION["stage"] = "awaiting_confirm"

    return prefix + session.voice_summary(current_route)


def _strip_mode_words(dest: str) -> str:
    """「搭捷運去板橋車站」被解析出的目的地可能夾帶交通方式，查地點前拿掉。"""
    d = dest or ""
    for prefix in ("搭捷運去", "坐捷運去", "搭公車去", "坐公車去", "搭火車去", "坐火車去", "走路去"):
        d = d.replace(prefix, "")
    return d.strip() or dest


def _switch_mode(pref: str) -> dict:
    """等待確認時說「搭捷運／搭公車／走路」：切到那種路線，找不到就照實說。"""
    session = SESSION["route_session"]
    route = session.choose_mode(pref)
    if route is None:
        cur = SESSION["current_route"]
        what = "走路" if pref == "步行" else f"搭{pref}"
        return _reply(f"不好意思，沒有找到{what}的路線。目前的方案是{routing.modes_phrase(cur, with_verb=True)}，"
                      f"全程約{cur['total_min']}分鐘。{session.options_hint()}，或說「開始」")
    SESSION["current_route"] = transit.apply_realtime_to_route(route)
    return _reply("好的。" + session.voice_summary(SESSION["current_route"]))


def _reply(text: str, extra: dict = None) -> dict:
    """
    統一組裝回傳給前端的JSON，自動附上：
      - stage：目前對話階段
      - dest_lat/dest_lng：目的地座標（一旦確定要去哪，就一路帶著，讓地圖能畫出目的地標記）
    extra 可以額外帶 walk_steps（含座標的步驟清單，給前端做地圖畫線+GPS自動觸發用）
    """
    payload = {"reply": text, "stage": SESSION["stage"]}
    if LOCATE["session"] is not None and not LOCATE["session"].done:
        payload["locate"] = True   # 前端看到就開鏡頭送 /locate_frame
    guide = SESSION.get("guide")
    if SESSION["stage"] == "station_guidance" and guide and not guide.done:
        # 前端看到 guide_target 就開鏡頭，持續把影格送到 /api/sign_frame
        payload["guide_target"] = guide.current_target
    chosen = SESSION.get("chosen_place")
    if chosen:
        payload["dest_lat"] = chosen.get("lat")
        payload["dest_lng"] = chosen.get("lng")
    if extra:
        payload.update(extra)
    return payload


def _current_leg() -> dict:
    legs = SESSION.get("legs") or []
    i = SESSION.get("leg_index", 0)
    if 0 <= i < len(legs):
        return legs[i]
    return SESSION["current_route"] or {}


def _transit_boarding_prompt(prefix: str = "") -> str:
    leg = _current_leg()
    SESSION["stage"] = "navigating_transit_wait_board"
    line_name = leg.get("line_name", "")
    headsign = leg.get("headsign", "")
    mode = leg.get("mode", "大眾運輸")
    wait_min = leg.get("wait_min")

    text = f"{prefix}到了{leg.get('departure_stop', '站牌')}，請確認搭乘{mode}{line_name}"
    if headsign:
        text += f"，往{headsign}方向"
    if wait_min is not None:
        text += f"，預計{wait_min}分鐘後進站"
    text += "。上車後請說「上車了」"
    return text


# ============================================================
# 車站指標導引（呼叫 sign_detector）：任何捷運／火車／高鐵站都適用
# ============================================================
def _same_station(a: str, b: str) -> bool:
    """轉乘的下車站和上車站是不是同一站（同站就在站內找指標，不同站要先出站走過去）。"""
    if station_guide.is_taipei_main_name(a) and station_guide.is_taipei_main_name(b):
        return True
    return bool(a) and station_guide._strip_station(a) == station_guide._strip_station(b)


def _start_guidance(plan: list, purpose: str, reason: str, prefix: str = "", station: str = "") -> tuple:
    """
    進入 station_guidance 階段。purpose：
      board          → 找月台，走完直接進「等車確認」
      exit_transfer  → 出站去轉乘（例如捷運轉公車），走完接著走到下一段的上車處
      exit           → 最後一段下車出站，走完接著走到目的地
    """
    guide = station_guide.SignGuidanceSession(plan, purpose, reason=reason)
    SESSION["guide"] = guide
    if guide.done:
        return _finish_guidance(prefix)
    SESSION["stage"] = "station_guidance"
    # 「我在哪裡」只有台北車站有店家資料
    tail = "。想知道自己在哪、附近有什麼，可以說「我在哪裡」" if station_guide.is_taipei_main_name(station) else ""
    return prefix + guide.intro_text() + tail, []


def _finish_guidance(prefix: str = "") -> tuple:
    guide = SESSION.get("guide")
    purpose = guide.purpose if guide else "board"
    SESSION["guide"] = None
    if purpose == "board":
        return _transit_boarding_prompt(prefix=prefix + "已到達月台。"), []
    if purpose == "exit_transfer":
        return _proceed_to_current_leg(prefix=prefix + "已經出站。")
    return _walk_to_destination(prefix=prefix + "已經出站。")


def _arrive_at_boarding_stop(lat=None, lng=None, prefix: str = "") -> tuple:
    """走到（或轉乘到）這一段的上車處：軌道運輸就先用指標帶到月台，公車直接等車確認。"""
    leg = _current_leg()
    if station_guide.needs_boarding_guidance(leg, lat, lng):
        plan = station_guide.plan_for_boarding(leg)
        dep = leg.get("departure_stop", "車站")
        return _start_guidance(plan, "board", reason=f"已到達{dep}，要搭{leg.get('mode', '')}{leg.get('line_name', '')}",
                               prefix=prefix, station=dep)
    return _transit_boarding_prompt(prefix=prefix), []


def _proceed_to_current_leg(prefix: str = "", lat=None, lng=None) -> tuple:
    """開始目前這一段：有上車前步行就先走路（GPS 自動推進），沒有就直接到上車處。"""
    leg = _current_leg()
    steps_before = leg.get("steps_before") or []
    if steps_before:
        SESSION["stage"] = "navigating_transit_before"
        return f"{prefix}先走到{leg.get('departure_stop')}。{steps_before[0]['voice_text']}", steps_before
    return _arrive_at_boarding_stop(lat, lng, prefix=prefix)


def _walk_to_destination(prefix: str = "") -> tuple:
    steps_after = SESSION["current_route"].get("steps_after", [])
    if steps_after:
        SESSION["stage"] = "navigating_transit_after"
        lead = "繼續走向目的地" if prefix else "下車後繼續走向目的地"
        return f"{prefix}{lead}。{steps_after[0]['voice_text']}", steps_after
    _reset_session()
    return f"{prefix}已到達目的地附近，導航結束", []


def _after_alighting() -> tuple:
    """使用者說「下車了」：判斷要轉車、出站導引，還是直接走向目的地。"""
    leg = _current_leg()
    legs = SESSION.get("legs") or []
    next_index = SESSION.get("leg_index", 0) + 1

    if next_index < len(legs):
        next_leg = legs[next_index]
        SESSION["leg_index"] = next_index
        prefix = (f"好的，接下來在{leg.get('arrival_stop', '這一站')}轉乘"
                  f"{next_leg.get('mode', '')}{next_leg.get('line_name', '')}。")
        here = leg.get("arrival_stop", "這一站")
        if station_guide.needs_boarding_guidance(next_leg) and _same_station(here, next_leg.get("departure_stop")):
            # 站內轉乘（例如新北產業園區：機場捷運 → 環狀線）：直接用指標帶到下一條線的月台
            plan = station_guide.plan_for_boarding(next_leg)
            return _start_guidance(plan, "board", reason=f"在{here}轉乘", prefix=prefix, station=here)
        if station_guide.needs_exit_guidance(leg):
            # 下捷運/火車，要出站轉公車或走到別的車站
            return _start_guidance(station_guide.plan_for_exit(), "exit_transfer",
                                   reason=f"在{here}下車，要先出站轉乘", prefix=prefix, station=here)
        return _proceed_to_current_leg(prefix=prefix)

    if station_guide.needs_exit_guidance(leg):
        here = leg.get("arrival_stop", "車站")
        return _start_guidance(station_guide.plan_for_exit(), "exit",
                               reason=f"在{here}下車，要出站", station=here)
    return _walk_to_destination()


def _begin_navigation() -> tuple:
    """
    使用者確認開始後，依交通方式分流進入對應的逐步導航階段。

    回傳 (語音文字, walk_steps清單)。walk_steps是給前端的完整步驟清單
    （含每步終點座標），前端拿到後就自己用GPS持續比對距離，
    快到某個點才觸發下一句語音，不再需要使用者講話來推進——
    這是這次的關鍵修正：之前是「使用者講什麼都當作推進信號」，
    現在改成「真的走到定點才推進」，行為更接近Google Maps那種體驗。
    """
    route = SESSION["current_route"]

    if route["mode"] == "步行" and route.get("steps"):
        SESSION["stage"] = "navigating_walk"
        steps = route["steps"]
        if steps:
            reply = f"好的，開始導航，全程共{len(steps)}個路段。{steps[0]['voice_text']}"
        else:
            reply = "好的，開始導航"
        return reply, steps

    if (route.get("modes") or [route["mode"]]) != ["步行"] and route.get("departure_stop"):
        SESSION["legs"] = routing.get_transit_legs(route)
        SESSION["leg_index"] = 0
        reply, steps = _proceed_to_current_leg(prefix="好的，開始導航。")
        return reply, steps

    _reset_session()
    return "這條路線目前還沒有詳細的逐步導航資料，建議改用其他交通方式", []


def _help_destination():
    """找人幫忙時要說「想去哪」：依目前導航進度挑最具體的目標。"""
    guide = SESSION.get("guide")
    if SESSION["stage"] == "station_guidance" and guide and not guide.done:
        leg = _current_leg()
        if guide.purpose == "board" and leg.get("line_name"):
            head = f"往{leg['headsign']}方向" if leg.get("headsign") else ""
            return f"搭{leg.get('mode', '')}{leg['line_name']}{head}的月台"
        return "最近的出口"
    if SESSION["stage"] in ("navigating_transit_before", "navigating_transit_wait_board"):
        leg = _current_leg()
        return f"{leg.get('departure_stop', '')}搭{leg.get('mode', '')}{leg.get('line_name', '')}"
    chosen = SESSION.get("chosen_place")
    return chosen.get("name") if chosen else None


def _handle_locate_commands(text: str):
    """「我在哪裡」「找人幫忙」「不用找了」；不是這類指令就回傳 None 交給導航流程。"""
    if any(k in text for k in station_locator.HELP_KEYWORDS):
        last = LOCATE["last"]
        msg = station_locator.help_message(last["short"] if last else None, _help_destination())
        return _reply(msg["speak"], extra={"help_card": msg["card"]})

    if any(k in text for k in station_locator.LOCATE_KEYWORDS):
        LOCATE["session"] = station_locator.LocateSession()
        return _reply(LOCATE["session"].intro_text())

    if LOCATE["session"] is not None and not LOCATE["session"].done and \
            any(k in text for k in STOP_LOCATE_KEYWORDS):
        LOCATE["session"] = None
        return _reply("好的，不找了")
    return None


@router.post("/command")
def api_command(data: dict = Body(default={})):
    text = (data.get("text") or "").strip()
    lat = data.get("lat")
    lng = data.get("lng")

    if not text:
        return _reply("沒有收到語音內容，請再說一次")

    # === 台北車站室內定位 / 找人幫忙：任何階段都能用，不影響導航進度 ===
    located = _handle_locate_commands(text)
    if located is not None:
        return located

    stage = SESSION["stage"]

    # === 階段：閒置，等待導航請求 ===
    if stage == "idle":
        intent = nlu.parse_intent(text)
        if intent["intent"] != "navigate":
            return _reply("不好意思，我沒聽懂，你可以說「導航去...」")

        location_bias = {"lat": lat, "lng": lng} if lat is not None and lng is not None else None
        SESSION["mode_pref"] = routing.detect_mode_preference(text)
        result = places.disambiguate(_strip_mode_words(intent["destination"]), location_bias=location_bias)

        if result["status"] == "not_found":
            return _reply(result["voice_prompt"])

        if result["status"] == "ambiguous":
            SESSION["candidates"] = result["place"]
            SESSION["stage"] = "awaiting_place_choice"
            return _reply(result["voice_prompt"])

        if lat is None or lng is None:
            return _reply("沒有取得你目前的位置，請確認手機已允許定位權限")
        reply = _start_route_session(result["place"], lat, lng)
        return _reply(reply)

    # === 階段：多筆候選地點，等待使用者選擇 ===
    if stage == "awaiting_place_choice":
        if any(kw in text for kw in nlu.CANCEL_KEYWORDS):
            _reset_session()
            return _reply("好的，已取消")

        chosen = None
        for place in SESSION["candidates"]:
            if text in place["name"] or text in place["address"]:
                chosen = place
                break

        if chosen is None:
            return _reply("不好意思，我沒聽清楚是哪一個，請再說一次")

        if lat is None or lng is None:
            return _reply("沒有取得你目前的位置，請確認手機已允許定位權限")

        reply = _start_route_session(chosen, lat, lng)
        return _reply(f"好的，導航去{chosen['name']}。{reply}")

    # === 階段：路線摘要已講完，等待確認/換一個/改交通方式/取消 ===
    if stage == "awaiting_confirm":
        session = SESSION["route_session"]
        # 先看有沒有指定交通方式（不用打 LLM，也避免「搭捷運好了」裡的「好」被當成確認開始）
        pref = routing.detect_mode_preference(text)
        if pref and not any(k in text for k in nlu.CANCEL_KEYWORDS):
            return _switch_mode(pref)

        intent = nlu.parse_intent(text)

        if intent["intent"] == "next_option":
            SESSION["current_route"] = transit.apply_realtime_to_route(session.next_route())
            return _reply(session.voice_summary(SESSION["current_route"]))

        if intent["intent"] == "cancel":
            _reset_session()
            return _reply("好的，已取消導航")

        if intent["intent"] == "change_mode":
            SESSION["current_route"] = transit.apply_realtime_to_route(session.change_mode())
            return _reply("好的，換一種交通方式。" + session.voice_summary(SESSION["current_route"]))

        if any(kw in text for kw in CONFIRM_START_KEYWORDS):
            reply, walk_steps = _begin_navigation()
            return _reply(reply, extra={"walk_steps": walk_steps})

        return _reply("不好意思，我沒聽懂，你可以說「開始」、「換一個」、「搭捷運」、「搭公車」、「走路」或「取消」")

    # === 以下幾個階段是「移動中」的狀態：這幾個階段的路段推進
    #     現在改由前端用GPS自動觸發（呼叫 /api/segment_complete），
    #     這裡只處理使用者主動講話要「取消」的情況，其他語音內容
    #     不會再誤觸發「跳下一句」，只會被當作單純的狀態確認訊息 ===
    if stage in (
        "navigating_walk",
        "navigating_transit_before",
        "navigating_transit_after",
    ):
        if any(kw in text for kw in CANCEL_NAV_KEYWORDS):
            _reset_session()
            return _reply("好的，已取消導航")
        return _reply("目前導航進行中，如果要取消請說「取消」")

    # === 階段：等待使用者確認上車（這個環節維持語音確認，因為系統
    #     沒辦法單靠手機GPS準確判斷「你是不是真的上車了」）===
    if stage == "navigating_transit_wait_board":
        if any(kw in text for kw in CANCEL_NAV_KEYWORDS):
            _reset_session()
            return _reply("好的，已取消導航")

        if any(kw in text for kw in ["上車了", "上車", "已經上車"]):
            leg = _current_leg()
            stop_count = leg.get("stop_count")
            arrival_stop = leg.get("arrival_stop", "目的地站")
            SESSION["stage"] = "navigating_transit_riding"
            if stop_count:
                reply = f"好的，坐{stop_count}站，到{arrival_stop}要下車。到站後請說「下車了」"
            else:
                reply = f"請留意廣播，到{arrival_stop}要下車。到站後請說「下車了」"
            return _reply(reply)

        return _reply("請在上車後告訴我「上車了」，或說「取消」結束導航")

    # === 階段：搭乘中，等待使用者確認下車 ===
    if stage == "navigating_transit_riding":
        if any(kw in text for kw in CANCEL_NAV_KEYWORDS):
            _reset_session()
            return _reply("好的，已取消導航")

        if any(kw in text for kw in ["下車了", "下車", "已經下車", "到了"]):
            reply, steps = _after_alighting()
            return _reply(reply, extra={"walk_steps": steps} if steps else None)

        return _reply("請在下車後告訴我「下車了」，或說「取消」結束導航")

    # === 階段：台北車站站內指標導引（鏡頭影格由前端送到 /api/sign_frame）===
    if stage == "station_guidance":
        guide = SESSION["guide"]
        if any(kw in text for kw in CANCEL_NAV_KEYWORDS):
            _reset_session()
            return _reply("好的，已取消導航")

        if any(kw in text for kw in station_guide.ADVANCE_KEYWORDS):
            msg = guide.advance()
            if guide.done:
                reply, steps = _finish_guidance()
                return _reply(reply, extra={"walk_steps": steps} if steps else None)
            return _reply(msg)

        if any(kw in text for kw in station_guide.REPEAT_KEYWORDS):
            return _reply(guide.repeat_text())

        return _reply(f"正在用指標牌帶你找「{guide.current_target}」，走到之後請說「到了」，或說「取消」結束導航")

    _reset_session()
    return _reply("系統狀態異常，已重置，請重新導航")


@router.post("/segment_complete")
def api_segment_complete(data: dict = Body(default={})):
    """
    前端用GPS判斷「已經走到這一段路的終點」時呼叫這個endpoint，
    不需要使用者講任何話——這是這次修正的核心：路段推進交給真實GPS距離判斷，
    不再依賴「使用者有沒有開口說話」這種不精確的替代方案。
    """
    data = data or {}
    stage = SESSION["stage"]

    if stage == "navigating_walk":
        _reset_session()
        return _reply("已到達目的地附近，導航結束")

    if stage == "navigating_transit_before":
        reply, steps = _arrive_at_boarding_stop(data.get("lat"), data.get("lng"))
        return _reply(reply, extra={"walk_steps": steps} if steps else None)

    if stage == "navigating_transit_after":
        _reset_session()
        return _reply("已到達目的地附近，導航結束")

    return _reply("")


@router.post("/position")
def api_position(data: dict = Body(default={})):
    """
    前端在「走向上車處」時定期回報 GPS。一進入台北車站地理圍欄、而且這一段要在台北車站
    搭捷運/火車/高鐵，就不等走完 GPS 路段（站內 GPS 很不準），直接切換成指標導引。
    """
    data = data or {}
    lat, lng = data.get("lat"), data.get("lng")
    leg = _current_leg()
    if (SESSION["stage"] == "navigating_transit_before"
            and leg.get("mode") in station_guide.RAIL_MODES
            and station_guide.is_taipei_main_name(leg.get("departure_stop"))
            and station_guide.in_taipei_main_geofence(lat, lng)):
        reply, _ = _arrive_at_boarding_stop(lat, lng)
        payload = _reply(reply)
        payload["changed"] = True
        return payload
    return {"changed": False, "stage": SESSION["stage"]}


@router.post("/sign_frame")
def api_sign_frame(data: dict = Body(default={})):
    """
    台北車站指標導引：前端每隔一段時間送一張鏡頭影格，這裡帶上「目前要找的指標」
    轉送給 sign_detector，再經過穩定化（連續一致才播報）後回傳要不要講話。
    """
    guide = SESSION.get("guide")
    if SESSION["stage"] != "station_guidance" or guide is None or guide.done:
        return {"active": False, "stage": SESSION["stage"]}

    data = data or {}
    image_b64 = data.get("image_base64") or ""
    if not image_b64:
        return {"active": True, "speak": False, "error": "沒有收到影像", "stage": SESSION["stage"]}

    target = guide.current_target
    result = sign_client.navigate(image_b64, target)
    out = guide.process_result(result)
    out.update({
        "active": True,
        "stage": SESSION["stage"],
        "guide_target": target,
        "sentence": result.get("sentence") or result.get("message"),
        "matched_box": result.get("matched_box"),
        "arrow_box": result.get("arrow_box"),
    })
    return out


@router.post("/locate_frame")
def api_locate_frame(data: dict = Body(default={})):
    """
    台北車站室內定位：前端送一張影格 → OCR 讀招牌/出口編號 → 比對店家資料 →
    連續幾張都指向同一個地方才播報，播完就結束（active=False，前端關鏡頭）。
    """
    session = LOCATE["session"]
    if session is None or session.done:
        return {"active": False, "stage": SESSION["stage"]}
    data = data or {}
    image_b64 = data.get("image_base64") or ""
    if not image_b64:
        return {"active": True, "speak": False, "error": "沒有收到影像", "stage": SESSION["stage"]}

    ocr = sign_client.read_texts(image_b64)
    heading = data.get("heading")     # 手機羅盤（0=北）；前端判斷不可信時不會送
    out = session.process(ocr.get("texts", []), heading=float(heading) if heading is not None else None)
    if out["done"]:
        LOCATE["last"] = out["result"]
        LOCATE["session"] = None
    return {
        "active": not out["done"],
        "speak": out["speak"],
        "text": out["text"],
        "clues": [{"kind": c["kind"], "key": c["key"]} for c in out["clues"]],
        "texts_read": [t.get("text") for t in ocr.get("texts", [])][:10],
        "error": ocr.get("error"),
        "stage": SESSION["stage"],
    }


@router.post("/reset")
def api_reset():
    """提供手機端一個「強制重來」的按鈕用，萬一卡在某個階段出不去"""
    _reset_session()
    LOCATE["session"] = None
    LOCATE["last"] = None
    return _reply("已重置")
