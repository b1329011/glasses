"""
模擬結果視覺化：每個情境輸出一張 PNG 圖表 + 一份 JSON 結果檔（給前端讀取）。

    output/<scenario>_scenario.png
    output/<scenario>_result.json
    frontend/data.js              （所有情境打包，讓前端直接用瀏覽器開啟也能讀到）
"""

import json
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import fusion  # noqa: E402

from detectors import ground_drop_detector as gdd  # noqa: E402
from detectors import hanging_obstacle_detector as hod  # noqa: E402

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
FRONTEND_DATA = ROOT / "frontend" / "data.js"

# 圖表配色（與前端一致；色盲友善）
COLOR_DEPTH = "#3987e5"
COLOR_TOF = "#199e70"
COLOR_WARNING = "#c98500"
COLOR_CRITICAL = "#d03b3b"
COLOR_MUTED = "#8f8e86"

plt.rcParams["font.sans-serif"] = [
    "Microsoft JhengHei", "PingFang TC", "Noto Sans CJK TC", "Heiti TC", "Arial Unicode MS", "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False


def thresholds():
    """目前使用的門檻值（直接從偵測器常數讀取，前端也用這份）。"""
    return {
        "hanging": {
            "critical_m": hod.CRITICAL_THRESHOLD_M,
            "warning_m": hod.WARNING_THRESHOLD_M,
        },
        "ground_drop": {
            "warning_cm": gdd.DROP_WARNING_CM,
            "critical_cm": gdd.DROP_CRITICAL_CM,
            "confirm_count": gdd.CONFIRM_COUNT,
            "window_size": gdd.WINDOW_SIZE,
            "min_baseline_samples": gdd.MIN_BASELINE_SAMPLES,
            "rebaseline_count": gdd.REBASELINE_COUNT,
            "tof_max_range_cm": gdd.TOF_MAX_RANGE_CM,
        },
    }


def build_result(scenario, title, records, validation=None):
    """把逐筆處理紀錄整理成前端用的 JSON 結構。"""
    alerts = []
    prev = None
    for r in records:
        ev = r["event"]
        if ev["event_type"] != "obstacle_alert":
            prev = None
            continue
        key = (ev["priority"], ev["data"]["obstacle_type"])
        alerts.append({
            "timestamp": r["t"],
            "time": ev["timestamp"],
            "level": ev["priority"],
            "type": ev["data"]["obstacle_type"],
            "distance": ev["data"].get("distance"),
            "drop_cm": ev["data"].get("drop_height_cm"),
            "vibration": ev["action_required"]["vibration"],
            "audio_message": ev["action_required"]["audio_message"],
            "is_new": key != prev,  # 新觸發（等級或類型改變）或延續上一筆
        })
        prev = key

    return {
        "scenario": scenario,
        "title": title,
        "timestamps": [r["t"] for r in records],
        "depth_distance": [r["depth"] for r in records],
        "tof_distance": [r["tof"] for r in records],
        "hanging_levels": [r["hanging"]["level"] for r in records],
        "drop_levels": [r["drop"]["level"] for r in records],
        "drop_status": [r["drop"]["status"] for r in records],
        "drop_baseline": [r["drop"]["baseline"] for r in records],
        "alerts": alerts,
        "sample_events": _sample_events(records),
        "validation": validation or [],
        "thresholds": thresholds(),
    }


def _sample_events(records):
    """每種 (等級, 類型) 留第一筆完整的融合事件 JSON，供前端展示輸出格式。"""
    seen, out = set(), []
    for r in records:
        ev = r["event"]
        key = (ev["priority"], ev["data"]["obstacle_type"])
        if ev["event_type"] == "obstacle_alert" and key not in seen:
            seen.add(key)
            out.append(ev)
    return out


def _mark(ax, t, y, levels, wanted, color, label, marker="o"):
    pts = [(ti, yi) for ti, yi, lv in zip(t, y, levels) if lv == wanted]
    if pts:
        xs, ys = zip(*pts)
        ax.scatter(xs, ys, s=36, color=color, marker=marker, zorder=5, label=label,
                   edgecolors="white", linewidths=0.6)


def plot_scenario(result, out_dir=OUTPUT_DIR):
    """上圖：深度相機距離；下圖：ToF 距離。共用時間軸，標出 warning / critical 觸發點。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    t = result["timestamps"]
    th = result["thresholds"]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True, constrained_layout=True)
    fig.suptitle(f"障礙物避障模擬：{result['title']}", fontsize=14, fontweight="bold")

    # 上圖：深度相機
    ax1.plot(t, result["depth_distance"], color=COLOR_DEPTH, lw=2, label="深度相機 ROI 最近距離")
    ax1.axhline(th["hanging"]["warning_m"], color=COLOR_WARNING, ls="--", lw=1,
                label=f"warning 門檻 {th['hanging']['warning_m']} m")
    ax1.axhline(th["hanging"]["critical_m"], color=COLOR_CRITICAL, ls="--", lw=1,
                label=f"critical 門檻 {th['hanging']['critical_m']} m")
    _mark(ax1, t, result["depth_distance"], result["hanging_levels"], "warning", COLOR_WARNING, "觸發 warning")
    _mark(ax1, t, result["depth_distance"], result["hanging_levels"], "critical", COLOR_CRITICAL, "觸發 critical")
    ax1.set_ylabel("距離（m）")
    ax1.set_ylim(0, max(8.5, max(result["depth_distance"]) + 0.5))
    ax1.set_title("懸空障礙物（眼鏡深度相機）", loc="left", fontsize=11)

    # 下圖：ToF
    ax2.plot(t, result["tof_distance"], color=COLOR_TOF, lw=2, label="手杖 ToF 距離")
    base = result["drop_baseline"]
    ax2.plot(t, [b if b is not None else float("nan") for b in base], color=COLOR_MUTED, ls="--", lw=1,
             label="地面基準（視窗中位數）")
    ax2.plot(t, [b + th["ground_drop"]["warning_cm"] if b is not None else float("nan") for b in base],
             color=COLOR_WARNING, ls=":", lw=1, label=f"基準 +{th['ground_drop']['warning_cm']} cm")
    ax2.plot(t, [b + th["ground_drop"]["critical_cm"] if b is not None else float("nan") for b in base],
             color=COLOR_CRITICAL, ls=":", lw=1, label=f"基準 +{th['ground_drop']['critical_cm']} cm")
    filtered = ["filtered" if s == "pending" and lv == "none" else "" for s, lv in
                zip(result["drop_status"], result["drop_levels"])]
    _mark(ax2, t, result["tof_distance"], filtered, "filtered", COLOR_MUTED,
          f"超過門檻但連續未滿 {th['ground_drop']['confirm_count']} 筆（不觸發）", marker="X")
    _mark(ax2, t, result["tof_distance"], result["drop_levels"], "warning", COLOR_WARNING, "觸發 warning")
    _mark(ax2, t, result["tof_distance"], result["drop_levels"], "critical", COLOR_CRITICAL, "觸發 critical")
    ax2.set_ylabel("距離（cm）")
    ax2.set_xlabel("時間（秒）")
    ax2.set_title("地面落差（手杖 ToF）", loc="left", fontsize=11)

    for ax in (ax1, ax2):
        ax.grid(alpha=0.25)
        ax.spines[["top", "right"]].set_visible(False)
        ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=9, frameon=False)

    path = out_dir / f"{result['scenario']}_scenario.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def save_json(result, out_dir=OUTPUT_DIR):
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{result['scenario']}_result.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def visualize_scenario(scenario, title, records, validation=None, out_dir=OUTPUT_DIR):
    """產出單一情境的 PNG 與 JSON，回傳 (result, png_path, json_path)。"""
    result = build_result(scenario, title, records, validation)
    return result, plot_scenario(result, out_dir), save_json(result, out_dir)


def write_frontend_bundle(results, path=FRONTEND_DATA):
    """把所有情境結果寫成 data.js，讓前端用 file:// 直接開啟時也能讀到資料。"""
    bundle = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "thresholds": thresholds(),
        # 震動與語音文字也從 fusion.py 帶過去，前端即時模擬才會和 Python 輸出一致
        "vibration": fusion.VIBRATION,
        "audio": {f"{t}|{lv}": msg for (t, lv), msg in fusion.AUDIO.items()},
        "scenarios": results,
    }
    path.write_text(
        "// 由 main.py 自動產生，請勿手動修改\n"
        f"window.SIM_DATA = {json.dumps(bundle, ensure_ascii=False)};\n",
        encoding="utf-8",
    )
    return path
