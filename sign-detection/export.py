#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export.py — 將訓練完的 YOLO .pt 模型匯出為部署格式
============================================================
功能：
  1. 匯出 ONNX 格式（跨平台、可用 ONNX Runtime 推論）
  2. 匯出 TensorRT engine（INT8 量化，適合 NVIDIA 邊緣裝置）
     - 附上量化用 calibration dataset 讀取邏輯
  3. 匯出後印出各模型檔案大小
  4. 若環境許可，做簡易推論速度 benchmark（FPS）

使用範例：
  # 只匯出 ONNX
  python export.py --weights best.pt --format onnx --imgsz 960

  # 匯出 ONNX + TensorRT(INT8)，並指定校正資料夾
  python export.py --weights best.pt --format all --int8 \
                   --calib-dir dataset/images/valid --imgsz 960

備註：
  * TensorRT / INT8 量化需 NVIDIA GPU + CUDA + TensorRT 環境。
    若環境不支援，程式會捕捉例外並提示改用 ONNX Runtime 推論。
  * INT8 量化需一批具代表性的校正影像（calibration dataset）來
    統計每層數值分布，ultralytics 透過 data=<yaml> 自動取用校正資料。
"""

import argparse
from pathlib import Path

try:
    from ultralytics import YOLO
except ImportError:
    raise SystemExit(
        "[錯誤] 找不到 ultralytics，請先執行：pip install -r requirements.txt"
    )

BASE_DIR = Path(__file__).resolve().parent


def file_size_mb(path: Path) -> float:
    """回傳檔案大小（MB）"""
    return path.stat().st_size / (1024 * 1024) if path.exists() else 0.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="匯出 YOLO 模型為 ONNX / TensorRT",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--weights", type=str, default=str(BASE_DIR / "best.pt"),
        help="訓練完成的 .pt 權重路徑",
    )
    parser.add_argument(
        "--format", type=str, default="onnx", choices=["onnx", "engine", "all"],
        help="匯出格式：onnx / engine(TensorRT) / all(兩者皆匯出)",
    )
    parser.add_argument("--imgsz", type=int, default=960, help="匯出時的輸入影像尺寸（需與訓練一致較佳）")
    parser.add_argument(
        "--half", action="store_true",
        help="FP16 半精度匯出（TensorRT/GPU 適用，加速且省記憶體）",
    )
    parser.add_argument(
        "--int8", action="store_true",
        help="INT8 量化匯出（僅 TensorRT engine 有效，需校正資料）",
    )
    parser.add_argument(
        "--data", type=str, default=str(BASE_DIR / "data.yaml"),
        help="INT8 校正所用的 data.yaml（ultralytics 依此讀取校正影像）",
    )
    parser.add_argument(
        "--calib-dir", type=str, default=None,
        help="（可選）明確指定校正影像資料夾；未指定則用 data.yaml 的 val 切分",
    )
    parser.add_argument(
        "--device", type=str, default=None,
        help="運算裝置：'0' 用 GPU、'cpu' 用 CPU；TensorRT 匯出必須用 GPU",
    )
    parser.add_argument(
        "--no-benchmark", action="store_true",
        help="關閉匯出後的推論速度 benchmark",
    )
    return parser.parse_args()


def export_onnx(model: YOLO, args) -> Path | None:
    """匯出 ONNX 格式"""
    print("\n[步驟] 匯出 ONNX ...")
    try:
        out = model.export(
            format="onnx",
            imgsz=args.imgsz,
            half=args.half,
            dynamic=False,     # 固定輸入尺寸，邊緣部署較穩定
            simplify=True,     # 簡化計算圖，縮小體積並利於推論
            opset=12,
            device=args.device,
        )
        path = Path(out)
        print(f"[完成] ONNX 匯出成功：{path}（{file_size_mb(path):.2f} MB）")
        return path
    except Exception as e:  # noqa: BLE001
        print(f"[錯誤] ONNX 匯出失敗：{e}")
        return None


def export_engine(model: YOLO, args) -> Path | None:
    """匯出 TensorRT engine（可選 INT8 量化）"""
    print("\n[步驟] 匯出 TensorRT engine ...")

    # 準備 INT8 校正資料設定
    # ultralytics 匯出 engine 時，若 int8=True 會用 data=<yaml> 內的
    # 影像做校正；此處允許用 --calib-dir 覆寫校正資料來源。
    calib_data = args.data
    if args.int8 and args.calib_dir:
        calib_yaml = _make_calib_yaml(Path(args.calib_dir))
        calib_data = str(calib_yaml)
        print(f"[資訊] 使用自訂校正資料：{args.calib_dir}")

    try:
        out = model.export(
            format="engine",
            imgsz=args.imgsz,
            half=args.half and not args.int8,  # INT8 優先於 FP16
            int8=args.int8,
            data=calib_data,                   # INT8 校正資料來源
            device=args.device or 0,           # TensorRT 必須在 GPU 上匯出
            workspace=4,                        # TensorRT builder 工作記憶體(GB)
        )
        path = Path(out)
        mode = "INT8" if args.int8 else ("FP16" if args.half else "FP32")
        print(f"[完成] TensorRT({mode}) 匯出成功：{path}（{file_size_mb(path):.2f} MB）")
        return path
    except Exception as e:  # noqa: BLE001
        # 常見於無 NVIDIA GPU / 未安裝 TensorRT 的環境
        print(f"[警告] TensorRT engine 匯出失敗：{e}")
        print("        → 此環境可能無 NVIDIA GPU 或未安裝 TensorRT。")
        print("        → 替代方案：改用 ONNX 格式並以 ONNX Runtime 推論")
        print("          （執行 inference.py 時指定 --model <匯出的 .onnx>）。")
        return None


def _make_calib_yaml(calib_dir: Path) -> Path:
    """
    以指定的校正影像資料夾，動態產生一份臨時 data.yaml 供 INT8 校正使用。
    只需 val 指向校正影像即可，類別沿用主設定檔。
    """
    calib_dir = calib_dir.resolve()
    content = (
        f"path: {calib_dir.parent}\n"
        f"train: {calib_dir.name}\n"
        f"val: {calib_dir.name}\n"
        "names:\n"
        "  0: sign_direction_left\n"
        "  1: sign_direction_right\n"
        "  2: sign_direction_forward\n"
        "  3: sign_train_line\n"
        "  4: sign_hsr\n"
    )
    out = BASE_DIR / "_calib_tmp.yaml"
    out.write_text(content, encoding="utf-8")
    return out


def benchmark(model_path: Path, args) -> None:
    """
    簡易推論速度 benchmark：對隨機輸入重複推論，估算平均延遲與 FPS。
    以 ONNX Runtime 進行（最通用）；失敗則略過。
    """
    if args.no_benchmark:
        return
    print("\n[步驟] 推論速度 benchmark ...")
    try:
        import time
        import numpy as np
        import onnxruntime as ort

        # 僅對 ONNX 檔做這段通用 benchmark
        if model_path.suffix != ".onnx":
            print("[資訊] 略過 benchmark（僅對 ONNX 檔進行通用測速）。")
            return

        # 優先使用 GPU，退回 CPU
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        sess = ort.InferenceSession(str(model_path), providers=providers)
        inp = sess.get_inputs()[0]
        # 建立假輸入（N,C,H,W）
        dummy = np.random.rand(1, 3, args.imgsz, args.imgsz).astype(np.float32)

        # 暖機（前幾次含初始化開銷，不計入）
        for _ in range(5):
            sess.run(None, {inp.name: dummy})

        # 正式量測
        n_iter = 50
        t0 = time.perf_counter()
        for _ in range(n_iter):
            sess.run(None, {inp.name: dummy})
        elapsed = time.perf_counter() - t0

        avg_ms = elapsed / n_iter * 1000
        fps = n_iter / elapsed
        print(f"[結果] 平均延遲：{avg_ms:.2f} ms／張，約 {fps:.1f} FPS")
        print(f"[結果] 使用 provider：{sess.get_providers()[0]}")
        if avg_ms < 200:
            print("[評估] 符合即時性目標（<200ms）。")
        else:
            print("[評估] 未達即時性目標（<200ms），可考慮更小模型或 INT8 量化。")
    except ImportError:
        print("[資訊] 未安裝 onnxruntime，略過 benchmark。")
    except Exception as e:  # noqa: BLE001
        print(f"[資訊] benchmark 略過：{e}")


def main() -> None:
    args = parse_args()

    weights = Path(args.weights)
    if not weights.exists():
        raise SystemExit(f"[錯誤] 找不到權重檔：{weights}（請先執行 train.py 或指定 --weights）")

    print(f"[資訊] 載入權重：{weights}")
    model = YOLO(str(weights))

    onnx_path = None
    if args.format in ("onnx", "all"):
        onnx_path = export_onnx(model, args)
    if args.format in ("engine", "all"):
        export_engine(model, args)

    # benchmark 以 ONNX 進行；若沒匯出 ONNX 但有現成同名檔則嘗試使用
    bench_target = onnx_path or weights.with_suffix(".onnx")
    if bench_target and bench_target.exists():
        benchmark(bench_target, args)

    print("\n[全部完成]")


if __name__ == "__main__":
    main()
