import base64
import json
import mimetypes
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import ProjectDB

ROOT = Path(__file__).resolve().parents[1]
BASE = os.getenv("LLM_TOKEN_BASE_URL", "https://api.llm-token.cn/v1").rstrip("/")
KEY = os.environ["LLM_TOKEN_API_KEY"]
REF = ROOT / "gpt_image_25_live_test.png"
REPORT = ROOT / "live_fullchain_report.json"
OUT = ROOT / "live_fullchain_gown.png"


def chat(model, messages, max_tokens=1800):
    r = requests.post(BASE + "/chat/completions", headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}, json={"model": model, "messages": messages, "temperature": 0.4, "max_tokens": max_tokens}, timeout=180)
    r.raise_for_status()
    return r.json()["choices"][0]["message"].get("content", "")


def main():
    raw = REF.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    mime = mimetypes.guess_type(REF.name)[0] or "image/png"
    vision_prompt = "请识别这张秀场礼服参考图，输出适合设计工作流的中文 JSON，字段包括：主题、主色、辅助色、廓形、领型、袖型、面料质感、工艺、灯光、构图视角、可借鉴的设计平台维度、需要避免直接复制的元素。"
    analysis = chat("qwen3.7-plus", [{"role": "user", "content": [{"type": "text", "text": vision_prompt}, {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}]}], 1800)
    plan_prompt = f"你是秀场礼服设计总监。基于下面的多模态识别结果和设计目标，生成可执行的中文设计方案，包含：设计概念、廓形比例、面料色彩、工艺拆解、舞台动态、打版试衣清单、最终图片生成提示词。识别结果：{analysis}。设计目标：午夜花园、液态金属、黑金鱼尾礼服、闭幕秀、完整全身视觉。"
    plan = chat("deepseek-v4.1-flash", [{"role": "user", "content": plan_prompt}], 2200)
    params = {"silhouette": "鱼尾", "neckline": "心形", "sleeve": "无袖", "fabric": "缎面与透明欧根纱", "main_color": "黑色", "accent_color": "古金", "train": "中拖尾", "craft": "珠绣、立体花饰、金属刺绣", "view": "纵向全身、三分之二视角、秀场灯光"}
    db = ProjectDB(ROOT / "live_fullchain.db")
    project_id = db.save({"project_name": "真实全链路测试 · 中国秀场模特", "analysis": {"raw": analysis}, "design_plan": plan, "parameters": params, "notes": "由真实 API 四阶段链路生成"}, [str(REF)])
    prompt = f"高级时装秀场礼服最终效果图。多模态参考识别：{analysis}。AI 设计方案：{plan}。已确认结构化参数：{json.dumps(params, ensure_ascii=False)}。生成一位中国秀场模特的全身专业时装视觉，黑金鱼尾礼服，完整展示结构和面料层次，午夜花园与液态金属氛围，专业时装摄影，纵向构图，无文字、无水印、无品牌标识。"
    files = [("image[]", (REF.name, open(REF, "rb"), "image/png"))]
    try:
        r = requests.post(BASE + "/images/edits", headers={"Authorization": f"Bearer {KEY}"}, data={"model": "gpt-image-2.5", "prompt": prompt, "size": "1024x1536", "quality": "low", "n": "1", "response_format": "b64_json"}, files=files, timeout=240)
    finally:
        files[0][1][1].close()
    r.raise_for_status()
    item = (r.json().get("data") or [{}])[0]
    if item.get("b64_json"):
        image_bytes = base64.b64decode(item["b64_json"])
    else:
        image_response = requests.get(item["url"], timeout=180); image_response.raise_for_status(); image_bytes = image_response.content
    OUT.write_bytes(image_bytes)
    db.record_generation(project_id, "gpt-image-2.5", prompt, str(OUT))
    report = {"created_at": datetime.now().isoformat(timespec="seconds"), "vision_model": "qwen3.7-plus", "text_model": "deepseek-v4.1-flash", "image_model": "gpt-image-2.5", "reference": str(REF), "analysis": analysis, "design_plan": plan, "parameters": params, "project_id": project_id, "database": str(ROOT / "live_fullchain.db"), "output": str(OUT), "output_bytes": len(image_bytes)}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("LIVE_FULLCHAIN_OK")
    print(json.dumps({"output": str(OUT), "bytes": len(image_bytes), "report": str(REPORT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
