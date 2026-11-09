"""
地面危險分析：斷崖、向下/向上樓梯、單一階差

做法（純幾何規則，不需訓練）：
1. 沿行進通道（左右 ±CORRIDOR_HALF_WIDTH_M），由畫面底部往上逐列取「實測離地高度」的中位數，
   得到一條由近到遠的地面剖面 (forward, height)。平地的 height ≈ 0。
2. 把剖面切成「高度穩定」的平台段（誤差 GROUND_FLAT_TOL_M 內），太短的段（樓梯踢面、雜訊）忽略。
3. 相鄰平台的高度差：
   - 下降 ≥ CLIFF_DROP_M，或地面突然看不到（距離跳很遠）→ 斷崖 / 大落差
   - 連續 ≥ STAIRS_MIN_STEPS 個 8~25 cm 的下降 → 向下樓梯；上升 → 向上樓梯
   - 只有一個 → 單一階差（路緣、台階）
"""

import numpy as np

from camera import config as cfg

HAZARD_ZH = {
    "cliff": "斷崖／大落差", "stairs_down": "向下樓梯", "stairs_up": "向上樓梯",
    "step_down": "向下階差", "step_up": "向上階差",
}


def _row_profile(forward, height, expected_z, expected_forward, u_norm):
    """由近到遠逐列取通道內的地面剖面。回傳 list of dict(row, f, h, f_exp, missing)。"""
    rows = []
    h_img = forward.shape[0]
    for v in range(h_img - 1, -1, -1):
        z_exp = expected_z[v]
        if not np.isfinite(z_exp):
            break  # 到地平線了
        f_exp = expected_forward[v]
        if f_exp > cfg.GROUND_MAX_FORWARD_M:
            break
        if f_exp < cfg.GROUND_MIN_FORWARD_M:
            continue
        # 通道用「如果是地面」的左右位置決定，只和幾何有關，地面消失時也能判斷
        in_corridor = np.abs(z_exp * u_norm) <= cfg.CORRIDOR_HALF_WIDTH_M
        n = int(in_corridor.sum())
        if n < 3:
            continue
        f, hh = forward[v][in_corridor], height[v][in_corridor]
        valid = np.isfinite(f) & np.isfinite(hh)
        if (~valid).sum() > n * 0.6:
            rows.append({"row": v, "f": None, "h": None, "f_exp": float(f_exp), "missing": True})
        elif valid.sum() >= 3:
            rows.append({"row": v, "f": float(np.median(f[valid])), "h": float(np.median(hh[valid])),
                         "f_exp": float(f_exp), "missing": False})
    return rows


def _segments(rows):
    """把剖面切成高度穩定的平台段。"""
    segs, cur = [], None
    for r in rows:
        if r["missing"]:
            if cur:
                segs.append(cur)
                cur = None
            segs.append({"missing": True, "f0": None, "row": r["row"], "f_exp": r["f_exp"], "n": 1})
            continue
        if cur and abs(r["h"] - cur["h"]) <= cfg.GROUND_FLAT_TOL_M:
            cur["hs"].append(r["h"])
            cur["h"] = float(np.median(cur["hs"]))
            cur["f1"] = r["f"]
            cur["n"] += 1
        else:
            if cur:
                segs.append(cur)
            cur = {"missing": False, "h": r["h"], "hs": [r["h"]], "f0": r["f"], "f1": r["f"], "row": r["row"], "n": 1}
    if cur:
        segs.append(cur)

    # 合併連續的 missing；過濾太短的平台
    out = []
    for s in segs:
        if s["missing"]:
            if out and out[-1]["missing"]:
                out[-1]["n"] += 1
            else:
                out.append(s)
        elif _is_plateau(s):
            out.append(s)
    return out


def _is_plateau(seg):
    """平台＝前後有一定深度且高度幾乎不變；垂直面（踢面、箱子正面）前後距離幾乎不變，不算。"""
    span = seg["f1"] - seg["f0"]
    return span >= cfg.MIN_PLATEAU_M and span >= 1.5 * (max(seg["hs"]) - min(seg["hs"]))


def _level_for(kind, distance):
    if distance is None or distance > cfg.GROUND_ALERT_RANGE_M:
        return "none"
    if kind in ("cliff", "stairs_down"):
        return "critical" if distance <= cfg.GROUND_CRITICAL_RANGE_M else "warning"
    return "warning"


def analyze_ground(forward, height, expected_z, expected_forward, u_norm):
    """
    forward / height：每個像素的世界座標（無效深度為 inf / nan）
    expected_z / expected_forward：每一列若為平地時的深度與前方距離
    u_norm：每一欄的 (u - cx) / fx
    回傳：
      {level, type, distance, height_change_m, steps, edge_row, profile: [[f, h], ...], plateaus: [...]}
    """
    rows = _row_profile(forward, height, expected_z, expected_forward, u_norm)
    segs = _segments(rows)
    result = {
        "level": "none", "type": None, "distance": None, "height_change_m": None, "steps": 0, "edge_row": None,
        "profile": [[round(r["f"], 2), round(r["h"], 3)] for r in rows if not r["missing"]],
        "plateaus": [{"f0": round(s["f0"], 2), "f1": round(s["f1"], 2), "h": round(s["h"], 3)}
                     for s in segs if not s["missing"]],
    }
    if not segs:
        return result

    transitions = []  # (kind, distance, delta_h, row)
    prev = None
    for s in segs:
        if s["missing"]:
            if prev is not None and s["n"] >= 2:
                # 地面突然看不到：邊緣在前一個平台的最遠處
                transitions.append(("gone", prev["f1"], None, s["row"]))
                break
            continue
        if prev is None and s["h"] > cfg.STEP_MAX_M:
            break  # 第一個平台就高出地面很多：是近處物體的頂面，不是地面（交給前方障礙物偵測）
        if prev is not None:
            delta = s["h"] - prev["h"]
            gap = (s["f0"] - prev["f1"]) > cfg.FORWARD_GAP_M
            if delta <= -cfg.CLIFF_DROP_M:
                transitions.append(("cliff", prev["f1"], delta, s["row"]))
                break
            if -cfg.STEP_MAX_M <= delta <= -cfg.STEP_MIN_M:
                transitions.append(("down", prev["f1"], delta, s["row"]))
            elif -cfg.CLIFF_DROP_M < delta < -cfg.STEP_MAX_M:
                # 從上往下看樓梯時，第一階常被邊緣擋住，兩階的高度差會合併成一次
                transitions.append(("down", prev["f1"], delta, s["row"]))
                transitions.append(("down", prev["f1"], 0.0, s["row"]))
            elif cfg.STEP_MIN_M <= delta <= cfg.STEP_MAX_M:
                transitions.append(("up", s["f0"], delta, s["row"]))
            elif delta > cfg.STEP_MAX_M:
                break  # 牆面或物體擋住，後方不再分析（交給前方障礙物偵測）
            elif gap and delta < 0:
                transitions.append(("down", prev["f1"], delta, s["row"]))
        prev = s

    if not transitions:
        return result

    first = transitions[0]
    kind, dist, delta, row = first
    if kind in ("cliff", "gone"):
        hazard = "cliff"
        change = delta
        steps = 0
    else:
        same = 0
        for t in transitions:
            if t[0] == kind:
                same += 1
            elif t[0] in ("cliff", "gone"):
                same = max(same, cfg.STAIRS_MIN_STEPS)  # 階梯後接大落差也視為向下
                break
            else:
                break
        steps = same
        total = sum(t[2] for t in transitions[:same] if t[2] is not None)
        change = total
        if kind == "down":
            hazard = "stairs_down" if same >= cfg.STAIRS_MIN_STEPS else "step_down"
        else:
            hazard = "stairs_up" if same >= cfg.STAIRS_MIN_STEPS else "step_up"

    result.update({
        "level": _level_for(hazard, dist),
        "type": hazard,
        "distance": round(float(dist), 2),
        "height_change_m": None if change is None else round(float(change), 2),
        "steps": steps,
        "edge_row": row,
    })
    return result
