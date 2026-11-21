# 交给新设备 Codex 的完整执行指令：Windows 与 macOS

请完整读取本文件并实际执行，目标是在当前新设备上安装或复用环境，让短剧监测项目能够正常采集和交付。不要只解释步骤或输出计划；完成不依赖用户输入的工作，遇到登录、缺少迁移文件或工具能力等真实阻塞时再清楚说明。

仓库：https://github.com/3593297903/tkcaijuduanju.git

运行分支：`feature/reusable-monitor`。`main` 保留旧版，不能把默认分支当作当前监测版本。已知功能基线提交为 `25aca3c7a0b432a4478806456f0f920587d6b18e`；本指令和跨平台入口在此提交之后，部署时读取上述分支实际最新提交并记录 SHA。

适用设备：Windows 与 macOS（分别识别 x64／ARM64、Intel／Apple Silicon）。一台设备的测试结果不能证明另一种系统或架构已经通过。

## 一、目标与授权范围

本任务允许你在当前项目目录安装所需项目依赖，检查并在缺失时安装官方 Git、Python、Node、Chrome，连接用户自己的 Chrome，采集当前北京时间日期的数据并生成原文报告。已有合适环境优先复用，减少重复安装。需要管理员权限、登录或浏览器工具授权时，按当前平台的真实权限流程处理。

保持以下项目规则：

- 正式采集沿用 Python `urllib` 请求正常授权的 JSON 接口、游标分页、19位字符串 ID、整数和完整性校验。不要替换成逐剧截图识别、Scrapling、抓视频文件或新建一套采集器。
- 默认监测范围仅为 Epic Creation `epiccreation111`、VerveDrama `vervedramaeg`、Baby Drama-ca `babydrama_ca`。其他已有账号配置继续保留，单独使用，不自动加入默认批次。
- 保留原全部历史、初始值、中文资料和封面；未知的昨天数据保持空值。发布时间只能使用真实来源字段，不能由 ID 或初见日期推算。
- 原文报表直接使用平台剧名和简介。用户明确要中文版时再用 `--translate`，复用原文一致的旧译文并按需补译。
- 请求文件、私有配置、数据库、报表、迁移包、环境变量和浏览器凭据只放本机被 Git 忽略的位置。不要输出签名 URL、Cookie、Authorization 或读取无关服务的密钥。
- 优先使用自己创建的临时 Chrome 页面，保留用户原标签页，默认不刷新用户页面、不滚动；不得全局关闭 TLS 校验、关闭浏览器安全设置或覆盖用户 Chrome 资料目录。
- 不修改 `legacy/v1/`，不重置 Git 历史、不覆盖未提交工作、不主动安装定时任务。仅为当前设备部署所需的配置和小范围兼容修复可以继续完成；修改采集口径或新增翻译收费服务不在本次范围。

## 二、先检查设备和仓库

1. 读取当前工作区适用的 `AGENTS.md` 和仓库 README、`docs/onboarding.md`、`docs/translations.md`、`docs/browser-session.md`，遵循实际工具文档。
2. 记录操作系统版本、CPU 架构、当前目录、可用磁盘、当前北京时间日期，以及 Git、Python、Node、Chrome 是否可用。检查必须执行真实程序，不能仅凭 PATH 中存在同名文件判断。例如 Windows 的 `python3.exe` 可能只是商店别名。
3. 如果存在 Codex 的 `load_workspace_dependencies` 工具，先调用它取得这台设备的真实 Python／Node／表格依赖路径，再执行验证。不能照抄旧 Windows 机器的 `C:\Users\Administrator\...` 或 `E:\jianc`。
4. 选择当前用户可写且便于使用的项目目录；路径允许中文和空格。使用相对配置路径或当前设备的真实绝对路径。
5. 没有仓库时克隆：

```text
git clone --branch feature/reusable-monitor --single-branch https://github.com/3593297903/tkcaijuduanju.git tkcaijuduanju
```

6. 已有目录时先检查 `git status`、分支和 remote。干净且指向本仓库的正确分支才 `git fetch origin`、`git pull --ff-only`；有未提交内容或其他项目时，保留原目录，另建独立目录再克隆。不要直接覆盖本机配置。
7. 记录实际 `git rev-parse HEAD`，检查 `run_daily.sh`／`run_onboard.sh` 和 `collect --help` 中的原文默认及 `--translate` 选项。可以用 `git merge-base --is-ancestor 25aca3c7a0b432a4478806456f0f920587d6b18e HEAD` 核对跨平台入口的历史基线。

## 三、环境检查、缺失安装与持久化

### 3.1 依赖要求

| 项目 | 用途与要求 |
|---|---|
| Python | 3.11及以上；需能导入 `sqlite3`、`ssl`、`venv`。优先使用已提供且验证通过的 Codex 运行时或合适的本机 Python。 |
| Pillow | 完整封面处理所需；用最终选定的 Python 验证 `from PIL import Image`。 |
| Node | 浏览器适配器测试和现有 Excel 导出所需；优先使用与表格包配套的 Codex Node，或经过本机验证的官方 LTS。 |
| `@oai/artifact-tool` 及运行依赖 | 当前 Excel 导出引擎，仓库未包含安装包；必须实际找到并验证，不能仅安装 Node 就声称 Excel 可用。 |
| Git | 获取、核对代码；不需要 GitHub 账户登录即可克隆此公开仓库。 |
| Chrome 与受支持的浏览器工具 | 首次建立或更新请求时使用用户自己的 Chrome；浏览器已安装不代表 Codex 已能控制它。 |

不要因为缺少 `sqlite3` 就执行 `pip install sqlite3`；应选用自带此模块的正确 Python。核心采集没有需要 `pip install -e .` 的打包配置，也不需要安装 Playwright 浏览器。

### 3.2 Windows

- 检查 `Get-Command`、`where.exe`、实际 `--version` 和 `python -c "import sys; print(sys.executable)"`，识别最终执行文件。
- 缺依赖时优先使用已有 `winget` 或官方安装程序。先 `winget search`／`winget show` 核实包 ID、发布者、适用架构和版本，再按 `winget install --id ACTUAL_ID --exact --source winget` 安装。不要把占位 `ACTUAL_ID` 原样执行。安装参数见 [WinGet 官方说明](https://learn.microsoft.com/en-us/windows/package-manager/winget/install)。
- 若 `winget` 不存在，使用 Python、Node、Git、Chrome 官方下载来源，检查安装完成后的真实路径。无需为运行本项目重装已有可用环境。
- 若选择独立 Python，则在项目内新建 `.venv`，通过 `.venv\Scripts\python.exe` 直接运行，无需调整整个系统的 PowerShell 执行策略：

```powershell
# BASE_PYTHON 要替换为已验证的真实解释器路径。
& 'BASE_PYTHON' -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install Pillow
& '.\.venv\Scripts\python.exe' -c "import sys, sqlite3, ssl; from PIL import Image; print(sys.version); print(sys.executable); print(sqlite3.sqlite_version)"
```

已经具备 Pillow 的合适 Codex Python 可以直接复用。不要重复创建同名虚拟环境覆盖已有内容。

### 3.3 macOS

- 用 `sw_vers`、`uname -m`、`command -v`、真实 `--version` 检查。优先使用原生架构；不得将 Windows 的 Python、Node、`.venv` 或 `node_modules` 直接复制到 Mac 使用。
- 已有 Homebrew 时按实际 `brew info` 结果安装缺少的 Git、Python、Node／Chrome。没有 Homebrew 时不强制安装，可以使用各项目官方安装程序。需要 Homebrew 时先读取其当前[官方安装说明](https://docs.brew.sh/Installation)，安装完成后按实际输出配置 PATH，不假定 Intel 和 Apple Silicon 的安装目录相同。
- 不修改 macOS 系统 Python。独立环境示例：

```sh
# BASE_PYTHON 要替换为已验证的真实解释器路径。
"BASE_PYTHON" -m venv .venv
./.venv/bin/python -m pip install Pillow
./.venv/bin/python -c 'import sys, sqlite3, ssl; from PIL import Image; print(sys.version); print(sys.executable); print(sqlite3.sqlite_version)'
```

### 3.4 Excel 引擎和本机运行时配置

先通过 Codex 依赖工具提供的位置或已明确安装的目录查找 `@oai/artifact-tool`。不要假定这个包可以从公共 npm 安装，不要扫描无关凭据尝试下载，不要混用另一系统或 CPU 架构的运行依赖。

确定并验证以下变量：

- `TKMONITOR_PYTHON`：能运行本项目并导入 Pillow 的 Python 绝对路径。
- `TKMONITOR_NODE`：选定 Node 的绝对路径。
- `TKMONITOR_NODE_MODULES`：包含 `@oai/artifact-tool` 的真实 Node 模块根目录。
- `TKMONITOR_CONFIG`：当前设备主配置的绝对路径。

Windows 写入私有 `runtime.local.cmd`，使用 `set "NAME=value"`。macOS 写入私有 `runtime.local.sh`，使用正确 shell 引号的 `export NAME='value'`。这些文件只保存上述路径，不放平台登录密钥。

例如 macOS 格式如下，所有占位路径必须替换为本机验证值：

```sh
export TKMONITOR_PYTHON='/实际项目路径/.venv/bin/python'
export TKMONITOR_NODE='/实际Node路径/node'
export TKMONITOR_NODE_MODULES='/实际模块目录/node_modules'
export TKMONITOR_CONFIG='/实际项目路径/config.local.json'
```

生成路径配置后，启动一个新的 shell／子进程重新加载该文件并验证，不能只验证临时会话里的环境变量。不要用 `eval` 执行不可信安装输出。

若确实无法取得表格运行时，继续完成核心、历史与网页的配置和测试，记录 `EXCEL_RUNTIME_UNAVAILABLE`。不得把 Excel 标记已通过，也不能默默取消 Excel 要求或擅自重写导出器。最终明确报告此阻塞及需要的运行时；只有实际保存并核验 XLSX 后，完整交付才算通过。

## 四、迁移已有数据，或建立首次监测

GitHub 只有代码、示例配置和合成测试数据。旧设备的监测数据库、初始值、中文、封面、原始响应、报表和私有请求没有上传。

先在当前工作区检查用户已提供的迁移目录或迁移包，不扫描无关磁盘。如未提供，只需一次说明需要的文件，并继续完成环境及离线测试；历史迁移标记未完成，不能编造旧播放量。用户明确选择从新设备第一次监测时，记录为新基线，昨天留空。

### 4.1 推荐迁移内容

| 必须保留的内容 | 操作 |
|---|---|
| `config.local.json` 和其他已有 `config.*.local.json` | 保留账号、额外字段和单账号范围，调整设备路径。 |
| 实际 `state_dir` 下的 `monitor.sqlite3` | 保留全库历史、初始值及中文资料；目标已有数据库时先备份，不盲目覆盖。 |
| 同一 `state_dir` 下的 `assets/`、`raw/`、`reports/`、`account-reports/` 等资料 | 保留图片、原始证据和历史报表；按实际文件存在情况处理。 |
| 配置实际引用的 `request-specs/*.local.json` 等请求文件 | 私下迁移以供复用；在新设备上实际请求验证，不承诺原授权必定有效。 |

源设备暂停采集并关闭写入后复制，或者使用 SQLite `Connection.backup()` 生成一致的数据库备份，参见[官方备份接口](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup)。活跃数据库可能有 WAL 等伴随状态，不要只复制一个正在写入的主文件就认定完整。

不要复制旧 `.venv`、Node 二进制、`node_modules`、Chrome 整个用户目录或旧 `runtime.local.cmd` 到另一平台执行。旧 onboarding manifest 含设备绝对路径，归档保留；新设备重新创建任务，不直接 `--resume` 旧 manifest 或手改其中状态。

### 4.2 路径与数据核验

1. 读取主配置和所有已有单账号配置；把 `state_dir`、`report_dir`、`source.request_spec` 的 Windows 盘符或旧设备绝对路径改为正确的相对路径或目标设备真实路径。不能全局替换数据库中的原始来源证据。
2. 保持默认三账号配置一致。已有单账号配置共享原历史库时继续共享，不能给新设备悄悄新建空库替代。
3. 首次用项目 `Store` 打开迁移库之前，先使用 SQLite **只读模式**验证 `PRAGMA quick_check`，记录全部快照 ID＋fingerprint、初始值表全部行、各账号日期范围、资料和封面数。将基准及迁移文件校验摘要存入 `_local/new-device/` 的私有验收目录。
4. 数值保持整数，剧目 ID 保持字符串。重生成报表以更新图片绝对路径；不要直接使用旧设备生成的 `workbook_configs.json` 中的绝对图片路径导出。
5. 若只有项目支持的历史 JSON／旧报告结构而没有 SQLite，按 `import-history --source 实际目录` 导入并验证；不要既恢复 SQLite 又重复导入另一套冲突的初始值。
6. 尚未提供历史时，创建新主配置前确认不存在待恢复的库，再从 `config.example.json` 复制；保留已有私有配置，不覆盖。

## 五、先完成离线验证

所有命令中的 Python／Node 都替换为已记录的本机解释器，使用匹配当前 shell 的引号。不要把文档中的 `PY`、`NODE`、日期和路径占位符直接当作真实值执行。

执行并保存日志：

```text
PY -X utf8 -m tkmonitor --help
PY -X utf8 -m unittest discover -s tests -v
NODE tests/test_chrome_request.mjs
NODE --v8-pool-size=1 tests/test_workbook_export.mjs
```

已知基线为105项 Python 测试、14项浏览器适配测试；部署新提交时以实际测试数量和退出码为准，不能因数量变化就跳过新测试。最后一项依赖真实表格引擎，不能以 mock 结果替代。

macOS 还执行 `sh -n run_daily.sh`、`sh -n run_onboard.sh`。两平台均验证从带空格的路径调用入口时，配置路径、额外参数和非零退出码能够保留。入口帮助检查即可，不要为测试重复采集。

测试数据要放独立目录，不得导入生产历史库。安装及离线通过分别记录，不能据此声称已完成在线采集。

## 六、Chrome、首次请求与在线采集

### 6.1 判断如何进入正式采集

先判断私有请求是否存在。请求齐全时，优先尝试原三账号的正常整批采集；需要恢复时使用现有 `--recover-request`，不要每天无条件打开、刷新或滚动 Chrome。

```text
PY -X utf8 -m tkmonitor --config config.local.json collect --report --metric frontend --finalize --xlsx --recover-request
```

保存命令输出、退出码和本机 manifest 路径，按下面规则继续。

如果是新配置且请求文件缺失，不要反复用整批 `collect` 重试。使用现有 `onboard` 按默认配置中的三个账号逐个建立请求并采集：

```text
PY -X utf8 -m tkmonitor --config config.local.json onboard --url https://www.tiktok.com/@epiccreation111
PY -X utf8 -m tkmonitor --config config.local.json onboard --url https://www.tiktok.com/@vervedramaeg
PY -X utf8 -m tkmonitor --config config.local.json onboard --url https://www.tiktok.com/@babydrama_ca
```

先核对单账号配置是否共享正确的 `state_dir`，再逐个完成捕获和恢复。三个账号的有效快照都保存到共享库后，直接生成总报告。

### 6.2 处理需要浏览器的任务

1. 退出码4／`NEEDS_BROWSER` 表示请求准备未完成。发现当前 Codex 可用浏览器工具，按工具要求初始化，并读取浏览器、Tab 和 CDP 能力文档。
2. 连接新设备上用户自己的 Chrome。登录或验证需要用户操作时，只让用户完成该具体步骤；不要要求复制 Cookie 或手工提供签名。
3. 按 `docs/onboarding.md`，在**受支持的已授权浏览器运行时**加载项目 `integrations/chrome_capture.mjs`，调用 `captureOnboarding`。使用本机文件 URL 和本次新生成的 manifest。Windows 与 Mac 的 file URL、中文和空格要正确编码。
4. 捕获器依赖调用方提供的临时 Tab 工厂；普通 Node CLI 不会自动连接 Chrome。不要凭空调用当前运行时没有的 API。
5. 提前监听真实首屏请求，验证可见账号、账号身份、关联成功 JSON 响应，按捕获模块的边界关闭自己的临时页。默认不滚动；确需一次备用滚动时走已有显式选项，不无限滚动重试。
6. 收到 `READY_TO_RESUME` 后，使用原任务的基础配置和本机 manifest 执行：

```text
PY -X utf8 -m tkmonitor --config config.local.json onboard --resume ACTUAL_MANIFEST.local.json
```

单账号任务和批次恢复任务都按原 manifest 的范围继续，不能把原三账号批次改成仅补交一个账号。`READY_TO_RESUME` 只代表准备好了请求，不代表全量统计已完成。

若 Codex 只能控制原生 UI、没有捕获模块所需的浏览器／CDP 工具，则记录 `BROWSER_UNAVAILABLE`，继续其他独立工作，明确首次接入尚未通过。不要把 Windows 上的成功证据当成 Mac 当前设备的成功。

## 七、原文报表与完整交付

正常交付直接使用原始剧名和简介。整批采集已生成并核验全套报表时，可以直接交付。逐个账号接入后要生成总报告，或需要重做交付时，按原采集日期运行以下命令。

```text
PY -X utf8 -m tkmonitor --config config.local.json finalize --day ACTUAL_BEIJING_DAY --metric frontend
PY -X utf8 -m tkmonitor --config config.local.json export-xlsx --day ACTUAL_BEIJING_DAY --metric frontend
PY -X utf8 -m tkmonitor --config config.local.json package --day ACTUAL_BEIJING_DAY --metric frontend --output _local/deliveries/ACTUAL_BEIJING_DAY
```

本次统计日按北京时间确定。跨北京时间零点时重新检查各账号日期覆盖。通过现有下载和缓存校验补齐页面对应缩略图。HTML 可展开原始简介，Excel 使用整数播放量和已有对比口径。

用户明确要中文版时，可以在开始采集或接入时加 `--translate`，也可以基于已采快照按说明补译。带 `--translate` 的任务在退出码 5 时返回私有待办，导入后从已提交快照继续交付。 [中文资料交付说明](translations.md)

## 八、验收与日常入口

Windows 日常运行：

```powershell
.\run_daily.cmd
.\run_onboard.cmd --url https://www.tiktok.com/@ACCOUNT
```

macOS 日常运行：

```sh
sh ./run_daily.sh
sh ./run_onboard.sh --url https://www.tiktok.com/@ACCOUNT
```

macOS 入口用 `sh` 调用，不要求先修改可执行位。两平台都需由自己的私有运行时文件提供正确路径。

逐项完成验收：

1. 从新 shell 重载运行时配置，项目帮助、测试和入口能运行。
2. 真实当前日期 HTTP 分页采集成功；记录每个账号、条数、抓取起止时间、统计时间、接口整数覆盖和末页确认。不要硬编码以前的剧目数量。
3. 全部旧快照 ID 和 fingerprint 与迁移前基准一致，旧初始值逐条一致；本次只追加有效观测或补充独立资料，默认三账号范围不变。
4. 核对完整报告每条剧的本次封面、原始剧名、原始简介和整数播放量。列明实际缺失项。显式交付中文版时另核对中文字段。
5. 真实生成并重新读取 XLSX，核对 `workbooks_verified.json` 的结果，确认数值正确且图片数与记录数一致。
6. 本机打开 HTML 检查图片和简介展开，检查 Excel 内容、图片和数字。无法视觉检查时在验收记录中说明。
7. 检查 Git 忽略规则及 `git status`，保证私有配置、数据库、`.venv`、Node 模块、截图／报表、迁移包没有进入提交。无需为本次部署推送代码。

`0` 表示完成，`2` 表示配置、采集或导出失败，`3` 表示报告不完整，`4` 表示需要已授权浏览器动作，`5` 表示显式请求的中文待补译且已采数据保留，`130` 表示用户中断。按对应流程继续处理，优先复用已提交快照。

## 九、最终交付给用户

把证据保存在项目 `_local/new-device/` 下以当前日期和唯一运行编号命名的私有目录；日志不含请求密钥。交付：

- 新设备 OS／架构、项目路径、实际代码 SHA、Python／Node／Pillow／表格运行时版本和路径；
- 已安装、复用、验证的依赖，以及可在新 shell 直接运行的日常命令；
- 历史迁移来源、备份位置、快照与初始值核验结果，缺历史时明确说明；
- 离线测试日志、浏览器接入证据、真实线上采集结果及统计时间；
- HTML、三个账号各自的 Excel、CSV／JSON、媒体和工作簿核验文件的绝对路径；
- 每项状态分为“通过”“阻塞”“未验证”。环境安装成功、核心可运行、历史已迁移、Chrome可用、完整Excel交付分别说明。

只有目标设备真实完成采集和全部交付核验，才可以说“这台设备已正常运行完整流程”。若缺少迁移资料、浏览器能力或表格运行时，完成其余工作并列出实际阻塞、已完成结果和下一步，不能夸大为全部完成。

## 官方参考（安装时重新核对当前页面）

- [Python Windows 下载](https://www.python.org/downloads/windows/)、[Python macOS 下载](https://www.python.org/downloads/macos/)
- [Python venv 说明](https://docs.python.org/3/library/venv.html)、[SQLite Python 接口及备份](https://docs.python.org/3/library/sqlite3.html)
- [Node.js 官方下载](https://nodejs.org/en/download)
- [Microsoft WinGet install](https://learn.microsoft.com/en-us/windows/package-manager/winget/install)
- [Homebrew 安装说明](https://docs.brew.sh/Installation)
- [Git 官方安装来源](https://git-scm.com/downloads)、[Chrome 官方下载](https://www.google.com/chrome/)

以上链接用于核实安装方式；本文件不承诺将来某个下载地址、包版本或浏览器工具能力永远不变。

本文随附的入口开发验证见[验证记录](verification-new-device-entry-2026-11-16.md)，仅代表记录中明确测试的平台和范围，不代替当前新设备的验收。
