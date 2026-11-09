"""
警示規劃測試：用假時鐘模擬「一路走近」的情境，檢查用語、優先順序與不重複播報。

執行：python -m camera.test_alert_planner
"""

import sys

from camera.alert_planner import AlertPlanner, Candidate

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FPS = 3.0
results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(f"{'[通過]' if cond else '[失敗]'} {name}" + (f"\n        {detail}" if detail and not cond else ""))


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def run(frames_fn, n, speak_ahead=False):
    """frames_fn(i) → 這一幀的候選清單。回傳每幀 (event, 時間)。"""
    clock = Clock()
    planner = AlertPlanner(clock)
    planner.speak_ahead = speak_ahead
    out = []
    for i in range(n):
        ev, _ = planner.plan(frames_fn(i))
        out.append((ev, clock.t))
        clock.t += 1 / FPS
    return out


def spoken(events):
    return [(round(t, 1), ev["action_required"]["audio_message"]) for ev, t in events if ev["action_required"]["speak"]]


def single(c, n=6):
    # 用語測試：連 2.5~5.5 m 的提早告知也唸出來，才看得到每一種句子
    events = run(lambda i: [c()], n, speak_ahead=True)
    return spoken(events), events[-1][0]


# ---------- 1. 用語 ----------
said, _ = single(lambda: Candidate("a", "person", "front_obstacle", "行人", 4.0))
check("2.5~5.5 m 正前方：前方xx公尺有xxx", said[:1] == [(0.3, "前方4公尺有行人")], said)

said, _ = single(lambda: Candidate("a", "vehicle", "front_obstacle", "機車", 3.2, "left", blocks_path=False))
check("左前方：…稍微往右走", said and said[0][1] == "左前方3公尺有機車，稍微往右走", said)

said, _ = single(lambda: Candidate("a", "fixed", "front_obstacle", "柱子", 4.6, "right", blocks_path=False))
check("右前方：…稍微往左走", said and said[0][1] == "右前方4.5公尺有柱子，稍微往左走", said)

said, _ = single(lambda: Candidate("a", "overhead", "hanging_obstacle", "招牌", 3.0, overhead=True))
check("頭部高度：上方xx公尺有xxx，注意頭部", said and said[0][1] == "上方3公尺有招牌，注意頭部", said)

said, _ = single(lambda: Candidate("a", "person", "front_obstacle", "行人", 0.9))
check("太近：請停下", said and said[0][1] == "請停下", said)

said, _ = single(lambda: Candidate("g", "stairs", "escalator", "手扶梯", 3.0))
check("手扶梯：前方有手扶梯，請小心", said and said[0][1] == "前方有手扶梯，請小心", said)

said, _ = single(lambda: Candidate("g", "stairs", "stairs_down", "向下樓梯", 2.0, "left"))
check("樓梯帶方向：左前方有向下樓梯，請小心", said and said[0][1] == "左前方有向下樓梯，請小心", said)

said, _ = single(lambda: Candidate("g", "drop", "cliff", "大落差", 1.8))
check("大落差在 2 m 內：請停下", said and said[0][1] == "請停下", said)

said, _ = single(lambda: Candidate("g", "drop", "cliff", "大落差", 3.4))
check("大落差較遠：提早告知", said and said[0][1] == "前方3.5公尺地面有大落差，請小心", said)

said, _ = single(lambda: Candidate("g", "stairs", "escalator", "手扶梯", 0.9))
check("手扶梯很近：不叫停，提醒小心", said and said[0][1] == "前方有手扶梯，請小心", said)

said, _ = single(lambda: Candidate("a", "person", "front_obstacle", "行人", 7.0))
check("超過 5.5 m：不播報", said == [], said)

# ---------- 2. 優先順序 ----------
events = run(lambda i: [Candidate("p", "person", "front_obstacle", "行人", 3.0),
                        Candidate("v", "vehicle", "front_obstacle", "汽車", 3.5)], 4)
check("同區間：車輛優先於行人", events[-1][0]["data"]["label"] == "汽車")

events = run(lambda i: [Candidate("v", "vehicle", "front_obstacle", "汽車", 2.0, "left", blocks_path=False),
                        Candidate("p", "person", "front_obstacle", "行人", 2.2)], 4)
check("同區間：擋在路上的行人優先於旁邊的車", events[-1][0]["data"]["label"] == "行人")

events = run(lambda i: [Candidate("v", "vehicle", "front_obstacle", "汽車", 3.0),
                        Candidate("b", "object", "front_obstacle", "箱子", 1.0)], 4)
check("太近（stop）永遠最優先", events[-1][0]["action_required"]["audio_message"] == "請停下")

events = run(lambda i: [Candidate("s", "stairs", "stairs_down", "向下樓梯", 2.0),
                        Candidate("p", "person", "front_obstacle", "行人", 2.0)], 4)
check("樓梯優先於行人", events[-1][0]["data"]["label"] == "向下樓梯")

# ---------- 3. 節流：一路走近，只在關鍵時刻開口 ----------
# 行人從 5.4 m 以 1 m/s 走近，到 0.9 m 停住（每幀 0.33 m）
def approach(i):
    d = max(0.9, 5.4 - i / FPS)
    return [Candidate("p", "person", "front_obstacle", "行人", d)]


events = run(approach, 36)  # 12 秒
said = spoken(events)
msgs = [m for _, m in said]
check("走近過程：2.5~5.5 m 預設只列在畫面，不唸", not any(m.startswith(("前方5", "前方4", "前方3")) for m in msgs), said)
check("走近過程：進入 near 時再播報", any("2公尺" in m or "2.5公尺" in m for m in msgs), said)
check("走近過程：太近時說請停下", "請停下" in msgs, said)
stops = [t for t, m in said if m == "請停下"]
gaps = [b - a for a, b in zip(stops, stops[1:])]
check("請停下約每 3 秒重複一次（3 fps 下誤差一幀）", stops and all(2.9 <= g <= 3.5 for g in gaps), said)
check("12 秒內總播報次數不多（≤ 5 次）", len(said) <= 5, f"共 {len(said)} 次：{said}")
total = len(events)
check("不會每幀都講（播報幀 < 15%）", len(said) / total < 0.15, f"{len(said)}/{total}")

events = run(approach, 36, speak_ahead=True)
check("開啟「提早告知」時，第一次出現就唸", spoken(events)[0][1].startswith("前方5"), spoken(events))

# ---------- 4. 不重唸 ----------
# 偵測框閃動造成編號一直換（同一個行人，每 2 幀換一次編號）
events = run(lambda i: [Candidate(f"obj{i // 2}", "person", "front_obstacle", "行人", 2.0)], 30)
said = spoken(events)
check("編號一直換（同類、同方向、同距離）：只唸一次", len(said) == 1, said)

# 兩個不同目標接連出現（後來的機車擋在路上、優先順序更高）：第二次播報要等滿 4 秒間隔
def two(i):
    out = [Candidate("a", "person", "front_obstacle", "行人", 2.0)]
    if i >= 3:
        out.append(Candidate("b", "vehicle", "front_obstacle", "機車", 1.9))
    return out
events = run(two, 30)
said = spoken(events)
gaps = [b[0] - a[0] for a, b in zip(said, said[1:])]
check("不同目標接連出現：兩次播報至少隔 4 秒", len(said) >= 2 and said[1][1] == "前方2公尺有機車"
      and all(g >= 3.9 for g in gaps), said)

# 請停下不受間隔限制
def then_stop(i):
    return [Candidate("a", "person", "front_obstacle", "行人", 2.0 if i < 4 else 0.9)]
events = run(then_stop, 8)
said = spoken(events)
check("剛講完話馬上太近：請停下立刻講，不等間隔", said and said[-1][1] == "請停下" and said[-1][0] - said[0][0] < 2, said)

# 距離在 2.5 m 邊界附近抖動：不應反覆播報
events = run(lambda i: [Candidate("p", "person", "front_obstacle", "行人", 2.4 if i % 2 else 2.6)], 30)
said = spoken(events)
check("在區間邊界抖動：遲滯避免反覆播報（10 秒內 ≤ 2 次）", len(said) <= 2, said)

# 只出現一幀的誤偵測：不播報
events = run(lambda i: [Candidate("x", "person", "front_obstacle", "行人", 2.0)] if i == 3 else [], 10)
check("只出現一幀的誤偵測不播報", spoken(events) == [])

# 目標消失 6 秒以上再出現：重新播報
events = run(lambda i: [Candidate("p", "person", "front_obstacle", "行人", 2.0)] if i < 6 or i > 40 else [], 50)
check("目標離開後再出現會重新播報", len(spoken(events)) == 2, spoken(events))

# 震動節奏欄位
events = run(lambda i: [Candidate("p", "person", "front_obstacle", "行人", 1.0)], 4)
check("太近時 urgency = 3（最快震動）", events[-1][0]["action_required"]["urgency"] == 3)

print(f"\n{sum(results)}/{len(results)} 通過")
sys.exit(0 if all(results) else 1)
