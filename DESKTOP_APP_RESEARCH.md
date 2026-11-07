# 秀场礼服设计工作台：桌面化方案调研

## 结论

本项目采用现有 PySide6 + SQLite + requests + PyInstaller，不直接嵌入 Seamly2D、Patro 或 FreeSewing。原因是当前目标是“数据库驱动的秀场礼服决策工作台”，而不是立刻替代完整 CAD；直接引入 GPLv3/AGPLv3 组件会增加分发和许可证边界，且不能自动解决本项目的视觉知识检索、模特造型选择和图片 API 编排问题。代码保留 `pattern_knowledge` 和检索接口，后续可以增加 SVG/DXF/参数化纸样导出适配层。

## 参考项目与采用方式

- PySide6 官方 Qt for Python 项目：作为中文桌面 GUI 基础，保持 Windows/Linux/macOS 的 Qt 控件和线程模型。
  https://github.com/pyside/pyside-setup
- PyInstaller：作为 Windows 独立 EXE 打包工具。官方说明它能把 Python 解释器、依赖和程序打包进单文件，但不能跨平台编译；Windows EXE 必须在 Windows 上运行打包流程。
  https://github.com/pyinstaller/pyinstaller
- PySide6 Windows 打包实践：采用 `--windowed`、`--onefile` 与 `--add-data` 资源打包；数据库在运行时复制到 EXE 旁边，配置和 SQLite 保持可写。
  https://www.pythonguis.com/tutorials/packaging-pyside6-applications-windows-pyinstaller-installforge/
- Seamly2D：成熟的开源纸样工具，支持多尺码和个人量体文件，GPLv3+。本项目只借鉴“量体数据与纸样验证分离”的产品思路，不复制其代码。
  https://github.com/fashionfreedom/seamly2d
- Patro：Python 纸样绘制库，包含几何、纸样、图形和 SVG/DXF 方向能力，AGPLv3。本项目只将其作为未来参数化纸样适配的候选，不作为当前 EXE 的运行依赖。
  https://fabricesalvaire.github.io/Patro/
- FreeSewing：参数化定制服装纸样平台，MIT，但主仓库已迁移到 Codeberg。本项目借鉴“量体参数进入纸样决策”的思路，当前不引入其 Node 运行时。
  https://github.com/freesewing/freesewing
- PyGPT：成熟桌面 AI 工具对 OpenAI-compatible provider 使用独立的 API base、API key、model 配置。本项目采用同样的三项配置方式，并把 key 放在本地 `config.json`，不写进源码。
  https://github.com/szczyglis-dev/py-gpt

## 当前实现

数据库在图片生成前依次参与：礼服证据检索、纸样证据检索、模特造型选择、结构化推荐、风险审查、中文方案提示词、纸样证据写入项目、图片提示词和生成审计。没有纸样证据命中时，图片生成按钮会被硬门槛阻止。

`build.bat` 只使用 ASCII 字符，使用 PyInstaller `--onefile --windowed` 并打包 `show_gown_studio.db` 和 `data` 资源。由于当前执行环境是 Linux，不能在这里声称已经编译出 Windows PE；Windows 用户在仓库目录双击 `build.bat` 即可生成 `release\\ShowGownStudio\\ShowGownStudio.exe`。
