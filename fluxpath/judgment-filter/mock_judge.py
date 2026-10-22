# -*- coding: utf-8 -*-
"""Local stand-in for the iLang judgment endpoint, for testing the FluxPath filter without touching
the production Space. Same route and record shape as space/app.py in ilang-ai/ilang-judge.

Nothing is judged here. The mode is read from a marker in the state text: [M1] to [M8]; [503] makes
the endpoint fail the way the real one does when Jev times out. Without a marker the mode is M1.

  python -m uvicorn mock_judge:app --port 7861
"""
import re
import secrets
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="mock iLang judge", docs_url=None, redoc_url=None)
CLASS5 = {"M1": "act", "M2": "act", "M3": "confirm", "M4": "confirm", "M5": "ask", "M6": "hand_over",
          "M7": "decline_or_stop", "M8": "decline_or_stop"}
CALLS = []          # every request body and its Authorization header, for the tests to read
REQUIRE_TOKEN = {"value": ""}


@app.get("/healthz")
async def healthz():
    return {"ok": True, "core": "mock"}


@app.post("/v1/judge")
async def judge(request: Request):
    body = await request.json()
    auth = request.headers.get("authorization", "")
    CALLS.append({"body": body, "authorization": auth})
    if REQUIRE_TOKEN["value"] and auth != "Bearer " + REQUIRE_TOKEN["value"]:
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    state = body.get("state")
    if not body.get("caller") or not isinstance(state, str) or not state.strip():
        return JSONResponse(status_code=422, content={"error": "need_caller_and_state"})
    # the marker is read from the user's current message only, the way the real judge weighs it
    current = state.rsplit("User (this message):", 1)[-1]
    if "[503]" in current:
        return JSONResponse(status_code=503, content={"error": "judge_timeout", "detail": "mock"})
    m = re.search(r"\[(M[1-8])\]", current)
    mode = m.group(1) if m else "M1"
    if "[slow]" in current:
        time.sleep(0.4)
    return {
        "id": "j_mock_%s" % secrets.token_hex(3),
        "caller": body["caller"],
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": mode,
        "class5_key": CLASS5[mode],
        "reason": "MOCK marker %s in state" % mode,
        "vector": {},
        "disagree": False,
        "versions": {"core": "mock", "rubric": body.get("rubric") or "mock"},
        "latency_ms": 0,
        "has_action": body.get("action") is not None,
        "mock": True,
    }
