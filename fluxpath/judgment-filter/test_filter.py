# -*- coding: utf-8 -*-
"""Tests for the FluxPath judgment filter against the local mock judge. Standard library plus the
packages the filter and the mock already need (pydantic, fastapi, uvicorn).

  python test_filter.py
"""
import asyncio
import glob
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time

import uvicorn

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import mock_judge  # noqa: E402

FILTER_PATH = sorted(glob.glob(os.path.join(HERE, "fluxpath_judgment_filter_v*.py")))[-1]
spec = importlib.util.spec_from_file_location("fluxpath_filter", FILTER_PATH)
flt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flt)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
server = uvicorn.Server(uvicorn.Config(mock_judge.app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
for _ in range(100):
    if server.started:
        break
    time.sleep(0.05)
URL = "http://127.0.0.1:%d" % PORT


class Browser:
    """What the filter sees of the browser: the event emitter and the event call."""

    def __init__(self, confirm=True, text="more detail"):
        self.confirm, self.text = confirm, text
        self.calls, self.status = [], []

    async def call(self, event):
        self.calls.append(event)
        if event["type"] == "confirmation":
            return self.confirm
        if event["type"] == "input":
            return self.text
        return 1

    async def emit(self, event):
        self.status.append(event["data"]["description"])

    def of(self, kind):
        return [c for c in self.calls if c["type"] == kind]


def new_filter(**valves):
    f = flt.Filter()
    f.valves = f.Valves(judge_url=URL, **valves)
    return f


def body_of(text, history=None):
    return {"model": "m", "messages": (history or []) + [{"role": "user", "content": text}]}


async def run(f, text, browser=None, user=None, metadata=None, history=None):
    browser = browser or Browser()
    b = body_of(text, history)
    meta = {"chat_id": "temporary:s1", "message_id": "msg-%d" % len(mock_judge.CALLS)} if metadata is None else metadata
    out = await f.inlet(b, __user__=user or {"id": "u1"}, __event_call__=browser.call,
                        __event_emitter__=browser.emit, __metadata__=meta)
    return out, browser, meta


async def blocked(coro):
    try:
        await coro
    except Exception as e:  # noqa: BLE001
        return str(e)
    return None


RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS  " if cond else "FAIL  ") + name + (("   " + str(detail)) if detail and not cond else ""))


async def main():
    # 1 M1 passes untouched, shows a status line, logs in the browser
    f = new_filter()
    out, br, _ = await run(f, "hello [M1] one")
    check("M1 passes unchanged", out["messages"][-1]["content"] == "hello [M1] one")
    check("M1 shows status", any("Judgment M1" in s for s in br.status), br.status)
    check("M1 writes one local log entry", len(br.of("execute")) == 1)
    check("M1 opens no dialog", not br.of("confirmation") and not br.of("input"))
    sent = mock_judge.CALLS[-1]["body"]
    check("payload has caller, state, action", sent["caller"] == "fluxpath" and sent["action"] == flt.ACTION
          and sent["state"] == "User (this message): hello [M1] one", sent)

    # 2 M2 passes and the outlet appends the note
    out, br, meta = await run(f, "do it [M2] two")
    reply = await f.outlet({"id": meta["message_id"], "messages": out["messages"] + [{"id": meta["message_id"], "role": "assistant", "content": "done"}]})
    check("M2 passes", out["messages"][-1]["content"] == "do it [M2] two")
    check("M2 note appended to the response", reply["messages"][-1]["content"].startswith("done\n\n*Judgment M2: act with a full audit trail."), reply["messages"][-1]["content"])
    again = await f.outlet({"id": meta["message_id"], "messages": [{"role": "assistant", "content": "done"}]})
    check("M2 note is appended once", again["messages"][-1]["content"] == "done")
    plain = await f.outlet({"id": "other", "messages": [{"role": "assistant", "content": "x"}]})
    check("outlet leaves other responses alone", plain["messages"][-1]["content"] == "x")
    out, br, meta = await run(f, "do it [M2] structured")
    structured = {"id": meta["message_id"], "role": "assistant", "content": "done",
                  "output": [{"type": "reasoning", "summary": []},
                             {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "done"}]}]}
    reply = await f.outlet({"id": meta["message_id"], "messages": [{"role": "user", "content": "x"}, structured]})
    text = reply["messages"][-1]["output"][-1]["content"][-1]["text"]
    check("M2 note appended to the structured output the chat renders",
          text.startswith("done\n\n*Judgment M2: act with a full audit trail.")
          and reply["messages"][-1]["content"] == text and len(reply["messages"][-1]["output"]) == 2, reply["messages"][-1])

    # 3 M3 and M4 ask for confirmation
    for mode in ("M3", "M4"):
        out, br, _ = await run(f, "send [%s] yes" % mode, Browser(confirm=True))
        check("%s confirmed is sent" % mode, out is not None and len(br.of("confirmation")) == 1)
        err = await blocked(run(f, "send [%s] no" % mode, Browser(confirm=False)))
        check("%s declined is not sent" % mode, err and "Not sent" in err and mode in err, err)

    # 4 M5 asks for more detail and sends it along
    out, br, _ = await run(f, "book it [M5] a", Browser(text="for two people on Friday"))
    check("M5 detail appended to the user's message", out["messages"][-1]["content"] == "book it [M5] a\n\nfor two people on Friday", out["messages"][-1])
    check("M5 used an input dialog", len(br.of("input")) == 1)
    err = await blocked(run(f, "book it [M5] b", Browser(text=False)))
    check("M5 cancelled is not sent", err and "Not sent" in err and "M5" in err, err)
    err = await blocked(run(f, "book it [M5] c", Browser(text="   ")))
    check("M5 empty answer is not sent", err and "Not sent" in err, err)
    parts = {"model": "m", "messages": [{"role": "user", "content": [{"type": "text", "text": "what is this [M5] d"}, {"type": "image_url", "image_url": {"url": "data:,"}}]}]}
    br = Browser(text="a receipt")
    out = await f.inlet(parts, __user__={"id": "u1"}, __event_call__=br.call, __event_emitter__=br.emit, __metadata__={"chat_id": "c", "message_id": "m"})
    check("M5 detail appended to multimodal content", out["messages"][-1]["content"][-1] == {"type": "text", "text": "a receipt"}, out["messages"][-1]["content"])

    # 5 M6, M7, M8 block and say why
    for mode in ("M6", "M7", "M8"):
        br = Browser()
        err = await blocked(run(f, "do [%s]" % mode, br))
        check("%s blocked with reason" % mode, err and err.startswith("Blocked. Judgment %s" % mode) and "MOCK marker" in err, err)
        check("%s blocked without a dialog, logged locally" % mode, not br.of("confirmation") and not br.of("input") and len(br.of("execute")) == 1)
        check("%s status ends on the judgment, not on checking" % mode, br.status[-1].startswith("Judgment %s:" % mode), br.status)

    # 6 endpoint failure: pass by default, block when told to
    out, br, _ = await run(f, "hi [503] a")
    check("unavailable passes by default", out is not None and any("unavailable (judge_timeout)" in s for s in br.status), br.status)
    check("unavailable is logged locally", len(br.of("execute")) == 1 and "sent_unchecked" in br.of("execute")[0]["data"]["code"])
    err = await blocked(run(new_filter(on_unavailable="block"), "hi [503] b"))
    check("unavailable blocks when configured", err and "unavailable" in err, err)
    dead = flt.Filter()
    dead.valves = dead.Valves(judge_url="http://127.0.0.1:9", timeout_s=2)
    out, br, _ = await run(dead, "hi nobody home")
    check("unreachable endpoint passes by default", out is not None and any("unavailable" in s for s in br.status), br.status)

    # 7 switched off by the user, not configured, background task
    n = len(mock_judge.CALLS)
    out, br, _ = await run(f, "x [M8] off", user={"id": "u1", "valves": f.UserValves(enabled=False)})
    check("user switch off skips the judge", out is not None and len(mock_judge.CALLS) == n and not br.calls)
    out, br, _ = await run(flt.Filter(), "x [M8] unconfigured")
    check("no endpoint configured passes", out is not None and len(mock_judge.CALLS) == n)
    out, br, _ = await run(f, "x [M8] task", metadata={"chat_id": "c", "message_id": "m", "task": "title_generation"})
    check("background tasks are not judged", out is not None and len(mock_judge.CALLS) == n)
    out = await f.inlet({"messages": [{"role": "system", "content": "s"}]}, __user__={"id": "u1"})
    check("no user message passes", out is not None and len(mock_judge.CALLS) == n)

    # 8 no browser session (API client): pass modes pass, anything needing the user is not sent
    out = await f.inlet(body_of("api [M1] call"), __user__={"id": "u2"}, __metadata__={})
    check("no browser: M1 passes", out is not None)
    err = await blocked(f.inlet(body_of("api [M3] call"), __user__={"id": "u2"}, __metadata__={}))
    check("no browser: M3 is not sent", err and "not confirmed" in err, err)

    # 9 several models answering the same message share one judgment and one dialog
    n = len(mock_judge.CALLS)
    br = Browser(confirm=True)
    b1, b2 = body_of("same [M3] [slow] msg"), body_of("same [M3] [slow] msg")
    kw = dict(__user__={"id": "u3"}, __event_call__=br.call, __event_emitter__=br.emit)
    r1, r2 = await asyncio.gather(f.inlet(b1, __metadata__={"chat_id": "c", "message_id": "a"}, **kw),
                                  f.inlet(b2, __metadata__={"chat_id": "c", "message_id": "b"}, **kw))
    check("concurrent identical messages: one judge call", len(mock_judge.CALLS) == n + 1, len(mock_judge.CALLS) - n)
    check("concurrent identical messages: one dialog", len(br.of("confirmation")) == 1)
    check("concurrent identical messages: each response gets the status line",
          sum(s.startswith("Judgment M3:") for s in br.status) == 2, br.status)

    class Broken(Browser):
        async def call(self, event):
            if event["type"] != "execute":
                raise RuntimeError("socket gone")
            return await super().call(event)

    err = await blocked(run(f, "broken browser [M3]", Broken()))
    check("a failing browser call counts as not confirmed", err and "not confirmed" in err, err)
    n = len(mock_judge.CALLS)
    await f.inlet(body_of("same [M3] [slow] msg"), __user__={"id": "u4"}, __event_call__=br.call, __metadata__={"chat_id": "c", "message_id": "z"})
    check("another user is judged separately", len(mock_judge.CALLS) == n + 1)

    # 10 context, token, rubric
    history = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "first " + "a" * 400},
               {"role": "assistant", "content": "answer one"}, {"role": "user", "content": "second"},
               {"role": "assistant", "content": "answer two"}]
    mock_judge.REQUIRE_TOKEN["value"] = "hf_test_token"
    f2 = new_filter(judge_token="hf_test_token", rubric="v5", context_turns=3)
    out, br, _ = await run(f2, "third [M1]", history=history)
    call = mock_judge.CALLS[-1]
    lines = call["body"]["state"].split("\n")
    check("token sent as bearer", call["authorization"] == "Bearer hf_test_token")
    check("rubric sent when set", call["body"].get("rubric") == "v5")
    check("state keeps recent turns and trims them", lines == ["Assistant: answer one", "User: second", "Assistant: answer two", "User (this message): third [M1]"], lines)
    out, br, _ = await run(new_filter(), "no token [M1]")
    check("wrong token counts as unavailable, passes by default", out is not None and any("unavailable (unauthorized)" in s for s in br.status), br.status)
    mock_judge.REQUIRE_TOKEN["value"] = ""

    # 11 the local log code runs in a browser-like wrapper and stores the record as returned
    f3 = new_filter(local_log_max=2)
    codes = []
    for i in range(3):
        _, br, _ = await run(f3, "log [M2] %d" % i)
        codes.append(br.of("execute")[0]["data"]["code"])
    check("local log entry carries the judge record", '"judge": {"id": "j_mock_' in codes[0] and '"outcome": "sent"' in codes[0], codes[0][:300])
    js = os.path.join(HERE, "_log_codes.json")
    with open(js, "w", encoding="utf-8") as fh:
        json.dump(codes, fh)
    node = subprocess.run(["node", os.path.join(HERE, "run_log_code.js"), js], capture_output=True, text=True)
    os.remove(js)
    check("local log code runs in the browser wrapper and keeps the newest 2", node.returncode == 0 and node.stdout.strip() == "ok 2 log [M2]", node.stdout + node.stderr)

    failed = [n for n, ok in RESULTS if not ok]
    print("\n%d/%d passed" % (len(RESULTS) - len(failed), len(RESULTS)))
    server.should_exit = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
