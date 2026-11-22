# 微信沟通日报 · 每晚自动生成

把当天微信聊天记录变成一份**可扫读的日报**：晚上 22:55 在你自己电脑上跑，
23:00 由云端定时任务把日报成品送到会话里。

原始聊天记录**不出你的电脑**，摘要用**你自己的 API key** 在本机生成，
云端只接触「日报成品」这一段文字。

---

## 目录

1. [项目解决什么问题](#1-项目解决什么问题)
2. [主要功能](#2-主要功能)
3. [安装方法](#3-安装方法)
4. [使用方法](#4-使用方法)
5. [输入输出示例](#5-输入输出示例)

附：[微信 4.x 的加密为什么这么难](#附微信-4x-的加密为什么这么难) ·
[配置项速查](#配置项速查) · [排查](#排查) · [自测](#自测)

---

## 1. 项目解决什么问题

**场景**：你一天在微信里聊了几十个会话——工作群、同事、领导、朋友、家人、
客户、拼单群。有人找你确认事情，有人 @ 你，有人只是路过。

到晚上想复盘的时候，聊天记录是一堆按时间排的消息，翻几屏就放弃；
真要总结，还得把十几个会话里的待办、承诺、结论一条条捞出来。

**这个项目做的事**：

```
① 把微信加密的数据库打开、导出成结构化消息      ← 最难的一步，已封装好
② 归一化成统一的「当天消息」                    ← 已做好
③ 按「我参与过的会话」筛一遍、以我为中心裁剪上下文  ← 已做好
④ 调用你自己的 API key 生成 Markdown 日报         ← 已做好
⑤ 日报写进飞书多维表格 + 存一份到本机 reports/    ← 已做好
⑥ 每天 22:55 自动跑                              ← 已做好（Windows 计划任务）
⑦ 每晚 23:00 云端定时任务把日报送到你的会话      ← 已做好（平台定时任务）
```

**它不做什么**（刻意不做）：

- 不抓取别人的微信，不联网上传原始记录
- 不代替你回消息，不自动做任何操作
- 拿不到日报就如实说没有，**绝不编造**

---

## 2. 主要功能

| 功能 | 说明 | 在哪 |
|---|---|---|
| **微信 4.2 解密导出** | 静态扫 XOR 常量 → 只读扫内存 → HMAC 验真 → 注入 chatlog-keeper → 导出 JSON | `collector/wechat4.py setup` |
| **可插拔采集** | 换解密工具不改后面的代码，只要它吐出 JSON/JSONL/CSV/MD | `config.env` 的 `EXPORT_CMD` |
| **多格式归一化** | chatlog / 留痕 CSV / 通用 JSON 数组 / Markdown 都吃，字段名有别名兜底 | `collector/normalize.py` |
| **范围过滤** | 默认只保留「我发过言的会话」，并以我为中心裁剪上下文 | `DAILY_SCOPE` / `DAILY_CONTEXT_WINDOW` |
| **白名单群** | 指定群不管你说不说话都进日报；当天全员沉默也进 | `DAILY_INCLUDE_CHATS` |
| **本机摘要** | 调 OpenAI 兼容接口生成日报，内容长自动分块再合并 | `collector/summarize_local.py` |
| **日报格式** | 事务型会话进「关键进展/待办/跟进/留意」，日常型进「日常往来」 | `prompts/daily_report.md` |
| **飞书中转** | 原始记录和日报成品走**两条独立通道**，云端只读日报那一列 | `collector/bridge.py` |
| **一键自检** | 缺什么、错在哪、怎么补，一条条列出来 | `collector/doctor.py` |
| **免手抄配置** | `set` / `set-table` / `set-clip` 改配置，密钥不回显、不打码 | `collector/wechat4.py` |
| **自动运行** | Windows 计划任务（22:55）+ 平台定时任务（23:00） | `collector/install_task.py` |

### 架构

```
你的 Windows 电脑                              飞书多维表格          平台云端
┌──────────────────────────────────┐          ┌──────────────┐      ┌────────────────┐
│ chatlog-keeper（解密导出）        │          │ 日期          │      │ 23:00 定时任务  │
│   ↓ wechat_messages.json         │  原始记录 │ 消息数        │      │ 只读「日报」列  │
│ normalize.py → data/<日期>.json   │ ───────→ │ 内容（原始）  │      │        ↓       │
│   ↓ 范围过滤 + 上下文裁剪         │          │              │      │ 会话里呈现日报  │
│ summarize_local.py               │  日报成品 │ 日报（成品）  │ ←─── │                │
│   ↓ 用你自己的 API key           │ ───────→ │              │      └────────────────┘
│ reports/<日期>.md（本机）         │          └──────────────┘
└──────────────────────────────────┘
```

### 许可证

[MIT](LICENSE)。项目里的 `setup` 会在运行时下载 chatlog-keeper 等第三方工具到
`%USERPROFILE%\.wechat_daily_tools\`，**不打包进仓库**，各自遵循其原有许可证。

## 隐私边界（代码里划死的）

| 环节 | 数据在哪 |
|---|---|
| 原始聊天记录 | 你的电脑 + 你选的飞书表「内容」列。**平台云端拿不到** |
| 摘要生成 | 你的电脑，调用**你自己的** MiniMax API key |
| 平台定时任务 | 只读飞书的「日报」列（`bridge.py pull-report` 只取这一列） |

原始记录会进飞书——这是你选的中转；除它之外，记录不再流到别处。
不想让飞书存原始记录，就用 `--no-push`，日报照样在本机 `reports/` 生成。

---

## 3. 安装方法

### 3.1 环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Windows（macOS / Linux 也能跑，只是没有计划任务那一环） |
| 微信 | **4.1.10.31 及以上**（含 4.2），已登录 |
| Python | 3.10+，**已加进 PATH**（能直接敲 `python`） |
| 磁盘 | 工具缓存约 50 MB（`%USERPROFILE%\.wechat_daily_tools\`） |

> Python 3.14 也能用：项目内置了 yara-python 的纯 Python 替身
> （`collector/_yara_shim.py`），不需要编译 MSVC。

### 3.2 拿到项目

解压到任意目录（建议路径别太深，别放在有中文和空格的路径里）：

```
C:\wechat-daily\
```

### 3.3 一次性设置

**① 解锁微信数据（最关键的一步）**

```bat
cd /d C:\wechat-daily\collector
setup.bat
```

它会自动：找 `xwechat_files` 目录 → 下载 chatlog-keeper → 装依赖 →
静态扫 XOR 常量 → 只读扫内存恢复密钥 → HMAC 验真 → 注入 → 导出验证。

- 需要**管理员权限**（读别的进程内存）
- 需要**微信已登录并正在运行**
- 自动找不到数据目录时手动指定：
  ```bat
  setup.bat --data-root "C:\Users\你的用户名\Documents\xwechat_files"
  ```

**② 建飞书多维表格**（详见 [飞书表格怎么建](#飞书多维表格怎么建)）

需要 4 列，**列名必须一字不差**：`日期`（文本）/ `消息数`（数字）/ `内容`（多行文本）/ `日报`（多行文本）

**③ 填配置**

```bat
cd /d C:\wechat-daily

REM 表格 ID 用网址来填（网址里不含密钥，复制不会被打码）
python collector\wechat4.py set-table "https://xxx.feishu.cn/base/appXXXXXXXX?table=tblYYYYYYYY"

REM 飞书凭据从剪贴板读，最不容易复制错
python collector\wechat4.py set-clip FS_APP_ID
python collector\wechat4.py set-clip FS_APP_SECRET

REM MiniMax 的 API key
python collector\wechat4.py set-clip LLM_API_KEY
```

> **为什么要用 `set-clip`**：密钥串在聊天里会被平台自动打码成 `{{ARCHON_SECRET:...}}`，
> 复制出来必然是错的。`set-clip` 直接从剪贴板读，机器上写、聊天里不留痕。
> 剪贴板里要是打码占位符、含换行、或者长度明显不对（LLM key 至少 32 位），程序会直接拒绝。

**④ 自检**

```bat
cd /d C:\wechat-daily
python collector\doctor.py --write-test
```

全绿就绪。`--write-test` 会真写一行测试日报 → 回读 → 删掉，用来验证飞书写权限。

**⑤ 装定时任务**

```bat
cd /d C:\wechat-daily\collector
install_task.bat install
```

任务名 `WeChatDailyReport`，每天 22:55 跑，日志追加到项目根目录的 `wechat_daily.log`。

### 3.4 飞书多维表格怎么建

1. 打开 <https://open.feishu.cn/app> → **创建企业自建应用** → 记下 **App ID** 和 **App Secret**
2. 应用详情 → **添加应用能力** → 找到「多维表格」→ 开通权限（至少 `bitable:app` **读写**）
3. 新建一张多维表格，建 4 列：

   | 列名 | 类型 | 谁写 | 内容 |
   |---|---|---|---|
   | 日期 | 文本（主字段） | 采集脚本 | `2026-11-22` |
   | 消息数 | 数字 | 采集脚本 | 当天消息条数 |
   | 内容 | 多行文本 | 采集脚本 | 归一化后的 JSON（原始记录） |
   | 日报 | 多行文本 | 本机摘要脚本 | Markdown 日报成品 |

4. 从表格 URL 里取 `app_token` 和 `table_id`：
   `https://xxx.feishu.cn/base/appXXXXXXXX?table=tblYYYYYYYY` → 拼成 `appXXXXXXXX/tblYYYYYYYY`
5. **把应用加为表格的「文档应用」**（这一步最容易卡住）：
   - 入口在**多维表格页面**（不是开放平台）右上角 **「···」→「···更多」→「添加文档应用」**
   - 搜应用名 → 选 **可管理** → 「添加」
   - ⚠️ 表格开了「高级权限」时**必须给「可管理」**，给「可编辑」仍然写不了（报 91403）
   - ⚠️ 窗口里搜不到这个应用 = ①应用没开通云文档 API 权限 ②**发布版本的可用范围不包含你自己**
6. 在开放平台「版本管理与发布」**创建版本并发布**——不发版权限不生效

### 3.5 平台侧（云端定时任务）

平台要读飞书的「日报」列，需要同样的三个凭据，在平台的密钥管理里建：

```bash
mavis secret create --name=FS_APP_ID     --value=<你的 app_id>
mavis secret create --name=FS_APP_SECRET --value=<你的 app_secret>
mavis secret create --name=FS_TABLE_ID   --value=appXXXXXXXX/tblYYYYYYYY
```

---

## 4. 使用方法

### 4.1 日常：什么都不用做

电脑开着、你已登录、网络通——晚上 **22:55** 自动出日报，
**23:00** 日报出现在会话里。

前提条件（详细说明见 [自动出日报的前提](#自动出日报的前提)）：

| 环节 | 条件 |
|---|---|
| 本机 22:55 | 电脑开着 + 你已登录（锁屏没关系，注销/睡眠/关机就错过） |
| | 网络通（连 MiniMax + 飞书） |
| | **微信不用开着**——密钥早注入缓存了；只有微信升级过才要开微信重跑 `setup` |
| 平台 23:00 | 定时任务启用 + 飞书三个凭据已配 |
| | 拉不到会等 3 分钟重试，最多 3 次（防本机还在跑就误报缺日报） |

### 4.2 手动跑一次

```bat
cd /d C:\wechat-daily\collector
daily.bat
```

补跑某一天：`python collector\daily.py --date 2026-11-20`

### 4.3 调整日报范围

**只看「我参与过的」聊天**（默认）：没回过的群、不关心的话题不进日报，
群里的闲聊也不会喂给模型。

```bat
cd /d C:\wechat-daily

REM 看当前设置
python collector\wechat4.py scope

REM 预览：今天到底哪些会话会进日报（跑一次导出，约十几秒）
python collector\wechat4.py scope --preview
```

```
  2026-11-22 的会话筛选预览
  --------------------------------------------------------
    [白] 拼单群          群聊   共 80 条 → 保留最近 50 条（白名单强制纳入，我没说话）
    [留] 读书会群        群聊   共 41 条 → 保留 6 条（我说了 1 条）
    [静] 今天没人说话的群  群聊   今天一条消息都没有（白名单群，今天没人发言）
  --------------------------------------------------------
    3 个会话进日报，0 个跳过
```

| 标记 | 含义 |
|---|---|
| `[留]` | 你发过言，按 `window` 裁剪上下文 |
| `[白]` | 白名单群，你没说话但群里有人说话，取最近 `include_max` 条 |
| `[静]` | 白名单群，**今天一条消息都没有**——也会进日报，写一句「XX 今天没有发言」 |
| 不出现 | 两样都不是，跳过 |

**改设置**（直接写进 `config.env`，不用开记事本）：

```bat
REM 上下文窗口：保留我发言前后 N 条（0=只留我自己的，-1=不裁剪）
python collector\wechat4.py scope --window 10

REM 全部会话都进日报
python collector\wechat4.py scope --scope all

REM 白名单：群名包含关键字即命中，写「读书会」能命中「XX读书会群」
python collector\wechat4.py scope --include "读书会群,家人群"

REM 白名单群里你不说话时，最多保留最近多少条（默认 50）
python collector\wechat4.py scope --include-max 80

REM 清空白名单 / 恢复默认
python collector\wechat4.py scope --clear-include
python collector\wechat4.py scope --reset
```

> 白名单群「今天没人发言也进日报」靠导出时多跑一次 `chatlog-keeper directory`
> （只列会话名，不读消息内容），产出 `EXPORT_DIR/wechat_directory.json`。
> 没这个文件时行为不变，不会凭空造会话。

### 4.4 命令速查

| 命令 | 作用 |
|---|---|
| `wechat4.py setup` | 一次性：装工具、恢复密钥、验证导出 |
| `wechat4.py export` | 导出最近两天的聊天记录 |
| `wechat4.py init-config` | 自动生成 `config.env`（探测微信数据目录） |
| `wechat4.py set KEY=VALUE ...` | 就地改配置，密钥不回显 |
| `wechat4.py set-table <网址>` | 从飞书网址解析 app_token/table_id |
| `wechat4.py set-clip KEY ...` | 从剪贴板读值写进配置 |
| `wechat4.py scope [--preview]` | 查看/调整日报范围 |
| `doctor.py [--write-test] [--llm-test]` | 自检：环境、飞书连通性、模型接口 |
| `run_daily.py --source <目录> [--summarize]` | 采集 → 归一化 → 推送（+摘要） |
| `normalize.py --date <日期> <来源...>` | 只做归一化 |
| `summarize_local.py --date <日期> [--push]` | 只出日报（可推飞书） |
| `bridge.py push/pull/push-report/pull-report --date <日期>` | 原始记录与日报两条独立通道 |
| `install_task.py install/status/uninstall` | 装/查/卸本机计划任务 |
| `make_sample.py --date <日期>` | 生成合成数据，用来演练 |

### 4.5 自检说「还差 N 项」

`doctor.py` 只读不写，会按依赖顺序列出缺什么、去哪补：

```
[OK]   Python 解释器  — 3.14.3
[OK]   config.env  — C:\wechat-daily\config.env
[OK]   微信数据根目录  — C:\Users\你的用户名\Documents\xwechat_files（db_storage 在 ...\wxid_xxxxxxxxxx\db_storage）
[OK]   消息库 — 15 个 message_*.db（最大的 biz_message_0.db）
[OK]   微信进程 — Weixin.exe 正在运行
[OK]   chatlog-keeper（解密导出）— C:\Users\你的用户名\.wechat_daily_tools\chatlog-keeper.exe
[~]    密钥恢复脚本 — 不在了（不影响每天跑）
[OK]   导出命令 EXPORT_CMD — "C:\Python314\python.exe" "collector\wechat4.py" export
[~]    导出目录 EXPORT_DIR — C:\Users\你的用户名\wx_export 还不存在（第一次导出时会自动创建）
[OK]   飞书凭据 — 已配置（表: appXXXXXXXX/tblYYYYYYYY）
[OK]   飞书连通性 — app_id / app_secret 有效
[OK]   飞书表格访问 — 读到 4 个字段：日期、消息数、内容、日报
[OK]   飞书表格字段 — 四列都在 ✓
[OK]   飞书写入 — 已写入一行测试日报
[OK]   飞书回读 — 内容一致 ✓
[OK]   清理测试行 — 已删除，表里不留东西
[OK]   本机摘要 API key — 已配置（模型 MiniMax-M3.1-Flash）
```

`[~]` 是提示（不影响），`[!!]` 才是真缺。

---

## 5. 输入输出示例

### 5.1 输入：解密导出的原始消息

`EXPORT_DIR/wechat_messages.json`——这是 chatlog-keeper 真实的输出格式
（字段名以它的源码为准，`normalize.py` 对每个字段都做了别名兼容）：

```json
[
 {
  "ts": 1794969000,
  "sender": "阿凯",
  "sender_wxid": "wxid_abc",
  "chat_room": "周末骑行群",
  "conversation_id": "12345678@chatroom",
  "content": "这周路线改成 40 公里环线，起点还是地铁站 B 口。",
  "msg_type": 1,
  "is_self": false,
  "is_group_chat": true
 },
 {
  "ts": 1794969060,
  "sender": "我",
  "sender_wxid": "wxid_me",
  "chat_room": "周末骑行群",
  "content": "行，我报名。",
  "msg_type": 1,
  "is_self": true,
  "is_group_chat": true
 }
]
```

也支持这些格式（自动识别，字段名有别名兜底）：

| 格式 | 样子 |
|---|---|
| chatlog 目录结构 | `<目录>/<会话名>/<日期>.json` |
| 扁平单文件 | `<会话名>.json` |
| CSV | 留痕 / WeChatMsg 导出 |
| Markdown 转录 | `[2026-11-18 09:12] 张三: 内容` |

`sample/` 目录里放的是「chatlog 目录结构」那种样例，一整套可以对着看。
**样例里的对话内容是完全虚构的**（读书会 / 骑行 / 咖啡店 / 摄影 / 家人），
不来自任何真实聊天记录，也不指向任何真实的人或机构。

### 5.2 中间产物：归一化 + 范围过滤后的 daily JSON

`data/<日期>.json`：

```json
{
  "date": "2026-11-18",
  "timezone": "Asia/Shanghai",
  "generated_at": "2026-11-22T22:59:45+08:00",
  "stats": {
    "message_count": 22,
    "message_count_total": 22,
    "chat_count": 5,
    "chat_count_total": 5,
    "skipped_chat_count": 0,
    "silent_chat_count": 0,
    "self_count": 10,
    "active_hours": [
      "09:12",
      "20:16"
    ],
    "peak_hour": 10
  },
  "chats": [
    {
      "chat_name": "周末骑行群",
      "chat_type": "group",
      "message_count": 6,
      "trimmed": false,
      "messages": [
        {
          "ts": 1794969000,
          "time": "2026-11-18 10:30:00",
          "hour": 10,
          "sender": "阿凯",
          "is_self": false,
          "type": "text",
          "content": "这周路线改成 40 公里 环线，起点还是地铁站 B 口。"
        },
        {
          "ts": 1794969060,
          "time": "2026-11-18 10:31:00",
          "hour": 10,
          "sender": "我",
          "is_self": true,
          "type": "text",
          "content": "行，我报名。"
        },
        {
          "...": "其余 4 条同结构"
        }
      ]
    }
  ]
}
```

### 5.3 输出：日报成品

`reports/<日期>.md`，同时写进飞书「日报」列：

```markdown
# 微信沟通日报 · 2026-11-18

> 今天的主线是两场周末活动定下了：读书会下午三点、骑行早八点；其余是日常闲聊。

## 关键进展

- **读书会时间地点已定**：周六下午三点，图书馆三楼会议室；我答应多带两本书备用。
- **骑行路线调整为 40 公里环线**：周六早八点地铁站 B 口集合，迟到不等，我说我七点半到。
- **咖啡店明早换豆**：换成耶加雪菲，风味偏花香。

## 待办与承诺

| 事项 | 承诺方 | 对方 | 时间点 | 来源 |
|---|---|---|---|---|
| 多带两本书去读书会备用 | 我 | 老周 | 周六下午三点前 | 读书会群 |
| 参加周末骑行，七点半前到地铁站 B 口 | 我 | 阿凯 | 周六早八点集合 | 周末骑行群 |
| 给新加入读书会的朋友留个位置 | 老周 | 我 | 未指定 | 读书会群 |

## 需要跟进

- **小林**：问现场能不能带饮品，还没回。

## 值得留意

- 新朋友想加入读书会，老周说人数已经报给管理员，需要确认是否还要多留一个位置。
- 骑行群里提到路上有段逆风，集合时间是早上八点，比较早。

## 日常往来

- **楼下咖啡店**：告知明天换成耶加雪菲豆子。
- **摄影爱好者群**：阿凯发了上周拍的 12 张片子，聊了第 7 张的构图。
- **家人群**：约了今晚八点回家吃饭。

## 沟通概览

- 消息 22 条 · 进日报 5 个会话 · 高峰 10 时
- 活跃时段：09:12 - 20:16
```

完整样例见 [`sample/2026-11-18.report.md`](sample/2026-11-18.report.md)，
对应的输入样例见 [`sample/`](sample/)。

### 5.4 飞书表里长什么样

| 日期 | 消息数 | 内容 | 日报 |
|---|---|---|---|
| 2026-11-22 | 22 | `{"date":"2026-11-22","stats":{...},"chats":[...]}` | `# 微信沟通日报 · 2026-11-22` … |

「内容」列是原始记录（只有本机和这张表能看到），
「日报」列是成品（**平台云端只读这一列**）。

### 5.5 云端定时任务拿到什么

23:00，平台侧执行：

```bash
cd <项目目录> && python3 collector/bridge.py pull-report --date 2026-11-22
```

→ 从飞书「日报」列读出成品，写到 `reports/2026-11-22.md`，
→ 在会话里**原样呈现**（不改写、不精简、不重新排版）。

---

## 附：微信 4.x 的加密为什么这么难

原来的首选工具 **chatlog（sjzar/chatlog）已在 2025-10-20 因微信官方函件删库**，
代码和提交历史全清空了。别再找它了。

| | 微信 3.x | 微信 4.x |
|---|---|---|
| 进程名 | `WeChat.exe` | `Weixin.exe` |
| 数据目录 | `WeChat Files\<wxid>\Msg\Multi\MSG*.db` | `xwechat_files\<wxid>\db_storage\message\message_*.db` |
| 加密 | SQLCipher 3（PBKDF2-SHA1 64000 轮） | **SQLCipher 4**（原生 32 字节 key，PBKDF2-SHA512 256000 轮，HMAC-SHA512） |
| 密钥 | 全局一把 | **每个库一把**（salt 各不相同） |
| 取密钥 | 扫内存拿明文 | 4.1.10.31 之后是 XOR 混淆态，得反推 |
| 消息表 | 单张 message 表 + talker 列 | **每个会话一张表** `Msg_<md5(username)>` |

所以 3.9 时代的工具在 4.2 上基本都不 work。

本项目自带的链路（已在你机器上实测跑通：1184 条 / 2 天 / ~9 秒）：

```
scan.py        静态扫 Weixin.dll 的 .text 段，拿 XOR 常量（按版本自动重提特征）
key_v4.py      只读扫微信进程内存 → 混淆态候选 → XOR 还原成 passphrase
               → 对每个库的 page1 算 HMAC 验真（唯一判据，不靠猜）
   ↓
chatlog-keeper set-key --key-stdin   注入密钥
chatlog-keeper wechat --days 2       导出 JSON
chatlog-keeper directory              列出全部会话（白名单「今天没人发言」靠它）
```

**关于 wxq**：它的扫描器只找明文 `x'<64位hex>'` keyspec，
而 4.1.10.31+ 的密钥是 XOR 混淆态 → 内存里没有明文 → **必然 0 命中**。
所以 4.2 上不要用 wxq。

**其他可选工具**（最终只要吐出 JSON/JSONL/CSV/MD，归一化层都能吃）：

| 工具 | 验证过的微信版本 | 输出 |
|---|---|---|
| [anpingjing/wechat-chatlog-decrypt-export](https://github.com/anpingjing/wechat-chatlog-decrypt-export) | 4.1.13.12 | `wechat_messages.json` + HTML |
| [yuerhoo/wechat_chatlog_decrypt_exporter](https://github.com/yuerhoo/wechat_chatlog_decrypt_exporter) | **4.2.0.13** | Excel |
| [labazhou2024/chatlog-keeper](https://github.com/labazhou2024/chatlog-keeper) | ≤ 4.0.x | `wechat_messages.json` + HTML |
| [vpcoderli/wxq](https://github.com/vpcoderli/wxq) | 4.0 及更早 | CLI / MCP |

换工具时改 `config.env` 的 `EXPORT_CMD` 就行，后面的代码一行不用动。

> 取密钥一般在**微信已登录并运行**时做一次，密钥会缓存；之后每天的导出很快，
> **微信不用开着**。只读内存扫描需要管理员权限。

---

## 配置项速查

`config.env`（项目根目录，不在包内，所以升级不会丢）：

```ini
# ---------- 微信数据位置 ----------
WECHAT_DATA_DIR=C:\Users\你的用户名\Documents\xwechat_files

# ---------- 解密导出工具 ----------
EXPORT_CMD="C:\Python314\python.exe" "collector\wechat4.py" export
EXPORT_DIR=C:\Users\你的用户名\wx_export

# ---------- 飞书中转 ----------
WEIXIN_BRIDGE=feishu
FS_APP_ID=cli_xxx
FS_APP_SECRET=xxx
FS_TABLE_ID=appXXXXXXXX/tblYYYYYYYY
FS_FIELD_DATE=日期
FS_FIELD_MSG=消息数
FS_FIELD_PAYLOAD=内容
FS_FIELD_REPORT=日报

# ---------- 本机摘要 ----------
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.minimax.cn/v1
LLM_MODEL=MiniMax-M3.1-Flash
LLM_EFFORT=medium

# ---------- 日报范围 ----------
DAILY_SCOPE=mine
DAILY_CONTEXT_WINDOW=5
DAILY_INCLUDE_CHATS=
DAILY_INCLUDE_MAX=50
```

## 自动出日报的前提

**本机侧（22:55）**

| 条件 | 说明 |
|---|---|
| 电脑开着 + 你已登录 | 锁屏**没关系**；注销 / 睡眠 / 关机就错过 |
| 网络通 | 要连 MiniMax（生成摘要）+ 飞书（推结果） |
| 微信**不用**开着 | 密钥早就注入并缓存了；只有微信升级过才需要开微信重跑 `setup` |

> 任务开了「错过后补跑」：22:55 没开机，之后开机也会补跑一次。
> 也开了「电池上也跑」：笔记本插不插电都能跑。

**平台侧（23:00）**

- 定时任务处于启用状态
- 飞书三个凭据已配置
- 拉不到时会等 3 分钟重试，最多 3 次

---

## 目录结构

```
wechat-daily/
├── collector/
│   ├── wechat4.py              微信 4.2 解密链路 + 配置命令（setup/export/set/scope…）
│   ├── _yara_shim.py          yara-python 的纯 Python 替身（Python 3.14 无 wheel）
│   ├── setup.bat              一次性设置入口（纯 ASCII）
│   ├── doctor.py / doctor.bat 自检：环境 / 飞书 / 模型接口
│   ├── daily.py / daily.bat   编排入口：跑导出命令 → 归一化 → 出日报
│   ├── collect.sh             macOS / Linux 入口
│   ├── run_daily.py           采集 → 归一化 → 推送（--summarize 顺带出日报）
│   ├── normalize.py           归一化：JSON/JSONL/CSV/Markdown → 统一 daily JSON
│   ├── summarize_local.py     本机调 OpenAI 兼容接口生成日报（自动分块）
│   ├── bridge.py              桥接：github/feishu/http，原始与日报两条独立通道
│   ├── install_task.py / .bat Windows 计划任务（走 XML，开补跑 + 电池可跑）
│   ├── run_daily_logged.bat   计划任务执行的包装脚本（写 wechat_daily.log）
│   ├── setup_wxq.py / .bat    备选：微信 4.0 及更早的 wxq 链路
│   ├── export_via_wxq.py      wxq 输出适配器
│   ├── fetch_chatlog_api.py   可选：走 chatlog 系分支的 HTTP API 采集
│   └── make_sample.py         生成合成数据，用来演练
├── data/                      每日原始数据（YYYY-MM-DD.json）
├── reports/                   日报成品（YYYY-MM-DD.md）
├── sample/                    输入/输出样例
├── prompts/daily_report.md    日报生成规范（结构 + 硬规则）
├── tests/                     11 套自测 + 假后端
├── config.env.example         配置模板
├── .gitignore                 挡住 config.env / 日志 / 运行时产物
├── LICENSE                    MIT
└── README.md
```

---

## 排查

| 现象 | 看哪里 |
|---|---|
| 取不到密钥 | 微信必须已登录并运行；4.1.10.31+ 是 XOR 混淆态 |
| 扫描 0 命中 | 密钥没驻留内存：小号/空账号常见；用主账号试 |
| 导出命令报找不到文件 | `EXPORT_DIR` 和工具的 `--out` 要指同一个目录 |
| 归一化说「解析出 0 条」 | 报错里会打印文件结构（字段名+类型），加个别名即可 |
| 消息条数比微信少 | 4.x 每会话一张表，部分会话类型（文件/转账/引用）解析失败会被跳过 |
| 飞书「内容」列空的 | `FS_*` 配错，或应用没被加为表格的「可管理」文档应用 |
| 飞书「日报」列空的 | 本机摘要失败，看 `wechat_daily.log`；多半是 key 或额度问题 |
| `401 (1004)` | key 和域名不匹配（国内/国际），让程序自动探测或手动改 `LLM_BASE_URL` |
| `402 (1008)` | 拿错凭据：`sk-api-` 要余额，`sk-cp-` 要订阅额度 |
| 平台会话说没拿到日报 | 平台侧密钥没建；或本机 22:55 没跑成（看 `wechat_daily.log`） |

---

## 自测

```bash
python tests/test_normalize.py      # 归一化：多种导出格式、字段别名、时区、会话名推断
python tests/test_chatlog_api.py    # 采集层：对假 chatlog 服务跑通
python tests/test_summarize.py      # 本机摘要：渲染、分块、假模型端到端
python tests/test_pipeline.py       # 全链路：采集 → 推送 → 摘要 → 平台拉取
python tests/test_daily.py          # 编排层：导出命令 → 归一化 → 日报
python tests/test_scope.py          # 范围：只留我参与过的 + 裁剪 + 白名单 + 静默会话
python tests/test_install_task.py   # 计划任务 XML：补跑、电池、静默等开关
python tests/test_export_wxq.py     # wxq 输出适配
python tests/test_bridge_save_env.py# 配置就地改写不丢注释
python tests/test_packaging.py      # 打包层：.bat 纯 ASCII、Python 可编译、包内容完整
python tests/test_wechat4.py        # 4.2 链路：路径探测、工具下载、命令契约
```

先不花钱验证的话，用合成数据：

```bash
python collector/make_sample.py --date 2026-11-18
python tests/test_pipeline.py
```

---

## 隐私

原始聊天记录会进飞书多维表格的「内容」列——这是你选的中转方式。除它之外，记录不出你的电脑：
摘要在本机生成，平台云端只接触日报成品。不想要飞书里存原始记录，把推送关掉即可
（`run_daily.py --no-push`），日报仍然会正常生成在本机 `reports/`。
