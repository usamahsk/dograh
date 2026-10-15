# Dograh — Run Locally

Pick **A** (Docker, recommended) for the fastest working setup. Pick **B** only if you are editing backend code daily.

## Prerequisites

- Docker + Docker Compose: `docker compose version`
- Git
- 8 GB RAM free (minimum)

## A. Local Docker run (recommended)

```bash
# 1. Clone (this fork) with the pipecat submodule
git clone --recurse-submodules https://github.com/usamahsk/dograh.git
cd dograh

# 2. Start everything (postgres, redis, minio, api, ui).
#    Creates .env secrets on first run, then asks to start.
./start_docker.sh
```

First boot takes 2–3 minutes (image pulls). Then open:

```
http://localhost:3010
```

Verify the API is healthy:

```bash
curl http://localhost:8000/api/v1/health
```

Stop / restart:

```bash
docker compose down        # stop (keeps data volumes)
docker compose up -d       # start again
```

### If phone-test audio fails locally (no audio / ICE failed)

Browsers on other LAN devices, VPNs, and strict NATs need a TURN relay:

```bash
./scripts/setup_local.sh
# answer y to coturn, Enter for host (or your LAN IP), Enter for secret
docker compose --profile local-turn --profile tunnel up --pull always
```

App is still at `http://localhost:3010`.

## B. Contributor run (code editing, fast reload)

Follows `docs/contribution/setup.mdx`. Roughly:

```bash
git clone --recurse-submodules https://github.com/usamahsk/dograh.git
cd dograh
docker compose -f docker-compose-local.yaml up -d   # postgres, redis, minio only
# then backend venv + frontend per docs/contribution/setup.mdx:
#   api/.env  for backend scripts, api/.env.test for pytest, ui/.env for frontend
```

## Useful commands

```bash
docker compose ps                 # container status
docker compose logs -f api        # backend logs
docker compose logs -f ui         # frontend logs
curl http://localhost:8000/api/v1/health
```

## Notes

- Do **not** run bare `docker compose up` on a fresh checkout without `./start_docker.sh` first — required secrets (e.g. `OSS_JWT_SECRET`) won't exist.
- After `git pull`, if the `pipecat/` submodule moved: `git submodule update --init --recursive`.
