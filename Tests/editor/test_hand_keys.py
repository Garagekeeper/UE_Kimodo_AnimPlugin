"""Hand keys through dispatch: base take -> keyed action -> new take with the same seed and the key label."""
import os
import sys

sys.path.insert(0, os.path.join(os.environ.get("KIMODO_PLUGIN_DIR", ""), "Tests", "editor"))
from common import run  # noqa: E402

PROMPT = "A person waves hello with big slow side to side arm movements, right arm raised high"
KEYS = [{"time": round(0.9 + 0.6 * i, 1), "hand": "right", "offset": [30 if i % 2 == 0 else -30, 5, 0]} for i in range(5)]


def body(t):
    s, _, _ = t.wait(t.call("generate", PROMPT, seed="4", duration="4.0"))
    base = s["takes"][0]
    t.check("base take", base["seed"] == "4", base)

    s, events, _ = t.wait(t.call("keyed", hand_keys=KEYS))
    keyed = s["takes"][0]
    t.check("keyed take added", len(s["takes"]) == 2 and keyed["number"] == 2, [x["number"] for x in s["takes"]])
    t.check("keyed keeps seed", keyed["seed"] == "4", keyed["seed"])
    t.check("keyed label", "5 hand keys" in keyed["prompt"], keyed["prompt"])
    t.check("keyed ready", s["message"].startswith("Preview ready") and events == ["ready"], (s["message"], events))
    take = t.panel.find_take(keyed["id"])
    t.check("keys stored on the take", len(take["hand_keys"]) == 5 and take["seconds"] == 4.0, take["hand_keys"])

    s = t.call("keyed", hand_keys=[{"time": 9.0, "hand": "right", "offset": [0, 0, 0]}])
    t.check("key outside the take is rejected", "inside the take" in s["message"] and not s["busy"], s["message"])


run("hand_keys", body)
