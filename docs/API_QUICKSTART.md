# WZ MiniMax 视频 API — 快速上手（2026-10-20 R550）

## 0. 地址与凭据

- Base URL：`https://api.example.com`（HTTP 内网：`http://127.0.0.1:8808`）
- 文档：`/docs`（Swagger）、`/openapi.json`（55 条路径）
- 测试台：`/play`
- 号池：**120 个唯一账号**（已去重，原 157 条里 37 条是同一账号的 `g+` 重复项）
- 16C16G 专用 Key（grandpa-16c16g-1020，rpm 120 / 并发 8 / 无配额上限）：
  `sk-wz-REPLACE_WITH_YOUR_KEY`
- 老 Key（grandpa-verify-1020）：
  `sk-wz-REPLACE_WITH_YOUR_KEY`
- 老 Key（grandpa-openapi）：
  `sk-wz-REPLACE_WITH_YOUR_KEY`
- Admin Token：`<ADMIN_TOKEN>`（请求头 `X-Admin-Token`）

鉴权：`Authorization: Bearer sk-wz-...`

流媒体/下载端点（`/v1/videos/{tid}/content`、`/v1/library/{fid}/content`）现在也校验 key：
可以带 `Authorization` 头，也可以给 `<video src>` 用 `?key=sk-wz-...`。

## 1. 创建 Key

```bash
curl -X POST https://api.example.com/admin/keys \
  -H "X-Admin-Token: <ADMIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"name":"my-key","rpm":120,"max_concurrent":8,"quota":0,"daily_quota":0}'
```

返回里的 `key` 就是 `sk-wz-...`。`quota=0` 表示不限总量，`daily_quota=0` 表示不限每日。

## 2. 出片（1080P）

```bash
curl -X POST https://api.example.com/v1/videos/generations \
  -H "Authorization: Bearer sk-wz-REPLACE_WITH_YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"海边日落，镜头缓慢推近","model":"MiniMax-Hailuo-2.3","resolution":"1080P","duration":6,"ratio":"16:9"}'
```

先估价不排队（单条 `dry_run`：不建任务、不扣额度、不占并发）：

```bash
curl -X POST https://api.example.com/v1/videos/generations \
  -H "Authorization: Bearer <key>" -H "Content-Type: application/json" \
  -d '{"prompt":"海边日落","model":"MiniMax-Hailuo-2.3","resolution":"1080P","duration":6,"dry_run":true}'
# {"object":"video.generation.preview","dry_run":true,"model":"MiniMax-Hailuo-2.3",
#  "resolution":"1080P","duration":6,"ratio":"16:9","credit_cost":80,
#  "queued":false,"charged":false,"note":"dry_run: nothing queued, nothing consumed"}
```

也支持 query 形式：`POST /v1/videos/generations?dry_run=1`。参数校验与真实提交完全同一套（`_check_spec`），所以 720P、空 prompt、错误模型都会在这里就返回 400。

轮询与下载：

```bash
curl https://api.example.com/v1/videos/<id>/status -H "Authorization: Bearer <key>"
curl -L -o out.mp4 "https://api.example.com/v1/videos/<id>/content?key=<key>"
```

## 3. 批量出片（≤64 条/批）

```bash
curl -X POST https://api.example.com/v1/videos/batch \
  -H "Authorization: Bearer <key>" -H "Content-Type: application/json" \
  -d '{"defaults":{"resolution":"1080P","duration":6,"ratio":"16:9"},
       "items":[{"prompt":"prompt A"},{"prompt":"prompt B"}]}'
```

查询：`GET /v1/videos/batch/{batch_id}`

## 4. 旧片库（24,494 条 hailuo 渲染农场）

```bash
curl "https://api.example.com/v1/library?limit=50&offset=0&q=t-" \
  -H "Authorization: Bearer <key>"

curl -L -o old.mp4 \
  "https://api.example.com/v1/library/<file_id>/content?key=<key>"
```

`GET /v1/library` 参数：`limit`(≤200) / `offset` / `q`(文件名子串) / `refresh=1`(强制重建索引)。
目录索引有 30s 缓存：冷查询约 300ms，热查询约 8ms。

## 5. 其它端点

| 路径 | 说明 |
|---|---|
| `GET /v1/models` | 模型、分辨率、时长、比例 |
| `GET /v1/pricing` | 价目（Hailuo-2.3 1080P/6s 当前不扣余额） |
| `GET /v1/tools` | 透传工具名清单 |
| `POST /v1/tools/call` | 透传调用 `{tool,arguments}` |
| `GET /v1/videos/generations` | 本 key 任务列表 |
| `GET /healthz` | 池状态（120 号 / dead 0） |
| `GET /metrics` | 指标 |
| `GET /admin/stats` `/admin/pool` `/admin/credits` `/admin/tasks` `/admin/usage` `/admin/token-age` | 管理视图 |
| `POST /admin/reload-pool`（`?full=1` 按磁盘重建，删除/去重立即生效）`/admin/revive` `/admin/unpark` `/admin/merge-tokens` `/admin/apply-liveness` | 池运维 |
| `DELETE /admin/keys/{kid}` | 禁用 key |

## 6. 视频存放位置（服务器 127.0.0.1）

| 目录 | 数量 | 体积 | 说明 |
|---|---|---|---|
| `/opt/h3v2/data/render/out` | 24,494 | 60 GB | 10-12~10-13 老 hailuo 农场，已挂到 `/v1/library` |
| `/opt/wzmm/videos` | 35 | 89 MB | 本 API 出片（10-19 ~ 10-20） |
| `/tmp/chk.mp4` | 1 | — | 测试残留 |

本机 `C:\Users\Administrator\Documents\Codex` 另有 909 条 mp4（11.1 GB，主要在 `2026-10-15`：
z-ma-5 450 条、sd2 批次 225 条；10-16 121 条；10-07 102 条）。

## 7. 自检

```bash
ssh root@127.0.0.1 '/opt/wzmm/venv/bin/python -u /opt/wzmm/selftest.py'
# [2026-10-19 23:15:40] 38/38 passed
```

systemd timer `wzmm-selftest.timer` 每天 00/06/12/18 UTC 跑一次，日志 `/opt/wzmm/selftest.log`。

## 8. 号池续期（服务器侧，无需浏览器）

- `renew.py` 两条路：`refreshToken -> securetoken.googleapis.com -> /oauth2/login loginType=11`，
  或 `password -> RSA -> loginType=20`，之后 `/oauth2/authorize?client_id=hub&redirect_uri=design.minimax.io` 取 `_token`。
- 2026-10-20 这轮从 38 个 Google 登录号的 Chrome profile 离线提取了 Firebase refreshToken
  （IndexedDB leveldb 明文，长度 439~460），服务器实测 5/5 有效，批量续期 70 条成功。
- 现在 120 个唯一账号全部具备密码或 refreshToken，token 统一到 **2026-11-28**，`renew.py` 会在到期前自动续。
- 现在 120 个唯一账号全部具备密码或 refreshToken（49 个带 RT、105 个带密码），token 统一到 **2026-11-28**，`renew.py` 会在到期前自动续。
- 试用窗口到期不等于废号：expired 账号仍按 `WZMM_EXPIRED_CAP`（默认 20 条/天）计额度；当前 55 trial + 65 expired 全部可调度。
- Google 每次 exchange 可能轮换 refresh_token：`renew.py` 会把响应里的新 RT 立即写回 pool，即使后续 `/oauth2/login` 失败也不丢，避免 RT 变陈旧导致断链。

## 9. 并发与可靠性实测（R526）

| 场景 | 结果 |
|---|---|
| 单条 1080P | 约 200~280s 出片 |
| 10 条并发 1080P | 9 条一次成功 + 1 条上游 503；**自动重试后 10/10 成功**，墙钟 319s（约 113 条/小时） |
| 20 条并发 1080P | 20/20 成功，墙钟 415s（约 173 条/小时），0 次 503 |
| 40 条并发 1080P | 40/40 成功，墙钟 353s（约 408 条/小时），0 次 503 |
| 64 条 · 上限 48 | 64/64 成功，墙钟 544s（约 423 条/小时），16 条排队 |
| 64 条 · 上限 64 | 64/64 成功，墙钟 448s（约 **514 条/小时**），0 次 503 |
| 3 条并发 | 3/3 成功，199s |
| 幂等 | 同 `X-Idempotency-Key` 二次提交返回同一 id + `idempotent_replay: true` |
| 回调 | `callback_url` 收到 `POST` + `X-WZ-Event: video.succeeded`，记录在 `/opt/wzmm/webhook.log` |

### 自动重试（R526 新增）

- 提交时遇到 5xx / `temporarily unavailable` / `try again`：该账号只冷却 **90s**，换账号重试，最多 5 次
- 已失败的任务：`retrier` 线程每 120s 扫一次，命中瞬时错误就重新排队，最多 3 次
- 全局在途上限 `WZMM_MAX_INFLIGHT`（**默认 64**，可用环境变量覆盖）：超过上限的新任务排队等待，避免突发批量把上游打爆
- 在途上限实测：临时设 `WZMM_MAX_INFLIGHT=1` 后提交 3 条，`running` 在 160s 内始终 = 1，另外 2 条排队；恢复 48 后 3/3 成功
- 上限调参实测：同一批 64 条，上限 48 时 544s（423 条/小时），上限 64 时 448s（514 条/小时），**提高 21%**，故默认改为 64
- 任务详情里 `retry` 字段可见重试次数：

```bash
curl https://api.example.com/v1/videos/<id>/status -H "Authorization: Bearer <key>"
# {"status":"succeeded", ..., "retry":1}
```

### 幂等与回调

```bash
curl -X POST .../v1/videos/generations \
  -H "Authorization: Bearer <key>" \
  -H "X-Idempotency-Key: my-job-0001" \
  -d '{"prompt":"...","callback_url":"https://your.host/hook","resolution":"1080P","duration":6}'
```

### 安全

- `/metrics` 现在需要 `Authorization` 或 `X-Admin-Token`，不再公开暴露池规模
## 10. 多租户与速率限制（R534）

- **任务归属**：`/v1/videos/{id}/status`、`/v1/videos/{id}/content` 只允许创建该任务的 key 读取；
  其他 key 返回 `403 task belongs to another key`，admin token 不受限。
- **共享片库**：`/v1/library` 与 `/v1/library/{fid}/content` 是公共素材库，任何有效 key 可读。
- **自助用量**：

```bash
curl "https://api.example.com/v1/usage?days=7" -H "Authorization: Bearer <key>"
# {"key":{"name":...,"rpm":120,"used":...},"days":7,"totals":{"tasks":212,"succeeded":212},
#  "series":[{"d":"2026-10-20","status":"succeeded","n":212}]}
```

- **限流响应头**：所有带 key 的请求返回

```
X-RateLimit-Limit: 120
X-RateLimit-Remaining: 116
```

- selftest 已扩到 **46 项**（含跨 key 403、admin 放行、usage、限流头）。
## 11. Webhook 签名（R535）

每条回调都带签名，接收方可以验证真伪，并按 delivery id 去重：

```
POST https://your.host/hook
X-WZ-Event: video.succeeded
X-WZ-Delivery: dlv_<24hex>      # 同一任务同一状态固定，幂等去重键
X-WZ-Attempt: 1                 # 1..3
X-WZ-Timestamp: 1792456475
X-WZ-Signature-Version: 1
X-WZ-Signature: t=1792456475,v1=<hex>
```

算法：`HMAC-SHA256(secret, "<timestamp>." + raw_body)`，输出小写 hex。
注意要用**原始请求体字节**，不要先 `json.dumps` 再验。

```python
import hmac, hashlib, time

def verify(secret, headers, raw_body):
    ts = headers["X-WZ-Timestamp"]
    v1 = dict(x.split("=", 1) for x in headers["X-WZ-Signature"].split(",") if "=" in x)["v1"]
    want = hmac.new(secret.encode(), (ts + ".").encode() + raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(v1, want):
        return False
    return abs(time.time() - int(ts)) < 300   # 防重放窗口
```

密钥：

- **全局默认**：服务端首次启动生成，落盘 `/opt/wzmm/webhook.secret`（0600），可用 `GET /admin/webhook` 看掩码
- **按 key 独立**（推荐多租户）：每个 key 自己一份，互不影响

```bash
curl -X POST https://api.example.com/v1/webhook/secret \
  -H "Authorization: Bearer <key>" -d '{}'
# {"ok":true,"secret":"whsec_...","note":"store it now; GET only returns a masked value"}

curl https://api.example.com/v1/webhook/secret -H "Authorization: Bearer <key>"
# {"present":true,"secret_masked":"whsec_abcd...ef12","effective":"key","algorithm":"..."}

curl -X DELETE https://api.example.com/v1/webhook/secret -H "Authorization: Bearer <key>"
# 回落到全局密钥
```

不真出片也能验签（立刻投一条样例事件到你自己的地址）：

```bash
curl -X POST https://api.example.com/v1/webhook/test \
  -H "Authorization: Bearer <key>" -H "Content-Type: application/json" \
  -d '{"url":"https://your.host/hook"}'
# {"ok":true,"result":"http 200","delivery":"dlv_test_...","timestamp":1792456475,
#  "signature":"t=1792456475,v1=..."}
```

admin 轮换全局密钥：`POST /admin/webhook/rotate`，body 可带 `{"secret":"..."}`，不带则随机生成并落盘。

## 12. 大批量排产 / 取消 / 进度（R536）

一次最多 2000 条，自动按 64 条分块，共用一个 `batch_id`：

```bash
curl -X POST https://api.example.com/v1/videos/bulk \
  -H "Authorization: Bearer <key>" -H "Content-Type: application/json" \
  -d '{"defaults":{"resolution":"1080P","duration":6,"ratio":"16:9"},
       "items":["prompt A","prompt B","prompt C"]}'
# {"batch_id":"bat_...","total":3,"accepted":3,"rejected":0,"chunks":1,"chunk_size":64,
#  "poll":"/v1/videos/batch/bat_...","cancel":"/v1/videos/batch/bat_.../cancel","data":[...]}
```

先算账不排队（`dry_run`）：

```bash
curl -X POST https://api.example.com/v1/videos/bulk \
  -H "Authorization: Bearer <key>" -H "Content-Type: application/json" \
  -d '{"dry_run":true,"items":["a","b"]}'
# {"dry_run":true,"accepted":2,"chunks":1,"estimated_credits":160,"note":"dry_run: nothing was queued"}
```

取消（只取消还在排队的；已提交上游的会跑完）：

```bash
curl -X POST https://api.example.com/v1/videos/batch/<batch_id>/cancel -H "Authorization: Bearer <key>"
# {"batch_id":"bat_...","canceled":33,"still_running":31,"summary":{"running":31,"canceled":33}}
curl -X POST https://api.example.com/v1/videos/<id>/cancel -H "Authorization: Bearer <key>"
```

进度（含实时速率与预计剩余秒数）：

```bash
curl https://api.example.com/v1/videos/batch/<batch_id> -H "Authorization: Bearer <key>"
# {"summary":{"succeeded":12,"running":40,"queued":12},
#  "progress":{"total":64,"done":12,"elapsed_s":180,"rate_per_hour":240.0,"eta_s":780}}
```

selftest 已扩到 **64 项**（含签名验签、bulk dry_run 分块与估价、取消 404 边界）。
## 13. 运维面：一屏总览 + 老片库回收（R537）

```bash
curl -H "X-Admin-Token: <admin>" https://api.example.com/admin/summary
# {"pool":{"total":120,"dead":0,"parked":4,"trial_alive":55,"expired_alive":65,"expired_capacity":1256},
#  "tasks":{"succeeded":243,"failed":17},"today_created":259,
#  "throughput":{"last_1h":199,"last_24h":242},
#  "queue":{"running":0,"queued":0,"max_inflight":64,"headroom":64},
#  "disk":{"total_gb":103.9,"used_gb":76.4,"free_gb":27.4,"used_pct":73.6,
#          "videos_bytes":774575290,"videos_files":242},
#  "top_errors_24h":[{"error":"{\"code\": 402 ...","n":10}]}
```

老农场回收（**默认 dry_run，只算不删**）：

```bash
# 1) 查重（size + 头尾 256KB md5），只算能省多少
curl -X POST -H "X-Admin-Token: <admin>" \
  "https://api.example.com/admin/library/gc?dry_run=1&mode=dedupe"
# {"files_total":24494,"bytes_total":64227053031,"files_to_delete":0,"bytes_to_free":0,
#  "duplicate_groups":0,"disk":{"used_gb":76.4,"free_gb":27.4}}

# 2) 按龄清理：删除 30 天前的
curl -X POST -H "X-Admin-Token: <admin>" \
  "https://api.example.com/admin/library/gc?dry_run=1&mode=older_than_days&older_than_days=30"

# 3) 真删（必须显式 dry_run=0）
curl -X POST -H "X-Admin-Token: <admin>" \
  "https://api.example.com/admin/library/gc?dry_run=0&mode=older_than_days&older_than_days=30"
```

`mode` 三选一：`dedupe` / `older_than_days` / `both`；`keep=newest|oldest` 控制重复保留哪一份。
删除后会立刻刷新 `/v1/library` 缓存索引。

现状实测（2026-10-20）：**24,494 个文件 / 64.2 GB，重复 0 组**；按龄分布 0–7 天 6,721 个（18.67 GB）、7–30 天 17,773 个（45.56 GB）。
## 14. 单文件 SDK / CLI（R540）

下载即用（stdlib，零依赖）：

```bash
curl -o wzmm.py https://api.example.com/sdk/wzmm.py
export WZMM_KEY=sk-wz-...
export WZMM_BASE=https://api.example.com
python wzmm.py models
```

一条命令出片并落盘：

```bash
python wzmm.py submit "macro shot of frost on a window at dawn" -o frost.mp4
# {"id":"vid_...","status":"queued",...}
# saved frost.mp4 (1458412 bytes)
```

批量（并发 + 自动轮询 + 落盘）：

```bash
printf '%s\n' "prompt A" "prompt B" "prompt C" > prompts.txt
python wzmm.py batch prompts.txt --concurrency 24 --outdir out --res 1080P --dur 6
```

其它子命令：`get <id>`、`meta <id>`、`thumb <id> -o t.jpg`、`download <id> -o a.mp4`、`cancel <id>`、`usage --days 7`、
`webhook-verify --secret whsec_... --ts <ts> --sig <sig> < body.bin`。

## 15. 元数据与缩略图（R540）

```bash
curl -H "Authorization: Bearer <key>" https://.../v1/videos/<id>/meta
# {"bytes":1458412,"width":1920,"height":1080,"resolution":"1920x1080","is_1080p":true,
#  "duration_s":5.875,"codec_name":"h264","nb_frames":"141","bit_rate":"1980648",
#  "requested_resolution":"1080P","requested_duration":6,"thumb_url":".../thumb"}
```

`GET /v1/videos/{id}/thumb?at=1.0` 返回 640px JPEG（首个请求现场抽帧，之后读缓存）。
`meta` / `thumb` 支持 `Authorization` 头，也支持 `?key=`（方便 `<img src>`）。

selftest 已扩到 **79 项**。
## 16. 老片库浏览器 + 分辨率画像（R541/R542）

在浏览器里直接看图选片（`key` 放 query，方便 `<img>`）：

```
https://api.example.com/library?key=sk-wz-...&limit=24&sort=largest
```

- 支持 `q`（文件名筛选）、`sort=newest|oldest|largest|smallest`、`limit=12|24|48|96`、`offset`
- 每张卡片：缩略图、体积、时间、天数；按钮「查分辨率」（按需 ffprobe，不批量打）
- 点击缩略图/播放 → 走 `/v1/library/{fid}/content`（支持 Range）

单文件接口：

```bash
curl -H "Authorization: Bearer <key>" "https://.../v1/library/<fid>/meta"
# {"resolution":"1364x768","duration_s":6.43,"is_1080p":false,"age_days":12.4,"thumb_url":"..."}
curl -o t.jpg "https://.../v1/library/<fid>/thumb?at=1.0&key=<key>"
```

全库画像（`farm_scan.py` 跑一次，24 线程 413s 扫完 24,494 个文件，结果写 `.meta.json` 旁车缓存）：

```bash
curl -H "Authorization: Bearer <key>" "https://.../v1/library/stats"
# {"files":24494,"total_gb":64.23,
#  "groups":[{"res":"1364x768","n":4839,"gb":15.23,"pct":19.8,"avg_s":6.43}, ...],
#  "keep_only_1080p":{"files":1080,"gb":5.0,"reclaim_gb":59.23},
#  "disk":{"used_gb":76.4,"free_gb":27.4,"used_pct":73.6}}
```

**当前实测分布（24,494 条 / 64.23 GB）**

| 分辨率 | 条数 | 体积 | 占比 |
|---|---|---|---|
| 1364×768 | 4,839 | 15.23 GB | 19.8% |
| 1366×768 | 3,929 | 7.35 GB | 16.0% |
| 768×768 | 3,699 | 5.05 GB | 15.1% |
| 1024×768 | 3,257 | 8.65 GB | 13.3% |
| 768×1364 | 3,148 | 7.93 GB | 12.9% |
| **1920×1080** | **1,080** | **5.00 GB** | **4.4%** |
| 1440×1080 | 859 | 3.89 GB | 3.5% |
| 1080×1920 | 871 | 3.78 GB | 3.6% |

→ 只留真 1080P：保留 1,080 条 / 5 GB，可释放 **59.2 GB**（磁盘 73.6% → ≈16%）。
## 17. 实时进度 SSE + 成品画廊（R543）

### SSE（不用轮询，一个连接看到底）

```bash
curl -N "https://api.example.com/v1/videos/<id>/events?key=<key>"
# retry: 3000
#
# event: status
# data: {"id":"vid_...","status":"running","elapsed_s":186,"ready":false,"bytes":null,...}
#
# event: done
# data: {"id":"vid_...","status":"succeeded","ready":true,"bytes":1458412,...}
```

- 只在**状态变化**时推一帧（`status/ready/bytes` 三元组变才推），心跳 1s
- 终态发 `event: done` 后关闭；`?timeout=900` 可控上限（最长 1800s）
- 已实测穿透 nginx（返回头 `X-Accel-Buffering: no`，公网 `curl -N` 正常连续出帧）
- 权限与 `status` 一致：别人的任务 403，admin 放行

### 成品画廊

```
https://api.example.com/gallery?key=sk-wz-...&limit=24&status=succeeded
```

- 每张卡片：缩略图、提示词、分辨率、时长、体积、时间，外加「播放 / meta / status / SSE」四个入口
- 支持 `q`（提示词关键词）、`status=succeeded|failed|running|queued|canceled`、`limit=12|24|48|96`、`offset`
- 非 admin 只能看到自己 key 的任务（与租户隔离一致）

### 回调重试语义（实测）

接收端前两次返回 500、第三次 200，服务端行为：

```
attempt 1  dlv_febf3a7727d1a94e5394eec3  500   （间隔 2s）
attempt 2  dlv_febf3a7727d1a94e5394eec3  500   （间隔 5s）
attempt 3  dlv_febf3a7727d1a94e5394eec3  200   -> 记入 /opt/wzmm/webhook.log
```

**三次重试共用同一个 `X-WZ-Delivery`**，接收方按它去重即可；`X-WZ-Attempt` 递增。

selftest 已扩到 **105 项**；SDK 另带 6 个离线单测（`python -m unittest sdk/test_wzmm.py`）。


## 18. 只做 1080P（R550 默认硬约束）

服务端默认 `WZMM_ONLY_1080P=1`，所有新任务入口统一拦截非 1080P：

- `POST /v1/videos/generations`
- `POST /v1/videos/batch`
- `POST /v1/videos/bulk`（含 `dry_run` 校验）
- `GET /v1/pricing`（768P/2K 直接 400）

实测：

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  "https://api.example.com/v1/pricing?model=MiniMax-Hailuo-2.3&resolution=768P&duration=6" \
  -H "Authorization: Bearer <key>"
# 400
```

`/v1/models` 现在按真实能力返回，不再把 H3/H3-Max 写成支持 1080P：

```json
{"only_1080p":true,"data":[
  {"id":"MiniMax-Hailuo-2.3","resolutions":["768P","1080P"],
   "default_resolution":"1080P","supports_1080p":true,"available":true},
  {"id":"MiniMax-H3","resolutions":["768P","2K"],
   "supports_1080p":false,"available":false},
  {"id":"MiniMax-H3-Max","resolutions":["768P","2K"],
   "supports_1080p":false,"available":false}
]}
```

需要临时放开时，在 `/etc/systemd/system/wzmm-api.service` 里加
`Environment=WZMM_ONLY_1080P=0` 后 `systemctl daemon-reload && systemctl restart wzmm-api`。

## 19. 每日签到领积分（R550）

账号池 120 个号全部带 `token + ruid`，已接入官方签到接口：

- `GET /minimax-cloud/api/v1/signin/status`
- `POST /minimax-cloud/api/v1/signin/claim`

### 手动触发

```bash
# 只看今天能不能领，不写池子
curl -X POST "https://api.example.com/admin/checkin?dry_run=1&limit=5" \
  -H "X-Admin-Token: <admin>"

# 真领并原子回写 pool.json（自动备份）
curl -X POST "https://api.example.com/admin/checkin" \
  -H "X-Admin-Token: <admin>"

# 看最近一次结果
curl "https://api.example.com/admin/checkin" \
  -H "X-Admin-Token: <admin>"
```

### 首轮实测（2026-10-20 01:27 UTC）

```json
{"ok":true,"write":true,"accounts":120,"duration_s":7.81,
 "counts":{"claimed":114,"already":4,"no_today":2},
 "awarded_points":47400,"changed":118}
```

- 114 个号领取成功，共 **47,400 分**
- 4 个号今天已经领过（`claim_result=2`，幂等）
- 2 个号今天没有签到面板（`no_today`）
- 池子累计 `points` 从 42,800 涨到 **90,200**
- 写前自动备份：`/opt/wzmm/pool.json.bak.checkin.<时间戳>`
- 写入用临时文件 + `os.replace` 原子替换，服务端再 `POST /admin/reload-pool?full=1`

### 定时器

`wzmm-checkin.timer` 已启用，每天两次（UTC）：

```
00:10 UTC  -> 08:10 CST
16:10 UTC  -> 00:10 CST
```

```bash
systemctl list-timers wzmm-checkin.timer
systemctl status wzmm-checkin.service
tail -f /opt/wzmm/checkin.log
```

签到是幂等的：已领过的号返回 `claim_result=2`，不会重复加分。

## 20. 当前回归

```bash
/opt/wzmm/venv/bin/python /opt/wzmm/selftest.py
# [2026-10-20 02:54:11] 132/132 passed
```

新增覆盖：`only_1080p` 元数据、768P 定价/提交拦截、`/admin/checkin` 状态接口、片库/清单、并发与看门狗告警。
## 21. 片库 / 并发 / 过期号额度 / 告警（R553–R556）

### 21.1 片库（老农场 24,494 条）

```bash
# 只列 1920x1080
curl -s "https://api.example.com/v1/library?res=1080p&limit=50&offset=0" \
  -H "Authorization: Bearer $KEY"

# 统计（按分辨率分组 + 可回收空间）
curl -s "https://api.example.com/v1/library/stats" -H "Authorization: Bearer $KEY"

# 批量清单：csv / txt（txt 每行自带 ?key=，可直接 wget -i）
curl -s "https://api.example.com/v1/library/manifest?res=1080p&format=csv" -H "Authorization: Bearer $KEY" -o lib1080.csv
curl -s "https://api.example.com/v1/library/manifest?res=1080p&format=txt" -H "Authorization: Bearer $KEY" -o lib1080.txt
wget -c -i lib1080.txt -P ./videos

# 单条下载 / 元信息 / 缩略图
curl -sO "https://api.example.com/v1/library/<fid>/content?key=$KEY"
curl -s  "https://api.example.com/v1/library/<fid>/meta"    -H "Authorization: Bearer $KEY"
curl -sO "https://api.example.com/v1/library/<fid>/thumb?key=$KEY"
```

### 21.2 并发与配额

| 项 | 值 |
|---|---|
| `WZMM_MAX_INFLIGHT` | 256（实测 256 并发 100% 成功，504.9s，约 30.4 条/分） |
| 有效并发 | `min(max_inflight, max(6, available*3))` |
| 单 key 并发 | `max_concurrent`（建 key 时指定，默认 32） |
| 单 key 速率 | `rpm`（建 key 时指定） |
| 过期号日额度 | `WZMM_EXPIRED_CAP=100`/号/天（实测单号 125/125 accepted） |
| 磁盘水位 | `WZMM_MIN_FREE_GB=20` 低于即停收；成品 TTL `WZMM_TTL_DAYS=3` |

压池杠杆（不影响账号本身）：

```bash
curl -X POST "https://api.example.com/admin/park" \
  -H "X-Admin-Token: $ADMIN" -H "Content-Type: application/json" -d '{"emails":["user@example.com"]}'
curl -X POST "https://api.example.com/admin/unpark" -H "X-Admin-Token: $ADMIN"
```

### 21.3 告警（R556）

四类看门狗，每 120s 扫一次：

| key | 触发 | 级别 | 冷却 |
|---|---|---|---|
| `pool_low` | `available < 30` | crit | 900s |
| `disk_low` | 剩余磁盘 `< 18GB` | crit | 900s |
| `stuck` | 单任务 running `> 40min` | warn | 1800s |
| `fail_burst` | 15 分钟内失败 `> 10` | warn | 900s |

```bash
curl -s "https://api.example.com/admin/alerts?limit=50" -H "X-Admin-Token: $ADMIN"
curl -s -X POST "https://api.example.com/admin/alert-test" -H "X-Admin-Token: $ADMIN"
```

Webhook 热加载：写一行 URL 进 `/opt/wzmm/alert_webhook.txt` 立即生效（无需重启），删除即关闭；
告警同时落盘 `/opt/wzmm/alerts.log`。推送体：

```json
{"ts": 1792464874, "level": "info", "key": "test", "msg": "watchdog self-test"}
```

已做端到端验证：本地接收器 8901 收到上述 JSON，`webhook:true`。

### 21.4 建 key

```bash
curl -X POST "https://api.example.com/admin/keys" \
  -H "X-Admin-Token: $ADMIN" -H "Content-Type: application/json" \
  -d '{"name":"my-app","rpm":180,"max_concurrent":64,"quota":0,"daily_quota":0}'
# -> {"id":"key_...","key":"sk-wz-...","name":"my-app","rpm":180,...}

curl -s "https://api.example.com/admin/keys" -H "X-Admin-Token: $ADMIN"
curl -X DELETE "https://api.example.com/admin/keys/<kid>" -H "X-Admin-Token: $ADMIN"   # 软禁用
```
### 21.5 全量接口清单 + 64 并发实测（R558）

**接口清单**：`/opt/wzmm/ENDPOINTS.md`（105 行，53 条路径，按管理面 / 片库 / 视频 / Webhook / 工具 / 配额元信息 / 页面分组，标注认证方式）。

**公网 HTTPS 逐条验（2026-10-20 03:05 CST，带新 key）**

| 路径 | 状态 | 字节 |
|---|---|---|
| `/healthz` | 200 | 265 |
| `/v1/models` | 200 | 860 |
| `/v1/pricing` | 200 | 359 |
| `/v1/usage` | 200 | 237 |
| `/v1/library/stats` | 200 | 1,288 |
| `/openapi.json` | 200 | 24,262 |
| `/v1/videos/<id>/content?key=` | 200 | 1,012,060 |

**SDK 端到端**（`/sdk/wzmm.py`，纯标准库）

```bash
export WZMM_KEY=sk-wz-...
python wzmm.py models
python wzmm.py library-stats
python wzmm.py manifest --res 1080p --format txt --embed-key -o urls.txt
python wzmm.py download vid_e0f8fffb688d9326c31009c4 -o out.mp4   # 1,012,060 bytes，与源一致
```

**64 并发 burst 实测**（单 key，`POST /v1/videos/bulk` 64 条，1080P/6s）

| 指标 | 值 |
|---|---|
| 接收 | 64 accepted / 0 rejected，intake < 1s（`batch=bat_c597d9cc917cd7e1`） |
| 成功 | **64/64 = 100%** |
| 时延 | p50 **163s** / p90 **188s** / max 351s |
| 完成分布 | ≤180s 52 条，≤210s 59 条，3 条拖到 351s |
| 全清 | 351.2s → 10.9 条/分（含长尾） |
| 峰值段 | 52 条 / 180s ≈ **17.3 条/分** |
| 产物体积 | 211.0 MB，平均 3.30 MB/条 |

**长尾归因**：6 个 `sdjw.monster` 号在并发瞬间吃到上游 transient 503 → `POOL.note(False)` 降权 → 重试换号，形成第二波。属上游限流，不是调度器排队；`bad503.json` 已记录，冷却后自动恢复。

**结论**：单 key 64 并发可打满、零失败；更大 backlog（256）才能吃满 30 条/分的流水线，64 条受「每号 1 并发 + 单波时长」限制。
### 21.6 断崖演练升级：纯过期号池 60 并发（R558）

**目的**：今晚 23:59 UTC 49 个试用号到期后，池子只剩过期号，验证此时仍能跑满并发。

**步骤**

```bash
# 1) 取全部试用号并 park 3600s
GET  /admin/pool            -> trial_left_h>0 的 55 个邮箱
POST /admin/park {"emails":[...],"seconds":3600}
# -> available 65 / parked 55 / eff_max_inflight 195

# 2) 单 key 提交 60 条 1080P/6s
POST /v1/videos/bulk        -> 60 accepted / 0 rejected / intake 1.04s
#    batch = bat_62338931cb56bc30

# 3) 跑完 unpark
POST /admin/unpark          -> unpark 55，available 120 / eff 256
```

**结果（DB 口径，t0=首条创建，t_end=末条完成）**

| 指标 | 值 |
|---|---|
| 成功 | **60/60 = 100%** |
| 总耗时 | **305.6s**（11.8 条/分含长尾） |
| 时延 | p50 **166s** / p90 **208s** / max 305s |
| 瞬时 | 前 210s 完成 57 条 ≈ **16.3 条/分** |
| 用号 | 60 个不同过期号（一人一条，单波） |
| 产物 | 191.4 MB，平均 3.19 MB/条 |

**结论**

1. 过期号池 100% 可跑，60 并发零失败 —— 今晚断崖不会停摆，只是日额度从「试用不限」变成「100/号/天」。
2. 到期后可用号仍是 120（65 过期 + 49 转过期），`eff_max_inflight` 回到 256；`daily_room` 从 61,448 降到约 12,000/天。
3. 单波 60–64 条的吞吐上限由「每号 1 并发 + 单条约 165s」决定，全清 300–350s；要吃满 30 条/分需要 ≥256 条 backlog 让流水线连续。
4. 演练后 `available 120 / parked 0 / eff 256`，无遗留 park。
### 21.7 把 1,080 条 1080P 老片一次性拉回本地（R558 实测）

**方式 A：CSV + 循环（推荐，文件名正确）**

```bash
K=sk-wz-...                                   # 你的 key
B=https://api.example.com

curl -s "$B/v1/library/manifest?res=1080p&format=csv&key=$K" -o lib1080.csv
wc -l lib1080.csv                             # 1081 = 表头 + 1080 条

mkdir -p videos1080p && cd videos1080p
tail -n +2 ../lib1080.csv | while IFS=, read -r id name bytes mb res cu tu; do
  [ -f "$name" ] || curl -s -o "$name" "$cu?key=$K"
done
```

- 实测 3 条：`t-3115827....mp4` 7,095,719 B、`t-32ba08f....mp4` 1,782,140 B、`t-36054ca....mp4` 2,034,151 B，与 `/v1/library` 返回的 `bytes` 完全一致
- 断点续传：`curl -C - -o "$name" "$cu?key=$K"`
- 并发 4 路：

```bash
tail -n +2 ../lib1080.csv | awk -F, '{print $1"|"$2"|"$6}' | \
  xargs -P4 -I{} bash -c 'IFS="|" read -r id name cu <<< "{}"; [ -f "$name" ] || curl -s -o "$name" "$cu?key='"$K"'"'
```

**方式 B：txt 清单直接喂 wget（快，但文件名会变成 `content?key=...`）**

```bash
curl -s "$B/v1/library/manifest?res=1080p&format=txt&embed_key=1&key=$K" -o lib1080.txt
wc -l lib1080.txt                             # 1080
wget -c -i lib1080.txt -P ./videos1080p
# 想保留原文件名：wget --content-disposition（需服务端带 header，本服务未设）
```

**注意**

- 参数名是下划线 **`embed_key=1`**（写成 `embed-key` 不会内嵌 key）
- `txt` 必须同时带 `key=` 与 `embed_key=1` 才会生成 `...content?key=sk-wz-...`
- 总量 1,080 条 / 5.00 GB，单条约 1.7–7.1 MB
- 只看不打包：`/library` 网页画廊，`/gallery` 实时成品墙
### 21.8 调度器修复：200 并发吞吐 21.9 → 31.9 条/分（R560）

**问题**：`Pool.pick()` 把「试用号」当成绝对优先级 —— 排序键第一项是 `0 if trial else 1`。结果一批 200 条任务全压在 49 个试用号上，65 个过期号全程空闲，任务只能排队跑 4 波。

**改法**（`app.py`，两处）

```python
# 新增：运行中任务数（1s 缓存），只读 DB，不会泄漏状态
def _active_map():
    rows = db().execute("select email, count(*) n from tasks where status='running' "
                        "and email is not null group by email").fetchall()
    return {r["email"]: int(r["n"]) for r in rows}

# pick()：先按「在跑条数」摊平，试用号只做同档优先
cand.sort(key=lambda a: (act.get(a["email"], 0),
                         0 if self.trial.get(a["email"], 0) > now else 1,
                         self.streak.get(a["email"], 0),
                         self.load.get(a["email"], 0),
                         self.usage.get(a["email"], [0, 0])[1],
                         a["email"]))
```

`self.load`（pick 时 +1、提交后 -1）保留，用来在同一批并发提交里继续分散。

**A/B 实测（同 200 条 1080P/6s，同一 key，同一池）**

| 指标 | 修复前 | 修复后 | 变化 |
|---|---|---|---|
| 成功 | 200/200 | 200/200 | — |
| 总耗时 | 547.6s | **376.7s** | **-31%** |
| 总吞吐 | 21.9 条/分 | **31.9 条/分** | **+46%** |
| 稳态吞吐 | 25.7 条/分 | **37.7 条/分** | +47% |
| p50 时延 | 342s | **179s** | **-48%** |
| 用号数 | **49** | **114** | **2.3×** |
| 产物 | 615.8MB | 669.0MB | — |

**64 条 burst 同步验证**：351.2s（max 351s）→ **207.3s（max 203s）**，长尾消失。

**回滚**

```bash
ls /opt/wzmm/app.py.bak.r560*
cp /opt/wzmm/app.py.bak.r560_<ts> /opt/wzmm/app.py && systemctl restart wzmm-api
```

**回归**：`selftest.py` → 132/132 passed（03:29:44），healthz 全绿。
### 21.9 磁盘保护改造：先告警、再删；软模式不碰当天成品（R561/R562）

**背景**：磁盘只剩 21 GB，而 `_disk_guard` 的规则是「free ≤ 20 GiB 就删最老的 mp4，删到 > 22 GiB 才停」。原来的 `disk_low` 告警阈值是 **18 GB（比删除线还晚）**，等于删完才通知；而且删除过程**不写任何日志/告警**。

**R561：先告警 + 删了必须说**

| 告警 key | 阈值 | 级别 | 冷却 |
|---|---|---|---|
| `disk_warn` | free < **25 GiB** | warn | 1h |
| `disk_low` | free < **18 GiB** | crit | 15min |
| `disk_gc` | 每次自动删除后 | crit | 5min |

`disk_gc` 的正文带四个数：删除条数、释放 MB、**保留的“太新”文件数**、当前剩余 GiB、是否紧急模式。

**R562：软模式 / 紧急模式**

```
free ≤ 20 GiB   → 软模式：只删 mtime > 6 小时的 mp4，当天的成品一律保留
free ≤ 12 GiB   → 紧急模式：不受 6 小时限制，按最老优先删到 22 GiB
```

即：**只有磁盘真的要满了，才允许动当天的新片**；平时宁可少删、并持续告警。

`/admin/health` 的 `disk` 新增字段：

```json
{"free_gib": 21.4, "min_free_gib": 20.0, "ttl_days": 3.0,
 "auto_gc_below_gib": 20.0, "gc_armed": true}
```

`gc_armed=true` 表示已进入「随时可能触发」区间（free ≤ 22 GiB），运维看这一格就够。

**本次腾挪（不动任何成品）**

```bash
journalctl --vacuum-size=300M     # /var/log 1.5G -> 514M
apt-get clean
# 可用空间 21G -> 22G（free_gib 20.0 -> 21.4）
```

**实测**：`disk_warn` 在 free 20.0 GiB 时准时触发（`/admin/alerts` 可查）；`selftest 132/132 passed`。

**备份**：`app.py.bak.r561_20261020_034720`、`app.py.bak.r562_20261020_034803`。
### 21.10 存储占用报表 + 老农场清理工具（只读报表，默认 dry-run）

**报表文件**：`/opt/wzmm/REPORT_STORAGE.md`、`REPORT_STORAGE.json`（生成脚本 `/root/_rep.py`，只读扫描）。
单位 GB = 1e9 字节。

#### 老农场 `/opt/h3v2/data/render/out`

- 文件数 **24,494**，总体积 **64.23 GB**；时间跨度 2026-10-12 → 2026-10-13（7–8 天前）

| 周龄 | 条数 | 体积 | 占比 |
|---|---|---|---|
| 3–7 天 | 4,281 | 12.13 GB | 18.9% |
| 7–14 天 | 20,213 | 52.10 GB | 81.1% |

| 分辨率 | 条数 | 体积 | 占比 |
|---|---|---|---|
| 1364x768 | 4,839 | 15.23 GB | 23.7% |
| 1024x768 | 3,257 | 8.65 GB | 13.5% |
| 768x1364 | 3,148 | 7.93 GB | 12.3% |
| 1366x768 | 3,929 | 7.35 GB | 11.4% |
| 768x768 | 3,699 | 5.05 GB | 7.9% |
| **1920x1080** | **1,080** | **5.00 GB** | **7.8%** |
| 1440x1080 | 859 | 3.89 GB | 6.1% |
| 1080x1920 | 871 | 3.78 GB | 5.9% |
| 其余 9 种 | 2,812 | 5.35 GB | 5.3% |

- 同字节数分组：74 组 / 148 个文件，全去重只能省 **0.15 GB**（基本没有重复片）

#### 本服务成品 `/opt/wzmm/videos`

- 1,594 条 / **6.54 GB**，全部 ≤1 天；无同尺寸重复

#### 处置测算（**仅测算，未执行**）

| 方案 | 保留 | 删除 | 释放 |
|---|---|---|---|
| A 只留 1920×1080 | 1,080 条 / 5.00 GB | 23,414 条 | **59.23 GB** |
| B 同字节数去重 | 保留一份 | 74 条 | 0.15 GB |
| C A+B | 1080P 去重后 | — | 约 59.37 GB |

#### 工具：`/opt/wzmm/purge_legacy.py`

```bash
/opt/wzmm/venv/bin/python /opt/wzmm/purge_legacy.py                    # 只统计（默认 dry-run）
/opt/wzmm/venv/bin/python /opt/wzmm/purge_legacy.py --limit 100        # 看前 100 条最老的能省多少
/opt/wzmm/venv/bin/python /opt/wzmm/purge_legacy.py --apply --yes      # 真删（先写 manifest）
```

- 参数：`--keep-res`（默认 `1920x1080`）、`--unknown keep|drop`（无侧车文件，默认 keep）、`--limit N`
- 安全闸：`--apply` 不带 `--yes` 直接拒绝（实测 `refusing: --apply requires --yes`，且不写 manifest）
- 执行前先落 `/opt/wzmm/purge_legacy_manifest.csv`（id,name,bytes,res,mtime），删完打印释放量与剩余空间
- 删除范围**仅限** `LEGACY_DIR` 下的 mp4 与同名 `.meta.json`；不碰 `/opt/wzmm/videos`、不碰 DB

**实测（dry-run）**：`keep 1,080 (5.00 GB) / drop 23,414 (59.23 GB) / unknown 0`。
### 21.11 SDK v2：53 条接口全覆盖（单文件、纯标准库）

`/sdk/wzmm.py` 升级到 v2（558 行，**52 个子命令 + 通用 `api` 直通**），下载即用：

```bash
curl -O https://api.example.com/sdk/wzmm.py
export WZMM_KEY=sk-wz-...
export WZMM_ADMIN=<admin token>          # 只在调 /admin/* 时需要
python wzmm.py --help
```

**环境变量**：`WZMM_BASE`（默认 `https://api.example.com`）、`WZMM_KEY`、`WZMM_ADMIN`；
也可用 `--base / --key / --admin-token` 覆盖。

| 分组 | 子命令 |
|---|---|
| 元信息 | `models` `pricing` `usage` `tools` `tool <name> <json>` |
| 生成 | `submit` `get` `meta` `wait` `cancel` `download` `thumb` `events` |
| 批量 | `batch <file>`（并发池）`bulk <json>`（≤2000）`batch-create <json>`（≤64）`batch-status` `batch-cancel` `video-list` |
| 片库 | `library` `library-stats` `library-meta` `library-thumb` `library-download` `manifest` |
| Webhook | `secret`（`--set/--delete`）`webhook-test <url>` `webhook-verify` |
| 管理面 | `admin-health` `admin-alerts` `admin-alert-test` `admin-keys` `admin-key-create` `admin-key-del` `admin-pool` `admin-park` `admin-unpark` `admin-tasks` `admin-stats` `admin-summary` `admin-usage` `admin-credits` `admin-checkin` `admin-token-age` `admin-liveness` `admin-merge` `admin-reload` `admin-revive` `admin-gc` `admin-webhook` `admin-webhook-rotate` |
| 兜底 | `api <METHOD> <path> [json]` —— 没封装的端点直接打 |

**常用**

```bash
python wzmm.py submit "a lantern over a lake" -o out.mp4         # 提交 + 等 + 下载
python wzmm.py bulk items.json --dry-run                          # 只校验不排队
python wzmm.py batch-status bat_xxxx
python wzmm.py manifest --res 1080p --format csv -o lib.csv
python wzmm.py admin-key-create --name my-app --rpm 180 --max-concurrent 64
python wzmm.py admin-park --n 20 --seconds 600
python wzmm.py api GET /v1/pricing
python wzmm.py api POST /v1/tools/call '{"name":"matrix_submit_video_generation"}'
```

**实测（2026-10-20 03:53 CST）**

| 命令 | 结果 |
|---|---|
| `models` / `pricing` / `usage` / `tools` | 200，字段完整 |
| `api GET /v1/pricing` | 200 |
| `admin-health` / `admin-pool` / `admin-keys` / `admin-summary` | 200 |
| `library --res 1080p --limit 2` | total 1080 |
| `manifest --res 1080p --limit 2` | 2 行直链 |
| `bulk --dry-run`（3 条） | accepted 3 / rejected 0，`estimated_credits 240` |
| `batch-create --dry-run`（2 条） | accepted 2 |
| `video-list --limit 2` | 200 |
| `library-thumb` | 28,837 bytes |
| `webhook-test http://127.0.0.1:8901/hook` | `ok:true, result:"http 200"`，接收端实收签名事件 |

**错误处理**：401 / 403 / 连接失败统一输出 `error: http <code>: <body>` 并 `exit 1`，不再抛 traceback。

**备份**：`/opt/wzmm/sdk/wzmm.py.bak.r564_*`、`.bak.r564b_*`。
### 21.12 SDK v2.1：重试退避 + bulk-download（一条命令把一批 prompt 变成文件）

**全局开关**（`wzmm.py` 652 行）

| 参数 | 默认 | 说明 |
|---|---|---|
| `--retries N` | `WZMM_RETRIES=2` | 429/500/502/503/504 与连接失败的重试次数 |
| `--timeout S` | 120 | 默认请求超时（`submit`/`events` 等各自有更大的专用值） |
| `--quiet` | off | 关掉进度输出，只留最终 JSON |

**重试策略**

- 触发：HTTP `429 / 500 / 502 / 503 / 504`、连接失败（URLError）
- 顺序：先看 `Retry-After`，没有就 `1.5^n + jitter`，单次上限 30s
- 重试耗尽后统一抛 `error: http <code>: <body>`（连接类为 `http 0`），退出码 1

实测：

```bash
$ wzmm.py --retries 1 --timeout 3 api GET http://127.0.0.1:9/x
retry 1/1 after [Errno 111] Connection refused in 1.1s
error: http 0: connection failed: [Errno 111] Connection refused
```

**新命令 `bulk-download`**

一次 `POST /v1/videos/bulk` 提交整批 → 轮询 `batch-status` → 多线程下载成功项：

```bash
printf '["a paper boat in rain","misty pine forest at dawn","autumn leaves close-up"]' > items.json
python wzmm.py bulk-download items.json --outdir out --workers 8
```

输出：

```json
{"batch":"bat_xxx","total":3,"succeeded":3,"downloaded":3,"failed":0,"bytes":8455224,"errors":[]}
```

文件命名 `0000_<task_id>.mp4`、`0001_...`，可直接接 ffmpeg / 剪辑流水线。

**实测（2026-10-20 03:58 CST）**

| 项 | 值 |
|---|---|
| batch | `bat_c7ea8d926522b963` |
| 结果 | 3/3 succeeded，3 个文件全下载 |
| 体积 | 8,455,224 bytes（4,302,058 / 2,185,996 / 1,967,170） |
| 耗时 | 2m42s（含生成 + 下载） |
| 命名 | `0000_vid_37f0....mp4` 等 |
| 磁盘占用 | 8.1M（`/tmp/_bdout`，测完待清） |

**用法建议**

- 少量：`submit`（单条，可 `-o` 直接落盘）
- 中量：`batch`（逐条提交 + 并发池，可按条控制）
- 大量：`bulk-download`（一次提交整批，最省往返）

**备份**：`/opt/wzmm/sdk/wzmm.py.bak.r565_*`。


---

## 22. 一键部署 + 全量接口覆盖实测（R567）

### 22.1 部署器 `deploy.sh`

服务器上：`/opt/wzmm/deploy.sh`（116 行级脚本，幂等，可重复执行）。

```bash
./deploy.sh --verify                    # 只看健康，不改任何东西
./deploy.sh --dry-run                   # 打印将要做的动作，零写入
sudo ./deploy.sh                        # 安装 / 升级（幂等：已存在的文件/依赖/unit 不动）
sudo ./deploy.sh --force                # 强制覆盖 payload 与 unit
sudo ./deploy.sh --bundle-out out.tgz   # 把现网打包（含 pool.json，属机密）
sudo ./deploy.sh --bundle out.tgz       # 在另一台机器上从包安装
sudo ./deploy.sh --smoke --prefix /tmp/x --port 18808
                                        # 装到临时目录并真启动 + healthz 自检
```

它会做的事：apt 补齐 `python3-venv/pip/ffmpeg/curl/sqlite3` → 建 `videos/ sdk/ logs/` → 复制 payload →
`pool.json` 缺失时写空池 → 建 venv 装 `fastapi uvicorn[standard] curl_cffi requests cryptography` →
生成/沿用 admin key（**已安装时从 unit 里沿用，不会旋转**）→ 渲染 `wzmm-api.service`（内容不变则不重写）→
开机自启 + 5 个辅助 timer（checkin/renew/equity/expiry/selftest）→ 40 秒内等 healthz 200。

**实测**

| 场景 | 结果 |
|---|---|
| 现网 `--dry-run` | 输出 5 步计划，0 写入；admin key 判定为「adopted from the installed unit (no rotation)」 |
| 空目录 `--smoke`（/tmp/wzmmtest:18808） | venv+依赖 91s 建好，healthz 200，smoke ok |
| 再跑一次（幂等） | **7s** 完成，`python deps: ok`、payload 不覆盖、空池不被改写 |
| `--bundle-out` | `wzmm-bundle-1020.tgz` **116K**，含 16 个文件（app/agent_mm/checkin/sdk/docs/pool.json） |
| 从包安装 `--bundle ... --smoke`（/tmp/wzmmbund:18809） | 33s 装完并启动，healthz `{"ok":true,"accounts":120,"points_total":90200,"only_1080p":true}` |

### 22.2 全量接口覆盖（COVERAGE.md）

用 `/openapi.json` 驱动，逐条真实调用 **53 路径 / 58 操作**，安全化处理破坏性接口：

- `library/gc` → 只跑 `dry_run=1`
- `admin/checkin` → `dry_run=1&limit=1`
- `admin/park` → 停 1 个号 20s 后立刻 `unpark`
- `admin/revive` → 调用前后备份/还原 `dead.jsonl`
- `admin/webhook/rotate` → 显式写入新 secret 并存到 `/opt/wzmm/.admin_webhook_secret.r567`（600）
- 取消类 → 先建 1 条任务/1 个 batch，再 cancel

结果：**62 次调用，0 个 5xx/传输失败**。仅 7 个非 200，全部是预期的鉴权/归属语义：

| 返回 | 接口 | 原因 |
|---|---|---|
| 401 ×3 | `/gallery`、`/library`、`/metrics` | 页面类需 `?key=`（已修正文档表述） |
| 403 ×4 | 别人 key 的任务 `meta/thumb/events/content` | key 隔离生效；换 owner key 后 4/4 全 200 |

产物：`/opt/wzmm/COVERAGE.md`（逐条状态码 + 耗时）、`/opt/wzmm/COVERAGE.json`（机器可读）。

### 22.3 本轮新增的小改动

- `GET /v1/usage` 的 `totals` 增加 `videos_on_disk` 与 `bytes`（只 stat 本 key 的成品，便于对账）
- `GET /admin/usage?by_key=1` 新增按 key 汇总：`n/ok/bad/canceled/success_rate/first_ts/last_ts`
- SDK v2.2：全局 `--json`（单行紧凑 JSON，隐含 `--quiet`）；`admin-usage --days N --by-key`

```bash
python wzmm.py --json admin-usage --days 2 --by-key | jq '.data[:3]'
python wzmm.py --json usage --days 7
```

### 22.4 产能跑道 `/admin/capacity`（R568）

```bash
curl -s -H "X-Admin-Token: $WZMM_ADMIN" https://api.example.com/admin/capacity | jq
python wzmm.py --json admin-capacity
```

返回：账号结构（trial/expired/dead/parked）、今日可跑条数、近 1h/24h 实际产出、积分、以及 **+1/+3/+7 天**的
产能投影（试用号到期后自动按 `min(EXPIRED_CAP, credits/80)` 折算）。

**实测（2026-10-20 04:16 CST）**

| 项 | 值 |
|---|---|
| 账号 | 120（trial 55 / expired 65 / dead 0 / parked 6） |
| 今日可跑 | 61,292（trial 侧 clamp 1000/号） |
| 24h 实际产出 | 1,598 条（1h 内 535 条 ≈ 8.9 条/分） |
| +1 天投影 | trial 6 / expired 114 → 15,820 条/日 |
| +3 天投影 | trial 0 / expired 120 → 10,420 条/日 |
| 按当前燃烧率 | 产能可支撑 **9.9 天** |

同时新增看门狗规则：`trial_expiring_24h >= 25` 且 `expired_alive < 90` 时告警 `trial_expiry`（6h 冷却），
避免「今晚到期、明早才发现」。


---

## 23. 轮询器修复：256/128 压测、p50 500s → 180s（R569）

### 23.1 问题

`poller()` 原来每 5 秒把所有 `running` 任务**全量重查一遍**，且只开 **16** 个线程，
再逐个 `f.result(timeout=40)` 串行等待。250 条在跑时，等于每轮要串 250/16 ≈ 16 批 × 最多 40s，
同一个任务两次查询之间能被拉开到几分钟——上游早就出片了，我们还在排队看结果。

### 23.2 改法

| 项 | 旧 | 新 |
|---|---|---|
| 轮询线程 | 16 | **48** |
| 调度 | 每轮全量 | 每任务独立到期时间：<5min 每 20s、<30min 每 30s、更久 60s |
| 单轮上限 | 无（可达 400+） | **120** 条 |
| 主循环间隔 | 5s | 2s（只挑到期的，不空转） |
| 会话表 | 无 | `_POLL_LAST`，>2000 条自动修剪 |

### 23.3 实测（同一批任务、重启前后 A/B）

批次 `bat_ab71d6c891beb613`（256 条，1080P/6s）：

| 阶段 | 窗口 | 完成数 | 速率 |
|---|---|---|---|
| 修复前 | ~9 min | 26 | ≈ **2.9 条/分** |
| 重启后 | 3 min | 71 | ≈ **23.7 条/分** |
| 整批合计 | 818s | **256/256 成功** | 18.8 条/分（含慢启动） |

p50 500.8s / p90 715.3s（被修复前的窗口拖高），落盘 **792,296,727 B**，用号 114。

### 23.4 干净复测（修复后新批次）

`bat_240461ae42292af8`（128 条）：

| 项 | 值 |
|---|---|
| 结果 | **128/128 succeeded**，0 失败 |
| 总耗时 | **302.2s** |
| 平均 | **25.4 条/分** |
| 末段稳态 | **1,525 条/小时（25.4 条/分）** |
| p50 / p90 / max | **180.0s / 266.9s / 296.4s** |
| 用号 | 108 个 |
| 落盘 | 403,136,682 B |

对照历史：R560 的 200 条是 31.9 条/分、p50 179s —— 现在 p50 回到 180s，说明瓶颈重新回到上游生成速度，
而不是我们自己的轮询。按 114 个可用号 × 180s 估算，理论上限 ≈ **38 条/分**。

### 23.5 备份还原演练

```
python3 /opt/wzmm/backup.py         → wzmm_20261020_042118.tar.gz (356KB)
tar xzf ... -C /tmp/restore_test    → wzmm.db + pool.json + apikey.txt + admin.key
对比：tasks 1874/1874、succeeded 1602/1602、keys 88/88、accounts 120/120
pragma integrity_check → ok
```

还原目录已清理；备份保留最近 14 份（`/opt/wzmm/backups`，700）。

### 23.6 补池管线的小修（本机侧）

`work/r2_minimax/_wz_replenish.py` 在服务器繁忙时算不出「该补几个」：

- `_wz_ssh.py` 连接超时 20s → **45s**（banner/auth 60s）
- `ask_needed()` 加 **3 次重试**（每次 180s 上限，退避 10s/20s）
- 修后实测：`{'alive': 120, 'deficit': 40, 'needed': 12, 'cap': 12, 'days_to_first_death': 39.7}`

注册侧仍被 reCAPTCHA Enterprise 拦（`1200063`），定时任务每 3h 试一次、失败不动池子。


---

## 24. 试用到期当晚的护栏 + 接口面防漂移（R570）

### 24.1 今晚到期会发生什么（已有实证，不是推测）

| 证据 | 值 |
|---|---|
| 最近 1 小时成功任务按账号类型 | trial **305** / expired **229**（过期号已承担 43% 流量） |
| 最近 24 小时 | trial 1,605 / expired 379，涉及 132 个不同账号 |
| 昨夜 00:10 UTC 探针 | 2 个纯过期号各出 1 条 **1920×1080**（`1080P OK AFTER TRIAL`，3.85 MB / 4.27 MB） |

也就是说「trial_exp 归零」只是把号从 A 桶挪到 B 桶，B 桶已经在正常干活。

### 24.2 探针本身有个计数 Bug（已修）

旧过滤 `if te and te < now` 会把 `trial_exp=0` 的号当成「未知」跳过；而这批 65 个过期号的
`trial_exp` 恰好全是 0 → 探针候选数 **0**，还会被新告警误判成失败。

修复后：

- `te == 0` 按「无试用」处理（和 `Pool.stats()` 的 expired_alive 口径一致）
- 候选为 0 时写 `ok_all: null / skipped: no expired candidates`，**退出码 0**，不报警
- 有结果时写 `ok / failed / ok_all`，失败退出码 **3**
- 看门狗新增两条：`expiry_probe_failed`（ok_all=false 且 24h 内，crit，6h 冷却）、
  `expiry_probe_stale`（超过 48h 没跑，warn）

**修复后实测**：`expired alive candidates: 65` → 1/1 成功 → `ok_all: true`，`exit=0`。

### 24.3 接口面防漂移 `check_surface.py`

三方对账：`/openapi.json` ↔ `sdk/wzmm.py` ↔ `ENDPOINTS.md`，路径模板统一归一化
（`%s` / `{id}` / `${...}` → `{}`），只把「文档里有、实现里没有」判为失败。

```
paths: openapi=54 sdk=57 docs=56 ops=59
SDK            missing=0
ENDPOINTS.md   missing=0
RESULT: OK   (exit 0)
```

SDK 补了两个能力，让「覆盖全量」名副其实：

```bash
python wzmm.py pages        # 6 个页面 + 3 个元接口的真实 URL
python wzmm.py status <id>  # /v1/videos/{id}/status（get 走 generations/{id}）
```

该检查已挂进 `selftest.py`（每 6 小时那次），目前自检 **133/133 passed**。

### 24.4 顺手补的文档缺口

`ENDPOINTS.md` 之前漏了 `/admin/capacity`（R568 新增），现已补行；surface 检查也会一直盯着它。


---

## 25. 异地备份 / 磁盘实测 / webhook 重投队列（R571）

### 25.1 异地备份（本机留一份）

服务器备份只落在同一块盘上，盘坏了就一起没。已把最新备份拉到爷爷这台机器：

```
C:\Users\Administrator\Documents\Codex\2026-10-16\wan\offsite_backups\
  wzmm_20261020_042118.tar.gz   355.8 KB
  SHA256SUMS.txt                59c8a672b6b189a05bf32490c68286c6…
```

本地解包校验：`integrity_check=ok`、tasks 1874、keys 88、accounts 120，与线上一致；解包目录已删，只留压缩包。

### 25.2 磁盘实测：自动 GC 其实救不了

刚统计 `/opt/wzmm/videos`：

| 项 | 值 |
|---|---|
| 现有 | 1,988 个 mp4 / 7.78 GB（均 3.9 MB） |
| GC 可删（≥6h，软模式） | **30 个 / 0.07 GB** |
| 受保护（<6h） | 1,958 个 / 7.71 GB |
| 空闲 | 21 GiB（红线 20 GiB） |

也就是说：**一旦跌破 20 GiB，自动 GC 只能腾出 0.07 GB，然后一直报警。**

按当前产量算账：2,000 条/天 × 3.9 MB ≈ **7.8 GB/天**；`TTL_DAYS=3` 的稳态占用 ≈ **23 GB**，
而空闲只有 21 GB —— 两天内必然撞线。可选解法（都要爷爷点头）：

1. `WZMM_TTL_DAYS` 3 → 2（稳态 ≈15.6 GB，能长期跑）
2. 清老农场非 1080P 老片（+59.23 GB）
3. 清本轮压测的 384 条测试片（≈1.2 GB，仅止血）

### 25.3 webhook 持久化重投

之前 `_deliver` 只做 3 次内联重试（约 7 秒），失败就丢事件。现在：

- 失败 → 写入 `/opt/wzmm/webhook_queue.json`（600，重启不丢），`delivery` id 不变，接收方可去重
- 后台 `hook_retrier` 每 15s 扫描，退避 **60s → 5m → 15m → 1h → 6h**，最多 6 次
- 用尽 → 丢弃并告警 `webhook_dropped`
- 可见性：`GET /admin/webhook/queue`（count / tries / next_in_s / last）

**端到端实测**（接收端前 4 次返回 500，之后 200）：

```
04:51:31  dlv_824aa645…  attempt=1  → 500
04:51:33  dlv_824aa645…  attempt=2  → 500
04:51:38  dlv_824aa645…  attempt=3  → 500      ← 3 次内联失败，入队
04:52:52  dlv_824aa645…  attempt=1  → 200      ← 66s 后重投成功，队列清空
```

同一个 delivery id 贯穿 4 次投递；`/admin/webhook/queue` 从 1 回到 0，队列文件持久化正常。

---

## 26. 观测与分页：webhook 指标 / 视频列表游标（R573）

### 26.1 `/metrics` 新增序列

```
wzmm_webhook_queue_depth                 待重投条数（0 = 队列干净）
wzmm_webhook_queue_oldest_age_seconds    队首已超期多久（>0 表示有积压）
wzmm_webhook_retried_total               失败后进入重投的次数
wzmm_webhook_delivered_total             重投成功次数
wzmm_webhook_dropped_total               6 次用尽后丢弃（应恒为 0）
wzmm_disk_free_bytes / wzmm_disk_total_bytes   磁盘水位（告警用）
```

抓取示例：

```bash
curl -s -H "X-Admin-Token: $ADMIN" https://<host>/metrics | grep -E 'webhook|disk_'
```

建议告警：`wzmm_webhook_dropped_total > 0`、`wzmm_webhook_queue_depth > 50`、
`wzmm_disk_free_bytes < 20GiB`。

### 26.2 `GET /v1/videos/generations` 游标分页

```bash
# 第一页
curl -s "https://<host>/v1/videos/generations?limit=2&status=succeeded" \
  -H "Authorization: Bearer $KEY"
# {"object":"list","data":[...],"has_more":true,"next_cursor":"MTc5..."}

# 下一页：把 next_cursor 原样带上
curl -s "https://<host>/v1/videos/generations?limit=2&cursor=MTc5..." \
  -H "Authorization: Bearer $KEY"
```

- 排序固定 `created desc, id desc`，`cursor` 为不透明串（`created|id` 的 base64url），**不要自己拼**
- 非法游标返回 `400 {"detail":"bad cursor"}`
- `limit` 上限 200，响应始终带 `has_more`
- SDK：`python wzmm.py video-list --limit 2 --cursor MTc5...`

### 26.3 本轮实测

| 项 | 结果 |
|---|---|
| `/metrics` 新序列 | 7 条全部出现，队列 0，磁盘 free 21,614,465,024 B |
| 分页第 1/2 页 | 4 条任务无重复、时间严格递减 |
| 非法 cursor | 400 |
| selftest | 133/133 passed |

### 26.4 R573 回归与公网端到端

| 项 | 结果 |
|---|---|
| 全量接口矩阵 `_wz_api_matrix.py` | **39/39 passed**（768P / H3 2K 在 `WZMM_ONLY_1080P=1` 下预期 400，已改为断言 400） |
| check_surface | openapi 55 / sdk 58 / docs 57 / ops 60，missing 0 → OK |
| selftest | 133/133 passed |
| 公网 HTTPS 出片 | `vid_8aa977d3f95840ff0d119fd9` 提交 200 → **206s succeeded** → content 200 / **7,400,385 B**（1080P） |
| 分页 | `limit=2` 两页无重复、`has_more/next_cursor` 正确；非法 cursor 400 |

---

## 27. 磁盘写入闸门：满之前先拒单（R574）

### 27.1 为什么要这道闸

`_disk_guard` 只在 **free < 6 GiB** 才开始删；而一次 1080P 渲染要先花积分、再落盘。
真掉到 6 GiB 时，任务已经跑完但可能存不下 —— 积分白烧、调用方拿到 failed。

所以加一道**前置**闸门：空间不够就直接 503，不接单、不消耗积分。

### 27.2 行为

| 项 | 说明 |
|---|---|
| 开关 | `WZMM_MIN_ACCEPT_GB`，默认 **8**（GiB），设 `0` 关闭 |
| 生效接口 | `POST /v1/videos/generations`、`/v1/videos/batch`、`/v1/videos/bulk`（**dry_run 不受限**）、`/v1/tools/call` 的提交工具 |
| 返回 | `503 {"detail":"storage nearly full (x.x GiB free); retry later"}` + `Retry-After: 600` |
| 告警 | `disk_reject`（crit，10 分钟冷却） |
| 可观测 | `/healthz` 新增 `disk_free_gib`、`min_accept_gib`；`/metrics` 有 `wzmm_disk_free_bytes` |

### 27.3 实测

| 用例 | 结果 |
|---|---|
| 正常空间（20.1 GiB 空闲，阈值 8） | 放行，`ok admit` |
| 强制 `WZMM_MIN_ACCEPT_GB=999999` | `HTTPException 503` + `Retry-After: 600` |
| `/v1/videos/bulk` dry_run | 200（不受闸门影响，不落盘） |
| `/v1/videos/batch` dry_run | 200 |
| selftest | 133/133 passed |
| 全量矩阵 | 39/39 passed |

---

## 28. 媒体端点支持 HEAD 与 Range（R575）

播放器/CDN 取片前常先发 `HEAD` 拿 `Content-Length`；之前只有 `GET`，返回 **405**。
现在四个媒体端点同时接受 `GET` 与 `HEAD`：

```
/v1/videos/{tid}/content      /v1/videos/{tid}/thumb
/v1/library/{fid}/content     /v1/library/{fid}/thumb
```

实测：

| 请求 | 结果 |
|---|---|
| `HEAD /v1/videos/<id>/content` | **200**，`content-length: 3079679`，无 body |
| `HEAD /v1/videos/<id>/thumb` | **200**，`content-length: 14447` |
| `GET` + `Range: bytes=0-1023` | **206**，`content-range: bytes 0-1023/3079679` |
| selftest / 矩阵 | 133/133 · 39/39 |

---

## 29. 轮询失败分类：账号猝死不拖 45 分钟（R576）

以前 `_query_one` 把查询异常**直接吞掉**：账号 token 失效时，任务会一直挂在 `running`，
直到 45 分钟超时被判 failed —— 用户白等，产能白占。

现在按错误类型分流（`_query_err_kind`）：

| 类型 | 判定 | 处理 |
|---|---|---|
| `auth` | 401/403/unauthorized/token expired/session expired… | 账号 `mark_dead`；任务**立刻重排**到新账号（retry<2），第三次失败才 fail |
| `transient` | timeout / 5xx / connection reset / upstream busy | **继续轮询**（上游大概率还在渲染，重排会二次扣费），仍由 45 分钟超时兜底 |
| `other` | 其它未知错误 | 连续 8 次 → 重排（retry<2），否则 fail |

配套：重排时清空该任务的错误计数，避免“新一轮第一枪就被判死”；`email` 为空的任务直接 fail，不再抛 `NoneType`。

### 29.1 实测（临时库 + monkeypatch，不碰生产数据）

| 用例 | 结果 |
|---|---|
| `HTTP 401 unauthorized` ×1 | `queued, retry=1, upstream=null, email=null`（换号重排） |
| auth 错误且 `retry=2` | 立即 `failed`，`err="poll auth error x2"`（不再等 45 分钟） |
| `socket read timeout` ×10 | 保持 `running`（不重排、不二次扣费），计数 10 |
| 未知错误 ×8 | 重排一次，计数清零 |
| 运行中任务 `email` 为空 | `failed: poll: task has no account` |
| selftest / 矩阵 | **133/133** · **39/39** |

> 注：测试用的是 `/tmp/r576.db` 临时库，但 `tasks.log` 是全局路径，因此生产事件日志里多了 4 条
> `vid_test_r576` / `vid_t1` 的 `poll_requeue` 记录（仅日志，无对应任务行）。

---

## 30. 批量产线实测 + 延迟分位（R579）

### 30.1 批量提交（公网 HTTPS）

| 批次 | 数量 | 结果 | 用时 | 备注 |
|---|---|---|---|---|
| `bat_26136035ac1700ce` | 3 | **3/3 succeeded** | 175s | content 4.36 / 3.75 / 2.39 MB |
| 24 条压力批 | 24 | **24/24 succeeded**，0 重试 | 429s | 单条 p50 ≈ 191s，最慢 417s（上游本身慢，`retry=0`） |

提交示例（一次最多 64 条）：

```bash
curl -s -X POST https://<host>/v1/videos/batch \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"items":[{"prompt":"a"},{"prompt":"b"}],"resolution":"1080P","duration":6}'
# {"batch_id":"bat_...","total":2,"accepted":2,"data":[{"id":"vid_..."},...]}
curl -s "https://<host>/v1/videos/batch/bat_..." -H "Authorization: Bearer $KEY"
```

### 30.2 `/admin/summary` 新增 `latency_24h`

```json
"latency_24h": {"n": 2014, "p50_s": 233.2, "p90_s": 468.5, "p99_s": 716.8, "max_s": 806.8}
```

- 只统计 `succeeded` 且 `updated > created` 的任务（近 24h）
- 分位按升序样本取点，含 R569 修复前的慢批次，所以 p90 仍偏高；修复后的干净批次见 §23.4（p50 180s / p90 267s）

### 30.3 回归

`selftest 133/133`、矩阵 `39/39`、`svc=active`；磁盘 21.5 GB 空闲（79.3%），片库 2,014 条 / 7.87 GB。

---

## 31. 配额语义修正：`quota` 只算受理任务，不再被轮询吃掉（R580）

之前 `used` 在 `require_key()` 里**每个请求 +1**：一次 1080P 任务要轮询几十次，
调用方设 `quota=100` 实际只能出 5 条片，而且额度用尽后连**查状态/下载**都会 402。

现在：

| 字段 | 含义 | 计入时机 |
|---|---|---|
| `rpm` | 每分钟请求数 | 每个请求（含轮询） |
| `quota` | **累计受理任务数**（视频条数） | `_create_task` 成功入库时 +1 |
| `daily_quota` | 每日受理任务数 | 入库前校验（batch/bulk 先按剩余量裁剪，避免逐条 count） |

行为：

- 额度用尽 → 只拦**新建任务**（402 `quota exhausted for this key (N tasks)`）
- 轮询 `/v1/videos/generations/{id}`、下载 `/content`、`/meta`、`/thumb` **照常 200**
- `batch`/`bulk` 仍按剩余日用额度裁剪；`dry_run` 不计数、不受限
- 兼容性：现网所有 key 的 `quota` 都是 0（不限），本次改动只影响显式设了 `quota` 的新 key

### 31.1 实测

| 用例 | 结果 |
|---|---|
| `quota=1` key 提交第 1 条 | 200，`/v1/usage` 显示 `used=1` |
| 同 key 连轮询 3 次 | 全部 **200**（不再扣额度） |
| 同 key 提交第 2 条 | **402** `quota exhausted for this key (1 tasks)` |
| `daily_quota=1` 单条创建第 2 次 | **402** `daily quota: 0 left today, requested 1` |
| `batch dry_run` | 200（不计数） |
| selftest / 矩阵 | **133/133** · **39/39** |

### 31.2 批量按剩余额度裁剪（R580b）

`batch` / `bulk` 在入库前先算剩余额度，避免“建到一半才 402、留下半批任务”：

| 场景 | 行为 |
|---|---|
| `quota=2`，一次提 3 条 | **402** `quota: 2 left, requested 3`，**一条都不建** |
| 同上 + `allow_partial:true` | **200**，`total=2 accepted=2`（按额度截断） |
| 额度用尽再提 1 条 | **402** `quota: 0 left, requested 1` |
| `/v1/usage` | `used=2`，与受理条数一致 |

## 9. 批量下载（pull_library.py）

服务器上已带批量拉取器：`/opt/wzmm/pull_library.py`（只用标准库，走 OpenAPI + Bearer key）。

```bash
cd /opt/wzmm
# 只拉 1080P 片库（1,080 条 / ~5.0 GB）
./venv/bin/python pull_library.py --key sk-wz-... --res 1920x1080 --out /root/dl1080 --workers 8

# 先看计划不落盘
./venv/bin/python pull_library.py --key sk-wz-... --res 1920x1080 --out /root/dl1080 --limit 5 --dry-run

# 拉本 key 自己提交过的任务片（按游标分页）
./venv/bin/python pull_library.py --key sk-wz-... --source catalog --out /root/dlcat
```

- `--res 1920x1080` / `--min-h 1080` 过滤；`--limit 0` 表示全量
- 断点续传：本地文件字节数与 `bytes` 一致就 skip
- 默认并发 6，`--workers` 可调；失败退出码 1

### 数据落点

| 内容 | 路径 | 规模 |
|---|---|---|
| 本服务出片目录（API 任务） | `/opt/wzmm/videos` | 2,019 mp4 / 7.4 GB |
| 老农场片库（`/v1/library` 根） | `/opt/h3v2/data/render/out` | 24,494 mp4 / 64.23 GB（1080P 1,080 条 / 5.0 GB） |
| 数据库 | `/opt/wzmm/wzmm.db` | tasks 2,036 |

## 32. 磁盘水位与自动 GC（R582）

| 变量 | 现网值 | 作用 |
|---|---|---|
| `WZMM_MIN_ACCEPT_GB` | 8 | 新任务准入闸门；低于即 503 + `Retry-After: 600`（dry_run 豁免） |
| `WZMM_MIN_FREE_GB` | 12 | 低于该值触发自动 GC |
| `WZMM_GC_MIN_AGE_H` | 24 | 非紧急状态只清超过 24h 的片子；`free <= MIN_FREE-8` 的紧急态才动新片 |
| `WZMM_GC_WARN_GB` | 8 | 预警线 = `MIN_FREE + 8`（现网 20 GiB） |
| `WZMM_TTL_DAYS` | 3 | janitor 每 30 分钟删超过 TTL 的本地文件 |

行为修正（R582）：
- GC 删文件后同步 `update tasks set local_path=null`，并写 `disk_gc_file` 事件（不会再留悬空路径）
- 本地文件被 GC 后，`/v1/videos/{id}/content` 自动 302 到上游签名 URL，有效期内照样能播
- `/healthz` 新增 `min_free_gib` / `gc_min_age_h` / `ttl_days`
- `/admin/capacity.disk` 新增 `catalog_gb`、`catalog_gb_24h`、`legacy_gb`、`days_to_accept_gate`、`days_to_full`

当前读数（2026-10-20 13:51 CST）：free **20.0 GiB**、catalog **7.88 GB**、24h 增量 **7.88 GB**、legacy **64.23 GB**
→ `days_to_accept_gate` **1.5**、`days_to_full` **2.5**。要延长跑道只能降 TTL 或清 legacy，需爷爷点头。

## 33. OpenAPI 1.5（R583）

- `/openapi.json` 补齐 `requestBody`：`VideoCreate` / `BatchCreate` / `KeyCreate` / `ToolsCall` /
  `WebhookSecret` / `WebhookTest` / `Park` / `ApplyLiveness`（Swagger UI 里直接有 body 编辑器，不再手写 JSON）
- `securitySchemes`：`BearerAuth`（`/v1/*`）、`AdminToken`（`/admin/*`，头名 `X-Admin-Token`）
- 每个 operation 都有 `tags`（videos / library / webhook / admin / misc / pages），Swagger 按组折叠
- `/play` 新增「创建新 Key / 列出现有 Key」面板（填 Admin Token 即可），创建成功后自动填入输入框
- 版本号 `info.version = 1.5`；路径仍是 55 条（未增删接口）

## 34. 全量矩阵扩到 58 项（R584）

`_wz_api_matrix.py` 原来 39 项（写接口 + 少量读接口），本轮把只读面也全部纳入，**58/58 passed**：

```
/v1/usage  /v1/videos/{id}/meta  /v1/videos/{id}/thumb
/v1/library/stats  /v1/library  /v1/library/manifest
/v1/webhook/secret  /gallery?key=  /sdk/wzmm.py  /openapi.json  /docs
/admin/summary  /admin/capacity  /admin/health  /admin/alerts
/admin/webhook  /admin/webhook/queue  /admin/usage?by_key=1  /admin/checkin
```

加上原有的写接口/权限/参数校验，现在一条命令就能证明"55 条 openapi 路径都通"：

```bash
cd /opt/wzmm && ./venv/bin/python _wz_api_matrix.py | tail -3
# SUMMARY 58/58 passed
```

## 35. Webhook 签名端到端（R585）

实测链路：`POST /v1/webhook/secret` 设密钥 → `POST /v1/webhook/test` 打本地监听 → 校验头与 HMAC。

```
x-wz-event: video.test
x-wz-delivery: dlv_test_<24hex>
x-wz-attempt: 1
x-wz-timestamp: <unix>
x-wz-signature-version: 1
x-wz-signature: t=<unix>,v1=<hex hmac_sha256(secret, "<unix>." + raw_body)>
```

- 8 项断言全 PASS：收到 / 路径 / event / delivery / attempt / 签名逐字节一致 / JSON body（`test:true`）/ 接口返回 `http 200`
- 验证脚本留档 `work/wzmm/wh_e2e.py`；测完已 `DELETE /v1/webhook/secret` 还原（effective=global）

## 36. GC 安全底线 + 磁盘指标（R587/R588）

**最新 1000 条永不删**：`WZMM_GC_KEEP_NEWEST`（默认 1000）。`_disk_guard` 按 mtime 升序处理，只在前 `len(files)-1000` 条里挑，紧急态也不例外。

- 临时库实测（5 个假片，keep=3，阈值拉满）：删掉最旧 2 条、保留最新 3 条、`local_path` 同步置空 → **PASS**
- `/healthz` 新增 `gc_keep_newest`
- `/metrics` 新增：

```
wzmm_disk_gate_bytes 8589934592
wzmm_catalog_bytes 7881782790
wzmm_catalog_bytes_24h 7881782790
wzmm_disk_days_to_gate 1.53      # -1 表示 24h 无新增，无法估算
```

建议告警规则：`days_to_gate < 1` → warn，`< 0.5` → crit；`catalog_bytes_24h` 突增 2 倍也值得看一眼。

## 37. 生产验证补记（R589/R590）

- **备份**：`backup.py` → `wzmm_20261020_060357.tar.gz`（412 KB），异地拉回后 sha256 两端一致
  `60a9132afdbdc325afaf3d16bc60118f516f6bd61ce03d95ea6ad973dbc54a34`；
  解包校验 `integrity ok` / tasks **2037** / succeeded **2020** / keys **164**
- **重启韧性（当前版本重测）**：提交 `vid_1234ded06d1cd17958871545` → 14s 进入 running →
  06:05:18 `systemctl restart` → 06:05:20 服务回来 → **06:08:09 succeeded**（171s 总耗时，retry=0）
  meta：1920×1080 / h264 / 141 帧 / 5.875s / 4,709,200 B
- **RPM 真限流**：建 rpm=2 的 key，连打 5 次 `/v1/models` → `[200,200,429,429,429]`，
  429 文案 `rate limit exceeded: 2 req/min`；测完 key 已禁用

## 38. Key 管理：PATCH 原地改限额（R591）

以前改额度只能删了重建（sk-wz 会变），现在可以原地改，**key 字符串不变**：

```bash
curl -X PATCH https://api.example.com/admin/keys/key_xxx \
  -H "X-Admin-Token: <admin>" -H "Content-Type: application/json" \
  -d '{"rpm":120,"quota":500,"max_concurrent":8,"name":"grandpa-1021","disabled":false}'
```

- 可改：`name` / `rpm` / `max_concurrent` / `quota` / `daily_quota` / `disabled`
- 空 body → 400；不存在的 kid → 404；负数字段 → 400
- 返回 `{"ok":true,"key":{...更新后的整行...}}`
- SDK：`python wzmm.py admin-key-update key_xxx --rpm 120 --quota 500`
- `/play` 的 Key 面板照旧（建/列）；改限额走 API 或 SDK


## 39. 零依赖客户端 `wz_client.py`（R597）

单文件、只用 Python 标准库，拷到任何机器就能跑（服务器上已放 `/opt/wzmm/wz_client.py`）。

```bash
# 健康（无需 key）
python wz_client.py --base https://api.example.com health

# 模型 / 价目 / 号池余量
python wz_client.py --key sk-wz-... models
python wz_client.py --key sk-wz-... pricing
python wz_client.py --admin <admin-token> token-age

# 出片并等待（1080P / 6s）
python wz_client.py --key sk-wz-... submit "海边日落，镜头缓慢推近" --dur 6 --wait

# 轮询 / 下载
python wz_client.py --key sk-wz-... wait vid_xxx
python wz_client.py --key sk-wz-... download vid_xxx out.mp4

# 批量（<=64 条/批）
python wz_client.py --key sk-wz-... batch items.json

# 建 key（admin）
python wz_client.py --admin <admin-token> newkey my-key --rpm 120 --concurrent 8
```

环境变量可替代参数：`WZMM_BASE` / `WZMM_KEY` / `WZMM_ADMIN`。

### `/healthz` 新增 `canary` 字段（R597）

```json
"canary": {"ok": true,
           "last": "[2026-10-20 01:05:31] canary OK id=vid_77fefdb1b55765c8b51c069f bytes=4123880 234s",
           "age_h": 5.3}
```

判读：`ok=false` 或 `age_h > 26` → 立刻看 `journalctl -u wzmm-canary -n 50`。


## 40. 磁盘到顶了怎么办：三条路线（R598）

现状：`/dev/vda1` 97G，已用 77G，可用 21G；`/opt/h3v2/data/render/out` 占 **59.82 GB / 24,494 条**，
`/opt/wzmm/videos` 占 7.34 GB / 2,024 条。系统侧已无油水（`/var/log` 535M、apt 缓存 5M、备份 1.3M）。

| 路线 | 动作 | 结果 | 代价 |
|---|---|---|---|
| **A 删片（只留 1920×1080）** | `purge_legacy.py --apply --yes` | 释放 59.23 GB | 768 类全没 |
| **B 删片（留 1080 类）** | `purge_legacy.py --apply --yes --keep-min-side 1080` | 释放 48.23 GB | 只清 768 级 |
| **C 扩容（推荐）** | 云控制台把系统盘 100G → 150G，再跑 `expand_disk.sh --apply` | 可用 +50G，一条不删 | 云盘费用 |

去重检查结果：全盘仅 **0.14 GB** 可去重（74 组同尺寸，148 文件），**不构成解法**。

### 扩容 SOP（KVM + ext4，支持在线）

```bash
# 1) 云控制台把系统盘从 100G 调到 150G（或更大），无需关机
# 2) 看新空间是否已可见
bash /opt/wzmm/expand_disk.sh
#    输出 RESULT: claimable=... MiB
# 3) 并入分区（ext4 在线扩，服务不用停）
bash /opt/wzmm/expand_disk.sh --apply
# 4) 复核
df -h /
```

`expand_disk.sh` 只做 `growpart /dev/vda 1` + `resize2fs /dev/vda1`，无参数时纯 dry-run。


## 41. 画廊按清晰度筛选 + library 直链带 key（R599）

### `/gallery` 新增 `res` 参数

```bash
# 只看 1080P 成品
https://api.example.com/gallery?key=sk-wz-...&res=1080P&limit=48
# 只看 768P（当前 WZMM_ONLY_1080P=1，通常为空）
https://api.example.com/gallery?key=sk-wz-...&res=768P
```

页面筛选栏已加“全部清晰度 / 1080P / 768P / 2K”下拉，翻页链接自动带上 `res`。

### `/v1/library` 新增 `embed_key=1`

```bash
curl -H "Authorization: Bearer sk-wz-..." \
  "https://api.example.com/v1/library?limit=50&embed_key=1"
```

返回的 `content_url` 会直接带 `?key=sk-wz-...`，可以原样丢给 `<video src>` / `wget` / `aria2c`，不用再手动拼接。
默认 `embed_key=0`（不带 key，需 Bearer），避免 key 意外进日志。


## 42. 老农场片库页也支持清晰度筛选（R600）

`/library`（24,494 条老农场）新增 `res` 参数，顶部实时显示筛选结果条数/体积。

```bash
# 只看 1920x1080（就是那 1,080 条 / 约 5 GB）
https://api.example.com/library?key=sk-wz-...&res=1920x1080&limit=48

# 简写别名同样可用
https://api.example.com/library?key=sk-wz-...&res=1080p
```

支持值：`1080p` / `720p` / `480p`（按短边下限）/ `1920x1080` 这类精确 `宽x高`。
首次带筛选约 1.9s（建分辨率索引），之后命中缓存约 0.05s。


## 43. 读接口不再消耗 rpm（R601）

批量场景最大的摩擦是“轮询把 rpm 打满”。现在区分读写：

| 类别 | 端点 | rpm 计费 |
|---|---|---|
| 读（不计） | `GET /v1/models`、`/v1/pricing`、`/v1/videos/generations`（列表）、`/v1/videos/{id}/status`、`/v1/videos/generations/{id}`、`/v1/videos/batch/{id}` | ❌ 不计 |
| 读（一直不计） | `/meta`、`/thumb`、`/events`、`/content`、`/v1/library*` | ❌ 不计 |
| 写（照常计） | `POST /v1/videos/generations`、`/batch`、`/bulk`、`/cancel`、`/tools/call`、webhook 密钥相关 | ✅ 计 |

读接口仍返回 `X-RateLimit-Limit` / `X-RateLimit-Remaining`（真实剩余，只是不扣减）。

### 实测

- rpm=5 的测试 key：连打 20 次 `GET /v1/models` → **20×200**；连打 20 次 `GET /status` → 20×404（不 429）
- 同一 key 连打 8 次 `POST /v1/videos/batch` → `400×5 + 429×3`，写限流仍在
- 压测（本地 loopback，主 key）：
  - 16 并发 × 20 = 320 次 `GET /v1/models` → **320×200**，177 rps，p95 128ms
  - 64 并发 × 10 = 640 次 → **640×200**，181 rps，p95 417ms
  - 8 并发 `/healthz` → p95 111ms

单进程（uvicorn）在 64 并发下 p95 约 0.4s；按日 6 万次调用（约 0.7 rps）余量充足。


## 44. 一键验收脚本 `verify_all.sh`（R602）

```bash
bash /opt/wzmm/verify_all.sh
```

只读、不产片、约 10 秒，逐项打印 `[OK]/[WARN]/[FAIL]` 并给 `SUMMARY`：

| 检查项 | 判据 |
|---|---|
| systemd | active + enabled |
| healthz gates | `ok=true`、`dead=0`、`disk_free >= min_accept` |
| canary fresh | canary `ok=true` 且 `age_h < 26` |
| selftest | 133/133 |
| api matrix | 60/60 |
| surface | `RESULT: OK` |
| token runway | `days_to_first_death > 7` |
| disk runway | `days_to_gate >= 3`（低于只 WARN，提示 A/B/C 待决） |
| inventory | API 出片数/体积、老农场数/体积、启用 key 数 |

退出码 = 问题数，可直接进 cron / 监控。

### 顺带：admin token 也能读元数据（R602）

```bash
curl -H "X-Admin-Token: <admin>" https://api.example.com/v1/models
curl -H "X-Admin-Token: <admin>" https://api.example.com/v1/pricing
```

实测 `200`；不带任何凭据仍 `401`。方便运维/监控脚本统一用一个令牌。


## 45. 客户端验收结果 + 两个用法提醒（R603）

`wz_client.py` 全链路实测（用已有片子，不新产片）：

| 命令 | 结果 |
|---|---|
| `health`（无 key） | 200 |
| `models` | 200，3 个模型 / only_1080p=true |
| `wait vid_1234ded0...` | `succeeded`，local=true，4,709,200 B |
| `download vid_1234ded0... out.mp4` | 4,709,200 B，0.2s |
| ffprobe 校验 | **h264 / 1920×1080 / 141 帧 / 5.875s** |
| `batch items.json`（`dry_run:1`） | `dry_run=true`，accepted=2，rejected=1（10s+1080P 被正确拒） |

### ⚠️ 提醒 1：`dry_run` 必须放**顶层**

```json
{"dry_run": 1,
 "defaults": {"resolution": "1080P", "duration": 6},
 "items": [{"prompt": "A"}, {"prompt": "B"}]}
```

放错到 `defaults` 里会被忽略 —— 那就**真提交**了。（本轮踩过：误提交 2 条，已立刻 `/v1/videos/batch/{id}/cancel` 全部取消，`canceled:2 / still_running:0`，未产片未占盘。）

### 提醒 2：`--admin` 现在也能查询/下载

```bash
python wz_client.py --admin <admin-token> wait vid_xxx
python wz_client.py --admin <admin-token> download vid_xxx out.mp4
```

任务归属校验仍在：普通 key 只能读自己创建的片子（跨 key 返回 403），admin 可读全部。


## 46. 备份与恢复（R604 刷新）

最新备份：`/opt/wzmm/backups/wzmm_20261020_064120.tar.gz`
- 大小 426,882 B，`kept=6`
- sha256 `c80725daf09b1a09eef7b0dc34eca8a72460ec14ec449ab0053d4b33872ba210`（服务器与本机 `offsite_backups\` 一致）
- 解包校验：`integrity ok` / tasks **2,040** / succeeded **2,023** / keys **206** / accounts **120**

### 恢复步骤

```bash
cd /opt/wzmm
systemctl stop wzmm-api
mkdir -p /opt/wzmm/_restore
tar -xzf backups/wzmm_20261020_064120.tar.gz -C /opt/wzmm/_restore
cp /opt/wzmm/_restore/wzmm/wzmm.db   /opt/wzmm/wzmm.db
cp /opt/wzmm/_restore/wzmm/pool.json /opt/wzmm/pool.json
cp /opt/wzmm/_restore/wzmm/apikey.txt /opt/wzmm/apikey.txt
cp /opt/wzmm/_restore/wzmm/admin.key  /opt/wzmm/admin.key
systemctl start wzmm-api
bash /opt/wzmm/verify_all.sh
```

### ⚠️ `pool.json` 是 **dict**，不是数组

```json
{"accounts": [ {...}, {...} ]}
```

数账号要用 `len(d["accounts"])`，直接 `len(d)` 会得到 `1`（键的数量）——本轮核对时就踩了这个坑，误以为只剩 1 个账号，实际 120 个完好。


## 47. 每小时自动验收 + 通用告警端点 + cancel 语义（R605）

### 自动验收

```
wzmm-verify.timer   每小时整点（RandomizedDelaySec=120，Persistent=true）
  └─ wzmm-verify.service → /opt/wzmm/verify_wrapper.sh
       ├─ bash verify_all.sh
       ├─ 追加到 /var/log/wzmm-verify.log
       └─ rc!=0 时 POST /admin/alert（level=crit, dedupe=1800）
```

查看：

```bash
systemctl list-timers wzmm-verify.timer
tail -f /var/log/wzmm-verify.log
tail -n 5 /opt/wzmm/alerts.log | grep verify
```

### 通用告警端点

```bash
curl -X POST .../admin/alert   -H "X-Admin-Token: <admin>" -H "Content-Type: application/json" \
  -d '{"key":"my-check","msg":"something wrong","level":"crit","dedupe":900}'
```

返回 `{"ok":true,"fired":{...},"webhook":bool}`；`dedupe` 是同类告警去重秒数。

### ⚠️ cancel 只对“排队中”生效

`POST /v1/videos/{id}/cancel` 与 `POST /v1/videos/batch/{id}/cancel` 只把 `status='queued'` 改成 `canceled`。
**已经 running 的任务撤不回来**——上游已经受理，会照常跑完并落盘成 `succeeded`。

所以返回里的 `still_running` 是真实含义：>0 就说明还有片子会产出。
R603 那 2 条就是这种情况（cancel 时已 running，最终各产 1 条 1080P，共 3,544,971 + 2,427,633 B）。

## 50. 按需验收：`POST /admin/verify`

忘了服务器现在绿不绿？两条命令：

```bash
A=$(cat /opt/wzmm/admin.key)
curl -s -X POST -H "X-Admin-Token: $A" https://api.example.com/admin/verify
# 202 {"ok":true,"started":true,"state":"running","run_id":"vfy_xxxx","poll":"GET /admin/verify"}

curl -s -H "X-Admin-Token: $A" https://api.example.com/admin/verify
# {"state":"done","rc":0,"summary":"SUMMARY: ALL GREEN","duration_s":8.0,"source":"api",...}
```

- 全程约 6–10 秒（跑 selftest 136 条 + 60 条矩阵 + surface + token/disk 读数）
- 同一时刻只允许一个 run，第二个请求返回 `started:false` 并给出正在跑的 `run_id`
- `source` 三态：`api`（手动触发）/ `timer`（每小时 systemd）/ `report`（外部回传）

Prometheus 三个 gauge（`/metrics`，需 admin 或 api key）：

```
wzmm_verify_ok 1            # 1 = 最近一次 rc=0
wzmm_verify_rc 0            # -1 = 从未运行
wzmm_verify_age_seconds 42  # -1 = 从未运行
```

定时器默认每小时跑一次并把结果 POST 回 `/admin/verify/report`，
所以 `wzmm_verify_age_seconds > 7200` 就说明定时器没在跑（或 API 刚重启）。

失败会自动调 `/admin/alert`（`key=verify`, `level=crit`, `dedupe=1800`），
同时写 `/var/log/wzmm-verify.log`。

## 51. 零依赖客户端：断点续传下载 + 老农场浏览（R607）

`wz_client.py`（stdlib only，无需 pip）新增四个能力：

```bash
python wz_client.py --key sk-wz-... download vid_xxx out.mp4
#  自动重试 3 次，中断后从 out.mp4.part 续传（HTTP Range），完成后原子改名

python wz_client.py --key sk-wz-... download vid_xxx out.mp4 --retry 5
#  弱网/跨国链路可加到 5~8 次

python wz_client.py --key sk-wz-... download vid_xxx out.mp4 --no-resume
#  从头下（.part 存在但内容可疑时用）

python wz_client.py --key sk-wz-... library --res 1080p --limit 10
#  root=/opt/h3v2/data/render/out  total=1080  showing=10 (offset 0)
#  id                                 MB    age_d  res
#  t-3115827ae24045b7af78           7.10      7.1  1920x1080

python wz_client.py --key sk-wz-... libget t-3115827ae24045b7af78 out.mp4
#  老农场单条直下，同样支持 --retry / --no-resume
```

实现要点：

- `out.mp4.part` 是唯一中间态；只有 `Content-Length` 校验通过才 `os.replace()` 成正式文件
- 服务端返回 `206` 就 `ab` 续写；返回 `200`（例如 302 跳到上游后不支持 Range）自动改为 `wb` 重下，不会拼出坏文件
- `401/403/404` 立即失败不重试；其它错误按 `2^n` 秒退避（上限 8s）
- 实测：3,544,971 B 视频，预置 1,000,000 B 的 `.part` → 续传后 sha256 与完整下载**完全一致**

## 52. 永久保留：`POST /v1/videos/{id}/pin`（R609）

**默认行为**：API 出的片子在 `/opt/wzmm/videos` 只活 `WZMM_TTL_DAYS`（当前 **3 天**），
`janitor()` 每 30 分钟扫一次，超期就删；磁盘低于 12 GiB 时 `_disk_guard()` 还会提前回收。

**想留的片子先 pin**：

```bash
# 单条
curl -X POST -H "Authorization: Bearer sk-wz-..." \
  https://api.example.com/v1/videos/vid_xxx/pin
# {"id":"vid_xxx","pinned":true}

# 取消
curl -X DELETE -H "Authorization: Bearer sk-wz-..." \
  https://api.example.com/v1/videos/vid_xxx/pin
```

批量（管理员）：

```bash
A=$(cat /opt/wzmm/admin.key)
curl -X POST -H "X-Admin-Token: $A" -H "Content-Type: application/json" \
  -d '{"ids":["vid_a","vid_b"]}' https://api.example.com/admin/pin
# {"ok":true,"updated":2,"pinned_total":2,"pinned":true}

# 整个批次一起 pin
curl -X POST -H "X-Admin-Token: $A" -H "Content-Type: application/json" \
  -d '{"batch_id":"bat_xxx"}' https://api.example.com/admin/pin

# 取消 pin
curl -X POST -H "X-Admin-Token: $A" -H "Content-Type: application/json" \
  -d '{"batch_id":"bat_xxx","pinned":false}' https://api.example.com/admin/pin
```

- `GET /v1/videos/{id}/status` 的返回里多了 `"pinned": true/false`
- `janitor()` 的 SQL 带 `coalesce(pinned,0)=0`，pinned 的行根本不会进候选
- `_disk_guard()` 在删除前再查一次 `_pinned_ids()`，即使磁盘告急也不会动 pinned 的片子
- 实测：`pin → status.pinned=true → unpin → status.pinned=false`，selftest 137/137 覆盖

## 53. 保留策略体检：`/admin/retention` + `/admin/pins` + `/admin/gc/preview`（R610）

三个只读端点，回答"我的片子还能活多久"：

```bash
A=$(cat /opt/wzmm/admin.key)

curl -s -H "X-Admin-Token: $A" https://api.example.com/admin/retention
# {
#   "ttl_days": 3.0, "gc_min_age_h": 24.0, "disk_free_gib": 20.0,
#   "totals":        {"files": 2023, "gb": 7.88},
#   "pinned":        {"files": 0,    "gb": 0.0},
#   "expiring_24h":  {"files": 0,    "gb": 0.0},
#   "expiring_72h":  {"files": 2023, "gb": 7.88},
#   "already_expired": {"files": 0,  "gb": 0.0},
#   "next_to_expire": [{"id":"vid_...","mb":2.02,"age_h":14.3,"left_h":57.7}, ...]
# }

curl -s -H "X-Admin-Token: $A" "https://api.example.com/admin/gc/preview?limit=20"
# {"dry_run":true,"would_delete":{"files":0,"gb":0.0},"sample":[...]}
# 只算不删；janitor 每 30 分钟真跑一次

curl -s -H "X-Admin-Token: $A" "https://api.example.com/admin/pins?limit=100"
# {"object":"list","count":0,"returned":0,"bytes":0,"gb":0.0,"data":[]}
```

典型工作流：

1. `GET /admin/retention` 看 `expiring_72h` —— 这就是三天内会消失的量
2. 想留的：`POST /admin/pin {"ids":[...]}` 或 `{"batch_id":"..."}`
3. `GET /admin/pins` 确认已保留
4. `GET /admin/gc/preview` 再确认下次 janitor 不会误伤

注意：`retention` 的 `expiring_72h` 包含 `expiring_24h` 和 `already_expired`；
`already_expired` > 0 说明 janitor 还没来得及跑（它 30 分钟一轮），不等于已经删了。

### 53.1 保留指标进 `/metrics` 与每小时验收（R611）

`/metrics` 新增 5 条 gauge（60 秒缓存，单次 8~9 ms）：

```
wzmm_clips_local 2023
wzmm_clips_bytes 7875915751
wzmm_clips_pinned 0
wzmm_clips_expiring_24h 0
wzmm_clips_expiring_72h 2023
```

`verify_all.sh` 多了一段：

```
== retention ==
  clips=2023 pinned=0 expiring_24h=0 (0.0 GB) expiring_72h=2023
  [OK]   retention                  0 clips expire within 24h
```

超过 200 条将在 24 小时内过期时变成 `[WARN]`，并随每小时验收写进
`/var/log/wzmm-verify.log`。这样"片子快没了"不会再悄无声息。

## 54. 孤儿文件：`/admin/orphans` 与 `/admin/orphans/import`（R612）

### 问题

`janitor()` 只遍历 `tasks.local_path`，`/gallery` 也只读 tasks。
任何直接写进 `/opt/wzmm/videos/` 却没登记 DB 的 mp4，会变成**永久孤儿**：
不参与 TTL、不出现在列表、也永远不会被清理。

实测发现 4 个：

```
expiry_454320624243061  5.67 MB
expiry_454254222029274  4.27 MB
expiry_454251056812409  3.85 MB
expiry_454154001830247  3.82 MB   （合计 17.6 MB）
```

来源：`expiry_probe.py`（试用期后 1080P 探针）直接落盘，没写 tasks。

### 两个端点

```bash
A=$(cat /opt/wzmm/admin.key)

# 只读：还有哪些孤儿
curl -s -H "X-Admin-Token: $A" "https://api.example.com/admin/orphans?limit=50"
# {"object":"list","count":0,"bytes":0,"gb":0.0,"data":[]}

# 登记（只增不删；登记后纳入 TTL，想长期留就再 pin）
curl -s -X POST -H "X-Admin-Token: $A" -H "Content-Type: application/json" \
  -d '{}' https://api.example.com/admin/orphans/import
# {"ok":true,"imported":4,"scanned":4,...}

# 只登记指定的
-d '{"ids":["expiry_454320624243061"]}'
```

### 根因已修

`expiry_probe.py` 新增 `register()`：探针出片后立刻插入 tasks
（`id=expiry_<task_id>`, `status=succeeded`, `local_path=<file>`）。
以后探针片会自动出现在 gallery、进 TTL、计入 retention，不会再变孤儿。

### 指标

```
wzmm_clips_orphan 0
wzmm_clips_orphan_bytes 0
```

修完后的实测：`inventory 2027 files` == `retention totals 2027` == `orphans 0`。

## 55. 批量打包下载：`GET /v1/videos/archive`（R613）

一条命令拿走整批片子，不用逐条下载：

```bash
# 整个批次（最常用）
curl -OJ -H "Authorization: Bearer sk-wz-..." \
  "https://api.example.com/v1/videos/archive?batch_id=bat_deac6415f36bc900"

# 指定若干条
curl -OJ -H "Authorization: Bearer sk-wz-..." \
  ".../v1/videos/archive?ids=vid_a,vid_b,vid_c"

# 所有已 pin 的（永久保留那批）
curl -OJ -H "Authorization: Bearer sk-wz-..." \
  ".../v1/videos/archive?pinned=1&limit=100"

# 管理员可跨 key 打包
curl -OJ -H "X-Admin-Token: $A" ".../v1/videos/archive?batch_id=bat_xxx"
```

限制与行为：

- 单次最多 **100 条**（`limit`，默认 50）
- 单次总大小上限 **1.5 GB**，超了返回 413，请拆批
- 同时只允许 **1 个** 归档任务，第二个返回 429（`Retry-After: 30`）
- zip 用 `ZIP_STORED`（mp4 本身已压缩，不再浪费 CPU）
- 服务端写到临时文件，响应结束由 `BackgroundTask` 删除，不会积压
- 只打包 `local_path` 存在且文件在盘的片子；没有就 404

SDK：

```bash
python3 /opt/wzmm/sdk/wzmm.py --admin-token "$A" archive \
  --batch-id bat_deac6415f36bc900 --out batch.zip
# wrote batch.zip (5972906 bytes)
```

实测：该批次 2 条 → 5,972,906 B，`zipfile.testzip()` 返回 None，
临时文件在响应完成后自动清零。

## 56. 磁盘决策测算：`GET /admin/capacity`（R615）

一个端点把 A/B/C 三笔账算清楚，只读、不删任何东西：

```bash
curl -s -H "X-Admin-Token: $A" https://api.example.com/admin/capacity
```

实测返回（2026-10-20）：

```json
{
  "disk": {"total_gb": 103.9, "used_gb": 82.4, "free_gb": 21.4, "used_pct": 79.3,
           "soft_gib": 14.0, "hard_gib": 8.0, "mode": 0},
  "api_cache": {"files": 2028, "gb": 7.89, "pinned_files": 1, "expiring_72h": 2027},
  "legacy": {"files": 24494, "gb": 64.23, "scan_age_h": 6.8, "groups": [ ... ]},
  "options": {
    "A_keep_1920x1080_only":  {"keep_files": 1080, "keep_gb": 5.0,  "reclaim_gb": 59.23},
    "B_keep_min_side_1080":   {"keep_files": 3991, "keep_gb": 15.99, "reclaim_gb": 48.24},
    "C_expand_disk":          {"reclaim_gb": 0, "needs": "cloud console 100G -> 150G"}
  },
  "recommendation": "C (no data loss) if console is reachable, else B",
  "deleted": false
}
```

读法：

- `legacy` 是老农场 `/opt/h3v2/data/render/out` 的扫描结果（`_farm_resolution.json`，每次 `farm_scan.py` 更新）
- A = 只留 1920x1080，能腾 **59.23 GB**，但删掉 23,414 条
- B = 留所有短边 >= 1080（含 1440x1080 / 1080x1920），能腾 **48.24 GB**，保留 3,991 条
- C = 云控制台扩到 150G，`reclaim_gb: 0`，零删除，但要你能进控制台
- `deleted: false` 是硬保证：这个端点只读，一个文件都不动

执行方式（需要你明确同意后才会跑）：

```bash
bash /opt/wzmm/purge_legacy.py --apply --yes                     # A
bash /opt/wzmm/purge_legacy.py --apply --yes --keep-min-side 1080 # B
bash /opt/wzmm/expand_disk.sh --apply                            # C（先扩容再跑）
```


---

## 57. 公网可达性探针（DNS / 证书 / nginx / 防火墙）

本机 `/healthz` 通不代表外面能访问。`public_probe.py` 从**服务器上**走公网域名打自己：

```
https://api.example.com/healthz        -> 期望 200 + ok=true
https://api.example.com/v1/models      -> 期望 200 + object=list（带 Bearer）
```

任何一环坏（DNS 解析、TLS 证书过期、nginx 反代、安全组/防火墙），它都会抓到并告警。

### 自动跑

```
systemctl status wzmm-public.timer      # 每 10 分钟 + RandomizedDelaySec=30
systemctl start  wzmm-public.service    # 手动立刻跑一次
tail -f /opt/wzmm/public_probe.log
```

### 查最近结果

```bash
A=$(cat /opt/wzmm/admin.key)
curl -s -H "X-Admin-Token: $A" https://api.example.com/admin/public
```

```json
{"ok": true,
 "data": {"ts": 1792483537.8, "ok": true, "age_s": 9.7, "err": "", "error": "",
          "healthz": {"http": 200, "ms": 194.1, "ok": true},
          "models":  {"http": 200, "ms": 222.2, "ok": true}},
 "source": "public_probe.py", "timer": "wzmm-public.timer"}
```

`age_s` = 距上次探针的秒数；超过 900 说明 timer 卡住了。

### 立刻重跑（阻塞，<=90s）

```bash
curl -s -X POST -H "X-Admin-Token: $A" https://api.example.com/admin/public/run
```

### Prometheus 指标

```
wzmm_public_ok 1                # 1=公网链路健康
wzmm_public_age_seconds 9.7     # 结果新鲜度
wzmm_public_healthz_http 200
wzmm_public_healthz_ms 194.1
wzmm_public_models_ms 222.2
```

告警建议：`wzmm_public_ok == 0 for 20m` 或 `wzmm_public_age_seconds > 900`。

### SDK

```bash
python wzmm.py admin-public
python wzmm.py admin-public-run
```

### 判定口径

| 现象 | 含义 |
|---|---|
| `healthz.http=0` | DNS / TCP / TLS 层就断了（域名解析或证书问题优先查） |
| `healthz.http=502/504` | nginx 起着但后端 8808 没起来 |
| `healthz.http=200` 但 `models.http=401` | 主 key 失效（`/opt/wzmm/apikey.txt`） |
| `age_s > 900` | timer 没跑（`systemctl status wzmm-public.timer`） |


---

## 58. 一句话出片 + 批量量产验收

### 一条命令拿到 mp4

```bash
export WZMM_KEY=sk-wz-...
python wzmm.py gen "清晨海边，浪花拍礁石，慢推近，电影感" -o out.mp4
```

`gen` = 提交 → 轮询 → 下载，一气呵成。返回：

```json
{"id": "vid_...", "status": "succeeded", "model": "MiniMax-Hailuo-2.3",
 "resolution": "1080P", "duration": 6, "elapsed_s": 213.4,
 "bytes": 1447813, "path": "/root/out.mp4"}
```

可选：`--pin`（出片即永久保留，GC 不删）、`--timeout 1800`、`--interval 5`、
`--res 1080P`、`--dur 6`、`--ratio 16:9`。

### 量产体检（每轮改动后建议跑一次）

`acceptance_batch.py` 走**公网 HTTPS**，不是本机回环，一次提交 N 条并逐条 ffprobe：

```bash
cd /opt/wzmm
export WZMM_KEY=$(cat /opt/wzmm/apikey.txt)
./venv/bin/python acceptance_batch.py --n 4 --timeout 1500
```

判据（全部满足才算 PASS）：

- 每条 `status=succeeded`
- 每条 `ffprobe` 出 `1920x1080` / `h264`
- `pass: true`

输出示例：

```json
{"n": 4, "succeeded_1080p": 4, "failed": 0, "wall_s": 421.6,
 "median_elapsed_s": 236.5, "max_elapsed_s": 312.0, "mb_total": 5.8, "pass": true}
```

证据落盘：`/opt/wzmm/acceptance/batch_<ts>.json`（全量）+ `acceptance/last.json`（最近一次）。

读法：

| 字段 | 含义 |
|---|---|
| `wall_s` | 从第一条提交到最后一条下载完的总墙钟时间 |
| `median_elapsed_s` | 单条从提交到 succeeded 的中位数（正常 3–6 分钟） |
| `succeeded_1080p` | 真的产出了 1920×1080 的条数（ffprobe 判定，不看接口自述） |
| `pass` | `succeeded_1080p == n` |

### 10 条并发实测（2026-10-20 08:20–08:24 UTC）

```json
{"n": 10, "succeeded_1080p": 10, "failed": 0, "wall_s": 238.0,
 "median_elapsed_s": 203.3, "p90_elapsed_s": 234.3, "max_elapsed_s": 234.3,
 "clips_per_min": 2.52, "mb_per_clip": 4.47, "mb_total": 44.68, "pass": true}
```

- **10/10 全过**，全部 1920×1080 h264 24fps 5.875s
- 墙钟 **238 秒**跑完 10 条 → 吞吐 **2.52 条/分钟**（并发 10）
- 每条平均 4.47 MB，10 条约 44.7 MB
- **账号分散**：10 条落在 **10 个不同账号**上（没有把单号打爆）
- **积分没掉**：跑前跑后 `points_total` 都是 96,000 —— Hailuo-2.3 的 1080P 走试用免费额度，不烧 credits

查账号分散度的办法（服务器上）：

```bash
sqlite3 /opt/wzmm/wzmm.db "select id,email from tasks order by created desc limit 10;"
```


---

## 59. 磁盘跑道：别被一次性导入骗了（口径修正）

### 之前为什么显示 1.49 天

老口径是「最近 24 小时所有 mp4 的 mtime 之和」当每天增量。问题是 10-19 那次
**一次性批量导入 7.96 GB**（2,028 条）全落在 24 小时窗口里，于是：

```
(20 GiB - 8 GiB) / 7.96 GB/d = 1.49 天   ← 假警报
```

实际生成速度也就几十条/天，根本不是每天 8 GB。

### 现在的算法（R619）

1. **优先用观测斜率**：每小时把 `(ts, free_bytes)` 追加到 `/opt/wzmm/_disk_history.jsonl`
   （30 分钟内不重复采样），窗口 ≥ 6 小时就按真实 `free` 下降速度算。
2. **窗口不够时退回 7 天平均**：`catalog_bytes_7d / 7`，一次性导入被摊薄。
3. 数字为 0 或负（在回收空间）时，跑道按「无限」处理，不再报警。

### 新指标

```
wzmm_catalog_bytes_7d          7958331367     # 7 天内新增片子的字节
wzmm_disk_burn_bytes_per_day   1137000000     # 当前采用的每日消耗
wzmm_disk_burn_window_hours    0.0            # 0 = 还没攒够观测窗口，用 7 天平均
wzmm_disk_days_to_gate         10.42          # 到 MIN_ACCEPT_GB 的剩余天数
```

`burn_source` 在 `/admin/capacity` 里直接标注：`catalog_7d_avg` 或 `observed_free_space`。

### 实测对比（2026-10-20 08:30 UTC）

| 口径 | 数值 |
|---|---|
| 旧（24h 增量） | **1.49 天**（会吓人） |
| 新（7 天平均） | **10.42 天** |
| 到写满 (`days_to_full`) | 17.5 天 |

6 小时后再看，`burn_window_h` 会变成真实窗口，跑道切换成纯观测值。

### 顺手修掉的隐藏 bug

`app.py` 里 `/admin/capacity` **被定义了两次**（2531 和 4046 行）。
FastAPI 只认先注册的那个，所以后来写的那份（含 runway/projection）**从来没执行过**，是死代码。
已删除死代码，并把 burn/runway 字段并进真正生效的那个 handler。

### 决策不变

A/B/C 还是等爷爷点头，`deleted: false` 的保证没动过；这轮一个文件都没删。


---

## 60. 并发天花板：20 条同时跑

### 实测（2026-10-20 08:32–08:37 UTC，公网 HTTPS）

```json
{"n": 20, "succeeded_1080p": 20, "failed": 0, "wall_s": 258.7,
 "median_elapsed_s": 222.7, "p90_elapsed_s": 245.9, "max_elapsed_s": 246.9,
 "clips_per_min": 4.64, "mb_per_clip": 5.24, "mb_total": 104.85, "pass": true}
```

| 并发 | 墙钟 | 吞吐 | 中位延迟 | p90 |
|---|---|---|---|---|
| 4 | 278.4s | 0.86 条/分 | 180.8s | — |
| 10 | 238.0s | 2.52 条/分 | 203.3s | 234.3s |
| 20 | 258.7s | **4.64 条/分** | 222.7s | 245.9s |

读法：

- 20 条**同时在跑**（`running=20` 稳定 3 分钟），没有排队堆积
- 墙钟几乎不随 N 增长（238s → 259s），说明瓶颈是**单条渲染延迟**，不是调度
- 但单条延迟随并发缓慢上升（180s → 223s），是上游侧排队，属于正常
- 20 条落在 **20 个不同账号**、5 个邮箱域，**积分仍然 96,000 → 96,000**

### 什么时候会撞天花板

`WZMM_MAX_INFLIGHT`（默认 64）和账号数（120）是硬上限；再往上要加号。
按当前 4.64 条/分、24 小时不间断，理论上限约 **6,700 条/天**，
但受限于 96,000 积分（1080P 免费路径不扣积分，实际受上游风控与号池健康度约束）。

### 复现

```bash
export WZMM_KEY=$(cat /opt/wzmm/apikey.txt)
./venv/bin/python acceptance_batch.py --n 20 --timeout 2400
```


---

## 61. 画廊缩略图：一次性预热，打开秒开

### 问题

`GET /v1/videos/{tid}/thumb` 是**按需**跑 ffmpeg 抓帧，冷缓存单张实测 **1.13 秒**。
画廊一页 24 张，冷启动时浏览器要串/并着拉，体验很差。

### 现在

`prewarm_thumbs.py` 把整个目录的缩略图一次性生成好，缓存文件就是端点用的同一个
`<mp4>.thumb.jpg`，所以**预热之后端点直接命中，不需要改路由**。

```bash
cd /opt/wzmm
./venv/bin/python prewarm_thumbs.py --workers 4        # 全量
./venv/bin/python prewarm_thumbs.py --workers 4 --limit 200   # 只补前 200 张
```

实测（2026-10-20 08:42–08:52 UTC）：

```
{"total": 2063, "already": 102, "built": 1961, "failed": 0,
 "seconds": 629.6, "missing_files": 0, "ok": true}
```

- 2,063 张全部生成，**0 失败**，4 并发约 **10.5 分钟**（~3.3 张/秒）
- 缓存总大小 **78.9 MB**（平均 38 KB/张，640px JPEG q3）
- 冷 → 热：单张 **1.13s → 0.08~0.23s**（约 8 倍）
- 画廊页 HTML 本身 0.13s

### API 方式

```bash
A=$(cat /opt/wzmm/admin.key)
curl -s -H "X-Admin-Token: $A" https://45-207-221-161.blah/admin/thumbs   # 覆盖率（只读）
curl -s -X POST -H "X-Admin-Token: $A" ".../admin/thumbs/prewarm?workers=4"  # 202，后台跑
```

`GET /admin/thumbs` 返回：

```json
{"ok": true,
 "cache": {"total": 2063, "ready": 2063, "missing": 0, "pct": 100.0, "cache_bytes": 78903059},
 "state": {"running": false, "rc": 0, "elapsed_s": 0.2},
 "last_run": {"built": 1961, "failed": 0, "seconds": 629.6, "ok": true},
 "profile": "640px jpeg q3, seek 1.0s"}
```

没活干时 `POST prewarm` 也会立刻返回，后台进程 0.2 秒就退（因为全部命中缓存）。

### 指标

```
wzmm_thumbs_ready 2063
wzmm_thumbs_total 2063
wzmm_thumbs_cache_bytes 78903059
```

告警建议：`wzmm_thumbs_ready < wzmm_thumbs_total * 0.9` 时跑一次 `admin-thumbs-prewarm`。

### 注意

- 新片入库后缩略图还是按需生成；想让首屏一直热，定时跑 `prewarm_thumbs.py`（增量，已存在的直接跳过）
- 缓存在 `videos/` 里，janitor 只删 `.mp4`，不会顺手删掉 `.thumb.jpg`；但片子被回收后缩略图会变成孤儿（约 38KB/张，可忽略）


---

## 62. key 卫生：禁用 ≠ 删除，测试别再堆垃圾

### 现象

跑了几十轮自检后，`keys` 表里堆了 **348 个 disabled key**（`selftest-scope` / `selftest-dq` / `matrix-test`），
每轮还在 +1~+3。`wzmm_keys_active` 只统计 enabled 所以指标没异常，但 admin 列表越来越长。

根因：`DELETE /admin/keys/{kid}` 语义是**禁用**（`disabled=1`），而 selftest / matrix 每轮都建临时 key 再"删"，
于是每次都留下一条禁用记录。

### 现在

1. `DELETE /admin/keys/{kid}` 保持不变（禁用，安全默认）。
2. `DELETE /admin/keys/{kid}?hard=1` **真删**，但有硬保护：**该 key 名下有任务就 409**：

```bash
curl -s -X DELETE -H "X-Admin-Token: $A" "$B/admin/keys/key_xxx?hard=1"
# {"ok": true, "id": "key_xxx", "deleted": true}

curl -s -X DELETE -H "X-Admin-Token: $A" "$B/admin/keys/key_big?hard=1"
# 409 {"detail": "key owns 1078 task(s); disable instead of delete"}
```

3. `GET /admin/keys/stale` 只读盘点：

```json
{"disabled_total": 348, "deletable": 341, "with_tasks": 7,
 "data": [{"id": "key_...", "name": "matrix-test", "used": 0, "age_h": 12.3, "tasks": 0}, ...],
 "hint": "DELETE /admin/keys/{id}?hard=1 removes a key that has no tasks"}
```

4. `selftest.py` 和 `_wz_api_matrix.py` 都改成 `?hard=1`，跑完自己收拾干净。

### 实测

```
selftest 139/139,  matrix 63/63
disabled keys before=348  after=348   ← 泄漏已止住
roundtrip: disable -> disabled:true ; hard -> deleted:true ; again -> 404 ; 有任务的 key -> 409
```

### 历史残留

存量 348 个里 **341 个可删**（`tasks=0`）、7 个有任务（保留）。
王炸没有自动清理——按爷爷的规矩，删东西要你点头。想清就跑：

```bash
curl -s -H "X-Admin-Token: $A" "$B/admin/keys/stale" \
 | python -c "import sys,json;[print(x['id']) for x in json.load(sys.stdin)['data']]" \
 | while read id; do curl -s -X DELETE -H "X-Admin-Token: $A" "$B/admin/keys/$id?hard=1" >/dev/null; done
```


---

## 63. 外部端到端验收（从 Windows 本机打公网）

前面所有验收都是在服务器上跑的（本机回环或本机出公网）。这一轮换成**完全外部视角**：
从一台 Windows 电脑，走公网 HTTPS，建 key → 出片 → 下载 → ffprobe → 租户隔离 → 清理。

脚本：`r623_external.py`（纯 stdlib + 本机 ffprobe，无第三方依赖）

### 实测（2026-10-20 09:00–09:04 CST）

| 步骤 | 结果 |
|---|---|
| `GET /healthz` | **200**，0.28s |
| `POST /admin/keys` | 200，`key_4f8b4ecee5d53725` |
| `POST /v1/videos/generations` | 200，`vid_5db215db364ca2911adb011e`，queued |
| 轮询到 succeeded | **205.5s** |
| 下载 mp4 | **7,293,929 B** |
| 本机 ffprobe | **1920×1080 / h264 / 5.875s** |
| 租户隔离 | 新 key 只看到自己的 **1** 条任务 |
| 无 key 访问 `/v1/models` | **401** |
| `?hard=1` 删这个 key | **409**（名下有任务，保护生效） |
| 普通删除（禁用） | 200，`disabled: true` |
| 禁用后再查 status | **401** |

结论：**从外面看，这就是一个正常的 OpenAI 风格视频接口** —— 建 key、提交、轮询、下载、隔离、回收，全程无人工干预。

### 复现

```bash
python r623_external.py      # Windows / Linux 都能跑，需要本机有 ffprobe
```

证据：`out_grandpa/r623_external.json` + `out_grandpa/r623_external_<tid>.mp4`

---

## 64. 并发天花板到底在哪：key 的 rpm，不是渲染（R624 / R625）

一句话：**渲染侧 40 条并发完全吃得下；挡住你的是 key 自己的 `rpm` 和 `max_concurrent`。**

### 实验设计

同一台服务器、同一个接口、同一个脚本、同一批提示词，只换 key：

| 组 | key | rpm | max_concurrent | n |
|---|---|---|---|---|
| A（对照组） | `grandpa`（apikey.txt 主 key） | 120 | 32 | 40 |
| B（实验组） | 临时 `r624-bulk` | 600 | 128 | 40 |

```bash
# 建一条专门跑批量的 key
curl -s -X POST -H "X-Admin-Token: $A" -H "Content-Type: application/json" \
  -d '{"name":"r624-bulk","rpm":600,"max_concurrent":128}' \
  http://127.0.0.1:8808/admin/keys
```

### 实测结果

| 指标 | A：rpm=120 | B：rpm=600 / mc=128 |
|---|---|---|
| 提交成功 | **32 / 40** | **40 / 40** |
| 提交耗时 | 40 秒内撞墙，8 条放弃 | **3.9s** |
| `http 429` 次数 | **16** | **0** |
| 出片成功 | 32 / 32（已提交的全成） | **40 / 40** |
| 墙钟 | — | **315.0s** |
| 中位耗时 | — | **192.7s** |
| P90 | — | **279.9s** |
| 最大 | — | **301.4s** |
| 吞吐 | — | **7.62 条/分** |
| 分辨率 | 1920×1080 | **40/40 = 1920×1080** |
| 体积 | — | 175.94 MB（4.4 MB/条） |

A 组的 429 来自两处，**都不在渲染层**：

- `app.py:551` —— key 的 `rpm`（120/min）提交速率
- `app.py:622` —— key 的 `max_concurrent`（32），第 33 条起 429

B 组把这两个闸门抬到 600 / 128 后，**429 归零，40 条 3.9 秒全提交完**；
渲染队列在 2 分 39 秒时还有 31 条 running，说明 120 个账号的池子才是真实上限。
按 7.62 条/分、单条 4.4 MB 线性外推：

- 1 小时 ≈ **457 条**、约 **2.0 GB**
- 想再往上，先看 `WZMM_MAX_INFLIGHT`（默认 64）和账号数（当前 120 / dead 0）

### 代码改动（本轮一并落地）

1. SDK `wzmm.py`：`RETRIES` 默认 **2 → 4**（`WZMM_RETRIES` 仍可覆盖），退避仍封顶 30s；
   新增 `wzmm.STATS = {requests, retries, http_429, http_5xx, conn_errors}` 实时计数。
2. `acceptance_batch.py` 汇总新增字段：`submit_wall_s` / `submitted` / `submit_errors` / `pending` / `client{...}`。
3. 修一个真 bug：提交失败的条目原本会让批次**空等到 `--timeout`**（40 条里 8 条没拿到 id，循环等 40 条 done）。
   现在只等拿到 id 的那些：`pending = [t for t in tasks if t.get("id")]`。

### 选 key 口诀

| 你要干什么 | rpm | max_concurrent |
|---|---|---|
| 一个人点着玩 | 30–60 | 4–8 |
| 脚本批量出片 | 300–600 | 64–128 |
| 多租户对外 | 按租户拆 key，别共用一条 | ≤ 账号数 × 2 |

⚠️ 提高 rpm 只是**放行提交**，不会凭空多出渲染能力；账号池打满后任务会在 `queued` 里排队，
`wall_s` 变长但不会 429。

### 证据

- A 组日志：`/opt/wzmm/acceptance/run_r624_rpm120.log`（`submitted=32`, `http 429=16`）
- B 组日志：`/opt/wzmm/acceptance/run_r625.log`
- B 组汇总：`/opt/wzmm/acceptance/batch_20261020_090641.json`（`pass: true`, `submit_wall_s: 3.9`）

---

## 65. 扩容曲线：20 → 40 → 64 → 96 并发（R626 / R627）

同一台机器、同一个脚本、同一批提示词，只改并发数（key 都用 rpm=600 / mc=128）：

| 并发 n | 墙钟 s | 中位 s | P90 s | 最大 s | 条/分 | 体积 MB |
|---|---|---|---|---|---|---|
| 20 | 258.7 | — | — | — | 4.64 | — |
| 40 | 315.0 | 192.7 | 279.9 | 301.4 | **7.62** | 175.94 |
| 64 | 397.6 | 244.1 | 321.3 | 374.0 | **9.66** | 264.96 |
| 96 | 578.4 | 303.1 | 455.0 | 547.9 | **9.96** | 413.04 |

### 结论

1. **64 是甜点**。96 只多 3% 吞吐（9.66 → 9.96），但 P90 从 321s 涨到 455s（+42%），
   单条等待明显变差。批量任务建议 32–64 一波，别一口气砸 96+。
2. 真正的上限是 **账号数 × 单账号吞吐**（上游按账号串行），
   不是 `WZMM_MAX_INFLIGHT`（=256），也不是 key 的 rpm。
   按当前 120 账号，稳定吞吐约 **10 条/分**；要更快只能加账号。
3. 提交是瞬时 burst：96 条 **7.0 秒**全部拿到 id，`http 429 = 0`。
4. 60 万次轮询里 0 次重试（`client.requests=4278`，`retries=0`）。

### 零积分核对（64 条前后）

```
points_total  96000 -> 96000
credits_total     0 -> 0
```

1080P 走的是试用额度，**64 条一条积分没扣**。

### 试用过期不等于报废

`wzmm-expiry.service` 在 trial_exp=0 的账号上实测 2/2 成功出 1080P
（`credits=0`、`trial=0`、`member=Free`）。
`cap_left()` 对过期账号给的是 `EXPIRED_DAILY_CAP`（默认 100/账号/天），
所以池子里 49 个账号 24 小时内到期**不会**让产能归零。

### 顺手修的两个生产问题

1. **selftest 假失败**：09:01 的每小时巡检报 `bulk empty prompt rejected` 失败，
   原因是 R624 的 40 并发把主 key 的 120 rpm 打满，测试拿到 429。
   `call()` 现在只对 429 重试 3 次（1.2s×attempt），其它状态码原样返回。
2. **主 key 限额**：`grandpa` 从 `rpm=120 / mc=32` 就地 PATCH 成 `rpm=600 / mc=128`
   （`PATCH /admin/keys/{kid}`，`sk-wz-...` 不变）。
   同时补了两条 selftest：就地改限额、负数拒绝 → **141 条**。

### 证据

- `acceptance/run_r626.log` + `batch_20261020_091557.json`（64 条）
- `acceptance/run_r627.log` + `batch_20261020_092450.json`（96 条）
- `acceptance/r627_sample.log`（每 15s 采样 running/queued）
- `expiry_test.json`（过期账号 1080P 成功）

---

## 66. 量产规划器：`GET /admin/plan`（R628）

前几轮量的是「现在能跑多快」，这一轮把它变成「要跑多快得补什么」。
`/admin/plan` 只读，不改任何东西，用**最近 7 天最忙那一个小时**的真实任务数做标定。

### 公式

```
per_account_hour = 峰值小时成功数 / 账号总数          # 实测 5.41（120 账号 ≈ 650/h）
required_accounts = ceil(target_per_hour / per_account_hour)
gb_per_day        = target_per_hour * 24 * mb_per_clip / 1024
steady_gb         = gb_per_day * ttl_days              # TTL 内堆积的稳态体积
headroom_gib      = free_gib - min(soft, hard gate)
max_per_hour_ttl  = headroom * 1024 / (mb_per_clip * 24 * ttl_days)
ttl_needed_h      = headroom * 24 / gb_per_day
```

### 实测输出（2026-10-20 09:40 CST）

`GET /admin/plan`（默认 600 条/小时、TTL 3 天）：

```json
{"target_per_hour":600,"feasible_now":false,"required_accounts":111,
 "accounts":{"total":120,"available":120,"gap":0},
 "key":{"rpm":600,"max_concurrent":111},
 "disk":{"mb_per_clip":4.04,"gb_per_day":56.81,"steady_gb":170.43,
         "free_gib":17.8,"headroom_gib":9.8,
         "max_per_hour_at_ttl":34,"ttl_needed_h":4.1,"extra_gb_needed":160.63},
 "measured":{"peak_hour_clips":649,"clips_24h":2296,"clips_per_account_hour":5.41},
 "notes":["steady state 170.4 GB > headroom 9.8 GB: need 160.6 GB more disk, or purge within 4.1 h"]}
```

R663 起同一个响应里多了 `both`，两种口径一次给全，不用再打第二次：

```json
"both":{"modes":{"retain":{"feasible_now":false,"steady_gb":170.43,"inflight_clips":0},
                 "release":{"feasible_now":true,"steady_gb":0.44,"inflight_clips":111}},
        "recommend":"release",
        "why":"release fits (steady 0.44 GB); retain steady 170.43 GB"}
```

`recommend` 只按可行性排序（先 release 后 retain，都不行就是 `null`），`why` 直接带两边的稳态体积。
所以「300/h 到底行不行」看 `both.recommend`，不用再猜 `ttl_days` 是哪套口径。

`GET /admin/plan?target_per_hour=1200`：

```json
{"feasible_now":false,"required_accounts":222,
 "accounts":{"gap":102},
 "notes":["short 102 account(s): add them or lower target_per_hour",
          "steady state 340.9 GB > headroom 9.8 GB: ...",
          "above the measured sweet spot (64 concurrent, ~600/h): expect p90 to grow"]}
```

`GET /admin/plan?target_per_hour=0` → **400**。

### 这张表说明什么

| 目标 | 账号够不够 | 盘够不够 |
|---|---|---|
| 600 条/小时 | 够（111 ≤ 120） | **不够**：按 TTL 3 天要 170 GB，只有 9.8 GB 余量 |
| 1200 条/小时 | **差 102 个账号** | 更不够（341 GB） |

**结论：现在的瓶颈已经从「渲染」转到「磁盘 + 账号」。**（用 `release=1` 闭环时盘不再是瓶颈，见下节） 600 条/小时能跑，
但每 4.1 小时必须清一次（或把 TTL 压到 ~4h，或加 160 GB 盘）。
盘不动的前提下，`max_per_hour_at_ttl = 34` 是稳态安全值。

### 用法

```bash
curl -s -H "X-Admin-Token: $A" "http://127.0.0.1:8808/admin/plan?target_per_hour=300&ttl_days=1"
# -> required_accounts 56, disk 差 18.6 GB，purge within 8.3h
```

SDK: `wzmm.admin_plan(target_per_hour=600, ttl_days=3)`


### `release=1`：按「下载即释放」算盘（R636）

R634 的 `batch_run.py` 是 **下载 → 校验 → release** 的闭环，服务端根本不存历史片。
上面那张表按「留 ttl_days 天」算，所以永远缺盘。加一个开关就能看到真实容量：

```bash
curl -s -H "X-Admin-Token: $A" \
  "http://127.0.0.1:8808/admin/plan?target_per_hour=600&release=1"
```

```json
{"mode":"release","feasible_now":true,"required_accounts":111,
 "accounts":{"gap":0},
 "disk":{"steady_gb":0.88,"inflight_clips":111,"inflight_gb":0.44,
         "outdir_gb_per_day":56.81,"headroom_gib":9.8},
 "notes":["release mode: server holds only the in-flight window (~0.9 GB at 111 concurrent), fits in headroom; output dir needs 56.8 GB/day (keep it off this volume)"]}
```

- `steady_gb` = 在途并发 × 单片大小 × 2（留一次重试余量）
- `outdir_gb_per_day` 是**你本地/挂载盘**的日增，不落在 `/opt`
- `mode` 默认 `retain`，`release=0` 与旧行为逐字段一致

**这才是 600 条/小时的真实答案：盘不缺，缺的是把片子搬走的出口。**

SDK：`wzmm.admin_plan(target_per_hour=600, release=True)`

---

## 67. 按任务保留期：`retention_hours`（R631）

R628 的规划器说得很清楚：600 条/小时 = **56.81 GB/天**，全局 TTL 3 天 = 170 GB 稳态，
而这台盘只有 9.8 GB 余量。要跑量又不想删别人的片子，就得让**每一条任务自己带保留期**。

### 用法

```bash
curl -s -X POST -H "Authorization: Bearer $K" -H "Content-Type: application/json" \
  -d '{"prompt":"雨夜霓虹街道","resolution":"1080P","duration":6,"retention_hours":4}' \
  http://127.0.0.1:8808/v1/videos/generations
```

```json
{"id":"vid_...","status":"queued","retention_hours":4.0,"expires_at":1792493421}
```

- 取值 **1–720** 小时；`0` / 负数 / 超界 → **400**
- 不传 = 走全局 `WZMM_TTL_DAYS`（3 天），行为和以前完全一样
- `GET /v1/videos/{id}` 会带回 `expires_at`
- **pinned 依然免删**：`coalesce(pinned,0)=0` 的判断没有动

### 600 条/小时需要多长保留期

| retention_hours | 稳态占用 | 对比 9.8 GB 余量 |
|---|---|---|
| 1h | 2.37 GB | 宽松 |
| 2h | 4.73 GB | 宽松 |
| **4h** | **9.47 GB** | 刚好卡住 |
| 6h | 14.20 GB | 超了 |

结论：**600/h 想在这台盘上长期跑，用 `retention_hours: 4`（或更短）**，配合客户端随用随下。
要保留 3 天就按 R628 的结论加 ~160 GB 盘。

### 验证（都跑过，不是纸上推演）

1. 校验：`retention_hours=0` → 400，`999` → 400
2. 真建单：`retention_hours=1` → 返回 `expires_at - created = 3600`（误差 ≤5s），随后 cancel
3. janitor 真删：造 64 B 假文件 + `expires_at = now-60` 的行 → 重启服务 →
   **文件消失、`local_path` 变 null**，测试行再删除

### 代码位置

- `tasks.expires_at`（启动时 `alter table ... add column`，老库自动升级）
- `janitor()`：`due = expires_at 到期`，没有 `expires_at` 才回退到 mtime + TTL

---

## 68. 下载即释放：`POST /v1/videos/{id}/release`（R633）

`retention_hours` 最短是 1 小时，600 条/小时仍然要压 2.37 GB。
真正的高量产姿势是：**客户端拿到字节的那一刻就把服务端副本删掉**，稳态占用趋近 0。

### 用法

```bash
curl -s -X POST -H "Authorization: Bearer $K" \
  http://127.0.0.1:8808/v1/videos/vid_xxx/release
```

```json
{"id":"vid_xxx","released":true,"already":false,"bytes_freed":4362210}
```

第二次调用：

```json
{"id":"vid_xxx","released":true,"already":true,"bytes_freed":0}
```

### 语义

| 规则 | 说明 |
|---|---|
| 幂等 | 重复调用不报错，`already=true` |
| 归属 | 只有创建它的 key 或 admin 能释放；别人 → **403** |
| 不存在的 id | **404** |
| pinned | **也删**（这是 owner 的显式指令，优先级高于 janitor 的 pin 保护） |
| 删什么 | `.mp4` + `.thumb.jpg` + `.meta.json` 三个一起 |
| 不删什么 | 数据库行本身：`status`/`url`/`usage` 历史全保留，`released_at` 记时间 |
| `bytes_freed` | 只统计 mp4 本体 |

### 验证（真删过）

1. 造 64 B 假 mp4 + 8 B 缩略图 + 归属 grandpa 的行 → 第一次 `release` 返回
   `{"released":true,"already":false,"bytes_freed":64}`，**两个文件都没了**
2. 第二次 → `{"already":true,"bytes_freed":0}`
3. 数据库 `local_path=null`、`released_at` 已写入，测试行随后清理
4. selftest 新增：`release unknown -> 404`、`other key release -> 403`

### 600 条/小时的推荐剧本

```
提交(带 retention_hours=2 兜底) → 轮询 → GET content 下载 → POST release
```

只要客户端下载完就 release，磁盘稳态 = 在途那几十条，和 TTL 无关。
`retention_hours` 变成「客户端挂了没人 release 时的兜底」，不再需要压到 4 小时。

---

## 69. 一条命令跑量产：`batch_run.py`（R634）

R633 给了 `release`，这一轮把它串成完整闭环：**提交 → 轮询 → 下载 → 校验 → 释放**。

```bash
cd /opt/wzmm
export WZMM_KEY=$(cat apikey.txt)

# 先看会跑成什么样（不提交任何任务）
./venv/bin/python batch_run.py --n 600 --chunk 32 --retention-hours 2 --dry-run
# {"dry_run":true,"n":600,"chunk":32,"chunks":19,"retention_hours":2.0,"release":true,"est_gb":2.52}

# 真跑
./venv/bin/python batch_run.py --n 600 --chunk 32 --retention-hours 2 --outdir /data/clips
```

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--n` | 10 | 要出多少条 |
| `--chunk` | 32 | 一波提交多少（32–64 是实测甜点） |
| `--pace` | 0 | 波与波之间停几秒 |
| `--retention-hours` | 2 | 客户端没来 release 时的兜底 TTL |
| `--outdir` | `/opt/wzmm/acceptance` | 下载落地目录 |
| `--no-release` | 关 | 保留服务端副本（调试用）；默认下载成功且字节数 >0 就释放 |
| `--dry-run` | 关 | 只打印计划，不提交 |
| `--prompts-file` | 空 | 一行一个提示词，空则用内置 8 条轮转 |

退出码：**全部下载成功才 0**；有失败 → 1，并写 `batch_run_<ts>.json` 证据。

### 单条端到端验收（真跑）

```
chunk 1/1 submitted 1/1 (err 0)
{"n":1,"submitted":1,"succeeded":1,"downloaded":1,"released":1,
 "bytes_freed":7548545,"mb_downloaded":7.55,"wall_s":230.1,"pass":true}
```

- 下载文件 ffprobe：**1920×1080 / h264** ✅
- 服务端副本：**不存在** ✅
- 数据库：`local_path=null`、`released_at` 已写 ✅
- 释放字节数 = 下载字节数（7,548,545）✅

### 抓到的竞态（已修）

第一次验收时 `bytes_freed=0`，但服务端副本还在。原因：
客户端在「状态刚变 succeeded」的瞬间就下载并 release，而后台的 `_download()`
还没把文件落盘，于是 `release` 看到 `local_path=null` 返回 `already=true`，
几秒后下载器又把文件写了回来。

修法（两条）：

1. `release` 无论有没有文件，都写 `released_at`（记录「客户端已经拿走了」这个意图）
2. `_download()` 落盘前重新查 `released_at`；如果已经被释放 → 删掉刚落地的文件、不写 `local_path`

复测：`bytes_freed=7,548,545`，服务端副本消失，`released_at` 有值。

### 600 条/小时的推荐组合

```bash
./venv/bin/python batch_run.py --n 600 --chunk 32 --pace 0 --retention-hours 4 \
  --outdir /data/clips
```

在途窗口就是磁盘占用（几十条 × 4 MB），下载完即释放；`retention_hours` 只是防客户端崩掉。
按 R626/R627 的曲线，600 条大约 60 分钟；这时**盘不再是瓶颈**，账号数才是。

---

## 70. 号池扩容：这台机器现在注册不了新号（R635）

### 结论先说

**120 个账号是硬上限**，不是配额限制，是**出口 IP 被 Akamai 拦了**。

```
GET https://account.minimax.io/unified-login
→ HTTP 403  <TITLE>Access Denied</TITLE>
   ... "You don't have permission to access http://account.minimax.io/unified-login"
```

同时注册脚本 `_w120_reg_fresh.py` 里硬编码了

```
--proxy-server=http://127.0.0.1:3999
```

而这台机器上 **3999 端口没有进程监听**（`proxy_up=false`），所以即使 IP 没被封也走不通。

### 探针：`pool_grow_probe.py`

只发 **1 个 GET**，不创建任何账号，纯 stdlib。

```bash
cd /opt/wzmm
./venv/bin/python pool_grow_probe.py
# {"ts":1792491911,"proxy_up":false,"http":403,"state":"akamai_denied", ...}
echo $?    # 2
```

| 退出码 | state | 含义 |
|---|---|---|
| 0 | `ok` | 注册链路可用（代理通 + 页面 200/302） |
| 2 | `akamai_denied` | **IP 被 Akamai 拒**，必须换出口 |
| 3 | `network_error` | 到不了 account.minimax.io |

结果落盘 `/opt/wzmm/_pool_growth.json`，并由 `/admin/plan` 读取。

### 观测入口

```bash
curl -s -H "X-Admin-Token: $WZMM_ADMIN_KEY" \
  "http://127.0.0.1:8808/admin/plan?target_per_hour=600" | jq .pool_growth
```

```json
{"state":"akamai_denied","age_h":0.1,"stale":false,"http":403,
 "proxy_up":false,"used_proxy":false,"proxy_port":"127.0.0.1:3999",
 "verdict":"this IP is denied by Akamai - pool cannot grow without a proxy"}
```

`state != ok` 且未过期时，`plan.notes` 会直接写一行
`pool cannot grow: akamai_denied - ...`，但 **`feasible_now` 不受影响**——
扩不了号不影响现有产能，只是吞吐上不去。

### 定时探测

`wzmm-pool-grow.timer` 每 6 小时跑一次（`OnCalendar=*-*-* 00/6:17:00`），
日志 `/var/log/wzmm-pool-grow.log`。`verify_all.sh` 里对应段落**只 WARN 不 FAIL**：
被封是外部条件，不该让巡检变红。

### 要扩容，需要什么

1. 一个能过 Akamai 的**住宅/移动代理**（机房 IP 基本都被拦）
2. 代理监听在 `127.0.0.1:3999`，或改 `_w120_reg_fresh.py` 的 `--proxy-server`
3. 重跑探针 → `state=ok`，再跑注册脚本

```bash
# 代理就绪后自检
./venv/bin/python pool_grow_probe.py && echo GROWABLE
```

探针返回 0 之前，任何"批量建号"的动作都只会得到 403，别浪费号池脚本。

---

## 71. 常驻产线：`runner.py` 按目标速率跑一整晚（R637）

`batch_run.py` 是「跑 N 条然后退出」；产线要的是「**按 N 条/小时一直跑**」。
`runner.py` 就是那个常驻进程：限速提交、每条独立走完 `submit → poll → download → release`、
状态与流水落盘，被杀掉还能接着跑。

### 起步

```bash
cd /opt/wzmm
export WZMM_KEY=$(cat apikey.txt)

# 先看计划（不提交任何任务）
./venv/bin/python runner.py --rate 300 --concurrency 32 --duration 8h --dry-run
# {"dry_run":true,"rate_per_h":300.0,"concurrency":32,"duration_s":28800,
#  "expected_clips":2400,"capacity_per_h":500,"feasible":true,
#  "est_gb_out":10.08,"release":true,"outdir":"/data/clips"}

# 真跑
./venv/bin/python runner.py --rate 300 --concurrency 32 --duration 8h --outdir /data/clips
```

`capacity_per_h` 的算法：**并发 × 3600 ÷ 230s**（230s 是 R634 实测的单条端到端耗时）。
`feasible=false` 就说明速率要得比并发能给的高，先加 `--concurrency`。

| 目标 | 建议并发 | 单条耗时 | 说明 |
|---|---|---|---|
| 300/h | 24 | ~230s | 稳态富余 |
| 600/h | 48 | ~230s | 实测甜点区（R626/R627） |
| 900/h | 64+ | ~230s | 逼近上限，p90 会涨 |

### 单条真跑验收（R637c）

```
{"event":"max_reached","done":1}
{"object":"runner_summary","wall":"0h02m52s","submitted":1,"succeeded":1,
 "downloaded":1,"released":1,"gb_downloaded":0.005,"gb_freed":0.005,
 "served_server":1,"served_upstream":0,"release_noop":0,
 "median_s":171.2,"pass":true}
```

流水行（`runner_ledger.jsonl`）：

```json
{"id":"vid_dc302168e5c7f3409918c44e","status":"succeeded","served_from":"server",
 "bytes":5142850,"mb":5.14,"released":true,"bytes_freed":5142850,"elapsed_s":171.2}
```

- 下载 ffprobe：**1920×1080 / h264** ✅
- `bytes_freed == bytes`（5,142,850）✅
- 服务端副本：已消失，`local_path=null`、`released_at` 已写 ✅

### 三种落盘来源（`served_from`）

| 值 | 含义 | release 效果 |
|---|---|---|
| `server` | 服务端自己的副本，走 FileResponse | `bytes_freed == bytes` |
| `upstream` | 后台下载还没落盘，`/content` **302 到上游**，客户端直连 MiniMax | `bytes_freed=0`，记 `release_noop` |

`--local-wait 15` 会先等一会儿本地副本；等不到才吃 302。
两种都算成功，但只有 `server` 能证明「盘上那份被我们清掉了」。

### 断点续跑

```bash
# 第一次被 kill / 断电 / 手动 Ctrl-C
./venv/bin/python runner.py --resume --rate 300 --concurrency 32 --outdir /data/clips
```

- `runner_state.json` 里存 `inflight`（tid + prompt + 提交时间）
- `--resume` 会**收养**这些在途任务而不是重新提交（不重复花额度）
- 被收养的条目在流水里 `"adopted": true`

**停机语义**：收到 SIGTERM/SIGINT → 立刻停止提交 → 给在途任务 `--drain` 秒收尾
（默认 120s）→ 还没完成的记为 `abandoned`，保留在 state 里等下次 `--resume`。
`abandoned` 不算失败，退出码仍是 0。

> 最容易踩的坑：`--drain 0` 时在途任务会立刻变成 `abandoned`。
> 这是设计行为，不是 bug —— 下次带 `--resume` 就接着跑。

### 退出码

| 码 | 含义 |
|---|---|
| 0 | 干净停止（含 `abandoned`），没有失败/错误 |
| 1 | 至少一条 `failed` 或 `errors` |
| 2 | 参数非法（rate ≤ 0 / concurrency 越界） |

### 磁盘

- 服务端：只存在途窗口（`/admin/plan?release=1` 的 `steady_gb`，600/h 约 0.9 GB）
- outdir：`rate × 24 × 单片大小`，600/h ≈ **56.8 GB/天**
- 所以 `--outdir` 别放 `/opt`，挂到别的盘或对象存储同步目录

---

## 72. 产线运维面：磁盘护栏、心跳、`/admin/runner`（R638）

`runner.py` 能跑一整晚，但运维上还缺三件事：**盘满了怎么办、怎么知道它还活着、怎么用 API 看**。

### 磁盘护栏

```bash
--min-free-gb 4        # outdir 所在卷低于 4GB 就暂停提交（默认）
--disk-action pause    # pause | stop
```

- `pause`：停止投喂新片，在途的照常收尾；空间回来自动续跑
- `stop`：直接进入 drain 流程退出（适合配 systemd 重启策略）

事件会写进日志：

```json
{"event":"disk_pause","free_gb":3.7,"min_free_gb":4.0,"outdir":"/data/clips"}
{"event":"disk_resume","free_gb":5.1,"paused_s":42.0}
```

**这是唯一会主动降速的保护**：宁可少出片，也不把机器写满（写满会连带把 `videos/` 和 sqlite 一起拖死）。

### 心跳文件

默认 `/opt/wzmm/runner_heartbeat.json`，每个统计周期（`--stats-every 30`）覆盖一次：

```json
{"object":"runner_heartbeat","pid":12345,"target_rate":300,"concurrency":32,
 "active":24,"submitted":1200,"downloaded":1180,"failed":0,"errors":0,"abandoned":0,
 "achieved_per_h":298.4,"elapsed_s":14220,"outdir":"/data/clips",
 "free_gb":41.2,"min_free_gb":4.0,"paused":false,"paused_total_s":0.0,
 "median_s":168.3,"p90_s":241.7}
```

### `GET /admin/runner`

```bash
curl -s -H "X-Admin-Token: $A" "http://127.0.0.1:8808/admin/runner"
```

```json
{"object":"runner","age_s":4,"stale":false,"stopped":false,"healthy":true,
 "heartbeat":{"pid":12345,"target_rate":300,"achieved_per_h":298.4,"active":24,
              "free_gb":41.2,"paused":false, "...":"..."}}
```

| 字段 | 含义 |
|---|---|
| `age_s` | 心跳距今秒数 |
| `stale` | 超过 `?max_age_s=`（默认 180s）没更新 |
| `stopped` | 进程是干净停的（SIGTERM + drain），不是崩了 |
| `healthy` | 新鲜 **且** 没暂停、没停过 |
| `heartbeat: null` | 从没跑过（`note` 里写原因） |

没有 admin token → **401**。`verify_all.sh` 里这段只 WARN：产线是可选组件，停了不该让巡检变红。

### systemd（单位已就位，默认不启用）

```bash
cat /opt/wzmm/runner.env.example >> /opt/wzmm/runner.env   # 改成自己的速率/目录
vi /opt/wzmm/runner.env

systemctl daemon-reload
systemctl enable --now wzmm-runner
systemctl status wzmm-runner --no-pager
curl -s -H "X-Admin-Token: $A" "http://127.0.0.1:8808/admin/runner" | head -c 300
```

`runner.env` 只需要一行（其余走默认）：

```
RUNNER_ARGS=--rate 300 --concurrency 32 --outdir /data/clips --resume --min-free-gb 6
```

单位特性：`Restart=always`（崩了 10s 后拉起）、`KillSignal=SIGTERM` + `TimeoutStopSec=300`
（给 drain 留够时间，不会把在途片子硬砍死）、`RequiresMountsFor` 注释在文件里，挂好盘再打开。

### 容量自检顺序

1. `df -h <outdir>`：日增 = `rate × 24 × 单片大小`（600/h ≈ 56.8 GB/天）
2. `runner.py --dry-run`：`feasible` 看并发够不够
3. `GET /admin/plan?release=1`：看服务端在途窗口
4. `GET /admin/runner`：看实际达成率与 `paused`

---

## 73. 每日签到：重试、状态语义、重复领取（R639）

`signin_daily.py` 给池子里每个号领当天积分（400/天，第 4/7 天 1000）。

### 以前的问题

1. 领取失败**只试一次**。120 个号 24 并发打过去，上游偶发
   `1406011050 "Something went wrong"` 就直接记失败，当天那 400 分就没了。
2. 判断「今天签没签」只看 `status == 2`，但上游其实有 **3 = 已领取**，
   于是已领的号又被拿去领一遍，白白制造 429/错误。
3. 重复领取时上游返回 **HTTP 200 + `claim_result=2`**，旧代码当成「领取成功」，
   统计里的 `claimed` 是虚高的（实测一次全量跑报了 84 个"claimed"，实际 0 个新领取）。

### 现在的行为

```bash
./venv/bin/python signin_daily.py                 # 全量，默认重试 3 次
./venv/bin/python signin_daily.py --retries 5     # 更耐心
./venv/bin/python signin_daily.py --only a@x,b@y  # 只补某几个号
./venv/bin/python signin_daily.py --dry           # 只看有没有可领
./venv/bin/python signin_daily.py --self-test     # 离线验证重试逻辑
```

每次 claim 失败后：

1. **回读 `/signin/status`** —— 失败也可能其实已经领到了（上游先落库再报错）
2. 没领到就退避重试（`1.2s × 次数 + 抖动`）
3. 全部用完 → `claim-failed`，记录 `attempts` 与 `code`

### 状态码语义（实测）

| `days[].status` | 含义 |
|---|---|
| 1 | 可领取 |
| 2 | 已领取（早前某次） |
| 3 | 已领取（本次/近期） |

`is_today` 那条的 `status in (2, 3)` 都算"今天已完成"。

`claim` 返回里的 `data.claim_result`：

| 值 | 含义 | 记账 |
|---|---|---|
| 1 | 本次真的领到了 | `claimed` |
| 2 | 今天已经领过（重复请求） | `skip` / `already` |

### 实测（120 号全量，2026-10-20 10:56 UTC）

```
signin start: 120 accounts dry=False retries=3
signin done in 12.3s claimed=0 already=118 failed=2 retried=2
FAIL [redacted]@example.com {"base_resp":{"status_code":1406011050,...}}
FAIL [redacted]@example.com {"base_resp":{"status_code":1406011050,...}}
```

- 修好语义后，同一批号从「claimed=84」变成「claimed=0 / already=118」——
  **统计不再虚高**，也没有多余的 claim 请求
- 剩下 2 个号：`trial_exp` 当天 08:00 UTC 到期，上游持续拒绝
  `1406011050`，重试 4 次（约 7s）后仍失败 → 判为上游拒绝，不是网络抖动

### 离线自检

```bash
./venv/bin/python signin_daily.py --self-test
SELFTEST confirm-by-status  PASS attempts=1
SELFTEST retry-then-ok      PASS attempts=3
SELFTEST exhaust-retries    PASS attempts=4 code=1406011050
SELFTEST SUMMARY PASS retries=3
```

三条覆盖：失败但状态确认已领、两次抖动后成功、永久失败精确试满 `RETRIES+1` 次。

### 定时

`wzmm-signin.timer` 现在一天两个窗口：

```
OnCalendar=*-*-* 08:15:00 UTC
OnCalendar=*-*-* 16:05:00 UTC
```

签到本身幂等（`claim_result=2` 只记 skip），多跑一次不会重复加分，
只是别让某个窗口整体错过。

---

## 74. 开跑前体检：`GET /admin/preflight`（R640）

跑 8 小时之前先问一句：**现在能不能开？** 这个接口把六个信号合成一个 GO / NO-GO，
每个 NO-GO 都带一条能直接照做的 blocker。

```bash
curl -s -H "X-Admin-Token: $A" \
  "http://127.0.0.1:8808/admin/preflight?rate_per_hour=300&hours=8&outdir_free_gb=40"
```

```json
{"object":"preflight","verdict":"GO",
 "rate_per_hour":300,"hours":8,"ttl_days":3,"planned_clips":2400,
 "blockers":[],
 "warnings":["pool cannot grow (akamai_denied): new accounts need a residential proxy"],
 "checks":{
   "accounts":{"total":120,"available":120,"gap":0},
   "disk_server":{"steady_gb":0.44,"headroom_gib":9.8,"inflight_clips":56},
   "outdir":{"needed_gb":9.4,"free_gb":40.0,"hours":8},
   "canary":{"ok":true,"age_h":0.6},
   "queue":{"inflight":0,"max_inflight":256},
   "pool_growth":{"state":"akamai_denied"}},
 "plan":{"required_accounts":56,"key":{"rpm":300,"max_concurrent":56},"mode":"release"}}
```

### 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `rate_per_hour` | 300 | 目标产能 |
| `hours` | 8 | 这次要跑多久（算 outdir 需要多少 GB） |
| `ttl_days` | 3 | 只在 retain 模式影响；体检固定按 release 模式算服务端盘 |
| `outdir_free_gb` | -1 | 传了才检查输出盘；`-1` 表示"我自己盯着" |

### 六项检查

| check | 何时 NO-GO |
|---|---|
| `accounts` | 目标产能超过现池能撑的量（`gap > 0`） |
| `disk_server` | 在途窗口 > 剩余盘（release 模式下几乎不会） |
| `outdir` | 这 `hours` 小时要写的 GB > 你给的 `outdir_free_gb` |
| `canary` | 端到端探针最近一次失败 |
| `queue` | 在途已打满（只 WARN，会让新任务排队） |
| `runner` | 已经有产线在跑（只 WARN，避免双份烧额度） |

`pool_growth` 永远是 WARN：扩不了号不影响现有产能。

### 什么时候用

```bash
# 手动开跑前
curl -s .../admin/preflight?rate_per_hour=600&hours=8&outdir_free_gb=$(df -Bg /data | awk 'NR==2{print $4}' | tr -d G)

# 塞进 systemd，让产线自己体检（NO-GO 就不启动）
ExecStartPre=/bin/sh -c 'curl -fsS -H "X-Admin-Token: $A" \
  "http://127.0.0.1:8808/admin/preflight?rate_per_hour=300&hours=8" \
  | grep -q "\"verdict\":\"GO\""'
```

`verify_all.sh` 里对应段落只 WARN：体检结果取决于当时的盘和目标速率，不该让巡检变红。

SDK：`wzmm.admin_preflight(300, hours=8, outdir_free_gb=40)`


## 75. 产线自适应并发：`runner.py --auto-concurrency`（R642）

固定并发是拍脑袋：230s/条时，300/h 要 ~20 路，600/h 要 ~39 路，人肉试太慢。
R642 加了闭环控制器，让产线自己找合适的水位。

```bash
./venv/bin/python runner.py --rate 600 --auto-concurrency \
  --concurrency 64 --min-concurrency 4 \
  --outdir /data/clips --duration 8h --min-free-gb 40
```

- `--concurrency` 变成**天花板**（线程池按它开），实际在飞的条数是 `concurrency_effective`
- `--min-concurrency` 是地板，默认 4
- 控制律（每 `--stats-every` 一个窗口，默认 30s）：
  | 观测 | 动作 |
  |---|---|
  | 完成率 < 目标 85% | `+25%`（至少 +1，封顶天花板） |
  | 窗口错误率 ≥ 5% | `-30%`（至少 -1，落到地板） |
  | 完成率 > 目标 125% | `-1`（慢慢收，避免浪费额度） |
  | 其余 | 保持 |
- 只在磁盘护栏 `go` 时调节；`disk_pause` 期间不降并发（不是产能不够，是盘满了）
- 每次调整打一条 `concurrency_change` 事件，带 `from/to/reason/achieved_per_h/err_rate`

心跳与 summary 新增两个字段，`GET /admin/runner` 直接能看到：

```json
{"concurrency": 64, "concurrency_effective": 38, "auto_concurrency": true}
```

离线自检（不联网、不出片）：

```bash
./venv/bin/python runner.py --self-test-concurrency --concurrency 64 --min-concurrency 4
# {"event":"self_test_concurrency","grown_to":38,"err_drop_to":26,"fast_floor":4,
#  "hold":[20,"hold"],"ceiling":64,"per_worker_per_h":15.65,"pass":true}
```

`--dry-run` 也会带上 `auto_concurrency` / `min_concurrency` / `capacity_per_h_at_min`，
方便先算「地板够不够、天花板够不够」再开跑。


## 76. 产线端到端自检：`runner.py --self-test-pipeline`（R643）

以前验证产线只能真开跑，既烧额度又出片。R643 内置了一个桩 API（127.0.0.1 随机端口），
把 submit → poll → download → release 整条链路真跑一遍，**不联网、不出片、不花额度**。

```bash
./venv/bin/python runner.py --self-test-pipeline
# {"event":"self_test_pipeline","rc":0,"succeeded":2,"downloaded":2,"released":2,
#  "submitted":2,"errors":0,"failed":0,"freed_bytes":8204,
#  "files":["t-stub01.mp4","t-stub02.mp4"],"sizes":[4102,4102],
#  "served_from":["server","server"],"pass":true}
```

桩端点：

| 方法 | 路径 | 行为 |
|---|---|---|
| POST | `/v1/videos/generations` | 发号 `t-stubNN`，初始 `queued` |
| GET | `/v1/videos/generations/{id}` | 第一次 `running`，第二次 `succeeded` + `local=true` |
| GET | `/v1/videos/{id}/content` | 4102 字节假 mp4 |
| POST | `/v1/videos/{id}/release` | `released=true`，`bytes_freed=4102` |

断言覆盖：提交数、成功/下载/释放计数、`served_from=server`、落盘字节数、
ledger 行数、`freed_bytes` 合计、桩侧真的收到两次 release。
任何一条不满足就 `pass=false` 并返回 1，`verify_all.sh` 里对应段落会直接 FAIL。

这条自检能抓住 R637 那类事故（`/content` 302 上游、release 空转、ledger 写错、计数漂移）。


## 77. 多 key 轮换：`runner.py --keys-file`（R644）

单 key 的天花板是它自己的 rpm。要量产就得把一个产线的片子分摊到多把 key 上。

```bash
cat > /opt/wzmm/keys.txt <<'EOF'
sk-wz-REPLACE_WITH_YOUR_KEY
sk-wz-REPLACE_WITH_YOUR_KEY
EOF

./venv/bin/python runner.py --rate 600 --auto-concurrency --concurrency 64 \
  --keys-file /opt/wzmm/keys.txt --outdir /data/clips --duration 8h
```

规则：

- 一行一把 key，`#` 开头和空行忽略；文件缺失/为空时回退 `--key` 或 `WZMM_KEY`
- **按条轮换**：一条片子的 submit / poll / content / release 全程用同一把 key（归属一致，别的 key 查不到）
- 断点续跑时 inflight 只存 **key 下标**（`ki`），不把明文 key 写进 outdir 的 state 文件
- 心跳与 summary 新增：
  ```json
  {"keys": 2, "keys_used": {"AAAAAA": 1, "BBBBBB": 1}}
  ```
  只暴露 key 尾 6 位，够看分摊是否均匀，不泄露整串
- 每把 key 自己的 `rpm` 仍由服务端 `/admin/keys` 的配置决定；轮换只是把并发压力摊开

实测（R644，桩 API 双 key）：

```json
{"event":"self_test_pipeline","submitted":2,"downloaded":2,"released":2,
 "keys":2,"keys_used":{"AAAAAA":1,"BBBBBB":1},
 "auth_seen":["Bearer sk-wz-s...AAAAAA","Bearer sk-wz-s...BBBBBB"],"pass":true}
```

`verify_all.sh` 的 `== runner pipeline ==` 段现在同时断言「两把 key 各用一次」。


## 78. 断点续跑自检：`runner.py --self-test-resume`（R645）

产线最贵的 bug 是「进程被 kill，在途的片子被当成失败重新提交」——那是白烧额度。
R645 用桩 API 把这件事做成可重复的回归：

```bash
./venv/bin/python runner.py --self-test-resume
# {"event":"self_test_resume","got_inflight":true,"rc_stop":0,
#  "inflight_after_stop":1,"abandoned_ledger":1,"rc_resume":0,
#  "ledger_lines":2,"adopted":1,"adopted_status":"succeeded",
#  "served_from":"server","inflight_after_resume":0,"pass":true}
```

流程：

1. 父进程起桩 API，`polls_before_done=10^9`（永远 running），子进程 `runner.py --rate 30 --concurrency 1 --max 1`
2. 等 `runner_state.json` 里出现 inflight，向子进程发 **SIGTERM**
3. 断言：子进程退出码 0、state 仍保留 1 条 inflight、ledger 里 1 条 `abandoned`
4. 放开桩（`polls_before_done=1`），子进程 `--resume` 启动
5. 断言：**不再重新 submit**（ledger 恰好 2 行）、收养那条 `adopted=true`、`status=succeeded`、`served_from=server`、落盘 4102B、state inflight 清空、桩侧收到 release

这条自检同时覆盖：`--resume` 读取 totals、inflight 的 key 下标解析、
SIGTERM 与 `_hard` 的先后语义、abandoned 不算失败、以及 release 不会空转。


## 79. 浏览器 Key 控制台：`GET /admin/ui?token=`（R646）

建 key 不用再手敲 curl 了。

```
https://api.example.com/admin/ui?token=<你的 admin token>
```

页面功能：

| 区域 | 能做什么 |
|---|---|
| 创建 Key | 填 名称 / rpm / max_concurrent / quota / daily_quota → 直接拿到 `sk-wz-...`（带复制按钮） |
| 现有 Key 表 | 名称、Key（前 10 + 尾 6，可复制全文）、rpm、并发、已用、启用/停用 |
| 行操作 | 停用/启用（PATCH disabled）、改 rpm（弹窗）、硬删（DELETE ?hard=1，有任务会 409 并提示） |
| 禁用统计 | 调 `/admin/keys/stale`，显示「禁用 N 把 / 可硬删 M 把 / 有任务 K 把」 |

安全边界：

- 页面本身要 `?token=`，与 `X-Admin-Token` 同一把管理密钥；错误/缺失都是 401
- token 只嵌在页面 JS 里用于同源 fetch，不写 cookie、不发第三方
- 所有动作走的还是原有的 `/admin/keys` 系列接口，权限模型没变
- 硬删仍受「有任务则 409」保护，页面上删不掉会原样提示

实测（R646）：

```
page=200 bytes=6534     # 含「创建 Key」，无 __TOKEN__ 残留
bad=401 none=401        # token 错误 / 缺失
create=200 patch=200 delete=200   # 页面按钮背后的三次调用
node --check ui.js      # 前端脚本语法通过
```

`selftest.py` 新增 3 条（页面 200 / 错 token 401 / 无 token 401）→ **169/169**。


## 80. 多速率体检：`GET /admin/preflight/matrix`（R647）

选 300 还是 600，不用拍脑袋，一次把几档都算出来。

```bash
curl -s -H "X-Admin-Token: $A"   "http://127.0.0.1:8808/admin/preflight/matrix?rates=300,600,1200&hours=8&outdir_free_gb=18"
```

2026-10-20 实测（服务器当前状态）：

| 速率 | 结论 | 需要账号 | 每天落盘 | 8h 需要 | 在途窗口 | 剩余盘 |
|---|---|---|---|---|---|---|
| 300/h | **GO** | 56 | 28.41 GB | 9.5 GB | 0.44 GB | 9.8 GB |
| 600/h | NO-GO | 111 | 56.81 GB | 18.9 GB | 0.88 GB | 9.8 GB |
| 1200/h | NO-GO | 222 | 113.62 GB | 37.9 GB | 1.02 GB | 9.8 GB |

- 600/h 只差一点：outdir 要 18.9 GB，当时只剩 18.0 GB
- 1200/h 还差 102 个账号（号池扩不动，卡 Akamai 403）

返回结构：

```json
{"object":"preflight_matrix","hours":8,"ttl_days":3,"outdir_free_gb":18,
 "summary":{"300":"GO","600":"NO-GO","1200":"NO-GO"},
 "scenarios":[{"rate_per_hour":300,"verdict":"GO","required_accounts":56,
               "outdir_gb_per_day":28.41,"outdir_needed_gb":9.5,
               "steady_gb":0.44,"headroom_gib":9.8,
               "blockers":[],"warnings":["..."]}]}
```

参数：

| 参数 | 默认 | 说明 |
|---|---|---|
| `rates` | `300,600,1200` | 逗号分隔，最多 8 档，1..100000 |
| `hours` | 8 | 本次计划跑多久（0..720） |
| `ttl_days` | 3 | 保留策略，release 模式下影响很小 |
| `outdir_free_gb` | -1 | 传了才检查输出盘；`-1` 表示不检查 |

单速率的老接口 `/admin/preflight` 行为完全不变（内部抽成 `_preflight_calc`，两处共用）。
`selftest.py` 新增 4 条（两档返回、verdict 合法、非法 rate 400、无 admin 401）→ **173/173**。
SDK：`wzmm.admin_preflight_matrix("300,600,1200", hours=8, outdir_free_gb=18)`


## 81. 续跑时的 key 归属：尾号优先（R648）

`--keys-file` 是给人改的，产线停着的时候你完全可能把 keys.txt 重新排序、删掉一把死 key。
老实现只存下标 `ki`，重排之后 `ki=0` 可能指向**另一把 key**，收养时 poll 直接 401，那条片就废了。

R648 的做法：

1. inflight 里同时记 `kt`（key 尾 6 位）和 `ki`
2. 收养时先按 `kt` 在**当前** keys 列表里找，找不到才回退 `ki`，再不行用第一把
3. 子进程（`--resume`）不会重新提交，仍然只 poll 原来那条

桩 API 也升级了：**按 key 校验归属**，submit 记下 `Bearer ...`，之后 status / content / release
用错 key 一律 401。这样测试才真的能抓到「拿错 key 去 poll」。

自检里现在会把 keys.txt 倒序再 `--resume`：

```json
{"got_inflight":true,"rc_stop":0,"inflight_after_stop":1,"abandoned_ledger":1,
 "rc_resume":0,"ledger_lines":2,"adopted":1,"adopted_status":"succeeded",
 "served_from":"server","keys_reordered":true,"keys":2,"pass":true}
```

反向验证（把尾号匹配临时关掉，其他不变）：

```json
{"rc_resume":1,"adopted_status":"error","served_from":null,"files":[],
 "keys_reordered":true,"pass":false}
```

说明这条自检**真的会红**，不是摆设；恢复实现后立刻回到 `pass=true`。


## 82. 429 不再丢片：按 key 冷却 + 轮换（R649）

高峰期最典型的失败形态：某把 key 的 rpm 打满，submit 返回 429。SDK 自己会重试 4 次，
但连续 429 超过预算就抛错，`runner.work()` 把这条记成 error——**额度没花，但这条片没了**。

R649 的处理：

1. `_submit()` 在 runner 层兜住 `APIError(429)`
2. 命中 429 的 key 按尾号进冷却表（`--key-cooldown`，默认 60s）
3. 立刻换下一把 key；全部冷却时等最早解冻的那把
4. 直到成功或尝试上限（`max(6, keys*6)`）才放弃，`errors` 仍是 0
5. 心跳里的 `keys_used` 现在统计**尝试次数**，能看出 429 时流量怎么摊的

```bash
./venv/bin/python runner.py --rate 600 --keys-file keys.txt --key-cooldown 60 ...
```

离线自检（桩 API 注入 8 次 429，SDK 重试临时关掉，专测 runner 逻辑）：

```json
{"event":"self_test_429","rc":0,"throttled_429":8,"submitted":1,"succeeded":1,
 "downloaded":1,"errors":0,"keys_used":{"AAAAAA":5,"BBBBBB":4},
 "cooldown_s":0.2,"files":["t-stub01.mp4"],"sizes":[4102],"wall_s":1.03,"pass":true}
```

8 次 429 被两把 key 轮流吃掉（5/4），第 9 次提交成功，片子照样下载+释放。
`verify_all.sh` 的 `== runner 429 ==` 段失败会直接 FAIL。


## 83. 常驻产线的开机闸门：`preflight_gate.sh`（R650）

`wzmm-runner.service` 还是 disabled（等你定速率/目录），但「怎么安全地常驻」已经接好了。

单元里的关键几行：

```ini
EnvironmentFile=-/opt/wzmm/runner.env
Environment=WZMM_BASE=http://127.0.0.1:8808
ExecStartPre=/opt/wzmm/preflight_gate.sh
ExecStart=/opt/wzmm/venv/bin/python /opt/wzmm/runner.py $RUNNER_ARGS
Restart=always
RestartSec=10
KillSignal=SIGTERM
TimeoutStopSec=300
StartLimitIntervalSec=300
StartLimitBurst=5
LimitNOFILE=65535
```

闸门逻辑（`/opt/wzmm/preflight_gate.sh`）：

1. `RUNNER_SKIP_PREFLIGHT=1` → 直接放行（应急）
2. 没配 `WZMM_ADMIN_KEY` → 退出码 **2**
3. `RUNNER_OUTDIR` 存在时自动 `df` 量剩余空间（可用 `RUNNER_OUTDIR_FREE_GB` 覆盖）
4. 调 `GET /admin/preflight`，`verdict != GO` → 退出码 **1**，systemd 不会启动产线
5. 请求失败 → 退出码 **3**

实测：

```
$ RUNNER_RATE=300 RUNNER_OUTDIR=/opt/wzmm/videos ./preflight_gate.sh
preflight: outdir=/opt/wzmm/videos free=18GB (measured at /opt/wzmm/videos)
preflight: verdict=GO rate=300/h hours=8.0 ...
rc=0

$ RUNNER_RATE=5000 ...
preflight: verdict=NO-GO ... blockers=['short 805 account(s) for 5000/h',
  'outdir needs 157.8 GB for 8.0h, only 18.0 GB free']
rc=1

$ RUNNER_SKIP_PREFLIGHT=1 ./preflight_gate.sh
preflight: skipped by RUNNER_SKIP_PREFLIGHT=1   rc=0

$ WZMM_ADMIN_KEY= ./preflight_gate.sh
preflight: WZMM_ADMIN_KEY is empty               rc=2
```

配置模板：`/opt/wzmm/runner.env.example`（复制成 `runner.env`，`chmod 600`），
里面已经写好 `RUNNER_ARGS`（auto-concurrency / keys-file / min-free-gb / key-cooldown / duration / resume）。

`systemctl is-active wzmm-runner` 目前仍是 `inactive`，`is-enabled` 仍是 `disabled`——没有你的指令不会开跑。

## 84. keys.txt 抗“复制粘贴损坏” + 空钥匙串快速失败（R652）

**问题**：`--keys-file` 原来的解析只有一句列表推导，只挡空行和 `#`。
真实环境里的粘帖损坏会让每一支片子都 401：

| 损坏 | 来源 | 后果 |
|---|---|---|
| UTF-8 BOM | Windows 记事本 / `Set-Content -Encoding UTF8` | 第一把 key 前挂 `\ufeff`，变成 `Bearer \ufeffsk-...` → 401 |
| 首尾引号 | 从 JSON / 聊天记录复制 | `Bearer "sk-..."` → 401 |
| `Bearer ` 前缀 | 直接抄 curl 命令行 | `Bearer Bearer sk-...` → 401 |
| 重复行 | 多次追加 | 同一把 key 占两格，轮询和冷却被稀释 |

**修复**（`runner.py` R652）：

1. 新 `_resolve_keys()`：`utf-8-sig` 打开（吃掉 BOM）→ `strip()`（吃掉 CRLF/空白）→ 去引号 → 去 `Bearer ` 前缀 → 按原顺序精确去重
2. 启动打一行 `keys_loaded`，带 `lines / blank / comment / dup / file_error` 计数
3. 冷却桶从“尾号”改为**完整 key**（号池上千把时尾号会撞）
4. 心跳仍显示尾号；撞尾时自动加 `#n`（`111111#1` / `111111#2`），两个号不会看成同一个
5. **空钥匙串快速失败**：`run()` 开头直接返回 `2` 并打 `no_keys`，不再让每支片子各报一次错

**解析规则**：

```
空行 / 纯空白        -> 跳过
# 开头（可前导空格）  -> 注释，跳过
"sk-..." / 'sk-...'  -> 去掉首尾引号
Bearer sk-...        -> 去掉 Bearer 前缀（大小写不敏感）
完全相同的 key        -> 只留第一次出现
其它                 -> 原样保留（让 API 报 401，而不是悄悄少一把）
```

**离线自检**：`runner.py --self-test-keyring`（9 个断言，零网络、零额度）

实测：

```json
{"event":"self_test_keyring","checks":[
 {"check":"messy_parse","ok":true},
 {"check":"dup_dropped","ok":true},
 {"check":"comments_and_blanks","ok":true},
 {"check":"single_key","ok":true},
 {"check":"fallback_env","ok":true},
 {"check":"zero_keys","ok":true},
 {"check":"missing_file","ok":true},
 {"check":"no_key_fail_fast","ok":true,"detail":{"rc":2,"keys":0}},
 {"check":"tail_collision","ok":true,"detail":["111111#1","111111#2"]}],
 "pass":true}
```

**运维提示**：`keys.txt` 一行一把，`#` 可写注释；BOM / 引号 / Bearer 现在都不会炸，
但文件权限仍建议 `chmod 600`。

## 85. 上游错误分类：5xx 重试、4xx 快失败、配置错误熔断（R653）

**发现的真 bug**：`wzmm.download()` 原来是**裸** `urlopen`，没有重试。
`/content` 边缘节点回一个 503/429，整支**已经生成完、额度已经扣掉**的片子就丢了：
字节流拿不到，runner 记 `errors=1`，`release` 也不会执行。

**同一个位置还有第二个 bug**：`work()` 里 `C["succeeded"] += 1` 写在 `download()` **之前**，
所以下载失败时同一支片子既算 `succeeded=1` 又算 `errors=1`（数字自相矛盾）。

### 修复

| 位置 | 改动 |
|---|---|
| `sdk/wzmm.py download()` | 与 `_req()` 同一套重试策略：429/500/502/503/504/连接错误 → 指数退避（`1.5^n + jitter`，尊重 `Retry-After`），最多 `RETRIES` 次 |
| `sdk/wzmm.py download()` | 先写 `<path>.part`，完整收完才 `os.replace()`；失败即删 `.part` → **半截文件不会冒充成片** |
| `runner.py work()` | `succeeded / downloaded / bytes` 只在字节落盘后一起加，`LAT` 也挪到下载成功后 |
| `runner.py` 熔断 | 连续 `--breaker-fails`（默认 12）次 **4xx**（不含 429）失败 → 停投，打 `fault_breaker`；5xx/连接错误不计数 |
| 自检 | `--self-test-outerr`：4 个场景，零网络、零额度 |

**为什么只对 4xx 熔断**：401/403/400 是配置问题（key 失效、分辨率不支持、prompt 非法），
继续按 300/h 投只会把时间烧在同一处；5xx 和断连是上游抖动，不该停线。

### 实测（本地 + 服务器）

```
transient      submit 503x2, poll 502x1, content 503x1
               -> rc=0 succeeded=1 downloaded=1 errors=0
                  submit 请求 3 次、content 请求 2 次、落盘 4102B
permanent      submit 400 永远
               -> rc=1 errors=1 succeeded=0，submit 只请求 1 次（无重试风暴）
content_dead   content 503 永远
               -> rc=1 errors=1 succeeded=0，content 请求 3 次，无残留文件
breaker        400 永远 + --breaker-fails 3
               -> 只投 3 次就停（修复前会一路投到窗口结束），wall 2.5s
```

`content_dead` 这条就是第二处 bug 的回归证据：修复前它会同时报 `succeeded=1` 和 `errors=1`。

### 新增参数

```bash
--breaker-fails 12     # 连续 4xx 失败多少次后停投；0 = 关闭
```

## 86. /status 抖动与 /release 失败都不该丢掉成片（R654）

R653 修了 `/content` 的重试，但同一支片子还有两个“非交付环节”能把已经生成好的片子弄丢：

| 环节 | 原来的行为 | 后果 |
|---|---|---|
| `GET /v1/videos/{id}/status` | SDK 重试 4 次仍失败就抛异常，直接落到 `work()` 外层 `except` | 片子还在上游渲染，本地却判死，`errors=1` |
| `POST /v1/videos/{id}/release` | 抛异常 → 同样落外层 `except` | 文件已经在盘上，却被记成错误：`succeeded=1` 且 `errors=1` |

### 修复

1. **轮询容错**：`/status` 的 5xx / 断连由 runner 自己再扛，`POLL_FAIL_CAP = 10` 次连续失败才放弃；
   4xx（不含 429）仍然立刻失败——那是配置问题，不是抖动
2. **release 降级**：`/release` 失败只记 `release_error` + `release_fail += 1`，
   **不影响** `succeeded / downloaded`，退出码也不变；成片照常交付
3. **可观测**：心跳和 summary 新增 `poll_retry`、`release_fail` 两个字段

### 实测（`--self-test-stagefail`，零网络、零额度）

```
poll_blip      /status 连吃 3 个 502 后转 succeeded
               -> rc=0 succeeded=1 downloaded=1 errors=0
                  poll_retry=3，/status 请求 4 次，落盘 4102B
release_dead   /release 永远 503
               -> rc=0 succeeded=1 downloaded=1 errors=0
                  released=0 release_fail=1，文件仍在，4102B
```

反向对照：

- 去掉轮询 try/except → `poll_blip_recovers=false`（errors=1）
- 去掉 release try/except → `release_fail_keeps_clip=false`（succeeded=1 且 errors=1）

## 87. 本地留存策略：默认永不删，开了才按龄/按量回收（R655）

产线如果 24 小时连跑，`outdir` 会一直涨：300/h × 4.3MB ≈ **1.3GB/h**，
18GB 空闲 = 13 小时后就触发 `disk_pause`，然后**永远停在暂停**——因为 release 只清上游，不动本地。

### 新增两个参数（都默认 0 = 不删任何东西）

```bash
--local-keep-hours 0    # >0：删掉超过 N 小时的已完成 mp4
--max-local-gb 0        # >0：outdir 里 mp4 总量超限时，从最旧的开始删
```

### 安全边界（自检逐条钉住）

| 规则 | 说明 |
|---|---|
| 默认关闭 | 两个参数都为 0 时 `_prune_local()` 直接返回 0，一个字节都不动 |
| 只删 `*.mp4` | `.part`（下载中的半截文件）、`runner_state.json`、ledger、heartbeat 一律不碰 |
| 不删在飞的 | `state.inflight` 里的 tid 跳过，哪怕它已经超龄 |
| 最旧优先 | 按 mtime 升序删，直到满足龄/量条件 |
| 暂停中也回收 | `disk_pause` 分支里同样调用，回收后能自己恢复 `disk_resume` |
| 有账可查 | 每次打 `prune_local` 事件；心跳/summary 新增 `pruned`、`pruned_mb` |

### 实测（`--self-test-prune`，零网络）

```
default_off   两个参数 0        -> 删除 0 个，目录内容不变
keep_hours    keep=1h，3 老 + 2 新 + 1 个 .part
              -> 删 3 老，新片和 .part 原样保留
max_gb        cap=0.5MB，5 个 1MB + t-new0 在飞
              -> 删 4 个最旧，t-new0 保留，mp4 总量 1,048,576B
off_again     改回 0            -> 删除 0 个
```

反向对照：

- 去掉 inflight 保护 → `max_gb=false`（在飞的片子被删）
- 龄判断改成“有 keep 就全删” → `keep_hours=false`（新片被误删）

## 88. 单产线锁：同一 outdir 只允许一个生产者（R656）

**风险场景**：`systemctl restart` 与新进程重叠、或人工多敲了一次 `python runner.py`。
第二个进程会：

- 用同一份 `runner_state.json` 互相覆盖（`prompt_idx`、`inflight` 全乱）
- 对同一批 prompt 再提交一次 → **额度双倍消耗**
- 两份 ledger 交错，`--resume` 收养时归属错乱

### 实现

- 启动时在 `outdir/runner.lock` 写 `{pid, started, outdir, argv}`
- 已有锁且 **pid 还活着** → 打 `lock_busy`（带 holder pid/outdir/started）并返回 **3**
- 已有锁但 pid 已死（崩溃残留）→ 自动接管，不阻塞重启
- `run()` 用 `try/finally` 保证正常退出时删锁
- `--no-lock` 可显式关闭（调试/多机共享盘时慎用）
- `--lock-file PATH` 可把锁放到别处

### 一个 Windows 陷阱（已防）

`os.kill(pid, 0)` 在 Linux 是“探活”，在 **Windows 上会直接 TerminateProcess**——
拿它探活等于把对方杀掉。所以 `_pid_alive()` 在 Windows 走
`OpenProcess(QUERY_LIMITED_INFORMATION) + GetExitCodeProcess == 259`；
自检里专门有一条 `probe_does_not_kill` 钉住这个行为。

### 实测（`--self-test-lock`，零网络）

```
live_holder_blocks  另一个真进程持锁      -> 拒绝，返回 False
probe_does_not_kill 探活后对方仍 returncode=None
run_rc3             持锁时 run()           -> 退出码 3，不投任何片
stale_stolen        holder 被 kill 后      -> 接管成功，锁内 pid = 自己
no_lock_bypass      --no-lock              -> 直接放行
```

## 89. 提交幂等：响应丢了不等于再买一次（R657）

服务端早就支持 `X-Idempotency-Key`（`create_video` 里按 `(key_id, idem_key)` 回放），
但 **runner 从来没传过**。于是这个真实场景会双倍扣额度：

```
[runner] POST /v1/videos/generations  ----> [api] 创建任务 #1, 调上游, 已扣费
                                            |
                                     [proxy] 502 / 连接超时
[runner] SDK 重试 POST（无 idem）      ----> [api] 创建任务 #2, 再扣一次
```

任务 #1 变成孤儿：没人轮询、没人下载、额度照扣。

### 改动

- `Runner` 启动时生成 `run_id = uuid4()[:8]`
- 每支新片生成 `idem = "<run_id>-<seq:06d>"`，写进 ledger（`rec["idem"]`）
- 所有重试（SDK 内部 4 次 + runner 的 429 换 key 循环）**复用同一个 idem**
- 服务端回 `idempotent_replay: true` 时记 `rec["replay"]`，`C["idem_replay"] += 1`
- 心跳/summary 新增 `idem_replay`

### 实测（`--self-test-idem`，零网络、零额度）

stub 模拟“任务已创建但响应 502”：

```
lost_response   submit 第 1 次：建任务 #1 → 502；第 2 次：同 idem 回放
                -> rc=0 succeeded=1 errors=0
                   subs=1（只建了一个任务） req.submit=2
                   idem_replays=1  replay_count=1  两次 idem 完全相同
clean           正常一次提交 -> subs=1，idem_replays=0，idem 非空
```

反向对照：不传 `idem` → `subs=2`（两次扣费），`lost_response_no_double_buy=false`。

## 90. 开跑前一分钟自检：`runner.py --preflight`（R658）

`preflight_gate.sh` 只看号池和磁盘（走 admin 接口）。但真正决定“能不能开跑”的还有三件事：
**钥匙串里的 key 到底能不能用**、**outdir 能不能写**、**是不是已经有一个进程占着这个 outdir**。

```bash
./venv/bin/python runner.py --preflight \
  --keys-file /opt/wzmm/keys.txt --outdir /opt/wzmm/videos \
  --rate 300 --min-free-gb 4
```

### 检查项（全部零额度）

| 检查 | 方法 | 失败时 |
|---|---|---|
| 钥匙串 | `_resolve_keys` 解析 + 前 5 把 key 逐个 `GET /v1/models` | `key ...xxxxxx rejected: HTTP 401` |
| API 可达 | 同上，连接异常即停 | `api unreachable at ...` |
| outdir 可写 | 建 `.wzmm_write_probe` 再删 | `outdir not writable` |
| 磁盘余量 | `disk_usage(outdir).free` vs `--min-free-gb` | `free 3.9GB < min_free_gb 4.0GB` |
| 单产线锁 | 读 `runner.lock` + pid 存活判断 | `outdir locked by live pid 12345` |
| 参数 | `rate > 0`、`breaker-fails >= 0` | 对应提示 |

输出一行 JSON，`verdict=GO|NO-GO`，`spent_credits=0`。退出码 0/1 可直接给 systemd 或 shell 用。

### 实测（对真实 API，零额度）

```
$ runner.py --preflight --key sk-wz-kAWq... --outdir /tmp/x --rate 300
{"verdict":"GO","blockers":[],"check":{"keys":1,"keys_ok":1,"outdir_writable":true,"free_gb":53.94}}
rc=0

$ runner.py --preflight --key sk-wz-BOGUS ...
{"verdict":"NO-GO","blockers":["key ...-BOGUS rejected: HTTP 401"],"check":{"keys_ok":0}}
rc=1
```

离线自检 `--self-test-preflight` 4 条：好 key→GO、坏 key→NO-GO、outdir 不可写→NO-GO、被别人持锁→NO-GO。
反向对照：把 `blockers` 清空恒判 GO → 后三条全红。

## 91. 硬杀（SIGKILL/OOM）也不再丢片：提交即落盘（R659）

R645 处理的是 `SIGTERM`（优雅停止，会 drain 并写 ledger）。但 16G 机器上更常见的是 **OOM killer 直接 SIGKILL**：
`finally` 不执行、锁不释放、ledger 不写。

原来 `runner_state.json` 只在每个 stats tick（默认 30s）保存一次。于是存在一个窗口：

```
submit 成功（额度已扣） → 还没来得及 save → SIGKILL
→ 重启后 state 里没有 inflight → --resume 收养 0 条 → 这支片永远丢了
```

### 改法

`work()` 里 `note_inflight()` 之后立刻 `save()`（仅新提交，收养不重复写）：

```python
            if not adopted_tid:
                # R659: 在第一次轮询之前就把 tid 落盘
                self.save()
```

每支片一次原子写（tmp + `os.replace`），300/h 就是 12 秒一次，代价可以忽略。

### 实测（`--self-test-kill9`，真实子进程，零网络外呼）

子进程故意设 `--stats-every 600`（不会有周期性保存），在 stub 收到 POST 的瞬间被 `SIGKILL`：

```
submit_seen=true          rc_kill=1（Windows）/ -9（Linux）
inflight_persisted=1      ← 关键：硬杀前 tid 已在 state 里
lock_left_pid=37924       ← 锁文件残留（死 pid）
rc_resume=0  adopted=1  adopted_status=succeeded  served_from=server
subs_total=1              ← 全程只创建了一个任务，没有重复扣费
files=[t-stub01.mp4] 4102B  released=1
```

反向对照：删掉那句 `self.save()` → `inflight_persisted=0`，`adopted=0`，`pass=false`。

### 和 R656 / R657 的关系

- R656 的锁：死 pid 的残留锁会被自动接管，所以硬杀后能立刻重启
- R657 的幂等键：如果连 save 都没赶上（毫秒级窗口），重提也只会回放，不会双扣
