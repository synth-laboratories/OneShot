#!/usr/bin/env python3
"""
Eval rollouts orchestrator.

Usage:
  uv run python scripts/eval_rollouts.py run <config_toml>
  uv run python scripts/eval_rollouts.py summarize <config_name> [--latest]

Config TOML schema (example):

name = "hello_world"
parallel = 2

[[tasks]]
prepared_dir = "/abs/path/to/data/tasks/prepared/add-hello-world-to-readme"

[[tasks]]
prepared_dir = "/abs/path/to/data/tasks/prepared/create-codebasemd-from-specs-and-src-patterns"
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import tomllib  # Python 3.11+


REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_SCRIPT = REPO_ROOT / "scripts" / "run_codex_box.sh"
EVAL_DOCKER_SCRIPT = REPO_ROOT / "scripts" / "eval_in_docker.sh"


def run_cmd(cmd: List[str], env: Dict[str, str] | None = None) -> int:
    from subprocess import run
    e = os.environ.copy()
    if env:
        e.update(env)
    r = run(cmd, env=e)
    return r.returncode


def now_ts() -> str:
    return time.strftime("%Y%m%d__%H-%M-%S", time.gmtime())


@dataclass
class TaskSpec:
    prepared_dir: str
    model: str = "gpt-5-mini"
    overrides: str | None = None
    apply_overrides: bool = True
    rollouts: int = 1


def load_config(path: Path) -> Dict[str, Any]:
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    if not data:
        raise SystemExit(f"Empty or missing config: {path}")
    if "name" not in data:
        data["name"] = path.stem
    if "parallel" not in data:
        data["parallel"] = 1
    tasks: List[TaskSpec] = []
    raw_tasks = data.get("tasks", [])
    for t in raw_tasks:
        pd = t.get("prepared_dir")
        if not pd:
            raise SystemExit("Each [[tasks]] entry must include prepared_dir")
        tasks.append(TaskSpec(
            prepared_dir=pd,
            model=t.get("model", "gpt-5-mini"),
            overrides=t.get("overrides"),
            apply_overrides=bool(t.get("apply_overrides", True)),
            rollouts=int(t.get("rollouts", 1)),
        ))
    data["_tasks_parsed"] = tasks
    return data


def run_single_task(task: TaskSpec, rollout_dir: Path, idx: int) -> Dict[str, Any]:
    task_dir = Path(task.prepared_dir)
    slug = task_dir.name
    base = now_ts()
    # Use hyphen for index to keep docker volume mounts valid (avoid ':')
    run_id = f"{base}-{idx}"
    run_dir = REPO_ROOT / "data" / "runs" / run_id

    # Launch Codex-in-the-Box run, forcing RUN_ID so we can reference run_dir
    env = {
        "RUN_ID": run_id,
        "OPENAI_MODEL": task.model,
        "ROLLOUT_APPLY_OVERRIDES": "1" if task.apply_overrides else "0",
    }
    if task.overrides:
        env["ROLLOUT_OVERRIDES_FILE"] = task.overrides
    rc = run_cmd(["bash", str(RUN_SCRIPT), str(task_dir)], env=env)
    result: Dict[str, Any] = {
        "task_dir": str(task_dir),
        "run_dir": str(run_dir),
        "run_id": run_id,
        "launch_rc": rc,
    }
    if rc != 0:
        result["status"] = "launch_failed"
        return result
    result["status"] = "launched"
    return result


def cmd_run(config_path: Path) -> None:
    cfg = load_config(config_path)
    cfg_name = cfg["name"]
    tasks: List[TaskSpec] = cfg["_tasks_parsed"]
    parallel = int(cfg.get("parallel", 1))

    # Prepare rollout directory
    stamp = now_ts()
    rollout_dir = REPO_ROOT / "data" / "rollouts" / cfg_name / stamp
    rollout_dir.mkdir(parents=True, exist_ok=True)

    print(f"[rollouts] config={config_path} name={cfg_name} tasks={len(tasks)} parallel={parallel}")
    print(f"[rollouts] output={rollout_dir}")

    results: List[Dict[str, Any]] = []
    launch_specs: List[tuple[int, TaskSpec]] = []
    for i, t in enumerate(tasks):
        for r in range(t.rollouts):
            launch_specs.append((len(launch_specs), t))
    with ThreadPoolExecutor(max_workers=parallel) as ex:
        fut_to_task = {ex.submit(run_single_task, t, rollout_dir, i): (i, t) for i, t in launch_specs}
        for fut in as_completed(fut_to_task):
            res = fut.result()
            results.append(res)
            print(f"[rollouts] finished: run_id={res['run_id']} status={res['status']}")

    # Save manifest and runs list
    (rollout_dir / "manifest.json").write_text(json.dumps({
        "config": str(config_path),
        "name": cfg_name,
        "created_at": stamp,
        "results": results,
    }, indent=2))

    with open(rollout_dir / "runs.txt", "w") as f:
        for r in results:
            f.write(r["run_dir"] + "\n")

    # Also save a simple JSON under temp/ for quick --latest eval usage
    temp_dir = REPO_ROOT / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_json = temp_dir / f"rollout_{cfg_name}__{stamp}.json"
    temp_json.write_text(json.dumps({
        "created_at": stamp,
        "rollout_dir": str(rollout_dir),
        "runs": [r["run_dir"] for r in results],
        "tasks": [r["task_dir"] for r in results],
    }, indent=2))
    print(f"[rollouts] temp index: {temp_json}")

    # Print quick table
    print("\n[rollouts] Summary:")
    print("| task | run_id | status |")
    print("|---|---|---|")
    for r in results:
        print(f"| {Path(r['task_dir']).name} | {r['run_id']} | {r['status']} |")


def eval_single_run(run_dir: Path, task_dir: Path) -> Dict[str, Any]:
    rc = run_cmd(["bash", str(EVAL_DOCKER_SCRIPT), str(run_dir), str(task_dir)])
    return {
        "run_dir": str(run_dir),
        "task_dir": str(task_dir),
        "eval_rc": rc,
        "status": "ok" if rc == 0 else "eval_failed",
    }


def find_latest_rollout(cfg_name: str) -> Path | None:
    base = REPO_ROOT / "data" / "rollouts" / cfg_name
    if not base.exists():
        return None
    dirs = sorted([d for d in base.iterdir() if d.is_dir()], key=lambda p: p.name)
    return dirs[-1] if dirs else None


def summarize_rollout(rollout_dir: Path) -> None:
    # Load runs list and evaluation results
    runs_file = rollout_dir / "runs.txt"
    if not runs_file.exists():
        print(f"No runs.txt in {rollout_dir}", file=sys.stderr)
        sys.exit(1)
    run_dirs = [Path(l.strip()) for l in runs_file.read_text().splitlines() if l.strip()]

    rows: List[Dict[str, Any]] = []
    for rd in run_dirs:
        eval_json = rd / "evaluation_results.json"
        task_id = rd.name
        unit_score = None
        lm_score = None
        tokens = None
        tools = None
        if eval_json.exists():
            try:
                data = json.loads(eval_json.read_text())
                unit_score = data.get("evaluation", {}).get("total_score")
                lm = data.get("lm_evaluation")
                if lm and isinstance(lm.get("weighted_score"), (int, float)):
                    lm_score = lm["weighted_score"]
                am = data.get("agent_metrics") or {}
                tokens = am.get("tokens_total")
                tools = am.get("tool_calls_total")
                task_id = data.get("task_id", task_id)
            except Exception:
                pass
        rows.append({
            "task": task_id,
            "run": rd.name,
            "unit": unit_score,
            "lm": lm_score,
            "tokens": tokens,
            "tools": tools,
        })

    # Print per-run table
    print(f"[rollouts] Summary for {rollout_dir}")
    print("| task | run | unit | lm | tokens | tools |")
    print("|---|---|---:|---:|---:|---:|")
    unit_vals: List[float] = []
    lm_vals: List[float] = []
    for r in rows:
        u = f"{(r['unit']*100):.0f}%" if isinstance(r['unit'], (int, float)) else "N/A"
        l = f"{(r['lm']*100):.0f}%" if isinstance(r['lm'], (int, float)) else "N/A"
        if isinstance(r['unit'], (int, float)):
            unit_vals.append(float(r['unit']))
        if isinstance(r['lm'], (int, float)):
            lm_vals.append(float(r['lm']))
        print(f"| {r['task']} | {r['run']} | {u} | {l} | {r['tokens'] or 0} | {r['tools'] or 0} |")

    # Aggregates (overall)
    unit_avg = (sum(unit_vals) / len(unit_vals)) if unit_vals else None
    lm_avg = (sum(lm_vals) / len(lm_vals)) if lm_vals else None
    print("\n[rollouts] Aggregates:")
    print(f"- Unit tests average: {unit_avg*100:.0f}%" if unit_avg is not None else "- Unit tests average: N/A")
    print(f"- LM rubric average: {lm_avg*100:.0f}%" if lm_avg is not None else "- LM rubric average: N/A")

    # Per-task aggregates (mean and max across rollouts)
    # Group rows by task
    task_groups: Dict[str, Dict[str, List[float]]] = {}
    for r in rows:
        task = r["task"]
        g = task_groups.setdefault(task, {"unit": [], "lm": []})
        if isinstance(r["unit"], (int, float)):
            g["unit"].append(float(r["unit"]))
        if isinstance(r["lm"], (int, float)):
            g["lm"].append(float(r["lm"]))

    if task_groups:
        print("\n[rollouts] Per-task aggregates (mean/max across rollouts):")
        print("| task | rollouts | unit_mean | unit_max | lm_mean | lm_max |")
        print("|---|---:|---:|---:|---:|---:|")
        for task, g in sorted(task_groups.items(), key=lambda kv: kv[0]):
            n = sum(1 for _ in (t for t in rows if t["task"] == task))
            u_vals = g["unit"]
            l_vals = g["lm"]
            u_mean = f"{(sum(u_vals)/len(u_vals)*100):.0f}%" if u_vals else "N/A"
            u_max = f"{(max(u_vals)*100):.0f}%" if u_vals else "N/A"
            l_mean = f"{(sum(l_vals)/len(l_vals)*100):.0f}%" if l_vals else "N/A"
            l_max = f"{(max(l_vals)*100):.0f}%" if l_vals else "N/A"
            print(f"| {task} | {n} | {u_mean} | {u_max} | {l_mean} | {l_max} |")

    # Save markdown
    out_md = rollout_dir / "summary.md"
    with open(out_md, "w") as f:
        f.write(f"# Rollout Summary: {rollout_dir.name}\n\n")
        f.write("| task | run | unit | lm | tokens | tools |\n")
        f.write("|---|---|---:|---:|---:|---:|\n")
        for r in rows:
            u = f"{(r['unit']*100):.0f}%" if isinstance(r['unit'], (int, float)) else "N/A"
            l = f"{(r['lm']*100):.0f}%" if isinstance(r['lm'], (int, float)) else "N/A"
            f.write(f"| {r['task']} | {r['run']} | {u} | {l} | {r['tokens'] or 0} | {r['tools'] or 0} |\n")
        f.write("\n## Aggregates\n\n")
        f.write((f"- Unit tests average: {unit_avg*100:.0f}%\n") if unit_avg is not None else "- Unit tests average: N/A\n")
        f.write((f"- LM rubric average: {lm_avg*100:.0f}%\n") if lm_avg is not None else "- LM rubric average: N/A\n")
        if task_groups:
            f.write("\n## Per-task aggregates (mean/max across rollouts)\n\n")
            f.write("| task | rollouts | unit_mean | unit_max | lm_mean | lm_max |\n")
            f.write("|---|---:|---:|---:|---:|---:|\n")
            for task, g in sorted(task_groups.items(), key=lambda kv: kv[0]):
                n = sum(1 for _ in (t for t in rows if t["task"] == task))
                u_vals = g["unit"]
                l_vals = g["lm"]
                u_mean = f"{(sum(u_vals)/len(u_vals)*100):.0f}%" if u_vals else "N/A"
                u_max = f"{(max(u_vals)*100):.0f}%" if u_vals else "N/A"
                l_mean = f"{(sum(l_vals)/len(l_vals)*100):.0f}%" if l_vals else "N/A"
                l_max = f"{(max(l_vals)*100):.0f}%" if l_vals else "N/A"
                f.write(f"| {task} | {n} | {u_mean} | {u_max} | {l_mean} | {l_max} |\n")
    print(f"[rollouts] summary.md written: {out_md}")


def cmd_summarize(cfg_name: str, latest: bool) -> None:
    if latest:
        rd = find_latest_rollout(cfg_name)
        if not rd:
            print(f"No rollouts found for {cfg_name}", file=sys.stderr)
            sys.exit(1)
        summarize_rollout(rd)
        return
    else:
        print("Usage: summarize <config_name> --latest", file=sys.stderr)
        sys.exit(1)


def cmd_eval(args: List[str]) -> None:
    # eval <config_name> --latest | eval <rollout_dir>
    if len(args) < 1:
        print("Usage: eval <config_name> --latest | eval <rollout_dir>", file=sys.stderr)
        sys.exit(1)
    if args[1:] and args[1] == "--latest":
        cfg_name = args[0]
        rd = find_latest_rollout(cfg_name)
        if not rd:
            print(f"No rollouts found for {cfg_name}", file=sys.stderr)
            sys.exit(1)
        rollout_dir = rd
    else:
        rollout_dir = Path(args[0])
        if not rollout_dir.exists():
            print(f"Rollout dir does not exist: {rollout_dir}", file=sys.stderr)
            sys.exit(1)

    # Load manifest and eval all runs in parallel
    manifest = json.loads((rollout_dir / "manifest.json").read_text())
    results = manifest.get("results", [])
    pairs = [(Path(r["run_dir"]), Path(r["task_dir"])) for r in results]

    parallel = min(4, len(pairs)) if pairs else 0
    eval_results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as ex:
        futs = {ex.submit(eval_single_run, rd, td): (rd, td) for rd, td in pairs}
        for fut in as_completed(futs):
            res = fut.result()
            eval_results.append(res)
            print(f"[eval] {res['run_dir']} -> {res['status']}")

    (rollout_dir / "eval_manifest.json").write_text(json.dumps({
        "evaluated_at": now_ts(),
        "results": eval_results,
    }, indent=2))
    print(f"[eval] eval_manifest.json written: {rollout_dir/'eval_manifest.json'}")

    # Print final table and aggregates by reusing summarize
    summarize_rollout(rollout_dir)


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "run":
        cfg_path = Path(sys.argv[2])
        cmd_run(cfg_path)
        return
    if cmd == "summarize":
        cfg_name = sys.argv[2]
        latest = "--latest" in sys.argv[3:]
        cmd_summarize(cfg_name, latest)
        return
    if cmd == "eval":
        cmd_eval(sys.argv[2:])
        return
    print(__doc__)
    sys.exit(1)


if __name__ == "__main__":
    main()


