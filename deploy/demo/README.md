# Dograh + Genesys AudioHook — Local Setup Guide

Guide for running Dograh locally with the Genesys Cloud CX (AudioHook)
integration, and connecting it to a Genesys Cloud org end to end.

---

## 0. Current state of this machine (as configured)

Everything runs on the **Docker Desktop engine** — visible in the Docker
Desktop GUI and via the `docker` CLI (default context).

| Container | Role | Status field to expect |
|---|---|---|
| `dograh-postgres` | Database (pgvector 17) | healthy |
| `dograh-redis` | Cache / queue | healthy |
| `dograh-minio` | Audio storage | healthy |
| `dograh-api` | Dograh API **with Genesys provider** | healthy |
| `dograh-ui` | Web UI | running |
| `dograh-nginx` | Single origin: UI + API + audio | running |
| `dograh-tunnel` | Public wss URL for Genesys | running |

### Current values (copy/paste for Genesys)

| What | Value |
|---|---|
| Web UI | `http://localhost:8080` |
| API direct (host) | `http://localhost:8000` (required by the UI's voice test-run) |
| Public origin (via tunnel) | `https://wet-strengthen-prisoners-andale.trycloudflare.com` |
| **Genesys Base Connection URI** | `wss://wet-strengthen-prisoners-andale.trycloudflare.com/api/v1/telephony/genesys` |
| Connector ID (per agent) | the agent's `workflow_uuid` (from the agent page URL in the UI) |

> The trycloudflare domain is random and **changes every time the tunnel
> restarts**. Always re-read it: `docker logs dograh-tunnel | grep trycloudflare`
> and update the Genesys integration if it changed.

---

## 1. Daily start / stop

**From Docker Desktop GUI:** stop/start the `dograh-*` containers. Data
survives restarts (postgres + minio use named volumes).

**From CLI:**

```bash
# stop everything
docker compose -f deploy/demo/docker-compose.yaml --profile tunnel stop

# start everything again
docker compose -f deploy/demo/docker-compose.yaml --profile tunnel start

# status
docker compose -f deploy/demo/docker-compose.yaml --profile tunnel ps

# tunnel URL
docker logs dograh-tunnel 2>&1 | grep trycloudflare

# API logs (Genesys sessions appear here)
docker logs -f dograh-api 2>&1 | grep -i genesys
```

**After code changes** (rebuild the changed image, then recreate):

```bash
docker build -f api/Dockerfile -t dograh-api:genesys .
docker build -f ui/Dockerfile  -t dograh-ui:genesys  .

docker compose -f deploy/demo/docker-compose.yaml --profile tunnel up -d --force-recreate api ui
```

---

## 2. Dograh-side setup (browser, ~5 min)

1. Open `http://localhost:8080` → **Sign up** (create your org user).
2. Create your **agent** (workflow), configure and **publish** it.
3. Open the agent and copy the **workflow UUID** from the URL
   (`.../workflow/<uuid>/...`) — this is the Genesys **Connector ID**.
4. Go to **Telephony Configurations → Add → Genesys Cloud CX (AudioHook)**.
5. Enter your own **API Key** (e.g. `dograh-genesys-key-01`) and a strong
   **Client Secret** → Save.
   - Same values go into Genesys in step 4.3/4.4 below.
   - Client Secret left empty = unsigned connections accepted.
6. Base Connection URI for Genesys = section 0 table (append nothing —
   Genesys adds the Connector ID as a path itself).

---

## 3. Local protocol test (before touching Genesys)

Simulates a Genesys AudioHook client (open → audio → ping → close):

```bash
python3 /tmp/opencode/genesys_sim.py \
  wss://wet-strengthen-prisoners-andale.trycloudflare.com/api/v1/telephony/genesys/<AGENT_UUID> \
  <API_KEY>
```

Expected: `open → opened`, `ping → pong`, `close → closed`.

Negative checks (no setup needed):

```bash
curl -s http://localhost:8080/api/v1/telephony/genesys/x
# → {"detail":"Not Found"}  — expected: the route is WebSocket-only
```

| Close code | Meaning |
|---|---|
| 4401 | Missing/mismatched `X-API-KEY` or bad signature |
| 4404 | Unknown Connector ID (agent `workflow_uuid` not found) |
| 1008 | Quota exceeded |

---

## 4. Genesys Cloud AudioConnector setup (client org or trial)

**4.0 — Get access** (do this first)
- Client sandbox org with an Admin user, **or** a Genesys Cloud CX trial
  (`mypurecloud.com` → Start for free).
- Audio Connector is a **premium app** — if gated on the trial, request
  enablement from Genesys sales (mention AudioHook POC).

**4.1 — Install Audio Connector**
1. Genesys Cloud → **Admin → Integrations → Integrations**
2. Search **"Audio Connector"** → click card → **Install**

**4.2 — Configure + activate**
1. Open the integration → **Details** tab → name it `Dograh AudioHook`
2. **Configuration → Properties** → Base Connection URI =
   `wss://wet-strengthen-prisoners-andale.trycloudflare.com/api/v1/telephony/genesys`
   (always re-check the tunnel domain first!)
3. **Configuration → Credentials → Configure** → paste the same
   **API Key** + **Client Secret** from step 2.5 → OK → **Save**
4. **Details** tab → toggle **Inactive → Active → Yes** (toggle twice —
   the second activation validates the handshake against Dograh live)

**4.3 — Architect flow**
1. **Admin → Architect → + → Inbound Call** flow
2. Toolbox → **Bot** category → drag **Call Audio Connector** into the task
3. Set **Integration** = Dograh AudioHook
4. Set **Connector ID** = the agent's `workflow_uuid` (step 2.3)
5. Optional: **Inputs** (e.g. caller data), **Outputs** (variables Dograh
   returns for routing), **Failure Outputs** (`errorType`/`errorMessage`)
6. **Publish** the flow

**4.4 — Route a phone number**
- Easiest: edit the trial org's **default inbound flow** (already bound to
  its trial DID) and add the same Call Audio Connector action, **or**
- Assign your flow to a DID via the number's call route
  (Admin → Telephony → Numbers).

**4.5 — Test**
1. Call the DID.
2. Watch: `docker logs -f dograh-api 2>&1 | grep -i genesys`
   → expect `Genesys AudioHook connected: workflow=... config=...`
3. The caller talks to the Dograh agent; on completion the Architect flow
   resumes with Dograh's output variables (e.g. transfer to queue).

---

## 5. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Docker Desktop GUI empty | Docker Desktop app not running → `systemctl --user start docker-desktop` |
| `{"detail":"Not Found"}` on the genesys path | Normal for plain HTTP GET — the route only speaks WebSocket |
| Genesys activation fails | Tunnel restarted and the domain changed → re-read `docker logs dograh-tunnel`, update the Base URI |
| Genesys connects, instant close 4401 | API key mismatch or bad client secret |
| Close 4404 | Connector ID in Architect ≠ agent `workflow_uuid` |
| Session drops at 15 min | Genesys default AudioHook max duration (raise via Genesys Customer Care) |
| No audio but handshake OK | Check agent STT/TTS keys configured in Dograh; PCMU 8 kHz is negotiated automatically |
| Audio Connector missing from AppFoundry search | Premium app not enabled on the org → contact Genesys sales |

---

## 6. Architecture (one line for the client call)

> Genesys Cloud streams call audio to Dograh over a secure WebSocket
> (AudioHook protocol); Dograh's AI agent answers the caller and returns
> outcome variables to the Genesys flow for routing or agent transfer.
