# TaskDeck · 定时任务管理器

一个轻量、开源的 Windows 桌面应用，用来管理多个定时任务。任务本质是「在终端里执行一条命令」——可以定时跑 Python 脚本、定时抓数据、定时推送钉钉消息。

关掉窗口不会退出程序，它会缩到系统托盘继续按时执行。

---

## 快速开始

```bat
安装依赖.bat      :: 首次使用，创建虚拟环境并安装依赖
启动 TaskDeck.bat :: 双击启动（无黑框，窗口 + 托盘）
```

命令行方式：

```bash
python app.py               # 启动桌面窗口
python app.py --hidden      # 启动后直接驻留系统托盘
python app.py --no-gui      # 只起本地服务，用浏览器访问 http://127.0.0.1:17823
python app.py --port 18000  # 指定端口
```

自检脚本（需要先启动服务）：

```bash
python app.py --no-gui --port 17999
python tests/smoke_test.py
```

---

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 三种调度 | 固定间隔（秒/分/时/天）、Cron 表达式（分 时 日 月 周）、单次定时 |
| 任意命令 | 任何终端命令；可选经 Shell 执行或直接执行 |
| 实时输出 | 运行中的 stdout/stderr 实时回传，stderr 标红显示 |
| 超时终止 | 到点强制结束进程**及其子进程树**，不会出现杀不掉的孤儿进程 |
| 失败重试 | 自定义重试次数与重试间隔，每次尝试独立留痕 |
| 环境变量 | 每个任务独立注入环境变量，笔记密码 / Token 不用写进命令 |
| 运行历史 | 每次运行的开始时间、耗时、退出码、完整输出全部留存，可回看 |
| 钉钉通知 | 任务结束推送到钉钉群，可选「仅失败时通知」 |
| 托盘常驻 | 关闭窗口 = 最小化到托盘，定时任务不受影响 |
| 并发保护 | 同一任务不会重复并发执行 |

---

## 用模板 3 步搭一个「百度热搜 → 钉钉」

1. 点右上角 **模板库** → 选「百度热搜 → 钉钉推送」
2. 填 Webhook（和加签密钥，如果机器人安全设置是「加签」），设置每天 9:00
3. 点 **创建任务**，然后选中它点 **立即运行** 验证

内置三个模板，脚本都在 `scripts/` 目录，**零第三方依赖**（只用标准库），可以直接改：

| 模板 | 脚本 | 说明 |
| --- | --- | --- |
| 百度热搜 → 钉钉 | `scripts/baidu_hot_dingtalk.py` | 抓热搜榜 Top N 推送到群，支持关键词/加签 |
| 运行 Python 脚本 → 钉钉 | `scripts/run_script_dingtalk.py` | 跑你自己的脚本，把输出推到群，默认仅失败推送 |
| 接口健康巡检 → 告警 | `scripts/health_check_dingtalk.py` | 定时请求 URL，状态码异常或响应过慢就告警 |

模板脚本都可以单独在终端里跑，方便调试：

```bash
python scripts/baidu_hot_dingtalk.py --topn 5 --dry-run   # 只打印不推送
python scripts/health_check_dingtalk.py --url https://www.baidu.com
```

### 钉钉机器人怎么拿 Webhook

群设置 → 智能群助手 → 添加机器人 → 自定义 → 复制 Webhook 地址。

安全设置三选一，应用都支持：

- **加签**：复制 `SEC...` 填到「加签密钥」
- **自定义关键词**：填 `热搜`（模板里可改 `DINGTALK_KEYWORD`）
- **IP 段**：留空密钥即可

---

## 目录结构

```
task-manager/
├── app.py                 # 桌面应用入口（窗口 / 托盘 / 服务）
├── requirements.txt
├── 安装依赖.bat
├── 启动 TaskDeck.bat
├── core/
│   ├── config.py          # 路径与端口
│   ├── store.py           # SQLite：任务表 + 运行记录表
│   ├── runner.py          # 执行引擎：子进程、实时输出、超时终止、重试
│   ├── scheduler.py       # APScheduler 封装 + 调度描述
│   ├── notify.py          # 钉钉推送
│   ├── templates.py       # 内置模板定义
│   └── server.py          # 本地 HTTP API
├── web/
│   ├── index.html
│   ├── style.css
│   └── app.js
├── scripts/               # 模板脚本（可直接编辑）
├── assets/                # 运行时生成的图标
├── data/                  # 运行时生成：taskmanager.db
└── tests/smoke_test.py    # 端到端自检
```

---

## 常见问题

**中文输出乱码？**
执行引擎会按 `UTF-8 → GBK → 系统编码` 自动解码，并对子进程预设 `PYTHONIOENCODING=utf-8` 和 `PYTHONUTF8=1`。如果个别程序仍乱码，给任务加环境变量 `PYTHONIOENCODING=gbk`。

**任务到点没跑？**
确认开关是启用状态、列表里显示了「下次」时间。程序退出期间错过的任务，重启后 1 小时内会补跑一次（`misfire_grace_time=3600`）。

**想开机自启？**
给「启动 TaskDeck.bat」建快捷方式，放进 `Win + R` → `shell:startup` 目录。

**数据存在哪？**
`data/taskmanager.db`（SQLite）。每个任务默认保留最近 200 条运行记录，超出自动清理。

**想用浏览器管理？**
`python app.py --no-gui`，然后访问 `http://127.0.0.1:17823`。

---

## 技术栈

Python 3 · pywebview（原生窗口，基于 Edge WebView2）· APScheduler（调度）·
Flask（本地 API）· SQLite（存储）· pystray（系统托盘）· 原生 HTML/CSS/JS（界面，无构建步骤）
