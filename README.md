# 短剧采集与监测

`tkmonitor` 是仅依赖 Python 标准库的本机监测工具。采集来源、SQLite 历史、指标计算和报表导出相互独立。核心不调用大模型，不翻译或编写剧情。新增资料或发生变化的原文需要翻译时，报表明确显示“待翻译／待补充”。

`legacy/v1/` 保留旧方法的只读归档；`legacy/v1/manifest.json` 记录原文件 SHA-256。原始业务数据、浏览器资料、Cookie、签名请求、SQLite 和报表只保存在被 Git 忽略的本机目录中。`config.example.json` 与测试数据可公开；`config.local.json`、`request-specs/` 和 `_local/` 不提交。

## 快速开始

需要 Python 3.11 或更新版本。安装核心无需 `pip`。在仓库目录运行：

```powershell
Copy-Item config.example.json config.local.json
python -X utf8 -m tkmonitor --config config.local.json import-history --source E:/jianc/outputs/monitoring_2026-10-28
python -X utf8 -m tkmonitor --config config.local.json report --day 2026-10-28 --metric display
python -X utf8 -m tkmonitor --config config.local.json status
```

上面的日期和来源目录仅用于迁移已有历史；运行日期不写在程序中。`report`、`collect` 和 `export-xlsx` 省略 `--day` 时采用当前北京时间日期。报告必须明确选择 `--metric display` 或 `--metric frontend`。没有该日有效快照时返回非零退出码，不使用旧数据冒充当天结果。

配置中的三个账号为 Epic Creation (`epiccreation111`)、VerveDrama (`vervedramaeg`)、Baby Drama-ca (`babydrama_ca`)。可增删账号；剧目数量和日期均由数据决定。`state_dir`、`report_dir`、请求配置和 checkpoint 的相对路径均相对于配置文件解析。默认状态位于 `_local/state`，默认报告位于 `_local/state/reports/YYYY-MM-DD/METRIC`。

## 历史与统计口径

`import-history` 读取 `history_snapshots.json`、`initial_baselines.json`，以及可选的 `workbook_configs.json`。它验证每条初始值与原历史中最早观察一致，复制已有本机封面到状态目录的 `assets/`，并按文件内容散列保存原始文件副本。原目录不变。同一快照 ID 重复导入没有副作用；内容改变则拒绝整个事务，不能覆盖旧快照或重置初始值。

- 身份键是账号加 19 位剧目 ID，ID 始终作为字符串保存。
- 时间保存实际 UTC 时间；统计日按 `Asia/Shanghai`（UTC+8）计算。同账号同日取最后有效快照。
- `display` 仅使用当时页面显示的缩写折算整数；无法恢复历史被省略的低位。`frontend` 仅使用保存的 `frontendInteger`、`numWatched` 或明确映射的前端整数。两条序列不会互相补值。
- 每剧初始页面值固定保留；首次已知前端整数及其时间另列。历史报告只使用截至该快照已知的基线和资料，不泄露未来精确值。
- “昨天”固定指日历前一天。没有当天快照或某剧未出现时留空；“上次实际”单列该剧最近一次更早观察，可能是同日更早快照。按真实间隔小时另算折合 24 小时增量和线性涨幅。
- 缺失为 `null`／CSV 空单元格／HTML 的 `—`，真实零保留 `0`，负变化保留负值。新增剧昨天、日增量和日涨幅留空；零分母的涨幅留空。
- 比较合计仅包含两端都有对应指标的剧目，涨幅以同批剧目原值为分母。摘要同时显示已知数量和可比数量。
- 视频时间只来自原始来源字段，不根据 ID 或初次采集日期推算发布时间。资料原文变化会使对应旧翻译失效，等待人工更新。

## HTTP 采集

HTTP 需要用户已有授权浏览器实际发出的普通请求配置。没有私有配置时会明确失败。本工具不逆向签名、不绕过验证、不从旧 checkpoint 自动降级伪造新结果。

在本机 `request-specs/HANDLE.local.json` 保存请求，URL 和 headers 不进入日志：

```json
{
  "url": "https://www.tiktok.com/api/YOUR_CAPTURED_ENDPOINT?cursor=0",
  "headers": {"Accept": "application/json"},
  "cursor_param": "cursor",
  "initial_cursor": "0",
  "max_pages": 100,
  "timeout_seconds": 30,
  "retries": 2,
  "max_backoff_seconds": 30,
  "fields": {"items": "dramaList", "has_more": "hasMore", "cursor": "cursor", "status": "status_code"},
  "record_fields": {"id": "dramaID", "title": "dramaName", "frontend_integer": "numWatched"}
}
```

示例端点是占位符，实际 URL、签名和必要授权头必须取自本机请求。字段映射支持点分隔的嵌套路径。API 没有明确页面折算值时，`display` 留空；不会将精确整数四舍五入为页面值。可映射 `display_views`、`views_raw`；原字段为 `viewsRaw` 时自动读取。

只允许 HTTPS 的 `tiktok.com` 或其子域，阻止私有网络解析地址、Host 覆盖和跨主机重定向。每页检查状态、JSON、游标前进、唯一 ID 和末页 `hasMore=false`，并设置最大页数。超时、网络错误、429 和 5xx 有限重试；401/403、登录／验证 HTML、游标不前进、重复 ID、空结果或中途失败均不提交快照。失败响应保留在 `_local/state/raw/attempts/`，CLI 只输出固定的操作提示。

```powershell
python -X utf8 -m tkmonitor --config config.local.json collect --report --metric display
# 可选只采一个账号。
python -X utf8 -m tkmonitor --config config.local.json collect --account epiccreation111
```

同一批选择的账号全部验证通过后才事务提交。报表或 XLSX 在提交之后单独执行，导出失败不会回滚有效采集。缺少部分账号的报表明确标注，退出码为3。`status` 中的 `recent_attempts` 区分 `running`、`failed`、`valid`。

## 浏览器与 checkpoint 接口

Chrome 辅助采集由独立集成运行，读取用户已授权页面的原始响应，不依赖核心内置浏览器库。可将账号的 `source` 配置为：

```json
{
  "type": "browser",
  "path": "_local/state/checkpoints/epiccreation111.json",
  "command": ["python", "YOUR_BROWSER_HELPER.py", "--account", "{account}", "--output", "{output}", "--day", "{day}"],
  "timeout_seconds": 1800
}
```

命令必须是 argv 数组，通过 `{account}`、`{output}`、`{day}` 接收参数。核心检查采集开始及完成时间均不早于本次命令开始，且属于指定北京时间日。旧文件即使遗留在输出路径也不能通过。子进程输出只写入本机私有日志；命令失败会返回非零并保留检查材料。

手工接入本日已完成文件时可用 `{"type":"checkpoint","path":"..."}`，它也检查账号、日期、ID、指标和完整性。新适配器推荐输出：

```json
{
  "schema": "tkmonitor.snapshot/v1",
  "account": "demo",
  "captured_at": "2020-01-01T01:02:03Z",
  "collection_started_at": "2020-01-01T01:00:00Z",
  "source": "authorized browser response checkpoint",
  "complete": true,
  "has_more": false,
  "records": [{"id":"0000000000000000001","title":"Synthetic","display_views":1200,"frontend_integer":1234,"views_raw":"1.2K","metadata":{}}]
}
```

上述时间与 ID 仅为脱敏结构示例，不能作为当天有效采集。也兼容旧 `{run:{account,start_at,last_read_at,pages:[{observed_at,response}]},cards:[...]}` 格式，逐页重新验证，分别读取可见卡片数字与 API 前端整数。核心不会仅凭 `complete` 推断旧分页完整。

## 报表与可选 XLSX

每次 `report` 生成各账号的完整数字 CSV、`merged.json`、`monitor.html` 和 `workbook_configs.json`。HTML 无网络依赖，可直接双击打开；可切换历史日期、账号、统计口径、搜索和排序。英文标题先显示、中文名随后；94px 封面来自本机嵌入，简介可展开。未采日期明确提示缺失。`--no-covers` 可减小 HTML 体积。

`workbook_configs.json` 保持旧导出器的行和摘要字段，使用 `rows`／`records`、`summary`、`report_day`、`previous_day`、`last_day`。`row.image` 是可选本机封面绝对路径；数据为空时指标为 `null`。独立 Node 集成位于 `integrations/export_workbooks.mjs`；Python 核心不直接生成 XLSX。

```powershell
$env:TKMONITOR_NODE = 'C:\path\to\node.exe'
$env:TKMONITOR_NODE_MODULES = 'C:\path\to\node_modules'
python -X utf8 -m tkmonitor --config config.local.json export-xlsx --day 2026-10-28 --metric display
# 也可 collect --report --metric display --xlsx
```

Node 环境需提供 `@oai/artifact-tool` 及其运行依赖。`TKMONITOR_NODE_MODULES` 指向包含该包的目录；未设置时使用 Node 正常模块解析。Python 会为导出器设置适合本机内存限制的 Node 运行参数；导出日志保存在报告目录。集成未安装或运行失败返回非零，已有 CSV／JSON／HTML 和数据库仍保留。

## Windows 每日入口与可选计划任务

`run_daily.cmd` 从自身目录执行 `collect --report --metric display`，不写死 Codex 或 Python 安装路径。可通过 `TKMONITOR_PYTHON` 指定 Python，通过 `TKMONITOR_CONFIG` 指定配置；默认分别为 `python`、仓库中的 `config.local.json`。可以追加 `--xlsx` 等采集选项。

先完成一次真正在线采集并检查报告，再按需安装任务。下面只是手动可选命令，仓库不会自行安装计划任务。浏览器模式需要对应用户已登录且浏览器辅助通道可用：

```powershell
schtasks /Create /TN "TikTokDramaMonitor" /SC DAILY /ST 16:30 /TR "E:\jianc\tkcaijuduanju\run_daily.cmd" /IT
```

任务使用系统环境中的 Python/Node 设置，临时 PowerShell 环境变量不会自动传给未来的计划任务。退出码：`0` 完成，`2` 配置／采集／导出失败，`3` 报告缺少部分账号，`130` 用户中断。失败信息提供操作方向，不打印请求参数、Cookie 或授权头。

## 验证

```powershell
python -X utf8 -m unittest discover -v
```

测试只使用模拟数据，覆盖不可变和重复迁移、事务回滚、缺日与零、同日多次快照、未来基线隔离、双指标隔离、配对分母、分页／认证／非 JSON／重试、陈旧 checkpoint、CLI 退出码及导出失败后的有效采集保留。HTTP transport 可注入，离线测试不调用 TikTok。

已有历史的本机验收结果写入被忽略的 `_local/integration_result.json`。历史回放和离线测试通过不等于在线 HTTP 或 Chrome 已稳定；每次真实在线能力以单独保留的验证结果为准。
