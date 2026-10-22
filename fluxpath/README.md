# FluxPath

AI harness with a judgment layer. Built by Eastsoft Inc. on Open WebUI; protocol iLang v5.0.

This directory holds everything FluxPath adds. The rest of the repository is Open WebUI as
published upstream, with its branding intact as the Open WebUI License requires; the instance
name is set through `WEBUI_NAME`, which upstream renders as "FluxPath (Open WebUI)".

- `judgment-filter/` the Open WebUI filter function that asks the iLang judgment endpoint
  (`POST /v1/judge`, see ilang-ai/ilang-judge) before every model request and acts on the mode
  M1 to M8. Judgments are logged in the browser, never on the server. Tests run against a local
  mock judge: `python test_filter.py`.
- `landing/` the static page served at fluxpath.app.
- `deploy/` Docker Compose for app.fluxpath.app behind a Cloudflare Tunnel, the settings that keep
  user conversations off the server, and the setup scripts.
- `local-test/` runs an unmodified Open WebUI locally with mock model and mock judge endpoints
  to exercise the filter end to end.

Conversations are never stored on FluxPath servers: ordinary users get temporary chats only, and
every user brings their own model key.
