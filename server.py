#!/usr/bin/env python3
"""
Publish-Subscribe Broker Server
Handles multiple concurrent publishers and subscribers with SSL/TLS encryption.

Project upgrades:
  - Live dashboard API + static dashboard serving
  - EWMA-based hot-topic ranking for burst detection and observability
  - Structured in-memory event log for real-time monitoring
  - Message history replay + keepalive + robust socket cleanup
"""

import json
import logging
import os
import socket
import ssl
import sys
import threading
import time
import argparse
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from config import *


class PubSubBroker:
    """Central broker for pub-sub messaging system."""

    def __init__(
        self,
        host=SERVER_HOST,
        port=SERVER_PORT,
        dashboard_host=DASHBOARD_HOST,
        dashboard_port=DASHBOARD_PORT,
        dashboard_enabled=DASHBOARD_ENABLED,
    ):
        self.host = host
        self.port = port
        self.dashboard_host = dashboard_host
        self.dashboard_port = dashboard_port
        self.dashboard_enabled = dashboard_enabled

        self.lock = threading.Lock()

        # Topic management: {topic_name: set(subscriber_sockets)}
        self.topics = {}

        # Message history: {topic_name: deque[(timestamp, publisher_addr, content)]}
        self.history = {}

        # Client management: {socket: info}
        self.clients = {}

        # Topic metrics: {topic_name: {published, delivered, ewma, last_publish_at, subscribers}}
        self.topic_stats = {}

        # Broker-level metrics
        self.total_published = 0
        self.total_delivered = 0
        self.total_failed_deliveries = 0
        self.publish_timestamps = deque()  # Unix timestamps, pruned to last 60s

        # Event log for monitoring API
        self.event_seq = 0
        self.event_log = deque(maxlen=BROKER_EVENT_BUFFER_SIZE)

        self.active_clients = 0
        self.started_at = time.time()

        self.server_socket = None
        self.ssl_context = None
        self.dashboard_server = None
        self.dashboard_thread = None

        self.setup_logging()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def setup_logging(self):
        """Configure logging system."""
        os.makedirs(LOG_DIR, exist_ok=True)

        logging.basicConfig(
            level=getattr(logging, LOG_LEVEL),
            format='%(asctime)s - %(levelname)s - [%(threadName)s] - %(message)s',
            handlers=[
                logging.FileHandler(LOG_FILE),
                logging.StreamHandler(sys.stdout),
            ],
        )
        self.logger = logging.getLogger(__name__)

    def setup_ssl_context(self):
        """Configure SSL/TLS context."""
        if not SSL_ENABLED:
            return None

        if not os.path.exists(SERVER_CERT) or not os.path.exists(SERVER_KEY):
            self.logger.error("SSL certificates not found!")
            self.logger.error("Run './generate_certs.sh' to create certificates")
            sys.exit(1)

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(SERVER_CERT, SERVER_KEY)
        context.minimum_version = ssl.TLSVersion.TLSv1_2

        self.logger.info("✓ SSL/TLS context configured")
        return context

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Start broker TCP server + optional dashboard server."""
        self.logger.info("=" * 60)
        self.logger.info("Publish-Subscribe Broker Server Starting...")
        self.logger.info("=" * 60)

        self.ssl_context = self.setup_ssl_context()

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(MAX_CLIENTS)

        self.logger.info("✓ Broker bound to %s:%s", self.host, self.port)
        self.logger.info("✓ SSL/TLS: %s", "Enabled" if SSL_ENABLED else "Disabled")
        self.logger.info("✓ Maximum clients: %s", MAX_CLIENTS)
        self.logger.info("✓ Message history per topic: %s", MESSAGE_HISTORY_SIZE)
        self.logger.info("✓ Keepalive interval: %ss", PING_INTERVAL)

        self.record_event("SYS", "Broker started", meta={"host": self.host, "port": self.port})

        if self.dashboard_enabled:
            self.start_dashboard_server()
        else:
            self.logger.info("✓ Dashboard/API: Disabled by runtime option")

        ping_thread = threading.Thread(target=self.keepalive_loop, daemon=True, name="keepalive")
        ping_thread.start()

        try:
            while True:
                client_socket, address = self.server_socket.accept()

                if SSL_ENABLED:
                    try:
                        client_socket = self.ssl_context.wrap_socket(client_socket, server_side=True)
                    except ssl.SSLError as exc:
                        self.logger.error("SSL handshake failed with %s: %s", address, exc)
                        client_socket.close()
                        continue

                with self.lock:
                    if self.active_clients >= MAX_CLIENTS:
                        reject = True
                    else:
                        self.active_clients += 1
                        reject = False

                if reject:
                    self.logger.warning("Max clients reached. Rejecting %s", address)
                    client_socket.close()
                    continue

                self.logger.info("✓ New connection from %s (Active: %s)", address, self.active_clients)
                self.record_event("SYS", f"Client connected: {address[0]}:{address[1]}")

                client_thread = threading.Thread(
                    target=self.handle_client,
                    args=(client_socket, address),
                    daemon=True,
                    name=f"client-{address[0]}:{address[1]}",
                )
                client_thread.start()

        except KeyboardInterrupt:
            self.logger.info("\n✓ Server shutdown initiated by user")
        except Exception as exc:
            self.logger.error("Server error: %s", exc)
        finally:
            self.shutdown()

    def shutdown(self):
        """Gracefully shutdown broker and dashboard server."""
        self.logger.info("Shutting down server...")

        with self.lock:
            sockets = list(self.clients.keys())

        for client_socket in sockets:
            try:
                client_socket.close()
            except Exception:
                pass

        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass

        if self.dashboard_server:
            try:
                self.dashboard_server.shutdown()
                self.dashboard_server.server_close()
            except Exception:
                pass

        self.record_event("SYS", "Broker shutdown")
        self.logger.info("✓ Server shutdown complete")

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def record_event(self, event_type, message, topic=None, severity="info", meta=None):
        """Append a structured event in an in-memory ring buffer."""
        with self.lock:
            self.event_seq += 1
            event = {
                "id": self.event_seq,
                "time": datetime.now().strftime("%H:%M:%S"),
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "type": event_type,
                "topic": topic,
                "severity": severity,
                "message": message,
                "meta": meta or {},
            }
            self.event_log.append(event)

    def _prune_publish_timestamps(self, now_ts):
        cutoff = now_ts - 60
        while self.publish_timestamps and self.publish_timestamps[0] < cutoff:
            self.publish_timestamps.popleft()

    def _update_topic_subscriber_count(self, topic):
        if topic not in self.topic_stats:
            self.topic_stats[topic] = {
                "published": 0,
                "delivered": 0,
                "ewma": 0.0,
                "last_publish_at": None,
                "subscribers": 0,
            }
        self.topic_stats[topic]["subscribers"] = len(self.topics.get(topic, set()))

    def _topic_score(self, stat):
        # Novel scoring: recent publish intensity (EWMA rate) x fanout factor.
        return round(stat["ewma"] * (1 + stat["subscribers"]), 4)

    def get_snapshot(self):
        """Return a JSON-serializable snapshot for dashboard/API consumers."""
        now_ts = time.time()
        with self.lock:
            self._prune_publish_timestamps(now_ts)

            topic_rows = []
            for name in sorted(self.topic_stats.keys()):
                stat = self.topic_stats[name]
                row = {
                    "name": name,
                    "subs": stat["subscribers"],
                    "published": stat["published"],
                    "delivered": stat["delivered"],
                    "ewma": round(stat["ewma"], 4),
                    "score": self._topic_score(stat),
                }
                topic_rows.append(row)

            hot_topics = sorted(topic_rows, key=lambda x: x["score"], reverse=True)[:HOT_TOPIC_COUNT]

            client_rows = []
            for info in self.clients.values():
                addr = info["address"]
                client_rows.append(
                    {
                        "addr": f"{addr[0]}:{addr[1]}",
                        "type": info["type"],
                        "subscriptions": sorted(info["subscriptions"]),
                        "connected_at": info["connected_at"].isoformat(timespec="seconds"),
                    }
                )

            logs = list(self.event_log)[-200:]

            snapshot = {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "uptime_seconds": int(now_ts - self.started_at),
                "broker": {
                    "host": self.host,
                    "port": self.port,
                    "ssl_enabled": SSL_ENABLED,
                    "active_clients": self.active_clients,
                    "messages_last_minute": len(self.publish_timestamps),
                    "total_published": self.total_published,
                    "total_delivered": self.total_delivered,
                    "failed_deliveries": self.total_failed_deliveries,
                },
                "topics": topic_rows,
                "hot_topics": hot_topics,
                "clients": sorted(client_rows, key=lambda c: c["addr"]),
                "events": logs,
            }

        return snapshot

    # ------------------------------------------------------------------
    # Dashboard HTTP server
    # ------------------------------------------------------------------

    def start_dashboard_server(self):
        """Start built-in HTTP server for dashboard + monitoring API."""
        dashboard_path = os.path.join(os.getcwd(), DASHBOARD_FILE)

        if not os.path.exists(dashboard_path):
            self.logger.warning("Dashboard file not found at %s. API will still run.", dashboard_path)

        broker = self

        class DashboardHandler(BaseHTTPRequestHandler):
            def _write_json(self, payload, status=200):
                body = json.dumps(payload).encode(ENCODING)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()
                self.wfile.write(body)

            def _write_html(self, html_bytes, status=200):
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html_bytes)))
                self.end_headers()
                self.wfile.write(html_bytes)

            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

            def do_GET(self):
                if self.path in ("/", "/dashboard", "/dashboard.html"):
                    if os.path.exists(dashboard_path):
                        with open(dashboard_path, "rb") as f:
                            self._write_html(f.read(), status=200)
                    else:
                        self._write_html(b"<h1>dashboard.html not found</h1>", status=404)
                    return

                if self.path == "/api/health":
                    self._write_json(
                        {
                            "status": "online",
                            "broker": f"{broker.host}:{broker.port}",
                            "dashboard": f"{broker.dashboard_host}:{broker.dashboard_port}",
                            "timestamp": datetime.now().isoformat(timespec="seconds"),
                        }
                    )
                    return

                if self.path == "/api/snapshot":
                    self._write_json(broker.get_snapshot())
                    return

                self._write_json({"error": "Not found"}, status=404)

            def do_POST(self):
                if self.path not in ("/api/publish", "/api/topic"):
                    self._write_json({"error": "Not found"}, status=404)
                    return

                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                try:
                    data = json.loads(raw.decode(ENCODING))
                except Exception:
                    self._write_json({"error": "Invalid JSON payload"}, status=400)
                    return

                topic = (data.get("topic") or "").strip()

                if self.path == "/api/topic":
                    if not topic:
                        self._write_json({"error": "Field 'topic' is required"}, status=400)
                        return
                    result = broker.create_topic(topic)
                    self._write_json(result, status=201 if result.get("created") else 200)
                    return

                message = (data.get("message") or "").strip()
                if not topic or not message:
                    self._write_json({"error": "Both 'topic' and 'message' are required"}, status=400)
                    return

                result = broker.publish_from_dashboard(topic, message)
                self._write_json(result)

            def log_message(self, fmt, *args):
                broker.logger.debug("Dashboard HTTP: " + fmt, *args)

        self.dashboard_server = ThreadingHTTPServer((self.dashboard_host, self.dashboard_port), DashboardHandler)
        self.dashboard_thread = threading.Thread(
            target=self.dashboard_server.serve_forever,
            daemon=True,
            name="dashboard-http",
        )
        self.dashboard_thread.start()

        self.logger.info("✓ Dashboard/API available at http://%s:%s", self.dashboard_host, self.dashboard_port)
        self.record_event(
            "SYS",
            "Dashboard HTTP server started",
            meta={"host": self.dashboard_host, "port": self.dashboard_port},
        )

    # ------------------------------------------------------------------
    # Client handling
    # ------------------------------------------------------------------

    def handle_client(self, client_socket, address):
        """Handle an individual client connection."""
        client_socket.settimeout(SOCKET_TIMEOUT)

        with self.lock:
            self.clients[client_socket] = {
                "type": CLIENT_TYPE_UNDEFINED,
                "address": address,
                "subscriptions": set(),
                "connected_at": datetime.now(),
                "last_pong": time.time(),
            }

        try:
            buffer = ""
            keep_running = True
            while keep_running:
                data = client_socket.recv(BUFFER_SIZE)
                if not data:
                    break

                buffer += data.decode(ENCODING)

                while MESSAGE_DELIMITER in buffer:
                    raw, buffer = buffer.split(MESSAGE_DELIMITER, 1)
                    raw = raw.strip()
                    if not raw:
                        continue
                    self.logger.debug("Received from %s: %s", address, raw)
                    keep_running = self.process_command(client_socket, raw)
                    if not keep_running:
                        break

        except socket.timeout:
            self.logger.warning("Client %s timed out", address)
            self.record_event("ERROR", f"Client timeout: {address[0]}:{address[1]}", severity="warning")
        except ConnectionResetError:
            self.logger.warning("Client %s connection reset", address)
            self.record_event("ERROR", f"Connection reset: {address[0]}:{address[1]}", severity="warning")
        except Exception as exc:
            self.logger.error("Error handling client %s: %s", address, exc)
            self.record_event("ERROR", f"Client handler error: {exc}", severity="error")
        finally:
            self.disconnect_client(client_socket)

    def process_command(self, client_socket, message):
        """Parse and execute a client command. Returns False to close connection."""
        parts = message.split("|")
        if not parts:
            self.send_error(client_socket, "Empty command")
            return True

        command = parts[0].upper()

        try:
            if command == CMD_PUBLISH:
                if len(parts) < 3:
                    self.send_error(client_socket, "PUBLISH requires topic and message")
                    return True
                topic = parts[1].strip()
                msg_content = "|".join(parts[2:]).strip()
                self.handle_publish(client_socket, topic, msg_content)

            elif command == CMD_SUBSCRIBE:
                if len(parts) < 2:
                    self.send_error(client_socket, "SUBSCRIBE requires topic")
                    return True
                self.handle_subscribe(client_socket, parts[1].strip())

            elif command == CMD_UNSUBSCRIBE:
                if len(parts) < 2:
                    self.send_error(client_socket, "UNSUBSCRIBE requires topic")
                    return True
                self.handle_unsubscribe(client_socket, parts[1].strip())

            elif command == CMD_LIST_TOPICS:
                self.handle_list_topics(client_socket)

            elif command == CMD_STATS:
                self.handle_stats(client_socket)

            elif command == CMD_LIST_CLIENTS:
                self.handle_list_clients(client_socket)

            elif command == CMD_PONG:
                with self.lock:
                    if client_socket in self.clients:
                        self.clients[client_socket]["last_pong"] = time.time()

            elif command == CMD_DISCONNECT:
                self.send_ack(client_socket, "Goodbye")
                return False

            else:
                self.send_error(client_socket, f"Unknown command: {command}")

        except Exception as exc:
            self.logger.error("Error processing command '%s': %s", command, exc)
            self.send_error(client_socket, "Internal server error")

        return True

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def _ensure_topic_structures(self, topic):
        if topic not in self.topics:
            self.topics[topic] = set()
            self.logger.info("✓ New topic created: '%s'", topic)

        if topic not in self.history:
            self.history[topic] = deque(maxlen=MESSAGE_HISTORY_SIZE)

        if topic not in self.topic_stats:
            self.topic_stats[topic] = {
                "published": 0,
                "delivered": 0,
                "ewma": 0.0,
                "last_publish_at": None,
                "subscribers": 0,
            }

    def _publish_message(self, topic, message, publisher_addr):
        """Shared publish path used by socket clients and dashboard API."""
        now_ts = time.time()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self.lock:
            self._ensure_topic_structures(topic)

            self.history[topic].append((timestamp, publisher_addr, message))

            topic_stat = self.topic_stats[topic]
            topic_stat["published"] += 1

            last_at = topic_stat["last_publish_at"]
            if last_at is None:
                topic_stat["ewma"] = 1.0
            else:
                dt = max(now_ts - last_at, 0.001)
                instant_rate = 1.0 / dt
                topic_stat["ewma"] = (1 - EWMA_DECAY) * topic_stat["ewma"] + EWMA_DECAY * instant_rate
            topic_stat["last_publish_at"] = now_ts

            subscribers = list(self.topics.get(topic, set()))
            topic_stat["subscribers"] = len(subscribers)

            self.total_published += 1
            self.publish_timestamps.append(now_ts)
            self._prune_publish_timestamps(now_ts)

        delivered = 0
        failed_sockets = []

        for sub_socket in subscribers:
            try:
                frame = (
                    f"{RESP_MESSAGE}|{topic}|{publisher_addr}|{timestamp}|{message}"
                    f"{MESSAGE_DELIMITER}"
                )
                sub_socket.send(frame.encode(ENCODING))
                delivered += 1
            except Exception:
                failed_sockets.append(sub_socket)

        with self.lock:
            self.total_delivered += delivered
            self.total_failed_deliveries += len(failed_sockets)
            self.topic_stats[topic]["delivered"] += delivered

            if failed_sockets:
                for dead in failed_sockets:
                    if topic in self.topics:
                        self.topics[topic].discard(dead)
                self._update_topic_subscriber_count(topic)

        self.record_event(
            "PUBLISH",
            f"{publisher_addr} -> {topic} (delivered={delivered}, failed={len(failed_sockets)})",
            topic=topic,
            meta={"message": message},
        )

        return {
            "topic": topic,
            "publisher": publisher_addr,
            "timestamp": timestamp,
            "delivered": delivered,
            "failed": len(failed_sockets),
        }

    def handle_publish(self, client_socket, topic, message):
        """Route a published message to all subscribers of a topic."""
        with self.lock:
            if client_socket not in self.clients:
                return
            address = self.clients[client_socket]["address"]
            if self.clients[client_socket]["type"] == CLIENT_TYPE_UNDEFINED:
                self.clients[client_socket]["type"] = CLIENT_TYPE_PUBLISHER

        publisher_addr = f"{address[0]}:{address[1]}"
        result = self._publish_message(topic, message, publisher_addr)

        self.logger.info(
            "PUBLISH from %s to '%s': delivered=%s failed=%s",
            publisher_addr,
            topic,
            result["delivered"],
            result["failed"],
        )
        self.send_ack(
            client_socket,
            f"Published to {result['delivered']} subscriber(s)"
            + (f" ({result['failed']} failed)" if result["failed"] else ""),
        )

    def publish_from_dashboard(self, topic, message):
        """Publish message via HTTP dashboard API."""
        result = self._publish_message(topic, message, "dashboard@web")
        result["status"] = "ok"
        return result

    def create_topic(self, topic, source="dashboard@web"):
        """Create a topic without publishing a seed message."""
        if not topic:
            return {"status": "error", "error": "Topic name is required"}

        with self.lock:
            exists = topic in self.topics
            self._ensure_topic_structures(topic)
            self._update_topic_subscriber_count(topic)

        if not exists:
            self.record_event("SYS", f"{source} created topic '{topic}'", topic=topic)

        return {
            "status": "ok",
            "topic": topic,
            "created": not exists,
        }

    def handle_subscribe(self, client_socket, topic):
        """Subscribe a client to a topic and replay message history."""
        with self.lock:
            if client_socket not in self.clients:
                return

            info = self.clients[client_socket]
            address = info["address"]

            if info["type"] == CLIENT_TYPE_UNDEFINED:
                info["type"] = CLIENT_TYPE_SUBSCRIBER

            self._ensure_topic_structures(topic)
            already = client_socket in self.topics[topic]

            if not already:
                self.topics[topic].add(client_socket)
                info["subscriptions"].add(topic)
                self._update_topic_subscriber_count(topic)

            history_snapshot = list(self.history.get(topic, []))

        if already:
            self.send_ack(client_socket, f"Already subscribed to: {topic}")
            return

        self.logger.info("✓ %s subscribed to '%s'", address, topic)
        self.record_event("SUBSCRIBE", f"{address[0]}:{address[1]} subscribed", topic=topic)

        self.send_ack(client_socket, f"Subscribed to topic: {topic}")

        for ts, pub_addr, content in history_snapshot:
            try:
                frame = (
                    f"{RESP_MESSAGE}|{topic}|{pub_addr}|{ts}|[HISTORY] {content}"
                    f"{MESSAGE_DELIMITER}"
                )
                client_socket.send(frame.encode(ENCODING))
            except Exception:
                break

    def handle_unsubscribe(self, client_socket, topic):
        """Unsubscribe a client from a topic."""
        with self.lock:
            if client_socket not in self.clients:
                return

            info = self.clients[client_socket]
            address = info["address"]

            if topic in self.topics and client_socket in self.topics[topic]:
                self.topics[topic].remove(client_socket)
                info["subscriptions"].discard(topic)

                if not self.topics[topic]:
                    del self.topics[topic]

                self._update_topic_subscriber_count(topic)

                self.logger.info("✓ %s unsubscribed from '%s'", address, topic)
                self.record_event("UNSUB", f"{address[0]}:{address[1]} unsubscribed", topic=topic)
                self.send_ack(client_socket, f"Unsubscribed from topic: {topic}")
            else:
                self.send_error(client_socket, f"Not subscribed to topic: {topic}")

    def handle_list_topics(self, client_socket):
        """Return active topic names."""
        with self.lock:
            topic_list = sorted(self.topics.keys())

        topics_str = ",".join(topic_list)
        frame = f"{RESP_TOPICS}|{topics_str}{MESSAGE_DELIMITER}"
        try:
            client_socket.send(frame.encode(ENCODING))
        except Exception as exc:
            self.logger.error("Error sending topic list: %s", exc)

    def handle_stats(self, client_socket):
        """Return compact stats snapshot via TCP protocol."""
        try:
            payload = json.dumps(self.get_snapshot()["broker"], separators=(",", ":"))
            frame = f"{RESP_STATS}|{payload}{MESSAGE_DELIMITER}"
            client_socket.send(frame.encode(ENCODING))
        except Exception as exc:
            self.logger.error("Error sending stats: %s", exc)

    def handle_list_clients(self, client_socket):
        """Return currently connected clients via TCP protocol."""
        with self.lock:
            rows = []
            for info in self.clients.values():
                addr = info["address"]
                rows.append(f"{addr[0]}:{addr[1]}:{info['type']}")

        frame = f"{RESP_CLIENTS}|{','.join(rows)}{MESSAGE_DELIMITER}"
        try:
            client_socket.send(frame.encode(ENCODING))
        except Exception as exc:
            self.logger.error("Error sending clients list: %s", exc)

    # ------------------------------------------------------------------
    # Keepalive
    # ------------------------------------------------------------------

    def keepalive_loop(self):
        """Periodic keepalive: send PING, drop stale sockets."""
        self.logger.info("✓ Keepalive loop started")
        while True:
            time.sleep(PING_INTERVAL)
            now = time.time()
            dead_clients = []

            with self.lock:
                snapshot = list(self.clients.items())

            for sock, info in snapshot:
                last_pong = info.get("last_pong", now)
                if now - last_pong > PING_INTERVAL + PING_TIMEOUT:
                    dead_clients.append(sock)
                    continue

                try:
                    sock.send(f"{RESP_PING}{MESSAGE_DELIMITER}".encode(ENCODING))
                except Exception:
                    dead_clients.append(sock)

            for sock in dead_clients:
                self.disconnect_client(sock)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def disconnect_client(self, client_socket):
        """Safely disconnect and cleanup client structures."""
        with self.lock:
            if client_socket not in self.clients:
                return

            info = self.clients[client_socket]
            address = info["address"]
            subscriptions = list(info["subscriptions"])

            for topic in subscriptions:
                if topic in self.topics:
                    self.topics[topic].discard(client_socket)
                    if not self.topics[topic]:
                        del self.topics[topic]
                    self._update_topic_subscriber_count(topic)

            del self.clients[client_socket]
            self.active_clients -= 1

        self.logger.info("✓ Client %s disconnected (Active: %s)", address, self.active_clients)
        self.record_event("SYS", f"Client disconnected: {address[0]}:{address[1]}")

        try:
            client_socket.close()
        except Exception:
            pass

    def send_ack(self, client_socket, message="OK"):
        try:
            client_socket.send(f"{RESP_ACK}|{message}{MESSAGE_DELIMITER}".encode(ENCODING))
        except Exception as exc:
            self.logger.error("Error sending ACK: %s", exc)

    def send_error(self, client_socket, message):
        try:
            client_socket.send(f"{RESP_ERROR}|{message}{MESSAGE_DELIMITER}".encode(ENCODING))
            self.record_event("ERROR", message, severity="warning")
        except Exception as exc:
            self.logger.error("Error sending error message: %s", exc)


def main():
    parser = argparse.ArgumentParser(
        description="Secure socket-based publish-subscribe broker server"
    )
    parser.add_argument(
        "--host",
        default=SERVER_HOST,
        help="Broker bind host (use 0.0.0.0 for LAN demo access)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=SERVER_PORT,
        help="Broker bind TCP port",
    )
    parser.add_argument(
        "--dashboard-host",
        default=DASHBOARD_HOST,
        help="Dashboard/API bind host (use 0.0.0.0 for LAN access)",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=DASHBOARD_PORT,
        help="Dashboard/API bind HTTP port",
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Disable built-in dashboard/API server",
    )

    args = parser.parse_args()

    broker = PubSubBroker(
        host=args.host,
        port=args.port,
        dashboard_host=args.dashboard_host,
        dashboard_port=args.dashboard_port,
        dashboard_enabled=(not args.no_dashboard),
    )
    broker.start()


if __name__ == "__main__":
    main()
