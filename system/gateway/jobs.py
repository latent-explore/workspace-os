"""Long jobs (training, experiments) run as their own systemd units under the wos user manager,
so they survive the agent exiting and a gateway restart. Needs `loginctl enable-linger wos`."""
import subprocess
import time
from pathlib import Path


class JobError(RuntimeError):
    pass


def job_dir(state_dir: Path, task_id: int) -> Path:
    return state_dir / "jobs" / str(task_id)


def start(state_dir: Path, task_id: int, argv: list[str], cwd: Path, memory: str = "16G", cpu: str = "200%") -> str:
    if not argv:
        raise JobError("no command given")
    unit = f"wos-job-{task_id}-{int(time.time())}"
    folder = job_dir(state_dir, task_id) / unit
    folder.mkdir(parents=True, exist_ok=True)
    log, exit_file = folder / "log.txt", folder / "exit"
    # The wrapper records the exit code in a file, because systemd forgets a finished unit.
    wrapper = f'"$@"; echo $? > "{exit_file}"'
    cmd = [
        "systemd-run", "--user", "--quiet", "--collect", "--unit", unit,
        "-p", f"MemoryMax={memory}", "-p", f"CPUQuota={cpu}",
        "-p", f"StandardOutput=append:{log}", "-p", f"StandardError=append:{log}",
        "--working-directory", str(cwd), "--", "bash", "-c", wrapper, "wos-job", *argv,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise JobError(f"could not start job: {proc.stderr.strip()[:300]}")
    return unit


def status(state_dir: Path, task_id: int, unit: str) -> dict:
    """running | finished (with exit code) | lost (unit gone, no exit code)."""
    folder = job_dir(state_dir, task_id) / unit
    exit_file = folder / "exit"
    if exit_file.exists():
        code = exit_file.read_text().strip()
        return {"unit": unit, "state": "finished", "exit_code": int(code) if code.isdigit() else None,
                "log": str(folder / "log.txt")}
    proc = subprocess.run(["systemctl", "--user", "is-active", unit], capture_output=True, text=True)
    state = "running" if proc.stdout.strip() in ("active", "activating") else "lost"
    return {"unit": unit, "state": state, "exit_code": None, "log": str(folder / "log.txt")}


def stop(unit: str) -> None:
    subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True)


def all_done(state_dir: Path, task_id: int, units: list[str]) -> bool:
    return bool(units) and all(status(state_dir, task_id, u)["state"] != "running" for u in units)
