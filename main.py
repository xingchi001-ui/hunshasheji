import base64
import html
import json
import mimetypes
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import requests
from dress_knowledge import ensure_schema, import_segments, rebuild_model_styling_knowledge, rebuild_pattern_knowledge, search_segments, search_model_styling, search_pattern_knowledge, format_context, recommend_design, audit_design, format_decision_package
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPushButton, QSpinBox, QDoubleSpinBox, QTabWidget, QTextEdit,
    QVBoxLayout, QWidget, QGridLayout
)

APP_NAME = "秀场礼服设计工作台"
ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))
CONFIG_PATH = ROOT / "config.json"
DB_PATH = ROOT / "show_gown_studio.db"
TRAINING_DATA_PATH = Path(os.getenv("DRESS_TRAINING_DATA", ROOT / "data" / "dress_training" / "segments_all.jsonl"))


def bootstrap_runtime_files():
    """Copy read-only PyInstaller resources beside the EXE for a writable local workspace."""
    if not getattr(sys, "frozen", False):
        return
    for relative in (Path("show_gown_studio.db"), Path("data") / "dress_training" / "segments_all.jsonl"):
        target = ROOT / relative
        source = RESOURCE_ROOT / relative
        if not target.exists() and source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())


bootstrap_runtime_files()


def default_config():
    return {
        "api_base_url": "https://api.llm-token.cn/v1",
        "api_key": "",
        "vision_model": "MiniMax-M3",
        "text_model": "deepseek-v4.1-flash",
        "image_model": "gpt-image-2.5",
        "timeout_seconds": 180,
        "dress_training_data": str(TRAINING_DATA_PATH),
    }


def load_config():
    data = default_config()
    if CONFIG_PATH.exists():
        try:
            data.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    data["api_key"] = os.getenv("LLM_TOKEN_API_KEY", data.get("api_key", ""))
    data["api_base_url"] = os.getenv("LLM_TOKEN_BASE_URL", data.get("api_base_url", data["api_base_url"]))
    return data


def save_config(data):
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_raw_image(path):
    """Read original bytes only; no image decode, resize, crop, or pixel processing."""
    raw = Path(path).read_bytes()
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return mime, base64.b64encode(raw).decode("ascii")


class ProjectDB:
    def __init__(self, path=DB_PATH):
        self.path = path
        self.init_schema()

    def connect(self):
        return sqlite3.connect(self.path)

    def init_schema(self):
        with self.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                analysis_json TEXT DEFAULT '',
                design_plan TEXT DEFAULT '',
                parameters_json TEXT DEFAULT '',
                notes TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS reference_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER,
                path TEXT NOT NULL,
                label TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS generation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER,
                model TEXT,
                prompt TEXT,
                output_path TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS project_knowledge_links (
                project_id INTEGER NOT NULL,
                segment_id TEXT NOT NULL,
                rank INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(project_id, segment_id)
            );
            CREATE TABLE IF NOT EXISTS project_pattern_links (
                project_id INTEGER NOT NULL,
                knowledge_id TEXT NOT NULL,
                rank INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(project_id, knowledge_id)
            );
            CREATE TABLE IF NOT EXISTS project_decision_audits (
                project_id INTEGER PRIMARY KEY,
                recommendations_json TEXT NOT NULL,
                audit_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS generation_audits (
                generation_id INTEGER PRIMARY KEY,
                project_id INTEGER,
                knowledge_ids_json TEXT NOT NULL,
                decision_package_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """)
            ensure_schema(c)
            rebuild_model_styling_knowledge(c)
            rebuild_pattern_knowledge(c)

    def save(self, data, references, knowledge_matches=None, recommendations=None, audit=None, pattern_matches=None):
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as c:
            cursor = c.execute("INSERT INTO projects(name,created_at,updated_at,analysis_json,design_plan,parameters_json,notes) VALUES(?,?,?,?,?,?,?)", (
                data["project_name"], now, now, json.dumps(data["analysis"], ensure_ascii=False), data["design_plan"], json.dumps(data["parameters"], ensure_ascii=False), data["notes"]))
            project_id = cursor.lastrowid
            for path in references:
                c.execute("INSERT INTO reference_assets(project_id,path,label,created_at) VALUES(?,?,?,?)", (project_id, path, Path(path).name, now))
            for rank, row in enumerate(knowledge_matches or [], start=1):
                c.execute("INSERT OR REPLACE INTO project_knowledge_links(project_id,segment_id,rank,created_at) VALUES(?,?,?,?)", (project_id, row["id"], rank, now))
            for rank, row in enumerate(pattern_matches or [], start=1):
                c.execute("INSERT OR REPLACE INTO project_pattern_links(project_id,knowledge_id,rank,created_at) VALUES(?,?,?,?)", (project_id, row["knowledge_id"], rank, now))
            if recommendations is not None or audit is not None:
                c.execute("INSERT OR REPLACE INTO project_decision_audits(project_id,recommendations_json,audit_json,created_at) VALUES(?,?,?,?)", (project_id, json.dumps(recommendations or {}, ensure_ascii=False), json.dumps(audit or {}, ensure_ascii=False), now))
            return project_id

    def record_generation(self, project_id, model, prompt, output_path, decision_package=None, knowledge_ids=None):
        with self.connect() as c:
            c.execute("INSERT INTO generation_runs(project_id,model,prompt,output_path,created_at) VALUES(?,?,?,?,?)", (project_id, model, prompt, output_path, datetime.now().isoformat(timespec="seconds")))
            generation_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.execute("INSERT INTO generation_audits(generation_id,project_id,knowledge_ids_json,decision_package_json,created_at) VALUES(?,?,?,?,?)", (generation_id, project_id, json.dumps(knowledge_ids or [], ensure_ascii=False), json.dumps(decision_package or {}, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")))

    def import_dress_training_data(self, jsonl_path, replace=False):
        with self.connect() as c:
            return import_segments(c, jsonl_path, replace=replace)

    def search_dress_knowledge(self, query, limit=8):
        with self.connect() as c:
            return search_segments(c, query, limit=limit)

    def search_model_styling(self, query, recommendations, limit=8):
        with self.connect() as c:
            rotation_index = c.execute("SELECT COUNT(*) FROM generation_runs").fetchone()[0]
            return search_model_styling(c, query, recommendations, rotation_index=rotation_index, limit=limit)

    def search_pattern_knowledge(self, query, limit=8):
        with self.connect() as c:
            return search_pattern_knowledge(c, query, limit=limit)


class ApiWorker(QThread):
    done = Signal(object, str, bool)
    log = Signal(str)

    def __init__(self, config, operation, payload):
        super().__init__()
        self.config, self.operation, self.payload = config, operation, payload

    def run(self):
        try:
            self.log.emit(f"开始调用：{self.operation}")
            result = self.call_api()
            self.log.emit(f"调用完成：返回对象类型={type(result).__name__}，长度={len(result) if hasattr(result, '__len__') else '未知'}")
            self.done.emit(result, "", True)
        except requests.exceptions.ReadTimeout:
            self.log.emit("错误：读取超时，服务端在规定时间内未返回")
            self.done.emit(None, "API 在规定时间内未返回。图片生成允许等待 180 秒；多模态分析建议检查模型和网络。", False)
        except requests.exceptions.HTTPError as e:
            detail = e.response.text[:2000] if e.response is not None else str(e)
            status = e.response.status_code if e.response is not None else "未知"
            self.log.emit(f"HTTP 错误：状态码={status}，响应={detail}")
            self.done.emit(None, f"API HTTP 错误：{detail}", False)
        except Exception as e:
            self.log.emit(f"异常：{type(e).__name__}: {e}")
            self.done.emit(None, str(e), False)

    def headers(self, json_body=False):
        h = {"Authorization": f"Bearer {self.config['api_key']}"}
        if json_body:
            h["Content-Type"] = "application/json"
        return h

    def call_api(self):
        base = self.config["api_base_url"].rstrip("/")
        timeout = int(self.config.get("timeout_seconds", 180))
        if self.operation == "chat":
            self.log.emit(f"请求端点：{base}/chat/completions")
            self.log.emit(f"请求模型：{self.payload.get('model')}，消息数={len(self.payload.get('messages', []))}")
            r = requests.post(base + "/chat/completions", headers=self.headers(True), json=self.payload, timeout=timeout)
            self.log.emit(f"HTTP 返回：{r.status_code}，响应字节={len(r.content)}，Content-Type={r.headers.get('content-type', '')}")
            r.raise_for_status()
            data = r.json()
            choices = data.get("choices") or []
            content = choices[0].get("message", {}).get("content", "") if choices else ""
            choice = choices[0] if choices else {}
            message = choice.get("message", {})
            reasoning = message.get("reasoning", message.get("reasoning_content", "")) or ""
            self.log.emit(f"响应结构：choices={len(choices)}，finish_reason={choice.get('finish_reason', '无')}，文本长度={len(content)}，推理字段长度={len(reasoning)}，request_id={data.get('id', '无')}")
            if not content:
                self.log.emit(f"空内容响应摘要：{json.dumps(data, ensure_ascii=False)[:2000]}")
            return content
        if self.operation == "image":
            model = self.config.get("image_model") or "gpt-image-2.5"
            prompt = self.payload["prompt"]
            refs = self.payload.get("references", [])
            # The connected image endpoint accepts integer n and returns b64_json by default;
            # response_format is not supported on this route and caused HTTP 500 responses.
            common = {"model": model, "prompt": prompt, "size": self.payload.get("size", "1024x1024"), "quality": self.payload.get("quality", "low"), "n": 1}
            if refs:
                self.log.emit(f"请求端点：{base}/images/edits；模型={model}；原始参考图数量={len(refs)}")
                files = []
                try:
                    for path in refs:
                        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
                        files.append(("image[]", (Path(path).name, open(path, "rb"), mime)))
                    r = requests.post(base + "/images/edits", headers=self.headers(False), data=common, files=files, timeout=timeout)
                finally:
                    for _, item in files:
                        item[1].close()
            else:
                self.log.emit(f"请求端点：{base}/images/generations；模型={model}；无参考图")
                r = requests.post(base + "/images/generations", headers=self.headers(True), json=common, timeout=timeout)
            self.log.emit(f"图片 API 返回：{r.status_code}，响应字节={len(r.content)}，Content-Type={r.headers.get('content-type', '')}")
            r.raise_for_status()
            data = r.json()
            item = (data.get("data") or [{}])[0]
            if item.get("b64_json"):
                self.log.emit("图片响应包含 b64_json")
                return base64.b64decode(item["b64_json"])
            if item.get("url"):
                self.log.emit("图片响应包含 url，开始下载原图")
                image = requests.get(item["url"], timeout=timeout)
                image.raise_for_status()
                self.log.emit(f"原图下载完成：字节={len(image.content)}")
                return image.content
            self.log.emit(f"图片响应结构异常：{json.dumps(data, ensure_ascii=False)[:2000]}")
            raise RuntimeError("图片 API 返回中没有 b64_json 或 url")
        raise RuntimeError("未知 API 操作")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.db = ProjectDB()
        self.ensure_dress_training_data()
        self.knowledge_matches = []
        self.design_recommendations = {}
        self.design_audit = {}
        self.model_styling_decision = {}
        self.pattern_matches = []
        self.current_project_id = None
        self.references = []
        self.analysis = {}
        self.design_plan = ""
        self.generated_bytes = b""
        self._last_image_prompt = ""
        self.worker = None
        self.setWindowTitle(APP_NAME)
        self.resize(1450, 920)
        self.setStyleSheet(self.styles())
        self.build_ui()
        self.update_steps()

    def ensure_dress_training_data(self):
        path = Path(self.config.get("dress_training_data", TRAINING_DATA_PATH)).expanduser()
        if not path.exists():
            self._training_data_status = "未配置本地礼服知识库"
            return
        try:
            with self.db.connect() as connection:
                count = connection.execute("SELECT COUNT(*) FROM dress_knowledge").fetchone()[0]
            if count == 0:
                imported = self.db.import_dress_training_data(path)
                self._training_data_status = f"已载入礼服知识库 {imported} 条"
            else:
                self._training_data_status = f"礼服知识库已就绪 {count} 条"
        except Exception as exc:
            self._training_data_status = f"礼服知识库未载入：{exc}"

    def styles(self):
        return """
        QWidget { font-family: 'Microsoft YaHei'; font-size: 13px; color: #28252a; }
        QMainWindow, QTabWidget::pane { background: #f7f4ef; }
        QGroupBox { background: #fffdf9; border: 1px solid #ddd4c8; border-radius: 10px; margin-top: 12px; padding: 14px; font-weight: bold; }
        QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; color: #6d4e4a; }
        QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: white; border: 1px solid #d5cbc0; border-radius: 6px; padding: 7px; }
        QPushButton { background: #6d4e4a; color: white; border: 0; border-radius: 6px; padding: 9px 14px; }
        QPushButton:hover { background: #8b625b; }
        QPushButton[secondary="true"] { background: #e7ded4; color: #4d403e; }
        QTabBar::tab { background: #e9e1d8; padding: 11px 20px; margin-right: 3px; border-radius: 6px; }
        QTabBar::tab:selected { background: #6d4e4a; color: white; }
        QListWidget { background: #fffdf9; border: 1px solid #ddd4c8; border-radius: 8px; }
        QLabel#title { font-size: 22px; font-weight: bold; color: #4e3535; }
        QLabel#muted { color: #81766d; }
        QLabel#step { font-size: 12px; color: #6d4e4a; padding: 7px; }
        """

    def build_ui(self):
        root = QWidget(); self.setCentralWidget(root); outer = QVBoxLayout(root)
        head = QHBoxLayout(); title = QLabel(APP_NAME); title.setObjectName("title"); head.addWidget(title); head.addStretch(); self.status = QLabel("全链路本地工作区"); self.status.setObjectName("muted"); head.addWidget(self.status); outer.addLayout(head)
        steps = QHBoxLayout()
        self.step_labels = []
        for text in ["01 灵感与参考", "02 AI 设计助理", "03 设计工作台", "04 API 效果图"]:
            label = QLabel(text); label.setObjectName("step"); self.step_labels.append(label); steps.addWidget(label)
        outer.addLayout(steps)
        self.tabs = QTabWidget(); outer.addWidget(self.tabs)
        self.tabs.addTab(self.reference_tab(), "01 灵感与参考")
        self.tabs.addTab(self.assistant_tab(), "02 AI 设计助理")
        self.tabs.addTab(self.workbench_tab(), "03 设计工作台")
        self.tabs.addTab(self.image_tab(), "04 API 效果图")
        self.tabs.addTab(self.settings_tab(), "接口设置")
        bottom = QHBoxLayout(); bottom.addStretch(); save = QPushButton("保存到 SQLite"); save.clicked.connect(self.save_project); bottom.addWidget(save); export = QPushButton("导出设计单"); export.clicked.connect(self.export_brief); bottom.addWidget(export); outer.addLayout(bottom)
        self.tabs.currentChanged.connect(self.update_steps)

    def reference_tab(self):
        tab = QWidget(); layout = QHBoxLayout(tab)
        left = QVBoxLayout(); box = QGroupBox("灵感与参考图"); bv = QVBoxLayout(box); add = QPushButton("导入参考图"); add.clicked.connect(self.add_reference); bv.addWidget(add); self.ref_list = QListWidget(); bv.addWidget(self.ref_list); left.addWidget(box)
        self.keywords = QTextEdit("午夜花园\n液态金属\n雕塑感肩线\n轻盈透明层次\n远看强轮廓，近看细工艺"); left.addWidget(QLabel("灵感关键词")); left.addWidget(self.keywords); layout.addLayout(left, 1)
        right = QVBoxLayout(); intro = QLabel("第一步：上传参考图后，让多模态模型识别色彩、廓形、面料、工艺、氛围、构图和设计平台维度。程序只读取原始字节并通过 API 发送，不做本地图像识别或静态图像处理。") ; intro.setWordWrap(True); intro.setObjectName("muted"); right.addWidget(intro); self.analyze_btn = QPushButton("调用多模态模型识别参考图"); self.analyze_btn.clicked.connect(self.analyze_references); right.addWidget(self.analyze_btn); self.analysis_output = QTextEdit(); self.analysis_output.setPlaceholderText("多模态识别结果会以 JSON/中文摘要显示在这里，可人工修改后进入下一步。"); right.addWidget(self.analysis_output, 2); right.addWidget(QLabel("实时 API 日志（不记录 API Key）")); self.live_log = QTextEdit(); self.live_log.setReadOnly(True); self.live_log.setPlaceholderText("点击识别后，这里会显示端点、模型、HTTP 状态、响应结构和具体错误。"); self.live_log.setMaximumHeight(190); right.addWidget(self.live_log); layout.addLayout(right, 2); return tab

    def assistant_tab(self):
        tab = QWidget(); layout = QVBoxLayout(tab); intro = QLabel("第二步：数据库先形成设计决策包，再由 AI 编排原创方案。生成后请查看结构化推荐与风险审查。"); intro.setObjectName("muted"); layout.addWidget(intro); self.brief_input = QTextEdit(); self.brief_input.setPlaceholderText("补充设计目标，例如：希望更适合闭幕秀，强调远距离轮廓和灯光下的闪烁层次……"); layout.addWidget(self.brief_input); row = QHBoxLayout(); self.knowledge_btn = QPushButton("检索并生成设计决策包"); self.knowledge_btn.clicked.connect(self.refresh_knowledge_context); row.addWidget(self.knowledge_btn); self.plan_btn = QPushButton("生成 AI 设计方案"); self.plan_btn.clicked.connect(self.generate_plan); row.addWidget(self.plan_btn); apply = QPushButton("将方案写入设计工作台"); apply.setProperty("secondary", True); apply.clicked.connect(self.apply_plan); row.addWidget(apply); row.addStretch(); layout.addLayout(row); self.knowledge_output = QTextEdit(); self.knowledge_output.setReadOnly(True); self.knowledge_output.setPlaceholderText("这里显示分层检索、结构化推荐、来源证据和风险审查。"); layout.addWidget(QLabel("数据库设计决策包：推荐、证据与审查")); layout.addWidget(self.knowledge_output, 2); self.plan_output = QTextEdit(); self.plan_output.setPlaceholderText("AI 设计助理输出：概念、廓形、面料、工艺、秀场动态、试衣检查和图片生成提示词。"); layout.addWidget(self.plan_output, 2); return tab

    def workbench_tab(self):
        tab = QWidget(); layout = QHBoxLayout(tab); left = QVBoxLayout(); meta = QGroupBox("项目与结构化参数"); mf = QFormLayout(meta); self.project_name = QLineEdit("2026 春夏 · 午夜花园"); self.client = QLineEdit("Look 01"); self.occasion = QLineEdit("品牌发布秀 / 闭幕造型"); mf.addRow("项目名称", self.project_name); mf.addRow("造型编号", self.client); mf.addRow("使用场景", self.occasion); left.addWidget(meta)
        params = QGroupBox("设计工作台参数"); pf = QFormLayout(params); self.silhouette = self.combo(["A字", "鱼尾", "蓬蓬", "直身"]); self.neckline = self.combo(["心形", "一字肩", "高领", "深V", "不对称"]); self.sleeve = self.combo(["无袖", "泡泡袖", "长袖", "披帛", "手套袖"]); self.fabric = self.combo(["缎面", "欧根纱", "天鹅绒", "亮片", "蕾丝", "金属丝"]); self.main_color = self.combo(["黑色", "酒红", "深海蓝", "古金", "象牙白", "灰粉"]); self.accent = self.combo(["古金", "银色", "珍珠白", "深紫", "黑色"]); self.train = self.combo(["无拖尾", "短拖尾", "中拖尾", "超长拖尾"]); self.craft = QLineEdit("珠绣、立体花饰、金属刺绣"); self.work_notes = QTextEdit(); self.work_notes.setPlaceholderText("确认后的版型、工艺、搭配和试衣备注"); pf.addRow("主廓形", self.silhouette); pf.addRow("领型", self.neckline); pf.addRow("袖型", self.sleeve); pf.addRow("面料", self.fabric); pf.addRow("主色", self.main_color); pf.addRow("点缀色", self.accent); pf.addRow("拖尾", self.train); pf.addRow("工艺", self.craft); pf.addRow("确认备注", self.work_notes); left.addWidget(params)
        measures = QGroupBox("量体数据"); grid = QGridLayout(measures); self.height = self.measure(175); self.bust = self.measure(84); self.waist = self.measure(64); self.hip = self.measure(90); widgets = [("身高", self.height), ("胸围", self.bust), ("腰围", self.waist), ("臀围", self.hip)];
        for i, (name, widget) in enumerate(widgets): grid.addWidget(QLabel(name), i // 2, (i % 2) * 2); grid.addWidget(widget, i // 2, (i % 2) * 2 + 1)
        left.addWidget(measures); layout.addLayout(left, 1)
        right = QVBoxLayout(); info = QLabel("第三步：AI 方案落地为可修改的数据库参数。只有确认这里的结构化参数后，第四步才会调用图片 API。") ; info.setWordWrap(True); info.setObjectName("muted"); right.addWidget(info); self.knowledge_status = QLabel("知识库链路：等待检索"); self.knowledge_status.setObjectName("muted"); right.addWidget(self.knowledge_status); self.parameter_json = QTextEdit(); self.parameter_json.setReadOnly(True); right.addWidget(QLabel("当前参数 JSON")); right.addWidget(self.parameter_json, 2); refresh = QPushButton("刷新结构化参数"); refresh.clicked.connect(self.refresh_parameter_json); right.addWidget(refresh); layout.addLayout(right, 1); return tab

    def image_tab(self):
        tab = QWidget(); layout = QVBoxLayout(tab); intro = QLabel("第四步：根据已确认的设计工作台参数、AI 方案、灵感关键词和原始参考图调用图片 API。无参考图调用 generations，有参考图调用 edits。") ; intro.setWordWrap(True); intro.setObjectName("muted"); layout.addWidget(intro); controls = QGroupBox("效果图设置"); cf = QFormLayout(controls); self.image_extra = QTextEdit(); self.image_extra.setPlaceholderText("补充视角、舞台灯光、模特姿态、摄影风格等"); self.image_size = self.combo(["1024x1536", "1024x1024", "1536x1024"]); self.image_quality = self.combo(["low", "medium", "high"]); cf.addRow("补充提示词", self.image_extra); cf.addRow("画幅", self.image_size); cf.addRow("质量", self.image_quality); layout.addWidget(controls); row = QHBoxLayout(); self.generate_btn = QPushButton("调用 gpt-image-2.5 生成效果图"); self.generate_btn.clicked.connect(self.generate_image); row.addWidget(self.generate_btn); self.save_image_btn = QPushButton("保存 API 原图"); self.save_image_btn.setProperty("secondary", True); self.save_image_btn.setEnabled(False); self.save_image_btn.clicked.connect(self.save_generated_image); row.addWidget(self.save_image_btn); row.addStretch(); layout.addLayout(row); self.generated_label = QLabel("API 返回的原图将在这里显示"); self.generated_label.setAlignment(Qt.AlignCenter); self.generated_label.setMinimumHeight(420); self.generated_label.setStyleSheet("background:#fffdf9;border:1px solid #ddd4c8;border-radius:8px;color:#81766d;"); layout.addWidget(self.generated_label, 1); return tab

    def settings_tab(self):
        tab = QWidget(); layout = QVBoxLayout(tab); box = QGroupBox("API 设置"); form = QFormLayout(box); self.base_url = QLineEdit(self.config["api_base_url"]); self.api_key = QLineEdit(self.config.get("api_key", "")); self.api_key.setEchoMode(QLineEdit.Password); self.vision_model = self.model_combo(["MiniMax-M3.1", "qwen3.7-plus", "qwen3.8-max", "glm-5.3-flash"] , self.config.get("vision_model", "MiniMax-M3.1")); self.text_model = self.model_combo(["deepseek-v4.1-flash", "deepseek-v4-flash", "qwen3.7-plus", "MiniMax-M3.1-flash", "glm-5.3-flash", "kimi-k2.8"], self.config.get("text_model", "deepseek-v4.1-flash")); self.image_model = self.model_combo(["gpt-image-2.5", "gpt-image-2", "gpt-image-2.5-sunburst", "gpt-image-2.5-flare"], self.config.get("image_model", "gpt-image-2.5")); form.addRow("Base URL", self.base_url); form.addRow("API Key", self.api_key); form.addRow("多模态模型", self.vision_model); form.addRow("文字模型", self.text_model); form.addRow("图片模型", self.image_model); layout.addWidget(box); row = QHBoxLayout(); save = QPushButton("保存 API 设置"); save.clicked.connect(self.save_settings); row.addWidget(save); test = QPushButton("测试文字 API"); test.setProperty("secondary", True); test.clicked.connect(self.test_text_api); row.addWidget(test); row.addStretch(); layout.addLayout(row); note = QLabel("API Key 只写入当前目录 config.json；参考图原始字节直接发送到 API，生成图片直接保存 API 返回内容。") ; note.setWordWrap(True); note.setObjectName("muted"); layout.addWidget(note); layout.addStretch(); return tab

    def combo(self, items):
        c = QComboBox(); c.addItems(items); return c

    def model_combo(self, items, current):
        c = QComboBox(); c.setEditable(True); c.addItems(items); c.setCurrentText(current); return c

    def measure(self, value):
        s = QDoubleSpinBox(); s.setRange(40, 240); s.setValue(value); s.setSuffix(" cm"); return s

    def add_reference(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择参考图", "", "Images (*.png *.jpg *.jpeg *.webp)")
        for path in paths:
            self.references.append(path); item = QListWidgetItem(Path(path).name); item.setToolTip(path); self.ref_list.addItem(item)
        self.update_steps()

    def save_settings(self):
        self.config.update({"api_base_url": self.base_url.text().strip(), "api_key": self.api_key.text().strip(), "vision_model": self.vision_model.currentText().strip(), "text_model": self.text_model.currentText().strip(), "image_model": self.image_model.currentText().strip()}); save_config(self.config); self.status.setText("接口设置已保存到 config.json"); QMessageBox.information(self, "已保存", "API 设置已保存到当前目录 config.json。")

    def save_settings_silent(self):
        self.config.update({"api_base_url": self.base_url.text().strip(), "api_key": self.api_key.text().strip(), "vision_model": self.vision_model.currentText().strip(), "text_model": self.text_model.currentText().strip(), "image_model": self.image_model.currentText().strip()}); save_config(self.config)

    def start_chat(self, messages, callback, model=None):
        self.save_settings_silent()
        if not self.config.get("api_key"):
            QMessageBox.warning(self, "缺少 API Key", "请先在接口设置中填写 API Key。"); self.tabs.setCurrentIndex(4); return
        selected_model = model or self.config.get("text_model") or "deepseek-v4.1-flash"
        payload = {"model": selected_model, "messages": messages, "temperature": 0.7, "max_completion_tokens": 8000}
        model_key = selected_model.lower().replace("-", "").replace(".", "")
        if model_key in ("minimaxm31", "minimaxm31flash"):
            payload["reasoning_effort"] = "low"
        self.append_log(f"准备请求模型：{payload['model']}，max_completion_tokens=8000，reasoning_effort={payload.get('reasoning_effort', '默认')}")
        self.worker = ApiWorker(self.config, "chat", payload); self.worker.log.connect(self.append_log); self.worker.done.connect(callback); self.worker.start()

    def append_log(self, message):
        if hasattr(self, "live_log"):
            stamp = datetime.now().strftime("%H:%M:%S")
            self.live_log.append(f"[{stamp}] {message}")

    def analyze_references(self):
        if not self.references: QMessageBox.warning(self, "还没有参考图", "请先导入至少一张参考图。"); return
        parts = [{"type": "text", "text": "你是一名高级定制服装与秀场设计研究员。请识别这些参考图，输出严格 JSON 后再给中文摘要。提取：主题、色彩、廓形、领型、袖型、面料、工艺、纹理、氛围、舞台灯光、构图与视角、适合借鉴的设计平台维度、不可直接复制的品牌或文字元素。"}]
        for path in self.references:
            mime, encoded = read_raw_image(path); parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
        self.live_log.clear(); self.append_log(f"参考图数量：{len(self.references)}；多模态模型：{self.config.get('vision_model') or 'MiniMax-M3.1'}"); self.analyze_btn.setEnabled(False); self.analyze_btn.setText("多模态识别中……"); self.start_chat([{"role": "user", "content": parts}], self.analysis_done, self.config.get("vision_model") or "MiniMax-M3.1")

    def analysis_done(self, result, error, ok):
        self.analyze_btn.setEnabled(True); self.analyze_btn.setText("调用多模态模型识别参考图")
        if not ok: QMessageBox.critical(self, "多模态识别失败", error); return
        if not isinstance(result, str) or not result.strip():
            self.append_log("错误：API 请求成功但返回文本为空，第一步不标记为完成")
            QMessageBox.critical(self, "多模态识别失败", "API 返回为空。请查看实时 API 日志，并核对模型是否支持图片输入。")
            return
        self.analysis_output.setPlainText(result); self.analysis = {"raw": result}; self.status.setText("第一步已完成：参考图识别结果已生成"); self.update_steps()

    def generate_plan(self):
        analysis = self.analysis_output.toPlainText().strip() or "尚未完成参考图识别，请基于用户关键词先给出初步方案。"
        context = self.refresh_knowledge_context()
        package = format_decision_package(self.design_recommendations, self.design_audit)
        prompt = f"你是一名资深秀场礼服设计总监。根据参考图识别、设计师要求和下方设计决策包，生成原创且可执行的中文礼服设计方案。必须包含：设计概念、廓形比例、领袖结构、面料色彩、工艺拆解、秀场灯光与动态、量体/打版/试衣检查、给图片模型的视觉提示词。必须逐项回应风险审查；不要照抄来源原文，区分候选证据与最终判断。\n参考识别：{analysis}\n灵感关键词：{self.keywords.toPlainText()}\n补充要求：{self.brief_input.toPlainText()}\n{context}\n结构化设计决策包：\n{package}"
        self.plan_btn.setEnabled(False); self.plan_btn.setText("AI 方案生成中……"); self.start_chat([{"role": "user", "content": prompt}], self.plan_done)

    def refresh_knowledge_context(self):
        query = " ".join([self.analysis_output.toPlainText(), self.keywords.toPlainText(), self.brief_input.toPlainText()])
        self.knowledge_matches = self.db.search_dress_knowledge(query, limit=8)
        self.pattern_matches = self.db.search_pattern_knowledge(query, limit=8)
        context = format_context(self.knowledge_matches) if self.knowledge_matches else "本地礼服知识库暂无匹配记录；请基于通用服装设计原则完成方案。"
        self.design_recommendations = recommend_design(self.knowledge_matches, query)
        self.model_styling_decision = self.db.search_model_styling(query, self.design_recommendations, limit=8)
        self.design_recommendations["model_styling_decision"] = self.model_styling_decision
        self.design_recommendations["pattern_decision"] = {
            "status": "selected_from_database" if self.pattern_matches else "no_database_evidence",
            "decision_source": "SQLite pattern_knowledge",
            "evidence_ids": [row["knowledge_id"] for row in self.pattern_matches],
            "source_pages": [f"{row['book_id']} p.{row['page']}" for row in self.pattern_matches],
            "seam_lines": [row["seam_lines"] for row in self.pattern_matches if row["seam_lines"]][:6],
            "darts": [row["darts"] for row in self.pattern_matches if row["darts"]][:6],
            "panels": [row["panels"] for row in self.pattern_matches if row["panels"]][:6],
            "layers": [row["layers"] for row in self.pattern_matches if row["layers"]][:6],
            "draping_clues": [row["draping_clues"] for row in self.pattern_matches if row["draping_clues"]][:6],
            "ease_clues": [row["ease_clues"] for row in self.pattern_matches if row["ease_clues"]][:6],
            "validation": [row["validation"] for row in self.pattern_matches if row["validation"]][:6],
            "risk": [row["risk"] for row in self.pattern_matches if row["risk"]][:6],
            "must_review_with_patternmaker": True,
        }
        self.design_audit = audit_design(self.parameters(), self.design_recommendations)
        decision_text = format_decision_package(self.design_recommendations, self.design_audit)
        if hasattr(self, "knowledge_output"):
            self.knowledge_output.setPlainText(decision_text + "\n\n【来源证据】\n" + context)
        if hasattr(self, "knowledge_status"):
            self.knowledge_status.setText(f"知识库链路：已命中礼服 {len(self.knowledge_matches)} 条 · 纸样 {len(self.pattern_matches)} 条 · 模特造型已接管")
        self.append_log(f"数据库决策：礼服={len(self.knowledge_matches)}，纸样={len(self.pattern_matches)}，模特造型=1")
        styling_text = "\n数据库模特造型决策：\n" + json.dumps(self.model_styling_decision, ensure_ascii=False, indent=2)
        pattern_text = "\n数据库纸样决策证据：\n" + json.dumps(self.design_recommendations.get("pattern_decision", {}), ensure_ascii=False, indent=2)
        return context + styling_text + pattern_text + "\n结构化设计决策包：\n" + decision_text

    def plan_done(self, result, error, ok):
        self.plan_btn.setEnabled(True); self.plan_btn.setText("生成 AI 设计方案")
        if not ok: QMessageBox.critical(self, "设计方案失败", error); return
        self.design_plan = result; self.plan_output.setPlainText(result); self.status.setText("第二步已完成：AI 设计方案已生成"); self.update_steps()

    def apply_plan(self):
        text = self.plan_output.toPlainText().strip()
        if not text: QMessageBox.warning(self, "没有设计方案", "请先生成或填写 AI 设计方案。"); return
        self.design_plan = text; self.status.setText("方案已写入工作台，可继续确认参数"); self.tabs.setCurrentIndex(2); self.refresh_parameter_json(); self.update_steps()

    def parameters(self):
        return {"silhouette": self.silhouette.currentText(), "neckline": self.neckline.currentText(), "sleeve": self.sleeve.currentText(), "fabric": self.fabric.currentText(), "main_color": self.main_color.currentText(), "accent_color": self.accent.currentText(), "train": self.train.currentText(), "craft": self.craft.text(), "measurements": {"height_cm": self.height.value(), "bust_cm": self.bust.value(), "waist_cm": self.waist.value(), "hip_cm": self.hip.value()}}

    def refresh_parameter_json(self):
        data = self.parameters(); self.parameter_json.setPlainText(json.dumps(data, ensure_ascii=False, indent=2)); self.update_steps()

    def collect_data(self):
        return {"project_name": self.project_name.text(), "analysis": self.analysis, "design_plan": self.plan_output.toPlainText(), "parameters": self.parameters(), "notes": self.work_notes.toPlainText()}

    def image_prompt(self):
        d = self.collect_data(); styling = json.dumps(self.model_styling_decision, ensure_ascii=False, indent=2); pattern = json.dumps(self.design_recommendations.get("pattern_decision", {}), ensure_ascii=False, indent=2); acceptance = json.dumps(self.model_styling_decision.get("database_acceptance", {}), ensure_ascii=False, indent=2); return f"高级时装秀场礼服效果图。项目：{d['project_name']}。参考图识别摘要：{self.analysis_output.toPlainText()}。AI 设计方案：{d['design_plan']}。最终确认工作台参数：{json.dumps(d['parameters'], ensure_ascii=False)}。数据库设计决策包：{format_decision_package(self.design_recommendations, self.design_audit)}。数据库模特造型决策：{styling}。数据库纸样决策证据：{pattern}。数据库图片验收硬约束：{acceptance}。灵感关键词：{self.keywords.toPlainText()}。补充要求：{self.image_extra.toPlainText()}。必须是中国成年女性真人时装摄影感，严格遵守数据库选择的发型、头饰、表情、视线、姿态与动态；必须让数据库验收条件在画面中清晰可见，不得用与数据库指定姿态不一致的正面静态站姿替代；面部自然有轻微不对称，真实皮肤纹理和毛孔，真实眼神、手部和身体比例，妆容克制并与礼服风格协调；避免塑料皮肤、蜡像脸、过度磨皮、诡异眼神、僵硬表情、重复脸、AI伪影。请生成全身专业时装视觉，完整表现礼服结构、比例、面料层次、工艺细节、秀场灯光和全身造型；遵守审查风险并避免不安全结构；不要文字、不要水印、不要品牌标识。"

    def generate_image(self):
        self.refresh_knowledge_context()
        self.refresh_parameter_json()
        if not self.plan_output.toPlainText().strip(): QMessageBox.warning(self, "尚未完成第二步", "请先生成或填写 AI 设计方案。"); self.tabs.setCurrentIndex(1); return
        if not self.pattern_matches:
            QMessageBox.warning(self, "纸样证据未命中", "当前设计没有命中数据库纸样证据，不能进入图片生成。请补充廓形、面料、分割线或纸样关键词后重新检索。")
            self.tabs.setCurrentIndex(1); return
        if not self.config.get("api_key"): QMessageBox.warning(self, "缺少 API Key", "请先在接口设置中填写 API Key。"); self.tabs.setCurrentIndex(4); return
        self._last_image_prompt = self.image_prompt(); payload = {"prompt": self._last_image_prompt, "references": self.references, "size": self.image_size.currentText(), "quality": self.image_quality.currentText()}; self.generate_btn.setEnabled(False); self.generate_btn.setText("API 生成中，最长等待 180 秒……"); self.save_image_btn.setEnabled(False); self.save_settings_silent(); self.append_log(f"准备图片请求：模型={self.config.get('image_model')}，参考图数量={len(self.references)}"); self.worker = ApiWorker(self.config, "image", payload); self.worker.log.connect(self.append_log); self.worker.done.connect(self.image_done); self.worker.start()

    def image_done(self, result, error, ok):
        self.generate_btn.setEnabled(True); self.generate_btn.setText("调用 gpt-image-2.5 生成效果图")
        if not ok: QMessageBox.critical(self, "图片生成失败", error); return
        self.generated_bytes = result; pix = QPixmap(); pix.loadFromData(result); self.generated_label.setPixmap(pix.scaled(self.generated_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)); self.save_image_btn.setEnabled(True); self.status.setText("第四步已完成：API 效果图已返回"); self.update_steps()

    def save_generated_image(self):
        if not self.generated_bytes: return
        path, _ = QFileDialog.getSaveFileName(self, "保存 API 原图", "gown_api_generated.png", "PNG (*.png);;JPEG (*.jpg);;WEBP (*.webp)")
        if path: Path(path).write_bytes(self.generated_bytes); self.db.record_generation(self.current_project_id, self.config.get("image_model", "gpt-image-2.5"), self._last_image_prompt or self.image_prompt(), path, {"recommendations": self.design_recommendations, "audit": self.design_audit}, [row["id"] for row in self.knowledge_matches] + [row["knowledge_id"] for row in self.pattern_matches]); self.status.setText(f"已保存 API 原图 · {Path(path).name}")

    def test_text_api(self):
        self.start_chat([{"role": "user", "content": "只回复：文字 API 连接正常"}], lambda result, error, ok: (QMessageBox.information(self, "测试结果", result if ok else error)))

    def save_project(self):
        self.refresh_parameter_json(); project_id = self.db.save(self.collect_data(), self.references, self.knowledge_matches, self.design_recommendations, self.design_audit, self.pattern_matches); self.current_project_id = project_id; self.status.setText(f"已保存到 SQLite · 项目 {project_id} · 礼服证据 {len(self.knowledge_matches)} 条 · 纸样证据 {len(self.pattern_matches)} 条 · 风险 {self.design_audit.get('risk_count', 0)} 项"); QMessageBox.information(self, "已保存", f"项目已保存到本地数据库：{DB_PATH.name}\n项目编号：{project_id}\n礼服证据：{len(self.knowledge_matches)} 条\n纸样证据：{len(self.pattern_matches)} 条\n审查风险：{self.design_audit.get('risk_count', 0)} 项")

    def export_brief(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出设计单", "show_gown_design_brief.html", "HTML (*.html);;Markdown (*.md)")
        if not path: return
        d = self.collect_data(); content = "# " + d["project_name"] + "\n\n## 参考图识别\n" + self.analysis_output.toPlainText() + "\n\n## AI 设计方案\n" + d["design_plan"] + "\n\n## 工作台参数\n```json\n" + json.dumps(d["parameters"], ensure_ascii=False, indent=2) + "\n```\n\n## 备注\n" + d["notes"]
        if path.lower().endswith(".html"): content = "<html><meta charset='utf-8'><body><pre>" + html.escape(content) + "</pre></body></html>"
        Path(path).write_text(content, encoding="utf-8"); self.status.setText(f"已导出 · {Path(path).name}")

    def update_steps(self, *_):
        states = [bool(self.references and self.analysis_output.toPlainText().strip()), bool(self.plan_output.toPlainText().strip()), bool(self.parameter_json.toPlainText().strip()), bool(self.generated_bytes)]
        for label, done in zip(self.step_labels, states): label.setStyleSheet("background:#d8b36a;color:#3e2d1e;border-radius:5px;" if done else "")


def main():
    app = QApplication(sys.argv); app.setApplicationName(APP_NAME); window = MainWindow(); window.show(); sys.exit(app.exec())


if __name__ == "__main__":
    main()
