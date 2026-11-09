"""
Agent C - 室外空間導航 Agent  完整流程 Demo
====================================================
用來驗證：喚醒詞 → 語意解析 → 地點消歧 → 路線推薦 → 語音摘要 →
         使用者「換一個/開始/改變交通方式/取消」的完整互動邏輯

這是一個「文字模擬語音」的CLI demo：
  - 你打字輸入代替對眼鏡說話
  - 系統印出文字代替語音播放

驗證的是「對話流程與系統邏輯」是否合理，不是真的語音辨識效果。
之後把 modules/ 裡標記「真實API串接位置」的地方換成真實API呼叫，
邏輯架構完全不用重寫。

執行方式：
    python3 main.py
"""

from modules import nlu, places, routing, transit, voice_io, location, turn_by_turn, transit_navigation


# 使用者確認開始導航的常見講法（規則版判斷，因為這是簡單的是非題，
# 不需要動用LLM，直接關鍵字比對反而更快、更省API額度）
CONFIRM_START_KEYWORDS = ["開始", "好", "好的", "好啊", "可以", "對", "走吧", "出發", "ok", "OK"]

WAKE_WORD = "小北"


def run_navigation_flow(destination_query: str):
    """
    處理一次完整的導航請求：地點消歧 → 路線推薦 → 互動迴圈
    """
    # === 步驟1：地點消歧（真實版本呼叫 Google Places API）===
    result = places.disambiguate(destination_query)

    if result["status"] == "not_found":
        voice_io.speak(result["voice_prompt"])
        return

    if result["status"] == "ambiguous":
        voice_io.speak(result["voice_prompt"])
        choice_text = voice_io.listen()

        # 先檢查使用者是不是要取消，這是最基本、也是之前漏掉沒處理的情況
        if any(kw in choice_text for kw in nlu.CANCEL_KEYWORDS):
            voice_io.speak("好的，已取消")
            return

        # 嘗試從使用者回答裡比對出選了哪一個候選地點（用地址關鍵字，例如「文青路那間」）
        chosen_place = None
        for place in result["place"]:
            if choice_text and (choice_text in place["name"] or choice_text in place["address"]):
                chosen_place = place
                break

        if chosen_place is None:
            # 比對不出來，不要默默選第一個，要誠實告知並停止，避免帶錯地方
            voice_io.speak("不好意思，我沒聽清楚是哪一個，請重新導航一次並說明確一點")
            return

        voice_io.speak(f"好的，導航去{chosen_place['name']}")
    else:
        chosen_place = result["place"]

    # === 步驟2：GPS定位 ===
    # 開發測試階段用IP定位取得大概位置，正式系統要換成手機/眼鏡的真實GPS
    current_location = location.get_current_location()
    print(f"   [系統內部] 目前位置：{current_location}")
    print(f"   [系統內部] 目的地：{chosen_place['name']} {chosen_place}")

    # === 步驟3：路線規劃（真實版本呼叫 Google Routes API，一次取多方案存池）===
    session = routing.RouteSession(origin=current_location, destination=chosen_place)

    # === 步驟4：即時運輸資訊覆蓋（真實版本呼叫 TDX API）===
    current_route = transit.apply_realtime_to_route(session.get_current_route())

    # === 步驟5：語音摘要輸出（系統直接推薦最佳方案）===
    voice_io.speak(session.voice_summary(current_route))

    # === 步驟6：互動迴圈：等待使用者回應 ===
    navigating = False
    while True:
        user_text = voice_io.listen()
        intent = nlu.parse_intent(user_text)

        if intent["intent"] == "next_option":
            current_route = transit.apply_realtime_to_route(session.next_route())
            voice_io.speak(session.voice_summary(current_route))

        elif intent["intent"] == "cancel":
            voice_io.speak("好的，已取消導航")
            return

        elif intent["intent"] == "change_mode":
            voice_io.speak("好的，正在為你重新規劃交通方式...")
            # 真實系統：以「目前GPS位置」為新起點，重新呼叫Routes API並篩選對應交通方式
            # Demo簡化：直接換下一個候選示意
            current_route = transit.apply_realtime_to_route(session.next_route())
            voice_io.speak(session.voice_summary(current_route))

        elif any(kw in user_text for kw in CONFIRM_START_KEYWORDS) or (navigating is False and user_text.strip() == ""):
            navigating = True
            start_navigation(current_route)
            return

        else:
            voice_io.speak("不好意思，我沒聽懂，你可以說「換一個」、「開始」或「取消」")


def start_navigation(route: dict):
    """
    依交通方式分流：
    - 步行：走真實逐步導航（turn_by_turn.py）
    - 公車/捷運：走真實逐步導航（transit_navigation.py），涵蓋走到站牌→
      等車確認→上車→坐幾站→下車→走到目的地
    - 其他/資料不完整時：退回示意版
    """
    voice_io.speak("好的，開始導航")

    if route["mode"] == "步行" and route.get("steps"):
        turn_by_turn.narrate_walk_route(route)
    elif route["mode"] in ("公車", "捷運") and route.get("departure_stop"):
        transit_navigation.narrate_transit_route(route)
    else:
        simulate_turn_by_turn(route)


def simulate_turn_by_turn(route: dict):
    """
    大眾運輸路線的逐步導航，目前還是示意版（固定台詞），
    因為公車/捷運的逐步語音要處理的東西跟步行不同（上下車提醒、
    到站前倒數等），還沒開發，之後可以比照 turn_by_turn.py 的方式做。
    """
    print("\n--- 逐步導航開始（模擬）---")

    landmark = route.get("landmark", "")
    first_step = f"從{landmark}出發" if landmark else "開始前往目的地"

    steps = [
        first_step,
        "再走30步後右轉",
        "前方即為目的地方向，注意路口車流",
    ]
    for step in steps:
        voice_io.speak(step)
    voice_io.speak("你可以隨時說「我想改搭車」或「取消」來打斷導航")
    print("--- 逐步導航示意結束 ---\n")


def main():
    print("=" * 50)
    print("Agent C 室外導航 Demo（文字模擬語音）")
    print(f"提示：先輸入喚醒詞「{WAKE_WORD}」開始，或直接輸入「導航去...」測試")
    print("輸入 exit 結束程式")
    print("=" * 50)

    while True:
        user_text = voice_io.listen()

        # 支援多種「結束程式」的講法，不用硬記只有 exit 才有效
        if user_text.strip().lower() in ("exit", "結束", "結束程式", "離開", "quit"):
            print("結束demo")
            break

        # 喚醒詞判斷（真實版本：on-device wake word引擎持續監聽）
        if WAKE_WORD in user_text:
            voice_io.speak_wake_prompt()
            user_text = voice_io.listen()

        intent = nlu.parse_intent(user_text)

        if intent["intent"] == "navigate":
            run_navigation_flow(intent["destination"])
        elif intent["intent"] == "unknown":
            voice_io.speak("不好意思，我沒聽懂，你可以說「導航去...」")
        else:
            voice_io.speak("目前沒有進行中的導航")


if __name__ == "__main__":
    main()