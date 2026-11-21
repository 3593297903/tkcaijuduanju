# 日常使用与恢复

## 已配置的本机

双击仓库根目录的`run_daily.cmd`。本机`runtime.local.cmd`已设置现有Python/Node运行环境，`config.local.json`和`request-specs/*.local.json`已配置三个监测账号。这些文件被Git忽略。

正常流程是 HTTP 分页采集、完整性校验、提交 SQLite，再用原始剧名和简介导出 CSV、JSON、离线网页及 Excel。默认流程无翻译待办。退出码 0 表示本次流水线完成。报告只使用指定统计日的有效数据。

报表位于 `_local/state/reports/北京时间日期/frontend/monitor.html`，可离线双击查看。每日入口同时导出各账号的 Excel。Excel 导出失败时，可以单独运行 `export-xlsx`，复用本次采集及已有报表。

## 项目专用代理与 DNS

项目目录下的 `network.local.json` 为本机私有配置。列表、封面、接入恢复和 `finalize` 下载共用此配置。没有该文件时使用原有网络路径。隔离验收可用 `TKMONITOR_NETWORK_CONFIG` 指向私有配置文件。

```json
{
  "mode": "proxy",
  "proxy": "http://127.0.0.1:7897",
  "dns_url": "https://dns.google/resolve",
  "browser_fallback": true
}
```

端口填写本机已验证可信的 HTTP 代理端口。DNS 支持上例或 `https://cloudflare-dns.com/dns-query`。项目通过代理建立到经公网校验的 IPv4 地址的隧道，仍用原域名验证 TLS。代理配置只在项目进程内生效。

`browser_fallback` 可省略，省略时关闭。也可以在发起 `collect` 或 `onboard --url` 时传 `--browser-fallback`。启用后，合适的 DNS 或代理故障可返回浏览器全量任务。列表已入库、封面因网络或缺少来源地址无法完成的情况，可返回只补封面的任务。独立 `finalize` 使用同一网络配置，负责重做已有快照的报表。 [浏览器备用任务的操作](browser-session.md#全量采集与封面备用任务)

## 第一次在另一台电脑运行

安装Python3.11+，复制`config.example.json`为`config.local.json`，配置实际账号和本机请求；需要历史时导入合法保留的历史目录或备份。核心无需pip依赖。

有Node及`@oai/artifact-tool`运行环境时再配置Excel导出。可在本机创建`runtime.local.cmd`设置`TKMONITOR_PYTHON`、`TKMONITOR_NODE`、`TKMONITOR_NODE_MODULES`。不要把真实路径和凭据提交到公共仓库。

## 请求失效或网络失败

新的单账号接入可使用 `onboard`，正常配置直接复用；需要Chrome时返回私有manifest和退出码4，浏览器端准备完成后 `onboard --resume` 继续。详细命令见[接入与恢复说明](onboarding.md)。原入口不自动扩大监测范围。需要原三账号整批准备恢复任务时可显式使用 `run_daily.cmd --recover-request`。

1. 先用`python -m tkmonitor --config config.local.json status`查看最近尝试。`failed`不等于有效快照。
2. 网络错误有有限重试；排查本机网络后可以重跑。401/403或验证HTML则恢复正常浏览器会话，不重复轰炸接口。
3. 推荐按返回的manifest调用`integrations/chrome_capture.mjs`中的`captureOnboarding`，在用户Chrome临时页中提前监听正常首屏请求；再用`onboard --resume`验证并恢复。模块不读取Cookie/Authorization头、不生成签名、不固定滚动。没有浏览器通道时明确保留待办，不假报完成。
4. 再运行一键入口。新的有效记录只会追加；旧快照和初始值不重置。

## 统计口径

2026-10-30已适配列表中的纯ID引用项（仅含`dramaID`）：分页仍按原始条目数推进，最终必须找到该ID的完整记录才能提交。引用项本身不作为剧目、不填0；完整记录重复、资料冲突或引用未解析仍然失败。

日常默认`frontend`为接口整数。旧`display`历史为页面缩写折算整数，两条序列独立。某日没有某指标时留空；不是改用另一个指标。

初始表示每部剧在所选指标下的首次已知值；日历昨天与最近实际记录分开。新增剧昨天无记录时留空。被下架或暂时没有采到的剧不删除历史、不按0计算。精确历史不足的剧，涨幅只用实际存在的相同口径数据。

## 资料和备份

封面可复用本机已有文件，缺图时按本次采集保存的 URL 下载并核验。原文报表直接显示平台资料。需要中文时加 `--translate`，按账号、剧目 ID 及确切原文复用译文，再对新增或原文变化的条目补译。补齐后可直接重做报表。整剧发布时间未知时留空。 [中文资料交付说明](translations.md)

`legacy/v1`及版本标签保留旧方法。完整旧资料ZIP在本机`_local/`中。定期在程序未写入时备份`_local/state/`；公共GitHub只保存源码、文档和脱敏测试数据。

## 定时任务

目前没有擅自安装计划任务。确认本机连续运行、跨日参数可用后，再按README的可选命令安装。电脑需开机、联网；请求过期仍需正常更新。不能承诺第三方网页接口永不改变，但来源变化只需调整采集适配器，历史和统计层保留。
