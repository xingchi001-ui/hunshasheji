import os
import sys
import tempfile
import json
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from main import MainWindow, ProjectDB, default_config, read_raw_image
from dress_knowledge import recommend_design, audit_design, format_decision_package

app = QApplication.instance() or QApplication([])
with tempfile.TemporaryDirectory() as td:
    db = ProjectDB(Path(td) / "test.db")
    assert (Path(td) / "test.db").exists()
    with db.connect() as c:
        names = {row[0] for row in c.execute("select name from sqlite_master where type='table'")}
    assert {"projects", "reference_assets", "generation_runs", "dress_knowledge", "pattern_knowledge", "project_knowledge_links", "project_pattern_links", "project_decision_audits", "generation_audits"}.issubset(names)
    sample_data = Path(td) / "segments.jsonl"
    sample_data.write_text(json.dumps({
        "id": "book:p0001:s0001", "book_id": "book", "page": 1,
        "text": "鱼尾礼服可通过腰部收省形成贴体廓形。",
        "source": {"ocr_confidence": 0.99},
        "knowledge": {"silhouette": ["鱼尾"], "construction": ["省道"]},
        "review": {"needs_human_check": False}
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    assert db.import_dress_training_data(sample_data) == 1
    matches = db.search_dress_knowledge("鱼尾", limit=3)
    assert matches and matches[0]["text"].startswith("鱼尾礼服")

win = MainWindow()
assert win.tabs.count() == 5
assert win.tabs.currentIndex() == 4
assert Path("assets/logo_show_gown_needle.png").exists()
assert win.logo_label.pixmap() is not None and not win.logo_label.pixmap().isNull()
assert win.vision_model.currentText() == "MiniMax-M3"
assert win.text_model.currentText() == "deepseek-v4.1-flash"
assert win.image_model.currentText() == "gpt-image-2.5"
win.silhouette.setCurrentText("鱼尾")
win.fabric.setCurrentText("亮片")
win.refresh_parameter_json()
assert '"silhouette": "鱼尾"' in win.parameter_json.toPlainText()
assert win.parameters()["measurements"]["waist_cm"] == 64.0
win.analysis_output.setPlainText('{"主题":"午夜花园"}')
win.plan_output.setPlainText("鱼尾礼服设计方案：黑金缎面与珠绣。")
win.brief_input.setPlainText("鱼尾 蕾丝 立体裁剪")
win.extract_inspiration()
assert win.workflow_status["inspiration"] == "待确认"
win.confirm_inspiration()
assert win.workflow_status["inspiration"] == "已确认"
context = win.refresh_knowledge_context()
assert win.knowledge_matches
assert win.model_styling_decision["status"] == "selected_from_database"
assert win.model_styling_decision["selected_evidence_id"] in win.model_styling_decision["evidence_ids"]
assert win.model_styling_decision["database_acceptance"]["reject_if_missing"] is True
assert "书籍" in context
assert "鱼尾" in win.knowledge_output.toPlainText()
assert "已命中" in win.knowledge_status.text()
assert win.design_recommendations["evidence_count"] == len(win.knowledge_matches)
assert "evidence_ids" in win.knowledge_output.toPlainText()
assert "数据库模特造型决策" in context
assert "数据库纸样决策证据" in context
assert win.design_recommendations["pattern_decision"]["decision_source"] == "SQLite pattern_knowledge"
assert len(win.pattern_matches) > 0
assert set(win.design_recommendations["all_database_dimensions"]["dress_dimensions"]) >= {"silhouette", "fabric", "construction", "movement", "risk", "pattern_seam_lines", "pattern_darts"}
assert "professional_pose_terms" in win.design_recommendations["all_database_dimensions"]["model_styling_dimensions"]
assert "数据库记录未明确" in win.dimension_output.toPlainText() or win.design_recommendations["all_database_dimensions"]["model_styling_dimensions"]["professional_pose_terms"]
assert win.config["image_timeout_seconds"] == 360
assert win.design_recommendations["evidence_count"] > 0
assert win.design_audit["evidence_count"] == len(win.knowledge_matches)
win.accept_database_decision()
assert win.database_decision_confirmed is True
win.lock_final_design()
assert win.final_design_locked is True
assert Path("final_design_spec.json").exists()
assert json.loads(Path("final_design_spec.json").read_text(encoding="utf-8"))["parameters"]["silhouette"] == "鱼尾"
assert "all_database_dimensions" in json.loads(Path("final_design_spec.json").read_text(encoding="utf-8"))
pattern_spec = Path("pattern_reference_spec.json")
assert pattern_spec.exists()
pattern_data = json.loads(pattern_spec.read_text(encoding="utf-8"))
assert len(pattern_data["views"]) == 14
assert {"01_front_static", "02_back_static", "05_walking_stride", "06_turn_45deg", "08_back_walking"} <= {v["id"] for v in pattern_data["views"]}
assert "午夜花园" in win.image_prompt()
assert "鱼尾" in win.image_prompt()
assert "final_design_spec" in win.image_prompt()
assert "database_acceptance" in win.image_prompt()
assert "中国成年女性真人时装摄影感" in win.image_prompt()
assert "AI 伪影" in win.image_prompt()
win.unlock_final_design()
assert win.final_design_locked is False
win.lock_final_design()
audit = audit_design({"silhouette": "鱼尾", "train": "超长拖尾", "craft": "珠绣", "fabric": "透明欧根纱", "measurements": {}}, win.design_recommendations)
assert audit["risk_count"] >= 3
assert "preflight_audit" in format_decision_package(win.design_recommendations, audit)
assert "gpt-image-2.5" in default_config()["image_model"]
assert default_config()["image_timeout_seconds"] == 360
assert "pattern_pack_output_dir" in default_config()

with tempfile.TemporaryDirectory() as td:
    # Original bytes are read for API transport only; no image decoding or processing.
    sample = Path(td) / "reference.png"
    sample.write_bytes(b"\x89PNG\r\n\x1a\nraw-test")
    mime, encoded = read_raw_image(sample)
    assert mime == "image/png"
    import base64
    assert base64.b64decode(encoded) == sample.read_bytes()

source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
for token in ["/chat/completions", "/images/generations", "/images/edits", "image[]", "qwen3.7-plus", "gpt-image-2.5"]:
    assert token in source
assert '"n": 1' in source
assert '"response_format": "b64_json"' not in source
assert "model_styling_decision" in source and "search_model_styling" in source
assert "final_design_spec" in source and "锁定最终设计" in source and "图片验收通过" in source
assert 'self.start_chat([{"role": "user", "content": parts}], self.analysis_done, self.config.get("vision_model")' in source
assert 'max_completion_tokens": 8000' in source
assert 'finish_reason=' in source and '推理字段长度=' in source
assert "reasoning_effort" in source and "model_key" in source
for token in ["recommend_design", "audit_design", "project_decision_audits", "generation_audits", "evidence_ids", "PatternPackWorker", "pattern_reference_spec.json", "professional_pose_terms", "00_overview_contact_sheet.png", "show_pattern_overview", "open_pattern_directory", "choose_pattern_output_dir", "pattern_pack_output_dir", "每张图成功后立即写入本地", "查看打版参考包总览", "path.write_bytes(data)"]:
    assert token in source or token in (Path(__file__).resolve().parents[1] / "dress_knowledge.py").read_text(encoding="utf-8")
for token in ["logo_show_gown_needle.png", "DATABASE-LED COUTURE WORKBENCH", "QLabel#subtitle", "QTabBar::tab:selected"]:
    assert token in source
bundled_db = Path(__file__).resolve().parents[1] / "show_gown_studio.db"
assert bundled_db.exists()
import sqlite3
with sqlite3.connect(bundled_db) as c:
    assert c.execute("select count(*) from dress_knowledge").fetchone()[0] >= 1063
    assert c.execute("select count(*) from dress_knowledge where book_id='婚纱礼服100款'").fetchone()[0] == 217
    assert c.execute("select count(*) from pattern_knowledge").fetchone()[0] == 217
    pattern_columns = {r[1] for r in c.execute("pragma table_info(pattern_knowledge)")}
    assert {"seam_lines", "darts", "panels", "layers", "draping_clues", "ease_clues", "validation", "risk"} <= pattern_columns
build_script = (Path(__file__).resolve().parents[1] / "build.bat").read_text(encoding="utf-8")
assert "--onefile" in build_script and "--add-data \"show_gown_studio.db;.\"" in build_script and "--add-data \"data;data\"" in build_script
assert all(ord(c) < 128 for c in build_script)
assert "cv2" not in source and "PatternPackWorker" in source and "Image.open" in source
bat = (Path(__file__).resolve().parents[1] / "build.bat").read_text(encoding="utf-8")
assert all(ord(c) < 128 for c in bat)
print("FULLCHAIN_REGRESSION_OK")
