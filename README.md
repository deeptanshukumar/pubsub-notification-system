# Secure Socket Pub/Sub with Live Dashboard

A computer-networks mini-project using low-level socket programming, TLS security, multi-client concurrency, and live observability.

## What This Project Demonstrates
- Direct TCP socket programming (`socket`, `send`, `recv`, framed protocol)
- TLS-secured broker-client communication (`ssl`)
- Concurrent client handling (thread-per-client + keepalive thread)
- Custom protocol commands (`PUBLISH`, `SUBSCRIBE`, `ACK`, `MESSAGE`, `PING/PONG`)
- Runtime dashboard + monitoring APIs
- Repeatable benchmark evidence (latency/throughput under load)

## Quick Start (Local Laptop)
```bash
cd /home/deeptanshukumar/Downloads/files
bash generate_certs.sh
python server.py
```
Open dashboard:
```text
http://127.0.0.1:8088
```

In separate terminals:
```bash
python subscriber.py
python publisher.py
```

## 3-Laptop Setup (Server + Publisher + Subscriber)

### Topology
- Laptop A: broker server (this machine)
- Laptop B: subscriber client
- Laptop C: publisher client

All 3 must be on the same network (same Wi-Fi/hotspot/router).

### Step 1) Find Laptop A LAN IP
On Laptop A:
- Linux/macOS:
  ```bash
  hostname -I
  ```
  or
  ```bash
  ip addr
  ```
- Windows (PowerShell/CMD):
  ```powershell
  ipconfig
  ```

Use the IPv4 address (example: `192.168.1.50`).

### Step 2) Start server in LAN mode on Laptop A
```bash
cd /home/deeptanshukumar/Downloads/files
python server.py --host 0.0.0.0 --port 9999 --dashboard-host 0.0.0.0 --dashboard-port 8088
```

### Step 3) Open firewall ports on Laptop A
Allow inbound TCP ports:
- `9999` (broker)
- `8088` (dashboard/API)

Windows (PowerShell as Admin):
```powershell
netsh advfirewall firewall add rule name="PubSub Broker 9999" dir=in action=allow protocol=TCP localport=9999
netsh advfirewall firewall add rule name="PubSub Dashboard 8088" dir=in action=allow protocol=TCP localport=8088
```

Linux (if UFW enabled):
```bash
sudo ufw allow 9999/tcp
sudo ufw allow 8088/tcp
```

### Step 4) Connect Laptop B as subscriber
```bash
python subscriber.py --host <SERVER_LAN_IP> --port 9999
```
Example:
```bash
python subscriber.py --host 192.168.1.50 --port 9999
```
Then subscribe to a topic in interactive shell.

### Step 5) Connect Laptop C as publisher
```bash
python publisher.py --host <SERVER_LAN_IP> --port 9999
```
Or one-shot:
```bash
python publisher.py --host <SERVER_LAN_IP> --port 9999 cn-lab "hello from laptop C"
```

### Step 6) Validate end-to-end
- Laptop B should display incoming message.
- Laptop A `logs/server.log` should show remote client IPs.
- Any laptop can open dashboard using:
  ```text
  http://<SERVER_LAN_IP>:8088
  ```

### If campus network blocks peer traffic
Use fallback: create a mobile hotspot and connect all 3 laptops to it.

## 5-Minute Final Demo Flow
1. Start server in LAN mode from Laptop A.
2. Subscriber joins from Laptop B and subscribes.
3. Publisher sends from Laptop C.
4. Show live dashboard (`/api/snapshot`) updating remotely.
5. Run benchmark script and show `results/` + `docs/performance.md` evidence.

Detailed viva script: [docs/demo-guide.md](docs/demo-guide.md)

## Architecture and Protocol
- Architecture + diagrams: [docs/architecture.md](docs/architecture.md)
- Stability/fix evidence: [docs/stability-and-fixes.md](docs/stability-and-fixes.md)
- Performance report: [docs/performance.md](docs/performance.md)

### Core TCP Protocol
Client to broker:
- `PUBLISH|topic|message`
- `SUBSCRIBE|topic`
- `UNSUBSCRIBE|topic`
- `LIST_TOPICS`, `STATS`, `LIST_CLIENTS`
- `PONG`, `DISCONNECT`

Broker to client:
- `ACK|...`, `ERROR|...`
- `MESSAGE|topic|publisher|timestamp|content`
- `TOPICS|...`, `STATS|...`, `CLIENTS|...`, `PING`

### Dashboard/API Endpoints
- `GET /api/health`
- `GET /api/snapshot`
- `POST /api/topic`
- `POST /api/publish`

## Performance Evaluation (Rubric Component 4)
Run all standard rubric scenarios (S1/S2/S3):
```bash
python scripts/load_harness.py --run-standard-scenarios --output results/rubric_benchmark.csv --markdown docs/performance.md
```

Custom benchmark:
```bash
python scripts/load_harness.py --publishers 5 --subscribers 10 --messages 200 --topics 4 --output results/custom_run.csv --markdown docs/performance.md
```

Outputs:
- CSV: `results/*.csv`
- Summary report: `docs/performance.md`

## Rubric Mapping (40 Marks)
| Rubric Component | Exact Evidence | Repro Command |
|---|---|---|
| 1. Problem Definition and Architecture (6) | Objectives + architecture/protocol docs in `README.md` and `docs/architecture.md` | `cat docs/architecture.md` |
| 2. Core Implementation (8) | Low-level socket lifecycle + command processing in `server.py`, `publisher.py`, `subscriber.py` | `python server.py --host 127.0.0.1 --port 9999` |
| 3. Feature Implementation – Deliverable 1 (8) | Multi-client pub/sub + TLS + dashboard publish/topic APIs | `python subscriber.py --host <IP> --port 9999` and `python publisher.py --host <IP> --port 9999 ...` |
| 4. Performance Evaluation (7) | Scenario benchmarks + latency/throughput CSV/report | `python scripts/load_harness.py --run-standard-scenarios --output results/rubric_benchmark.csv --markdown docs/performance.md` |
| 5. Optimization and Fixes (5) | Keepalive/timeout/error handling and edge-case evidence | `cat docs/stability-and-fixes.md` |
| 6. Final Demo + GitHub Packaging – Deliverable 2 (6) | Demo runbook + commands + docs cross-links | `cat docs/demo-guide.md` |

## Troubleshooting
### SSL cert issue
```bash
bash generate_certs.sh
```

### Port in use
```bash
pkill -f "python server.py"
python server.py
```

### Remote client cannot connect
- Verify Laptop A IP and same network.
- Confirm firewall allows TCP 9999 and 8088.
- Start server with `--host 0.0.0.0`.

### Dashboard not reachable from other laptops
- Start with `--dashboard-host 0.0.0.0`.
- Open `http://<SERVER_LAN_IP>:8088`.

## Repository Structure
```text
.
├── server.py
├── publisher.py
├── subscriber.py
├── dashboard.html
├── config.py
├── scripts/
│   └── load_harness.py
├── docs/
│   ├── architecture.md
│   ├── demo-guide.md
│   ├── performance.md
│   └── stability-and-fixes.md
├── results/
├── logs/
└── generate_certs.sh
```

## Suggested Viva Talking Points
- TCP chosen for ordered reliable delivery + ACK semantics.
- TLS integration and local-lab trust model for self-signed certificates.
- Concurrency model and lock-protected shared state.
- Keepalive strategy for dead-client cleanup.
- Benchmark interpretation (throughput and p95/p99 trends across S1/S2/S3).
