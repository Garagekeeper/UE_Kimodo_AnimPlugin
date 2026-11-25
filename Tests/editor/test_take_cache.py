"""Takes are cached as /KimodoTakes/Preview assets, restored after an editor restart, and deleted with their take."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.environ.get("KIMODO_PLUGIN_DIR", ""), "Tests", "editor"))
from common import ASSETS, PROMPT, TAKE_ROOT, run  # noqa: E402


def body(t):
    s, _, _ = t.wait(t.call("generate", PROMPT, seed="70", count=2))
    takes = {x["number"]: x for x in s["takes"]}
    paths = [x.split(".")[0] for x in [t.panel.find_take(x["id"])["cached"] for x in s["takes"]]]
    t.check("takes cached under Preview", all(p.startswith(f"{TAKE_ROOT}/Preview/T0") for p in paths), paths)
    t.check("cache assets exist", all(ASSETS.does_asset_exist(p) for p in paths))

    # 에디터를 다시 연 상황: 새 panel.py 인스턴스가 같은 작업 폴더의 목록을 찾는다.
    s = t.reload()
    t.check("restore offered after restart", s["restorable"] and s["restore_count"] == 2, s["message"])
    s = t.call("generate", PROMPT, seed="72")  # 복원 전에 생성해도 번호가 이어져야 한다
    s, _, _ = t.wait(s)
    started = time.perf_counter()
    s = t.call("restore")
    ms = 1000 * (time.perf_counter() - started)
    numbers = [x["number"] for x in s["takes"]]
    t.check("restore keeps numbers unique and ordered", numbers == [3, 2, 1], numbers)
    t.check("restored takes are loaded", all(x["loaded"] for x in s["takes"]))
    t.check("restore is fast (< 2 s)", ms < 2000, f"{ms:.0f} ms")
    t.check("restored seeds kept", takes[1]["seed"] == next(x for x in s["takes"] if x["number"] == 1)["seed"])

    for take in list(s["takes"]):
        t.call("discard", take["id"])
    t.call("poll")
    t.check("discard deletes cache assets", not any(ASSETS.does_asset_exist(p) for p in paths))


run("take_cache", body)
