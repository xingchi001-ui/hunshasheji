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
    assert {"projects", "reference_assets", "generation_runs", "dress_knowledge", "project_knowledge_links", "project_decision_audits", "generation_audits"}.issubset(names)
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
assert "午夜花园" in win.image_prompt()
assert "鱼尾" in win.image_prompt()
win.brief_input.setPlainText("鱼尾 蕾丝 立体裁剪")
context = win.refresh_knowledge_context()
assert win.knowledge_matches
assert "书籍" in context
assert "鱼尾" in win.knowledge_output.toPlainText()
assert "已命中" in win.knowledge_status.text()
assert win.design_recommendations["evidence_count"] == len(win.knowledge_matches)
assert "evidence_ids" in win.knowledge_output.toPlainText()
assert win.design_recommendations["evidence_count"] > 0
assert win.design_audit["evidence_count"] == len(win.knowledge_matches)
audit = audit_design({"silhouette": "鱼尾", "train": "超长拖尾", "craft": "珠绣", "fabric": "透明欧根纱", "measurements": {}}, win.design_recommendations)
assert audit["risk_count"] >= 3
assert "preflight_audit" in format_decision_package(win.design_recommendations, audit)
assert "gpt-image-2.5" in default_config()["image_model"]

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
assert 'self.start_chat([{"role": "user", "content": parts}], self.analysis_done, self.config.get("vision_model")' in source
assert 'max_completion_tokens": 8000' in source
assert 'finish_reason=' in source and '推理字段长度=' in source
assert "reasoning_effort" in source and "model_key" in source
for token in ["recommend_design", "audit_design", "project_decision_audits", "generation_audits", "evidence_ids"]:
    assert token in source or token in (Path(__file__).resolve().parents[1] / "dress_knowledge.py").read_text(encoding="utf-8")
bundled_db = Path(__file__).resolve().parents[1] / "show_gown_studio.db"
assert bundled_db.exists()
import sqlite3
with sqlite3.connect(bundled_db) as c:
    assert c.execute("select count(*) from dress_knowledge").fetchone()[0] == 846
assert "PIL" not in source and "cv2" not in source and "Image.open" not in source
bat = (Path(__file__).resolve().parents[1] / "build.bat").read_text(encoding="utf-8")
assert all(ord(c) < 128 for c in bat)
print("FULLCHAIN_REGRESSION_OK")
