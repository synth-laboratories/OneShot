import os
import json
from pathlib import Path

import modal

APP_NAME = "oneshot-bench-modal"

# Base image with system deps similar to the Dockerfile
image = (
    modal.Image.debian_slim()
    .apt_install(
        "git",
        "curl",
        "build-essential",
        "python3",
        "python3-venv",
        "python3-pip",
        "tmux",
        "vim",
        "less",
        "jq",
        "ca-certificates",
        "util-linux",
        "expect",
    )
    .run_commands(
        # Node.js 20
        "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -",
        "apt-get install -y nodejs",
        # Python deps
        "pip3 install --no-cache-dir pytest",
    )
)

app = modal.App(APP_NAME)


@app.function(
    image=image,
    timeout=60 * 30,
)
def run_task(task_archive: bytes, model: str, openai_api_key: str) -> dict:
    import os
    import json
    import shutil
    import subprocess
    from pathlib import Path

    # Prepare workspace
    os.makedirs("/app", exist_ok=True)
    # Unpack the provided task archive into /tmp/task
    import io, tarfile
    task_root = Path("/tmp/task")
    shutil.rmtree(task_root, ignore_errors=True)
    task_root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(task_archive), mode="r:gz") as tf:
        tf.extractall(task_root)

    # Copy overlay files into /app
    overlay = task_root / "overlay_files"
    if overlay.exists():
        for item in overlay.iterdir():
            dst = Path("/app") / item.name
            if item.is_dir():
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst)
    # Ensure scripts are executable inside Modal image
    subprocess.run(["bash", "-lc", "chmod +x /app/*.sh 2>/dev/null || true"], check=False)
    subprocess.run(["bash", "-lc", "chmod +x /app/codex-synth 2>/dev/null || true"], check=False)

    # Setup codex installation inside container from task's codex-files
    codex_src = task_root / "codex-files"
    codex_dst = Path("/usr/local/lib/node_modules/@openai/codex")
    codex_dst.parent.mkdir(parents=True, exist_ok=True)
    if codex_src.exists():
        shutil.copytree(codex_src, codex_dst, dirs_exist_ok=True)
    # Symlink codex binary
    subprocess.run(
        [
            "bash",
            "-lc",
            "ln -sf /usr/local/lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex && chmod +x /usr/local/bin/codex",
        ],
        check=False,
    )

    # Read tb_meta.json for repo info
    tb_meta_path = task_root / "tb_meta.json"
    repo_url = ""
    branch = "main"
    commit = "HEAD"
    if tb_meta_path.exists():
        meta = json.loads(tb_meta_path.read_text())
        repo = meta.get("repo", {})
        repo_url = repo.get("git_url", repo_url)
        branch = repo.get("branch", branch)
        commit = repo.get("start_commit_sha", commit)

    # Clone repo into /app/repo
    if repo_url:
        subprocess.run(["bash", "-lc", f"cd /app && git clone {repo_url} repo"], check=True)
        subprocess.run(["bash", "-lc", f"cd /app/repo && git checkout {branch}"], check=True)
        if commit and commit != "HEAD":
            subprocess.run(["bash", "-lc", f"cd /app/repo && git reset --hard {commit}"], check=True)
        subprocess.run(["bash", "-lc", "chmod -R 777 /app/repo"], check=False)

    # Write .env
    env_src = task_root / ".env"
    if env_src.exists():
        shutil.copy2(env_src, Path("/app/.env"))
    else:
        Path("/app/.env").write_text("")

    # Ensure artifacts dir
    os.makedirs("/app/artifacts", exist_ok=True)

    # Set env
    os.environ["OPENAI_API_KEY"] = openai_api_key
    os.environ["OPENAI_MODEL"] = model
    os.environ["PYTHONUNBUFFERED"] = "1"
    os.environ["RUN_ON_MODAL"] = "1"

    # Run bootstrap
    code = subprocess.run(["bash", "-lc", "/app/box_bootstrap.sh"]).returncode

    # Collect results
    out = {"exit_code": code}
    diff_path = Path("/app/artifacts/diff.patch")
    eval_path = Path("/app/artifacts/tb_evaluation_results.json")
    if diff_path.exists():
        out["diff"] = diff_path.read_text()
    if eval_path.exists():
        try:
            out["evaluation"] = json.loads(eval_path.read_text())
        except Exception:
            out["evaluation"] = None
    return out


@app.local_entrypoint()
def main(task_dir: str, model: str = os.environ.get("OPENAI_MODEL", "gpt-5-mini")):
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("[error] OPENAI_API_KEY is not set")
        raise SystemExit(1)

    # Pack task_dir into a tar.gz and send as bytes to the Modal function
    import io, tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        root = Path(task_dir)
        for p in root.rglob("*"):
            tf.add(p, arcname=p.relative_to(root))
    archive_bytes = buf.getvalue()

    result = run_task.remote(archive_bytes, model, api_key)

    print("[results] ========================================")
    print("[results] Git diff (modal):")
    diff = result.get("diff", "") or "(empty)"
    print(diff)
    print("[results] ----------------------------------------")
    evaluation = result.get("evaluation") or {}
    total = (evaluation.get("evaluation", {}) or {}).get("total_score", 0.0)
    print(f"[results] Rubric total score: {total:.0%}")
    rubrics = (evaluation.get("evaluation", {}) or {}).get("rubrics", {})
    if isinstance(rubrics, dict):
        for rid, r in rubrics.items():
            print(f"[results]  - {rid}: {r.get('score',0):.0%} (weight={r.get('weight',1)})")
    tests = evaluation.get("test_results", {})
    passed = sum(1 for v in tests.values() if v.get("success"))
    failed = sum(1 for v in tests.values() if not v.get("success"))
    print(f"[results] Unit tests: {passed} passed, {failed} failed")
    print("[results] ========================================")


