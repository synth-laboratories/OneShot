#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

RUN_DIR_INPUT="${1:-}"
TASK_DIR_INPUT="${2:-}"

if [[ -z "$RUN_DIR_INPUT" ]]; then
  echo "Usage: $0 <run_dir> <prepared_task_dir>" >&2
  exit 1
fi

# Normalize paths
if [[ "$RUN_DIR_INPUT" != /* ]]; then RUN_DIR_INPUT="$REPO_ROOT/$RUN_DIR_INPUT"; fi
if [[ "$TASK_DIR_INPUT" != /* ]]; then TASK_DIR_INPUT="$REPO_ROOT/$TASK_DIR_INPUT"; fi

RUN_DIR="$RUN_DIR_INPUT"
TASK_DIR="$TASK_DIR_INPUT"

if [[ ! -d "$RUN_DIR" ]]; then
  echo "Error: run_dir does not exist: $RUN_DIR" >&2
  exit 1
fi
if [[ ! -d "$TASK_DIR" ]]; then
  echo "Error: prepared_task_dir does not exist: $TASK_DIR" >&2
  exit 1
fi

echo "[eval-docker] run_dir=$RUN_DIR"
echo "[eval-docker] task_dir=$TASK_DIR"

# Pick diff file from run artifacts
DIFF_FILE=""
if [[ -f "$RUN_DIR/artifacts/container_git_diff_from_baseline.patch" ]]; then
  DIFF_FILE="$RUN_DIR/artifacts/container_git_diff_from_baseline.patch"
elif [[ -f "$RUN_DIR/artifacts/diff.patch" ]]; then
  DIFF_FILE="$RUN_DIR/artifacts/diff.patch"
else
  echo "Warning: No diff patch found in $RUN_DIR/artifacts; tests will run against baseline repo" >&2
fi

# Build image from prepared task
IMAGE_TAG="oneshot-eval-$(date +%s)"
echo "[eval-docker] Building image: $IMAGE_TAG"
docker build -t "$IMAGE_TAG" "$TASK_DIR" | cat

CONTAINER_NAME="eval_${IMAGE_TAG}"

echo "[eval-docker] Starting container for evaluation..."
docker run --name "$CONTAINER_NAME" -d \
  --rm \
  -v "$RUN_DIR/artifacts:/runs/artifacts:ro" \
  -e OPENAI_API_KEY="${OPENAI_API_KEY:-}" \
  "$IMAGE_TAG" >/dev/null

# Create evaluation runner on host then copy into container (avoids nested heredoc quoting)
TMP_EVAL_PY=$(mktemp)
cat > "$TMP_EVAL_PY" << 'PY'
import json, subprocess, os, sys, pathlib, time

TB_META = '/app/tb_meta.json'
ART_DIR = '/app/artifacts'
REPO_DIR = '/app/repo'
DIFF_CANDIDATES = [
    '/runs/artifacts/container_git_diff_from_baseline.patch',
    '/runs/artifacts/diff.patch'
]

def load_meta():
    with open(TB_META) as f:
        return json.load(f)

def apply_diff(repo_dir: str) -> None:
    diff_path = None
    for p in DIFF_CANDIDATES:
        if os.path.isfile(p):
            diff_path = p
            break
    if not diff_path:
        print('[eval-docker] No diff file found; skipping apply')
        (pathlib.Path(ART_DIR) / 'applied_patch.txt').write_text('none\n')
        return
    print("[eval-docker] Applying diff file: " + diff_path)
    # Try clean apply, then 3-way
    r = subprocess.run(['git', 'apply', diff_path], cwd=repo_dir, capture_output=True, text=True)
    if r.returncode == 0:
        (pathlib.Path(ART_DIR) / 'applied_patch.txt').write_text("applied: " + diff_path + "\nmode: clean\n")
        print('[eval-docker] Patch applied (clean)')
        return
    r3 = subprocess.run(['git', 'apply', '--3way', diff_path], cwd=repo_dir, capture_output=True, text=True)
    if r3.returncode == 0:
        (pathlib.Path(ART_DIR) / 'applied_patch.txt').write_text("applied: " + diff_path + "\nmode: 3way\n")
        print('[eval-docker] Patch applied (3-way)')
    else:
        (pathlib.Path(ART_DIR) / 'applied_patch.txt').write_text("failed: " + diff_path + "\nclean_err: " + r.stderr + "\nthree_way_err: " + r3.stderr + "\n")
        print('[eval-docker] Patch apply failed; proceeding with baseline repo')

def write_tests(meta):
    tests = meta.get('evaluation', {}).get('test_scripts', [])
    created = []
    for t in tests:
        path = pathlib.Path(REPO_DIR) / t['path']
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(t['content'])
        created.append(str(path))
    return created

def run_pytest(paths):
    if not paths:
        return {}, {}
    results = {}
    combined_stdout = []
    combined_stderr = []
    start_all = time.time()
    for p in paths:
        start = time.time()
        proc = subprocess.run(['python3', '-m', 'pytest', '-q', p], cwd=REPO_DIR, capture_output=True, text=True)
        duration = time.time() - start
        rel = p.replace(REPO_DIR + '/', '')
        results[rel] = {
            'success': proc.returncode == 0,
            'duration_sec': duration,
            'output': proc.stdout + '\n' + proc.stderr
        }
        combined_stdout.append(f"===== {rel} =====\n" + proc.stdout)
        if proc.stderr.strip():
            combined_stderr.append(f"===== {rel} =====\n" + proc.stderr)
    total_duration = time.time() - start_all
    out_path = pathlib.Path(ART_DIR) / 'pytest.txt'
    out_path.write_text("\n".join(combined_stdout) + ("\n" + "\n".join(combined_stderr) if combined_stderr else ""))
    return results, {
        'exit_code': 0 if all(v['success'] for v in results.values()) else 1,
        'duration_sec': total_duration
    }

def score_rubrics(meta, test_results):
    rubrics = meta.get('evaluation', {}).get('rubrics', [])
    by_rubric = {}
    for t in meta.get('evaluation', {}).get('test_scripts', []):
        rid = t.get('rubric_id')
        if not rid: continue
        by_rubric.setdefault(rid, []).append(t['path'])

    rubric_scores = {}
    earned = 0.0
    total_w = 0.0
    for r in rubrics:
        rid = r['id']
        w = r['weight']
        total_w += w
        tests = by_rubric.get(rid, [])
        if tests:
            passed = sum(1 for p in tests if test_results.get(p, {}).get('success'))
            score = passed / len(tests)
        else:
            score = None
        rubric_scores[rid] = {
            'criterion': r['criterion'],
            'weight': w,
            'score': score,
            'test_count': len(tests),
            'tests_passed': sum(1 for p in tests if test_results.get(p, {}).get('success'))
        }
        if score is not None:
            earned += score * w

    total_score = (earned / total_w) if total_w > 0 else 0.0
    return {
        'rubrics': rubric_scores,
        'total_score': total_score
    }

def main():
    meta = load_meta()
    os.makedirs(ART_DIR, exist_ok=True)
    # Ensure git identity locally
    subprocess.run(['git', 'config', 'user.email', 'codex@local'], cwd=REPO_DIR)
    subprocess.run(['git', 'config', 'user.name', 'Codex'], cwd=REPO_DIR)
    apply_diff(REPO_DIR)
    test_paths = write_tests(meta)
    test_results, test_meta = run_pytest(test_paths)
    evaluation = score_rubrics(meta, test_results)
    payload = {
        'evaluation': evaluation,
        'test_results': test_results,
        'test_meta': test_meta
    }
    (pathlib.Path(ART_DIR) / 'tb_evaluation_results.json').write_text(json.dumps(payload, indent=2))

if __name__ == '__main__':
    main()
PY

docker cp "$TMP_EVAL_PY" "$CONTAINER_NAME:/app/eval_runner.py"
rm -f "$TMP_EVAL_PY"

# Run the evaluation inside the container
echo "[eval-docker] Running container tests..."
docker exec "$CONTAINER_NAME" bash -lc "python3 /app/eval_runner.py && echo '[eval-docker] Container tests complete'"

# Copy results back to run dir
mkdir -p "$RUN_DIR/artifacts"
docker cp "$CONTAINER_NAME:/app/artifacts/tb_evaluation_results.json" "$RUN_DIR/artifacts/" 2>/dev/null || true
docker cp "$CONTAINER_NAME:/app/artifacts/pytest.txt" "$RUN_DIR/artifacts/" 2>/dev/null || true

# Stop container
docker stop "$CONTAINER_NAME" >/dev/null 2>&1 || true

# Now run host-side evaluation to generate report and LLM rubric score
export PYTHONPATH="$REPO_ROOT/src:${PYTHONPATH:-}"
uv run python -m one_shot_bench.evaluate_run "$RUN_DIR" "$TASK_DIR" | cat

exit 0


