"""
大眾運輸逐步語音導航播報模組
====================================
比步行複雜的地方在於：走路只要「線性推進」，
但搭車要處理「等車→確認上對車→坐幾站→提醒下車→走到目的地」這一整套流程，
每個環節都是視障者容易卡住的地方（尤其②等車確認、④到站提醒，
一般人用眼睛就能確認的事，這裡都要靠語音明確講出來）。

目前設計（沒有GPS階段）：
    - 上車、下車都用「使用者自己說『上車了』/『下車了』」來確認，
      之後接上GPS/IMU，可以改成偵測到「移動速度變化」自動判斷上下車，
      或搭配藍牙/NFC讀取公車動態感測（更進階的做法，這學期先不用做到這裡）
    - 坐幾站這件事，目前设計成「系統事先講清楚坐幾站」，
      不做「即時報站」（因為那需要即時定位在哪一站，這塊比走路的GPS距離判斷更複雜，
      需要額外的公車即時位置資料，比對目前在哪一站，先不在這學期範圍內）
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules import voice_io, turn_by_turn


def narrate_transit_route(route: dict) -> bool:
    """
    逐步播報一條大眾運輸路線，涵蓋：走到站牌→等車確認→上車→坐幾站→下車→走到目的地。

    Args:
        route: routing.py 產生的路線dict，需包含 steps_before/departure_stop/
               arrival_stop/line_name/headsign/stop_count/steps_after

    Returns:
        bool: True 表示走完全程，False 表示使用者中途取消
    """
    mode = route.get("mode", "大眾運輸")

    # === 第①段：走到站牌/月台 ===
    steps_before = route.get("steps_before", [])
    if steps_before:
        voice_io.speak(f"先走到{route.get('departure_stop', '站牌')}")
        for step in steps_before:
            voice_io.speak(step["voice_text"])
            signal = turn_by_turn.wait_for_next_trigger()
            if signal == "stop":
                voice_io.speak("好的，已取消導航")
                return False
    else:
        voice_io.speak(f"請前往{route.get('departure_stop', '站牌')}")

    # === 第②段：等車確認（最重要的環節，講清楚方向避免上錯車）===
    line_name = route.get("line_name", "")
    headsign = route.get("headsign", "")
    wait_min = route.get("wait_min", None)

    confirm_text = f"到了{route.get('departure_stop', '站牌')}，請確認搭乘{mode}{line_name}"
    if headsign:
        confirm_text += f"，往{headsign}方向"
    if wait_min is not None:
        confirm_text += f"，預計{wait_min}分鐘後進站"
    voice_io.speak(confirm_text)
    voice_io.speak("上車後請說「上車了」，我會提醒你什麼時候下車")

    # 等待使用者確認上車（這是明確的動作確認，不是單純推進，所以用專屬關鍵字判斷）
    while True:
        user_text = voice_io.listen()
        if any(kw in user_text for kw in ["上車了", "上車", "已經上車"]):
            break
        if any(kw in user_text for kw in turn_by_turn.STOP_KEYWORDS):
            voice_io.speak("好的，已取消導航")
            return False
        voice_io.speak("請在上車後告訴我「上車了」，或說「取消」結束導航")

    # === 第③段：坐幾站，提醒下車 ===
    stop_count = route.get("stop_count", None)
    arrival_stop = route.get("arrival_stop", "目的地站")

    if stop_count:
        voice_io.speak(f"好的，坐{stop_count}站，到{arrival_stop}要下車")
    else:
        voice_io.speak(f"請留意廣播，到{arrival_stop}要下車")

    voice_io.speak("到站後請說「下車了」")

    while True:
        user_text = voice_io.listen()
        if any(kw in user_text for kw in ["下車了", "下車", "已經下車", "到了"]):
            break
        if any(kw in user_text for kw in turn_by_turn.STOP_KEYWORDS):
            voice_io.speak("好的，已取消導航")
            return False
        voice_io.speak("請在下車後告訴我「下車了」，或說「取消」結束導航")

    # === 第④段：下車後走到目的地 ===
    steps_after = route.get("steps_after", [])
    if steps_after:
        voice_io.speak("下車後繼續走向目的地")
        for i, step in enumerate(steps_after):
            voice_io.speak(step["voice_text"])
            is_last = (i == len(steps_after) - 1)
            if is_last:
                break
            signal = turn_by_turn.wait_for_next_trigger()
            if signal == "stop":
                voice_io.speak("好的，已取消導航")
                return False

    voice_io.speak("已到達目的地附近，導航結束")
    return True


if __name__ == "__main__":
    # 快速測試：用一組假的捷運路線資料跑一次完整播報流程
    test_route = {
        "mode": "捷運",
        "departure_stop": "市政府站",
        "arrival_stop": "台北車站",
        "line_name": "板南線",
        "headsign": "台北車站",
        "wait_min": 4,
        "stop_count": 4,
        "steps_before": [
            {"instruction": "沿市府路走向站口", "distance_m": 200, "voice_text": "走約286步後，沿市府路走向站口"},
        ],
        "steps_after": [
            {"instruction": "出站後直走即為目的地", "distance_m": 100, "voice_text": "走約143步後，出站後直走即為目的地"},
        ],
    }
    print("測試大眾運輸逐步導航播報：")
    print("（走路段輸入任意文字代表『繼續』；上下車階段要輸入「上車了」「下車了」）\n")
    completed = narrate_transit_route(test_route)
    print(f"\n測試結果：{'完整走完' if completed else '中途取消'}")