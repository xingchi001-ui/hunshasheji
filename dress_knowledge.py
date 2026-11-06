import json
import re
import sqlite3
from pathlib import Path
from typing import Iterable

FACETS = ("page_type", "visible_garment", "garment_type", "silhouette", "neckline", "sleeve", "waistline", "skirt_shape", "fabric", "surface_detail", "construction", "occasion", "color", "measurements", "design_concept", "visual_effect", "movement", "fit", "risk", "lighting", "process", "compatibility", "design_inspiration", "visual_evidence", "uncertainty", "pattern_seam_lines", "pattern_darts", "pattern_panels", "pattern_layers", "pattern_draping_clues", "pattern_ease_clues", "pattern_material_relation", "pattern_process", "pattern_validation", "pattern_risk")
SCHEMA = """
CREATE TABLE IF NOT EXISTS dress_knowledge (
    id TEXT PRIMARY KEY, book_id TEXT NOT NULL, page INTEGER NOT NULL, text TEXT NOT NULL,
    source_json TEXT NOT NULL, knowledge_json TEXT NOT NULL, review_required INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dress_knowledge_book_page ON dress_knowledge(book_id, page);
CREATE INDEX IF NOT EXISTS idx_dress_knowledge_review ON dress_knowledge(review_required);
CREATE TABLE IF NOT EXISTS model_styling_knowledge (
    id TEXT PRIMARY KEY, knowledge_id TEXT NOT NULL, book_id TEXT NOT NULL, page INTEGER NOT NULL,
    hairstyle TEXT DEFAULT '', hair_accessory TEXT DEFAULT '', expression TEXT DEFAULT '',
    pose TEXT DEFAULT '', gaze TEXT DEFAULT '', movement TEXT DEFAULT '', compatibility TEXT DEFAULT '',
    evidence_text TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_model_styling_book_page ON model_styling_knowledge(book_id, page);
CREATE TABLE IF NOT EXISTS pattern_knowledge (
    id TEXT PRIMARY KEY, knowledge_id TEXT NOT NULL, book_id TEXT NOT NULL, page INTEGER NOT NULL,
    seam_lines TEXT DEFAULT '', darts TEXT DEFAULT '', panels TEXT DEFAULT '', layers TEXT DEFAULT '',
    draping_clues TEXT DEFAULT '', ease_clues TEXT DEFAULT '', material_relation TEXT DEFAULT '',
    process TEXT DEFAULT '', validation TEXT DEFAULT '', risk TEXT DEFAULT '', evidence_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pattern_knowledge_book_page ON pattern_knowledge(book_id, page);
"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)


def import_segments(connection: sqlite3.Connection, jsonl_path: str | Path, replace: bool = False) -> int:
    ensure_schema(connection)
    path = Path(jsonl_path)
    if replace:
        connection.execute("DELETE FROM dress_knowledge")
    count = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            connection.execute(
                """INSERT OR REPLACE INTO dress_knowledge
                (id, book_id, page, text, source_json, knowledge_json, review_required)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (row["id"], row["book_id"], int(row["page"]), row.get("text", ""),
                 json.dumps(row.get("source", {}), ensure_ascii=False),
                 json.dumps(row.get("knowledge", {}), ensure_ascii=False),
                 int(bool((row.get("review") or {}).get("needs_human_check")))),
            )
            count += 1
    connection.commit()
    rebuild_model_styling_knowledge(connection)
    rebuild_pattern_knowledge(connection)
    return count


def _extract_styling(knowledge: dict, text: str) -> dict:
    """Convert visual evidence into database-owned model styling facets; no random defaults."""
    blob = " ".join([text, json.dumps(knowledge, ensure_ascii=False)])
    groups = {
        "hairstyle": ("发型", "盘发", "发髻", "马尾", "编发", "发丝", "长发", "短发", "波浪发"),
        "hair_accessory": ("头纱", "头饰", "发饰", "冠", "帽", "花环"),
        "expression": ("回眸", "凝视", "微笑", "沉静", "温柔", "冷峻", "优雅", "浪漫", "神秘", "自信"),
        "pose": ("站姿", "坐姿", "抬臂", "展臂", "屈臂", "迈步", "行走", "转身", "侧身", "回眸"),
        "gaze": ("回眸", "凝视", "视线", "看向", "望向"),
        "movement": ("行走", "迈步", "转身", "摆臂", "摆动", "飘动", "扬起", "展开", "延迟摆动", "动态"),
    }
    out = {}
    for field, terms in groups.items():
        out[field] = "、".join(dict.fromkeys(term for term in terms if term in blob))
    compatibility = []
    for field in ("silhouette", "fabric", "occasion", "visual_effect", "lighting"):
        compatibility.extend(str(v) for v in (knowledge.get(field) or []) if str(v).strip())
    out["compatibility"] = "、".join(dict.fromkeys(compatibility[:12]))
    out["evidence_text"] = text[:1200]
    return out


def rebuild_model_styling_knowledge(connection: sqlite3.Connection) -> int:
    ensure_schema(connection)
    connection.execute("DELETE FROM model_styling_knowledge")
    rows = connection.execute("SELECT id, book_id, page, text, knowledge_json FROM dress_knowledge").fetchall()
    inserted = 0
    for knowledge_id, book_id, page, text, knowledge_json in rows:
        knowledge = json.loads(knowledge_json or "{}")
        styling = _extract_styling(knowledge, text)
        if not any(styling.get(k) for k in ("hairstyle", "hair_accessory", "expression", "pose", "gaze", "movement")):
            continue
        connection.execute(
            """INSERT INTO model_styling_knowledge
            (id, knowledge_id, book_id, page, hairstyle, hair_accessory, expression, pose, gaze, movement, compatibility, evidence_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"{knowledge_id}:styling", knowledge_id, book_id, int(page), styling["hairstyle"], styling["hair_accessory"], styling["expression"], styling["pose"], styling["gaze"], styling["movement"], styling["compatibility"], styling["evidence_text"]),
        )
        inserted += 1
    connection.commit()
    return inserted


def rebuild_pattern_knowledge(connection: sqlite3.Connection) -> int:
    """Materialize visual pattern-making clues; blanks mean the page did not show evidence."""
    ensure_schema(connection)
    connection.execute("DELETE FROM pattern_knowledge")
    rows = connection.execute("SELECT id, book_id, page, text, knowledge_json FROM dress_knowledge").fetchall()
    fields = {
        "seam_lines": "pattern_seam_lines", "darts": "pattern_darts", "panels": "pattern_panels",
        "layers": "pattern_layers", "draping_clues": "pattern_draping_clues", "ease_clues": "pattern_ease_clues",
        "material_relation": "pattern_material_relation", "process": "pattern_process",
        "validation": "pattern_validation", "risk": "pattern_risk",
    }
    inserted = 0
    for knowledge_id, book_id, page, text, knowledge_json in rows:
        knowledge = json.loads(knowledge_json or "{}")
        values = {name: "、".join(str(v) for v in (knowledge.get(facet) or []) if str(v).strip()) for name, facet in fields.items()}
        if not any(values.values()):
            continue
        connection.execute(
            """INSERT INTO pattern_knowledge
            (id, knowledge_id, book_id, page, seam_lines, darts, panels, layers, draping_clues, ease_clues, material_relation, process, validation, risk, evidence_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (f"{knowledge_id}:pattern", knowledge_id, book_id, int(page), values["seam_lines"], values["darts"], values["panels"], values["layers"], values["draping_clues"], values["ease_clues"], values["material_relation"], values["process"], values["validation"], values["risk"], text[:1600]),
        )
        inserted += 1
    connection.commit()
    return inserted


def search_pattern_knowledge(connection: sqlite3.Connection, query: str, limit: int = 8) -> list[dict]:
    """Retrieve pattern-making evidence from visual records; never invent measurements."""
    ensure_schema(connection)
    terms = _query_terms(query)
    if not terms:
        return []
    cols = ("id", "knowledge_id", "book_id", "page", "seam_lines", "darts", "panels", "layers", "draping_clues", "ease_clues", "material_relation", "process", "validation", "risk", "evidence_text")
    rows = connection.execute("SELECT " + ", ".join(cols) + " FROM pattern_knowledge").fetchall()
    ranked = []
    for row in rows:
        item = dict(zip(cols, row)); blob = " ".join(str(item.get(k, "")) for k in cols)
        score = sum(3 for term in terms if term in blob)
        if score:
            item["score"] = score; ranked.append(item)
    ranked.sort(key=lambda x: (-x["score"], x["book_id"], x["page"]))
    return ranked[:int(limit)]


def search_model_styling(connection: sqlite3.Connection, query: str, recommendations: dict, rotation_index: int = 0, limit: int = 8) -> dict:
    """Choose model styling only from visual evidence stored in SQLite."""
    ensure_schema(connection)
    terms = _query_terms(query + " " + " ".join(str(v) for v in (recommendations or {}).get("silhouette", [])))
    rows = connection.execute("SELECT id, knowledge_id, book_id, page, hairstyle, hair_accessory, expression, pose, gaze, movement, compatibility, evidence_text FROM model_styling_knowledge").fetchall()
    ranked = []
    for row in rows:
        item = dict(zip(("id", "knowledge_id", "book_id", "page", "hairstyle", "hair_accessory", "expression", "pose", "gaze", "movement", "compatibility", "evidence_text"), row))
        blob = " ".join(str(item.get(k, "")) for k in ("hairstyle", "hair_accessory", "expression", "pose", "gaze", "movement", "compatibility", "evidence_text"))
        score = sum(3 for term in terms if term and term in blob)
        score += sum(2 for term in (recommendations or {}).get("fabric", []) if term in item["compatibility"])
        if item["pose"]: score += 1
        if item["hairstyle"] or item["hair_accessory"]: score += 1
        if score: item["score"] = score; ranked.append(item)
    ranked.sort(key=lambda x: (-x["score"], x["book_id"], x["page"], x["id"]))
    candidates = ranked[:max(1, int(limit))]
    if not candidates:
        return {"status": "no_database_evidence", "decision_source": "SQLite model_styling_knowledge", "evidence_ids": [], "source_pages": []}
    chosen = candidates[int(rotation_index) % len(candidates)]
    hairstyle = chosen["hairstyle"] or "数据库记录未明确发型"
    hair_accessory = chosen["hair_accessory"] or "数据库记录未明确头饰"
    expression = chosen["expression"] or "数据库记录未明确表情"
    pose = chosen["pose"] or "数据库记录未明确姿态"
    gaze = chosen["gaze"] or "数据库记录未明确视线"
    movement = chosen["movement"] or "数据库记录未明确动态"
    garment_requirements = []
    for field in ("silhouette", "fabric", "surface_detail", "construction", "color"):
        garment_requirements.extend((recommendations or {}).get(field, [])[:3])
    must_show = list(dict.fromkeys([v for v in garment_requirements if v]))
    model_requirements = [v for v in (hairstyle, hair_accessory, expression, gaze, pose, movement) if not v.startswith("数据库记录未明确")]
    return {"status": "selected_from_database", "decision_source": "SQLite model_styling_knowledge", "hairstyle": hairstyle, "hair_accessory": hair_accessory, "expression": expression, "pose": pose, "gaze": gaze, "movement": movement, "compatibility": chosen["compatibility"], "evidence_ids": [c["knowledge_id"] for c in candidates], "source_pages": [f"{c['book_id']} p.{c['page']}" for c in candidates], "selected_evidence_id": chosen["knowledge_id"], "selected_source_page": f"{chosen['book_id']} p.{chosen['page']}", "rotation_index": int(rotation_index), "database_acceptance": {"must_show_garment": must_show, "must_show_model": model_requirements, "must_show_pose_and_movement": [v for v in (pose, movement) if not v.startswith("数据库记录未明确")], "reject_if_missing": True}}


def _facet_values(row: dict) -> dict:
    knowledge = row.get("knowledge") or {}
    return {facet: [str(v) for v in (knowledge.get(facet) or []) if str(v).strip()] for facet in FACETS}


def _query_terms(query: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"[\s,，、；;|]+", query or "") if p.strip()]
    return list(dict.fromkeys(parts))


def _score(row: dict, terms: list[str]) -> tuple[int, int]:
    facets = _facet_values(row)
    searchable = " ".join([row.get("text", "")] + [v for values in facets.values() for v in values])
    hits = sum(1 for term in terms if term in searchable)
    facet_hits = sum(1 for values in facets.values() if any(term in " ".join(values) for term in terms))
    return hits * 3 + facet_hits * 2, facet_hits


def search_segments(connection: sqlite3.Connection, query: str, limit: int = 8) -> list[dict]:
    """Rank useful design evidence by text hits, structured facet hits and review state."""
    ensure_schema(connection)
    terms = _query_terms(query)
    if not terms:
        return []
    rows = connection.execute("SELECT id, book_id, page, text, knowledge_json, review_required FROM dress_knowledge").fetchall()
    result = []
    for rid, book_id, page, text, knowledge_json, review_required in rows:
        row = {"id": rid, "book_id": book_id, "page": page, "text": text,
               "knowledge": json.loads(knowledge_json or "{}"), "review_required": bool(review_required)}
        score, facet_hits = _score(row, terms)
        if score:
            row["score"] = score; row["facet_hits"] = facet_hits
            result.append(row)
    result.sort(key=lambda r: (-r["score"], r["review_required"], r["book_id"], r["page"]))
    return result[: int(limit)]


def _merge_values(rows: Iterable[dict]) -> dict:
    merged = {facet: [] for facet in FACETS}
    for row in rows:
        for facet, values in _facet_values(row).items():
            for value in values:
                if value not in merged[facet]:
                    merged[facet].append(value)
    return merged


def recommend_design(rows: list[dict], query: str = "") -> dict:
    """Create a deterministic, inspectable design decision package from retrieved evidence."""
    facets = _merge_values(rows)
    text = " ".join(row.get("text", "") for row in rows)
    q = query or ""
    def pick(names, fallback):
        values = facets.get(names, [])
        return values[:6] if values else fallback
    recommendations = {
        "theme": q[:160],
        "silhouette": pick("silhouette", ["根据设计目标人工确认"]),
        "neckline": pick("neckline", ["根据模特与场景确认"]),
        "sleeve": pick("sleeve", ["根据造型需求确认"]),
        "fabric": pick("fabric", ["根据光泽、垂坠与重量确认"]),
        "surface_detail": pick("surface_detail", ["控制装饰密度并做动态测试"]),
        "construction": pick("construction", ["先做立体裁剪/胚布验证"]),
        "occasion": pick("occasion", ["秀场闭幕造型"]),
        "color": pick("color", ["依据主题与灯光确认"]),
        "measurements": pick("measurements", ["使用实测量体，不从资料臆造数值"]),
        "evidence_count": len(rows),
        "evidence_ids": [row["id"] for row in rows],
        "source_pages": [f"{row['book_id']} p.{row['page']}" for row in rows],
    }
    return recommendations


def audit_design(parameters: dict, recommendations: dict | None = None) -> dict:
    """Rule-based preflight audit for common gown construction and runway risks."""
    p = parameters or {}; rec = recommendations or {}; risks = []; checks = []
    silhouette = str(p.get("silhouette", "")); train = str(p.get("train", "")); craft = str(p.get("craft", "")); fabric = str(p.get("fabric", ""))
    if "鱼尾" in silhouette and ("超长" in train or "长拖尾" in train): risks.append({"level":"高", "rule":"鱼尾+长拖尾", "message":"膝下活动与转身空间可能不足，必须做行走、转身和上下台测试。"})
    if any(x in craft for x in ("珠绣", "亮片", "金属", "立体")):
        risks.append({"level":"中", "rule":"高密度装饰", "message":"装饰会增加重量和摩擦，需检查肩/腰承重点、内衬固定和长时间穿着舒适度。"})
    if "透明" in fabric or "欧根纱" in fabric:
        risks.append({"level":"中", "rule":"透明/轻薄层", "message":"需做灯光透视、静电、接缝和内层遮蔽测试。"})
    if not p.get("measurements"):
        risks.append({"level":"中", "rule":"缺少量体", "message":"当前方案没有完整量体数据，不能直接进入打版生产。"})
    checks.extend(["核对廓形与面料垂坠关系", "进行静态试衣与动态走台", "记录设计师最终取舍并复核来源"])
    return {"risk_count": len(risks), "risks": risks, "checks": checks, "evidence_count": rec.get("evidence_count", 0)}


def format_context(rows: Iterable[dict], max_chars: int = 5000) -> str:
    rows = list(rows); parts = ["以下是本地礼服设计知识库的候选证据，仅作设计参考；需复核记录不可视为已验证事实："]
    for row in rows:
        mark = "需复核" if row.get("review_required") else "规则筛选通过"
        facets = _facet_values(row)
        compact = "; ".join(f"{k}={','.join(v[:4])}" for k, v in facets.items() if v)
        parts.append(f"- [{mark}] 书籍 {row['book_id']} 第{row['page']}页 | score={row.get('score', 0)} | {compact} | 原文：{row['text']}")
    return "\n".join(parts)[:max_chars]


def format_decision_package(recommendations: dict, audit: dict) -> str:
    return json.dumps({"design_recommendations": recommendations, "preflight_audit": audit}, ensure_ascii=False, indent=2)
