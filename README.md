
## MiniMax-M3.1 推理强度兼容

针对 MiniMax-M3.1 多模态识图可能出现 `finish_reason=length`、`content` 为空的问题，程序会自动为 `MiniMax-M3.1` 和 `MiniMax-M3.1-Flash` 加入：

```json
"reasoning_effort": "low"
```

同时保留 `max_completion_tokens=8000`。其他文字模型不会自动携带该参数，避免模型不兼容。

最终 GUI 实测日志：

```text
请求模型：MiniMax-M3.1，max_completion_tokens=8000，reasoning_effort=low
HTTP 返回：200
finish_reason=stop
文本长度=4641
```

## 礼服设计知识库兼容接入

项目现在支持将本地生成的礼服设计 `segments.jsonl` 导入 SQLite 的 `dress_knowledge` 表，并在生成设计方案前按参考图识别结果、灵感关键词和设计师要求检索候选记录。

```bash
python import_dress_data.py /path/to/segments.jsonl --db show_gown_studio.db --replace
python main.py
```

也可以设置 `DRESS_TRAINING_DATA`，工作台启动时会在知识表为空时自动导入。没有本地训练数据时，原有工作流保持不变。原书 PDF 和完整正文不随代码提交到公开仓库。

兼容性回归：

```bash
python tests/test_regression.py
```

## Windows standalone EXE build

On Windows, double-click the ASCII-only `build.bat`. It creates a virtual environment, installs dependencies, runs PyInstaller, and generates:

```text
release/ShowGownStudio/ShowGownStudio.exe
```

The GUI remains Chinese. Copy `config.example.json` to `config.json` beside the EXE and fill in the API key. Then double-click the ASCII-only `start.bat` to launch the app. The config file, SQLite database, and local training data stay beside the EXE instead of the PyInstaller temporary folder.

Optional local training data path:

```text
data/dress_training/segments.jsonl
```

Original PDFs and complete book text are not included in the EXE package.
