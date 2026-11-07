import concurrent.futures
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACK = Path('/home/ubuntu/pattern_reference_pack_consistent')
LOCK = threading.Lock()

def run_cmd(name, command, timeout=180):
    start = time.time()
    env = os.environ.copy()
    env['QT_QPA_PLATFORM'] = 'offscreen'
    proc = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f'{name} failed\nSTDOUT:\n{proc.stdout[-2000:]}\nSTDERR:\n{proc.stderr[-2000:]}')
    return {'name': name, 'seconds': round(time.time() - start, 2), 'detail': proc.stdout.strip()[-500:]}

def check_syntax():
    return run_cmd('语法与编译', [sys.executable, '-m', 'py_compile', 'main.py', 'dress_knowledge.py', 'tests/test_regression.py', 'tests/test_parallel_regression.py'])

def check_fullchain():
    return run_cmd('GUI与数据库全链路', [sys.executable, 'tests/test_regression.py'], timeout=240)

def check_database():
    start = time.time()
    import sqlite3
    with sqlite3.connect(ROOT / 'show_gown_studio.db') as c:
        dress = c.execute('select count(*) from dress_knowledge').fetchone()[0]
        pattern = c.execute('select count(*) from pattern_knowledge').fetchone()[0]
        styling = c.execute('select count(*) from model_styling_knowledge').fetchone()[0]
        book = c.execute("select count(*) from dress_knowledge where book_id='婚纱礼服100款'").fetchone()[0]
        cols = {r[1] for r in c.execute('pragma table_info(pattern_knowledge)')}
    assert dress >= 1063 and pattern == 217 and styling >= 600 and book == 217
    assert {'seam_lines','darts','panels','layers','draping_clues','ease_clues','validation','risk'} <= cols
    return {'name':'SQLite数据库完整性','seconds':round(time.time()-start,2),'detail':f'dress={dress}, pattern={pattern}, styling={styling}, new_book={book}'}

def check_source_contract():
    start = time.time()
    source = (ROOT / 'main.py').read_text(encoding='utf-8')
    build = (ROOT / 'build.bat').read_text(encoding='utf-8')
    for token in ['PatternPackWorker','anchor_reference','pattern_reference_spec.json','professional_pose_terms','final_design_spec','图片验收通过','/images/edits']:
        assert token in source, token
    assert '--onefile' in build and all(ord(c) < 128 for c in build)
    return {'name':'一致性母版与打包契约','seconds':round(time.time()-start,2),'detail':'anchor_reference, final spec, pattern pack, ASCII build.bat OK'}

def check_pattern_pack():
    start = time.time()
    manifest = json.loads((PACK / 'pattern_reference_manifest.json').read_text(encoding='utf-8'))
    files = list(PACK.glob('*.png'))
    ids = {f'{i:02d}_{name}' for i, name in enumerate([], 1)}
    expected = ['01_front_static','02_back_static','03_left_side_static','04_right_side_static','05_walking_stride','06_turn_45deg','07_turn_90deg','08_back_walking','09_veil_skirt_spread','10_bodice_waist_detail','11_side_hip_detail','12_neckline_armhole_detail','13_skirt_train_spread','14_layer_structure_detail']
    assert manifest.get('consistency_mode') == 'anchor_reference'
    assert manifest.get('failed_views') == []
    assert len(manifest.get('created_files', [])) == 14
    assert all((PACK / f'{name}.png').exists() and (PACK / f'{name}.png').stat().st_size > 100000 for name in expected)
    assert (PACK / '00_overview_contact_sheet.png').exists()
    return {'name':'14张同一礼服视觉包','seconds':round(time.time()-start,2),'detail':f"consistency={manifest['consistency_mode']}, png={len(files)}, failed=0"}

def check_api_smoke_artifact():
    start = time.time()
    smoke = Path('/home/ubuntu/controlled_workflow_image_smoke.png')
    front = PACK / '01_front_static.png'
    assert smoke.exists() and smoke.stat().st_size > 100000
    assert front.exists() and front.stat().st_size > 100000
    return {'name':'图片API输出资产','seconds':round(time.time()-start,2),'detail':f'smoke={smoke.stat().st_size}, anchor={front.stat().st_size} bytes'}

def main():
    tasks = [check_syntax, check_fullchain, check_database, check_source_contract, check_pattern_pack, check_api_smoke_artifact]
    completed = 0
    results = []
    failures = []
    total = len(tasks)
    print(f'并行回归启动：{total} 个线程任务', flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=total) as pool:
        futures = {pool.submit(task): task.__name__ for task in tasks}
        for future in concurrent.futures.as_completed(futures):
            completed += 1
            try:
                result = future.result(); results.append(result)
                print(f"[{'#' * completed}{'.' * (total-completed)}] {completed}/{total} PASS {result['name']} ({result['seconds']}s) · {result['detail']}", flush=True)
            except Exception as exc:
                failures.append({'task': futures[future], 'error': str(exc)})
                print(f"[{'#' * completed}{'.' * (total-completed)}] {completed}/{total} FAIL {futures[future]} · {exc}", flush=True)
    report = {'result': 'PARALLEL_REGRESSION_OK' if not failures else 'PARALLEL_REGRESSION_FAIL', 'total': total, 'passed': len(results), 'failed': failures, 'results': results, 'created_at': time.strftime('%Y-%m-%dT%H:%M:%S%z')}
    (ROOT / 'parallel_regression_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if not failures else 1

if __name__ == '__main__':
    raise SystemExit(main())
