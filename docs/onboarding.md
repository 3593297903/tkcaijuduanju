# 新账号接入与有界恢复

本功能复用现有 Python HTTP 全量采集器。已有配置直接采集；缺配置时，Python 生成私有接入任务，由 Codex 已连接用户 Chrome 的运行时取得正常请求，再通过同一命令恢复采集。

普通终端没有 Codex 浏览器 Tab，不能自行完成 Chrome 部分。返回 `NEEDS_BROWSER`、退出码4表示未完成，不是采集成功。

## 已有账号

原默认三个账号的入口保持不变：

```powershell
.\run_daily.cmd
```

已有请求不会按保存日期强制更新，也不会在每天采集前额外探测一页。正式采集结果决定是否可用。

单独采集 SnapScript，可沿用其配置：

```powershell
python -X utf8 -m tkmonitor --config config.jvgfc4ej48.local.json collect --report --metric frontend --finalize --xlsx
```

新入口也可复用已有配置，并使用单账号报告范围：

```powershell
python -X utf8 -m tkmonitor --config config.local.json onboard --url https://www.tiktok.com/@jvgfc4ej48 --name SnapScript --report --metric frontend --finalize --xlsx
```

`python`、`node` 可替换为调用方的实际运行时。XLSX 仍需本机已有的 `TKMONITOR_NODE` 和 `TKMONITOR_NODE_MODULES`。不安装新浏览器或扩展。

Windows 可使用新增的 `run_onboard.cmd`，自动读取原有 `runtime.local.cmd`，无需另行选择Python/Node。该入口不强加导出选项，因此也能正确用于 `--dry-run` 和 `--resume`：

```powershell
.\run_onboard.cmd --url https://www.tiktok.com/@jvgfc4ej48 --name SnapScript --report --metric frontend --finalize --xlsx
.\run_onboard.cmd --resume '_local/onboarding/实际任务ID.local.json'
```

## 新账号：计划、接入、恢复

```powershell
# 只看计划：不发HTTP请求，不操作浏览器，不写配置或数据库。
python -X utf8 -m tkmonitor --config config.local.json onboard --url https://www.tiktok.com/@ACCOUNT --name DISPLAY_NAME --dry-run

# 开始接入；已有请求就直接采集，没有则返回待办任务。
python -X utf8 -m tkmonitor --config config.local.json onboard --url https://www.tiktok.com/@ACCOUNT --name DISPLAY_NAME --report --metric frontend --finalize --xlsx
```

没有请求时返回：

```json
{"status":"NEEDS_BROWSER","account":"ACCOUNT","manifest":"本机实际私有任务路径","collection":"not_completed"}
```

账号配置默认是根目录 `config.ACCOUNT.local.json`，报表使用 `state_dir/account-reports/ACCOUNT/日期/口径`。已有单账号配置、自定义路径和额外字段保留；默认三账号名单不变。

### Codex Chrome 运行时

先按浏览器工具要求完成初始化、选择用户 Chrome、命名会话，并读取其浏览器和 CDP 文档。不能用普通 `node` 命令代替这一步。

在受支持的 `cua_repl` 中，创建临时 TikTok 同域页并读取 CDP 文档后，可调用以下项目模块。`manifestPath` 必须替换为本次 Python 实际返回的路径：

```js
var initTab = await cua.createBrowserTab('chrome', 'https://www.tiktok.com/', {sessionName:'📊 短剧接入'});
nodeRepl.write(await (await initTab.capabilities.get('cdp')).documentation());
```

阅读上一步返回的文档后，在下一次调用中执行捕获；先将模块地址替换为项目的实际绝对 file URL：

```js
var capture = await import('file:///项目绝对路径/integrations/chrome_capture.mjs');
var result = await capture.captureOnboarding({
  manifestPath: 'Python返回的实际manifest绝对路径',
  createTab: async url => {
    if (initTab) { var tab=initTab; initTab=null; return tab; }
    return await cua.createBrowserTab('chrome', url, {sessionName:'📊 短剧接入'});
  }
});
nodeRepl.write(result);
```

捕获模块在自己的临时页中先监听，再正常打开目标主页或进入 Short dramas；核对可见账号、正常资料中的 `secUid`、请求及成功 JSON 响应。默认不滚动。完成后关闭自己的临时页，保留用户原页面。

如果只有一个已明确记录的同账号请求，可通过 `recordedTab`、`initialEvents` 显式传入；这不是读取浏览器全部历史的接口。必须有实际留存事件，不能假定过去未监听的请求可恢复。

模块保留 `refreshRequestSpec` 兼容导出，但取消隐式导航和固定滚动。直接调用时默认只观察调用方提供的目标页；需要导航必须提供自己拥有的临时页并显式设置 `navigate:true`。推荐使用上面的 manifest 工作流。

### 恢复正式采集

浏览器返回 `READY_TO_RESUME` 后：

```powershell
python -X utf8 -m tkmonitor --config config.local.json onboard --resume '_local/onboarding/实际任务ID.local.json'
```

恢复使用任务内保存的目标和导出参数，不应额外修改 `--name`、`--day` 或导出选项。候选请求验证通过才激活，之后仍从 cursor0 读取完整账号，不直接把捕获的一页作为快照提交。

导出失败后再次 resume 会核对已提交的快照证据，只重做导出。已完成任务重复 resume 返回完成回执，不再采集。若还没提交且已跨北京时间午夜，则重新确定当天并重新请求，不将旧响应当今天。

## 无首屏请求与权限边界

提前监听不能保证所有 TikTok 首屏都发出目标接口。如果已正常进入 Short dramas 后仍没有可验证请求，默认返回明确未完成状态。

确实允许一次滚动作为备用时，开始任务时加：

```text
--allow-scroll-fallback
```

一个任务最多一次备用滚动；因事件截断最多重建一次临时页，共享同一个时间预算。默认预算90秒，`--browser-timeout 35` 可调整为5至300秒。页面无下一页时不循环滚动。浏览器运行时不可用时保留任务，不伪装连接成功。

登录、验证码和浏览器权限按工具规则交接，不绕过验证，不开放调试端口、不复制登录数据库。

如果捕获进程中断，任务留有开始标记但没有完整候选，默认返回 `NEEDS_BROWSER_ACTION`，不会无休止重试同一任务。先确认原捕获已停止；需要重新开始时，用相同主页和导出参数显式增加 `--new-task`，创建新任务并保留旧证据。该参数不会强制更新仍有效的请求，也不能与 `--resume` 同用。

## 请求失效

`onboard` 对本轮 HTTP 401/403 生成一次恢复任务，更新后仍被拒绝就失败，不重复开页。网络超时、限流、5xx、未知业务错误、字段变化和普通非JSON响应不会盲目触发 Chrome 恢复。

原 `collect` 默认保留直接报错的行为。需要为原三账号整批准备恢复任务时，显式选择：

```powershell
python -X utf8 -m tkmonitor --config config.local.json collect --recover-request --report --metric frontend --finalize --xlsx
# 原Windows入口会传递该参数：
.\run_daily.cmd --recover-request
```

失败批次没有半批快照提交。Chrome 更新失败账号的请求后，使用返回 manifest 恢复整个原选择批次；不是只补交其中一个账号。

## 私有文件与验证

请求、候选、正常响应、身份核验和manifest都保存在被Git忽略的本机路径。普通头采用白名单，不保存Cookie/Authorization头；正常URL自带的签名留在私有请求文件中，不在日志输出。

同账号操作采用OS文件锁，进程退出自动释放。候选先核验再替换，请求与配置更新分别记录阶段，不能宣称文件系统和SQLite组成了跨资源原子事务。

采集仍由 `collect_http` 负责整数、唯一ID、游标偏移、引用项与终页核对。保留初始值、历史、双指标隔离、缩略图及原文。中文默认按[中文资料交付说明](translations.md)补齐；缺失时返回退出码5，导入翻译后再次 `onboard --resume`，直接使用已提交快照继续导出，不重采。发布时间未知仍留空。默认监测范围不会因为调用 `onboard` 自动扩大。

退出码：0完成或只读计划通过，2失败，3报告不完整，4需要浏览器/恢复动作，5中文待补译且有效采集已保留，130中断。`DRY_RUN` 不代表已采集；`READY_TO_RESUME` 不代表全量完成。

```powershell
python -X utf8 -m unittest discover -s tests -v
node tests/test_chrome_request.mjs
```

上述测试使用合成数据。真实线上能力以独立验收记录为准；已有账号无缓存重接入不等于一个全新账号第一次接入。
