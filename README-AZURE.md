# Dograh — Deploy to Azure (VM + Scalable Containers)

Three paths, cheapest first:

| Path | What | When |
|------|------|------|
| **1. Azure VM (single instance)** | Docker Compose stack on one VM, same as upstream remote deploy | Dev / staging / low-volume prod. Cheapest. |
| **2. Azure Container Apps** | Managed containers, scale-out web tier | You want autoscale without managing Kubernetes. |
| **3. AKS + Helm chart** | Full Kubernetes, decomposed workers + HPA | High-volume prod. Most setup. |

Because this fork carries custom code (Genesys agent-stream, Azure call artifacts), **always deploy in build mode** (images compiled from your repo), never the prebuilt official images.

All commands assume `bash`. Replace `usamahsk/dograh` with your fork if different.

---

## 0. Fork prep (do once, on your machine)

```bash
git clone --recurse-submodules https://github.com/usamahsk/dograh.git
cd dograh
git checkout main
git submodule update --init --recursive   # required: pipecat/ is a submodule
```

Push any change you want deployed first:

```bash
git add <files>   # never add local-only dirs (e.g. MergedtoDeploy/)
git commit -m "feat: describe change"
git push origin main
```

---

## 1. Azure VM — single instance (Docker Compose)

Mirrors the upstream remote flow (`docs/deployment/docker.mdx`): nginx + HTTPS + coturn on one box. Minimum size: **4 vCPU / 8 GB RAM** (e.g. `Standard_D4s_v5`).

### 1.1 Create the VM + open ports

```bash
az login
az group create -n dograh-rg -l westeurope

az vm create \
  -g dograh-rg -n dograh-vm \
  --image Ubuntu2404 \
  --size Standard_D4s_v5 \
  --admin-username azureuser \
  --generate-ssh-keys \
  --public-ip-sku Standard

# TCP: SSH, HTTP, HTTPS, TURN/TURNS
az vm open-port -g dograh-rg -n dograh-vm --port 22,80,443,3478,5349 --priority 100

# UDP: TURN + RTP media range (TCP rule above does NOT cover UDP)
az network nsg rule create \
  -g dograh-rg --nsg-name dograh-vmNSG \
  -n Allow-TURN-UDP --priority 101 \
  --direction Inbound --access Allow --protocol Udp \
  --destination-port-ranges 3478 5349 49152-49200
```

Get the public IP:

```bash
az vm show -d -g dograh-rg -n dograh-vm --query publicIps -o tsv
```

### 1.2 Install Docker on the VM

```bash
ssh azureuser@<VM_PUBLIC_IP>
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin
sudo usermod -aG docker $USER && exit
# log back in so group membership applies
ssh azureuser@<VM_PUBLIC_IP>
docker compose version
```

### 1.3 Deploy (build mode = your fork)

```bash
curl -o setup_remote.sh https://raw.githubusercontent.com/usamahsk/dograh/main/scripts/setup_remote.sh
chmod +x setup_remote.sh
sudo ./setup_remote.sh
```

Answer the prompts:

- **Server IP:** the VM public IP. Public IP → free trusted HTTPS via sslip.io + Let's Encrypt, printed as `https://<ip-with-dashes>.sslip.io`. Private IP → self-signed cert (browser warning).
- **Deployment mode:** `build` (clones your fork; it will ask for `owner/name` = `usamahsk/dograh` and branch `main`).
- **FastAPI workers:** Enter for default `4` (nginx `least_conn` load-balances them; ~300–500 MB RAM each).

Start the stack (public-IP installs are already started by setup; self-signed installs run):

```bash
cd dograh
./remote_up.sh --build
```

Verify:

```bash
curl -k https://<VM_PUBLIC_IP>/api/v1/health
docker compose --profile remote ps
```

Open the printed `https://…sslip.io` (or `https://<VM_PUBLIC_IP>`) in a browser.

### 1.4 Azure Blob for recordings + transcripts (this fork)

Official compose does not wire Azure env into the `api` container, so add an override (kept next to `docker-compose.yaml`; compose auto-loads `docker-compose.override.yaml` — in build mode that file already exists, append to it):

```yaml
# docker-compose.azure.yaml  (merge into docker-compose.override.yaml)
services:
  api:
    environment:
      ENABLE_AZURE_BLOB_STORAGE: "true"
      AZURE_STORAGE_CONNECTION_STRING: "<storage-account-connection-string>"
      AZURE_BLOB_CONTAINER: "voice-audio"
```

Connection string alternative — account name + key instead:

```yaml
      # AZURE_STORAGE_ACCOUNT_NAME: "<account>"
      # AZURE_STORAGE_ACCOUNT_URL: "https://<account>.blob.core.windows.net"
      # AZURE_STORAGE_ACCOUNT_KEY: "<key>"
```

Apply:

```bash
cd dograh
./remote_up.sh --build
curl -k https://<VM_PUBLIC_IP>/api/v1/health
```

Place a test call, then confirm the run row has `storage_backend=azure` and the recording/transcript download. Only call artifacts (recordings + transcripts) go to Blob; everything else stays on the default backend.

### 1.5 Update after a code change

```bash
cd dograh
git pull
git submodule update --init --recursive   # if pipecat/ moved
sudo docker compose --profile remote build api ui
sudo docker compose --profile remote up -d
sudo docker compose --profile remote ps   # CREATED column must be fresh
```

Single service (faster): `build api` + `up -d api`. Stale layers after dependency changes: add `--no-cache`.

---

## 2. Azure Container Apps — scalable, no Kubernetes

Managed, scale-to-zero-capable hosting for the `api` + `ui` images; keep Postgres/Redis as Azure managed services.

### 2.1 Build + push images to Azure Container Registry

```bash
az acr create -g dograh-rg -n dograhacr --sku Basic
az acr login -n dograhacr

# From your local repo checkout (needs Docker; build context is the repo root):
docker build -f api/Dockerfile -t dograhacr.azurecr.io/dograh-api:latest .
docker build -f ui/Dockerfile  -t dograhacr.azurecr.io/dograh-ui:latest .
docker push dograhacr.azurecr.io/dograh-api:latest
docker push dograhacr.azurecr.io/dograh-ui:latest
```

### 2.2 Managed data services

```bash
# Postgres (flexible server) + Redis (managed cache)
az postgres flexible-server create -g dograh-rg -n dograh-pg \
  --sku-name Standard_D2s_v3 --tier GeneralPurpose --storage-size 64 --version 17
az redis create -g dograh-rg -n dograh-redis --sku Basic --vm-size c0
```

Create the Blob container for call artifacts (Storage account → `voice-audio` container), then collect: `DATABASE_URL`, `REDIS_URL`, `AZURE_STORAGE_CONNECTION_STRING`.

### 2.3 Create the environment + apps

```bash
az extension add -n containerapp --upgrade
az containerapp env create -g dograh-rg -n dograh-env --location westeurope

az containerapp create -g dograh-rg -n dograh-api \
  --environment dograh-env \
  --image dograhacr.azurecr.io/dograh-api:latest \
  --registry-server dograhacr.azurecr.io \
  --target-port 8000 --ingress external --transport http \
  --min-replicas 1 --max-replicas 6 --cpu 2.0 --memory 4Gi \
  --env-vars \
    ENVIRONMENT=production \
    DATABASE_URL='<postgres-url>' \
    REDIS_URL='<redis-url>' \
    OSS_JWT_SECRET="$(openssl rand -hex 32)" \
    PUBLIC_BASE_URL='https://<api-fqdn>' \
    ENABLE_AZURE_BLOB_STORAGE=true \
    AZURE_STORAGE_CONNECTION_STRING='<blob-connection-string>' \
    AZURE_BLOB_CONTAINER=voice-audio

az containerapp create -g dograh-rg -n dograh-ui \
  --environment dograh-env \
  --image dograhacr.azurecr.io/dograh-ui:latest \
  --registry-server dograhacr.azurecr.io \
  --target-port 3010 --ingress external \
  --min-replicas 1 --max-replicas 3 --cpu 1.0 --memory 2Gi \
  --env-vars BACKEND_URL='http://dograh-api:8000' NODE_ENV=oss
```

Run DB migrations against the managed Postgres before first traffic (from any machine with repo + `DATABASE_URL` pointing at Azure):

```bash
./scripts/migrate.sh
```

### 2.4 Caveats (read before prod)

- **WebSockets stick to a revision replica** — scale-out spreads *new* calls; a single long call never migrates. Size replicas for concurrent-call count.
- **Singletons stay at 1**: ARQ background workers and any in-memory coordinators (`ari_manager`, `campaign_orchestrator`) must not scale out — keep them on one replica (or the VM path) until leader election exists.
- **TURN**: Container Apps has no host UDP range; run coturn on the small VM from path 1 (or an Azure LoadBalancer VM) and set `TURN_HOST`/`TURN_SECRET` on the api app.

---

## 3. AKS + Helm chart — full Kubernetes scale-out

Repo chart: `deploy/helm/dograh/` (web + ARQ workers + telephony/campaign singletons + UI + coturn; HPA templates included, singletons hard-pinned to 1). Status: **alpha — `helm lint`/`helm template` validated, not yet exercised live**. Expect to shake it down in staging first.

```bash
# 1. Cluster (autoscale node pool) + registry wiring
az aks create -g dograh-rg -n dograh-aks \
  --node-count 2 --enable-cluster-autoscaler --min-count 2 --max-count 6 \
  --node-vm-size Standard_D4s_v5 --attach-acr dograhacr
az aks get-credentials -g dograh-rg -n dograh-aks

# 2. Push images (see 2.1), then install with managed DB/Redis/S3-or-Blob values
cd deploy/helm/dograh
helm lint .
helm template dograh . -f examples/values-managed.yaml > /tmp/render.yaml  # inspect

helm install dograh . \
  -f examples/values-managed.yaml \
  --set secrets.databaseUrl='<postgres-url>' \
  --set secrets.redisUrl='<redis-url>' \
  --set secrets.ossJwtSecret="$(openssl rand -hex 32)" \
  --set secrets.turnSecret="$(openssl rand -hex 32)" \
  --set exposure.gatewayApi.gatewayClassName='<your-gateway-class>'
```

Scale knobs afterwards: `FASTAPI_WORKERS`-equivalent web replicas + HPA (`autoscaling.web/ui/workers`), one worker per vCPU as a starting point (`docs/deployment/scaling.mdx`). For Azure Blob artifacts, add the section-1.4 env (`ENABLE_AZURE_BLOB_STORAGE`, connection string, container) to the chart's api env in `values.yaml`.

---

## Cheat sheet

```bash
# health
curl -k https://<HOST>/api/v1/health
# status / logs (VM path)
sudo docker compose --profile remote ps
sudo docker compose --profile remote logs -f api
# rebuild after git pull (VM path)
git pull && git submodule update --init --recursive
sudo docker compose --profile remote build api ui
sudo docker compose --profile remote up -d
```
