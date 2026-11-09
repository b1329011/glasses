"""
Flask後端伺服器 —— 把 main.py 的CLI邏輯改成網頁可呼叫的API
====================================================
架構：
    手機瀏覽器（HTML+JS，負責語音輸入/輸出、GPS）
        ↓ HTTP請求（同一個Wi-Fi網路內）
    這支程式（跑在你的筆電，負責呼叫Gemini/Places/Routes/TDX，金鑰留在這裡）

安全性重點：
    Gemini/Google Maps/TDX的金鑰只存在這支伺服器程式裡（讀取.env），
    手機端的網頁完全不會接觸到任何金鑰，這是正確的做法——
    金鑰絕對不能出現在瀏覽器看得到的程式碼裡。

執行方式：
    python3 app.py
    啟動後會印出「Running on http://0.0.0.0:5000」，
    接著找到你筆電目前的區網IP（Mac：系統設定→網路，會顯示類似192.168.x.x），
    手機（要跟筆電連同一個Wi-Fi）瀏覽器輸入：http://你的筆電IP:5000

設計說明：
    因為網頁是「一問一答」的請求模式（不像CLI可以用while迴圈一直等），
    這裡用一個簡單的伺服器端狀態機（SESSION字典）記錄「現在對話進行到哪一步」，
    每次收到手機傳來的一句話，就依照目前所在的階段(stage)決定怎麼回應、
    要不要換到下一個階段。這個demo只設計給單一使用者使用（適合展示/測試），
    真正多人使用的正式系統，會需要每個使用者有自己獨立的session（例如用cookie
    或token區分），這學期先不用做到這裡。
"""

from flask import Flask, request, jsonify

from modules import nlu, places, routing, transit

app = Flask(__name__)

CONFIRM_START_KEYWORDS = ["開始", "好", "好的", "好啊", "可以", "對", "走吧", "出發", "ok", "OK"]

# 單一使用者的對話狀態（demo規模；正式多人系統要換成per-user session）
SESSION = {
    "stage": "idle",
    "candidates": [],
    "chosen_place": None,
    "route_session": None,
    "current_route": None,
}


def _reset_session():
    SESSION["stage"] = "idle"
    SESSION["candidates"] = []
    SESSION["chosen_place"] = None
    SESSION["route_session"] = None
    SESSION["current_route"] = None


def _start_route_session(chosen_place: dict, lat: float, lng: float) -> str:
    """建立路線候選池、套即時資訊，回傳語音摘要，並把狀態切到「等待確認」"""
    origin = {"lat": lat, "lng": lng}
    session = routing.RouteSession(origin=origin, destination=chosen_place)
    current_route = transit.apply_realtime_to_route(session.get_current_route())

    SESSION["chosen_place"] = chosen_place
    SESSION["route_session"] = session
    SESSION["current_route"] = current_route
    SESSION["stage"] = "awaiting_confirm"

    return session.voice_summary(current_route)


def _reply(text: str, extra: dict = None) -> "flask.Response":
    """
    統一組裝回傳給前端的JSON，自動附上：
      - stage：目前對話階段
      - dest_lat/dest_lng：目的地座標（一旦確定要去哪，就一路帶著，讓地圖能畫出目的地標記）
    extra 可以額外帶 walk_steps（含座標的步驟清單，給前端做地圖畫線+GPS自動觸發用）
    """
    payload = {"reply": text, "stage": SESSION["stage"]}
    chosen = SESSION.get("chosen_place")
    if chosen:
        payload["dest_lat"] = chosen.get("lat")
        payload["dest_lng"] = chosen.get("lng")
    if extra:
        payload.update(extra)
    return jsonify(payload)


def _transit_boarding_prompt() -> str:
    route = SESSION["current_route"]
    SESSION["stage"] = "navigating_transit_wait_board"
    line_name = route.get("line_name", "")
    headsign = route.get("headsign", "")
    mode = route.get("mode", "大眾運輸")
    wait_min = route.get("wait_min")

    text = f"到了{route.get('departure_stop', '站牌')}，請確認搭乘{mode}{line_name}"
    if headsign:
        text += f"，往{headsign}方向"
    if wait_min is not None:
        text += f"，預計{wait_min}分鐘後進站"
    text += "。上車後請說「上車了」"
    return text


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

    if route["mode"] in ("公車", "捷運") and route.get("departure_stop"):
        SESSION["stage"] = "navigating_transit_before"
        steps_before = route.get("steps_before", [])
        if steps_before:
            reply = f"好的，開始導航。先走到{route.get('departure_stop')}。{steps_before[0]['voice_text']}"
            return reply, steps_before
        reply = _transit_boarding_prompt()
        return reply, []

    _reset_session()
    return "這條路線目前還沒有詳細的逐步導航資料，建議改用其他交通方式", []


@app.route("/api/command", methods=["POST"])
def api_command():
    data = request.get_json(force=True)
    text = (data.get("text") or "").strip()
    lat = data.get("lat")
    lng = data.get("lng")

    if not text:
        return _reply("沒有收到語音內容，請再說一次")

    stage = SESSION["stage"]

    # === 階段：閒置，等待導航請求 ===
    if stage == "idle":
        intent = nlu.parse_intent(text)
        if intent["intent"] != "navigate":
            return _reply("不好意思，我沒聽懂，你可以說「導航去...」")

        location_bias = {"lat": lat, "lng": lng} if lat is not None and lng is not None else None
        result = places.disambiguate(intent["destination"], location_bias=location_bias)

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
        intent = nlu.parse_intent(text)
        session = SESSION["route_session"]

        if intent["intent"] == "next_option":
            SESSION["current_route"] = transit.apply_realtime_to_route(session.next_route())
            return _reply(session.voice_summary(SESSION["current_route"]))

        if intent["intent"] == "cancel":
            _reset_session()
            return _reply("好的，已取消導航")

        if intent["intent"] == "change_mode":
            SESSION["current_route"] = transit.apply_realtime_to_route(session.next_route())
            reply = "好的，正在為你重新規劃交通方式...。" + session.voice_summary(SESSION["current_route"])
            return _reply(reply)

        if any(kw in text for kw in CONFIRM_START_KEYWORDS):
            reply, walk_steps = _begin_navigation()
            return _reply(reply, extra={"walk_steps": walk_steps})

        return _reply("不好意思，我沒聽懂，你可以說「換一個」、「開始」或「取消」")

    # === 以下幾個階段是「移動中」的狀態：這幾個階段的路段推進
    #     現在改由前端用GPS自動觸發（呼叫 /api/segment_complete），
    #     這裡只處理使用者主動講話要「取消」的情況，其他語音內容
    #     不會再誤觸發「跳下一句」，只會被當作單純的狀態確認訊息 ===
    if stage in (
        "navigating_walk",
        "navigating_transit_before",
        "navigating_transit_after",
    ):
        if any(kw in text for kw in ["取消", "停止", "不要導航了"]):
            _reset_session()
            return _reply("好的，已取消導航")
        return _reply("目前導航進行中，如果要取消請說「取消」")

    # === 階段：等待使用者確認上車（這個環節維持語音確認，因為系統
    #     沒辦法單靠手機GPS準確判斷「你是不是真的上車了」）===
    if stage == "navigating_transit_wait_board":
        if any(kw in text for kw in ["取消", "停止", "不要導航了"]):
            _reset_session()
            return _reply("好的，已取消導航")

        if any(kw in text for kw in ["上車了", "上車", "已經上車"]):
            route = SESSION["current_route"]
            stop_count = route.get("stop_count")
            arrival_stop = route.get("arrival_stop", "目的地站")
            SESSION["stage"] = "navigating_transit_riding"
            if stop_count:
                reply = f"好的，坐{stop_count}站，到{arrival_stop}要下車。到站後請說「下車了」"
            else:
                reply = f"請留意廣播，到{arrival_stop}要下車。到站後請說「下車了」"
            return _reply(reply)

        return _reply("請在上車後告訴我「上車了」，或說「取消」結束導航")

    # === 階段：搭乘中，等待使用者確認下車 ===
    if stage == "navigating_transit_riding":
        if any(kw in text for kw in ["取消", "停止", "不要導航了"]):
            _reset_session()
            return _reply("好的，已取消導航")

        if any(kw in text for kw in ["下車了", "下車", "已經下車", "到了"]):
            steps_after = SESSION["current_route"].get("steps_after", [])
            if steps_after:
                SESSION["stage"] = "navigating_transit_after"
                reply = f"下車後繼續走向目的地。{steps_after[0]['voice_text']}"
                return _reply(reply, extra={"walk_steps": steps_after})
            _reset_session()
            return _reply("已到達目的地附近，導航結束")

        return _reply("請在下車後告訴我「下車了」，或說「取消」結束導航")

    _reset_session()
    return _reply("系統狀態異常，已重置，請重新導航")


@app.route("/api/segment_complete", methods=["POST"])
def api_segment_complete():
    """
    前端用GPS判斷「已經走到這一段路的終點」時呼叫這個endpoint，
    不需要使用者講任何話——這是這次修正的核心：路段推進交給真實GPS距離判斷，
    不再依賴「使用者有沒有開口說話」這種不精確的替代方案。
    """
    stage = SESSION["stage"]

    if stage == "navigating_walk":
        _reset_session()
        return _reply("已到達目的地附近，導航結束")

    if stage == "navigating_transit_before":
        reply = _transit_boarding_prompt()
        return _reply(reply)

    if stage == "navigating_transit_after":
        _reset_session()
        return _reply("已到達目的地附近，導航結束")

    return _reply("")


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """提供手機端一個「強制重來」的按鈕用，萬一卡在某個階段出不去"""
    _reset_session()
    return _reply("已重置")


@app.route("/")
def index():
    with open("static_index.html", "r", encoding="utf-8") as f:
        return f.read()


if __name__ == "__main__":
    PORT = 8080  # 改用8080，避開macOS AirPlay接收器佔用的5000埠
    print("=" * 50)
    print("Agent C 室外導航 - 網頁版伺服器")
    print("手機要跟這台電腦連同一個Wi-Fi，瀏覽器輸入：")
    print(f"  https://你的電腦區網IP:{PORT}")
    print("（注意是 https，不是 http——麥克風跟GPS權限需要安全連線才會生效）")
    print("第一次連線手機會顯示「憑證不受信任」的警告頁面，這是正常的，")
    print("因為是自己簽的測試憑證，不是正式憑證，點選「進階」→「繼續前往」即可")
    print("=" * 50)
    # ssl_context='adhoc' 會自動產生一張自簽憑證，讓瀏覽器願意開放麥克風/GPS權限，
    # 不用申請任何正式憑證或第三方服務，適合區網內的開發測試
    app.run(host="0.0.0.0", port=PORT, debug=True, ssl_context="adhoc")