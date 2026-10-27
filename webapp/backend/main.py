# -*- coding: utf-8 -*-
"""
main.py — 指標牌辨識操作介面 後端 API（FastAPI）
============================================================
提供給 Vue 前端的 REST API：
  類別管理 / 影像上傳 / 標註存取 / 訓練 / 推論

啟動：
  cd webapp/backend
  uvicorn main:app --reload --port 8000
"""

import base64
import json
import shutil
import threading
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

import config
from db import (
    SessionLocal, init_db, ClassLabel, ImageRecord, Annotation, TrainingRun,
)

app = FastAPI(title="Sign Detector 操作介面 API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 啟動初始化
# ============================================================
@app.on_event("startup")
def _startup():
    init_db()
    _seed_default_classes()
    _regenerate_data_yaml()


def _seed_default_classes():
    """若尚無類別，種入幾個常見箭頭方向作為起點（之後可自行新增）。"""
    with SessionLocal() as db:
        if db.query(ClassLabel).count() > 0:
            return
        defaults = [
            ("arrow_left", "往左"),
            ("arrow_right", "往右"),
            ("arrow_forward", "往前"),
        ]
        for i, (name, disp) in enumerate(defaults):
            db.add(ClassLabel(class_index=i, name=name, display_name=disp))
        db.commit()


# ============================================================
# 工具函式
# ============================================================
def _regenerate_data_yaml():
    """依 DB 類別重新產生 sign-detection/data.yaml。"""
    with SessionLocal() as db:
        classes = db.query(ClassLabel).order_by(ClassLabel.class_index).all()
        names_lines = "\n".join(f"  {c.class_index}: {c.name}" for c in classes)
        nc = len(classes)

    # 若 valid 無影像，val 就指回 train（小資料集示範用）
    has_valid = any(config.VALID_LABELS_DIR.glob("*.txt"))
    val_path = "images/valid" if has_valid else "images/train"

    content = (
        "# 本檔由後端依資料庫類別自動產生，請勿手動編輯\n"
        f"path: {config.DATASET_DIR.as_posix()}\n"
        "train: images/train\n"
        f"val: {val_path}\n"
        "test: images/test\n"
        "names:\n"
        f"{names_lines}\n"
        f"nc: {nc}\n"
    )
    config.DATA_YAML.write_text(content, encoding="utf-8")


def _write_label_file(image: ImageRecord, boxes):
    """把標註寫成 YOLO 格式 .txt。"""
    stem = Path(image.filename).stem
    label_path = config.LABELS_DIR / f"{stem}.txt"
    lines = [
        f"{b['class_index']} {b['cx']:.6f} {b['cy']:.6f} {b['w']:.6f} {b['h']:.6f}"
        for b in boxes
    ]
    label_path.write_text("\n".join(lines), encoding="utf-8")


def _class_map():
    with SessionLocal() as db:
        return {
            c.class_index: (c.display_name or c.name)
            for c in db.query(ClassLabel).all()
        }


def _model_class_meta(model):
    """
    用「模型訓練當下的類別名」建立 index → {name, display, kind}。
    以名稱(而非編號)去資料庫查中文與 kind，這樣即使資料庫之後重新編號、
    刪類別，辨識結果仍對得上模型實際學到的類別（避免 text 被叫成 line 這種錯位）。
    """
    with SessionLocal() as db:
        by_name = {
            c.name: ((c.display_name or c.name),
                     (getattr(c, "kind", "arrow") or "arrow"))
            for c in db.query(ClassLabel).all()
        }
    meta = {}
    for idx, nm in getattr(model, "names", {}).items():
        disp, kind = by_name.get(nm, (nm, "arrow"))
        meta[int(idx)] = {"name": nm, "display": disp, "kind": kind}
    return meta


# ============================================================
# 類別管理
# ============================================================
@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/classes")
def list_classes():
    from sqlalchemy import func
    with SessionLocal() as db:
        classes = db.query(ClassLabel).order_by(ClassLabel.class_index).all()
        # 每個類別已標註的框數
        counts = dict(
            db.query(Annotation.class_index, func.count(Annotation.id))
            .group_by(Annotation.class_index).all()
        )
        return [
            {"class_index": c.class_index, "name": c.name,
             "display_name": c.display_name,
             "kind": getattr(c, "kind", "arrow") or "arrow",
             "num_boxes": int(counts.get(c.class_index, 0))}
            for c in classes
        ]


@app.post("/api/classes")
def add_class(payload: dict = Body(...)):
    """新增類別。payload: {name, display_name, kind}；kind: arrow(方向) / icon(圖標)"""
    name = (payload.get("name") or "").strip()
    display_name = (payload.get("display_name") or "").strip()
    kind = (payload.get("kind") or "arrow").strip()
    if kind not in ("arrow", "icon", "text"):
        kind = "arrow"
    if not name:
        raise HTTPException(400, "name 不可為空")
    with SessionLocal() as db:
        if db.query(ClassLabel).filter_by(name=name).first():
            raise HTTPException(409, f"類別 '{name}' 已存在")
        next_index = (db.query(ClassLabel).count())  # 0-based 連續遞增
        c = ClassLabel(class_index=next_index, name=name,
                       display_name=display_name, kind=kind)
        db.add(c)
        db.commit()
        result = {"class_index": c.class_index, "name": c.name,
                  "display_name": c.display_name, "kind": c.kind}
    _regenerate_data_yaml()
    return result


# ============================================================
# 影像上傳 / 列表 / 檔案
# ============================================================
@app.post("/api/images")
async def upload_image(file: UploadFile = File(...)):
    raw = await file.read()
    arr = np.frombuffer(raw, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "無法解析影像")
    h, w = img.shape[:2]

    # 避免檔名衝突，加時間戳
    stem = Path(file.filename).stem
    suffix = Path(file.filename).suffix.lower() or ".jpg"
    ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
    safe_name = f"{stem}_{ts}{suffix}"
    (config.IMAGES_DIR / safe_name).write_bytes(raw)

    with SessionLocal() as db:
        rec = ImageRecord(filename=safe_name, split="train", width=w, height=h)
        db.add(rec)
        db.commit()
        return {"id": rec.id, "filename": rec.filename, "width": w, "height": h}


@app.get("/api/images")
def list_images():
    with SessionLocal() as db:
        # 上次「成功完成」的訓練時間，用來判斷哪些照片是之後才上傳的（新的）
        last_run = (
            db.query(TrainingRun)
            .filter(TrainingRun.status == "done",
                    TrainingRun.finished_at.isnot(None))
            .order_by(TrainingRun.finished_at.desc())
            .first()
        )
        last_train_at = last_run.finished_at if last_run else None

        images = db.query(ImageRecord).order_by(ImageRecord.id.desc()).all()
        return [
            {
                "id": im.id, "filename": im.filename,
                "width": im.width, "height": im.height,
                "num_boxes": len(im.annotations),
                # 上次訓練後才上傳 = 尚未被訓練過的新照片
                "is_new": bool(last_train_at is not None
                               and im.uploaded_at is not None
                               and im.uploaded_at > last_train_at),
            }
            for im in images
        ]


@app.get("/api/images/{image_id}/file")
def get_image_file(image_id: int):
    with SessionLocal() as db:
        im = db.get(ImageRecord, image_id)
        if not im:
            raise HTTPException(404, "找不到影像")
        path = config.IMAGES_DIR / im.filename
    if not path.exists():
        raise HTTPException(404, "影像檔遺失")
    return FileResponse(path)


@app.delete("/api/images/{image_id}")
def delete_image(image_id: int):
    with SessionLocal() as db:
        im = db.get(ImageRecord, image_id)
        if not im:
            raise HTTPException(404, "找不到影像")
        stem = Path(im.filename).stem
        (config.IMAGES_DIR / im.filename).unlink(missing_ok=True)
        (config.LABELS_DIR / f"{stem}.txt").unlink(missing_ok=True)
        db.delete(im)
        db.commit()
    return {"ok": True}


# ============================================================
# 標註存取
# ============================================================
@app.get("/api/images/{image_id}/annotations")
def get_annotations(image_id: int):
    with SessionLocal() as db:
        im = db.get(ImageRecord, image_id)
        if not im:
            raise HTTPException(404, "找不到影像")
        return [
            {"class_index": a.class_index, "cx": a.cx, "cy": a.cy,
             "w": a.w, "h": a.h}
            for a in im.annotations
        ]


@app.put("/api/images/{image_id}/annotations")
def save_annotations(image_id: int, payload: dict = Body(...)):
    """整批覆寫某影像的標註。payload: {boxes:[{class_index,cx,cy,w,h}]}"""
    boxes = payload.get("boxes", [])
    with SessionLocal() as db:
        im = db.get(ImageRecord, image_id)
        if not im:
            raise HTTPException(404, "找不到影像")
        # 清掉舊標註再寫新
        for a in list(im.annotations):
            db.delete(a)
        for b in boxes:
            db.add(Annotation(
                image_id=im.id, class_index=int(b["class_index"]),
                cx=float(b["cx"]), cy=float(b["cy"]),
                w=float(b["w"]), h=float(b["h"]),
            ))
        db.commit()
        _write_label_file(im, boxes)
    return {"ok": True, "num_boxes": len(boxes)}


# ============================================================
# 訓練（背景執行緒）
# ============================================================
train_state = {
    "running": False, "run_id": None, "logs": [], "error": None,
    "progress": {"epoch": 0, "epochs": 0},
    "metrics": {"map50": 0.0, "map": 0.0, "precision": 0.0, "recall": 0.0},
}
_train_lock = threading.Lock()


def _log(msg: str):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    train_state["logs"].append(line)
    train_state["logs"] = train_state["logs"][-300:]  # 僅保留最近 300 行
    print(line)


def _safe_copy(src, dst, attempts: int = 5, delay: float = 1.0):
    """複製檔案，遇到暫時性鎖檔（OneDrive/防毒）時重試。"""
    import time
    for i in range(attempts):
        try:
            shutil.copy(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                _log(f"複製 {Path(dst).name} 失敗（檔案被鎖），已略過。")
                return
            time.sleep(delay)


def _prune_old_models(keep: int):
    """只保留最近 keep 個 runN_best.pt，更舊的自動刪除。keep<=0 則不清理。"""
    if keep <= 0:
        return
    models = sorted(
        config.MODELS_DIR.glob("run*_best.pt"),
        key=lambda p: p.stat().st_mtime,      # 依修改時間，新的在後
        reverse=True,
    )
    for old in models[keep:]:                  # 超過保留數的舊模型
        try:
            old.unlink()
            _log(f"自動清除舊模型：{old.name}")
        except OSError:
            pass


def _run_training(params: dict, run_id: int):
    try:
        from ultralytics import YOLO
    except ImportError:
        train_state["error"] = "未安裝 ultralytics，請 pip install ultralytics"
        train_state["running"] = False
        _log(train_state["error"])
        _finish_run(run_id, "error")
        return

    try:
        _regenerate_data_yaml()
        model_size = params.get("model", "n")
        # finetune=True 且已有上一個模型 → 從 best.pt 接著練（暖啟動，保留先前學習）；
        # 否則從官方預訓練權重開始（從零學起）。
        prev_best = config.SIGN_DIR / "best.pt"
        if params.get("finetune") and prev_best.exists():
            weights = str(prev_best)
            _log(f"從上一個模型繼續訓練（fine-tune）：{weights}")
        else:
            weights = f"yolov8{model_size}.pt"
            _log(f"從預訓練權重開始（全新訓練）：{weights}")
        model = YOLO(weights)

        epochs = int(params.get("epochs", 50))
        train_state["progress"]["epochs"] = epochs

        # 每個 epoch 結束更新進度與指標
        def on_fit_epoch_end(trainer):
            train_state["progress"]["epoch"] = int(trainer.epoch) + 1
            m = getattr(trainer, "metrics", {}) or {}
            train_state["metrics"] = {
                "map50": float(m.get("metrics/mAP50(B)", 0.0)),
                "map": float(m.get("metrics/mAP50-95(B)", 0.0)),
                "precision": float(m.get("metrics/precision(B)", 0.0)),
                "recall": float(m.get("metrics/recall(B)", 0.0)),
            }
            _log(f"epoch {train_state['progress']['epoch']}/{epochs} "
                 f"mAP50={train_state['metrics']['map50']:.3f}")

        model.add_callback("on_fit_epoch_end", on_fit_epoch_end)

        _log("開始訓練 ...")
        results = model.train(
            data=str(config.DATA_YAML),
            epochs=epochs,
            batch=int(params.get("batch", 16)),
            imgsz=int(params.get("imgsz", 960)),
            device=params.get("device") or None,
            project=str(config.RUNS_DIR),
            name=params.get("name", "ui_run"),
            exist_ok=True,
            plots=True,
            # 關鍵：關閉水平翻轉增強。方向箭頭左右翻轉後語意會相反，
            # 但標籤不變，會把模型教成左右不分。方向類任務務必設 0。
            fliplr=0.0,
        )
        save_dir = Path(results.save_dir)
        best = save_dir / "weights" / "best.pt"

        # 複製 best.pt 到 models/ 與 sign-detection/best.pt
        # 目的地在 OneDrive 內時，偶爾會被同步鎖檔，故用重試包一層。
        weights_path = ""
        if best.exists():
            dst = config.MODELS_DIR / f"run{run_id}_best.pt"
            _safe_copy(best, dst)
            _safe_copy(best, config.SIGN_DIR / "best.pt")
            weights_path = str(dst)
            _log(f"最佳權重已存：{dst}")
            # 只保留最近 N 次，更舊的自動清除
            _prune_old_models(config.KEEP_MODELS)

        _finish_run(run_id, "done", weights_path)
        _log("訓練完成。")
    except Exception as e:  # noqa: BLE001
        train_state["error"] = str(e)
        _log(f"訓練失敗：{e}")
        _finish_run(run_id, "error")
    finally:
        train_state["running"] = False


def _finish_run(run_id: int, status: str, weights_path: str = ""):
    with SessionLocal() as db:
        run = db.get(TrainingRun, run_id)
        if run:
            run.status = status
            run.finished_at = datetime.utcnow()
            m = train_state["metrics"]
            run.map50, run.map = m["map50"], m["map"]
            run.precision, run.recall = m["precision"], m["recall"]
            run.weights_path = weights_path
            db.commit()


@app.post("/api/train")
def start_training(params: dict = Body(default={})):
    with _train_lock:
        if train_state["running"]:
            raise HTTPException(409, "已有訓練進行中")

        # 需至少有一張已標註影像
        with SessionLocal() as db:
            n_boxes = db.query(Annotation).count()
        if n_boxes == 0:
            raise HTTPException(400, "尚無任何標註，請先在標註頁框出箭頭並儲存")

        # 建立訓練紀錄
        with SessionLocal() as db:
            run = TrainingRun(
                name=params.get("name", "ui_run"),
                status="running",
                params=json.dumps(params, ensure_ascii=False),
            )
            db.add(run)
            db.commit()
            run_id = run.id

        train_state.update({
            "running": True, "run_id": run_id, "logs": [], "error": None,
            "progress": {"epoch": 0, "epochs": int(params.get("epochs", 50))},
            "metrics": {"map50": 0.0, "map": 0.0, "precision": 0.0, "recall": 0.0},
        })
        threading.Thread(target=_run_training, args=(params, run_id),
                         daemon=True).start()
    return {"ok": True, "run_id": run_id}


@app.get("/api/train/status")
def training_status():
    return train_state


@app.get("/api/models")
def list_models():
    models = sorted(config.MODELS_DIR.glob("*.pt"))
    root_best = config.SIGN_DIR / "best.pt"
    if root_best.exists():
        models.insert(0, root_best)
    return [{"name": m.name, "path": str(m),
             "size_mb": round(m.stat().st_size / 1024 / 1024, 2)}
            for m in models]


# ============================================================
# 推論（單張影像 / 攝影機影格皆走這支）
# ============================================================
def _load_infer_model(model_path: str | None):
    from ultralytics import YOLO
    if model_path and Path(model_path).exists():
        return YOLO(model_path)
    # 預設用最新 best.pt
    best = config.SIGN_DIR / "best.pt"
    if not best.exists():
        raise HTTPException(400, "尚無訓練好的模型，請先完成訓練")
    return YOLO(str(best))


# 簡單快取，避免每次請求都重載模型
_infer_cache = {"path": None, "model": None}


@app.post("/api/infer")
def infer(payload: dict = Body(...)):
    """
    payload: {
      image_base64: "data:image/jpeg;base64,....",  # 或純 base64
      conf: 0.4, iou: 0.45, model_path: 可選
    }
    回傳偵測框（像素座標）、類別、信心值、標註後影像(base64)、延遲(ms)
    """
    import time

    b64 = payload.get("image_base64", "")
    if "," in b64:
        b64 = b64.split(",", 1)[1]
    try:
        raw = base64.b64decode(b64)
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "image_base64 無法解碼")
    arr = np.frombuffer(raw, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "無法解析影像")

    conf = float(payload.get("conf", 0.4))
    iou = float(payload.get("iou", 0.45))
    imgsz = int(payload.get("imgsz", 960))   # 與訓練一致，利於小箭頭偵測
    model_path = payload.get("model_path")

    # 載入（含快取）
    try:
        if _infer_cache["path"] != model_path or _infer_cache["model"] is None:
            _infer_cache["model"] = _load_infer_model(model_path)
            _infer_cache["path"] = model_path
        model = _infer_cache["model"]
    except HTTPException:
        raise
    except ImportError:
        raise HTTPException(500, "未安裝 ultralytics")

    # 用模型自己的類別名（以名稱查中文），避免資料庫重編號造成錯位
    meta = _model_class_meta(model)
    class_map = {i: m["display"] for i, m in meta.items()}

    t0 = time.perf_counter()
    results = model.predict(source=img, conf=conf, iou=iou, imgsz=imgsz, verbose=False)
    latency_ms = (time.perf_counter() - t0) * 1000
    result = results[0]

    detections = []
    for b in result.boxes:
        cls_id = int(b.cls.item())
        x1, y1, x2, y2 = [round(v, 1) for v in b.xyxy[0].tolist()]
        detections.append({
            "class_index": cls_id,
            "class_name": class_map.get(cls_id, str(cls_id)),
            "conf": round(float(b.conf.item()), 3),
            "box": [x1, y1, x2, y2],
        })

    # 產生標註後影像回傳（前端可直接顯示）
    annotated = result.plot()
    ok, buf = cv2.imencode(".jpg", annotated)
    annotated_b64 = base64.b64encode(buf).decode() if ok else ""

    return {
        "detections": detections,
        "latency_ms": round(latency_ms, 1),
        "realtime_ok": latency_ms < 200,
        "annotated_base64": f"data:image/jpeg;base64,{annotated_b64}",
    }


# ============================================================
# 文字辨識 OCR（EasyOCR，繁中 + 英文）
# ============================================================
# 說明：
#   * 用現成預訓練模型讀出圖片中的文字，不需自行訓練。
#   * Reader 載入很慢（且首次會自動下載中文模型檔），故全域快取只建一次。
#   * 標註影像只畫「框 + 編號」（OpenCV 無法顯示中文），實際文字在回傳清單，
#     前端以 HTML 呈現即可正確顯示繁中。
#   * 之後接「YOLO 先框指標牌 → 裁切 → 這支 OCR」時，可直接重用本端點。
_ocr_cache = {"reader": None, "engine": "none"}


def _ensure_ocr_reader():
    """
    取得（必要時建立並快取）OCR 引擎。
    優先用 RapidOCR（PP-OCR 模型，中文招牌較強、不需 paddle）；
    沒裝則退回 EasyOCR。_ocr_cache['engine'] 記錄目前用哪個。
    """
    if _ocr_cache["reader"] is not None:
        return _ocr_cache["reader"]
    try:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_cache["reader"] = RapidOCR()
        _ocr_cache["engine"] = "rapid"
        print("[OCR] 使用 RapidOCR")
        return _ocr_cache["reader"]
    except Exception as e:  # noqa: BLE001
        print(f"[OCR] RapidOCR 不可用（{e}），改用 EasyOCR")
    try:
        import easyocr
    except ImportError:
        raise HTTPException(500, "未安裝 OCR 引擎（rapidocr-onnxruntime 或 easyocr）")
    try:
        import torch
        use_gpu = torch.cuda.is_available()
    except Exception:  # noqa: BLE001
        use_gpu = False
    _ocr_cache["reader"] = easyocr.Reader(["ch_tra", "en"], gpu=use_gpu)
    _ocr_cache["engine"] = "easy"
    return _ocr_cache["reader"]


def _ocr_readtext(img, max_size: int = 1280, min_size: int = 10):
    """
    OCR：先把大圖縮到最長邊 max_size 再辨識（加速），座標再換回原圖。
    回傳 [(bbox四點, text, conf), ...]。引擎自動選 RapidOCR 或 EasyOCR。
    """
    reader = _ensure_ocr_reader()
    h, w = img.shape[:2]
    scale = min(1.0, max_size / float(max(h, w)))
    if scale < 1.0:
        small = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))),
                           interpolation=cv2.INTER_AREA)
    else:
        small = img
    inv = 1.0 / scale
    out = []
    if _ocr_cache["engine"] == "rapid":
        res, _ = reader(small)                       # [[box, text, score], ...] 或 None
        for box, text, score in (res or []):
            out.append(([[p[0] * inv, p[1] * inv] for p in box],
                        str(text), float(score)))
    else:
        results = reader.readtext(small, canvas_size=max_size, mag_ratio=1.0,
                                 min_size=min_size)
        for bbox, text, c in results:
            out.append(([[p[0] * inv, p[1] * inv] for p in bbox], text, c))
    return out


def _ocr_recognize(crop):
    """
    只做『辨識』、跳過『找字框』——用於 YOLO 已框好的文字區塊，比 readtext 快約 8 倍
    （~3000ms → ~400ms）。整塊當一行字辨識。回傳 [(text, conf), ...]。
    """
    if crop is None or crop.size == 0:
        return []
    reader = _ensure_ocr_reader()
    h, w = crop.shape[:2]
    if h < 2 or w < 2:
        return []
    try:
        res = reader.recognize(crop, horizontal_list=[[0, w, 0, h]], free_list=[])
    except Exception:  # noqa: BLE001
        return []
    return [(str(t), float(c)) for _b, t, c in res]


def _has_cjk(s: str) -> bool:
    """字串是否含中日韓漢字（用來判斷是不是中文文字）。"""
    return any(
        "一" <= ch <= "鿿" or "㐀" <= ch <= "䶿"
        for ch in s
    )


def _region_color(img, box):
    """
    取區域的「代表色」(BGR)：只看有彩度的像素(忽略白/黑/灰背景)，
    取其平均，用來判斷文字或箭頭的顏色。純黑白區域回傳 None。
    """
    h, w = img.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in box]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return None
    region = img[y1:y2, x1:x2]
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    mask = (hsv[:, :, 1] > 50) & (hsv[:, :, 2] > 50)     # 有彩度且不太暗
    if int(mask.sum()) < max(20, int(region.size / 3 * 0.02)):
        return None                                       # 幾乎沒有彩色像素(黑白牌)
    return region[mask].mean(axis=0)                      # 彩色像素的 BGR 平均


def _decode_image(payload: dict):
    """從 payload.image_base64 解出 OpenCV BGR 影像。"""
    b64 = payload.get("image_base64", "")
    if "," in b64:
        b64 = b64.split(",", 1)[1]
    try:
        raw = base64.b64decode(b64)
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "image_base64 無法解碼")
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "無法解析影像")
    return img


@app.post("/api/ocr")
def ocr(payload: dict = Body(...)):
    """
    文字辨識。payload: { image_base64, min_conf: 0.3 }
    回傳每段文字的內容、位置、信心值，及標註框後的影像與延遲。
    """
    import time

    img = _decode_image(payload)
    min_conf = float(payload.get("min_conf", 0.3))

    t0 = time.perf_counter()
    raw_results = _ocr_readtext(img)  # 縮圖加速；座標已換回原圖
    latency_ms = (time.perf_counter() - t0) * 1000

    texts = []
    annotated = img.copy()
    idx = 0
    for bbox, text, conf in raw_results:
        if conf < min_conf:
            continue
        idx += 1
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        # EasyOCR 回傳的是 numpy 型別，需轉成 Python float 才能被 JSON 序列化
        x1, y1 = float(min(xs)), float(min(ys))
        x2, y2 = float(max(xs)), float(max(ys))
        texts.append({
            "index": idx,
            "text": str(text),
            "conf": round(float(conf), 3),
            "box": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
        })
        # 畫框 + 編號（中文不在圖上畫，交給前端清單顯示）
        cv2.rectangle(annotated, (int(x1), int(y1)), (int(x2), int(y2)),
                      (0, 200, 255), 2)
        cv2.putText(annotated, str(idx), (int(x1), max(int(y1) - 6, 12)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)

    ok, buf = cv2.imencode(".jpg", annotated)
    annotated_b64 = base64.b64encode(buf).decode() if ok else ""

    # 把所有文字串起來，方便直接看整段內容
    joined = " ".join(t["text"] for t in texts)

    return {
        "texts": texts,
        "joined": joined,
        "latency_ms": round(latency_ms, 1),
        "annotated_base64": f"data:image/jpeg;base64,{annotated_b64}",
    }


# ============================================================
# 導航提示：YOLO 箭頭 + OCR 文字 → 「往X是OO的方向」
# ============================================================
# 作法：同一張圖同時跑箭頭偵測與文字辨識，再把每個箭頭配對「距離最近」的
#       文字，組成人類看得懂的方向指引句。這是本專案的最終核心：
#       把「方向」與「目的地文字」融合成導航語句。
@app.post("/api/guide")
def guide(payload: dict = Body(...)):
    """
    payload: { image_base64, conf, iou, model_path }
    回傳：guides（方向指引句）、arrows、texts；YOLO 若無模型會在 yolo_error 說明。
    """
    img = _decode_image(payload)
    conf = float(payload.get("conf", 0.4))
    iou = float(payload.get("iou", 0.45))
    imgsz = int(payload.get("imgsz", 960))
    # ocr_crop=True：只 OCR 每個箭頭旁的一小塊（快很多，適合連續/即時導航）
    ocr_crop = bool(payload.get("ocr_crop", False))
    model_path = payload.get("model_path")

    # --- 1. 箭頭偵測（YOLO）---
    arrows = []
    yolo_error = None
    try:
        if _infer_cache["path"] != model_path or _infer_cache["model"] is None:
            _infer_cache["model"] = _load_infer_model(model_path)
            _infer_cache["path"] = model_path
        model = _infer_cache["model"]
        class_map = {i: m["display"] for i, m in _model_class_meta(model).items()}
        result = model.predict(source=img, conf=conf, iou=iou, imgsz=imgsz, verbose=False)[0]
        for b in result.boxes:
            cls_id = int(b.cls.item())
            x1, y1, x2, y2 = [float(v) for v in b.xyxy[0].tolist()]
            arrows.append({
                "class_index": cls_id,
                "direction": class_map.get(cls_id, str(cls_id)),
                "conf": round(float(b.conf.item()), 3),
                "cx": (x1 + x2) / 2, "cy": (y1 + y2) / 2,
                "box": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
            })
    except HTTPException as e:
        yolo_error = e.detail          # 例如尚未訓練模型
    except Exception as e:  # noqa: BLE001
        yolo_error = str(e)

    # --- 2 + 3. 文字辨識 + 配對 ---
    reader = _ensure_ocr_reader()
    texts = []
    guides = []

    if ocr_crop and arrows:
        # 快速模式：只 OCR 每個箭頭周圍的裁切區，直接取區內信心最高的中文
        H, W = img.shape[:2]
        for a in arrows:
            x1, y1, x2, y2 = a["box"]
            bw, bh = x2 - x1, y2 - y1
            # 指標牌文字通常在箭頭旁邊，往四周擴張（水平多一些）
            mx, my = bw * 1.8, bh * 1.2
            cx1, cy1 = max(0, int(x1 - mx)), max(0, int(y1 - my))
            cx2, cy2 = min(W, int(x2 + mx)), min(H, int(y2 + my))
            crop = img[cy1:cy2, cx1:cx2]
            best_text, best_conf = None, 0.0
            if crop.size:
                for bbox, text, c in reader.readtext(crop):
                    if c < 0.3 or not _has_cjk(str(text)):
                        continue
                    xs = [p[0] + cx1 for p in bbox]
                    ys = [p[1] + cy1 for p in bbox]
                    texts.append({"text": str(text), "conf": round(float(c), 3),
                                  "cx": float(sum(xs) / len(xs)),
                                  "cy": float(sum(ys) / len(ys))})
                    if c > best_conf:
                        best_text, best_conf = str(text), float(c)
            if best_text:
                guides.append({"direction": a["direction"], "text": best_text,
                               "sentence": f'{a["direction"]}是{best_text}的方向',
                               "conf": a["conf"]})
            else:
                guides.append({"direction": a["direction"], "text": None,
                               "sentence": f'{a["direction"]}（附近沒有中文文字）',
                               "conf": a["conf"]})
    else:
        # 完整模式：整張圖 OCR（縮圖加速），再用「最近距離」配對
        for bbox, text, c in _ocr_readtext(img):
            if c < 0.3:
                continue
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            texts.append({"text": str(text), "conf": round(float(c), 3),
                          "cx": float(sum(xs) / len(xs)),
                          "cy": float(sum(ys) / len(ys))})
        pool = [t for t in texts if _has_cjk(t["text"])]
        for a in arrows:
            nearest, best = None, float("inf")
            for t in pool:
                d = (a["cx"] - t["cx"]) ** 2 + (a["cy"] - t["cy"]) ** 2
                if d < best:
                    best, nearest = d, t
            if nearest:
                guides.append({"direction": a["direction"], "text": nearest["text"],
                               "sentence": f'{a["direction"]}是{nearest["text"]}的方向',
                               "conf": a["conf"]})
            else:
                guides.append({"direction": a["direction"], "text": None,
                               "sentence": f'{a["direction"]}（附近沒有中文文字）',
                               "conf": a["conf"]})

    return {
        "guides": guides,
        "arrows": [{"class_index": a["class_index"], "direction": a["direction"],
                    "conf": a["conf"], "box": a["box"]}
                   for a in arrows],
        "texts": [{"text": t["text"], "conf": t["conf"]} for t in texts],
        "yolo_error": yolo_error,
    }


# ============================================================
# 目標導向導航：輸入路線/目標 → 找到文字 → 找旁邊的箭頭 → 報方向
# ============================================================
def _text_match_score(target: str, text: str) -> int:
    """回傳目標與 OCR 文字的相符程度（0=不符；越大越相符）。"""
    t = target.replace(" ", "")
    x = text.replace(" ", "")
    if not t or not x:
        return 0
    if t in x or x in t:                       # 一方包含另一方 → 很強
        return min(len(t), len(x)) + 100
    best = 0                                    # 否則取最長共同子字串（>=2 才算）
    for i in range(len(t)):
        for j in range(i + 2, len(t) + 1):
            if t[i:j] in x and (j - i) > best:
                best = j - i
    return best


@app.post("/api/navigate")
def navigate(payload: dict = Body(...)):
    """
    目標導向導航。payload: { image_base64, target, conf, iou, imgsz, model_path }
    先在畫面找到 target 文字，再找它附近、大小相近的箭頭，輸出「target 在 X 的方向」。
    """
    img = _decode_image(payload)
    target = (payload.get("target") or "").strip()
    if not target:
        raise HTTPException(400, "請先輸入要前往的路線或目標（如：淡水信義線、出口、台鐵）")
    conf = float(payload.get("conf", 0.4))
    iou = float(payload.get("iou", 0.45))
    # 導航求快：箭頭偵測解析度預設降到 768（比 960 快約 1.5x），OCR 縮到 1024 並跳過小字
    imgsz = int(payload.get("imgsz", 768))
    ocr_size = int(payload.get("ocr_size", 1024))
    ocr_min = int(payload.get("ocr_min", 20))
    # no_full_ocr=True：跳過「整張 OCR 保底」（連續/即時導航用，避免卡在慢 OCR）
    no_full_ocr = bool(payload.get("no_full_ocr", False))
    model_path = payload.get("model_path")

    # --- 1. 一次 YOLO 偵測，分成 箭頭 / 圖標 / 文字區塊 ---
    # 類別對照以「模型訓練當下的名稱」為準（用名稱查 kind/中文），
    # 這樣資料庫重編號後也不會錯位。
    kind_map, disp_map, name_map = {}, {}, {}
    arrows, icons, text_blocks, yolo_error = [], [], [], None
    try:
        if _infer_cache["path"] != model_path or _infer_cache["model"] is None:
            _infer_cache["model"] = _load_infer_model(model_path)
            _infer_cache["path"] = model_path
        model = _infer_cache["model"]
        meta = _model_class_meta(model)
        kind_map = {i: m["kind"] for i, m in meta.items()}
        disp_map = {i: m["display"] for i, m in meta.items()}
        name_map = {i: m["name"] for i, m in meta.items()}
        result = model.predict(source=img, conf=conf, iou=iou, imgsz=imgsz, verbose=False)[0]
        for b in result.boxes:
            cls_id = int(b.cls.item())
            x1, y1, x2, y2 = [float(v) for v in b.xyxy[0].tolist()]
            entry = {
                "class_index": cls_id,
                "direction": disp_map.get(cls_id, str(cls_id)),
                "conf": round(float(b.conf.item()), 3),
                "cx": (x1 + x2) / 2, "cy": (y1 + y2) / 2, "h": y2 - y1,
                "box": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
            }
            k = kind_map.get(cls_id, "arrow")
            if k == "icon":
                icons.append(entry)
            elif k == "text":
                text_blocks.append(entry)
            else:
                arrows.append(entry)
    except HTTPException as e:
        yolo_error = e.detail
    except Exception as e:  # noqa: BLE001
        yolo_error = str(e)

    # --- 2. 依規則決定目標位置 ---
    #   規則：
    #   (1) 台鐵/高鐵/廁所/樓梯/出口/電梯… → 直接用偵測到的「圖標」(免OCR)。
    #   (2) 桃園機場捷運/xxx線 → OCR「文字指引(kind=text)」區塊；有「捷運線 marker」
    #       時，偏好靠近該 marker 的文字指引。
    #   (3) 都找不到 → 退回整張 OCR。
    H, W = img.shape[:2]
    matched, matched_text, via, is_icon, texts = None, None, None, False, []

    def _is_line_marker(ci):
        nm = (name_map.get(ci, "") or "").lower()
        dp = disp_map.get(ci, "") or ""
        return ("line" in nm) or ("線" in dp) or ("路線" in dp)

    def _is_dir_marker(ci):
        nm = (name_map.get(ci, "") or "").lower()
        dp = disp_map.get(ci, "") or ""
        return ("direction" in nm) or ("方向" in dp and "線" not in dp)

    line_markers = [i for i in icons if _is_line_marker(i["class_index"])]
    dir_markers = [i for i in icons if _is_dir_marker(i["class_index"])]

    def _crop_ocr_best(box, want):
        """OCR 一個框內的文字，回傳與 want 最相符的（要在框內，即重疊）。"""
        x1, y1, x2, y2 = box
        mx, my = (x2 - x1) * 0.05, (y2 - y1) * 0.08
        cx1, cy1 = max(0, int(x1 - mx)), max(0, int(y1 - my))
        cx2, cy2 = min(W, int(x2 + mx)), min(H, int(y2 + my))
        crop = img[cy1:cy2, cx1:cx2]
        best_t, best_s = None, 0
        if crop.size:
            for _b, text, c in _ocr_readtext(crop, max_size=800, min_size=5):
                if c < 0.3 or not _has_cjk(str(text)):
                    continue
                texts.append(str(text))
                s = _text_match_score(want, str(text))
                if s > best_s:
                    best_s, best_t = s, str(text)
        return best_t, best_s

    # 2a. 「往X的方向」型 → 在「捷運方向」牌內找 X 的文字（文字要落在方向牌內＝重疊）
    dest = None
    t = target.strip()
    if t.startswith("往"):
        d = t[1:]
        for suf in ("的方向", "方向", "的"):
            if d.endswith(suf):
                d = d[: -len(suf)]
                break
        dest = d.strip() or None
    if dest and dir_markers:
        best_dm, best_s = None, 0
        for dm in dir_markers:
            _t, s = _crop_ocr_best(dm["box"], dest)
            if s > best_s:
                best_s, best_dm = s, dm
        if best_dm is not None:
            matched = best_dm
            matched_text, via = f"往{dest}", "direction"

    # 2b. 目標是不是某個「目的地圖標」？(捷運線 / 捷運方向 marker 不算)
    if matched is None:
        best_icon_cls, best_sc = None, 0
        for ci in [c for c in kind_map if kind_map[c] == "icon"
                   and not _is_line_marker(c) and not _is_dir_marker(c)]:
            sc = _text_match_score(target, disp_map.get(ci, ""))
            if sc > best_sc:
                best_sc, best_icon_cls = sc, ci
        if best_icon_cls is not None:
            cand = [i for i in icons if i["class_index"] == best_icon_cls]
            if cand:
                matched = max(cand, key=lambda i: i["conf"])
                matched_text, via, is_icon = disp_map.get(best_icon_cls), "icon", True

    # 2b. 不是圖標(或圖標沒偵測到) → OCR「文字指引」區塊
    is_line_target = ("線" in target) or ("捷運" in target)
    if matched is None:
        tcands = []
        for tb in text_blocks:
            x1, y1, x2, y2 = tb["box"]
            mx, my = (x2 - x1) * 0.06, (y2 - y1) * 0.12      # 稍微外擴避免切字
            cx1, cy1 = max(0, int(x1 - mx)), max(0, int(y1 - my))
            cx2, cy2 = min(W, int(x2 + mx)), min(H, int(y2 + my))
            crop = img[cy1:cy2, cx1:cx2]
            best_t, best_c = None, 0.0
            if crop.size:
                for _b, text, c in _ocr_readtext(crop, max_size=800, min_size=5):
                    if c < 0.3 or not _has_cjk(str(text)):
                        continue
                    texts.append(str(text))
                    if c > best_c:
                        best_c, best_t = float(c), str(text)
            if best_t:
                near_line = min(
                    (((tb["cx"] - m["cx"]) ** 2 + (tb["cy"] - m["cy"]) ** 2) ** 0.5
                     for m in line_markers), default=1e18)
                tcands.append({"text": best_t, "box": tb["box"], "cx": tb["cx"],
                               "cy": tb["cy"], "h": tb["h"], "near_line": near_line})
        scored = [(_text_match_score(target, t["text"]), t) for t in tcands]
        scored = [s for s in scored if s[0] > 0]
        if scored:
            if is_line_target and line_markers:      # 捷運線目標：偏好靠近 marker 的
                scored.sort(key=lambda s: (-s[0], s[1]["near_line"]))
            else:
                scored.sort(key=lambda s: -s[0])
            matched, matched_text, via = scored[0][1], scored[0][1]["text"], "text_block"

    # 2c. 後備：文字指引都沒配到 → 退回整張 OCR（連續模式關閉以求快）
    if matched is None and not no_full_ocr:
        whole = []
        for bbox, text, c in _ocr_readtext(img, max_size=ocr_size, min_size=ocr_min):
            if c < 0.3:
                continue
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
            whole.append({"text": str(text), "cx": (x1 + x2) / 2, "cy": (y1 + y2) / 2,
                          "h": y2 - y1,
                          "box": [round(float(x1), 1), round(float(y1), 1),
                                  round(float(x2), 1), round(float(y2), 1)]})
            texts.append(str(text))
        sc2 = [(_text_match_score(target, t["text"]), t) for t in whole]
        sc2 = [s for s in sc2 if s[0] > 0]
        if sc2:
            sc2.sort(key=lambda s: -s[0])
            matched, matched_text, via = sc2[0][1], sc2[0][1]["text"], "text"

    if matched is None:
        return {
            "found": False, "target": target, "direction": None, "sentence": None,
            "message": f"畫面中找不到「{target}」（文字或圖標），請對準指標牌再試一次。",
            "texts_read": texts, "yolo_error": yolo_error,
        }

    # --- 4. 找「最近、大小相近、顏色相符」的箭頭；位置-方向矛盾者兩段式排除 ---
    matched_box = matched["box"]
    text_color = _region_color(img, matched_box)         # 文字的代表色(可能 None)

    def _arrow_side(a):
        """箭頭『指向』的左右（用名稱判斷，較穩）：left / right / None(前/下/迴轉)。"""
        nm = (name_map.get(a["class_index"], "") or "").lower()
        if "left" in nm:
            return "left"
        if "right" in nm:
            return "right"
        return None

    def _contradicts(a):
        """箭頭在文字左邊卻指右、或在右邊卻指左 → 矛盾。"""
        side = _arrow_side(a)
        if side is None:
            return False
        on_left = a["cx"] < matched["cx"]                # 箭頭實際位置在文字左邊?
        return (on_left and side == "right") or ((not on_left) and side == "left")

    best_arrow = None
    if arrows:
        th = matched["h"] or 1.0

        def score(a):
            ah = a["h"] or 1.0
            ratio = max(ah / th, th / ah)                # 大小差異(>=1)
            dist = ((a["cx"] - matched["cx"]) ** 2 +
                    (a["cy"] - matched["cy"]) ** 2) ** 0.5
            s = dist * (1 + (ratio - 1) * 0.5)           # 近 + 大小相近者分數低
            if text_color is not None:                   # 顏色相符：同色優先、異色壓低
                ac = _region_color(img, a["box"])
                if ac is not None:
                    cdist = float(np.linalg.norm(text_color - ac))
                    if cdist < 60:
                        s *= 0.6
                    elif cdist > 150:
                        s *= 1.6
            return s

        # 兩段式：先只從「不矛盾」的箭頭裡選；真的一個都沒有，才退而用矛盾的。
        consistent = [a for a in arrows if not _contradicts(a)]
        pool = consistent if consistent else arrows
        best_arrow = min(pool, key=score)

    # --- 5. 組句 + 畫圖（框文字綠、框箭頭青、連線）---
    annotated = img.copy()
    mx1, my1, mx2, my2 = [int(v) for v in matched_box]
    cv2.rectangle(annotated, (mx1, my1), (mx2, my2), (0, 200, 0), 3)
    if best_arrow:
        ax1, ay1, ax2, ay2 = [int(v) for v in best_arrow["box"]]
        cv2.rectangle(annotated, (ax1, ay1), (ax2, ay2), (255, 200, 0), 3)
        cv2.line(annotated, (int(matched["cx"]), int(matched["cy"])),
                 (int(best_arrow["cx"]), int(best_arrow["cy"])), (0, 200, 255), 2)
    ok, buf = cv2.imencode(".jpg", annotated)
    annotated_b64 = base64.b64encode(buf).decode() if ok else ""

    if best_arrow:
        direction = best_arrow["direction"]
        sentence = f'{matched_text}在{direction}的方向'
    else:
        direction = None
        sentence = f'找到「{matched_text}」，但附近沒有偵測到箭頭'

    return {
        "found": True, "target": target,
        "matched_text": matched_text, "via": via,   # via: icon(圖標) / text(OCR文字)
        "direction": direction, "sentence": sentence,
        "matched_box": matched_box,
        "arrow_box": best_arrow["box"] if best_arrow else None,
        "annotated_base64": f"data:image/jpeg;base64,{annotated_b64}",
        "yolo_error": yolo_error,
    }
