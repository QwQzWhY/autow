# AutoW — 学校通知和 QQ 群消息 → 待办

把两个官网的通知公告（学院 `ai.lzu.edu.cn`、学校 `news.lzu.edu.cn`），
以及 QQ 群里 @全体成员 和管理员的消息，自动抓下来、交给大模型判断哪些需要你行动、
什么时候截止，汇总成一份待办清单，显示在手机主屏上。

**电脑不需要常开，也不需要服务器**——采集跑在 GitHub Actions 上。

## 它怎么工作

```
GitHub Actions（云端，每 2 小时）              你的手机
├─ Playwright 抓学院 + 学校官网 ──┐
├─ 收 QQ 群消息触发 ──────────────┤──> 大模型抽取 ──> docs/data/todos.json
└─ 结果提交回仓库 ────────────────┘                        │
                                             GitHub Pages
                                                  │
                                             待办 PWA（主屏图标）
```

部署步骤在 [docs/SETUP.md](docs/SETUP.md)，一次性配置约 15 分钟。

## 为什么爬虫要写得这么小心

`ai.lzu.edu.cn`、`www.lzu.edu.cn`、`news.lzu.edu.cn` 都在 JS 挑战型 WAF（瑞数）后面，实测下来：

| 方式 | 结果 |
|---|---|
| `curl` / `requests` | 412，只有一段混淆脚本，拿不到正文 |
| `curl_cffi`（模拟 Chrome TLS 指纹） | 同样 412，**必须真正执行 JS** |
| 无头 Chrome（默认） | 空的 412，直接被拒 |
| 无头 Chrome（换 UA + 去掉自动化标志） | 拿到完整页面 |

关于限流，得纠正一句我早先的判断：真正让我连续失败的并不是访问太快。我当时把浏览器配置
持久化下来想减少验证次数，结果配置里的 Cookie 失效后，站点会对**整个配置**返回空的 412，
换一个全新会话立刻恢复。所以那不是 IP 层面的封禁。不过爬虫仍然刻意放慢、并且只在有新通知时
才打开正文页——对一个学校服务器客气一点没有坏处。

好消息是站点是 Nuxt SSR，列表和正文都在 HTML 里，过了挑战就能直接用 DOM 读全。

## 它能多快

| 来源 | 延迟 | 为什么 |
|---|---|---|
| QQ 群消息 | 约 1 分钟 | 事件驱动：QQ 一弹通知就立刻触发云端任务 |
| 学院通知 / 学校通知 | 1 小时 | 定时轮询，这两个站没有推送机制 |

QQ 那条能做到准实时，是因为它不需要浏览器：处理消息的 workflow 不装任何依赖（有测试守着
这条约束），从消息发出到出现在看板上通常不超过一分钟。官网那条受限于轮询周期，想更密就改
`scan.yml` 里的 cron。

手机看板开着的时候每 60 秒自己拉一次数据，切回前台也会立刻拉，所以云端更新完你基本不用手动刷新。

## 抽取：规则 + 大模型

- 配了 `LLM_API_KEY` 就走大模型，输出结构化 JSON（要不要行动、做什么、什么时候截止、优先级）。
- 没配或调用失败，自动退回 `autow/extract.py` 里的规则版，流程不会断。
- 结果按消息正文缓存，重复运行不重复花钱；换模型会自动重新分析。

## 代码结构

| 路径 | 作用 |
|---|---|
| `autow/browser.py` | 反检测浏览器会话（每次全新，不复用配置） |
| `autow/sources/lzu_ai.py` | 学院官网适配器：通知列表 + 正文 |
| `autow/sources/lzu_news.py` | 学校新闻网适配器（`news.lzu.edu.cn/tzgg.htm`） |
| `autow/dates.py` | 中文日期解析（`10月09日` 这种没年份的） |
| `autow/extract.py` | 规则版抽取，也是大模型不可用时的兜底 |
| `autow/llm.py` | 大模型抽取（任意 OpenAI 兼容接口，标准库实现，无 SDK 依赖） |
| `autow/store.py` | SQLite 存储与去重 |
| `autow/publish.py` | 生成看板用的 `docs/data/todos.json` |
| `autow/cli.py` | 命令行入口 |
| `docs/` | 手机看板（PWA），GitHub Pages 从这里发布 |
| `android/` | QQ 通知桥：只读系统通知，不碰 QQ 协议 |
| `tools/` | 开发用的侦察、预览、图标生成脚本 |

## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 复用本机已装的 Chrome，不用另外下载浏览器
$env:LLM_API_KEY = "sk-..."          # 可选，不配就走规则版
.\.venv\Scripts\python.exe -m autow run
.\.venv\Scripts\python.exe -m autow todos -v

# 不用真实 API 也能验证整条链路
$env:AUTOW_LLM_MODE = "mock"
```

`python -m autow --help` 有全部子命令。

## 已知限制

- 覆盖学院通知公告（第一页 10 条）和学校新闻网通知公告（一页 20 条，没有翻页）。
  教务处、研究生院等二级站还没接。
- 学校通知是全校范围的，一半以上是招聘、招标、后勤这类与学生无关的事，
  靠大模型过滤。没配 API key 时规则版过滤得不准，会比较吵。
- QQ 那条路依赖 QQ 弹出的通知：消息被折叠或超长时，拿到的是截断版本。
- 待办的勾选状态存在手机本地，换设备不同步。
- 云端机房 IP 有可能被学校 WAF 拦得更严——这一点我没法在这里验证，
  [docs/SETUP.md](docs/SETUP.md) 的常见问题里写了退路。
