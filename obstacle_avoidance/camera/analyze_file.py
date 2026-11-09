"""
離線分析圖片或影片（不需要鏡頭與網頁），輸出標註結果圖並印出判斷。

    python -m camera.analyze_file 照片.jpg [--height 1.5 --pitch 15 --hfov 70]
    python -m camera.analyze_file 影片.mp4 --every 5
"""

import argparse
import base64
import sys
from pathlib import Path

import cv2
import numpy as np

from camera.scene_analyzer import Models, SceneAnalyzer

OUT_DIR = Path(__file__).resolve().parent.parent / "output" / "camera"
LEVEL_BGR = {"critical": (59, 59, 208), "warning": (25, 178, 250), "none": (12, 163, 12)}


def draw(bgr, res):
    img = bgr.copy()
    h, w = img.shape[:2]
    for side in res["corridor"]:
        pts = np.array([[p[0] * w, p[1] * h] for p in side], np.int32)
        if len(pts) > 1:
            cv2.polylines(img, [pts], False, (229, 135, 57), 2)
    edge = res["ground"]["edge"]
    if edge:
        cv2.line(img, (int(edge[0][0] * w), int(edge[0][1] * h)), (int(edge[1][0] * w), int(edge[1][1] * h)),
                 LEVEL_BGR[res["hazards"]["ground"]["level"]] if res["hazards"]["ground"]["level"] != "none"
                 else (200, 200, 200), 3)
    for o in res["objects"]:
        x1, y1, x2, y2 = (int(o["box"][0] * w), int(o["box"][1] * h), int(o["box"][2] * w), int(o["box"][3] * h))
        color = (25, 178, 250) if o.get("in_path") else (180, 180, 180)
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        txt = f"{o['label']} {o['distance']:.1f}m" if o.get("distance") is not None else o["label"]
        cv2.putText(img, txt, (x1 + 3, max(y1 - 6, 14)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    if res.get("depth_image"):
        raw = base64.b64decode(res["depth_image"].split(",", 1)[1])
        depth = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        depth = cv2.resize(depth, (w, h))
        img = np.hstack([img, depth])
    return img


def summarize(res):
    lines = [f"  深度：{res['calibration']['status']}"
             + (f"（誤差 {res['calibration']['rel_error']:.0%}）" if res["calibration"].get("rel_error") is not None else "")]
    for key, name in (("front", "前方障礙物"), ("hanging", "懸空障礙物"), ("ground", "地面")):
        hz = res["hazards"][key]
        dist = f"{hz['distance']} m" if hz.get("distance") is not None else "—"
        lines.append(f"  {name}：{hz.get('type_zh') or '無'} / {hz['level']} / {dist}")
    for o in res["objects"]:
        lines.append(f"  物體：{o['label_zh']}（{o['conf']:.2f}） 距離 {o.get('distance')} m "
                     f"{o.get('direction') or ''}{'，在行進路線上' if o.get('in_path') else ''}")
    ev = res["event"]
    lines.append(f"  融合：{ev['priority']} → {ev['action_required']['audio_message'] or '（無警報）'}")
    lines.append(f"  耗時：{res['timing_ms']['total']} ms")
    return "\n".join(lines)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="離線分析圖片／影片")
    ap.add_argument("path")
    ap.add_argument("--height", type=float, help="鏡頭離地高度（m）")
    ap.add_argument("--pitch", type=float, help="鏡頭往下傾角（度）")
    ap.add_argument("--hfov", type=float, help="水平視角（度）")
    ap.add_argument("--every", type=int, default=5, help="影片每幾幀分析一次")
    args = ap.parse_args()

    analyzer = SceneAnalyzer(Models())
    analyzer.set_geometry(args.height, args.pitch, args.hfov)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    src = Path(args.path)

    img = cv2.imdecode(np.fromfile(str(src), np.uint8), cv2.IMREAD_COLOR)
    if img is not None:
        for _ in range(2):  # 時間平滑需要連續幀，靜態圖片重複送兩次
            res = analyzer.process(img)
        out = OUT_DIR / f"{src.stem}_analyzed.jpg"
        cv2.imencode(".jpg", draw(img, res))[1].tofile(str(out))
        print(f"{src.name}\n{summarize(res)}\n  輸出：{out}")
        return 0

    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        print(f"無法讀取：{src}")
        return 1
    writer, i = None, 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % args.every == 0:
            res = analyzer.process(frame)
            vis = draw(frame, res)
            if writer is None:
                out = OUT_DIR / f"{src.stem}_analyzed.mp4"
                writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), 6, (vis.shape[1], vis.shape[0]))
            writer.write(vis)
            ev = res["event"]
            if ev["priority"] != "none":
                print(f"  第 {i} 幀：{ev['priority']} {ev['action_required']['audio_message']}")
        i += 1
    if writer:
        writer.release()
        print(f"輸出：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
