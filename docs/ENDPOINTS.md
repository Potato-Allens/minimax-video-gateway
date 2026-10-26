# WZ MiniMax Video API — 全量接口清单

生成时间：2026-10-20 03:08:05 CST　｜　覆盖实测：2026-10-20 04:05 CST（62 次调用 / 0 个 5xx，见 COVERAGE.md）　｜　共 **55** 条路径　｜　OpenAPI 版本：3.1.0

Base URL：`https://api.example.com`　｜　机器可读：`/openapi.json`　｜　交互文档：`/docs`

## 认证

- 业务面：`Authorization: Bearer sk-wz-...`（也支持 `?key=` 便于 wget/播放器）
- 管理面：`X-Admin-Token: <WZMM_ADMIN_KEY>`
- 页面类：`/healthz`、`/play`、`/sdk/wzmm.py`、`/openapi.json`、`/docs` 公开；
- 受保护页面：`/gallery`、`/library` 需要 `?key=sk-wz-...`（或 Bearer）；
- R696 只读浏览：`?browse=bw_<kid>_<exp>.<mac>`（`GET /admin/browse-token?ttl_h=168` 签发，可看全部 key 的片子，不能写）；
- R697 轮换：`POST /admin/browse-rotate` 让旧 kid 全部 401；`GET /admin/browse-audit` 查签发/作废记录；`/metrics` 需要 Bearer 或 `?key=`（Prometheus 抓取建议带 `?key=`）


## 管理面

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| POST | `/admin/alert-test` | X-Admin-Token | Admin Alert Test |
| POST | `/admin/alert` | X-Admin-Token | Admin Alert (custom key/msg/level) |
| GET | `/admin/alerts` | X-Admin-Token | Admin Alerts |
| POST | `/admin/apply-liveness` | X-Admin-Token | Admin Apply Liveness |
| GET,POST | `/admin/checkin` | X-Admin-Token | Admin Checkin Status |
| GET | `/admin/credits` | X-Admin-Token | Admin Credits |
| GET | `/admin/capacity` | X-Admin-Token | 产能跑道（今日可跑 / 到期投影） |
| GET | `/admin/health` | X-Admin-Token | Admin Health |
| GET,POST | `/admin/keys` | X-Admin-Token | Admin List Keys |
| GET | `/admin/keys/stale` | X-Admin-Token | 已禁用 key 清单（可删/有任务分开）|
| GET | `/admin/ui?token=` | 管理 token | 浏览器 Key 控制台（建 key / 改 rpm / 停用 / 硬删）|
| DELETE | `/admin/keys/{kid}` | X-Admin-Token | 默认禁用；`?hard=1` 真删（有任务则 409）|
| PATCH | `/admin/keys/{kid}` | X-Admin-Token | Admin Update Key (name/rpm/quota/max_concurrent/disabled) |
| POST | `/admin/library/gc` | X-Admin-Token | Library Gc |
| POST | `/admin/merge-tokens` | X-Admin-Token | Admin Merge Tokens |
| POST | `/admin/park` | X-Admin-Token | Admin Park |
| POST | `/admin/simulate-402` | X-Admin-Token | Drill 402 demotion (R704, restore=1) |
| GET | `/admin/capacity/plan` | X-Admin-Token | Retention TTL what-if (R704, read-only) |
| POST | `/v1/videos/{id}/release` | Bearer | 客户端已拿到文件 -> 立即删除本地副本（幂等） |
| GET | `/admin/plan` | X-Admin-Token | 量产规划：目标吞吐 -> 账号 / 磁盘 / key 建议 |
| GET | `/admin/runner` | X-Admin-Token | 常驻产线心跳：达成率 / 在途 / 磁盘护栏状态 |
| GET | `/admin/preflight` | X-Admin-Token | 开跑前体检：账号/盘/outdir/金丝雀 -> GO / NO-GO |
| GET | `/admin/preflight/matrix?rates=300,600` | X-Admin-Token | 多速率一起体检，返回 scenarios 表 |
| GET | `/admin/pool` | X-Admin-Token | Admin Pool |
| POST | `/admin/pin` | X-Admin-Token | 批量 pin/unpin（ids[] 或 batch_id） |
| GET | `/admin/retention` | X-Admin-Token | TTL 回收预告（24h/72h 条数+GB） |
| GET | `/admin/pins` | X-Admin-Token | 已永久保留清单 |
| GET | `/admin/gc/preview` | X-Admin-Token | janitor 下次会删什么（dry-run） |
| GET | `/admin/capacity` | X-Admin-Token | 磁盘 A/B/C 方案测算（只读） |
| GET | `/admin/orphans` | X-Admin-Token | 未登记的孤儿 mp4 清单 |
| POST | `/admin/orphans/import` | X-Admin-Token | 把孤儿登记进 tasks（只增不删） |
| GET | `/admin/public` | X-Admin-Token | 公网可达性探针最近结果（DNS/证书/nginx/防火墙级）|
| POST | `/admin/public/run` | X-Admin-Token | 立即跑一次公网探针（阻塞 <=90s）|
| GET | `/admin/thumbs` | X-Admin-Token | 缩略图缓存覆盖率 + 上次预热结果（只读）|
| POST | `/admin/thumbs/prewarm` | X-Admin-Token | 后台补齐缺失缩略图（202，workers 1-8）|
| POST | `/admin/reload-pool` | X-Admin-Token | Admin Reload Pool |
| POST | `/admin/revive` | X-Admin-Token | Admin Revive |
| GET | `/admin/stats` | X-Admin-Token | Admin Stats |
| GET | `/admin/summary` | X-Admin-Token | Admin Summary |
| GET | `/admin/tasks` | X-Admin-Token | Admin Tasks |
| GET | `/admin/token-age` | X-Admin-Token | Admin Token Age |
| POST | `/admin/unpark` | X-Admin-Token | Admin Unpark |
| GET | `/admin/usage` | X-Admin-Token | Admin Usage |
| GET | `/admin/verify` | X-Admin-Token | 最近一次按需验收结果（state / rc / summary） |
| POST | `/admin/verify` | X-Admin-Token | 后台触发 verify_all 全量验收（202，轮询 GET） |
| POST | `/admin/verify/report` | X-Admin-Token | 定时器回传验收结果（写 /metrics 的 wzmm_verify_*） |
| GET | `/admin/webhook` | X-Admin-Token | Admin Webhook Info |
| GET | `/admin/webhook/queue` | X-Admin-Token | Webhook 重投队列（tries / next_in_s / last） |
| POST | `/admin/webhook/rotate` | X-Admin-Token | Admin Webhook Rotate |

## 页面/其它

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| GET | `/gallery` | `?key=` 或 Bearer | Gallery Page |
| GET | `/healthz` | 公开 | Healthz |
| GET | `/library` | `?key=` 或 Bearer | Library Page |
| GET | `/metrics` | Bearer 或 `?key=` | Metrics |
| GET | `/play` | 公开 | Play |
| GET | `/sdk/wzmm.py` | 公开 | Sdk File |

## 片库

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| GET | `/v1/library` | Bearer key | Library List |
| GET | `/v1/library/manifest` | Bearer key | Library Manifest |
| GET | `/v1/library/stats` | Bearer key | Library Stats |
| GET,HEAD | `/v1/library/{fid}/content` | Bearer key | Library Content |
| GET | `/v1/library/{fid}/meta` | Bearer key | Library Meta |
| GET,HEAD | `/v1/library/{fid}/thumb` | Bearer key | Library Thumb |

## 配额/元信息

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| GET | `/v1/models` | Bearer key | Models |
| GET | `/v1/pricing` | Bearer key | Pricing |

## 工具

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| GET | `/v1/tools` | Bearer key | Tools List |
| POST | `/v1/tools/call` | Bearer key | Tools Call |

## 配额/元信息

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| GET | `/v1/usage` | Bearer key | My Usage |

## 视频

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| POST | `/v1/videos/batch` | Bearer key | Create Batch |
| GET | `/v1/videos/batch/{batch_id}` | Bearer key | Get Batch |
| POST | `/v1/videos/batch/{batch_id}/cancel` | Bearer key | Cancel Batch |
| POST | `/v1/videos/bulk` | Bearer key | Create Bulk |
| GET | `/v1/videos/archive` (`?batch_id&ids&pinned&limit`) | Bearer key | Zip 打包下载 |
| POST,GET | `/v1/videos/generations` (`?limit&status&cursor`) | Bearer key | List Videos |
| GET | `/v1/videos/generations/{tid}` | Bearer key | Get Video |
| POST | `/v1/videos/{tid}/cancel` | Bearer key | Cancel Task |
| POST | `/v1/videos/{tid}/pin` | Bearer key | 永久保留该片（GC 跳过） |
| DELETE | `/v1/videos/{tid}/pin` | Bearer key | 取消永久保留 |
| GET,HEAD | `/v1/videos/{tid}/content` | Bearer key | Video Content |
| GET | `/v1/videos/{tid}/events` | Bearer key | Video Events |
| GET | `/v1/videos/{tid}/meta` | Bearer key | Video Meta |
| GET | `/v1/videos/{tid}/status` | Bearer key | Video Status |
| GET,HEAD | `/v1/videos/{tid}/thumb` | Bearer key | Video Thumb |

## Webhook

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| GET,POST,DELETE | `/v1/webhook/secret` | Bearer key | Get Webhook Secret |
| POST | `/v1/webhook/test` | Bearer key | Webhook Self Test |

## 网页

| 方法 | 路径 | 认证 | 说明 |
|---|---|---|---|
| GET | `/play` | 公开 | 试玩页 |
| GET | `/gallery` | `?key=` / `?browse=` / admin | 新产线画廊（DB 分页） |
| GET | `/library` | `?key=` / `?browse=` / admin | 老农场片库（24,494） |
| GET | `/watch/{tid}` | `?key=` / `?browse=` / admin | R698 单片播放器（prev/next） |
| GET | `/watch/lib/{fid}` | `?key=` / `?browse=` / admin | R698 老农场播放器 |
| GET | `/v1/videos/archive` | `?key=` / `?browse=` / admin | 手选打包 zip（≤100 条 / 1.5GB）|
| GET | `/v1/library/archive` | `?key=` / `?browse=` / admin | 老农场手选打包 zip（R700）|
| POST | `/v1/archive/jobs` | `?key=` / `?browse=` / admin | 创建打包任务（R703）|
| GET | `/v1/archive/jobs/{id}` | 同上 | 任务进度 |
| GET | `/v1/archive/jobs/{id}/file` | 同上 | 下载 zip（Range 206）|
| DELETE | `/v1/archive/jobs/{id}` | 同上 | 删除产物 |
