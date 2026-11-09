"""
障礙物避障模組：模擬驗證主程式

流程：感測器（模擬 / 真實） → 偵測器（懸空障礙物 + 地面落差） → 融合 → 輸出事件
執行：
    python main.py          跑四種模擬情境，印出驗證結果並產生圖表 / JSON / 前端資料
    python main.py --real   改用真實硬體讀取器（尚未實作，會提示）
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from detectors import ground_drop_detector as gdd
from detectors import hanging_obstacle_detector
from detectors.ground_drop_detector import CONFIRM_COUNT, GroundDropDetector
from fusion import fuse, is_alert
from sensors import simulate_data as sim
from sensors.real_sensor_reader import SensorReader
from visualize import OUTPUT_DIR, visualize_scenario, write_frontend_bundle

LEVEL_ZH = {"critical": "危險 critical", "warning": "警告 warning", "none": "無"}
TYPE_ZH = {"hanging_obstacle": "懸空障礙物", "ground_drop": "地面落差", "multiple": "懸空障礙物 + 地面落差"}

# 情境參數集中在這裡，驗證條件也依這些數值判斷
HANGING_AT = 30
DROP_AT = 35
MIXED = {"obstacle_at_step": 28, "dodge_at_step": 33, "drop_at_step": 64}


# ---------- 處理流程（與資料來源無關） ----------

def run_pipeline(reader, n_steps, start_time):
    """從 reader 讀 n_steps 筆資料，經兩個偵測器與融合，回傳逐筆紀錄。"""
    drop_detector = GroundDropDetector()
    records = []
    for i in range(n_steps):
        frame = reader.read()
        hanging = hanging_obstacle_detector.detect(frame.depth_min_distance)
        drop = drop_detector.update(frame.tof_distance)
        iso = (start_time + timedelta(seconds=frame.timestamp)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        records.append({
            "i": i,
            "t": frame.timestamp,
            "depth": frame.depth_min_distance,
            "tof": frame.tof_distance,
            "hanging": hanging,
            "drop": drop,
            "event": fuse(hanging, drop, timestamp=iso),
        })
    return records


# ---------- 統計與驗證 ----------

def first_step(records, source, level):
    """某個偵測器第一次達到指定等級的步數（source: hanging / drop）。"""
    for r in records:
        if r[source]["level"] == level:
            return r["i"]
    return None


def alert_steps(records, source, lo=0, hi=None):
    hi = len(records) if hi is None else hi
    return [r["i"] for r in records[lo:hi] if r[source]["level"] != "none"]


def filtered_spikes(records):
    """超過落差門檻但沒有連續 CONFIRM_COUNT 筆（被濾掉的手杖擺動）的次數。"""
    count, in_run = 0, False
    for r in records:
        status = r["drop"]["status"]
        if status == "pending":
            in_run = True
        elif in_run:
            count += status != "confirmed"  # 後來被確認的不算濾除
            in_run = False
    return count


def check(desc, passed):
    return {"desc": desc, "passed": bool(passed)}


def validate_normal(records):
    spikes = filtered_spikes(records)
    return [
        check("全程沒有任何警報", not any(is_alert(r["event"]) for r in records)),
        check(f"手杖擺動突波 {spikes} 次，全部被連續 {CONFIRM_COUNT} 筆確認機制濾除", spikes > 0),
    ]


def validate_hanging(records):
    w, c = first_step(records, "hanging", "warning"), first_step(records, "hanging", "critical")
    return [
        check(f"先觸發 warning（第 {w} 筆）再升級為 critical（第 {c} 筆）", w is not None and c is not None and w < c),
        check(f"critical 在設定的障礙物位置（第 {HANGING_AT} 筆）附近觸發",
              c is not None and abs(c - HANGING_AT) <= 2),
        check("地面落差偵測器沒有誤報", not alert_steps(records, "drop")),
    ]


def validate_drop(records):
    c = first_step(records, "drop", "critical")
    expect = DROP_AT + CONFIRM_COUNT - 1
    return [
        check(f"落差出現前（第 {DROP_AT} 筆前）沒有誤報，擺動突波 {filtered_spikes(records)} 次被濾除",
              not alert_steps(records, "drop", hi=DROP_AT)),
        check(f"落差出現後連續 {CONFIRM_COUNT} 筆確認，於第 {c} 筆觸發 critical（預期第 {expect} 筆）", c == expect),
        check("落差持續期間警報不中斷", c is not None and len(alert_steps(records, "drop", lo=c)) == len(records) - c),
        check("懸空障礙物偵測器沒有誤報", not alert_steps(records, "hanging")),
    ]


def validate_mixed(records):
    o, d, g = MIXED["obstacle_at_step"], MIXED["dodge_at_step"], MIXED["drop_at_step"]
    hc = first_step(records, "hanging", "critical")
    gw = first_step(records, "drop", "warning")
    quiet = [r["i"] for r in records[d:g] if is_alert(r["event"])]
    return [
        check(f"遇懸空障礙物：第 {hc} 筆觸發 critical", hc is not None and abs(hc - o) <= 2),
        check(f"閃避後（第 {d}~{g - 1} 筆）恢復正常，無警報", not quiet),
        check(f"遇階梯：第 {gw} 筆觸發地面落差 warning", gw == g + CONFIRM_COUNT - 1),
        check("階梯落差約 40 cm，判定為 warning 而非 critical", first_step(records, "drop", "critical") is None),
    ]


SCENARIOS = [
    ("normal_walking", "正常走路", lambda: sim.simulate_normal_walking(steps=50), validate_normal),
    ("hanging_obstacle", "懸空障礙物（招牌）",
     lambda: sim.simulate_hanging_obstacle(steps=50, obstacle_at_step=HANGING_AT), validate_hanging),
    ("ground_drop", "地面落差（月台邊緣）",
     lambda: sim.simulate_ground_drop(steps=50, drop_at_step=DROP_AT), validate_drop),
    ("mixed", "混合情境", lambda: sim.simulate_mixed_scenario(steps=80, **MIXED), validate_mixed),
]


# ---------- 輸出 ----------

def triggers(records):
    """把逐筆警報整理成「觸發點」：等級或類型改變時才算一次新的觸發。"""
    out, prev = [], None
    for r in records:
        ev = r["event"]
        key = (ev["priority"], ev["data"]["obstacle_type"]) if is_alert(ev) else None
        if key and key != prev:
            out.append({"t": r["t"], "i": r["i"], "level": key[0], "type": key[1], "len": 1})
        elif key:
            out[-1]["len"] += 1
        prev = key
    return out


def print_scenario(idx, title, records, validation):
    line = "─" * 64
    print(f"\n{line}\n【情境 {idx}】{title}（共 {len(records)} 筆，{records[-1]['t']:.1f} 秒）\n{line}")
    trig = triggers(records)
    for level in ("critical", "warning"):
        items = [x for x in trig if x["level"] == level]
        steps = sum(x["len"] for x in items)
        print(f"  {LEVEL_ZH[level]:<12} 觸發 {len(items)} 次（共 {steps} 筆警報）")
        for x in items:
            print(f"      ▸ t = {x['t']:5.1f} 秒（第 {x['i']:>2} 筆）{TYPE_ZH[x['type']]}，持續 {x['len']} 筆")
    if not trig:
        print("  （無任何警報）")
    print("  驗證：")
    for v in validation:
        print(f"    {'[通過]' if v['passed'] else '[失敗]'} {v['desc']}")


def check_real_reader():
    try:
        SensorReader().read()
    except NotImplementedError as e:
        print(f"真實硬體讀取器尚未實作：{e}")
        print("請在 sensors/real_sensor_reader.py 實作 SensorReader，main.py 的流程不需要修改。")
        return 1
    return 0


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="障礙物避障模組模擬驗證")
    parser.add_argument("--real", action="store_true", help="使用真實硬體讀取器（尚未實作）")
    args = parser.parse_args()
    if args.real:
        return check_real_reader()

    print("=" * 64)
    print("  障礙物避障模組｜深度相機 + ToF 融合偵測｜模擬驗證")
    print("=" * 64)
    print(f"  懸空障礙物門檻：critical < {hanging_obstacle_detector.CRITICAL_THRESHOLD_M} m，"
          f"warning ≤ {hanging_obstacle_detector.WARNING_THRESHOLD_M} m")
    print(f"  地面落差門檻：warning ≥ {gdd.DROP_WARNING_CM} cm，critical ≥ {gdd.DROP_CRITICAL_CM} cm，"
          f"需連續 {gdd.CONFIRM_COUNT} 筆確認")

    start_time = datetime.now(timezone.utc).replace(microsecond=0)
    results, summary = {}, []
    example_event = None

    for idx, (key, title, make_data, validate) in enumerate(SCENARIOS, 1):
        data = make_data()
        reader = sim.SimulatedSensorReader(data)  # 之後換成 SensorReader() 即可串接真實硬體
        records = run_pipeline(reader, len(reader), start_time)
        validation = validate(records)
        print_scenario(idx, title, records, validation)

        result, png, js = visualize_scenario(key, title, records, validation)
        results[key] = result
        print(f"  輸出：{png.relative_to(OUTPUT_DIR.parent)}、{js.relative_to(OUTPUT_DIR.parent)}")

        trig = triggers(records)
        summary.append((title, sum(x["level"] == "critical" for x in trig),
                        sum(x["level"] == "warning" for x in trig), all(v["passed"] for v in validation)))
        if example_event is None and key == "hanging_obstacle":
            example_event = next(r["event"] for r in records if r["event"]["priority"] == "critical")

    bundle = write_frontend_bundle(results)

    print("\n" + "=" * 64)
    print("  融合輸出範例（Navigation Agent 事件）")
    print("=" * 64)
    print(json.dumps(example_event, ensure_ascii=False, indent=2))

    print("\n" + "=" * 64)
    print("  驗證總結")
    print("=" * 64)
    print(f"  {'情境':<22}{'critical':>10}{'warning':>10}    結果")
    for title, c, w, ok in summary:
        pad = 24 - sum(2 if ord(ch) > 127 else 1 for ch in title) + len(title)
        print(f"  {title:<{pad}}{c:>10}{w:>10}    {'通過' if ok else '失敗'}")
    all_ok = all(s[3] for s in summary)
    print("-" * 64)
    print(f"  {'全部情境驗證通過：模擬數據皆正確觸發 / 不觸發警報' if all_ok else '有情境驗證失敗，請檢查上方細節'}")
    print(f"  前端資料：{bundle.relative_to(OUTPUT_DIR.parent)}（用瀏覽器開啟 frontend/index.html）")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
