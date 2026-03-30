# Final Demo Guide (Deliverable 2)

## Demo Goal
Demonstrate a secure, concurrent, multi-client socket system with:
- TLS-secured communication
- Topic-based message routing
- Real-time observability
- Performance evidence under load

## Pre-Demo Checklist
- [ ] `certs/server.crt` and `certs/server.key` exist
- [ ] All laptops connected to same network/hotspot
- [ ] Firewall on server laptop allows TCP 9999 and 8088
- [ ] Server laptop LAN IP identified

## Inter-Laptop Runbook (3 Laptops)

### Role Assignment
- Laptop A: server + dashboard host
- Laptop B: subscriber
- Laptop C: publisher

### 1) Find LAN IP on Laptop A
- Linux/macOS:
  ```bash
  hostname -I
  ```
  or
  ```bash
  ip addr
  ```
- Windows:
  ```powershell
  ipconfig
  ```

Take IPv4 address, e.g. `192.168.1.50`.

### 2) Start server on Laptop A (LAN bind)
```bash
cd /home/deeptanshukumar/Downloads/files
python server.py --host 0.0.0.0 --port 9999 --dashboard-host 0.0.0.0 --dashboard-port 8088
```

### 3) Allow firewall ports on Laptop A
Windows (Admin PowerShell):
```powershell
netsh advfirewall firewall add rule name="PubSub Broker 9999" dir=in action=allow protocol=TCP localport=9999
netsh advfirewall firewall add rule name="PubSub Dashboard 8088" dir=in action=allow protocol=TCP localport=8088
```

Linux (UFW):
```bash
sudo ufw allow 9999/tcp
sudo ufw allow 8088/tcp
```

### 4) Start subscriber on Laptop B
```bash
python subscriber.py --host 192.168.1.50 --port 9999
```
Inside shell:
```text
subscribe cn-lab
```

### 5) Start publisher on Laptop C
```bash
python publisher.py --host 192.168.1.50 --port 9999
```
Inside shell:
```text
publish cn-lab hello from laptop C
```

### 6) Validate outcomes
- Laptop B should print incoming `MESSAGE` frame.
- Laptop A logs should show remote addresses from B/C.
- Open dashboard from any laptop:
  ```text
  http://192.168.1.50:8088
  ```

## 5-Minute Viva Script
1. Explain architecture with `docs/architecture.md` diagram.
2. Show LAN server startup command (`0.0.0.0` bind).
3. Live publish/subscribe across laptops.
4. Show dashboard metrics and event log updating.
5. Show benchmark evidence by running:
   ```bash
   python scripts/load_harness.py --run-standard-scenarios --output results/rubric_benchmark.csv --markdown docs/performance.md
   ```

## Expected Talking Points During Viva
- Why TCP for reliability and ordered delivery.
- How TLS is applied to broker-client sockets.
- How concurrency is implemented (thread-per-client + keepalive).
- How dead clients are detected (`PING/PONG`).
- Why S1/S2/S3 performance scenarios demonstrate scalability.

## Fast Troubleshooting During Demo
- No remote connection:
  - Verify same network.
  - Re-check server IP.
  - Re-check firewall rules.
  - Confirm server started with `--host 0.0.0.0`.
- Dashboard inaccessible remotely:
  - Start with `--dashboard-host 0.0.0.0`.
  - Confirm TCP 8088 open.
- Campus network blocks peer traffic:
  - Use mobile hotspot fallback.

## Final Submission Checklist
- [ ] Code pushed to GitHub
- [ ] `README.md` includes LAN setup and rubric mapping
- [ ] `docs/` complete (`architecture`, `demo-guide`, `performance`, `stability-and-fixes`)
- [ ] `results/rubric_benchmark.csv` present
