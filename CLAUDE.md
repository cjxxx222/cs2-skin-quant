# CLAUDE.md — 项目上下文

本文件为 Claude Code 提供项目背景，新会话会自动加载。**修改代码前请先读完本文。**

---

## 这个仓库是什么

CS2（Counter-Strike 2）饰品市场的数据项目集，**包含两个独立子项目**：

| 项目 | 位置 | 定位 | 技术栈 |
|---|---|---|---|
| **① 量化分析系统** | 仓库根目录 | 面向个人交易者，把行情变成信号 | Flask + pandas + Plotly + CSV |
| **② 全市场数据系统** | `steamdt-market-ai/` | 面向数据基础设施，把全市场数据变得可查询 | 采集器 + MySQL + 智能搜索 |

两者共用 [SteamDT 开放平台](https://doc.steamdt.com/) 作为数据源，但解决的问题互补。
**改代码时先确认改的是哪一个**——它们的目录、依赖、数据库完全不同。

---

## 环境配置

### 密钥（环境变量，勿写入代码）

```
STEAMDT_API_KEY    SteamDT 开放平台密钥（两个项目都用）
AI_API_KEY         DeepSeek 密钥（仅项目①的 AI 早报功能用）
```

Windows 设置方式：`setx STEAMDT_API_KEY "你的key"`（设置后需重开终端）

**⚠️ 绝不要把 key 提交到 Git。** 检查方式：
```bash
git ls-files | xargs grep -ln "sk-[a-zA-Z0-9]\{20,\}" 2>/dev/null
```

### MySQL（项目②）

- **位置**：`D:\mysql-8.4.9-winx64`（免安装版，无需管理员权限）
- **启动**：`steamdt-market-ai\scripts\start_mysql.bat`（双击，关窗口即停）
- **连接**：`127.0.0.1:3306`，用户 `root`，**空密码**，库 `steamdt_market`
- **客户端**：DBeaver 社区版（已安装）

### Python 环境

- Python 3.12（项目①）/ 3.14（项目②所在机器也装了）
- 依赖：各自目录下的 `requirements.txt`

---

## 🔴 关键坑（血泪教训，务必遵守）

### 1. MySQL 在 Windows 上不支持中文路径

**症状**：`mysqld: Can't create directory 'D:\学习\...' (OS errno 2)`

MySQL 读不懂中文目录名。安装目录**必须是纯 ASCII 路径**。
（当初想放 `D:\学习`，直接报错，最后移到 `D:\mysql-8.4.9-winx64`）

### 2. 匕首/手套的 market_hash_name 必须带 `★ ` 前缀

```
★ Butterfly Knife | Lore (Minimal Wear)     ✅ 能查到
Butterfly Knife | Lore (Minimal Wear)       ❌ 返回空数据
```

这是 Steam 的命名规则。构造 market_hash_name 时，`category` 为
`Knives` / `Gloves` 的需要加前缀。**不加星号查不到任何数据，且不报错。**

### 3. Windows 控制台 GBK 编码会让 emoji 崩溃

**症状**：`UnicodeEncodeError: 'gbk' codec can't encode character '\U0001f4ca'`

中文 Windows 控制台默认 GBK，打不出 emoji，**脚本直接崩**。

**修复模式**（已在 `config.py` 顶部实现，所有模块 import config 即可生效）：
```python
if sys.platform == "win32":
    import io
    try:
        if not isinstance(sys.stdout, io.TextIOWrapper) or sys.stdout.encoding != "utf-8":
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        if not isinstance(sys.stderr, io.TextIOWrapper) or sys.stderr.encoding != "utf-8":
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except (ValueError, AttributeError):
        pass
```

**新增任何带 print 的入口脚本时，确保它 import 了 config。**

### 4. 模块间字段名不一致（踩过两次）

不同模块对同一概念用了不同字段名：

| 来源 | 中文名 | 英文名 |
|---|---|---|
| SteamDT base 接口 | `name` | `marketHashName` |
| 本项目 item_catalog | `name_cn` | `market_hash_name` |

`search/matcher.py` 的 `extract_names()` 统一处理了两种命名。
**新增模块时，读写饰品字段一律经过 `extract_names()`，不要直接下标取值。**

### 5. SteamDT `base` 接口每天只能调 1 次

`GET /open/cs2/v1/base`（全量饰品列表）**每日限 1 次**，用掉当天就没有了。

应对：
- `collector/base_cache.py` 落盘缓存，遇 4005 错误自动回退旧缓存
- **更优方案**：`collector/item_catalog.py` 用开源数据集构建（见下），完全不消耗额度

### 6. 网络可达性（实测结果）

| 域名 | 状态 |
|---|---|
| `open.steamdt.com` | ✅ 可达 |
| `cdn.jsdelivr.net` / `fastly.jsdelivr.net` | ✅ 可达（用来下开源数据集） |
| `steamcommunity.com` | ❌ **超时**（Steam 官方市场被墙） |
| `raw.githubusercontent.com` | ❌ **不可达** |
| `github.com` | ✅ 可 clone |
| `dev.mysql.com` / `cdn.mysql.com` | ✅ 可达且快（11MB/s） |

### 7. `git add -A` 会扫进临时文件

之前把审查 agent 留下的 7 个临时文件（含一个 5.3MB 数据拷贝）一起提交了。

**提交前先 `git status` 看一眼。** `.gitignore` 已加 `_*` 规则拦截下划线前缀的临时文件。

---

## 常用命令

### 项目①（量化看板）

```bash
cd C:/Users/13481/Desktop/api追踪
python main.py                 # 启动看板（自动打开浏览器）
python main.py --collect-only  # 只采集数据
```

### 项目②（全市场数据）

```bash
cd C:/Users/13481/Desktop/api追踪/steamdt-market-ai

# 构建全市场饰品目录（8969 个 market_hash_name，不消耗 API 额度）
python collector/item_catalog.py

# 采集
python collect_market.py --sync-items          # 同步饰品主表
python collect_market.py --prices              # 全市场价格（90批，约92分钟，可中断续跑）
python collect_market.py --klines --top 500    # 拉重点品种 K 线
python collect_market.py --status              # 查看进度
python collect_market.py --all                 # 依次执行

# 测试
python search/matcher.py                       # 搜索功能自测
python db/connection.py                        # 数据库连接测试
```

---

## 架构要点

### 项目①：规则引擎 + 大模型的分工

```
行情数据 → pandas 算指标 → 规则引擎出信号 → 组织成上下文 → 大模型翻译成自然语言
```

**设计原则**：信号由规则引擎产出（可解释、可复现），大模型只负责"翻译与归纳"，
不负责从原始数据中发现规律。Prompt 中明确要求 **不得编造数据里没有的内容**。

**为什么用挂单量而非成交量**：SteamDT 不提供历史成交量。
挂单量骤降 = 有人扫货（吸筹最直接的证据），比成交量更贴近供给端。

信号 A 的流动性判断有三级降级：
1. 有挂单量历史 → 检测「骤降」（扫货）
2. 仅有当日快照 → 检测「是否低于市场中位数」（稀缺）
3. 无数据 → 该条件不计分，并标注「流动性未验证」

### 项目②：混合检索架构（规划中）

```
用户提问 → LLM 意图解析 → 拆成结构化条件 + 语义条件
                              ↓
              ┌───────────────┴───────────────┐
        SQL 通道（精确数值筛选）      向量通道（语义匹配）
              └───────────────┬───────────────┘
                         融合排序 → LLM 综合作答
```

**为什么必须混合**：向量检索不擅长数值筛选（问"跌幅>5%的AK"会答不准），
数值条件交给 SQL，模糊意图交给向量。

### 数据库表结构（项目②）

```
items         饰品主表    market_hash_name / name_cn / weapon / wear
prices        每日价格    open / close / high / low
listings      挂单快照    sell_count / bid_count / sell_price（唯一键: item+date+platform）
market_index  大盘指数
signals       量化信号
v_market_snapshot  视图（行情 + 涨跌幅 + 挂单量）
```

建表脚本：`sql/schema.sql`（可重复执行，会先 DROP）

---

## SteamDT 接口速查

所有接口需 `Authorization: Bearer <key>`。

| 接口 | 方法 | 用途 | 限速 |
|---|---|---|---|
| `/open/cs2/v1/base` | GET | 全量饰品列表 | **每日 1 次** |
| `/open/cs2/v1/price/single` | GET | 单品各平台价格/挂单量/求购量 | 60 次/分钟 |
| `/open/cs2/v1/price/batch` | POST | 批量价格（≤100个/次） | 1 次/分钟 |
| `/open/cs2/v1/price/avg` | GET | 跨平台均价 | 60 次/分钟 |
| `/open/cs2/item/v1/kline` | POST | 单品 K 线 | 120 次/分钟 |
| `/open/cs2/broad/v1/kline` | POST | 大盘 K 线 | — |
| `/open/cs2/broad/v1/index` | GET | 大盘指数 | — |
| `/open/cs2/v1/wear` | POST | 磨损度 | 36000 次/小时 |

**错误码**：4001 Key 无效 / 4005 超限 / 4006 系统异常 / 100002 参数错误

**K 线参数**：`type` 1=时K 2=日K 3=周K；`marketHashName` 必须带磨损后缀
（如 `AK-47 | Redline (Field-Tested)`），否则返回空数组。

---

## 开源数据源（不消耗 API 额度）

`ByMykel/CSGO-API` 提供 CS2 全量饰品数据（2126 款皮肤 × 磨损档位 ≈ 8969 个 market_hash_name）：

```bash
# 通过 jsDelivr CDN（国内可直连，raw.githubusercontent 被墙）
https://cdn.jsdelivr.net/gh/ByMykel/CSGO-API@main/public/api/en/skins.json      # 英文名
https://cdn.jsdelivr.net/gh/ByMykel/CSGO-API@main/public/api/zh-CN/skins.json   # 中文名
```

构造 market_hash_name 的规则：
```python
base = f"{weapon} | {pattern} ({wear})"
if category in ("Knives", "Gloves"):
    base = "★ " + base      # 刀和手套需要星号前缀
```

**实测抽样 24 个，与 SteamDT 匹配率 100%。**

---

## ⚠️ 项目有两份拷贝

```
Desktop/api追踪/steamdt-market-ai/     ← 合并版，GitHub 上的是这份
Desktop/steamdt-market-ai/             ← 独立版（本地 git 仓库）
```

内容相同。**改动只改一份**，否则会分叉。
**以 `api追踪` 里那份为准**（GitHub 仓库 `cjxxx222/cs2-skin-quant` 对应它）。

---

## 代码约定

- 注释和输出用**中文**
- 涉及网络/数据库的操作要有**降级路径**（无 key、接口挂了、数据缺失时功能不中断）
- 写库前校验，避免"看起来成功实际没写进去"
- 采集类脚本要**可中断续跑**（状态落盘，重跑自动跳过已完成部分）
- 新增依赖要同步更新 `requirements.txt`（曾出现代码 import pymysql 但 requirements 没写）
