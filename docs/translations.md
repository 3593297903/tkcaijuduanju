# 原文交付与按需中文补译

默认交付使用平台原始剧名和简介，直接生成报表。需要中文版时，在 `collect`、`onboard`、`report` 或 `finalize` 上显式使用 `--translate`。只有启用中文时，已有译文才按账号、剧目 ID 和确切原文复用。

启用 `--translate` 后，缺译时返回 `NEEDS_TRANSLATION`、`task` 路径和退出码 **5**。沿用已保存的快照，补译任务内的新增或变更条目，再导入并继续导出。播放量、初始值和统计时间保留在已提交的快照中。

补译任务由 Codex 或指定的翻译提供方填写。

## 补译命令

已有快照时，可以直接生成增量翻译任务。

```powershell
python -X utf8 -m tkmonitor --config config.ACCOUNT.local.json translation-tasks --day YYYY-MM-DD --metric frontend
```

保留任务文件中的 schema、账号、ID、`observed_at`、原始剧名、原始简介和 `source_hash`。逐条填写 `title_zh`、`description_zh` 和提供方 `translator`，可补充中文 `genre`。参考译名和简介应忠实于原文，保留人物关系、性别、能力、冲突和结局。原始简介缺失时先核实来源资料。

```powershell
python -X utf8 -m tkmonitor --config config.ACCOUNT.local.json import-translations --input PATH_TO_COMPLETED.local.json
```

导入会逐条核对保存的原文和来源哈希，整批通过后才写入。只有待翻译提示、纯英文或不匹配的原文不能作为中文导入。导入不会修改历史播放量。

按原统计日期重做中文版报表、Excel 与交付包。

```powershell
python -X utf8 -m tkmonitor --config config.ACCOUNT.local.json finalize --day YYYY-MM-DD --metric frontend --translate
python -X utf8 -m tkmonitor --config config.ACCOUNT.local.json export-xlsx --day YYYY-MM-DD --metric frontend --translate
python -X utf8 -m tkmonitor --config config.ACCOUNT.local.json package --day YYYY-MM-DD --metric frontend --translate --output DELIVERY_DIRECTORY
```

起始命令已加 `--translate` 的接入或恢复任务，可以在导入译文后用原配置和 manifest 继续。恢复沿用原选项并复用已提交快照。

```powershell
python -X utf8 -m tkmonitor --config config.local.json onboard --resume PATH_TO_ONBOARDING.local.json
```

`export-xlsx --translate` 和 `package --translate` 校验已生成的 `merged.json`。导入译文后，若保存的是原文版，请先运行 `finalize --translate` 重做报表，再导出并核验。单独导入只更新资料库。Excel 保存后会核对剧名、简介、题材、数值、ID 和图片数量。

`--allow-untranslated` 作为原文模式的兼容选项继续接受。日常命令可以直接省略语言选项。
