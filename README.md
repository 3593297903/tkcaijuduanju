# 短剧采集与监测

`tkmonitor` 的采集、SQLite 历史、指标计算和 CSV／JSON／HTML 报表仅依赖 Python 标准库。完整日报的封面校验与转换另需可选的 Pillow，Excel 导出另需 Node 表格引擎。默认交付要求中文剧名和中文简介齐全；缺失或原文变化时生成增量翻译任务，由 Codex 或明确指定的翻译提供方补译、导入后继续交付。Python 采集器自身不调用大模型，也不编写剧情。

`legacy/v1/` 保留旧方法的只读归档；`legacy/v1/manifest.json` 记录原文件 SHA-256。原始业务数据、浏览器资料、Cookie、签名请求、SQLite 和报表只保存在被 Git 忽略的本机目录中。`config.example.json` 与测试数据可公开；`config.local.json`、`request-specs/` 和 `_local/` 不提交。

## 快速开始

新设备上的 Codex 请先完整执行[Windows／macOS 新设备安装、迁移与验收指令](docs/new-device-codex.md)。该指令会识别本机环境、复用或安装依赖、保留历史并完成实际验收；目标设备通过验收前，不视为该设备已适配。

需要 Python 3.11 或更新版本。安装核心无需 `pip`。在仓库目录运行：

```powershell
Copy-Item config.example.json config.local.json
python -X utf8 -m tkmonitor --config config.local.json import-history --source C:/path/to/history
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
- 当前 HTTP 来源提供前端精确整数，不提供页面显示值，因此日常 `collect --report` 和 Windows 入口默认使用 `frontend`。历史精确覆盖可能稀疏，例如已有历史某日为 344/384；报表用 `metric_coverage` 和“已知数量”明确标示，允许部分覆盖，缺失值不补齐。精确序列的初始、昨天及上次对比必须已有相应精确观测；没有旧精确点时只展示首次精确基线和本次值，不借用页面值计算涨幅。
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
python -X utf8 -m tkmonitor --config config.local.json collect --report --metric frontend
# 可选只采一个账号。
python -X utf8 -m tkmonitor --config config.local.json collect --account epiccreation111
```

同一批选择的账号全部验证通过后才事务提交。报表或 XLSX 在提交之后单独执行，导出失败不会回滚有效采集。缺少部分账号，或所选指标当日完全没有已知值时，报表会明确标注并返回退出码3；有效采集仍保留。`status` 中的 `recent_attempts` 区分 `running`、`failed`、`valid`。

## 新账号接入与请求恢复

新增 `onboard` 入口，已有配置直接复用原HTTP采集，没有配置时返回 `NEEDS_BROWSER` 和私有manifest（退出码4），由已授权的Codex Chrome运行时捕获正常首屏请求，再恢复全量采集：

```powershell
python -X utf8 -m tkmonitor --config config.local.json onboard --url https://www.tiktok.com/@ACCOUNT --name DISPLAY_NAME --dry-run
python -X utf8 -m tkmonitor --config config.local.json onboard --url https://www.tiktok.com/@ACCOUNT --name DISPLAY_NAME --report --metric frontend --finalize --xlsx
python -X utf8 -m tkmonitor --config config.local.json onboard --resume '_local/onboarding/实际任务ID.local.json'
```

默认保留用户原标签页，不刷新、不固定滚动；首屏不发目标请求时明确返回未完成。只有显式 `--allow-scroll-fallback` 才允许一次备用滚动。普通终端不能自行取得用户Chrome连接。单账号配置和报告独立，默认三账号名单不变。

Windows 可使用 `run_onboard.cmd` 自动读取同一份本机运行时配置，例如 `run_onboard.cmd --url https://www.tiktok.com/@ACCOUNT --name DISPLAY_NAME --report --finalize --xlsx`；恢复时只传 `--resume` 和实际manifest路径。

原 `collect` 默认行为不变；显式 `--recover-request` 可在401/403时准备一次整批恢复任务，保持全部选中账号校验后统一提交。网络、限流、字段变化等错误不反复打开Chrome。详细操作和边界见[接入与恢复说明](docs/onboarding.md)。

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

报表的 `account_coverage_complete` 表示账号快照齐全，`metric_coverage` 分别给出所选指标的已知条数、总条数及 complete／partial／missing 状态。`complete=true` 需要账号齐全且至少一个所选指标值已知；部分指标覆盖可以成功，但全部缺失会保留可核查文件、提示缺失并返回非零，始终不替换统计口径。

`workbook_configs.json` 保持旧导出器的行和摘要字段，使用 `rows`／`records`、`summary`、`report_day`、`previous_day`、`last_day`。`row.image` 是可选本机封面绝对路径；数据为空时指标为 `null`。独立 Node 集成位于 `integrations/export_workbooks.mjs`；Python 核心不直接生成 XLSX。

```powershell
$env:TKMONITOR_NODE = 'C:\path\to\node.exe'
$env:TKMONITOR_NODE_MODULES = 'C:\path\to\node_modules'
python -X utf8 -m tkmonitor --config config.local.json export-xlsx --day 2026-10-28 --metric display
# 也可 collect --report --metric frontend --xlsx
```

Node 环境需提供 `@oai/artifact-tool` 及其运行依赖；该引擎需由运行环境提供，仓库未包含其安装包。`TKMONITOR_NODE_MODULES` 指向包含该包的目录；未设置时使用 Node 正常模块解析。Python 为每个账号单独启动进程，采用 `--no-opt`、384 MB V8 堆等已经用于本机日报的参数，避免多个工作簿累计占用内存。每个账号日志保存在报告目录的 `PREFIX_xlsx.stdout.log`／`PREFIX_xlsx.stderr.log`。导出后重新读取 XLSX，逐条核对 ID、标题、播放量、增量、涨幅、合计和图片数量。集成未安装或运行失败返回非零，已有 CSV／JSON／HTML 和数据库仍保留。

## 完整日报、资料继承与核验打包

最近使用的日报收尾步骤已纳入正式命令，不再需要仓库外的日期脚本或固定输出目录：

```powershell
# 完整日报的额外图片依赖；仅采集数字不需要安装。
python -m pip install Pillow
# 全量采集 → 封面补齐及简介来源标记 → HTML/CSV/JSON → 各账号 Excel → 核验。
python -X utf8 -m tkmonitor --config config.local.json collect --report --metric frontend --finalize --xlsx
# 可以追加 --delivery 路径，把核验通过的整套文件复制到指定目录。
python -X utf8 -m tkmonitor --config config.local.json collect --finalize --xlsx --delivery _local/delivery/today
```

封面只使用采集时保存的页面缩略图 URL，保留 HTTPS/TLS 校验、TikTok CDN 域名限制、同主机跳转限制、8 MB 大小限制和图片解码检查。优先复用本机已有图片；需要下载时最多四个并发、每张最多三次请求，每次超时十二秒。签名 URL 和原始错误消息不进入完成日志；图片转为 Excel 可用的 PNG。该图片下载器与已验证的日常方法一致，不采用 shell 中的代理环境变量。

中文资料按账号、剧目 ID 和确切原文复用；英文剧名或简介变化后，对应旧译文失效。默认 `report`、带报告的采集、`finalize`、`export-xlsx` 和 `package` 在缺中文时返回 `NEEDS_TRANSLATION`（退出码 5），不把“待翻译”当作完整交付。有效采集、播放量、初始值和统计时间仍然保留。只需要数字或英文原文时，才显式追加 `--allow-untranslated`。历史中文参考不算本次原文已核对的翻译。

补译流程见 [中文资料交付说明](docs/translations.md)。采集命令遇到退出码 5 后，Codex 应读取返回的私有任务，忠实翻译每条原文并导入，再继续导出；不要为补译重新采集。普通终端没有独立的 AI 翻译服务时，需要有人或外部提供方填好任务，不能声称已自动完成翻译。

`--finalize` 核查账号和整数覆盖、每条封面、英文简介、所有数值和快照指纹，输出 `media_validation.json`。数值或封面、原文不齐时返回非零，保留可检查的报表与已提交的采集数据。暂时无法联网下载图片、或只需重做 Excel 时，可单独重跑，不会新增一次采集：

```powershell
python -X utf8 -m tkmonitor --config config.local.json finalize --day YYYY-MM-DD --metric frontend
# 只使用本机已有图片。
python -X utf8 -m tkmonitor --config config.local.json finalize --day YYYY-MM-DD --no-download
python -X utf8 -m tkmonitor --config config.local.json export-xlsx --day YYYY-MM-DD --metric frontend
python -X utf8 -m tkmonitor --config config.local.json package --day YYYY-MM-DD --output _local/delivery/selected-day
```

`package` 在复制前校验整个批次，要求完整指标和通过资料核验的相同快照，以及与 `merged.json` 一致的工作簿配置；重新读取每个保存后的工作簿核对数值及图片数量。成功后输出 `workbooks_verified.json`，复制 HTML、JSON、三个账号各自的 CSV／XLSX（账号数量可配置）及核验记录。无 `--output` 时只在原报告目录核验，不额外复制。此核验不代表重新请求了在线接口；分页和原始整数由采集阶段验证。

大 HTML 使用流式 JSON 写入同目录临时文件，完成后原子替换旧网页。写入中断不会把已经生成的网页清空。

## Windows 每日入口与可选计划任务

`run_daily.cmd` 从自身目录执行 `collect --report --metric frontend --finalize --xlsx`，包括上述完整日报收尾，不写死 Codex 或 Python 安装路径。若存在本机私有的`runtime.local.cmd`，先加载其中环境设置。可通过 `TKMONITOR_PYTHON` 指定 Python，通过 `TKMONITOR_CONFIG` 指定配置；默认分别为 `python`、仓库中的 `config.local.json`。完整入口需要 Pillow 和 Node 表格引擎；只采集数字或只采单个账号时使用上面的独立 `collect` 命令。可以追加 `--day`、`--delivery` 等选项。`runtime.local.cmd`已被Git忽略，本机路径不会进入公共仓库。

macOS 使用 `sh ./run_daily.sh`、`sh ./run_onboard.sh --url https://www.tiktok.com/@ACCOUNT`。两个入口读取项目内的私有 `runtime.local.sh`；未指定 Python 时优先使用项目 `.venv/bin/python`，否则使用 `python3`。入口会转交全部参数和退出码。跨设备应重新发现运行时及 Chrome 能力，不能复制 Windows 的虚拟环境或二进制到 Mac 后宣称可用。

先完成一次真正在线采集并检查报告，再按需安装任务。下面只是手动可选命令，仓库不会自行安装计划任务。浏览器模式需要对应用户已登录且浏览器辅助通道可用：

```powershell
schtasks /Create /TN "TikTokDramaMonitor" /SC DAILY /ST 16:30 /TR "C:\path\to\tkcaijuduanju\run_daily.cmd" /IT
```

任务使用系统环境或`runtime.local.cmd`中的 Python/Node 设置，临时 PowerShell 环境变量不会自动传给未来的计划任务。退出码：`0` 完成，`2` 配置／采集／导出失败，`3` 报告缺少部分账号或所选指标全部缺失，`4` 接入或恢复需要已授权浏览器动作，`5` 中文待补译、有效采集已保留，`130` 用户中断。失败信息提供操作方向，不打印请求参数、Cookie 或授权头。

## 验证

```powershell
python -X utf8 -m unittest discover -s tests -v
```

测试只使用模拟数据，覆盖不可变和重复迁移、事务回滚、缺日与零、同日多次快照、未来基线隔离、双指标隔离、配对分母、分页／认证／非 JSON／重试、陈旧 checkpoint、CLI 退出码及导出失败后的有效采集保留。HTTP transport 可注入，离线测试不调用 TikTok。

已有历史的本机验收结果写入被忽略的 `_local/integration_result.json`。历史回放和离线测试通过不等于在线 HTTP 或 Chrome 已稳定；每次真实在线能力以单独保留的验证结果为准。

2026-10-29已完成真实验证：三个账号均可由独立HTTP程序全量采集，最新213／70／104部，共387部，均取得接口整数；关闭参数准备页后复测成功，一键入口也已通过。详细结果见[验证记录](docs/verification-2026-10-29.md)。请求参数的跨日有效期仍需后续观察，失效时使用[Chrome请求更新模块](docs/browser-session.md)，不是重新逐剧点击采集。

新增剧或原文无法与历史译文核对时，中文资料明确标记待补，不自动编写。新封面URL已保存，尚未下载的图片显示待采集；发布时间也不推算。默认采集、计算和报表不调用大模型。

可选Node验证（已配置`TKMONITOR_NODE_MODULES`并安装表格引擎时运行）：

```powershell
node tests/test_chrome_request.mjs
node --v8-pool-size=1 tests/test_workbook_export.mjs
```

第二项使用真实表格引擎校验部分指标缺失时的配对分母，不生成额外XLSX。测试需要允许启动本机Node子进程；受限沙箱可能需要额外的本地进程权限。
