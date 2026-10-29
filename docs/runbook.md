# 日常使用与恢复

## 已配置的本机

双击仓库根目录的`run_daily.cmd`。本机`runtime.local.cmd`已设置现有Python/Node运行环境，`config.local.json`和`request-specs/*.local.json`已配置三个监测账号。这些文件被Git忽略。

正常流程是HTTP分页采集、完整性校验、提交SQLite，再导出CSV、JSON及离线网页。不调用大模型，不需要让模型逐页看页面。退出码0才表示本次流水线完成；失败不会拿旧数据当今天。

报表位于`_local/state/reports/北京时间日期/frontend/monitor.html`，可离线双击查看。可追加`--xlsx`导出三个Excel。Excel导出失败时，采集及已有报表仍保存，可单独运行`export-xlsx`重做，不必再抓数据。

## 第一次在另一台电脑运行

安装Python3.11+，复制`config.example.json`为`config.local.json`，配置实际账号和本机请求；需要历史时导入合法保留的历史目录或备份。核心无需pip依赖。

有Node及`@oai/artifact-tool`运行环境时再配置Excel导出。可在本机创建`runtime.local.cmd`设置`TKMONITOR_PYTHON`、`TKMONITOR_NODE`、`TKMONITOR_NODE_MODULES`。不要把真实路径和凭据提交到公共仓库。

## 请求失效或网络失败

1. 先用`python -m tkmonitor --config config.local.json status`查看最近尝试。`failed`不等于有效快照。
2. 网络错误有有限重试；排查本机网络后可以重跑。401/403或验证HTML则恢复正常浏览器会话，不重复轰炸接口。
3. 使用`integrations/chrome_capture.mjs`中的`refreshRequestSpec`观察正常分页请求，更新对应私有配置。模块不读取Cookie/Authorization、不生成平台签名。
4. 再运行一键入口。新的有效记录只会追加；旧快照和初始值不重置。

## 统计口径

日常默认`frontend`为接口整数。旧`display`历史为页面缩写折算整数，两条序列独立。某日没有某指标时留空；不是改用另一个指标。

初始表示每部剧在所选指标下的首次已知值；日历昨天与最近实际记录分开。新增剧昨天无记录时留空。被下架或暂时没有采到的剧不删除历史、不按0计算。精确历史不足的剧，涨幅只用实际存在的相同口径数据。

## 资料和备份

已有封面和中文资料按ID保留。新剧及无法确认原文未变化的旧译文会提示待翻译/待核对，原始英文留存。新图像URL保留但下载补图是单独步骤。整剧发布时间未知时留空，关联视频日期不冒充上线日期。

`legacy/v1`及版本标签保留旧方法。完整旧资料ZIP在本机`_local/`中。定期在程序未写入时备份`_local/state/`；公共GitHub只保存源码、文档和脱敏测试数据。

## 定时任务

目前没有擅自安装计划任务。确认本机连续运行、跨日参数可用后，再按README的可选命令安装。电脑需开机、联网；请求过期仍需正常更新。不能承诺第三方网页接口永不改变，但来源变化只需调整采集适配器，历史和统计层保留。
