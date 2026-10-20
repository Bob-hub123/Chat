# 在线多人聊天平台 · 技术设计方案 v2

> 定位：极简单页匿名聊天室，B/S 架构，单机部署（2 核 2G Linux）。
> 目标：进入填昵称即可聊天，不做注册登录。
> 状态：**方案讨论稿**，确认后再进入开发。

---

## 〇、决策记录（v2 变更）

| 项 | v1 推荐 | **v2 已确认** | 影响 |
|---|---|---|---|
| **后端技术栈** | Node.js + TypeScript | **Python + FastAPI + websockets** | 见 §2.2（已改）；并发目标下调至 2000–3000 |
| **历史消息** | SQLite 持久化（5000 条 / 7 天） | **无数据库，纯内存；进程退出即清空** | 见 §3.6（已改）；去掉全部 DB 模块、备份、迁移工作量 |
| **前端** | 零构建原生三件套 | **已产出 `web/index.html`（单文件，含演示模式）** | 见 §2.3 |

**无数据库带来的连锁简化**
- 删除：`store/sqlite.ts`、批量落盘任务、定时清理、DB 备份与恢复、`PRAGMA user_version` 迁移 —— 省约 1 人日。
- 保留：内存环形缓冲（`deque(maxlen=200)`），新用户进房直接带历史，进程重启后清空。
- 代价：服务重启 = 聊天记录全丢。需在页面上明示（顶部房间副标题写「消息不保存，重连即清空」），避免用户误解。
- 升级方式简化为：换镜像重启即可，无需考虑数据迁移（但重启会断连，客户端自动重连）。

**演示模式说明**
`web/index.html` 内置 `DemoNet`：连不上后端（或 `file://` 直接双击打开）时自动用假数据驱动同一套 UI，可完整预览布局、气泡、系统提示、在线人数、断线横幅等效果，无需先起后端。

---

## 一、整体架构

### 1.1 逻辑架构

```
┌──────────────┐        HTTPS / WSS         ┌─────────────┐
│  浏览器       │  ────────────────────────▶ │    Nginx    │
│ PC / Mobile  │  ◀────────────────────────  │ 反向代理/SSL │
└──────────────┘                             └──────┬──────┘
       │                                            │ http://127.0.0.1:3000
       │  ① GET /            → 静态单页（Nginx 直接托管）    │
       │  ② WS  /ws          → 长连接（收发/心跳/状态/历史） │
       │                                            ▼
       │                                   ┌────────────────────┐
       │                                   │ 应用进程 (FastAPI)   │
       │                                   │  ┌──────────────┐  │
       └──────────────────────────────────▶ │  │ WS Hub       │  │
                                            │  │ 连接/房间/广播│  │
                                            │  ├──────────────┤  │
                                            │  │ Presence     │  │
                                            │  │ 在线成员表    │  │
                                            │  ├──────────────┤  │
                                            │  │ MessageStore │  │
                                            │  │ deque 200 条 │  │
                                            │  ├──────────────┤  │
                                            │  │ Guard        │  │
                                            │  │ 限流/过滤/转义│  │
                                            │  └──────────────┘  │
                                            └────────────────────┘
                                              全内存，无数据库
                                              进程退出 → 历史清空
```

### 1.2 部署形态（物理）

单机单进程起步，进程内留出水平扩展接口（见 §4.5）。

| 组件 | 说明 |
|---|---|
| Nginx | 终止 TLS、反向代理、静态资源缓存、连接数/请求限流 |
| App（单进程 uvicorn） | HTTP 接口 + WebSocket 同进程（端口 3000，仅监听 127.0.0.1）；**单 worker** |
| 数据 | **无数据库**。在线成员 `dict` + 最近 200 条消息 `deque` 均在进程内存，退出即清空 |
| 守护 | Docker `restart: always` 或 systemd `Restart=always` |

---

## 二、技术选型对比

### 2.1 实时通信方案对比（核心决策）

| 维度 | WebSocket | SSE (EventSource) | 长轮询 Long Poll | 短轮询 Polling |
|---|---|---|---|---|
| 通信方向 | 全双工 | 服务端→客户端单向（发送需另开 HTTP POST） | 半双工（拉模式） | 半双工 |
| 建连开销 | 1 次握手，之后帧级 | 1 次握手 | 每次请求重复握手/头 | 每次请求重复 |
| 实时延迟 | 毫秒级 | 毫秒级 | 百毫秒级 | 秒级（轮询间隔） |
| 服务端资源/连接 | 极低（几 KB） | 低 | 中（频繁重建） | 高（大量无效请求） |
| 断线重连 | 需自己实现 | **浏览器自动重连** | 需自己实现 | 天然无状态 |
| 心跳 | 需自己实现 | 自带 keep-alive 注释行 | 不适用 | 不适用 |
| 代理/防火墙兼容 | 好（部分老旧企业代理有问题） | 好（纯 HTTP） | 最好 | 最好 |
| 二进制支持 | 支持 | 不支持（UTF-8） | 支持 | 支持 |
| 复杂度 | 中 | 低 | 中 | 低 |

**推荐：WebSocket 为主通道，不做自动降级。**

理由：
1. 聊天是双向对等场景（既要秒收也要秒发），SSE 必须额外配一条 POST 通道，反而变成「两套协议 + 两套可靠性保障」，复杂度超过纯 WS。
2. 长/短轮询在 2 核 2G 下浪费 CPU 与带宽，且延迟无法接受。
3. 现代浏览器（含移动端 iOS/Android）WebSocket 支持率 > 98%，无需为老旧环境买单。
4. 断线重连与心跳虽然是自研，但代码量集中在客户端一个 `ReconnectingSocket` 类里（约 80 行），可控。

> 折中选项：如果确实担心某些网络环境封 WebSocket，可加一层「SSE + POST」降级，但建议放到 v2，先保证主链路稳定。

### 2.2 后端语言/框架对比

| 方案 | 内存基线 | 2C2G 适配 | WebSocket 生态 | 开发效率 | 备注 |
|---|---|---|---|---|---|
| Node.js + Fastify + ws | 50–80 MB | ★★★★★ | ★★★★★ | ★★★★★ | v1 推荐，v2 未采用 |
| Go + gorilla/websocket | 10–20 MB | ★★★★★ | ★★★★☆ | ★★★☆☆ | 性能天花板最高 |
| Java Spring Boot | 300–600 MB | ★★★☆☆ | ★★★★☆ | ★★★★☆ | 与 Java 后端求职方向一致 |
| **Python FastAPI + websockets（v2 已定）** | 80–120 MB | ★★★★☆ | ★★★★☆ | ★★★★★ | GIL 限制，并发目标下调 |

**Python 方案落地要点**

- ASGI 服务器：`uvicorn`，**单 worker 进程**（WebSocket 广播依赖进程内共享内存，多 worker 必须引入 Redis，v1 不做）。
  ```bash
  # 服务器（Linux，官方 CPython 3.11+）
  pip install fastapi "uvicorn[standard]" websockets
  uvicorn app.main:app --host 127.0.0.1 --port 3000 --loop uvloop --ws websockets
  ```
- 用 FastAPI/Starlette 内置 `WebSocket`（`@app.websocket("/ws")`）即可，不必单独用 `websockets` 库起服务；`websockets` 作为 uvicorn 的 WS 协议实现装上有性能优势。
- **本地 Windows 开发注意**：若 PATH 里混有 MSYS2/MinGW Python，其 venv 是 `mingw_x86_64` ABI，装 `uvicorn[standard]`（含 watchfiles/httptools 等 Rust/C 扩展）会因无对应 wheel 而编译失败。请用官方 Windows CPython（`py -3.13 -m venv .venv`）；应急可 `pip install fastapi uvicorn websockets`（不加 `standard` extra，纯 Python 实现也能跑）。
- 全部路由用 `async def`；**禁止在协程里做同步 IO**（本方案无 DB，风险天然规避）。
- 广播瓶颈：GIL 下单核广播约 1500–4000 msg/s（1000 人房每条消息一次全量 fan-out）。用 `asyncio.gather()` 并发 `send_text`，避免串行等待慢客户端。

### 2.3 前端

| 项 | 选择 | 理由 |
|---|---|---|
| 框架 | **原生 HTML/CSS/JS（零构建）** 或轻量 Vite + 原生 TS | 页面只有一个输入框 + 一个消息列表，上 React/Vue 属于过度设计；零构建还省掉 Node 打包链路 |
| 样式 | 手写 CSS + CSS 变量，flex 布局 | 无需 Tailwind |
| 图标/表情 | 原生 emoji，不引第三方库 | 控制体积 |

### 2.4 存储（v2：无数据库）

| 项 | 选择 |
|---|---|
| 在线成员 | **进程内存 dict**（`{userId: {nick, ws, join_at}}`） |
| 最近消息 | **进程内存环形缓冲** `collections.deque(maxlen=200)` |
| 历史消息 | **无持久化**，进程退出即清空（v2 已定） |
| 跨进程广播 | v1 不涉及；预留 `BroadcastAdapter` 接口，将来换 Redis Pub/Sub |

---

## 三、功能范围与交互设计

### 3.1 界面与交互

**页面结构（单页，两态）**

- **态 A · 入口页**：一句话标题 + 昵称输入框 + 「进入聊天室」按钮。
- **态 B · 聊天页**：顶部（房间名 + 在线人数） / 左侧或抽屉（成员列表，移动端折叠） / 中部（消息流） / 底部（输入框 + 发送）。

**移动端适配要点**

| 问题 | 处理 |
|---|---|
| 视口 | `<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">` |
| 刘海屏/底部横条 | `padding-bottom: env(safe-area-inset-bottom)` |
| 输入框被键盘遮挡 | 输入区用 `position: sticky/fixed` 底栏；监听 `visualViewport.resize` 动态调整 |
| iOS 输入框缩放 | 字号 ≥ 16px |
| 消息列表过长 | 只保留最近 300 条 DOM，超出移除头部节点；或滚动到顶时再拉取更早历史 |
| 移动端成员列表 | 默认隐藏，点顶部在线人数展开抽屉（半屏遮罩） |

### 3.2 昵称规则（重点）

| 场景 | 处理 |
|---|---|
| 为空 | 前端 `trim()` 后为空 → 按钮禁用 + 提示「请输入昵称」；服务端二次校验，非法直接拒绝连接（**前后端都校验**） |
| 长度 | **1–16 个字符**。计数用 `Array.from(nick).length`（避免 emoji / 代理对被算成 2 个字符）；超限在输入框下方实时提示 `8/16` |
| 字符白名单 | 允许中英文、数字、下划线、连字符、空格；**过滤**控制字符、零宽字符（`\u200b-\u200f`）、换行符、RTL 覆盖字符（防视觉欺骗） |
| 头尾空格 | 自动 trim；连续空格折叠为一个 |
| **昵称重复**（v2 已定） | **完全自由，不做任何重名处理。** 昵称只是显示层，身份一律由服务端下发的 `userId` 区分。可以给多人起一模一样的名字，服务端不拒绝、不加后缀、不改字。同名在 UI 上靠**头像色（userId 哈希取色）**区分 |
| **自称显示**（v2 已定） | **自己看自己显示「我」，别人看你显示你设置的昵称。** 同一条消息，发送者页面上是「我」，其他人页面上是你设的昵称。涉及自己的系统提示同样用第二人称：自己看到「你加入了聊天室」，别人看到「张三 加入了聊天室」（由服务端在 `sys` 里同时下发 `text` 与 `self` 两个版本，客户端按 `uid` 选一个） |
| **成员列表里的自己** | 显示**昵称 + 「我」标签**（不替换成「我」），因为用户需要知道自己当前对别人展示的是什么名字 |
| **刷新后身份保持** | 首次进入服务端下发 `{ userId, token }`，前端写入 `localStorage`（key: `chat.identity`）。刷新后先读 localStorage：有 token → 直接带 `?token=xxx` 连 WS，静默恢复身份；token 失效/过期（服务端 7 天 TTL）→ 回退到入口页，但**预填上次昵称** |
| 改昵称 | v1 支持：在成员列表点自己 → 重填 → 广播一条系统提示「张三 改名为 李四」。v1 可选，默认做（成本低） |
| 多标签页 | 同一浏览器开多标签：各自独立连接，计为多个在线（可接受）。如需限制，用 `BroadcastChannel` 同步身份 |

### 3.3 实时通信细节

**消息协议（JSON，字段精简）**

```jsonc
// 客户端 → 服务端
{ "t": "hello", "token": "xxx" }                       // 握手（可带身份恢复 token）
{ "t": "join",  "nick": "张三" }                        // 首次进入
{ "t": "msg",   "cid": "uuid-1", "text": "你好" }        // 发消息，cid=客户端消息ID
{ "t": "pong",  "ts": 1730000000 }                     // 应用层心跳回包
{ "t": "sync",  "lastSeq": 1024 }                      // 重连后补拉增量

// 服务端 → 客户端
{ "t": "welcome", "userId": "u_7f", "token": "xxx", "nick": "张三",
  "members": [...], "online": 12, "history": [...], "seq": 1024 }
{ "t": "chat",   "seq": 1025, "uid": "u_7f", "nick": "张三", "text": "你好", "ts": 1730000000000 }
{ "t": "ack",    "cid": "uuid-1", "seq": 1025, "ts": 1730000000000 }   // 发送成功确认
{ "t": "sys",    "seq": 1026, "uid": "u_7f",
  "text": "张三 加入了聊天室",       // 其他人看到的
  "self": "你加入了聊天室",          // 当事人自己看到的（uid === me 时客户端用这条）
  "ts": 1730000000000 }
{ "t": "presence", "online": 13, "members": [...] }     // 成员/人数变化（节流广播）
{ "t": "ping",   "ts": 1730000000 }                    // 应用层心跳
{ "t": "err",    "code": "RATE_LIMIT", "msg": "发送太频繁了" }
```

**可靠收发（ACK 机制）**

1. 客户端发消息时生成 `cid`（UUID/递增数），本地先以「发送中」态上屏（乐观 UI）。
2. 服务端广播后回 `ack`（带 `seq` + 服务端时间戳），客户端改为「已发送」并校准时间。
3. 客户端 5s 内未收到 `ack`：
   - 连接正常 → 标记「发送失败」，提供重试按钮；
   - 连接断开 → 进重连队列，重连成功后按 `cid` 去重重发（服务端用 `cid` 做幂等去重）。

**断线重连**

- 触发：`onclose` / `onerror` / 心跳超时。
- 策略：**指数退避 + 抖动**：`delay = min(30s, 1s × 2^n) × (0.5 ~ 1.0 随机)`，避免服务端重启后瞬间被打爆（惊群）。
- 监听 `window.online` 事件，网络恢复立即重试一次。
- 重连成功后发 `sync{lastSeq}`，服务端补齐 `lastSeq` 之后的消息（内存环形缓冲能覆盖就直接给，覆盖不了就只给最近 N 条并提示）。
- UI：顶部出现一条黄色横幅「连接已断开，正在重试…（第 3 次）」，重连成功自动消失。

**心跳机制（v2 修订：应用层双向 ping/pong）**

> 修订原因：Starlette / FastAPI 的 `WebSocket` 对象**不暴露协议层 ping 帧**（不像 `ws` 库能直接 `socket.ping()`），且浏览器端也收不到 pong。所以改为双方都在 JSON 层做心跳。

| 方向 | 实现 | 参数 |
|---|---|---|
| 服务端 → 客户端 | 服务端每 **20s** 发 `{"t":"ping","ts":...}`；客户端收到后回 `{"t":"pong","ts":...}` | 20s |
| 客户端 → 服务端 | 客户端每 **25s** 发 `{"t":"ping"}`；服务端回 `{"t":"pong"}` | 25s |
| 僵死判定 | 服务端：`last_seen`（收到任何帧都刷新）超过 **60s** → 主动 close(1001)<br>客户端：超过 **45s** 未收到任何服务端数据 → 主动重连 | 60s / 45s |
| Nginx | `proxy_read_timeout 75s; proxy_send_timeout 75s;` | 75s（必须大于心跳间隔） |

两套心跳并存是有意的：客户端→服务端保证链路有数据、穿过代理空闲超时；服务端→客户端让服务端能主动发现半开连接（手机切后台、断网）。

**系统提示（进入 / 离开）**

- 服务端在连接建立（发过 `hello`/`join` 后）与连接关闭时生成 `sys` 消息并广播。
- **防刷屏处理（重要）**：同一 `userId` 在 **60s 窗口内**的 leave→join（刷新页面）**不广播**进入/离开提示；超过 60s 才广播。实现方式：连接关闭时不立即广播 leave，而是延迟 10s 检查该 userId 是否重新上线。
- 合并：若 5s 内有多人进入，合并为一条「张三、李四 加入了聊天室」。

### 3.4 在线人数与成员列表

- 服务端内存维护 `Map<userId, { nick, connId, joinAt, lastSeen }>`。
- 变化时不逐条广播，而是**节流 1s 合并广播**一次 `presence`（全量成员列表，人数 < 200 时体积可接受；超过则只广播增量 diff）。
- 成员展示：昵称 + 头像色块（userId 哈希）+ 「（我）」标记。不展示 IP、不展示精确进入时间（v1）。

### 3.5 消息时间展示

- 服务端统一存 **UTC 毫秒时间戳**（`ts`），客户端用本地时区格式化，**避免服务端时区问题**。
- 展示规则：
  - 1 分钟内：`刚刚`
  - 同一天：`HH:mm`
  - 昨天：`昨天 HH:mm`
  - 更早：`MM-DD HH:mm`
- 连续消息折叠：同一用户、间隔 < 3 分钟的消息**只在首条显示时间 + 昵称**，后续缩进对齐（类似微信/Slack），显著减少视觉噪音。
- 注意客户端时钟偏移：以服务端 `ts` 为准，客户端仅做格式化；若发现本地与服务端时间差 > 60s，用偏移量校正。

### 3.6 历史消息（v2：纯内存，不持久化）

| 项 | 方案 |
|---|---|
| 是否持久化 | **不持久化**。进程内存 `deque(maxlen=200)`，进程重启/退出即全部清空 |
| 保留条数 | **最近 200 条** |
| 写入 | 直接 `append`，无 IO、无批量落盘任务 |
| 清理 | 无需定时清理，`deque` 自动淘汰最旧的 |
| 下发方式 | 新用户 WS 握手成功时服务端直接在 `welcome` 里带 `history`（最近 200 条） |
| 更早历史 | **不支持滚动加载**；`GET /api/history` 接口可先不做（v2 移除） |
| 用户告知 | 页面顶部副标题常驻显示「消息不保存，重连即清空」，避免误解 |

> 若将来想加持久化，最小改动是把 `MessageStore` 换成「deque + 定时快照到 SQLite」——接口不变，后端改动约 0.5 人日。

---

## 四、部署与性能

### 4.1 部署方式对比

| 方式 | 优点 | 缺点 | 适用 |
|---|---|---|---|
| **直接部署 + systemd + 宿主机 Nginx**（★ 实际采用） | 无额外依赖、排障直观（`journalctl` 直接看）、内存最省、部署最快 | 需手动管 Python 版本/依赖、迁移略麻烦 | 本项目：2 核 2G、单一服务、快速上线 |
| Docker Compose | 环境隔离、一条命令起停、迁移方便、版本可回滚 | 多一层、需装 Docker、2G 机器上 Docker 自身也占资源 | 长期运行、需要频繁换机器/多服务编排 |

**实际采用：直接部署。** 决策依据——① 2 核 2G 上 Docker 与 daemon 本身占用不划算；② 只有单一服务，Compose 的编排价值用不上；③ 学生机排障时 `journalctl -u chat -f` 比 `docker logs` 更直接。

产出文件：`deploy/nginx.conf`（反向代理 + WS 升级）、`deploy/chat.service`（systemd 守护）、`deploy/setup.sh`（首次初始化，幂等）、`deploy/update.sh`（更新代码后重启）。

> **端口选择：默认 8080 而非 80。** 国内云主机未备案时 80/443 会被拦截，8080 不受影响。域名备案完成后改 `listen 80` 并补 443 的 SSL 配置即可。

### 4.2 资源占用估算（2 核 2G，Ubuntu 22.04）

| 项目 | 估算 |
|---|---|
| OS + 系统服务（sshd、journald 等） | 150–250 MB |
| Nginx（2 worker） | 10–25 MB |
| uvicorn/FastAPI 进程基线（空载） | **80–120 MB**（CPython 解释器 + asyncio 事件循环） |
| 每 WebSocket 连接 | **约 15–30 KB**（asyncio Task + 收发缓冲 + 会话对象）；按 30 KB/连接保守估 |
| SQLite | **无**（v2 已移除） |
| Docker 本身开销 | 可忽略（< 30 MB） |

**连接数与内存换算（Python）**

| 并发连接数 | 预估内存增量 | 总内存占用（含系统） | 是否安全 |
|---|---|---|---|
| 500 | ~15 MB | ~400 MB | 非常安全 |
| 2,000 | ~60 MB | ~450 MB | 安全（推荐上限） |
| 3,000 | ~90 MB | ~500 MB | 安全 |
| 5,000 | ~150 MB | ~600 MB | 内存够，但 **CPU 会成为瓶颈**，不建议 |

**单进程能力上限（Python，保守）**
- 长连接：**2,000**（设为软上限，超过则拒绝并提示「房间已满，请稍后再来」）
- 消息吞吐：GIL 限制下单核约 **1,500–4,000 msg/s**；1000 人房广播一条 ≈ 20–50 ms
- CPU：500 活跃连接、每人 1 msg/5s（100 msg/s 广播）→ CPU 占用约 15–25%
- 瓶颈通常在 **CPU 而非内存**；若 CPU 先打满，优先做「广播合并」（见 §4.5）
- 容器 `--memory=800m`；Python 无 `--max-old-space-size` 这类硬限制，靠连接上限 + 日志轮转防止 OOM

### 4.3 系统参数调优（必做）

```bash
# /etc/security/limits.conf
* soft nofile 65535
* hard nofile 65535

# /etc/sysctl.conf
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 8192
net.ipv4.ip_local_port_range = 1024 65535
net.ipv4.tcp_tw_reuse = 1
```

Nginx：
```nginx
worker_processes auto;          # 2核 → 2
worker_rlimit_nofile 65535;
events { worker_connections 16384; }

map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
location /ws {
    proxy_pass http://127.0.0.1:3000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_read_timeout 75s;
    proxy_send_timeout 75s;
    proxy_buffering off;      # 关键：否则消息会被缓冲延迟
}
```

### 4.4 压测预案

| 项目 | 做法 |
|---|---|
| 压测工具 | `artillery`（`artillery quick --count 500 --num 10 ws://...`）或自写 Node 脚本用 `ws` 建 N 个连接 |
| 观测指标 | 连接数、`RSS/heapUsed`、消息 P99 延迟、Event Loop 延迟（`perf_hooks.monitorEventLoopDelay`）、CPU |
| 验收门槛 | 2000 连接下：建连 < 1s；消息端到端 P99 < 200ms；Event Loop 延迟 P99 < 50ms；内存 30 分钟无持续增长（无泄漏） |
| 泄漏排查 | 压测后 `--expose-gc` 手动 GC，观察 heap 是否回落；检查 `Map` 是否有未清理的断开会话 |

### 4.5 压力预案 / 扩展路径（按触发顺序）

1. **限流降级**：单 IP 最多 20 连接；消息令牌桶（容量 5，速率 1 条/秒）；超阈值返回 `err` 而非断开。
2. **背压保护**：为每个连接设待发队列上限（如 100 条 / 512 KB），超限直接断开慢客户端，防止拖垮整个事件循环。
3. **连接上限**：达到 `MAX_CONN`（默认 2000）时拒绝新连接，返回 HTTP 503 + 友好页。
4. **广播合并（Python 关键优化）**：把 100ms 内的多条聊天消息攒成一批一次 `send`（减少 JSON 序列化与系统调用次数）；`presence` 变更本来就已节流 1s。
5. **垂直扩展**：用 `uvicorn --workers 2` 跑多进程时，**WebSocket 必须靠 Nginx `ip_hash` 做会话粘性**，否则用户在两个进程间会看到不一致的成员列表；进程间广播需 `BroadcastAdapter`（v2 换 Redis Pub/Sub），**v1 直接用单 worker 规避**。
6. **水平扩展**：加机器 + Redis Pub/Sub + `presence` 放 Redis。此时才考虑引入数据库。
7. **静态资源卸载**：`index.html` 只有 1 个文件，直接由 Nginx 托管即可，不走 uvicorn。

---

## 五、安全与运维

### 5.1 XSS 防护（纵深防御，三层）

| 层 | 措施 |
|---|---|
| 输入 | 服务端校验：纯文本、长度 ≤ 500 字符、剔除控制字符与零宽字符 |
| 存储 | 存**转义后**还是**原文**？推荐：**存原文，出时转义**（避免双重转义与迁移问题） |
| 输出 | 前端渲染**必须**用 `textContent`，**严禁 `innerHTML`** 拼接用户内容；昵称与消息一视同仁 |
| 额外 | 设置 CSP：`default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'`，堵住即使有漏网的注入 |
| Cookie | 本项目不用 Cookie 做认证，但仍设 `HttpOnly/Secure/SameSite=Lax` 兜底 |

> v1 建议：**不把 URL 自动转成可点击链接**（一转就可能引入 `javascript:` 伪协议）。保持纯文本是最安全的。若将来要做，必须白名单 `http/https` 且加 `rel="noopener noreferrer"`。

### 5.2 刷屏与恶意内容

| 威胁 | 防护 |
|---|---|
| 高频刷屏 | **令牌桶**：每人容量 5、速率 1 条/秒；超限返回 `err{RATE_LIMIT}`，连续 3 次超限 → 静默 30s（只丢弃不告知细节） |
| 重复内容 | 连续 3 条完全相同（或相似度 > 0.9）→ 拦截并提示 |
| 超长消息 | 单条 ≤ 500 字符，超限前端截断 + 服务端拒绝 |
| 巨型帧 | WS `maxPayload: 8KB`，超出直接断开 |
| 连接轰炸 | 单 IP 连接数 ≤ 20；Nginx `limit_conn` 再做一层 |
| 新连接冷却 | 建连后 3 秒内不允许发言（防脚本秒进秒发） |
| 机器人 | v1 不做验证码（匿名聊天室体验优先）；若出现机器人再引入「首次发言前点一下按钮」的轻量验证 |
| 敏感词 | 可选：本地词表（DFA/Trie）匹配后替换为 `***`。词表热加载，不重启生效 |
| 违法内容 | 前端提供「举报」按钮（v2）；服务端保留最近 24h 日志便于追溯 |

### 5.3 日志

| 类型 | 内容 | 保留 |
|---|---|---|
| 访问日志 | Nginx access log（IP、路径、状态码、UA） | 14 天，logrotate |
| 应用日志 | JSON 结构化（pino），级别 `info/warn/error` | 14 天，logrotate + 单文件 ≤ 50MB |
| 必记事件 | 建连/断开、身份恢复失败、限流命中、异常堆栈、进程启停、定时清理结果 | — |
| **不记** | **不记录完整消息正文**（隐私 + 体积）。只记 `{seq, uid, len, ts}` 元数据；如需审计，记前 20 字摘要或 `sha256` | — |

```js
// 示例
{"level":30,"time":...,"event":"ws.open","connId":"c_12","ipHash":"a1b2c3","online":42}
{"level":40,"time":...,"event":"rate.hit","uid":"u_7f","rule":"msg_bucket"}
```
IP 记哈希而非明文，兼顾排障与隐私。

### 5.4 进程守护与监控

| 项 | 方案 |
|---|---|
| 守护 | Docker：`restart: always` + `healthcheck`（`GET /healthz` 每 30s，3 次失败重启）。systemd：`Restart=always`、`RestartSec=5` |
| 健康检查 | `/healthz` 返回 `{ok:true, uptime, conns, memMB, version}`；**不记日志**（避免刷满） |
| 监控 | 最简：一个 `/metrics` 端点输出 key-value（连接数、msg/s、heap）；或接 Prometheus + Node Exporter + 云厂商告警 |
| 告警 | 内存 > 80%、连接数 > 4000、进程重启（Docker 事件）、5xx 突增 → 邮件/企微推送 |
| 优雅停机 | 捕获 `SIGTERM` → 停止接受新连接 → 广播「服务器即将重启」→ 关闭所有 WS（1001）→ 刷盘 → 退出，超时 10s 强杀 |

### 5.5 备份与升级

| 项 | 方案 |
|---|---|
| 数据备份 | **无需**（无数据库，无用户数据）。`deque` 里的 200 条消息本就是临时态 |
| 配置备份 | `docker-compose.yml`、`nginx.conf`、`.env`、`web/index.html` 纳入 Git 私有仓库 |
| ~~恢复演练~~ | 不适用 |
| 升级流程 | 1) 本地/CI 构建镜像打 tag（`v1.2.3`）；2) `docker compose pull && docker compose up -d`（停机 < 2s，客户端自动重连）；3) 观察 5 分钟日志；4) 失败则指回上一个 tag 回滚 |
| ~~数据库迁移~~ | 不适用（无 schema） |
| 重启影响 | **重启 = 全员断连 + 聊天记录清空**。务必提前在页面/公告里说明；可选做法：优雅停机前先广播一条系统提示「服务器即将重启」 |

---

## 六、模块划分与实施顺序

### 6.1 模块划分

```
chat-platform/
├─ server/
│  ├─ app/                       # 后端：Python + FastAPI（扁平模块，无子包）
│  │  ├─ main.py                 # 组装：路由、静态页、lifespan、优雅停机
│  │  ├─ config.py               # 全部可环境变量覆盖的配置
│  │  ├─ logger.py               # JSON 行日志
│  │  ├─ identity.py             # 昵称规范化/校验、userId、token 签发与校验
│  │  ├─ sanitize.py             # 消息文本清洗（长度、控制字符）
│  │  ├─ ratelimit.py            # 令牌桶
│  │  ├─ ringbuf.py              # deque 环形缓冲（最近 200 条）
│  │  ├─ message.py              # 消息模型与序列化（chat/sys/welcome/ack/err）
│  │  ├─ room.py                 # 房间：成员表 + seq 分配 + 历史
│  │  ├─ protocol.py             # 客户端帧解析与基础校验
│  │  ├─ hub.py                  # ★核心：连接表、进出、广播、心跳、进出抑制
│  │  ├─ endpoint.py             # /ws 路由
│  │  └─ health.py               # /healthz、/metrics
│  ├─ requirements.txt
│  └─ Dockerfile
├─ web/
│  └─ index.html                 # 前端单文件（含演示模式）★ 已产出
├─ deploy/
│  ├─ docker-compose.yml
│  └─ nginx.conf
└─ docs/
   └─ design-v1.md               # 本文档（v2）
```

> **v2 调整：后端目录改扁平化。** 原计划按 `domain/`、`ws/`、`store/`、`guard/` 分四层共 16 个文件，但 50 人档下每个文件平均只有 30 行，拆这么细反而增加阅读与跳转成本，还多出 5 个空 `__init__.py`。改为 `app/` 单包 + 13 个语义清晰的模块。

**模块依赖关系**（无循环）

```
main.py ──▶ endpoint.py ──▶ hub.py ──▶ room.py ──▶ ringbuf.py
   │                          │          │
   ├──────▶ health.py ────────┤          └──▶ message.py
   │                          ├──▶ identity.py / sanitize.py / ratelimit.py
   │                          └──▶ protocol.py / config.py / logger.py
```

### 6.2 实施顺序与工作量（单人，人日）

| 阶段 | 内容 | 工作量 | 产出/验收 |
|---|---|---|---|
| **P0 确认** | 敲定 §7 开放问题、建仓库骨架、本地跑通空壳 | 0.5 d | 方案定稿 |
| **P1 前端** | 入口页 + 聊天页 + 移动端适配 + 时间展示 + 乐观 UI | 1.0–1.5 d | **已完成初版**（`web/index.html`），待你验收/改稿 |
| **P2 通信内核** | WS Hub + 协议 + 身份 + 在线列表 + 系统提示 + 心跳 + 重连 | 1.5–2.0 d | 多标签页可实时聊天，断网能自动恢复 |
| **P3 ~~持久化~~** | ~~环形缓冲 + SQLite 批量写 + 清理任务~~ → **改为纯 deque 环形缓冲** | **0.2 d** | 重启后历史清空（预期行为），新用户进房可见上下文 |
| **P4 安全** | XSS 转义 + 限流 + 背压 + 敏感词（可选） | 0.5–1.0 d | 压测脚本刷屏被拦截 |
| **P5 部署** | ~~Dockerfile + compose + Nginx + HTTPS + 守护 + logrotate~~ → **改为 systemd + 宿主机 Nginx + 8080 端口** | 0.5 d ★已完成 | `deploy/` 下 4 个文件；HTTPS 待域名备案后补 |
| **P6 压测调优** | 压测、参数调优、泄漏排查、写 README | 0.5–1.0 d | 2000 连接达标，文档齐全 |
| **合计** | | **4.2–6.7 人日**（较 v1 减少约 1.3 d） | |

> Python 版本相比 Node 版本：业务逻辑代码量更少（P2 略省），但并发上限低、P6 调优更依赖「广播合并」与 GIL 规避。

### 6.3 开发顺序原则

1. **先跑通「一个进程 + 内存缓冲」的最小闭环**（P1+P2），能聊天就算成功一半。
2. 持久化、限流、部署都建立在闭环之上，随时可裁剪。
3. 每个阶段结束都要能在浏览器里实际操作验证，避免最后集成踩坑。

---

## 七、需要你决策的开放问题

> 下面每条我都给了推荐项，你只需确认或改选即可。

| # | 问题 | 选项 | 我的推荐 |
|---|---|---|---|
| ~~Q1~~ | **后端技术栈** | ✅ **已定：Python FastAPI + websockets** | — |
| ~~Q2~~ | **历史消息是否持久化** | ✅ **已定：无数据库，纯内存，退出即清空** | — |
| ~~Q12~~ | **前端形态** | ✅ **已定：单文件零构建 + 演示模式**（`web/index.html` 已产出） | — |
| ~~Q3'~~ | **昵称重复策略** | ✅ **已定：完全自由，不校验重名、不加后缀**；自称显示「我」，他人显示其昵称 | — |
| **Q4'** | **目标并发量级** | ✅ **已定：50 人以内**（自用 / 小圈子） | 见下方「50 人档简化清单」 |

### 50 人档简化清单（并发已定）

并发 50 人意味着每秒广播压力极低（按每人每分钟 1 条算 ≈ 0.8 msg/s），因此**以下优化全部不做**：

| 原设计 | 50 人档处理 | 理由 |
|---|---|---|
| 广播合并（100ms 窗口攒批） | **不做**，直接 `asyncio.gather` 全量 fan-out | 50 人广播一次 < 2ms，攒批只会增加延迟 |
| presence 节流 1s | **不做**，成员变化立即广播 | 50 人规模下进出事件本就极稀疏 |
| 多 worker / 粘性会话 | **不做**，单 worker | 50 人单进程绰绰有余 |
| 连接软上限 | 从 2000 降到 **200**（50 人的 4 倍余量，防脚本刷连接） | 留余量但不浪费 |
| 内存/CPU 预期 | 内存 **约 200 MB 总量**，CPU **< 5%** | 2 核 2G 有巨大冗余 |
| 断线补拉 `sync{lastSeq}` | **不做**，重连直接下发完整 `welcome`（含 200 条历史） | 200 条历史 ≈ 30 KB，一次发完更简单可靠 |
| 背压队列 | 简化为 `asyncio.wait_for(send, 5s)`，超时直接断开慢客户端 | 无需独立发送队列 |
| 心跳 | **改为应用层双向 ping/pong**（见 §3.3 修订） | Starlette 的 WebSocket API 不暴露 ping 帧 |
| Q5 | **部署方式** | A. Docker Compose<br>B. 直接部署 + systemd + 宿主机 Nginx | **已定 B**（2G 内存省资源 + 排障直观） |
| Q6 | **域名与 HTTPS** | 是否已有域名？是否已备案（国内云）？证书用 Let's Encrypt 免费签还是自备？ | 无域名也能先用 IP + HTTP 跑通 |
| Q7 | **v1 是否就要多房间/频道** | A. v1 只做单公共房（代码预留 Room 抽象）<br>B. v1 就做房间列表 + 切换 | **A** |
| Q8 | **附加功能取舍**（多选） | 图片发送 / 表情包 / 私聊 / @提醒 / 消息撤回 / 敏感词过滤 / 举报 | 建议 v1 全不做，只做纯文本 |
| Q9 | **管理后台** | 是否需要踢人、禁言、查看在线、发公告？ | v1 建议不做 |
| Q10 | **消息日志粒度** | A. 只记元数据（推荐，隐私友好）<br>B. 记全文 | **A** |
| Q11 | **监控要求** | 要不要接 Prometheus + Grafana，还是只要 `/healthz` + `/metrics` | 建议先做端点 |
| **Q13** | **前端验收反馈** | 看完 `web/index.html` 后：配色 / 气泡样式 / 是否需要深色模式 / 输入框交互等 | 请直接提 |

> 优先级：先回 **Q13（前端是否满足预期）** + **Q4'（并发量级）**，这两条定了我就能直接进 P2 后端开发。

---

## 八、附：关键默认值一览（可直接改）

```bash
# ---- 服务端 ----
PORT=3000
WS_PATH=/ws
UVICORN_WORKERS=1              # WebSocket 场景必须单 worker（或配 ip_hash 粘性）
MAX_CONNECTIONS=2000           # Python 版软上限
NICK_MIN_LEN=1
NICK_MAX_LEN=16
MSG_MAX_LEN=500
HISTORY_ON_JOIN=200            # 进房下发条数（deque maxlen）
HEARTBEAT_SERVER_PING=20s      # 服务端 ping 帧间隔
DEAD_CONNECTION_TIMEOUT=60s
CLIENT_HEARTBEAT=25s
NGINX_PROXY_READ_TIMEOUT=75s
RATE_MSG_CAPACITY=5            # 令牌桶容量
RATE_MSG_REFILL=1              # 每秒补充
RATE_CONN_PER_IP=20
JOIN_LEAVE_SUPPRESS_WINDOW=60s # 刷新防刷屏窗口
PRESENCE_THROTTLE=1s
BROADCAST_COALESCE_MS=100      # 广播合并窗口（Python 关键优化）

# ---- 客户端（web/index.html 内 CFG）----
HEARTBEAT_MS=25000
WATCHDOG_MS=45000
ACK_MS=5000
GROUP_MS=180000                # 3 分钟内同一人消息合并
DIVIDER_MS=300000              # 超过 5 分钟插时间分隔
MAX_DOM=300                    # DOM 中最多保留消息条数
```
