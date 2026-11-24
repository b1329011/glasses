-- ============================================================
-- schema.sql — MySQL 建庫參考（SQLAlchemy 會自動建表，此檔供手動建庫用）
-- ============================================================
-- 使用方式：
--   mysql -u root -p < schema.sql
--   之後將 DATABASE_URL 指向本資料庫，例如：
--   set DATABASE_URL=mysql+pymysql://root:你的密碼@localhost:3306/sign_detector?charset=utf8mb4
-- ============================================================

CREATE DATABASE IF NOT EXISTS sign_detector
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE sign_detector;

-- 方向類別（YOLO class index，可新增）
CREATE TABLE IF NOT EXISTS classes (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  class_index  INT NOT NULL UNIQUE,
  name         VARCHAR(64) NOT NULL UNIQUE,
  display_name VARCHAR(128) DEFAULT '',
  created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 訓練影像 metadata
CREATE TABLE IF NOT EXISTS images (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  filename    VARCHAR(255) NOT NULL UNIQUE,
  split       VARCHAR(16) DEFAULT 'train',
  width       INT DEFAULT 0,
  height      INT DEFAULT 0,
  uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 標註框（YOLO 正規化座標）
CREATE TABLE IF NOT EXISTS annotations (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  image_id    INT NOT NULL,
  class_index INT NOT NULL,
  cx          FLOAT NOT NULL,
  cy          FLOAT NOT NULL,
  w           FLOAT NOT NULL,
  h           FLOAT NOT NULL,
  FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 訓練紀錄
CREATE TABLE IF NOT EXISTS training_runs (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  name         VARCHAR(128),
  status       VARCHAR(32) DEFAULT 'pending',
  params       TEXT,
  map50        FLOAT DEFAULT 0,
  map          FLOAT DEFAULT 0,
  `precision`  FLOAT DEFAULT 0,
  recall       FLOAT DEFAULT 0,
  weights_path VARCHAR(512) DEFAULT '',
  created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
  finished_at  DATETIME NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
