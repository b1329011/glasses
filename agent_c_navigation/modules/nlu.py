"""
NLU 模組：語意理解 + 目的地擷取
====================================
版本說明：
  - 沒有設定 GEMINI_API_KEY → 走簡單關鍵字規則（原本demo版本，免金鑰可跑）
  - 有設定 GEMINI_API_KEY  → 真的呼叫 Gemini API 做意圖解析（驗證AI可行性用這個）

申請Gemini免費金鑰：https://aistudio.google.com → 左下角「Get API key」
拿到金鑰後，建立 .env 檔案（跟 main.py 同一層），內容寫：
    GEMINI_API_KEY=你的金鑰貼在這裡
"""

import os
import json
import re
import time
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from modules import cache

load_dotenv()  # 讀取 .env 檔案裡的環境變數

USE_REAL_LLM = os.getenv("GEMINI_API_KEY") is not None  # 有金鑰就自動切換成真實模式

# 打斷/改變交通方式的關鍵字（規則版fallback用）
CHANGE_MODE_KEYWORDS = ["換交通方式", "改搭車", "改走路", "改搭", "我想改搭"]
CANCEL_KEYWORDS = ["取消", "停止", "不要導航了"]
NAVIGATE_KEYWORDS = ["導航去", "帶我去", "我要去", "去"]


def _parse_intent_rule_based(user_text: str) -> dict:
    """規則版：不需要API金鑰，用關鍵字比對（原本demo版本）"""
    text = user_text.strip()

    for kw in CHANGE_MODE_KEYWORDS:
        if kw in text:
            return {"intent": "change_mode", "destination": None, "raw": text}

    for kw in CANCEL_KEYWORDS:
        if kw in text:
            return {"intent": "cancel", "destination": None, "raw": text}

    if "換一個" in text or "換方案" in text:
        return {"intent": "next_option", "destination": None, "raw": text}

    for kw in NAVIGATE_KEYWORDS:
        if kw in text:
            idx = text.find(kw) + len(kw)
            destination = text[idx:].strip()
            if destination:
                return {"intent": "navigate", "destination": destination, "raw": text}

    return {"intent": "unknown", "destination": None, "raw": text}


def _parse_intent_gemini(user_text: str) -> dict:
    """
    真實版：呼叫 Gemini API 做意圖解析。
    這是「AI可行性驗證」真正要測的地方——口語化、不標準的講法，
    LLM能不能正確抓出意圖跟目的地。

    加入本地快取：完全相同的句子如果之前問過，直接回傳快取結果，
    不用重打API。這對高頻短句（「換一個」「取消」）特別有幫助，
    也能減少撞到Gemini免費版額度限制（你實測過一分鐘限5次）的機率。
    快取設定7天過期，因為語意解析的結果基本上不會因時間而改變。
    """
    cached = cache.get("nlu", user_text, ttl_seconds=7 * 24 * 3600)
    if cached is not None:
        cached["_from_cache"] = True
        return cached

    import google.generativeai as genai

    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    # 用 "-latest" 別名而非寫死版本號（如 gemini-2.5-flash），
    # 這樣Google之後汰換模型版本時，程式不會因為型號下架而報錯
    model = genai.GenerativeModel("gemini-flash-latest")

    prompt = f"""你是視障導航系統的語音指令解析器。請解析以下語音轉文字後的指令，
只回傳JSON，不要有其他文字或Markdown符號：

指令：「{user_text}」

意圖分類（intent）只能是以下其中一種：
- "navigate"：使用者想導航去某個地方
- "next_option"：使用者想換路線候選方案（例如說「換一個」「換方案」）
- "change_mode"：使用者想改變交通方式（例如「改搭車」「我想改走路」）
- "cancel"：使用者想取消導航
- "unknown"：聽不出明確意圖

回傳格式：{{"intent": "分類結果", "destination": "地點名稱或null"}}
"""

    try:
        response = model.generate_content(prompt)
        raw_text = response.text.strip()
        # 有時模型會包一層 ```json ... ``` ，先清掉
        raw_text = re.sub(r"^```json\s*|\s*```$", "", raw_text.strip())
        result = json.loads(raw_text)
        result["raw"] = user_text
        cache.set("nlu", user_text, result)  # 存進快取，下次同樣的話不用再問AI
        return result
    except Exception as e:
        print(f"   [警告] Gemini API 呼叫失敗，改用規則版判斷：{e}")
        fallback_result = _parse_intent_rule_based(user_text)
        fallback_result["_used_fallback"] = True  # 標記這句其實沒有真的測到LLM
        return fallback_result


def parse_intent(user_text: str) -> dict:
    """
    對外統一介面：main.py 呼叫這個函式，不用管背後是規則版還是真實LLM版。
    """
    if USE_REAL_LLM:
        return _parse_intent_gemini(user_text)
    return _parse_intent_rule_based(user_text)


if __name__ == "__main__":
    print(f"目前模式：{'真實Gemini API' if USE_REAL_LLM else '規則版（未偵測到GEMINI_API_KEY）'}\n")
    print("=" * 60)

    # 20句測試樣本，涵蓋不同難度與情境，每句附上「人工判定的正確答案」，
    # 跑完會自動比對、算出正確率，可直接作為驗證報告的數據來源。
    #
    # 分類說明：
    #   標準 = 標準說法，規則版跟LLM應該都要對
    #   省略 = 口語省略關鍵字，測試LLM能否理解言外之意
    #   長輩 = 模擬長輩使用者的講法
    #   模糊 = 指代不清楚，正確答案應為 unknown（誠實回報聽不懂，而非亂猜）
    #   混合 = 一句話包含多個動作
    #   干擾 = 與導航無關的閒聊，正確答案應為 unknown
    #   反悔 = 使用者中途改變心意/糾正
    tests = [
        # (輸入句子, 正確intent, 分類)
        ("導航去台北車站", "navigate", "標準"),
        ("我要去市政府", "navigate", "標準"),
        ("帶我去龍山寺", "navigate", "標準"),
        ("去車站那邊", "navigate", "省略"),
        ("北車怎麼走", "navigate", "省略"),
        ("我想去那個常去的診所啦", "navigate", "省略"),
        ("載我去買菜的地方", "navigate", "長輩"),
        ("我要去看醫生", "navigate", "長輩"),
        ("陪我去廟裡拜拜", "navigate", "長輩"),
        ("去那個地方", "unknown", "模糊"),
        ("回家", "navigate", "模糊"),  # 「家」本身是明確目的地，即使系統不知道住址，意圖仍是navigate
        ("換一個好了", "next_option", "標準"),
        ("換個方案", "next_option", "標準"),
        ("欸我突然想搭車耶", "change_mode", "省略"),
        ("我想改用走的", "change_mode", "標準"),
        ("算了不要走了", "cancel", "標準"),
        ("先不要了", "cancel", "省略"),
        ("不是右邊啦是左邊", "unknown", "反悔"),  # 這種糾正細節，意圖解析階段合理判斷為unknown
        ("今天天氣真好", "unknown", "干擾"),
        ("你叫什麼名字", "unknown", "干擾"),
    ]

    correct = 0
    wrong_cases = []
    fallback_count = 0

    for i, (text, expected_intent, category) in enumerate(tests):
        result = parse_intent(text)
        actual_intent = result.get("intent")
        used_fallback = result.get("_used_fallback", False)
        is_correct = actual_intent == expected_intent

        if used_fallback:
            fallback_count += 1

        if is_correct:
            correct += 1
            mark = "✅"
        else:
            wrong_cases.append((text, expected_intent, actual_intent, category))
            mark = "❌"

        fallback_tag = "　⚠️此句因API限制退回規則版，非真實LLM結果" if used_fallback else ""
        print(f"{mark} [{category}] 輸入：{text}{fallback_tag}")
        print(f"    預期：{expected_intent}　實際：{actual_intent}　完整結果：{result}\n")

        if USE_REAL_LLM and i < len(tests) - 1:
            time.sleep(13)

    total = len(tests)
    accuracy = correct / total * 100

    print("=" * 60)
    print(f"測試總數：{total}　正確：{correct}　正確率：{accuracy:.1f}%")

    if fallback_count > 0:
        print(f"\n⚠️ 注意：有{fallback_count}句因為API額度限制被迫退回規則版，")
        print(f"   這幾句不是真實LLM的表現，上面的正確率數字包含了規則版結果，")
        print(f"   如果要拿去當作「LLM可行性驗證」的正式數據，建議重跑一次，")
        print(f"   確保fallback_count = 0（畫面上都沒有⚠️標記）才算數。")

    if wrong_cases:
        print("\n答錯的案例（可用來分析系統弱點）：")
        for text, expected, actual, category in wrong_cases:
            print(f"  [{category}] 「{text}」→ 預期{expected}，實際判斷成{actual}")
    else:
        print("\n全部答對！")