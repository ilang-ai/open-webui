# -*- coding: utf-8 -*-
"""Runs a local Open WebUI (the pip release, unmodified) on 127.0.0.1:3000 for testing the FluxPath
judgment filter, together with the two local stand-ins it talks to: the mock judge (7861) and the
mock model (7862). Nothing here reaches the production judgment Space or a real model provider.

  .local/owui-venv/Scripts/python.exe local-test/run_owui.py
"""
import atexit
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LOCAL = os.path.join(ROOT, ".local")
SCRIPTS = os.path.dirname(sys.executable)

children = []


def spawn(app, app_dir, port, **extra_env):
    children.append(subprocess.Popen(
        [sys.executable, "-m", "uvicorn", app, "--app-dir", app_dir, "--host", "127.0.0.1", "--port", str(port),
         "--log-level", "warning"], env=dict(os.environ, **extra_env)))


def stop():
    for p in children:
        p.terminate()


atexit.register(stop)
spawn("mock_judge:app", os.path.join(ROOT, "judgment-filter"), 7861)
spawn("mock_model:app", HERE, 7862)
# a second model endpoint, for testing a user's own (direct) connection from the browser
spawn("mock_model:app", HERE, 7863, MOCK_MODEL_ID="direct-mock-model")

env = dict(os.environ)
env.update({
    "DATA_DIR": os.path.join(LOCAL, "owui-data"),
    "WEBUI_NAME": "FluxPath",                      # upstream renders this as "FluxPath (Open WebUI)"
    "OPENAI_API_BASE_URL": "http://127.0.0.1:7862/v1",
    "OPENAI_API_KEY": "mock-key",
    "ENABLE_OLLAMA_API": "false",
    "ENABLE_DIRECT_CONNECTIONS": "true",
    "BYPASS_MODEL_ACCESS_CONTROL": "true",
    # conversations of ordinary users are never written to the database
    "USER_PERMISSIONS_CHAT_TEMPORARY_ENFORCED": "true",
    # this Windows machine's application control policy blocks torch's DLLs, so the test venv has no
    # torch; embeddings are pointed away from the local sentence-transformers model
    "RAG_EMBEDDING_ENGINE": "openai",
    # keep the local run quiet and offline
    "OFFLINE_MODE": "true",
    "ENABLE_VERSION_UPDATE_CHECK": "false",
    "ENABLE_TITLE_GENERATION": "false",
    "ENABLE_TAGS_GENERATION": "false",
    "ENABLE_FOLLOW_UP_GENERATION": "false",
    "ENABLE_AUTOCOMPLETE_GENERATION": "false",
    "ANONYMIZED_TELEMETRY": "false",
    "SCARF_NO_ANALYTICS": "true",
    "DO_NOT_TRACK": "true",
    "PYTHONIOENCODING": "utf-8",
})

# --env-file <path>: lay a deployment env file over the local settings, to test it before it ships.
# Secrets left empty, the public URL and the embedding choice stay local.
if "--env-file" in sys.argv:
    keep_local = {"WEBUI_URL", "CORS_ALLOW_ORIGIN", "RAG_EMBEDDING_ENGINE"}
    with open(sys.argv[sys.argv.index("--env-file") + 1], encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                if value and key not in keep_local:
                    env[key] = value

os.makedirs(env["DATA_DIR"], exist_ok=True)
try:
    code = subprocess.call([os.path.join(SCRIPTS, "open-webui.exe"), "serve", "--host", "127.0.0.1", "--port", "3000"],
                           env=env, cwd=LOCAL)
finally:
    stop()
sys.exit(code)
