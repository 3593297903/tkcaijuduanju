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

## 请求配置

HTTP源从`request-specs/*.local.json`读取私有请求。禁止将真实Cookie、签名URL或HAR提交仓库。程序只使用正常请求与有限重试，不提供绕过验证码、生成平台签名或规避权限的功能。

新剧的封面下载、中文翻译和整剧发布时间需分别处理。默认交付缺中文时返回退出码5，按[中文资料交付说明](translations.md)由Codex忠实补译保存的原文，再继续导出，不重采数字；只有明确的原文交付使用`--allow-untranslated`。原始简介未获得时显示缺失，绝不能由剧名编剧情或由ID推算发布时间。
