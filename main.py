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
from dress_knowledge import FACETS, ensure_schema, import_segments, rebuild_model_styling_knowledge, rebuild_pattern_knowledge, search_segments, search_model_styling, search_pattern_knowledge, format_context, recommend_design, audit_design, format_decision_package
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtCore import QUrl
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
FINAL_SPEC_PATH = ROOT / "final_design_spec.json"
PATTERN_PACK_DIR = ROOT / "pattern_reference_pack"
PATTERN_SPEC_PATH = ROOT / "pattern_reference_spec.json"
LOGO_PATH = ROOT / "assets" / "logo_show_gown_needle.png"
MODEL_DIMENSIONS = ("hairstyle", "hair_accessory", "expression", "pose", "gaze", "movement", "compatibility")
POSE_TERMS = ("站立", "行走/走步", "转身", "坐姿", "侧身", "回眸", "屈膝", "抬臂", "旋转", "摆动", "展开", "上下台阶")
PATTERN_VIEWS = (
    {"id": "01_front_static", "label": "正面静态全身", "purpose": "前片、领口、胸腰比例和前中结构", "required": True, "motion": "正面静止站立，双脚自然分开，完整露出下摆"},
    {"id": "02_back_static", "label": "背面静态全身", "purpose": "后片、后中、后领、开口和拖尾", "required": True, "motion": "背面静止站立，完整露出后摆和拖尾"},
    {"id": "03_left_side_static", "label": "左侧面静态", "purpose": "左侧缝、胸腰臀曲线和侧身放量", "required": True, "motion": "左侧面静止站立"},
    {"id": "04_right_side_static", "label": "右侧面静态", "purpose": "右侧缝和左右结构核对", "required": True, "motion": "右侧面静止站立"},
    {"id": "05_walking_stride", "label": "行走/迈步", "purpose": "行动余量、膝下放量和裙摆干涉", "required": True, "motion": "专业走步动作，一脚前迈，裙摆自然分开"},
    {"id": "06_turn_45deg", "label": "45度转身", "purpose": "腰臀转折、侧缝和拖尾跟随", "required": True, "motion": "45度转身，身体旋转，裙摆产生轻微摆动"},
    {"id": "07_turn_90deg", "label": "90度转身/回身", "purpose": "后片、后中和拖尾扫摆", "required": True, "motion": "90度回身，头部回眸，拖尾向外展开"},
    {"id": "08_back_walking", "label": "背影行走", "purpose": "后片和拖尾实际动态", "required": True, "motion": "背影走步，后摆和拖尾连续落地"},
    {"id": "09_veil_skirt_spread", "label": "头纱/裙摆展开", "purpose": "外层、衬层、拖尾连接和动态放量", "required": True, "motion": "头纱和裙摆向后侧摆动展开"},
    {"id": "10_bodice_waist_detail", "label": "胸腰结构近景", "purpose": "胸腰收量、分割线、蕾丝层和衬底层", "required": True, "motion": "正面胸腰局部近景，保持服装结构不变"},
    {"id": "11_side_hip_detail", "label": "侧腰至臀部近景", "purpose": "侧缝、省道、腰臀贴合和放量起点", "required": True, "motion": "侧腰至臀部局部近景"},
    {"id": "12_neckline_armhole_detail", "label": "领口/袖窿近景", "purpose": "领口弧度、肩部承托和袖窿边缘", "required": True, "motion": "领口和袖窿局部近景"},
    {"id": "13_skirt_train_spread", "label": "裙摆/拖尾展开", "purpose": "裙身分片、下摆宽度、拖尾长度和层级", "required": True, "motion": "裙摆和拖尾平铺式展开，结构清晰"},
    {"id": "14_layer_structure_detail", "label": "内外层结构近景", "purpose": "外层、透明层、衬层、里布和支撑关系", "required": True, "motion": "局部结构展示，不虚构不可见缝份和尺寸"},
)
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
        "image_timeout_seconds": 360,
        "pattern_pack_output_dir": str(ROOT / "pattern_reference_pack"),
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
            image_wait = int(self.config.get("image_timeout_seconds", 360))
            self.done.emit(None, f"API 在规定时间内未返回。图片生成允许等待 {image_wait} 秒；请检查模型、网络和 API 日志。", False)
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
        timeout = int(self.config.get("image_timeout_seconds", 360) if self.operation == "image" else self.config.get("timeout_seconds", 180))
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


class PatternPackWorker(QThread):
    progress = Signal(int, str)
    log = Signal(str)
    done = Signal(object)

    def __init__(self, config, final_spec, output_dir):
        super().__init__()
        self.config, self.final_spec, self.output_dir = config, final_spec, Path(output_dir)

    def compact_spec(self):
        spec = self.final_spec
        dims = spec.get("all_database_dimensions", {})
        styling = dims.get("model_styling_dimensions", spec.get("model_styling", {}))
        return {"parameters": spec.get("parameters", {}), "dress_dimensions": dims.get("dress_dimensions", {}), "pattern_dimensions": dims.get("pattern_dimensions", {}), "model_styling": styling, "source_pages": dims.get("source_pages", [])[:8], "evidence_ids": dims.get("evidence_ids", [])[:8]}

    def view_prompt(self, view):
        shared = json.dumps(self.compact_spec(), ensure_ascii=False, indent=2)
        return f"服装打版视觉参考图，不是纸样线稿，不添加文字和尺寸标注。只执行以下已锁定礼服规格，不改变廓形、面料、颜色、工艺、层级、模特、发型和姿态：\n{shared}\n当前视图：{view['label']}。打版用途：{view['purpose']}。动作/构图要求：{view['motion']}。如果提供了正面母版参考图，必须保持同一位模特和同一套礼服，只改变视角或动作，不得重新设计、换色、换面料、增减袖子、改变领型或改变装饰位置。同一中国成年女性真人模特、同一套礼服、同一灯光基准，完整表现结构线、面料层级和下摆；不可见的缝份、精确尺寸、省量、布纹和内部支撑不得臆造；真实时装摄影，避免 AI 伪影、文字、水印和品牌标识。"

    def create_contact_sheet(self, paths):
        try:
            from PIL import Image, ImageOps, ImageDraw
            images = []
            for path in paths:
                image = Image.open(path).convert("RGB")
                image.thumbnail((420, 420))
                canvas = Image.new("RGB", (440, 480), "white")
                canvas.paste(image, ((440 - image.width) // 2, 20))
                ImageDraw.Draw(canvas).text((15, 450), Path(path).stem, fill="black")
                images.append(canvas)
            cols = 3; rows = (len(images) + cols - 1) // cols
            sheet = Image.new("RGB", (cols * 440, rows * 480), "#e9e4de")
            for index, image in enumerate(images): sheet.paste(image, ((index % cols) * 440, (index // cols) * 480))
            output = self.output_dir / "00_overview_contact_sheet.png"; sheet.save(output); return str(output)
        except Exception as exc:
            self.log.emit(f"拼接总览图失败：{exc}"); return ""

    def run(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        created, failed = [], []; anchor_path = None
        total = len(PATTERN_VIEWS)
        for index, view in enumerate(PATTERN_VIEWS, start=1):
            self.progress.emit(index - 1, f"正在生成 {view['id']} · {view['label']}")
            try:
                path = self.output_dir / f"{view['id']}.png"
                if path.exists() and path.stat().st_size > 0:
                    created.append(str(path)); anchor_path = anchor_path or (path if index == 1 else anchor_path); self.log.emit(f"跳过已有文件：{view['id']}"); self.progress.emit(index, f"已跳过 {index}/{total}：{view['label']}"); continue
                refs = [str(anchor_path)] if anchor_path else []
                worker = ApiWorker(self.config, "image", {"prompt": self.view_prompt(view), "references": refs, "size": "1024x1024", "quality": "low"})
                data = worker.call_api(); path.write_bytes(data); created.append(str(path)); self.log.emit(f"完成：{view['id']}，字节={len(data)}")
                anchor_path = anchor_path or path
            except Exception as exc:
                failed.append({"view": view["id"], "label": view["label"], "error": str(exc)}); self.log.emit(f"失败：{view['id']}：{exc}")
            self.progress.emit(index, f"已处理 {index}/{total}：{view['label']}")
        overview = self.create_contact_sheet(created) if created else ""
        manifest = {"spec_version": 1, "created_at": datetime.now().isoformat(timespec="seconds"), "consistency_mode": "anchor_reference", "anchor_view": "01_front_static", "views": list(PATTERN_VIEWS), "created_files": created, "failed_views": failed, "overview": overview, "source_spec": str(PATTERN_SPEC_PATH)}
        (self.output_dir / "pattern_reference_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        self.done.emit(manifest)


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
        self.workflow_status = {"inspiration": "未开始", "database": "未开始", "design": "未开始", "image": "未开始"}
        self.inspiration_card = {}
        self.database_decision_confirmed = False
        self.final_design_spec = {}
        self.final_design_locked = False
        self.image_review_status = "未验收"
        self.pattern_pack_worker = None
        self.pattern_pack_manifest = {}
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
        self.tabs.setCurrentIndex(4)
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
        QWidget { font-family: 'Microsoft YaHei'; font-size: 13px; color: #2f2930; }
        QMainWindow, QTabWidget::pane { background: #f3eee8; }
        QGroupBox { background: #fffdfa; border: 1px solid #ded3c7; border-radius: 12px; margin-top: 14px; padding: 16px; font-weight: bold; }
        QGroupBox::title { subcontrol-origin: margin; left: 16px; padding: 0 8px; color: #5f3d50; }
        QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #fffefd; border: 1px solid #d6c9c0; border-radius: 8px; padding: 8px 10px; selection-background-color: #9d7186; }
        QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid #9d7186; }
        QPushButton { background: #623f54; color: white; border: 0; border-radius: 8px; padding: 10px 16px; font-weight: 600; }
        QPushButton:hover { background: #81546f; }
        QPushButton:pressed { background: #4d3042; }
        QPushButton:disabled { background: #cfc5c0; color: #8c817c; }
        QPushButton[secondary="true"] { background: #e5d9d1; color: #573e4d; }
        QPushButton[secondary="true"]:hover { background: #d9c9c0; }
        QTabWidget::pane { border: 0; padding: 8px; }
        QTabBar::tab { background: #e7ddd7; color: #6b5a61; padding: 12px 22px; margin-right: 4px; border-radius: 8px; font-weight: 600; }
        QTabBar::tab:selected { background: #623f54; color: white; }
        QListWidget { background: #fffdfa; border: 1px solid #ded3c7; border-radius: 10px; padding: 4px; }
        QScrollBar:vertical { background: #ebe2dc; width: 10px; margin: 2px; border-radius: 5px; }
        QScrollBar::handle:vertical { background: #b69aa9; min-height: 30px; border-radius: 5px; }
        QLabel#title { font-size: 24px; font-weight: 700; color: #4c3043; }
        QLabel#subtitle { color: #91777f; font-size: 12px; }
        QLabel#muted { color: #82757b; }
        QLabel#step { font-size: 12px; color: #6d5262; padding: 8px 12px; background: #e8ded9; border-radius: 7px; }
        """

    def build_ui(self):
        root = QWidget(); self.setCentralWidget(root); outer = QVBoxLayout(root)
        head = QHBoxLayout(); self.logo_label = QLabel(); self.logo_label.setFixedSize(48, 48); self.logo_label.setAlignment(Qt.AlignCenter); logo = QPixmap(str(LOGO_PATH)); self.logo_label.setPixmap(logo.scaled(42, 42, Qt.KeepAspectRatio, Qt.SmoothTransformation) if not logo.isNull() else QPixmap()); head.addWidget(self.logo_label); brand = QVBoxLayout(); title = QLabel(APP_NAME); title.setObjectName("title"); brand.addWidget(title); subtitle = QLabel("DATABASE-LED COUTURE WORKBENCH"); subtitle.setObjectName("subtitle"); brand.addWidget(subtitle); head.addLayout(brand); head.addStretch(); self.status = QLabel("全链路本地工作区"); self.status.setObjectName("muted"); head.addWidget(self.status); outer.addLayout(head)
        steps = QHBoxLayout()
        self.step_labels = []
        for text in ["01 灵感采集", "02 数据库决策", "03 设计定案", "04 图片执行"]:
            label = QLabel(text); label.setObjectName("step"); self.step_labels.append(label); steps.addWidget(label)
        outer.addLayout(steps)
        self.tabs = QTabWidget(); outer.addWidget(self.tabs)
        self.tabs.addTab(self.reference_tab(), "01 灵感采集")
        self.tabs.addTab(self.assistant_tab(), "02 数据库决策")
        self.tabs.addTab(self.workbench_tab(), "03 设计定案")
        self.tabs.addTab(self.image_tab(), "04 图片执行")
        self.tabs.addTab(self.settings_tab(), "接口设置")
        bottom = QHBoxLayout(); bottom.addStretch(); save = QPushButton("保存到 SQLite"); save.clicked.connect(self.save_project); bottom.addWidget(save); export = QPushButton("导出设计单"); export.clicked.connect(self.export_brief); bottom.addWidget(export); outer.addLayout(bottom)
        self.tabs.currentChanged.connect(self.update_steps)

    def reference_tab(self):
        tab = QWidget(); layout = QHBoxLayout(tab)
        left = QVBoxLayout(); box = QGroupBox("灵感与参考图"); bv = QVBoxLayout(box); add = QPushButton("导入参考图"); add.clicked.connect(self.add_reference); bv.addWidget(add); self.ref_list = QListWidget(); bv.addWidget(self.ref_list); left.addWidget(box)
        self.keywords = QTextEdit("午夜花园\n液态金属\n雕塑感肩线\n轻盈透明层次\n远看强轮廓，近看细工艺"); left.addWidget(QLabel("灵感关键词")); left.addWidget(self.keywords); self.inspiration_status = QLabel("01 状态：未开始"); self.inspiration_status.setObjectName("muted"); left.addWidget(self.inspiration_status); inspiration_row = QHBoxLayout(); extract = QPushButton("提炼灵感方向"); extract.clicked.connect(self.extract_inspiration); inspiration_row.addWidget(extract); confirm = QPushButton("确认灵感方向"); confirm.clicked.connect(self.confirm_inspiration); inspiration_row.addWidget(confirm); unlock = QPushButton("解锁并修改"); unlock.clicked.connect(self.unlock_inspiration); inspiration_row.addWidget(unlock); left.addLayout(inspiration_row); self.inspiration_output = QTextEdit(); self.inspiration_output.setReadOnly(True); self.inspiration_output.setPlaceholderText("确认后生成灵感方向卡，作为 02 数据库决策的输入。"); left.addWidget(self.inspiration_output); layout.addLayout(left, 1)
        right = QVBoxLayout(); intro = QLabel("第一步：上传参考图后，让多模态模型识别色彩、廓形、面料、工艺、氛围、构图和设计平台维度。程序只读取原始字节并通过 API 发送，不做本地图像识别或静态图像处理。") ; intro.setWordWrap(True); intro.setObjectName("muted"); right.addWidget(intro); self.analyze_btn = QPushButton("调用多模态模型识别参考图"); self.analyze_btn.clicked.connect(self.analyze_references); right.addWidget(self.analyze_btn); self.analysis_output = QTextEdit(); self.analysis_output.setPlaceholderText("多模态识别结果会以 JSON/中文摘要显示在这里，可人工修改后进入下一步。"); right.addWidget(self.analysis_output, 2); right.addWidget(QLabel("实时 API 日志（不记录 API Key）")); self.live_log = QTextEdit(); self.live_log.setReadOnly(True); self.live_log.setPlaceholderText("点击识别后，这里会显示端点、模型、HTTP 状态、响应结构和具体错误。"); self.live_log.setMaximumHeight(190); right.addWidget(self.live_log); layout.addLayout(right, 2); return tab

    def assistant_tab(self):
        tab = QWidget(); layout = QVBoxLayout(tab); intro = QLabel("第二步：数据库先形成设计决策包，再由 AI 编排原创方案。接受数据库决策后才能进入 03。"); intro.setObjectName("muted"); layout.addWidget(intro); self.brief_input = QTextEdit(); self.brief_input.setPlaceholderText("补充设计目标，例如：希望更适合闭幕秀，强调远距离轮廓和灯光下的闪烁层次……"); layout.addWidget(self.brief_input); self.database_status = QLabel("02 状态：未开始"); self.database_status.setObjectName("muted"); layout.addWidget(self.database_status); row = QHBoxLayout(); self.knowledge_btn = QPushButton("检索并生成设计决策包"); self.knowledge_btn.clicked.connect(self.refresh_knowledge_context); row.addWidget(self.knowledge_btn); accept = QPushButton("接受数据库决策"); accept.clicked.connect(self.accept_database_decision); row.addWidget(accept); self.plan_btn = QPushButton("生成 AI 设计方案"); self.plan_btn.clicked.connect(self.generate_plan); row.addWidget(self.plan_btn); apply = QPushButton("将方案写入设计工作台"); apply.setProperty("secondary", True); apply.clicked.connect(self.apply_plan); row.addWidget(apply); row.addStretch(); layout.addLayout(row); self.knowledge_output = QTextEdit(); self.knowledge_output.setReadOnly(True); self.knowledge_output.setPlaceholderText("这里显示分层检索、结构化推荐、来源证据和风险审查。"); layout.addWidget(QLabel("数据库设计决策包：推荐、证据与审查")); layout.addWidget(self.knowledge_output, 2); self.plan_output = QTextEdit(); self.plan_output.setPlaceholderText("AI 设计助理输出：概念、廓形、面料、工艺、秀场动态、试衣检查和图片生成提示词。"); layout.addWidget(self.plan_output, 2); return tab

    def workbench_tab(self):
        tab = QWidget(); layout = QHBoxLayout(tab); left = QVBoxLayout(); meta = QGroupBox("项目与结构化参数"); mf = QFormLayout(meta); self.project_name = QLineEdit("2026 春夏 · 午夜花园"); self.client = QLineEdit("Look 01"); self.occasion = QLineEdit("品牌发布秀 / 闭幕造型"); mf.addRow("项目名称", self.project_name); mf.addRow("造型编号", self.client); mf.addRow("使用场景", self.occasion); left.addWidget(meta)
        params = QGroupBox("设计工作台参数"); pf = QFormLayout(params); self.silhouette = self.combo(["A字", "鱼尾", "蓬蓬", "直身"]); self.neckline = self.combo(["心形", "一字肩", "高领", "深V", "不对称"]); self.sleeve = self.combo(["无袖", "泡泡袖", "长袖", "披帛", "手套袖"]); self.fabric = self.combo(["缎面", "欧根纱", "天鹅绒", "亮片", "蕾丝", "金属丝"]); self.main_color = self.combo(["黑色", "酒红", "深海蓝", "古金", "象牙白", "灰粉"]); self.accent = self.combo(["古金", "银色", "珍珠白", "深紫", "黑色"]); self.train = self.combo(["无拖尾", "短拖尾", "中拖尾", "超长拖尾"]); self.craft = QLineEdit("珠绣、立体花饰、金属刺绣"); self.work_notes = QTextEdit(); self.work_notes.setPlaceholderText("确认后的版型、工艺、搭配和试衣备注"); pf.addRow("主廓形", self.silhouette); pf.addRow("领型", self.neckline); pf.addRow("袖型", self.sleeve); pf.addRow("面料", self.fabric); pf.addRow("主色", self.main_color); pf.addRow("点缀色", self.accent); pf.addRow("拖尾", self.train); pf.addRow("工艺", self.craft); pf.addRow("确认备注", self.work_notes); left.addWidget(params)
        measures = QGroupBox("量体数据"); grid = QGridLayout(measures); self.height = self.measure(175); self.bust = self.measure(84); self.waist = self.measure(64); self.hip = self.measure(90); widgets = [("身高", self.height), ("胸围", self.bust), ("腰围", self.waist), ("臀围", self.hip)];
        for i, (name, widget) in enumerate(widgets): grid.addWidget(QLabel(name), i // 2, (i % 2) * 2); grid.addWidget(widget, i // 2, (i % 2) * 2 + 1)
        left.addWidget(measures); layout.addLayout(left, 1)
        right = QVBoxLayout(); info = QLabel("第三步：AI 方案落地为可修改的最终设计参数。数据库全维度会在这里展示，必须锁定最终设计后，第四步才会调用图片 API。"); info.setWordWrap(True); info.setObjectName("muted"); right.addWidget(info); self.knowledge_status = QLabel("知识库链路：等待检索"); self.knowledge_status.setObjectName("muted"); right.addWidget(self.knowledge_status); self.design_status = QLabel("03 状态：未开始"); self.design_status.setObjectName("muted"); right.addWidget(self.design_status); self.parameter_json = QTextEdit(); self.parameter_json.setReadOnly(True); right.addWidget(QLabel("当前核心参数 JSON")); right.addWidget(self.parameter_json, 1); self.dimension_output = QTextEdit(); self.dimension_output.setReadOnly(True); self.dimension_output.setPlaceholderText("检索数据库后，这里显示礼服、纸样、模特和专业姿态/动态的全部维度。"); right.addWidget(QLabel("数据库全维度定案（含专业姿态术语）")); right.addWidget(self.dimension_output, 3); refresh = QPushButton("刷新结构化参数"); refresh.clicked.connect(self.refresh_parameter_json); right.addWidget(refresh); design_row = QHBoxLayout(); draft = QPushButton("保存设计草稿"); draft.clicked.connect(self.save_design_draft); design_row.addWidget(draft); lock = QPushButton("锁定最终设计"); lock.clicked.connect(self.lock_final_design); design_row.addWidget(lock); unlock = QPushButton("解锁并修改"); unlock.clicked.connect(self.unlock_final_design); design_row.addWidget(unlock); right.addLayout(design_row); layout.addLayout(right, 1); return tab

    def image_tab(self):
        tab = QWidget(); layout = QVBoxLayout(tab); intro = QLabel("第四步：只执行 03 已锁定的 final_design_spec.json。这里可以补充摄影要求，但不能改动最终廓形、面料、颜色、纸样和模特决策。") ; intro.setWordWrap(True); intro.setObjectName("muted"); layout.addWidget(intro); self.image_status = QLabel("04 状态：未开始"); self.image_status.setObjectName("muted"); layout.addWidget(self.image_status); controls = QGroupBox("效果图设置"); cf = QFormLayout(controls); self.image_extra = QTextEdit(); self.image_extra.setPlaceholderText("只填写视角、舞台灯光、摄影风格等执行要求；不要修改廓形和面料"); self.image_size = self.combo(["1024x1024", "1024x1536", "1536x1024"]); self.image_quality = self.combo(["low", "medium", "high"]); cf.addRow("摄影补充要求", self.image_extra); cf.addRow("画幅（默认方图更快）", self.image_size); cf.addRow("质量", self.image_quality); layout.addWidget(controls); output_row = QHBoxLayout(); output_row.addWidget(QLabel("打版包输出文件夹")); self.pattern_output_edit = QLineEdit(self.config.get("pattern_pack_output_dir", str(PATTERN_PACK_DIR))); self.pattern_output_edit.setToolTip("每张图片 API 成功返回后立即保存到此文件夹"); output_row.addWidget(self.pattern_output_edit, 1); browse = QPushButton("选择文件夹"); browse.clicked.connect(self.choose_pattern_output_dir); output_row.addWidget(browse); layout.addLayout(output_row); self.pattern_output_hint = QLabel("每张图成功后立即写入本地；支持断点续跑。") ; self.pattern_output_hint.setObjectName("muted"); layout.addWidget(self.pattern_output_hint); row = QHBoxLayout(); check = QPushButton("检查最终设计"); check.clicked.connect(self.check_final_design); row.addWidget(check); self.generate_btn = QPushButton("调用 gpt-image-2.5 生成效果图"); self.generate_btn.clicked.connect(self.generate_image); row.addWidget(self.generate_btn); pack = QPushButton("生成打版参考包（14视图）"); pack.clicked.connect(self.generate_pattern_pack); row.addWidget(pack); self.save_image_btn = QPushButton("保存 API 原图"); self.save_image_btn.setProperty("secondary", True); self.save_image_btn.setEnabled(False); self.save_image_btn.clicked.connect(self.save_generated_image); row.addWidget(self.save_image_btn); row.addStretch(); layout.addLayout(row); self.pattern_pack_status = QLabel("打版参考包：未生成"); self.pattern_pack_status.setObjectName("muted"); layout.addWidget(self.pattern_pack_status); pack_actions = QHBoxLayout(); self.pattern_overview_btn = QPushButton("查看打版参考包总览"); self.pattern_overview_btn.setEnabled(False); self.pattern_overview_btn.clicked.connect(self.show_pattern_overview); pack_actions.addWidget(self.pattern_overview_btn); self.open_pattern_dir_btn = QPushButton("打开参考包目录"); self.open_pattern_dir_btn.setEnabled(False); self.open_pattern_dir_btn.clicked.connect(self.open_pattern_directory); pack_actions.addWidget(self.open_pattern_dir_btn); pack_actions.addStretch(); layout.addLayout(pack_actions); review = QHBoxLayout(); self.pass_review_btn = QPushButton("图片验收通过"); self.pass_review_btn.clicked.connect(self.accept_image_review); review.addWidget(self.pass_review_btn); self.fail_review_btn = QPushButton("图片不通过，返回 03"); self.fail_review_btn.clicked.connect(self.reject_image_review); review.addWidget(self.fail_review_btn); layout.addLayout(review); self.generated_label = QLabel("API 返回的原图或打版参考包总览图将在这里显示"); self.generated_label.setAlignment(Qt.AlignCenter); self.generated_label.setMinimumHeight(420); self.generated_label.setStyleSheet("background:#fffdf9;border:1px solid #ddd4c8;border-radius:8px;color:#81766d;"); layout.addWidget(self.generated_label, 1); return tab

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

    def set_workflow_state(self, key, state):
        self.workflow_status[key] = state
        labels = {"inspiration": getattr(self, "inspiration_status", None), "database": getattr(self, "database_status", None), "design": getattr(self, "design_status", None), "image": getattr(self, "image_status", None)}
        if labels.get(key):
            labels[key].setText({"inspiration": "01", "database": "02", "design": "03", "image": "04"}[key] + f" 状态：{state}")

    def extract_inspiration(self):
        terms = [x.strip() for x in self.keywords.toPlainText().replace("，", "\n").replace(",", "\n").splitlines() if x.strip()]
        self.inspiration_card = {"theme": terms[0] if terms else "未命名主题", "mood": terms[1:4], "visual_keywords": terms, "silhouette_intent": "远看轮廓明确，近看工艺可见", "stage_intent": self.occasion.text() if hasattr(self, "occasion") else "秀场造型", "avoid": ["文字", "水印", "品牌标识", "明显 AI 伪影"], "reference_analysis": self.analysis_output.toPlainText().strip()}
        self.inspiration_output.setPlainText(json.dumps(self.inspiration_card, ensure_ascii=False, indent=2)); self.set_workflow_state("inspiration", "待确认"); self.set_workflow_state("database", "未开始"); self.database_decision_confirmed = False; self.set_workflow_state("design", "未开始"); self.final_design_locked = False; self.set_workflow_state("image", "未开始")

    def confirm_inspiration(self):
        if not self.inspiration_card: self.extract_inspiration()
        self.set_workflow_state("inspiration", "已确认"); self.append_log("01 灵感方向已确认，允许进入 02 数据库决策")

    def unlock_inspiration(self):
        self.set_workflow_state("inspiration", "编辑中"); self.database_decision_confirmed = False; self.set_workflow_state("database", "需要重新确认"); self.final_design_locked = False; self.set_workflow_state("design", "需要重新确认"); self.set_workflow_state("image", "需要重新确认")

    def accept_database_decision(self):
        if not self.knowledge_matches:
            QMessageBox.warning(self, "尚未检索数据库", "请先点击“检索并生成设计决策包”。"); return
        if self.workflow_status.get("inspiration") not in ("已确认", "已锁定"):
            QMessageBox.warning(self, "灵感方向未确认", "请先在 01 点击“确认灵感方向”。"); return
        self.database_decision_confirmed = True; self.set_workflow_state("database", "已确认"); self.set_workflow_state("design", "编辑中"); self.append_log("02 数据库设计决策已接受，允许进入 03 设计定案")

    def save_design_draft(self):
        self.refresh_parameter_json(); self.set_workflow_state("design", "待确认"); self.append_log("03 设计参数已保存为草稿")

    def build_final_design_spec(self):
        return {"spec_version": 1, "created_at": datetime.now().isoformat(timespec="seconds"), "inspiration_card": self.inspiration_card, "design_plan": self.plan_output.toPlainText().strip(), "parameters": self.parameters(), "database_decision": self.design_recommendations, "all_database_dimensions": self.database_dimension_package(), "pattern_evidence": self.pattern_matches, "model_styling": self.model_styling_decision, "audit": self.design_audit, "references": [Path(p).name for p in self.references], "designer_overrides": {"parameters": self.parameters()}}

    def build_pattern_reference_spec(self):
        dimensions = self.final_design_spec.get("all_database_dimensions", self.database_dimension_package())
        return {"spec_version": 1, "created_at": datetime.now().isoformat(timespec="seconds"), "source_final_design_spec": str(FINAL_SPEC_PATH), "database_dimensions": dimensions, "professional_pose_terms": dimensions.get("model_styling_dimensions", {}).get("professional_pose_terms", []), "views": list(PATTERN_VIEWS), "shared_constraints": ["同一中国成年女性模特", "同一礼服规格", "同一面料颜色工艺", "前后左右结构保持一致", "不可见尺寸、省量、缝份和内部支撑不得臆造", "每张独立原图必须保留"], "validation_checklist": ["前后视图比例", "左右结构对应", "胸腰臀围度", "分割线和省道方向", "行走活动量", "转身牵拉", "拖尾落地和绊倒风险", "外层衬层里布关系"]}

    def lock_final_design(self):
        if not self.database_decision_confirmed:
            QMessageBox.warning(self, "数据库决策未确认", "请先在 02 接受数据库决策。"); return
        if not self.plan_output.toPlainText().strip():
            QMessageBox.warning(self, "缺少设计方案", "请先生成或填写设计方案。"); return
        self.refresh_parameter_json(); self.final_design_spec = self.build_final_design_spec(); FINAL_SPEC_PATH.write_text(json.dumps(self.final_design_spec, ensure_ascii=False, indent=2), encoding="utf-8"); PATTERN_SPEC_PATH.write_text(json.dumps(self.build_pattern_reference_spec(), ensure_ascii=False, indent=2), encoding="utf-8"); self.final_design_locked = True; self.set_workflow_state("design", "已锁定"); self.set_workflow_state("image", "待检查"); self.append_log(f"03 最终设计已锁定：{FINAL_SPEC_PATH.name}；打版规格：{PATTERN_SPEC_PATH.name}"); self.tabs.setCurrentIndex(3)

    def unlock_final_design(self):
        self.final_design_locked = False; self.set_workflow_state("design", "编辑中"); self.set_workflow_state("image", "需要重新确认"); self.append_log("03 已解锁，04 生成资格已撤销")

    def check_final_design(self):
        if not self.final_design_locked or not self.final_design_spec:
            QMessageBox.warning(self, "最终设计未锁定", "请返回 03 锁定最终设计后再检查。"); self.tabs.setCurrentIndex(2); return
        self.set_workflow_state("image", "可生成"); self.status.setText("最终设计规格已检查，可调用图片 API")

    def generate_pattern_pack(self):
        if not self.final_design_locked or not self.final_design_spec:
            QMessageBox.warning(self, "最终设计未锁定", "请先在 03 锁定最终设计。"); self.tabs.setCurrentIndex(2); return
        if not self.config.get("api_key"):
            QMessageBox.warning(self, "缺少 API Key", "请先在接口设置中填写 API Key。"); self.tabs.setCurrentIndex(4); return
        output_dir = Path(self.pattern_output_edit.text().strip()).expanduser() if self.pattern_output_edit.text().strip() else PATTERN_PACK_DIR
        output_dir.mkdir(parents=True, exist_ok=True); self.config["pattern_pack_output_dir"] = str(output_dir); self.save_settings_silent(); old_manifest = output_dir / "pattern_reference_manifest.json"
        if old_manifest.exists():
            try:
                if json.loads(old_manifest.read_text(encoding="utf-8")).get("consistency_mode") != "anchor_reference":
                    for old_png in output_dir.glob("*.png"): old_png.unlink()
                    old_manifest.unlink()
                    self.append_log("检测到旧版独立随机视图包，已清理并改用同一正面母版生成")
            except Exception:
                pass
        self.pattern_pack_status.setText(f"打版参考包：准备生成 14 张，输出到 {output_dir}……"); self.set_workflow_state("image", "打版参考包生成中"); self.pattern_pack_worker = PatternPackWorker(self.config, self.final_design_spec, output_dir); self.pattern_pack_worker.progress.connect(lambda n, text: self.pattern_pack_status.setText(f"打版参考包：{text}")); self.pattern_pack_worker.log.connect(self.append_log); self.pattern_pack_worker.done.connect(self.pattern_pack_done); self.pattern_pack_worker.start()

    def pattern_pack_done(self, manifest):
        self.pattern_pack_manifest = manifest; failed = len(manifest.get("failed_views", [])); created = len(manifest.get("created_files", [])); overview = manifest.get("overview", ""); self.pattern_pack_status.setText(f"打版参考包：完成 {created} 张，失败 {failed} 张；总览图已生成" if overview else f"打版参考包：完成 {created} 张，失败 {failed} 张"); self.pattern_overview_btn.setEnabled(bool(overview)); self.open_pattern_dir_btn.setEnabled(bool(created)); self.set_workflow_state("image", "打版包待验收"); self.show_pattern_overview() if overview else None; self.status.setText(f"打版参考包已生成：{PATTERN_PACK_DIR.name}；独立图 {created} 张")

    def show_pattern_overview(self):
        overview = self.pattern_pack_manifest.get("overview", "")
        if not overview:
            overview = str(PATTERN_PACK_DIR / "00_overview_contact_sheet.png")
        path = Path(overview)
        if not path.exists():
            QMessageBox.warning(self, "总览图不存在", f"未找到：{path}"); return
        pix = QPixmap(str(path))
        if pix.isNull():
            QMessageBox.warning(self, "总览图无法读取", "请打开参考包目录检查文件。"); return
        self.generated_label.setPixmap(pix.scaled(self.generated_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)); self.generated_label.setToolTip(str(path)); self.status.setText(f"已显示打版参考包总览：{path.name}")

    def open_pattern_directory(self):
        output_dir = Path(self.pattern_output_edit.text().strip()).expanduser() if self.pattern_output_edit.text().strip() else PATTERN_PACK_DIR
        if output_dir.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(output_dir.resolve())))

    def choose_pattern_output_dir(self):
        current = self.pattern_output_edit.text().strip() or str(PATTERN_PACK_DIR)
        selected = QFileDialog.getExistingDirectory(self, "选择打版参考包输出文件夹", current)
        if selected:
            self.pattern_output_edit.setText(selected); self.config["pattern_pack_output_dir"] = selected; self.save_settings_silent(); self.pattern_pack_status.setText(f"打版包输出文件夹已设置：{selected}")

    def accept_image_review(self):
        if not self.generated_bytes:
            QMessageBox.warning(self, "没有生成图片", "请先生成图片。"); return
        self.image_review_status = "已验收"; self.set_workflow_state("image", "已验收"); self.status.setText("图片已通过人工/视觉验收，可保存为最终结果")

    def reject_image_review(self):
        self.image_review_status = "未通过"; self.set_workflow_state("image", "未通过，返回 03"); self.tabs.setCurrentIndex(2); self.append_log("图片验收未通过，请修改 03 最终设计后重新锁定")

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
        if self.workflow_status.get("inspiration") not in ("已确认", "已锁定"):
            QMessageBox.warning(self, "灵感方向未确认", "请先在 01 点击“提炼灵感方向”并确认。"); self.tabs.setCurrentIndex(0); return
        if not self.database_decision_confirmed:
            QMessageBox.warning(self, "数据库决策未确认", "请先检索并接受 02 数据库决策。"); return
        analysis = self.analysis_output.toPlainText().strip() or "尚未完成参考图识别，请基于用户关键词先给出初步方案。"
        context = self.refresh_knowledge_context()
        package = format_decision_package(self.design_recommendations, self.design_audit)
        prompt = f"你是一名资深秀场礼服设计总监。根据参考图识别、设计师要求和下方设计决策包，生成原创且可执行的中文礼服设计方案。必须包含：设计概念、廓形比例、领袖结构、面料色彩、工艺拆解、秀场灯光与动态、量体/打版/试衣检查、给图片模型的视觉提示词。必须逐项回应风险审查；不要照抄来源原文，区分候选证据与最终判断。\n参考识别：{analysis}\n灵感关键词：{self.keywords.toPlainText()}\n补充要求：{self.brief_input.toPlainText()}\n{context}\n结构化设计决策包：\n{package}"
        self.plan_btn.setEnabled(False); self.plan_btn.setText("AI 方案生成中……"); self.start_chat([{"role": "user", "content": prompt}], self.plan_done)

    def refresh_knowledge_context(self):
        if self.workflow_status.get("inspiration") not in ("已确认", "已锁定"):
            self.set_workflow_state("database", "等待 01 确认")
        query = " ".join([json.dumps(self.inspiration_card, ensure_ascii=False), self.analysis_output.toPlainText(), self.brief_input.toPlainText()])
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
        self.design_recommendations["all_database_dimensions"] = self.database_dimension_package()
        self.design_audit = audit_design(self.parameters(), self.design_recommendations)
        decision_text = format_decision_package(self.design_recommendations, self.design_audit)
        if hasattr(self, "knowledge_output"):
            self.knowledge_output.setPlainText(decision_text + "\n\n【来源证据】\n" + context)
        if hasattr(self, "knowledge_status"):
            self.knowledge_status.setText(f"知识库链路：已命中礼服 {len(self.knowledge_matches)} 条 · 纸样 {len(self.pattern_matches)} 条 · 模特造型已接管")
        if hasattr(self, "dimension_output"):
            self.dimension_output.setPlainText(json.dumps(self.design_recommendations["all_database_dimensions"], ensure_ascii=False, indent=2))
        self.append_log(f"数据库决策：礼服={len(self.knowledge_matches)}，纸样={len(self.pattern_matches)}，模特造型=1")
        if hasattr(self, "database_status") and not self.database_decision_confirmed:
            self.set_workflow_state("database", "待确认")
        styling_text = "\n数据库模特造型决策：\n" + json.dumps(self.model_styling_decision, ensure_ascii=False, indent=2)
        pattern_text = "\n数据库纸样决策证据：\n" + json.dumps(self.design_recommendations.get("pattern_decision", {}), ensure_ascii=False, indent=2)
        return context + styling_text + pattern_text + "\n结构化设计决策包：\n" + decision_text

    def database_dimension_package(self):
        rec = self.design_recommendations or {}
        dress = {facet: rec.get(facet, ["数据库记录未明确"]) for facet in FACETS}
        styling = self.model_styling_decision or {}
        pose_text = str(styling.get("pose", "数据库记录未明确")); movement_text = str(styling.get("movement", "数据库记录未明确"))
        aliases = (("行走", "行走（走步）"), ("走步", "走步"), ("转身", "转身"), ("坐", "坐姿"), ("站", "站立"), ("侧身", "侧身"), ("回眸", "回眸"), ("屈膝", "屈膝"), ("抬臂", "抬臂"), ("旋转", "旋转"), ("摆动", "摆动"), ("展开", "展开"), ("上下台阶", "上下台阶"))
        pose_terms = list(dict.fromkeys([label for needle, label in aliases if needle in pose_text or needle in movement_text]))
        if not pose_terms: pose_terms = [pose_text if pose_text else "数据库记录未明确"]
        model = {key: styling.get(key, "数据库记录未明确") for key in MODEL_DIMENSIONS}; model["professional_pose_terms"] = pose_terms
        pattern = rec.get("pattern_decision", {"status": "no_database_evidence"})
        return {"dress_dimensions": dress, "pattern_dimensions": pattern, "model_styling_dimensions": model, "source_pages": rec.get("source_pages", []), "evidence_ids": rec.get("evidence_ids", []), "decision_rule": "03 必须逐项确认；未明确字段不得由程序随机补全"}

    def plan_done(self, result, error, ok):
        self.plan_btn.setEnabled(True); self.plan_btn.setText("生成 AI 设计方案")
        if not ok: QMessageBox.critical(self, "设计方案失败", error); return
        self.design_plan = result; self.plan_output.setPlainText(result); self.status.setText("第二步已完成：AI 设计方案已生成"); self.update_steps()

    def apply_plan(self):
        text = self.plan_output.toPlainText().strip()
        if not text: QMessageBox.warning(self, "没有设计方案", "请先生成或填写 AI 设计方案。"); return
        self.design_plan = text; self.set_workflow_state("design", "编辑中"); self.final_design_locked = False; self.set_workflow_state("image", "等待 03 锁定"); self.status.setText("方案已写入工作台，请保存草稿并锁定最终设计"); self.tabs.setCurrentIndex(2); self.refresh_parameter_json(); self.update_steps()

    def parameters(self):
        return {"silhouette": self.silhouette.currentText(), "neckline": self.neckline.currentText(), "sleeve": self.sleeve.currentText(), "fabric": self.fabric.currentText(), "main_color": self.main_color.currentText(), "accent_color": self.accent.currentText(), "train": self.train.currentText(), "craft": self.craft.text(), "measurements": {"height_cm": self.height.value(), "bust_cm": self.bust.value(), "waist_cm": self.waist.value(), "hip_cm": self.hip.value()}}

    def refresh_parameter_json(self):
        data = self.parameters(); self.parameter_json.setPlainText(json.dumps(data, ensure_ascii=False, indent=2)); self.update_steps()

    def collect_data(self):
        return {"project_name": self.project_name.text(), "analysis": self.analysis, "design_plan": self.plan_output.toPlainText(), "parameters": self.parameters(), "notes": self.work_notes.toPlainText()}

    def image_prompt(self):
        if not self.final_design_locked or not self.final_design_spec:
            return "未锁定 final_design_spec，禁止调用图片 API。"
        spec = self.final_design_spec; styling = spec.get("model_styling", {}); pattern = spec.get("database_decision", {}).get("pattern_decision", {}); execution_spec = {"spec_version": spec.get("spec_version"), "parameters": spec.get("parameters", {}), "inspiration": {"theme": spec.get("inspiration_card", {}).get("theme", ""), "visual_keywords": spec.get("inspiration_card", {}).get("visual_keywords", [])[:8]}, "pattern_decision": {k: v[:4] if isinstance(v, list) else v for k, v in pattern.items() if k in ("seam_lines", "darts", "panels", "layers", "draping_clues", "ease_clues", "validation", "risk")}, "model_styling": {k: styling.get(k, "") for k in ("hairstyle", "hair_accessory", "expression", "gaze", "pose", "movement")}, "database_acceptance": styling.get("database_acceptance", {}), "source_pages": spec.get("database_decision", {}).get("source_pages", [])[:8]}; compact = json.dumps(execution_spec, ensure_ascii=False, indent=2); return f"只执行已锁定 final_design_spec 的以下执行投影，不重新设计、不覆盖参数：\n{compact}\n摄影补充要求：{self.image_extra.toPlainText()}。必须是中国成年女性真人时装摄影感；严格遵守执行投影中的廓形、领型、袖型、面料、颜色、拖尾、纸样证据、发型、头饰、表情、视线、姿态与动态；必须让 database_acceptance 条件在画面中清晰可见；不要文字、不要水印、不要品牌标识；避免塑料皮肤、蜡像脸、过度磨皮、诡异眼神、僵硬表情、重复脸和 AI 伪影。"

    def generate_image(self):
        self.refresh_knowledge_context()
        self.refresh_parameter_json()
        if not self.plan_output.toPlainText().strip(): QMessageBox.warning(self, "尚未完成第二步", "请先生成或填写 AI 设计方案。"); self.tabs.setCurrentIndex(1); return
        if not self.final_design_locked or not self.final_design_spec:
            QMessageBox.warning(self, "最终设计未锁定", "请先在 03 点击“锁定最终设计”，再进入图片执行。"); self.tabs.setCurrentIndex(2); return
        if not self.pattern_matches:
            QMessageBox.warning(self, "纸样证据未命中", "当前设计没有命中数据库纸样证据，不能进入图片生成。请补充廓形、面料、分割线或纸样关键词后重新检索。")
            self.tabs.setCurrentIndex(1); return
        if not self.config.get("api_key"): QMessageBox.warning(self, "缺少 API Key", "请先在接口设置中填写 API Key。"); self.tabs.setCurrentIndex(4); return
        self._last_image_prompt = self.image_prompt(); payload = {"prompt": self._last_image_prompt, "references": self.references, "size": self.image_size.currentText(), "quality": self.image_quality.currentText()}; self.generate_btn.setEnabled(False); self.generate_btn.setText(f"API 生成中，最长等待 {self.config.get('image_timeout_seconds', 360)} 秒……"); self.save_image_btn.setEnabled(False); self.save_settings_silent(); self.append_log(f"准备图片请求：模型={self.config.get('image_model')}，画幅={payload['size']}，质量={payload['quality']}，提示词字符={len(payload['prompt'])}，参考图数量={len(self.references)}"); self.worker = ApiWorker(self.config, "image", payload); self.worker.log.connect(self.append_log); self.worker.done.connect(self.image_done); self.worker.start()

    def image_done(self, result, error, ok):
        self.generate_btn.setEnabled(True); self.generate_btn.setText("调用 gpt-image-2.5 生成效果图")
        if not ok: QMessageBox.critical(self, "图片生成失败", error); return
        self.generated_bytes = result; self.image_review_status = "待验收"; self.set_workflow_state("image", "待视觉验收"); pix = QPixmap(); pix.loadFromData(result); self.generated_label.setPixmap(pix.scaled(self.generated_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)); self.save_image_btn.setEnabled(True); self.status.setText("第四步已完成：API 效果图已返回，等待验收"); self.update_steps()

    def save_generated_image(self):
        if not self.generated_bytes: return
        if self.image_review_status != "已验收":
            QMessageBox.warning(self, "尚未验收", "请先点击“图片验收通过”，再保存为最终结果。"); return
        path, _ = QFileDialog.getSaveFileName(self, "保存 API 原图", "gown_api_generated.png", "PNG (*.png);;JPEG (*.jpg);;WEBP (*.webp)")
        if path: Path(path).write_bytes(self.generated_bytes); self.db.record_generation(self.current_project_id, self.config.get("image_model", "gpt-image-2.5"), self._last_image_prompt or self.image_prompt(), path, {"final_design_spec": self.final_design_spec, "recommendations": self.design_recommendations, "audit": self.design_audit}, [row["id"] for row in self.knowledge_matches] + [row["knowledge_id"] for row in self.pattern_matches]); self.status.setText(f"已保存 API 原图 · {Path(path).name}")

    def test_text_api(self):
        self.start_chat([{"role": "user", "content": "只回复：文字 API 连接正常"}], lambda result, error, ok: (QMessageBox.information(self, "测试结果", result if ok else error)))

    def save_project(self):
        self.refresh_parameter_json(); decision_record = dict(self.design_recommendations); decision_record["final_design_spec"] = self.final_design_spec; project_id = self.db.save(self.collect_data(), self.references, self.knowledge_matches, decision_record, self.design_audit, self.pattern_matches); self.current_project_id = project_id; self.status.setText(f"已保存到 SQLite · 项目 {project_id} · 礼服证据 {len(self.knowledge_matches)} 条 · 纸样证据 {len(self.pattern_matches)} 条 · 风险 {self.design_audit.get('risk_count', 0)} 项"); QMessageBox.information(self, "已保存", f"项目已保存到本地数据库：{DB_PATH.name}\n项目编号：{project_id}\n礼服证据：{len(self.knowledge_matches)} 条\n纸样证据：{len(self.pattern_matches)} 条\n审查风险：{self.design_audit.get('risk_count', 0)} 项")

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
