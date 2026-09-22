"""
MySQL 连接管理
==============
提供简单的连接池式用法：with get_conn() as conn: ...

用法：
    from db.connection import query, execute, executemany

    rows = query("SELECT * FROM items WHERE weapon=%s", ("AK-47",))
    execute("INSERT INTO items (market_hash_name) VALUES (%s)", ("AK-47 | Redline (Field-Tested)",))
"""

import os
import sys
from contextlib import contextmanager
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymysql
from pymysql.cursors import DictCursor

from config import DB


@contextmanager
def get_conn(autocommit: bool = True):
    """获取数据库连接（上下文管理器，自动关闭）"""
    conn = pymysql.connect(
        host=DB["host"],
        port=DB["port"],
        user=DB["user"],
        password=DB["password"],
        database=DB["database"],
        charset=DB["charset"],
        autocommit=autocommit,
        cursorclass=DictCursor,
    )
    try:
        yield conn
    finally:
        conn.close()


# ------------------------------------------------------------
# 便捷操作
# ------------------------------------------------------------

def query(sql: str, params: Optional[Tuple] = None) -> List[Dict[str, Any]]:
    """执行查询，返回字典列表"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()


def query_one(sql: str, params: Optional[Tuple] = None) -> Optional[Dict[str, Any]]:
    """执行查询，返回第一行"""
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Optional[Tuple] = None) -> int:
    """执行写操作，返回受影响行数"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            return cur.execute(sql, params or ())


def executemany(sql: str, params_seq: Iterable[Tuple]) -> int:
    """批量执行（用于批量插入）"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            return cur.executemany(sql, list(params_seq))


# ------------------------------------------------------------
# 写入辅助（upsert）
# ------------------------------------------------------------

def upsert_item(market_hash_name: str, name_cn: str = "", weapon: str = "", wear: str = "") -> int:
    """
    插入或更新饰品，返回 item_id。
    依赖 market_hash_name 上的唯一索引。
    """
    sql = """
        INSERT INTO items (market_hash_name, name_cn, weapon, wear)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            name_cn = VALUES(name_cn),
            weapon  = VALUES(weapon),
            wear    = VALUES(wear),
            item_id = LAST_INSERT_ID(item_id)
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (market_hash_name, name_cn, weapon, wear))
            return cur.lastrowid


def save_prices(item_id: int, rows: Iterable[Tuple]) -> int:
    """
    批量写入 K 线。rows 为 (trade_date, open, close, high, low) 序列。
    已存在的同日数据会被覆盖。
    """
    sql = """
        INSERT INTO prices (item_id, trade_date, open, close, high, low)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            open=VALUES(open), close=VALUES(close),
            high=VALUES(high), low=VALUES(low)
    """
    data = [(item_id, d, o, c, h, l) for (d, o, c, h, l) in rows]
    return executemany(sql, data)


def save_listing(item_id: int, snap_date: str, platform: str,
                 sell_price: float, sell_count: int,
                 bid_price: float, bid_count: int) -> int:
    """写入一条挂单量快照"""
    sql = """
        INSERT INTO listings (item_id, snap_date, platform, sell_price, sell_count, bid_price, bid_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            sell_price=VALUES(sell_price), sell_count=VALUES(sell_count),
            bid_price=VALUES(bid_price),   bid_count=VALUES(bid_count)
    """
    return execute(sql, (item_id, snap_date, platform, sell_price, sell_count, bid_price, bid_count))


def table_counts() -> Dict[str, int]:
    """统计各表行数（便于确认数据写入情况）"""
    tables = ["items", "prices", "listings", "market_index", "signals"]
    result = {}
    for t in tables:
        row = query_one(f"SELECT COUNT(*) AS n FROM {t}")
        result[t] = row["n"] if row else 0
    return result


if __name__ == "__main__":
    import json
    print("数据库连接测试:")
    row = query_one("SELECT VERSION() AS v, DATABASE() AS db")
    print("  版本:", row["v"], "| 库:", row["db"])
    print("  各表行数:", json.dumps(table_counts(), ensure_ascii=False))
