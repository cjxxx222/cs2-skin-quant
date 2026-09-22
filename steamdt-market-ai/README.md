# SteamDT 全市场 AI 问答系统

> 基于真实 CS2 饰品全市场数据，用自然语言提问即可获得带数据支撑的市场分析。

## 这是什么

CS2 饰品市场有**数千款**饰品，散落在 BUFF、悠悠有品、C5、Steam 等多个平台。
普通玩家想问「最近跌得狠、适合抄底的 AK 皮肤有哪些」，只能一个个翻——本系统让你**直接问**。

## 与「项目1」的区别

| | 项目1（CS2 量化看板） | 本项目 |
|---|---|---|
| 驱动方式 | 规则引擎（固定信号） | 大模型（理解自然语言） |
| 数据规模 | 15 款精选 | 全市场数千款 |
| 检索方式 | 全量塞进 Prompt | 混合检索（SQL + 向量） |
| 输出 | 固定格式早报 | 对话式问答 |

两个项目在能力上互补：一个证明**规则设计能力**，一个证明**AI 工程能力**。

## 当前进度

- [x] **SteamDT API 客户端**（鉴权 / 限速 / 重试 / 错误码解析）
- [x] **全量列表缓存**（base 接口每日限 1 次，必须落盘）
- [x] **智能搜索匹配**（中英文混合、武器别名、皮肤昵称）
- [x] **MySQL 数据建模**（5 表 + 1 视图，含外键与索引）
- [ ] 全市场批量采集
- [ ] 混合检索（Text-to-SQL + 向量库）
- [ ] 多轮对话与 Agent 工具调用
- [ ] 评测闭环

## 目录结构

```
steamdt-market-ai/
├── config.py                  # 全局配置（API / 数据库 / 采集参数）
├── collector/
│   ├── client.py              # SteamDT API 客户端
│   └── base_cache.py          # 全量列表缓存
├── search/
│   └── matcher.py             # 智能名称匹配
├── db/
│   └── connection.py          # MySQL 连接与写入辅助
├── sql/
│   └── schema.sql             # 建表脚本
├── scripts/
│   ├── start_mysql.bat        # 启动 MySQL
│   └── mysql_cli.bat          # 打开 MySQL 命令行
└── data/cache/                # 缓存目录
```

## 快速开始

### 1. 启动 MySQL

```bash
# 双击运行（启动后窗口保持开启）
scripts\start_mysql.bat
```

> MySQL 为**免安装版**，位于 `D:\mysql-8.4.9-winx64`，无需管理员权限。
> ⚠️ MySQL 在 Windows 上**不支持中文路径**，因此不能放在 `D:\学习` 这类目录下。

### 2. 安装依赖并配置密钥

```bash
pip install -r requirements.txt
setx STEAMDT_API_KEY "你的key"     # 设置后需重开终端
```

### 3. 拉取全量饰品列表

```bash
# ⚠️ 每天只能执行 1 次（会落盘缓存，之后自动复用）
python -c "from collector.base_cache import BaseCache; BaseCache().refresh()"
```

### 4. 验证

```bash
python search/matcher.py       # 搜索功能测试
python db/connection.py        # 数据库连接测试
```

## 数据库结构

```
items         饰品主表     market_hash_name / name_cn / weapon / wear
prices        每日价格     open / close / high / low（唯一索引: item+date）
listings      挂单快照     sell_count / bid_count / sell_price（唯一索引: item+date+platform）
market_index  大盘指数     index_value / diff_ratio
signals       量化信号     signal_type / score / reasons

v_market_snapshot  视图：行情 + 涨跌幅 + 挂单量，可直接查询
```

## 核心设计说明

### 1. 为什么 base 接口必须缓存？

SteamDT 的 `/open/cs2/v1/base`（全量饰品列表）**每天只能调用 1 次**。
一旦浪费掉，当天就再也拿不到全市场列表，整个系统停摆。

`base_cache.py` 的处理：
- 优先读本地缓存，不重复调 API
- 遇到 `4005`（超限）**自动回退到旧缓存**，功能不中断
- 缓存文件带 `fetched_at` / `count` / `age_hours` 元信息，可判断新鲜度

### 2. 为什么需要智能搜索？

用户不可能打出 `AK-47 | Redline (Field-Tested)` 这样的完整名称。
实际输入往往是 `ak 红线`、`awp龙狙`、`沙鹰`。

`matcher.py` 用**武器别名表 + 皮肤昵称表 + 中英文混合分词**解决：
- `ak` → `ak-47` / `ak47`
- `龙狙` → `dragon lore`
- `ft` → `field-tested`
- `沙鹰` → `desert eagle`

## 数据来源

[SteamDT 开放平台](https://doc.steamdt.com/)（国内可直连）

| 接口 | 用途 | 限速 |
|---|---|---|
| `GET /open/cs2/v1/base` | 全量饰品列表 | 每日 1 次 |
| `GET /open/cs2/v1/price/single` | 单品各平台价格/挂单量 | 60 次/分钟 |
| `POST /open/cs2/v1/price/batch` | 批量价格 | 1 次/分钟 |
| `GET /open/cs2/v1/price/avg` | 跨平台均价 | 60 次/分钟 |
| `POST /open/cs2/item/v1/kline` | 单品 K 线 | 120 次/分钟 |
| `GET /open/cs2/broad/v1/index` | 大盘指数 | — |

## 免责声明

本项目为技术学习与作品展示用途，所有分析结论**不构成任何投资建议**。
不涉及真实资金交易、代客理财或任何博彩功能。
