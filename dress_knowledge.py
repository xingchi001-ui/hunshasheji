import json
import sqlite3
from pathlib import Path
from typing import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS dress_knowledge (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL,
    page INTEGER NOT NULL,
    text TEXT NOT NULL,
    source_json TEXT NOT NULL,
    knowledge_json TEXT NOT NULL,
    review_required INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dress_knowledge_book_page
    ON dress_knowledge(book_id, page);
CREATE INDEX IF NOT EXISTS idx_dress_knowledge_review
    ON dress_knowledge(review_required);
"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)


def _terms(row: dict) -> str:
    terms = []
    for values in (row.get("knowledge") or {}).values():
        if isinstance(values, list):
            terms.extend(str(v) for v in values)
    terms.append(str(row.get("text", "")))
    return " ".join(t for t in terms if t)


def import_segments(connection: sqlite3.Connection, jsonl_path: str | Path, replace: bool = False) -> int:
    """Import local sentence-level training data into the app database.

    The source JSONL is intentionally external to the public repository. This
    function accepts the previously generated segments.jsonl contract and keeps
    provenance plus review state for auditability.
    """
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
                (
                    row["id"],
                    row["book_id"],
                    int(row["page"]),
                    row.get("text", ""),
                    json.dumps(row.get("source", {}), ensure_ascii=False),
                    json.dumps(row.get("knowledge", {}), ensure_ascii=False),
                    int(bool((row.get("review") or {}).get("needs_human_check"))),
                ),
            )
            count += 1
    connection.commit()
    return count


def search_segments(connection: sqlite3.Connection, query: str, limit: int = 8) -> list[dict]:
    """Return deterministic keyword matches for prompt augmentation."""
    ensure_schema(connection)
    tokens = [t.strip() for t in query.replace("，", " ").replace("、", " ").split() if t.strip()]
    if not tokens:
        return []
    clauses = " OR ".join("(text LIKE ? OR knowledge_json LIKE ?)" for _ in tokens)
    params = [value for token in tokens for value in (f"%{token}%", f"%{token}%")]
    rows = connection.execute(
        f"SELECT id, book_id, page, text, knowledge_json, review_required FROM dress_knowledge WHERE {clauses} ORDER BY review_required ASC, book_id, page LIMIT ?",
        (*params, int(limit)),
    ).fetchall()
    return [
        {
            "id": r[0], "book_id": r[1], "page": r[2], "text": r[3],
            "knowledge": json.loads(r[4] or "{}"), "review_required": bool(r[5]),
        }
        for r in rows
    ]


def format_context(rows: Iterable[dict], max_chars: int = 3000) -> str:
    """Format retrieved records without claiming they are human-verified facts."""
    parts = ["以下是本地礼服设计知识库的候选检索结果，仅作设计参考；标记 review_required 的记录需要人工核验："]
    for row in rows:
        mark = "需复核" if row.get("review_required") else "已通过当前规则筛选"
        parts.append(f"- [{mark}] 书籍 {row['book_id']} 第 {row['page']} 页：{row['text']}")
    return "\n".join(parts)[:max_chars]
