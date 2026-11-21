# Chrome辅助采集

`integrations/chrome_capture.mjs`保留原导出，并提供新的 `captureOnboarding`，实现位于 `integrations/chrome_onboarding.mjs`。它依赖调用方提供的真实已授权Chrome运行时，取得正常请求后，仍由现有Python程序从第0页全量采集。不读取Cookie/Authorization头、不生成签名。

推荐按[新账号接入与恢复](onboarding.md)使用 `onboard → captureOnboarding → onboard --resume`。新流程优先复用配置，提前监听首屏，不刷新用户原标签页、不固定滚动；普通终端不会自动取得Codex的浏览器连接。

在Codex已连接用户Chrome、已读浏览器及CDP文档的Node运行时中使用：

```js
const {refreshRequestSpec}=await import('项目绝对路径/integrations/chrome_capture.mjs');
await refreshRequestSpec({
  tab: authorizedTab,
  account: 'epiccreation111',
  output: '项目绝对路径/request-specs/epiccreation111.local.json'
});
```

上面的兼容调用默认只观察已经位于目标账号的Tab，不隐式导航、不固定滚动；必须观察到可验证的请求及成功响应。需要导航时应由调用方提供自己创建的临时页并显式设置 `navigate:true`。只保存URL和Accept、Accept-Language、Referer、User-Agent四类普通头，输出必须以`.local.json`结尾。

新工作流会核对可见账号、公开资料身份与请求secUid、关联响应、业务状态和整数结构。默认无首屏请求时返回未完成；只有开始任务时显式 `--allow-scroll-fallback` 才允许一次备用滚动。兼容函数不声称只有一个请求样本就完成全账号采集。

这个步骤只更新私有请求配置，不向数据库提交任何快照。Python HTTP采集器会重新从cursor0读取全账号，校验偏移、唯一ID和末页标记后才提交。一次浏览器分页请求不等于一次完整采集。

## 独立运行限制

该模块需要受支持的浏览器工具运行时提供Tab。目前不是一个独立Chrome扩展，也不声称在普通Node命令行里可自动连接用户的默认Chrome。独立HTTP采集已于2026-10-29使用三个账号实际验证；参数跨日有效期仍需持续观察，失效时重新执行请求更新。

新系统的checkpoint命令源可接入能生成相同结构的外部程序；若配置外部命令，核心还会核验文件被本轮重写、采集开始时间晚于调用、统计日正确，防止旧文件被冒充为今天。

## 全量采集与封面备用任务

如果已启用浏览器备用，CLI 在明确的网络故障时返回 `NEEDS_BROWSER`，并生成以 `.browser.local.json` 结尾的私有任务。`purpose: full_collection` 需要捕获整批分页，`purpose: covers` 使用已提交的快照补图。首屏请求接入仍由 `captureOnboarding` 处理。全量任务打开自己的临时页，在同一页验证首屏并翻到终页。

Codex 连接用户 Chrome 并读过 CDP 文档后，可以处理 CLI 实际返回的备用任务。在浏览器工具的 JavaScript 运行时执行：

```js
const {captureCheckpoint} = await import('file:///E:/jianc/tkcaijuduanju/integrations/chrome_checkpoint.mjs');
const result = await captureCheckpoint({
  manifestPath: 'CLI 实际返回的 .browser.local.json 完整路径',
  createTab: url => cua.createBrowserTab('chrome', url, {sessionName: '📊 短剧采集恢复'}),
});
nodeRepl.write(result);
```

然后用原来的命令继续。完成首屏任务时保存的选项会沿用。收到 `COVERS_INCOMPLETE` 时也可先恢复一次，把已经核验的封面写入缓存，再按剩余任务继续。

```powershell
.\run_onboard.cmd --resume 'CLI 返回的 .browser.local.json 完整路径'
```

工具返回 `CAPTURE_ALREADY_RUNNING` 时，先等原捕获结束。若捕获进程意外终止且锁仍存在，确认原运行已经停止，再重新发起采集或根据私有任务记录恢复。浏览器断开时任务可以保留，直到 Codex 重新识别这台 Chrome。

## 请求配置

HTTP源从`request-specs/*.local.json`读取私有请求。禁止将真实Cookie、签名URL或HAR提交仓库。程序只使用正常请求与有限重试，不提供绕过验证码、生成平台签名或规避权限的功能。

正式采集默认交付原始剧名和简介，封面按现有流程下载和核验。需要中文时，另按[中文资料交付说明](translations.md)显式启用补译，复用已提交的数字快照。原始简介或发布时间缺失时如实标记。
