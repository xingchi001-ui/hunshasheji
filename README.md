
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

仓库已内置当前视觉知识库：`show_gown_studio.db` 包含 846 条逐页视觉理解记录，程序启动时默认读取该数据库。数据库不是静态附件：设计简报会先检索 `dress_knowledge`，再生成结构化推荐、来源证据和风险审查，并在保存项目/生成记录时写入关联证据与决策包。

```bash
python import_dress_data.py /path/to/segments.jsonl --db show_gown_studio.db --replace
python main.py
```

也可以设置 `DRESS_TRAINING_DATA`，工作台启动时会在知识表为空时自动导入。没有本地训练数据时，原有工作流保持不变。原书 PDF 和完整正文不随代码提交到公开仓库。

### 从 PDF 建立视觉设计数据库

`analyze_books_to_db.py` 会逐本逐页渲染 PDF 页面，并使用配置的视觉模型分析廓形、领型、袖型、面料、表面细节、结构工艺、色彩、动态效果、设计启发和不确定性。该流程只发送页面图像，明确禁止 OCR 和文字转录；结果可断点续跑，写入 `segments.jsonl` 后导入 SQLite：

```bash
LLM_TOKEN_API_KEY='你的密钥' python analyze_books_to_db.py /path/to/pdfs \
  --model MiniMax-M3 \
  --output data/dress_training/segments.jsonl \
  --db show_gown_studio.db
```

分析结果会进入 `dress_knowledge`，设计方案生成前按设计简报和视觉分析检索证据，推荐与风险审计会记录证据页和决策包。

### 已内置数据库文件

```text
show_gown_studio.db                         # 846 条知识记录，应用默认使用
data/dress_training/segments_846.jsonl      # 对应的视觉理解记录
data/dress_training/failed_pages.jsonl      # 尚未成功分析的页面清单
```

数据库链路的实测回归覆盖：SQLite 表初始化、知识检索、结构化推荐、风险审计、项目证据关联、生成审计和 GUI 离屏启动。

图片工具链已针对当前兼容端点完成参数修复：图片请求使用整数 `n=1`，不发送该端点不接受的 `response_format`，并已用项目自身 `ApiWorker` 实测生成 PNG、保存输出路径和写入 `generation_audits`。图片工具全链路实测结果为：846 条知识记录、8 条证据关联、1 条决策审计、1 条生成记录、1 条生成审计。

每次开始新的图片生成时，工作台会从模特变化策略中重新选择中国成年女性模特的脸型气质、表情、发束/发型和妆容，并要求真实皮肤纹理、自然面部轻微不对称、真实眼神与身体比例，抑制塑料皮肤、蜡像脸、过度磨皮和 AI 伪影。同一轮生成和保存生成审计会复用同一组模特特征，避免提示词与审计记录不一致。

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
