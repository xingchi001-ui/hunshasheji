
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

项目现在支持将本地生成的礼服设计 `segments.jsonl` 导入 SQLite 的 `dress_knowledge` 表，并在生成设计方案前按参考图识别结果、灵感关键词和设计师要求检索候选记录。礼服、纸样和模特造型知识共同参与决策，不是仅供查看的附件。

仓库已内置当前视觉知识库：`show_gown_studio.db` 包含 1063 条逐页视觉理解记录，程序启动时默认读取该数据库。数据库不是静态附件：设计简报会先检索礼服、纸样和模特证据，再生成结构化推荐、来源证据和风险审查，并在保存项目/生成记录时写入全部关联证据与决策包。

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
show_gown_studio.db                         # 1063 条知识记录，应用默认使用
data/dress_training/segments_846.jsonl      # 对应的视觉理解记录
data/dress_training/failed_pages.jsonl      # 尚未成功分析的页面清单
```

数据库链路的实测回归覆盖：SQLite 表初始化、知识检索、结构化推荐、风险审计、项目证据关联、生成审计和 GUI 离屏启动。

图片工具链已针对当前兼容端点完成参数修复：图片请求使用整数 `n=1`，不发送该端点不接受的 `response_format`，并已用项目自身 `ApiWorker` 实测生成 PNG、保存输出路径和写入 `generation_audits`。图片工具全链路实测结果为：1063 条知识记录、8 条证据关联、1 条决策审计、1 条生成记录、1 条生成审计。

模特造型决策也由数据库接管：导入视觉记录后，系统建立 `model_styling_knowledge` 表，从原始逐页证据中结构化提取发型/发束、头饰、表情、视线、姿态、动态和礼服兼容性。图片生成前按礼服检索结果和数据库轮换索引确定性选择候选，提示词严格引用所选来源页和证据 ID；若原始资料没有明确某一字段，明确写为“数据库记录未明确”，不由代码随机编造。真人摄影约束仍负责中国成年女性、真实皮肤纹理、自然面部不对称和抑制 AI 伪影，但不再决定发型或姿态。

图片生成前还会建立 `database_acceptance` 硬验收条件，列出必须在画面中出现的礼服、发饰、姿态和动态证据；图片提示词明确禁止用与数据库指定姿态不一致的正面静态站姿替代。回归测试要求硬验收字段、来源证据和拒绝条件全部存在后才允许进入图片 API。

图片接口使用独立的 `image_timeout_seconds=360`，不再与文字/视觉接口共用 180 秒超时；错误提示会显示实际等待上限。项目 `ApiWorker` 已真实调用 `gpt-image-2.5`，低质量 1024 方图返回约 1.3–1.9 MB 原图，单次测试约 31–35 秒。生成成功后还要经过 MiniMax-M3 图片硬验收，API 成功不等于数据库设计约束满分。

### 《婚纱礼服100款》纸样基础层

新增 `data/dress_training/segments_hunshasheji100.jsonl` 共 217 页，使用 MiniMax-M3 逐页视觉理解，未做 OCR。除原有礼服设计字段外，每页还记录 `pattern_seam_lines`（分割线）、`pattern_darts`（省道）、`pattern_panels`（拼片）、`pattern_layers`（层级）、`pattern_draping_clues`（立体裁剪线索）、`pattern_ease_clues`（放量线索）、`pattern_material_relation`（面料结构关系）、`pattern_process`（纸样工艺顺序）、`pattern_validation`（验证项目）和 `pattern_risk`（纸样风险）。

这些字段导入 SQLite 的 `pattern_knowledge` 表，目前为 217 条记录，并提供 `search_pattern_knowledge()` 检索接口，后续纸样方案可先按廓形、面料、分割线和验证项目召回视觉证据。字段只记录页面可见内容；不可见的量体、尺寸、真实省量、内层支撑和缝份不会被模型臆造，仍需打版师量体、胚布和动态试穿验证。

兼容性回归：

```bash
python tests/test_regression.py
```

## Windows standalone EXE build

### 四阶段可控工作流

当前 GUI 已改为必须逐步确认的单向流程：01“确认灵感方向”后才能检索 02；02“接受数据库决策”后才能进入 03；03“锁定最终设计”后生成根目录 `final_design_spec.json`，04 只读取这份锁定规格；图片返回后还必须点击“图片验收通过”才能保存为最终结果。解锁上游模块会自动撤销下游生成资格，避免旧提示词继续生成。

图片提示词不再直接拼接四个页面的原始内容，而是由 `final_design_spec.json` 和 04 的摄影补充要求组成。数据库证据、纸样证据、模特造型、人工覆盖项、风险审查和来源页都会写入最终规格及生成审计。

03“设计定案”现在展示完整数据库维度，不再只显示廓形、面料和颜色：包括礼服类型、可见部位、廓形、领型、袖型、腰线、裙型、面料、表面细节、结构工艺、场合、色彩、量体线索、设计概念、视觉效果、动态、合体度、风险、灯光、工艺流程、兼容性、设计灵感、视觉证据、不确定性，以及分割线、省道、拼片、层级、立体裁剪、放量、面料结构关系、纸样流程、验证和纸样风险。模特区单独展示发型、头饰、表情、视线、姿态、动态和兼容性，并将可见动作整理成专业术语，例如“行走（走步）”“转身”“坐姿”“侧身”“回眸”“屈膝”“抬臂”“摆动”“展开”等；数据库没有明确的字段会显示“数据库记录未明确”，不会随机补全。

03 锁定后还会生成 `pattern_reference_spec.json`。04 新增“生成打版参考包（14视图）”，先确定一张正面母版，再让背面、左右侧面、行走、45/90 度转身、背影行走、头纱/裙摆展开，以及胸腰、侧腰臀、领口袖窿、裙摆拖尾和内外层结构近景全部引用同一张母版进行图片编辑；不再独立随机生成不同礼服。每张独立图保留原文件，同时用本地确定性拼接生成 `00_overview_contact_sheet.png`；生成失败的视图写入 `pattern_reference_manifest.json`，可以断点补生成。旧版非母版包会在新生成时自动清理。

04 的“打版包输出文件夹”支持自定义路径。图片 API 每成功返回一张，程序立即写入该文件夹对应的 `01_*.png` 至 `14_*.png`，随后才继续下一张；因此中途断电、断网或服务端失败时，已经完成的图片不会丢失，重新生成会自动跳过已有文件。总览图和 `pattern_reference_manifest.json` 在本批次结束后写入同一文件夹。

界面升级：程序启动默认进入“接口设置”页，方便先配置 Base URL、API Key 和模型；顶部加入“针线礼服”品牌 Logo 与英文副标识，采用深梅紫、香槟金和暖米白的专业时装工作室配色，并统一卡片、按钮、输入框、标签页和滚动条的视觉层级。Logo 候选保存在 `assets/`，主 Logo 为 `logo_show_gown_needle.png`。

打包修复：PyInstaller 现在将整个 `assets` 目录嵌入单文件程序，冻结运行时从 `_MEIPASS` 读取 Logo，不再依赖 EXE 外部目录；`build.bat` 同时使用 `assets\show_gown_studio.ico` 作为 Windows EXE 图标，并验证了单文件内部包含 3 个 PNG Logo 和 ICO 图标。

任务栏图标修复：除了设置 PyInstaller 的 EXE 图标，程序现在在 QApplication 和主窗口创建时都显式设置 `show_gown_studio.ico`，确保 Windows 任务栏、窗口标题栏和 Alt+Tab 使用品牌图标，而不是 Qt/Python 默认图标。

02 设计目标已接入真实决策链：例如输入“适合走秀开场”后，程序会识别为“走秀开场”，向本地数据库检索“秀场、走台、行走、动态、轮廓、灯光”等相关证据，并生成“首轮入场识别度、行走/走步、一脚前迈、裙摆不妨碍行走、远距离强轮廓”等目标驱动约束；这些约束会写入数据库决策包，并以硬性要求传给 AI 设计方案，末尾还要求输出目标验收清单。闭幕/压轴目标会切换为转身、回眸、拖尾展开等另一组约束。

On Windows, double-click the ASCII-only `build.bat`. It creates a virtual environment, installs dependencies, runs PyInstaller, bundles the SQLite database and local visual-data folder, and generates:

```text
release/ShowGownStudio/ShowGownStudio.exe
```

The GUI remains Chinese. Copy `config.example.json` to `config.json` beside the EXE and fill in the API key. Then double-click the ASCII-only `start.bat` to launch the app. The first run copies the bundled SQLite database beside the EXE so projects and audit records remain writable. The API base URL defaults to `https://api.llm-token.cn/v1`; the key is never committed to Git.

Optional local training data path:

```text
data/dress_training/segments.jsonl
```

Original PDFs and complete book text are not included in the EXE package.
