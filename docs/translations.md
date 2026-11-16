# 默认中文交付与增量补译

默认报表需要每条剧目的中文剧名和中文简介。播放量采集仍使用原有 HTTP 分页和整数校验；翻译只改独立资料，不改快照、初始值、播放量或统计时间。此前已经保存且能确认原文一致的中文直接复用，只翻译新增或原文变化的条目。

中文缺失时，报告命令返回 `NEEDS_TRANSLATION`、`task` 路径和退出码 **5**。这表示有效数字已采集并保存，中文交付尚未完成。Codex 执行采集任务时应继续读取任务、补译并导出；不要在这里把英文待翻译版本当作完成品交给用户。

Python 采集器不自带付费翻译服务，不读取其他服务的密钥。独立终端或计划任务遇到退出码5需要翻译提供方处理任务；本项目没有声称后台无人值守的 AI 翻译已经接通。

## 补译命令

如果需要手动创建或查看增量任务：

```powershell
python -X utf8 -m tkmonitor --config config.ACCOUNT.local.json translation-tasks --day YYYY-MM-DD --metric frontend
```

读取返回的 `.local.json`，保留 schema、账号、ID、observed_at、英文剧名、原始简介和 source_hash。逐条填入 `title_zh`、`description_zh`，可填写中文 `genre`；填写 `translator` 记录提供方。英文原文不得改写，翻译需保留人物关系、性别、特殊能力、冲突和结局，不能只看剧名猜剧情。中文剧名是中文参考译名，不冒充官方译名。原始简介缺失时先补采原始资料，不能编写简介通过检查。

```powershell
python -X utf8 -m tkmonitor --config config.ACCOUNT.local.json import-translations --input PATH_TO_COMPLETED.local.json
```

导入会逐条核对保存的原文和来源哈希，整批通过后才写入。只有待翻译提示、纯英文或不匹配的原文不能作为中文导入。导入不会修改历史播放量。

普通日报按原统计日期重做导出：

```powershell
python -X utf8 -m tkmonitor --config config.ACCOUNT.local.json finalize --day YYYY-MM-DD --metric frontend
python -X utf8 -m tkmonitor --config config.ACCOUNT.local.json export-xlsx --day YYYY-MM-DD --metric frontend
python -X utf8 -m tkmonitor --config config.ACCOUNT.local.json package --day YYYY-MM-DD --metric frontend --output DELIVERY_DIRECTORY
```

如果是带 manifest 的新账号接入或恢复任务，使用最初的配置和 manifest 直接继续；不需要重采或重新准备浏览器：

```powershell
python -X utf8 -m tkmonitor --config config.local.json onboard --resume PATH_TO_ONBOARDING.local.json
```

`export-xlsx` 和 `package` 核查的是准备交付的 `merged.json`。如果导入后还留着英文旧版报表，需要先 `finalize` 或 `report` 重做报表；单独导入不会偷偷改写已经交付或正在打开的文件。Excel 保存后还会核对中文名、中文简介、题材、数值、ID及图片数量。

## 明确要求原文或只看数字

用户明确只需要播放量或英文原文时，可在报告或采集命令中追加 `--allow-untranslated`。纯 `collect`（不带报告、Excel或交付选项）仍只采数字，不因中文缺失中断。缺日、缺指标等原有退出码3保持不变。
