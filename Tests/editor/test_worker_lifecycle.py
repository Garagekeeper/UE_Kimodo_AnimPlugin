"""Resident Kimodo worker: reused between takes, unloaded when idle, kept on Cancel, unloaded when the panel closes."""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.environ.get("KIMODO_PLUGIN_DIR", ""), "Tests", "editor"))
from common import PROMPT, run  # noqa: E402


def alive(pid):
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


def body(t):
    s, _, _ = t.wait(t.call("generate", PROMPT, seed="60"))
    first = t.panel.worker
    t.check("worker loaded after first take", first is not None and first.alive())

    started = time.monotonic()
    s, _, _ = t.wait(t.call("generate", PROMPT, seed="61"))
    seconds = time.monotonic() - started
    t.check("second take reuses the worker", t.panel.worker is first)
    t.check("resident take is fast (< 12 s)", seconds < 12, f"{seconds:.1f}s")

    pid = first.pid
    first.last_used -= 601  # WORKER_IDLE_SECONDS(600)이 지난 것처럼
    t.call("poll")
    time.sleep(1)
    t.check("idle worker unloaded", t.panel.worker is None and not alive(pid))

    s, _, stopped = t.wait(t.call("generate", PROMPT, seed="62"), stop=lambda st: st["stage"] == "Generating motion")
    second = t.panel.worker
    t.check("new worker after idle unload", stopped and second is not None and second is not first)
    s = t.call("cancel")
    t.check("cancel keeps the worker", t.panel.worker is second and alive(second.pid), s["message"])
    s, _, _ = t.wait(t.call("generate", PROMPT, seed="63"))
    t.check("next take after cancel uses the same worker", t.panel.worker is second and s["message"].startswith("Preview ready"),
            s["message"])
    t.check("cancelled take not added", "62" not in [x["seed"] for x in s["takes"]])

    pid = second.pid
    t.mod["close_panel"]()
    time.sleep(1)
    t.check("closing the panel unloads the worker", t.panel.worker is None and not alive(pid))


run("worker_lifecycle", body)
