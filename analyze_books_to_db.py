#!/usr/bin/env python3
"""Analyze supplied gown-design PDFs page by page with MiniMax-M3 vision.

The pipeline intentionally sends rendered page images only. It does not OCR,
extract page text, or store a transcription. Results are resumable JSONL and
can be imported into the app's SQLite dress_knowledge table.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import requests

FACETS = (
    "page_type", "visible_garment", "garment_type", "silhouette", "neckline",
    "sleeve", "waistline", "skirt_shape", "fabric", "surface_detail",
    "construction", "occasion", "color", "design_concept", "visual_effect",
    "movement", "fit", "risk", "lighting", "process", "compatibility",
    "design_inspiration", "visual_evidence", "uncertainty",
    "pattern_seam_lines", "pattern_darts", "pattern_panels", "pattern_layers",
    "pattern_draping_clues", "pattern_ease_clues", "pattern_material_relation",
    "pattern_process", "pattern_validation", "pattern_risk",
)

ANALYSIS_PROMPT = """请把这张服装/礼服书页作为视觉设计资料进行精读，只分析可见的视觉与造型信息，不要OCR，不要读取、转录、翻译或复述图片中的任何文字。
输出严格为中文 JSON 对象，字段必须包含：
页面类型、可见服装、服装类别、廓形、领型、袖型、腰线、裙型、面料、表面细节、结构工艺、适用场合、色彩、设计概念、视觉效果、动态效果、合体与穿着、风险、灯光、工艺流程、搭配兼容、设计启发、视觉证据、不确定性、纸样分割线、纸样省道、纸样拼片、纸样层级、立体裁剪线索、放量线索、面料结构关系、纸样工艺顺序、纸样验证项目、纸样风险。
每个字段的值使用字符串数组，每个字段最多 5 条、每条不超过 40 个汉字；页面类型用一个或多个短标签。若页面是封面、目录、版权页、纯图示、空白页或无法看清，仍需标记，并把不确定性写清楚。只根据图像可见内容，不要臆造不可见的纸样尺寸、人体量体或内层结构。"""

KEY_MAP = {
    "页面类型": "page_type", "可见服装": "visible_garment", "服装类别": "garment_type",
    "廓形": "silhouette", "领型": "neckline", "袖型": "sleeve", "腰线": "waistline",
    "裙型": "skirt_shape", "面料": "fabric", "表面细节": "surface_detail",
    "结构工艺": "construction", "适用场合": "occasion", "色彩": "color",
    "设计概念": "design_concept", "视觉效果": "visual_effect", "动态效果": "movement",
    "合体与穿着": "fit", "风险": "risk", "灯光": "lighting", "工艺流程": "process",
    "搭配兼容": "compatibility", "设计启发": "design_inspiration", "视觉证据": "visual_evidence",
    "不确定性": "uncertainty",
    "纸样分割线": "pattern_seam_lines", "纸样省道": "pattern_darts",
    "纸样拼片": "pattern_panels", "纸样层级": "pattern_layers",
    "立体裁剪线索": "pattern_draping_clues", "放量线索": "pattern_ease_clues",
    "面料结构关系": "pattern_material_relation", "纸样工艺顺序": "pattern_process",
    "纸样验证项目": "pattern_validation", "纸样风险": "pattern_risk",
}


def clean_name(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z一-龥_-]+", "_", name).strip("_")[:100] or "book"


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        values = value
    else:
        values = [value]
    return [str(v).strip() for v in values if str(v).strip()]


def parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise
        data = json.loads(match.group(0))
    return data if isinstance(data, dict) else {"页面类型": ["模型返回非对象"]}


def render_page(pdf: Path, page: int, output: Path, dpi: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    prefix = output.with_suffix("")
    subprocess.run(
        ["pdftoppm", "-f", str(page), "-l", str(page), "-jpeg", "-r", str(dpi), str(pdf), str(prefix)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    rendered = sorted(output.parent.glob(prefix.name + "-*.jpg"))
    if not rendered:
        raise RuntimeError(f"pdftoppm did not render page: {pdf} p.{page}")
    shutil.move(str(rendered[0]), str(output))


def call_vision(session: requests.Session, base_url: str, api_key: str, image: Path, model: str, timeout: int) -> tuple[dict[str, Any], dict[str, Any]]:
    encoded = base64.b64encode(image.read_bytes()).decode("ascii")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是婚纱礼服设计研究员。输出结构化中文JSON；严禁OCR和文字转录。"},
            {"role": "user", "content": [
                {"type": "text", "text": ANALYSIS_PROMPT},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + encoded}},
            ]},
        ],
        "max_completion_tokens": 12000,
        "reasoning_effort": "low",
    }
    last_error = None
    for attempt in range(6):
        try:
            response = session.post(
                base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
                json=payload, timeout=timeout,
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            last_error = exc
            if attempt < 5:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"vision request failed after retries: {exc}") from exc
        response.raise_for_status()
        body = response.json()
        choice = (body.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        if not content:
            last_error = RuntimeError(f"empty model content; finish_reason={choice.get('finish_reason')}")
        else:
            try:
                return parse_json(content), {"finish_reason": choice.get("finish_reason"), "usage": body.get("usage", {})}
            except (json.JSONDecodeError, TypeError) as exc:
                last_error = exc
        if attempt < 5:
            if choice.get("finish_reason") == "length":
                payload["messages"][1]["content"][0]["text"] = "只返回完整JSON，不要OCR或复述文字。对这张服装设计页，只填以下字段，每个字段最多2条、每条不超过20字：页面类型、服装类别、廓形、领型、袖型、腰线、裙型、面料、表面细节、结构工艺、色彩、动态效果、纸样分割线、纸样省道、纸样拼片、纸样层级、立体裁剪线索、放量线索、面料结构关系、纸样工艺顺序、纸样验证项目、纸样风险、设计启发、不确定性。所有值必须是字符串数组。"
            else:
                payload["messages"][1]["content"][0]["text"] = ANALYSIS_PROMPT + "\n再次强调：只返回完整 JSON，不要解释，不要 Markdown 代码围栏。"
            time.sleep(1)
    raise RuntimeError(f"model JSON parse failed after retry: {last_error}")


def make_row(book_id: str, pdf: Path, page: int, analysis: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, list[str]] = {facet: [] for facet in FACETS}
    for key, value in analysis.items():
        facet = KEY_MAP.get(str(key), str(key) if str(key) in FACETS else None)
        if facet in normalized:
            normalized[facet].extend(as_list(value))
    for facet in normalized:
        normalized[facet] = list(dict.fromkeys(normalized[facet]))
    uncertain = normalized["uncertainty"]
    page_type = " ".join(normalized["page_type"])
    review = bool(uncertain) or any(x in page_type for x in ("无法", "空白", "目录", "版权", "封面"))
    text_parts = []
    for facet in ("page_type", "visible_garment", "silhouette", "fabric", "surface_detail", "construction", "design_concept", "design_inspiration"):
        if normalized[facet]:
            text_parts.append(f"{facet}=" + "、".join(normalized[facet][:8]))
    return {
        "id": f"{book_id}:p{page:04d}:s0001",
        "book_id": book_id,
        "page": page,
        "text": "；".join(text_parts),
        "source": {"pdf": pdf.name, "page": page, "visual_only": True, "model": meta.get("model", "MiniMax-M3"), "finish_reason": meta.get("finish_reason")},
        "knowledge": normalized,
        "review": {"needs_human_check": review, "reason": "；".join(uncertain[:4]) if uncertain else ""},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="逐本逐页进行非 OCR 礼服视觉理解并导入 SQLite")
    parser.add_argument("pdf_dir", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/dress_training/segments.jsonl"))
    parser.add_argument("--db", type=Path, default=Path("show_gown_studio.db"))
    parser.add_argument("--base-url", default=os.getenv("LLM_TOKEN_BASE_URL", "https://api.llm-token.cn/v1"))
    parser.add_argument("--api-key", default=os.getenv("LLM_TOKEN_API_KEY", ""))
    parser.add_argument("--model", default="MiniMax-M3")
    parser.add_argument("--dpi", type=int, default=120)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 页，用于试跑")
    parser.add_argument("--start-page", type=int, default=1, help="每本书的起始页（含）")
    parser.add_argument("--end-page", type=int, default=0, help="每本书的结束页（含），0 表示末页")
    parser.add_argument("--no-import", action="store_true")
    parser.add_argument("--failures", type=Path, default=Path("data/dress_training/failed_pages.jsonl"))
    parser.add_argument("--retry-failures", action="store_true", help="仅重试失败页")
    args = parser.parse_args()
    if not args.api_key:
        raise SystemExit("missing API key: pass --api-key or LLM_TOKEN_API_KEY")
    pdfs = sorted(args.pdf_dir.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"no PDFs under {args.pdf_dir}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    completed: dict[str, dict[str, Any]] = {}
    if args.output.exists():
        with args.output.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    completed[row["id"]] = row
    failure_rows: dict[str, dict[str, Any]] = {}
    if args.failures.exists():
        with args.failures.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    failure_rows[row["id"]] = row
    if args.retry_failures:
        wanted = set(failure_rows)
    else:
        wanted = None
    args.failures.parent.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    total = 0
    with tempfile.TemporaryDirectory(prefix="dress_pages_") as temp:
        temp_dir = Path(temp)
        with args.output.open("a", encoding="utf-8") as out:
            for pdf in pdfs:
                book_id = clean_name(pdf.stem)
                info = subprocess.check_output(["pdfinfo", str(pdf)], text=True)
                pages = int(re.search(r"^Pages:\s+(\d+)", info, re.M).group(1))
                page_limit = min(pages, args.limit) if args.limit else pages
                start_page = max(1, int(args.start_page))
                end_page = min(page_limit, int(args.end_page)) if args.end_page else page_limit
                for page in range(start_page, end_page + 1):
                    rid = f"{book_id}:p{page:04d}:s0001"
                    if rid in completed:
                        continue
                    if rid in failure_rows and not args.retry_failures:
                        continue
                    if wanted is not None and rid not in wanted:
                        continue
                    image = temp_dir / f"{book_id}_{page:04d}.jpg"
                    try:
                        render_page(pdf, page, image, args.dpi)
                        analysis, meta = call_vision(session, args.base_url, args.api_key, image, args.model, args.timeout)
                        meta["model"] = args.model
                        row = make_row(book_id, pdf, page, analysis, meta)
                        out.write(json.dumps(row, ensure_ascii=False) + "\n")
                        out.flush()
                        completed[rid] = row
                        failure_rows.pop(rid, None)
                        with args.failures.open("w", encoding="utf-8") as fail_out:
                            for failure in failure_rows.values():
                                fail_out.write(json.dumps(failure, ensure_ascii=False) + "\n")
                        total += 1
                        print(f"ANALYZED {book_id} page={page}/{pages} review={row['review']['needs_human_check']}", flush=True)
                    except Exception as exc:
                        print(f"ERROR {book_id} page={page}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                        failure_rows[rid] = {"id": rid, "book_id": book_id, "page": page, "pdf": pdf.name, "error": f"{type(exc).__name__}: {exc}"}
                        with args.failures.open("w", encoding="utf-8") as fail_out:
                            for failure in failure_rows.values():
                                fail_out.write(json.dumps(failure, ensure_ascii=False) + "\n")
                        continue
                    finally:
                        image.unlink(missing_ok=True)
                    if args.sleep:
                        time.sleep(args.sleep)
    if not args.no_import and not failure_rows:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from import_dress_data import main as import_main
        old_argv = sys.argv
        try:
            sys.argv = ["import_dress_data.py", str(args.output), "--db", str(args.db), "--replace"]
            import_main()
        finally:
            sys.argv = old_argv
    print(f"DONE newly_analyzed={total} total_records={len(completed)} failures={len(failure_rows)} output={args.output} db={args.db}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
