import json
import re
import sqlite3
from pathlib import Path
from typing import Iterable

FACETS = ("garment_type", "silhouette", "neckline", "sleeve", "waistline", "skirt_shape", "fabric", "surface_detail", "construction", "occasion", "color", "measurements")
SCHEMA = """
CREATE TABLE IF NOT EXISTS dress_knowledge (
    id TEXT PRIMARY KEY, book_id TEXT NOT NULL, page INTEGER NOT NULL, text TEXT NOT NULL,
    source_json TEXT NOT NULL, knowledge_json TEXT NOT NULL, review_required INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dress_knowledge_book_page ON dress_knowledge(book_id, page);
CREATE INDEX IF NOT EXISTS idx_dress_knowledge_review ON dress_knowledge(review_required);
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
    return count


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
