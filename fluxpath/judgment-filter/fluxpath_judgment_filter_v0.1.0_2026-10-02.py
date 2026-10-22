"""
title: FluxPath Judgment Layer
author: Eastsoft Inc.
author_url: https://fluxpath.app
description: Before every model request, asks an iLang v5.0 judgment endpoint (POST /v1/judge) how to proceed and acts on the returned mode, M1 to M8. Judgments are logged in the browser, not on the server.
version: 0.1.0
license: MIT
"""

import asyncio
import hashlib
import json
import time
import urllib.error
import urllib.request
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# What each mode means, in the words of the judgment endpoint's own five-class criteria.
MODE_TEXT = {
    "M1": "act and report afterwards",
    "M2": "act with a full audit trail",
    "M3": "propose and wait for confirmation",
    "M4": "advise only, without acting",
    "M5": "ask a clarifying question first",
    "M6": "hand the decision to a human",
    "M7": "decline and offer an alternative",
    "M8": "stop at a hard boundary",
}
PASS, CONFIRM, ASK, BLOCK = {"M1", "M2"}, {"M3", "M4"}, {"M5"}, {"M6", "M7", "M8"}

# The same shape the Hermes observer sends before a reply: state is the user's message with recent
# context, action is the reply that has not been generated yet.
ACTION = "Reply to the user (reply not generated yet)"
LOG_KEY = "fluxpath.judgments"
DECISION_TTL_S = 30
NOTE_TTL_S = 600


class JudgeUnavailable(Exception):
    pass


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def build_state(messages: list, turns: int) -> tuple[str, int]:
    """The user's latest message with recent context. Returns (state, index of that message)."""
    last = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "user"), -1)
    if last < 0:
        return "", -1
    msg = _text(messages[last].get("content")).strip()
    if not msg:
        return "", last
    recent = []
    for m in messages[max(0, last - 6) : last]:
        if m.get("role") in ("user", "assistant"):
            t = _text(m.get("content")).strip()
            if t:
                recent.append(("User: " if m["role"] == "user" else "Assistant: ") + t[:300])
    recent = recent[-turns:] if turns > 0 else []
    return ("\n".join(recent) + "\n" if recent else "") + "User (this message): " + msg[:2000], last


def _append_to_message(message: dict, extra: str) -> None:
    content = message.get("content")
    if isinstance(content, list):
        content.append({"type": "text", "text": extra})
    else:
        message["content"] = (content or "").rstrip() + "\n\n" + extra


def _append_note(message: dict, note: str) -> None:
    """Append the note to an assistant message. The chat renders the structured output when the
    message has one, so the note goes into its last text part as well as into the plain content."""
    for item in reversed(message.get("output") or []):
        if isinstance(item, dict) and item.get("type") == "message":
            parts = item.setdefault("content", [])
            if parts and isinstance(parts[-1], dict) and parts[-1].get("type") == "output_text":
                parts[-1]["text"] = (parts[-1].get("text") or "").rstrip() + "\n\n" + note
            else:
                parts.append({"type": "output_text", "text": note})
            break
    if isinstance(message.get("content"), str):
        message["content"] = message["content"].rstrip() + "\n\n" + note


class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=0, description="Filters run in ascending priority.")
        judge_url: str = Field(
            default="", description="Base URL of the judgment endpoint, for example https://<space>.hf.space"
        )
        judge_token: str = Field(
            default="",
            description="Bearer token for the endpoint (the Space owner's token if the Space is private).",
            json_schema_extra={"input": {"type": "password"}},
        )
        caller: str = Field(default="fluxpath", description="Caller name sent with every judgment request.")
        rubric: str = Field(default="", description="Rubric version to ask for. Empty uses the endpoint's default.")
        timeout_s: float = Field(default=12.0, description="Seconds to wait for the judgment endpoint.")
        context_turns: int = Field(default=4, description="Earlier turns sent as context with the user's message.")
        on_unavailable: Literal["pass", "block"] = Field(
            default="pass", description="What to do when the judgment endpoint fails or times out."
        )
        show_status: bool = Field(default=True, description="Show the judgment as a status line on each message.")
        local_log_max: int = Field(default=500, description="Judgments kept in the browser's local storage.")

    class UserValves(BaseModel):
        enabled: bool = Field(default=True, description="Check every message with the judgment layer before it is sent.")

    def __init__(self):
        self.valves = self.Valves()
        self._decisions = {}  # (user id, state hash) -> task resolving to a decision
        self._notes = {}  # response message id -> (time, note for the outlet)

    # ------------------------------------------------------------------ judgment endpoint
    def _post(self, payload: dict) -> dict:
        v = self.valves
        headers = {"Content-Type": "application/json"}
        if v.judge_token:
            headers["Authorization"] = "Bearer " + v.judge_token
        req = urllib.request.Request(
            v.judge_url.rstrip("/") + "/v1/judge",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=v.timeout_s) as r:
                record = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode("utf-8", "replace")).get("error")
            except Exception:
                detail = None
            raise JudgeUnavailable(detail or "http_%d" % e.code)
        except Exception as e:
            raise JudgeUnavailable(type(e).__name__)
        if not isinstance(record, dict) or record.get("mode") not in MODE_TEXT:
            raise JudgeUnavailable("unexpected_reply")
        return record

    # ------------------------------------------------------------------ browser side
    async def _status(self, emitter, text: str, done: bool = True) -> None:
        if emitter and self.valves.show_status:
            try:
                await emitter({"type": "status", "data": {"description": text, "done": done}})
            except Exception:
                pass

    async def _ask(self, event_call, kind: str, data: dict):
        """Ask the user through the browser. None when there is no browser session or the call fails."""
        if not event_call:
            return None
        try:
            return await event_call({"type": kind, "data": data})
        except Exception:
            return None

    async def _log_local(self, event_call, entry: dict) -> None:
        """Append the entry to the browser's local storage. The judge record inside it is stored as returned."""
        if not event_call or self.valves.local_log_max <= 0:
            return
        code = (
            "try{const k=%s;const l=JSON.parse(localStorage.getItem(k)||'[]');l.push(%s);"
            "while(l.length>%d)l.shift();localStorage.setItem(k,JSON.stringify(l));return l.length}"
            "catch(e){return -1}" % (json.dumps(LOG_KEY), json.dumps(entry), self.valves.local_log_max)
        )
        try:
            await asyncio.wait_for(event_call({"type": "execute", "data": {"code": code}}), timeout=5)
        except Exception:
            pass

    # ------------------------------------------------------------------ one decision per message
    async def _decide(self, state: str, event_call, emitter, chat_id, message_id) -> dict:
        """Judge the state and settle what happens to the request. Never raises."""
        payload = {"caller": self.valves.caller, "state": state[:6000], "action": ACTION}
        if self.valves.rubric:
            payload["rubric"] = self.valves.rubric
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "chat_id": chat_id, "message_id": message_id}

        await self._status(emitter, "Judgment layer: checking", done=False)
        try:
            record = await asyncio.to_thread(self._post, payload)
        except JudgeUnavailable as e:
            sent = self.valves.on_unavailable == "pass"
            outcome = "sent_unchecked" if sent else "blocked_unavailable"
            await self._log_local(event_call, dict(entry, outcome=outcome, error=str(e)))
            if sent:
                await self._status(emitter, "Judgment layer unavailable (%s): sent without a check" % e)
                return {"outcome": outcome}
            await self._status(emitter, "Judgment layer unavailable (%s)" % e)
            return {
                "outcome": outcome,
                "error": "Not sent: the judgment layer is unavailable (%s). Try again in a moment." % e,
            }

        mode, reason = record["mode"], str(record.get("reason") or "")
        head = "Judgment %s: %s" % (mode, MODE_TEXT[mode])
        decision = {"mode": mode}

        if mode in PASS:
            decision["outcome"] = "sent"
            if mode == "M2":
                decision["note"] = "*%s. %s (%s)*" % (head, reason, record.get("id", ""))
        elif mode in CONFIRM:
            answer = await self._ask(
                event_call, "confirmation", {"title": head, "message": "%s\n\nSend this message anyway?" % reason}
            )
            if answer is True:
                decision["outcome"] = "confirmed"
            else:
                decision["outcome"] = "declined"
                decision["error"] = "Not sent. %s, and it was not confirmed. %s" % (head, reason)
        elif mode in ASK:
            answer = await self._ask(
                event_call,
                "input",
                {
                    "title": head,
                    "message": "%s\n\nAdd the missing detail and it will be sent with your message." % reason,
                    "placeholder": "Add detail",
                },
            )
            if isinstance(answer, str) and answer.strip():
                decision["outcome"] = "sent_with_detail"
                decision["extra"] = answer.strip()
            else:
                decision["outcome"] = "not_answered"
                decision["error"] = "Not sent. %s. %s" % (head, reason)
        else:
            decision["outcome"] = "blocked"
            decision["error"] = "Blocked. %s. %s" % (head, reason)

        await self._log_local(event_call, dict(entry, outcome=decision["outcome"], judge=record))
        await self._status(emitter, head)
        return decision

    def _forget(self, key) -> None:
        self._decisions.pop(key, None)
        cutoff = time.time() - NOTE_TTL_S
        for message_id in [m for m, (t, _) in self._notes.items() if t < cutoff]:
            self._notes.pop(message_id, None)

    # ------------------------------------------------------------------ filter hooks
    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_call__=None,
        __event_emitter__=None,
        __metadata__: Optional[dict] = None,
    ) -> dict:
        user, metadata = __user__ or {}, __metadata__ or {}
        user_valves = user.get("valves")
        if user_valves is not None and not getattr(user_valves, "enabled", True):
            return body
        if not self.valves.judge_url or metadata.get("task"):
            return body

        messages = body.get("messages") or []
        state, last = build_state(messages, self.valves.context_turns)
        if not state:
            return body

        # Several models answering one message, or a quick retry, share one judgment and one dialog.
        key = (user.get("id"), hashlib.sha1(state.encode("utf-8")).hexdigest())
        task = self._decisions.get(key)
        shared = task is not None
        if not shared:
            task = asyncio.ensure_future(
                self._decide(state, __event_call__, __event_emitter__, metadata.get("chat_id"), metadata.get("message_id"))
            )
            self._decisions[key] = task
            task.add_done_callback(
                lambda _: asyncio.get_running_loop().call_later(DECISION_TTL_S, self._forget, key)
            )
        decision = await asyncio.shield(task)
        if shared and decision.get("mode"):
            await self._status(__event_emitter__, "Judgment %s: %s" % (decision["mode"], MODE_TEXT[decision["mode"]]))

        if "error" in decision:
            raise Exception(decision["error"])
        if decision.get("extra"):
            _append_to_message(messages[last], decision["extra"])
        if decision.get("note") and metadata.get("message_id"):
            self._notes[metadata["message_id"]] = (time.time(), decision["note"])
        return body

    async def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        note = self._notes.pop(body.get("id"), None)
        messages = body.get("messages") or []
        if note and messages and messages[-1].get("role") == "assistant":
            _append_note(messages[-1], note[1])
        return body
