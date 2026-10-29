# Chrome辅助采集

`integrations/chrome_capture.mjs`只依赖调用方提供的已授权浏览器Tab。它获取一次正常分页请求的现成参数，随后由独立Python程序从第0页全量采集。不读取Cookie/Authorization，不生成签名，不包含本机私有路径。

在Codex已连接用户Chrome、已读浏览器及CDP文档的Node运行时中使用：

```js
const {refreshRequestSpec}=await import('项目绝对路径/integrations/chrome_capture.mjs');
await refreshRequestSpec({
  tab: authorizedTab,
  account: 'epiccreation111',
  output: '项目绝对路径/request-specs/epiccreation111.local.json'
});
```

模块在当前账号首屏启用网络记录，用一次普通页面滚动捕获分页请求。它只保存URL和Accept、Accept-Language、Referer、User-Agent四类普通头。输出必须以`.local.json`结尾，不能意外保存为公共源码。

这个步骤只更新私有请求配置，不向数据库提交任何快照。Python HTTP采集器会重新从cursor0读取全账号，校验偏移、唯一ID和末页标记后才提交。一次浏览器分页请求不等于一次完整采集。

## 独立运行限制

该模块需要受支持的浏览器工具运行时提供Tab。目前不是一个独立Chrome扩展，也不声称在普通Node命令行里可自动连接用户的默认Chrome。独立HTTP采集已于2026-10-29使用三个账号实际验证；参数跨日有效期仍需持续观察，失效时重新执行请求更新。

新系统的checkpoint命令源可接入能生成相同结构的外部程序；若配置外部命令，核心还会核验文件被本轮重写、采集开始时间晚于调用、统计日正确，防止旧文件被冒充为今天。

## 请求配置

HTTP源从`request-specs/*.local.json`读取私有请求。禁止将真实Cookie、签名URL或HAR提交仓库。程序只使用正常请求与有限重试，不提供绕过验证码、生成平台签名或规避权限的功能。

新剧的封面下载/中文翻译和整剧发布时间需独立补充；未获得的资料显示待补，绝不能由剧名编剧情或由ID推算发布时间。
