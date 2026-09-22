-- ============================================================
-- SteamDT 全市场 AI 问答系统 — 数据库结构
-- ============================================================
-- 数据库: steamdt_market
-- 字符集: utf8mb4（支持中文饰品名和 emoji）
--
-- 表关系:
--   items (饰品主表)
--     ├─ prices        每日 OHLC 价格
--     ├─ listings      每日各平台挂单量/求购量快照
--     └─ signals       规则引擎产出的信号
--   market_index       大盘指数（独立表）
-- ============================================================

USE steamdt_market;

-- ------------------------------------------------------------
-- 1. 饰品主表
-- ------------------------------------------------------------
DROP TABLE IF EXISTS signals;
DROP TABLE IF EXISTS listings;
DROP TABLE IF EXISTS prices;
DROP TABLE IF EXISTS items;
DROP TABLE IF EXISTS market_index;

CREATE TABLE items (
    item_id           INT AUTO_INCREMENT PRIMARY KEY COMMENT '内部主键',
    market_hash_name  VARCHAR(255) NOT NULL COMMENT 'Steam 市场唯一名（如 AK-47 | Redline (Field-Tested)）',
    name_cn           VARCHAR(255)          COMMENT '中文名（如 AK-47 | 红线 (久经沙场)）',
    weapon            VARCHAR(64)           COMMENT '武器类型（AK-47 / AWP / M4A4 ...）',
    wear              VARCHAR(32)           COMMENT '磨损档位（Factory New / Field-Tested ...）',
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uk_market_hash_name (market_hash_name),
    INDEX idx_weapon  (weapon),
    INDEX idx_wear    (wear),
    INDEX idx_name_cn (name_cn)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='CS2 饰品主表';

-- ------------------------------------------------------------
-- 2. 每日价格（来自 K 线接口）
-- ------------------------------------------------------------
CREATE TABLE prices (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    item_id     INT NOT NULL,
    trade_date  DATE NOT NULL,
    open        DECIMAL(12,2) COMMENT '开盘价',
    close       DECIMAL(12,2) COMMENT '收盘价',
    high        DECIMAL(12,2) COMMENT '最高价',
    low         DECIMAL(12,2) COMMENT '最低价',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE KEY uk_item_date (item_id, trade_date),
    INDEX idx_date (trade_date),
    CONSTRAINT fk_prices_item FOREIGN KEY (item_id) REFERENCES items(item_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='每日价格K线';

-- ------------------------------------------------------------
-- 3. 挂单量 / 求购量（每日快照）
-- ------------------------------------------------------------
CREATE TABLE listings (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    item_id     INT NOT NULL,
    snap_date   DATE NOT NULL,
    platform    VARCHAR(32)   COMMENT '平台（BUFF / YOUPIN / C5 / STEAM ...）',
    sell_price  DECIMAL(12,2) COMMENT '在售最低价',
    sell_count  INT           COMMENT '在售数量（挂单量）',
    bid_price   DECIMAL(12,2) COMMENT '求购最高价',
    bid_count   INT           COMMENT '求购数量',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE KEY uk_item_date_platform (item_id, snap_date, platform),
    INDEX idx_snap_date (snap_date),
    CONSTRAINT fk_listings_item FOREIGN KEY (item_id) REFERENCES items(item_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='各平台挂单量/求购量每日快照';

-- ------------------------------------------------------------
-- 4. 大盘指数
-- ------------------------------------------------------------
CREATE TABLE market_index (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    trade_date     DATE NOT NULL,
    index_value    DECIMAL(12,2) COMMENT '大盘指数',
    diff_yesterday DECIMAL(12,2) COMMENT '较昨日变化',
    diff_ratio     DECIMAL(8,4)  COMMENT '较昨日变化率(%)',
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE KEY uk_trade_date (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='CS2 大盘指数';

-- ------------------------------------------------------------
-- 5. 信号（规则引擎产出，用于问答时引用）
-- ------------------------------------------------------------
CREATE TABLE signals (
    id           BIGINT AUTO_INCREMENT PRIMARY KEY,
    item_id      INT NOT NULL,
    signal_date  DATE NOT NULL,
    signal_type  VARCHAR(16)  COMMENT 'A=吸筹 / D=板块补涨 / A+D',
    level        VARCHAR(16)  COMMENT 'strong / watch',
    score        INT          COMMENT '0-100 综合评分',
    reasons      TEXT         COMMENT '触发原因（分号分隔）',
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE KEY uk_item_date_type (item_id, signal_date, signal_type),
    INDEX idx_signal_date (signal_date),
    INDEX idx_score (score),
    CONSTRAINT fk_signals_item FOREIGN KEY (item_id) REFERENCES items(item_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='量化信号';

-- ------------------------------------------------------------
-- 视图：带涨跌幅的行情快照（方便直接用）
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW v_market_snapshot AS
SELECT
    i.item_id,
    i.market_hash_name,
    i.name_cn,
    i.weapon,
    i.wear,
    p.trade_date,
    p.close                                              AS price,
    ROUND((p.close - p.open) / p.open * 100, 2)          AS change_pct,
    l.sell_count                                         AS listings,
    l.bid_count                                          AS bids,
    l.sell_price
FROM items i
JOIN prices p    ON p.item_id = i.item_id
LEFT JOIN listings l
       ON l.item_id = i.item_id
      AND l.snap_date = p.trade_date
      AND l.platform = 'BUFF';

SELECT '✅ 数据库结构创建完成' AS 状态;
SELECT TABLE_NAME AS 表名, TABLE_COMMENT AS 说明
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'steamdt_market' AND TABLE_TYPE = 'BASE TABLE';
