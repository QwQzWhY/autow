# 部署说明

整个过程只有一次配置，大约 15 分钟。配好之后：**电脑关机不影响**，官网通知每小时自动抓一次，
QQ 群里 @全体成员 的消息大约一分钟内出现在待办里，全程显示在手机上。

```
GitHub Actions（云端）                         你的手机
├─ 每小时抓学院 + 学校官网 ──┐  scan.yml
├─ 收到 QQ 消息立刻触发 ─────┤  qq.yml ──> 大模型抽取 ──> docs/data/todos.json
└─ 提交回仓库 ───────────────┘                                │
                                            GitHub Pages 托管
                                                  │
                                            待办 PWA（加到主屏）
```

## 第一步：建仓库

1. 在 GitHub 上新建一个**公开**仓库，名字随意，比如 `autow`。

   > 为什么必须是公开的：GitHub Pages 免费版只给公开仓库用。通知本身是公开信息，
   > QQ 消息我们已经让大模型只输出"要做什么"，不复述群名和人名。不接受的话看文末
   > 「用私有仓库」一节。

2. 把这个项目推上去：

   ```bash
   cd AutoW
   git add -A
   git commit -m "初始化"
   git branch -M main
   git remote add origin https://github.com/<你的用户名>/autow.git
   git push -u origin main
   ```

## 第二步：配大模型

去仓库的 **Settings → Secrets and variables → Actions → New repository secret**，
加三个 secret：

| 名称 | 值 |
|---|---|
| `LLM_API_KEY` | 你的大模型 API Key |
| `LLM_BASE_URL` | 接口地址，见下表 |
| `LLM_MODEL` | 模型名，见下表 |

下面三个都对外兼容 OpenAI 格式：

| 服务商 | `LLM_BASE_URL` | `LLM_MODEL` |
|---|---|---|
| DeepSeek（推荐，便宜） | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |

> GitHub 的服务器在美国，上面这些国内厂商的 API 都能直接访问。

**没有 key 也能跑**，会自动退回规则抽取，只是准确率差一些、容易漏掉隐含的截止时间。

## 第三步：开启写入权限

Actions 需要能提交结果回仓库：

**Settings → Actions → General → Workflow permissions** → 选
**Read and write permissions** → Save。

## 第四步：开启 Pages

**Settings → Pages**：

- Source 选 `Deploy from a branch`
- Branch 选 `main`，目录选 **`/docs`**
- Save

等一两分钟，你的待办页面就在 `https://<你的用户名>.github.io/autow/`。

## 第五步：跑第一次

**Actions → 扫描通知 → Run workflow**。

跑完去看 **docs/data/todos.json** 有没有变化——有内容就说明整条链路通了。

## 第六步：装到手机

用手机浏览器打开 `https://<你的用户名>.github.io/autow/`，
然后「添加到主屏幕」。之后打开就像个原生 App，勾选状态存在手机本地。

---

## QQ 群消息怎么进来

安卓的「通知使用权」能读到 QQ 弹出来的通知。**@全体成员 时 QQ 一定会弹通知**，
所以这条路能覆盖你最关心的那类消息。它不碰 QQ 协议、不登录你的账号，主号零风险。

要连管理员发言，就在 QQ 里把那几个人设成「特别关注」，让他们的消息也弹通知。

### 方案 A：MacroDroid（推荐，不用编译任何东西）

1. 应用商店装 **MacroDroid**（免费）。
2. 新建宏 → 触发器选 **通知 → 收到通知**，配置：
   - 应用：QQ
   - 内容包含：@全体成员
3. 加动作 **网络 → HTTP 请求**，POST：
   - URL：`https://api.github.com/repos/<用户名>/<仓库名>/dispatches`
   - Header 三条：
     ```
     Authorization: Bearer <你的 PAT>
     Accept: application/vnd.github+json
     Content-Type: application/json
     ```
   - Body：
     ```json
     {"event_type":"qq-message","client_payload":{"group":"[notification_title]","sender":"","text":"[notification_text]","time":"[year]-[month]-[day]T[hour]:[minute]"}}
     ```
4. 保存。之后每逢 @全体成员，几秒内待办里就会多一条（前提是大模型判断它需要行动）。

`[notification_title]` 这类是 MacroDroid 的魔法文本变量，在下拉列表里能直接选。

### 方案 B：编译自带的通知桥 APK

仓库里带了 `android/`，是个只读通知、不做别的事的小应用。

1. **Actions → 构建通知桥 APK → Run workflow**。
2. 跑完在这次的 Artifacts 里下载 `autow-bridge-apk`。
3. 手机上安装（需要允许安装未知来源）。
4. 打开 App，填三样：仓库名、PAT、关键词。
5. 点「① 开启通知使用权」，在系统设置里勾上它。
6. 点「② 发一条测试请求」，回 GitHub 看 Actions 有没有被触发。

### PAT 怎么建

**Settings → Developer settings → Personal access tokens → Fine-grained tokens**：

- Repository access：只选这一个仓库
- Permissions → **Contents: Read and write**（`repository_dispatch` 需要这个）
- 有效期建议 90 天

---

## 隐私

- 待办页面公开可访问，只是 URL 不为人知。**不要往里放私人信息。**
- 给大模型的 QQ 消息提示词里明确要求：不复述群名、人名和闲聊内容，只输出要做什么。
- QQ 通知桥只读系统通知，不接触聊天记录，不登录你的 QQ。
- GitHub token 存在手机的应用私有目录里。

## 用私有仓库

私有仓库 + 免费账号没有 Pages。两个折中办法：

- 仓库设成公开，但让 `autow/publish.py` 只输出摘要，不输出原文。
- 或者不要网页看板，改成推送：在 workflow 最后加一步，用 Bark / Server 酱
  把当天的待办推到手机。

## 常见问题

**抓不到通知，日志里全是 412？**
说明 WAF 拦了。这个站有反爬，云端机房 IP 可能比家里 IP 更严。如果 Actions 里一直失败，
就把 workflow 里的 `python -m autow run` 留给你自己的电脑在开机时跑。

**Actions 报权限错误？**
回到第三步，确认 Workflow permissions 是 Read and write。

**待办不准？**
换个更强的模型（`LLM_MODEL` 改成 `deepseek-reasoner` 或 `glm-4-plus`）。抽取结果按消息内容
缓存，换模型后会自动重新分析。

**想手动跑一次？**
Actions → 扫描通知 → Run workflow。
