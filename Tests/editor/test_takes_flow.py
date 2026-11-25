"""Generate x2 -> takes list, select, star, save, revise (keeps seed), discard."""
import os
import sys

sys.path.insert(0, os.path.join(os.environ.get("KIMODO_PLUGIN_DIR", ""), "Tests", "editor"))
from common import ASSETS, PROMPT, run  # noqa: E402


def body(t):
    s, events, _ = t.wait(t.call("generate", PROMPT, seed="50", count=2))
    seeds = sorted(int(x["seed"]) for x in s["takes"])
    t.check("batch makes two takes with seed, seed+1", seeds == [50, 51], seeds)
    t.check("one ready notification for the batch", events == ["ready"], events)
    t.check("batch message", s["message"].startswith("Preview ready · 2 takes"), s["message"])
    t.check("newest take listed first", [x["number"] for x in s["takes"]] == [2, 1], [x["number"] for x in s["takes"]])

    older = s["takes"][-1]
    s = t.call("select", older["id"])
    t.check("select older take", s["selected"] == older["id"] and s["seed"] == "50", (s["selected"], s["seed"]))
    s = t.call("star", older["id"])
    t.check("star", next(x for x in s["takes"] if x["id"] == older["id"])["starred"])

    name = s["suggested_name"]
    s = t.call("save", name)
    path = f"{t.folder}/{name}"
    t.check("save creates the AnimSequence", ASSETS.does_asset_exist(path), path)
    t.check("take marked saved", next(x for x in s["takes"] if x["id"] == older["id"])["saved"] == path)
    t.check("next suggested name differs", s["suggested_name"] != name, s["suggested_name"])

    s, events, _ = t.wait(t.call("revise", "Wave faster", seed=""))
    newest = s["takes"][0]
    t.check("revise adds a take with the same seed", newest["seed"] == "50" and newest["number"] == 3,
            (newest["number"], newest["seed"]))
    t.check("revise prompt joins base and change", "→ Wave faster" in newest["prompt"], newest["prompt"])

    s = t.call("discard", newest["id"])
    t.check("discard removes the take", newest["id"] not in [x["id"] for x in s["takes"]] and len(s["takes"]) == 2)


run("takes_flow", body)
