# Stability, Edge Cases, and Fix Evidence

This document maps robustness requirements to explicit tests and fixes.

## 1) Abrupt Client Disconnect
### What was improved
- Broker-side disconnect cleanup removes dead sockets from subscriptions safely.
- Prevents stale subscribers and bad file descriptor behavior.

### Repro command
1. Start server.
2. Start subscriber and subscribe to a topic.
3. Force-kill subscriber process (`Ctrl+C` or kill PID).
4. Publish to same topic.

Expected:
- Server does not crash.
- Broker logs disconnection and continues serving new requests.

## 2) Invalid Command Handling
### What was improved
- Protocol parser validates command and argument count.
- Sends `ERROR|...` frames for invalid or malformed requests.

### Repro command
Use raw invalid frame (custom socket/netcat script):
```text
INVALID_CMD|abc
```
Expected:
- Client receives `ERROR|Unknown command: INVALID_CMD`
- Broker remains stable for other clients.

## 3) Missing Certificate / TLS Startup Failure
### What was improved
- Startup validates cert/key presence before binding secure server behavior.
- Explicit operator guidance is printed.

### Repro command
```bash
mv certs certs_backup
python server.py
```
Expected:
- Startup fails with clear message to run `./generate_certs.sh`.

Recovery:
```bash
mv certs_backup certs
```

## 4) Keepalive Timeout and Dead Socket Cleanup
### What was improved
- Background keepalive loop sends periodic `PING`.
- Clients that do not return `PONG` are disconnected after timeout threshold.

### Repro command
- Connect a custom client that ignores `PING`.
- Wait beyond `PING_INTERVAL + PING_TIMEOUT`.

Expected:
- Broker logs stale client removal.
- No global service interruption.

## 5) Dashboard Topic Create + Publish Selection Race
### What was improved
- Added backend endpoint `POST /api/topic` for topic creation without seed publish.
- Frontend preserves selected topic in publish dropdown during periodic refresh.

### Repro command
1. Open dashboard (`http://127.0.0.1:8088`).
2. Create topic from left panel.
3. Select topic in publish dropdown.
4. Wait for multiple snapshot refresh cycles.

Expected:
- Selection does not reset unexpectedly.
- Publishing uses selected topic correctly.

## 6) Partial Failure During Broadcast
### What was improved
- Publish path tracks failed subscriber sends and prunes dead sockets.
- Delivery failure count is captured in broker metrics.

### Repro command
- Start multiple subscribers.
- Kill one subscriber during high-volume publish.

Expected:
- Remaining subscribers still receive messages.
- Broker metrics increase failed delivery count without crashing.

## 7) Timeout and Connection Reset Handling
### What was improved
- Explicit handling for socket timeout and reset exceptions in client thread loop.
- Event log captures incidents for observability.

### Repro command
- Connect/disconnect clients rapidly.
- Pause clients to trigger timeout.

Expected:
- Broker remains responsive.
- Errors are recorded without terminating server.

## 8) Inter-Laptop Disconnect Case (Verified Scenario)
### What was improved
- LAN-mode server binding (`--host 0.0.0.0`) and remote client flags (`--host`, `--port`) added.
- Disconnect path verified for remote subscriber drop during active publishing.

### Repro command
1. Laptop A: run server in LAN mode.
2. Laptop B: run subscriber and subscribe to topic.
3. Laptop C: continuously publish to same topic.
4. Disconnect Laptop B from Wi-Fi (or close subscriber abruptly).

Expected:
- Laptop A broker remains stable.
- Laptop C can continue publishing and receiving ACKs.
- Broker logs remote client disconnect and continues serving others.

## Suggested Evidence to Attach in Submission
- Relevant `logs/server.log` excerpts.
- Short terminal screenshots for each repro case.
- Benchmark CSV/report (`results/*.csv`, `docs/performance.md`).
