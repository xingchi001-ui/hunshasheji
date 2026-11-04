# 本地礼服训练数据

将本地生成的 `segments.jsonl` 放到本目录后导入 SQLite：

```bash
python import_dress_data.py data/dress_training/segments.jsonl --db show_gown_studio.db --replace
```

也可以通过环境变量指定路径：

```bash
DRESS_TRAINING_DATA=/path/to/segments.jsonl python main.py
```

程序会在生成设计方案前按灵感关键词、参考图识别结果和补充要求检索候选记录，并把候选上下文注入文字模型提示词。没有数据文件时，应用仍按原有流程运行。

原书 PDF、扫描页图像和未经授权的完整正文不应提交到公开仓库；本目录只提供接入说明，实际数据保留在本地或受控存储中。
