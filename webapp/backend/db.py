# -*- coding: utf-8 -*-
"""
db.py — SQLAlchemy 資料庫模型與連線
============================================================
資料表：
  classes        方向類別（YOLO class index 0..N-1，可新增）
  images         已上傳的訓練影像 metadata
  annotations    每張影像的標註框（YOLO 正規化座標）
  training_runs  訓練紀錄與最終指標

影像檔與 .txt 標註存在 dataset 資料夾；本 DB 只存 metadata。
"""

from datetime import datetime

from sqlalchemy import (
    create_engine, Integer, String, Float, DateTime, ForeignKey, Text,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker,
)

from config import DEFAULT_MYSQL_URL, SQLITE_FALLBACK_URL


class Base(DeclarativeBase):
    pass


class ClassLabel(Base):
    """方向類別。class_index 對應 YOLO 標註的類別編號。"""
    __tablename__ = "classes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_index: Mapped[int] = mapped_column(Integer, unique=True)       # 0-based
    name: Mapped[str] = mapped_column(String(64), unique=True)           # 英文 id 名
    display_name: Mapped[str] = mapped_column(String(128), default="")    # 中文顯示
    kind: Mapped[str] = mapped_column(String(16), default="arrow")        # arrow=方向箭頭 / icon=圖標
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ImageRecord(Base):
    """已上傳的訓練影像。"""
    __tablename__ = "images"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255), unique=True)
    split: Mapped[str] = mapped_column(String(16), default="train")
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    annotations: Mapped[list["Annotation"]] = relationship(
        back_populates="image", cascade="all, delete-orphan"
    )


class Annotation(Base):
    """單一標註框（YOLO 正規化 cx,cy,w,h，皆 0~1）。"""
    __tablename__ = "annotations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    image_id: Mapped[int] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"))
    class_index: Mapped[int] = mapped_column(Integer)
    cx: Mapped[float] = mapped_column(Float)
    cy: Mapped[float] = mapped_column(Float)
    w: Mapped[float] = mapped_column(Float)
    h: Mapped[float] = mapped_column(Float)
    image: Mapped["ImageRecord"] = relationship(back_populates="annotations")


class TrainingRun(Base):
    """一次訓練的參數與最終指標。"""
    __tablename__ = "training_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="pending")  # running/done/error
    params: Mapped[str] = mapped_column(Text, default="")               # JSON 字串
    map50: Mapped[float] = mapped_column(Float, default=0.0)
    map: Mapped[float] = mapped_column(Float, default=0.0)
    precision: Mapped[float] = mapped_column(Float, default=0.0)
    recall: Mapped[float] = mapped_column(Float, default=0.0)
    weights_path: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)


def _make_engine():
    """優先連 MySQL，連不上就退回 SQLite，確保服務一定能啟動。"""
    try:
        engine = create_engine(DEFAULT_MYSQL_URL, pool_pre_ping=True, future=True)
        # 實際嘗試連線
        with engine.connect():
            pass
        print(f"[DB] 已連線 MySQL：{DEFAULT_MYSQL_URL.split('@')[-1]}")
        return engine
    except Exception as e:  # noqa: BLE001
        print(f"[DB] MySQL 連線失敗（{e.__class__.__name__}），改用 SQLite 後備。")
        print(f"[DB] SQLite：{SQLITE_FALLBACK_URL}")
        return create_engine(SQLITE_FALLBACK_URL, future=True,
                             connect_args={"check_same_thread": False})


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)


def init_db() -> None:
    """建立資料表（若不存在），並補上舊資料庫缺少的欄位。"""
    Base.metadata.create_all(engine)
    # 輕量 migration：舊 DB 的 classes 表若沒有 kind 欄位就補上（預設 arrow）
    from sqlalchemy import inspect, text
    try:
        cols = [c["name"] for c in inspect(engine).get_columns("classes")]
        if "kind" not in cols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE classes ADD COLUMN kind VARCHAR(16) DEFAULT 'arrow'"))
            print("[DB] 已為 classes 補上 kind 欄位")
    except Exception as e:  # noqa: BLE001
        print(f"[DB] kind 欄位 migration 略過：{e}")
