# -*- coding: utf-8 -*-
"""Local stand-in for an OpenAI-compatible model endpoint, for testing FluxPath without a real key.
The reply repeats what the model received, so a test can see what the judgment filter let through.

  python -m uvicorn mock_model:app --port 7862
"""
import asyncio
import json
import os
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

app = FastAPI(title="mock model", docs_url=None, redoc_url=None)
# A browser calls this endpoint directly when it is used as a user's own (direct) connection.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
MODEL = os.environ.get("MOCK_MODEL_ID", "mock-model")


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": MODEL, "object": "model", "created": 0, "owned_by": "mock"}]}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    last = next((_text(m.get("content")) for m in reversed(body.get("messages") or []) if m.get("role") == "user"), "")
    reply = "Mock model reply. The model received: " + last
    base = {"id": "chatcmpl-mock", "created": int(time.time()), "model": body.get("model") or MODEL}

    if not body.get("stream"):
        return dict(base, object="chat.completion",
                    choices=[{"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": "stop"}],
                    usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})

    def sse(delta, finish=None):
        chunk = dict(base, object="chat.completion.chunk",
                     choices=[{"index": 0, "delta": delta, "finish_reason": finish}])
        return "data: " + json.dumps(chunk) + "\n\n"

    async def stream():
        yield sse({"role": "assistant", "content": ""})
        for i in range(0, len(reply), 14):
            await asyncio.sleep(0.02)
            yield sse({"content": reply[i:i + 14]})
        yield sse({}, "stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")
