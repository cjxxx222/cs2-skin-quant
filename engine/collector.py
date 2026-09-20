"""
数据采集引擎
支持多数据源、可配置 API 端点、自动重试、分页、限速

配合 config/api_endpoints.json 使用：
  1. 抓包获取 SteamDT 真实 API 地址
  2. 填入 api_endpoints.json 对应字段
  3. 采集器自动读取配置并采集

设计目标：
  - API 端点完全可配置（无需改代码）
  - 支持多个数据源（steamdt / steam / buff）
  - 自动分页、重试、限速
  - 数据标准化后写入 CSV
"""

import os
import sys
import json
import time
import hashlib
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Iterator
from dataclasses import dataclass, field
from collections import deque

# Windows 编码修复（安全包装，避免重复包装）
if sys.platform == "win32":
    import io
    try:
        if not isinstance(sys.stdout, io.TextIOWrapper) or sys.stdout.encoding != "utf-8":
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        if not isinstance(sys.stderr, io.TextIOWrapper) or sys.stderr.encoding != "utf-8":
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except (ValueError, AttributeError):
        pass

import pandas as pd
import numpy as np

# 优先使用 httpx，回退到 urllib
try:
    import urllib.request
    import urllib.error
    import urllib.parse
    _USE_HTTPX = False
except Exception:
    pass


@dataclass
class EndpointConfig:
    """单个 API 端点的配置"""
    method: str = "GET"
    path: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)
    pagination: Dict[str, Any] = field(default_factory=dict)
    response_mapping: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceConfig:
    """数据源配置"""
    name: str
    enabled: bool = True
    base_url: str = ""
    endpoints: Dict[str, EndpointConfig] = field(default_factory=dict)
    categories: List[Dict] = field(default_factory=list)
    rate_limit: Dict[str, Any] = field(default_factory=dict)


class RateLimiter:
    """简陋但够用的请求限速器"""
    def __init__(self, rps: float = 3, burst: int = 5, cooldown: float = 60):
        self.min_interval = 1.0 / rps
        self.burst = burst
        self.cooldown = cooldown
        self.timestamps: deque = deque(maxlen=burst)
        self._cooling_down = False

    def wait(self):
        now = time.time()
        if self._cooling_down:
            time.sleep(self.cooldown)
            self._cooling_down = False
            self.timestamps.clear()

        if self.timestamps:
            elapsed = now - self.timestamps[-1]
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)

        self.timestamps.append(time.time())


def load_endpoint_config(config_path: str) -> Dict[str, SourceConfig]:
    """从 JSON 加载数据源配置"""
    with open(config_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    sources = {}
    for src_name, src_data in raw.get("sources", {}).items():
        if not src_data.get("enabled", True):
            continue

        endpoints = {}
        for ep_name, ep_data in src_data.get("endpoints", {}).items():
            endpoints[ep_name] = EndpointConfig(
                method=ep_data.get("method", "GET"),
                path=ep_data.get("path", ""),
                params=ep_data.get("params", {}),
                headers=ep_data.get("headers", {}),
                pagination=ep_data.get("pagination", {}),
                response_mapping=ep_data.get("response_mapping", {}),
            )

        sources[src_name] = SourceConfig(
            name=src_name,
            enabled=src_data.get("enabled", True),
            base_url=src_data.get("base_url", ""),
            endpoints=endpoints,
            categories=src_data.get("categories", []),
            rate_limit=src_data.get("rate_limit", {}),
        )

    return sources


class HTTPClient:
    """简洁的 HTTP 客户端（优先 httpx，回退 urllib）"""

    def __init__(self, timeout: int = 10, user_agent: str = None):
        self.timeout = timeout
        self.user_agent = user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

    def get(self, url: str, params: Dict = None, headers: Dict = None) -> Optional[dict]:
        """GET 请求，返回 JSON"""
        # 构建 URL
        if params:
            query = urllib.parse.urlencode(params)
            url = f"{url}?{query}"

        # 构建请求头
        req_headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        req_headers.update(headers or {})

        req = urllib.request.Request(url, headers=req_headers)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = resp.read().decode("utf-8")
                return json.loads(data)
        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code}: {url}")
            return None
        except urllib.error.URLError as e:
            print(f"  连接失败: {e.reason}")
            return None
        except json.JSONDecodeError:
            print(f"  JSON 解析失败: {url}")
            return None
        except Exception as e:
            print(f"  请求异常: {e}")
            return None


class MarketCollector:
    """
    市场数据采集器

    用法：
        collector = MarketCollector("config/api_endpoints.json")
        collector.collect_all()          # 全量采集
        collector.collect_category("collection")  # 按分类采集
    """

    def __init__(self, config_path: str = None, data_path: str = None):
        # 配置
        if config_path is None:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(base, "config", "api_endpoints.json")
        self.config_path = config_path
        self.sources = load_endpoint_config(config_path)

        # 数据存储
        if data_path is None:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_path = os.path.join(base, "data", "prices.csv")
        self.data_path = data_path

        # HTTP 客户端
        self.client = HTTPClient(timeout=10)

        # 限速器
        self.limiters: Dict[str, RateLimiter] = {}
        for name, src in self.sources.items():
            rl = src.rate_limit
            self.limiters[name] = RateLimiter(
                rps=rl.get("requests_per_second", 3),
                burst=rl.get("burst", 5),
                cooldown=rl.get("cooldown_seconds", 60),
            )

        self._stats = {"total_collected": 0, "errors": 0, "skipped": 0}

    # ============================================================
    # 公共 API
    # ============================================================

    def collect_all(self) -> pd.DataFrame:
        """全量采集：遍历所有启用的分类"""
        print("\n" + "=" * 60)
        print("📡 开始全市场数据采集")
        print("=" * 60)

        all_records = []

        with open(self.config_path, "r", encoding="utf-8") as f:
            raw_config = json.load(f)

        for cat_cfg in raw_config.get("scan_categories", []):
            if not cat_cfg.get("enabled", True):
                continue
            cat_key = cat_cfg["key"]
            source_name = cat_cfg["source"]
            endpoint_name = cat_cfg["endpoint"]
            params_override = cat_cfg.get("params_override", {})

            print(f"\n📂 分类: {cat_key} (源: {source_name}, 端点: {endpoint_name})")
            records = self._collect_endpoint(
                source_name, endpoint_name, params_override
            )
            all_records.extend(records)
            print(f"   ✅ {len(records)} 条记录")

        if all_records:
            df = self._save_to_csv(all_records)
            print(f"\n💾 已保存 {len(all_records)} 条记录 → {self.data_path}")
            return df
        else:
            print("\n⚠️ 未采集到任何数据（请检查 API 端点配置是否正确）")
            return pd.DataFrame()

    def collect_category(self, category_key: str, params_override: Dict = None) -> List[Dict]:
        """采集单个分类"""
        with open(self.config_path, "r", encoding="utf-8") as f:
            raw_config = json.load(f)

        for cat_cfg in raw_config.get("scan_categories", []):
            if cat_cfg["key"] == category_key:
                source_name = cat_cfg["source"]
                endpoint_name = cat_cfg["endpoint"]
                override = {**cat_cfg.get("params_override", {}), **(params_override or {})}
                return self._collect_endpoint(source_name, endpoint_name, override)

        print(f"⚠️ 未找到分类: {category_key}")
        return []

    # ============================================================
    # 内部：端点调用
    # ============================================================

    def _collect_endpoint(
        self, source_name: str, endpoint_name: str, params_override: Dict = None
    ) -> List[Dict]:
        """采集单个端点的全部分页数据"""
        source = self.sources.get(source_name)
        if not source:
            print(f"❌ 数据源不存在: {source_name}")
            return []

        ep = source.endpoints.get(endpoint_name)
        if not ep:
            print(f"❌ 端点不存在: {endpoint_name}")
            return []

        limiter = self.limiters.get(source_name)
        all_records = []

        # 合并参数
        params = dict(ep.params)
        if params_override:
            params.update(params_override)

        # 分页采集
        pagination = ep.pagination
        pag_type = pagination.get("type", "offset")
        max_pages = pagination.get("max_pages", 50)
        param_page = pagination.get("param_page", "page")
        param_size = pagination.get("param_size", "pageSize")

        for page in range(1, max_pages + 1):
            if pag_type == "offset":
                params[param_page] = page

            url = source.base_url.rstrip("/") + "/" + ep.path.lstrip("/")
            limiter.wait()

            resp = self.client.get(url, params=params, headers=ep.headers)

            if resp is None:
                self._stats["errors"] += 1
                break

            # 提取数据
            records = self._extract_records(resp, ep.response_mapping)
            if not records:
                break

            all_records.extend(records)

            # 检查是否还有更多页
            data_path = ep.response_mapping.get("data_path", "data.items")
            total_path = ep.response_mapping.get("total_path", "data.total")

            total = self._get_nested(resp, total_path, 0)
            if total and len(all_records) >= total:
                break
            if len(records) < params.get(param_size, 100):
                break

        self._stats["total_collected"] += len(all_records)
        return all_records

    def _extract_records(self, response: dict, mapping: Dict) -> List[Dict]:
        """从 API 响应中提取并标准化数据"""
        data_path = mapping.get("data_path", "data.items")
        raw_items = self._get_nested(response, data_path, [])

        if not raw_items:
            return []

        field_map = mapping.get("fields", {})
        records = []

        for item in raw_items:
            if not isinstance(item, dict):
                continue

            record = {
                "skin_name": str(item.get(field_map.get("name", "name"), "")),
                "date": datetime.now().strftime("%Y-%m-%d"),
                "price": self._safe_float(item.get(field_map.get("price", "price"), 0)),
                "volume": int(item.get(field_map.get("volume_24h", "volume24h"), 0) or 0),
                "listings": int(item.get(field_map.get("listings", "listings"), 0) or 0),
                "change_percent": self._safe_float(item.get(field_map.get("change_percent", "changePercent"), 0)),
                "source": "steamdt",
                "collected_at": datetime.now().isoformat(),
            }
            records.append(record)

        return records

    # ============================================================
    # 辅助方法
    # ============================================================

    def _save_to_csv(self, records: List[Dict]) -> pd.DataFrame:
        """将采集记录写入 CSV（追加到现有文件）"""
        df_new = pd.DataFrame(records)

        os.makedirs(os.path.dirname(self.data_path), exist_ok=True)

        if os.path.exists(self.data_path):
            df_existing = pd.read_csv(self.data_path)
            df_existing["date"] = pd.to_datetime(df_existing["date"])
            # 去重：同 skin_name + 同 date 的记录只保留最新
            df_combined = pd.concat([df_existing, df_new], ignore_index=True)
            df_combined = df_combined.sort_values(["skin_name", "date", "collected_at"])
            df_combined = df_combined.drop_duplicates(
                subset=["skin_name", "date"], keep="last"
            )
            df_combined.to_csv(self.data_path, index=False, encoding="utf-8-sig")
            return df_combined
        else:
            df_new.to_csv(self.data_path, index=False, encoding="utf-8-sig")
            return df_new

    @staticmethod
    def _get_nested(obj: dict, path: str, default: Any = None) -> Any:
        """用点分隔路径访问嵌套字典"""
        try:
            for key in path.split("."):
                if isinstance(obj, dict):
                    obj = obj.get(key, default)
                elif isinstance(obj, list) and key.isdigit():
                    obj = obj[int(key)]
                else:
                    return default
            return obj
        except (KeyError, IndexError, TypeError):
            return default

    @staticmethod
    def _safe_float(val) -> float:
        """安全转换为浮点数（处理 '¥1,234.56' 这类格式）"""
        if val is None:
            return 0.0
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            val = val.replace("¥", "").replace("$", "").replace(",", "").replace(" ", "")
            try:
                return float(val)
            except ValueError:
                return 0.0
        return 0.0

    @property
    def stats(self) -> Dict:
        return dict(self._stats)


# ============================================================
# 便捷函数
# ============================================================

def quick_collect(config_path: str = None, data_path: str = None) -> int:
    """快速采集：返回采集记录数"""
    collector = MarketCollector(config_path, data_path)
    df = collector.collect_all()
    return len(df)


if __name__ == "__main__":
    collector = MarketCollector()
    collector.collect_all()
    print(f"\n采集统计: {collector.stats}")
