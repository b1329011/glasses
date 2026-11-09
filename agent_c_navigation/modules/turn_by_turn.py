"""
逐步語音導航播報模組
====================================
這個模組負責「把一份步驟清單，逐步唸給使用者聽」這件事。

目前設計（沒有GPS階段）：
    使用者自己說「下一步」或「到了」來推進到下一個指示，
    這是刻意先跳過GPS，把「步驟清單→語音播報」這個邏輯先做完整、先驗證。

之後接上GPS要怎麼改：
    把 wait_for_next_trigger() 這個函式換掉即可，其他邏輯完全不用動。
    真實版本的觸發條件會是：
        持續讀取GPS位置 → 計算跟下一個轉彎點的距離 → 距離小於一定門檻
        （例如10公尺內）→ 自動觸發下一句語音，不需要使用者自己講「下一步」。
    也就是說，現在「使用者說下一步」跟之後「GPS判斷你走到了」，
    做的是同一件事：決定「什麼時候該唸下一句」，只是觸發來源不同。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules import voice_io

# 使用者要求提前進到下一步的講法
NEXT_STEP_KEYWORDS = ["下一步", "到了", "然後呢", "繼續"]
STOP_KEYWORDS = ["取消", "停止", "不要導航了", "先不要", "停"]


def wait_for_next_trigger() -> str:
    """
    決定「什麼時候該唸下一句指示」的觸發點。

    目前版本：等待使用者自己說話來推進（手動模擬）。
    之後GPS版本：這裡會換成「持續檢查GPS距離，達到門檻就回傳」，
                 呼叫端（narrate_walk_route）完全不用改。

    Returns:
        str: "next"（推進到下一步）或 "stop"（使用者要取消）
    """
    user_text = voice_io.listen()

    if any(kw in user_text for kw in STOP_KEYWORDS):
        return "stop"
    return "next"  # 目前設計成：只要不是明確喊停，任何回應都視為「可以繼續」


def narrate_walk_route(route: dict) -> bool:
    """
    逐步播報一條步行路線的所有指示。

    Args:
        route: routing.py 產生的路線dict，需包含 "steps" 欄位

    Returns:
        bool: True 表示走完全程，False 表示使用者中途取消
    """
    steps = route.get("steps", [])

    if not steps:
        voice_io.speak("這條路線沒有詳細的分段指示，建議直接照地圖走或改用其他方式確認方向")
        return True

    voice_io.speak(f"好的，開始導航，全程共{len(steps)}個路段")

    for i, step in enumerate(steps):
        voice_io.speak(step["voice_text"])

        is_last_step = (i == len(steps) - 1)
        if is_last_step:
            break

        # 講完這句之後，等待「該唸下一句了」的觸發訊號
        # （現在是等使用者說話，之後是等GPS判斷走到定位點）
        signal = wait_for_next_trigger()
        if signal == "stop":
            voice_io.speak("好的，已取消導航")
            return False

    voice_io.speak("已到達目的地附近，導航結束")
    return True


if __name__ == "__main__":
    # 快速測試：用假的步驟清單跑一次播報流程
    test_route = {
        "steps": [
            {"instruction": "沿中山路直走", "distance_m": 200, "voice_text": "走約286步後，沿中山路直走"},
            {"instruction": "在7-11前左轉", "distance_m": 80, "voice_text": "走約114步後，在7-11前左轉"},
            {"instruction": "直走至目的地", "distance_m": 50, "voice_text": "走約71步後，直走至目的地"},
        ]
    }
    print("測試逐步語音導航播報（每唸完一句，輸入任意文字代表『繼續』，輸入「取消」測試中途喊停）：\n")
    completed = narrate_walk_route(test_route)
    print(f"\n測試結果：{'完整走完' if completed else '中途取消'}")