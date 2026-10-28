# mteam-keepalive

> 用无头浏览器每天自动登录一次 PT 站，防止账号因「长期未登录」被清理。

[![bilibili](https://img.shields.io/badge/Bilibili-主页-00A1D6?logo=bilibili&logoColor=white)](https://space.bilibili.com/2250922)
[![macOS](https://img.shields.io/badge/macOS-Apple%20Script-informational?style=flat-square)](https://www.apple.com/macos/)
[![Python](https://img.shields.io/badge/Python-3.8%2B-blue?style=flat-square)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)

## 为什么需要这个

M-Team（馒头）这类私有种子站有条闲置清理机制：

| 账号状态 | 规则 |
|---|---|
| 未封存账号 | **连续 40 天不登录即删除**，无宽限期，立刻生效 |
| 已封存账号 | 连续 90 天不登录即删除 |
| 最高等级（府尹 / Extreme User） | 永久保留，不受此规则约束 |

坊间流传的「45 天」是错的，40 天为准。不同等级有豁免。

> 规则可能随站点调整，以站点规则页为准。本项目只保持登录状态，不涉及任何资源获取。

更麻烦的是，**挂着种子做种不算登录**。论坛里被删号的案例几乎都是同一个剧本：

> 「这段时间忙一直没登录，NAS 也关了，回来发现账户没了。」

出国、停电、NAS 迁移、账号级别不够高——一次疏忽，几年的攒种和分享率全归零。

这项目做的事就一句话：**每天凌晨自动开浏览器，登录，随机翻几个页面，走人。** 一天一次，一次一到三分钟。

## 特性

| 特性 | 说明 |
|---|---|
| 免验证码登录 | 复用日常浏览器的会话令牌（JWT），令牌有效时不触发 2FA |
| 自动 2FA | 令牌过期就走账号密码 + 邮箱验证码，从 IMAP 抓码自动填 |
| 同一会话内完成 2FA | 浏览器停在 2FA 页等码，不重开（重开会让码失效）|
| 拟人化 | 打字随机延迟、点击随机停顿、页面间随机漫游，不走固定路径 |
| 防休眠错过 | 三个触发点 + 幂等打标，一天只跑一次 |
| 异常自愈 | 代理不通降级直连、发码限流退避、发信失败落盘 |
| 独立运行时 | Node 私有副本放项目目录，不污染系统，卸载无残留 |
| 每日邮件报告 | 执行结果、访问页面列表、异常原因发到指定邮箱 |

## 怎么工作

```
launchd 定时触发（05:10 / 09:30 / 20:00）
   │
   ▼
run_task.sh ─── 幂等检查（同日已成功则跳过）+ 时间窗口检查
   │
   ▼
mteam_visit.py ─── 自检 → 同步登录态 → 调浏览器 → 出报告 → 发信
   │                    │
   │                    └─ sync_auth.py：从日常浏览器复制 localStorage（JWT）
   ▼
browser_runner.cjs (Node + puppeteer-core)
   ├── 打开站点，判断是否已登录
   ├── 需要登录 → 填账号密码 → 2FA 等码 → 提交
   │                ▲
   │mail_tool.py ────┘（Python 侧后台线程 IMAP 取码写入文件，浏览器轮询读取）
   └── 随机漫游 6-10 个站内页面
```

### 为什么必须复用登录态

目标站点（以 M-Team 为例）**不写 cookie，把 JWT 存在 localStorage**。这带来两个结论：

1. **只查 cookie 库会误判「站点没存登录态」**——这是开发时最大的坑。Cookies 表命中 0 条，不代表没有会话。
2. **复制 `Local Storage` 目录 + `Local State` 就能免验证码登录**，因为 `Local State` 里带着解密密钥来源。

`sync_auth.py` 干的就是这件事。令牌约 7 天有效期，过期后自动退回「账号密码 + 邮箱验证码」；想恢复免验证码，在日常浏览器登录一次就行。

## 装

要求：macOS（依赖 launchd）、Python 3.8+（只用标准库）、Edge 或 Chrome、一个支持 IMAP + SMTP 的邮箱。

```bash
# 1. 安装（下载私有 Node + puppeteer-core，生成 plist 并加载 launchd 任务）
chmod +x install.sh run_task.sh uninstall.sh
./install.sh

# 2. 编辑配置
vi config.json
```

`config.json` 关键字段：

```jsonc
{
  "site_url": "https://kp.m-team.cc/index",
  "site_user": "你的用户名",
  "site_pass": "你的密码",

  "mail_user": "你的邮箱",
  "mail_auth_code": "邮箱授权码",   // 不是登录密码，是 IMAP/SMTP 授权码
  "report_to": "你的邮箱",

  "proxy": "",                       // 要代理才填，如 http://127.0.0.1:7890；直连留空
  "headless": true                   // false 能看到浏览器窗口，调试用
}
```

授权码怎么拿：QQ 邮箱 → 设置 → 账户 → 开 IMAP/SMTP → 生成 16 位授权码。

```bash
# 3. 先在日常浏览器登录一次站点（建立可同步的登录态），再手动跑一次
./sync_auth.py
./run_task.sh
tail -60 logs/launchd.log
```

看到 `登录成功` → `漫游 N页` → `报告已发送`，就成了。

## 运维

```bash
# 立即触发一次
launchctl kickstart gui/$(id -u)/com.keepalive.mteam.visit

# 看定时任务状态
launchctl list | grep keepalive

# 看最近一次报告
tail -60 logs/launchd.log
cat "$(ls -t logs/report-*.txt | head -1)"

# 卸载定时任务（保留配置和数据）
./uninstall.sh
```

### 目录

| 文件 / 目录 | 作用 |
|---|---|
| `mteam_visit.py` | 主控：自检 → 调度 → 重试 → 出报告 |
| `browser_runner.cjs` | 浏览器驱动：登录、2FA、随机漫游 |
| `mail_tool.py` | IMAP 抓码 / SMTP 发信（仅标准库）|
| `sync_auth.py` | 同步日常浏览器登录态（免验证码的关键）|
| `run_task.sh` | launchd 入口，含幂等与时间窗口保护 |
| `set_auth_code.py` | 授权码配置助手 |
| `config.json` | 你的凭据，已被 .gitignore 忽略 |
| `runtime/` | 私有 Node 运行时 |
| `node_modules/` | 私有 puppeteer-core |
| `profile/` | 独立浏览器配置，不碰日常浏览器 |
| `logs/` | 日志、每日报告、幂等打标 |

## 参数

| 字段 | 默认 | 说明 |
|---|---|---|
| `min_pages` / `max_pages` | 6 / 10 | 每次随机访问的页面数 |
| `min_seconds` / `max_seconds` | 60 / 180 | 每次任务总时长（秒）|
| `max_retry` | 3 | 失败重试轮数，退避 5 / 10 分钟 |
| `headless` | true | 无界面运行，false 可观察过程 |
| `proxy` | 空 | 本地代理，如 `http://127.0.0.1:7890`，不通自动降级直连 |
| `sender_keywords` | m-team 相关 | 邮件筛选关键词，换站点改这里 |
| `login_path` | `/login` | 登录页路径特征，已内置 |
| `pages` | 内置列表 | 漫游页面清单，换站点改这里 |

换站点只改 `config.json` 三处，不动代码：`site_url` / `login_path`、`pages` 数组（`u` 是相对路径，`label` 是报告里显示的名字）、`sender_keywords`。

## 踩坑记录

<details>
<summary><b>1. React / Vue 拒收 DOM 合成点击</b></summary>

`element.click()` 在 Ant Design 页面上**完全不触发**请求。必须用真实鼠标事件：

```js
const box = await el.boundingBox();
await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
```

改用真实点击后浏览器才发出 POST 并返回 200。
</details>

<details>
<summary><b>2. 发码有静默限流</b></summary>

短时间内多次请求「发送验证码」，**接口返回 200，但邮件根本不投递**（收件箱和垃圾箱都没有）。

应对：重试间隔拉到 5 / 10 分钟（脚本已实现）；排查时不要反复重试，先等冷却。定时任务间隔数小时，天然规避。
</details>

<details>
<summary><b>3. 取码必须按邮件时间排序</b></summary>

按 IMAP 返回顺序取会拿到旧验证码。正确做法是解析每封的 `Date` 头，只取时间窗内**最新**那封。
</details>

<details>
<summary><b>4. 重开浏览器会让验证码作废</b></summary>

取到码后重启浏览器 = 全新会话，码已失效。正确做法：浏览器停在 2FA 页等，Python 把码**写入文件**，浏览器轮询后原地填入。
</details>

<details>
<summary><b>5. 「获取验证码」按钮初始 disabled</b></summary>

必须先填邮箱字段才能点（表单联动）。
</details>

<details>
<summary><b>6. 误判「站点不存登录态」—— 只查 cookie 是错的</b></summary>

**本项目最大的误判。**

查浏览器 Cookies 库（1500+ 条）发现目标站点命中 0 条，curl 访问会员接口又返回 302，于是判定「站点不签发可转移会话凭据，必须每次发码」。

**真相**：站点用 localStorage 里的 JWT 认证，压根不写 cookie。

**教训**：排查登录态必须同时查 cookie 和 localStorage；判断要靠「实际跑一次」，不能只看存储文件。
</details>

<details>
<summary><b>7. `Default/Default/` 是历史备份目录</b></summary>

浏览器用户数据下存在同名的历史日期目录，搜索时容易当成当前 profile。确认路径看修改时间。
</details>

<details>
<summary><b>8. 会话 Cookie 表在运行时可能滞后</b></summary>

Cookies 文件是刷盘写入的，运行中读可能滞后。另外新版 Chromium 把历史从 `urls` 表拆到了独立的 `History` 文件，查历史要拷 `Default/History`。
</details>

<details>
<summary><b>9. 账户设置里的 2FA 开关不代表登录不需要验证码</b></summary>

那个字段只是设置开关，站点可能对**每个新会话**都强制验证码。JWT 有效就完全不会走到这一步。
</details>

<details>
<summary><b>10. 休眠会让定时任务「错过」</b></summary>

launchd 在机器睡眠时**错过**触发点，唤醒后补跑一次。脚本额外做幂等保护：当天已成功过就跳过，避免补跑导致一天访问多次。

想严格准时可配定时唤醒：

```bash
sudo pmset -a repeat wake MTWRFSU 05:05:00
```
</details>

## 安全

- **凭据不出本机**：`config.json` 在 `.gitignore` 里，脚本不会把账号密码写进日志或报告
- **报告脱敏**：邮件正文只有访问页面列表和状态，不含凭据
- **独立 profile**：用项目内的浏览器配置目录，不读写日常浏览器其他数据；`sync_auth.py` 只**读**日常浏览器的 Local Storage，不做修改
- **无第三方外传**：除目标站点和你的邮箱，不向任何服务器发数据

> ⚠️ 使用前请确认：本项目只保持登录状态，不下载、不分享、不做任何违反站点服务条款的事。请自行确认使用方式符合站点规则与当地法律，风险自担。

## 免责声明

本项目仅供学习与个人使用。使用者需自行确认目标站点的服务条款允许自动化访问，并承担因使用本项目产生的一切后果。作者不对账号损失或任何其他损失承担责任。

## License

[MIT](LICENSE)
