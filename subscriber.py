#!/usr/bin/env python3
"""
Subscriber Client for Pub-Sub System
Subscribes to topics and receives real-time messages.

Improvements over original:
  - Proper TCP buffering — no more dropped/merged messages
  - Displays timestamp on every received message
  - Handles [HISTORY] replay messages from broker with a distinct label
  - Responds to PING keepalive from server (sends PONG back)
"""

import socket
import ssl
import sys
import threading
import argparse
from config import *


class Subscriber:
    """Subscriber client that receives messages from topics."""

    def __init__(self, host=SERVER_HOST, port=SERVER_PORT):
        self.host = host
        self.port = port
        self.socket = None
        self.connected = False
        self.running = False
        self.subscribed_topics = set()

    # -------------------------------------------------------------------------
    # Connection
    # -------------------------------------------------------------------------

    def connect(self):
        """Establish SSL connection to broker."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

            if SSL_ENABLED:
                context = ssl.create_default_context()
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                self.socket = context.wrap_socket(sock, server_hostname=self.host)
            else:
                self.socket = sock

            self.socket.connect((self.host, self.port))
            self.connected = True

            print(f"✓ Connected to broker at {self.host}:{self.port}")
            print(f"✓ SSL/TLS: {'Enabled' if SSL_ENABLED else 'Disabled'}")
            print()

        except ConnectionRefusedError:
            print(f"✗ Error: Could not connect to broker at {self.host}:{self.port}")
            print("  Make sure the server is running!")
            sys.exit(1)
        except Exception as e:
            print(f"✗ Connection error: {e}")
            sys.exit(1)

    def disconnect(self):
        """Disconnect from broker."""
        self.running = False
        if self.socket and self.connected:
            try:
                frame = f"{CMD_DISCONNECT}{MESSAGE_DELIMITER}"
                self.socket.send(frame.encode(ENCODING))
                self.socket.close()
                print("\n✓ Disconnected from broker")
            except Exception:
                pass
        self.connected = False

    # -------------------------------------------------------------------------
    # Subscribe / Unsubscribe / List
    # -------------------------------------------------------------------------

    def subscribe(self, topic):
        """Subscribe to a topic (broker will replay history automatically)."""
        if not self.connected:
            print("✗ Not connected to broker")
            return False
        try:
            frame = f"{CMD_SUBSCRIBE}|{topic}{MESSAGE_DELIMITER}"
            self.socket.send(frame.encode(ENCODING))
            # The ACK will arrive via the listener thread; just track locally
            self.subscribed_topics.add(topic)
            print(f"✓ Subscribed to topic: {topic}")
            return True
        except Exception as e:
            print(f"✗ Error subscribing to topic: {e}")
            self.connected = False
            return False

    def unsubscribe(self, topic):
        """Unsubscribe from a topic."""
        if not self.connected:
            print("✗ Not connected to broker")
            return False
        try:
            frame = f"{CMD_UNSUBSCRIBE}|{topic}{MESSAGE_DELIMITER}"
            self.socket.send(frame.encode(ENCODING))
            self.subscribed_topics.discard(topic)
            print(f"✓ Unsubscribed from topic: {topic}")
            return True
        except Exception as e:
            print(f"✗ Error unsubscribing: {e}")
            self.connected = False
            return False

    def list_topics(self):
        """Request the list of active topics from the broker."""
        if not self.connected:
            print("✗ Not connected to broker")
            return []
        try:
            frame = f"{CMD_LIST_TOPICS}{MESSAGE_DELIMITER}"
            self.socket.send(frame.encode(ENCODING))
            return []  # Response arrives via listener thread
        except Exception as e:
            print(f"✗ Error requesting topics: {e}")
            self.connected = False
            return []

    # -------------------------------------------------------------------------
    # Message listener (runs in background thread)
    # -------------------------------------------------------------------------

    def listen_for_messages(self):
        """
        Reads all incoming frames from the broker.
        - PING  → reply PONG immediately
        - MESSAGE → print with timestamp (marks history replays)
        - TOPICS  → print topic list
        - ACK / ERROR → print status
        """
        print("✓ Listening for messages... (Press Ctrl+C to stop)")
        print("=" * 60)
        print()

        buffer = ""

        try:
            while self.running and self.connected:
                data = self.socket.recv(BUFFER_SIZE)
                if not data:
                    print("\n✗ Connection closed by broker")
                    self.connected = False
                    break

                buffer += data.decode(ENCODING)

                # Process every complete newline-delimited frame
                while MESSAGE_DELIMITER in buffer:
                    frame, buffer = buffer.split(MESSAGE_DELIMITER, 1)
                    frame = frame.strip()
                    if not frame:
                        continue

                    # --- PING keepalive ---
                    if frame == RESP_PING:
                        try:
                            self.socket.send(
                                f"{CMD_PONG}{MESSAGE_DELIMITER}".encode(ENCODING)
                            )
                        except Exception:
                            pass

                    # --- Delivered message ---
                    # Format: MESSAGE|topic|publisher_addr|timestamp|content
                    elif frame.startswith(RESP_MESSAGE):
                        parts = frame.split('|', 4)
                        if len(parts) >= 5:
                            topic     = parts[1]
                            sender    = parts[2]
                            timestamp = parts[3]
                            content   = parts[4]

                            is_history = content.startswith("[HISTORY]")
                            label = "📜 HISTORY" if is_history else "📨 MESSAGE"
                            clean_content = (
                                content[len("[HISTORY] "):] if is_history else content
                            )

                            print(f"{label}  [{timestamp}]  topic={topic}  from={sender}")
                            print(f"  {clean_content}")
                            print()

                    # --- Topic list response ---
                    elif frame.startswith(RESP_TOPICS):
                        parts = frame.split('|', 1)
                        raw = parts[1] if len(parts) > 1 else ''
                        topics = [t for t in raw.split(',') if t]
                        if topics:
                            print(f"Available topics ({len(topics)}):")
                            for t in topics:
                                print(f"  - {t}")
                        else:
                            print("No active topics on broker")
                        print()

                    # --- ACK ---
                    elif frame.startswith(RESP_ACK):
                        parts = frame.split('|', 1)
                        msg = parts[1] if len(parts) > 1 else "OK"
                        print(f"✓ {msg}")

                    # --- Error ---
                    elif frame.startswith(RESP_ERROR):
                        parts = frame.split('|', 1)
                        msg = parts[1] if len(parts) > 1 else "Unknown error"
                        print(f"✗ Broker error: {msg}")
                        print()

        except Exception as e:
            if self.running:
                print(f"\n✗ Error receiving messages: {e}")
                self.connected = False

    # -------------------------------------------------------------------------
    # Interactive mode
    # -------------------------------------------------------------------------

    def interactive_mode(self):
        """Run subscriber in interactive mode."""
        print("=" * 60)
        print("Subscriber Client - Interactive Mode")
        print("=" * 60)
        print("Commands:")
        print("  subscribe <topic>    - Subscribe to a topic")
        print("  unsubscribe <topic>  - Unsubscribe from a topic")
        print("  list                 - List available topics")
        print("  topics               - Show your subscribed topics")
        print("  quit                 - Exit")
        print("=" * 60)
        print()

        self.connect()

        self.running = True
        listener_thread = threading.Thread(
            target=self.listen_for_messages, daemon=True
        )
        listener_thread.start()

        try:
            while True:
                try:
                    user_input = input("subscriber> ").strip()
                except EOFError:
                    break

                if not user_input:
                    continue

                parts = user_input.split(' ', 1)
                command = parts[0].lower()

                if command in ('quit', 'exit'):
                    break
                elif command == 'subscribe':
                    if len(parts) < 2:
                        print("Usage: subscribe <topic>")
                    else:
                        self.subscribe(parts[1])
                elif command == 'unsubscribe':
                    if len(parts) < 2:
                        print("Usage: unsubscribe <topic>")
                    else:
                        self.unsubscribe(parts[1])
                elif command == 'list':
                    self.list_topics()
                elif command == 'topics':
                    if self.subscribed_topics:
                        print(f"Subscribed topics ({len(self.subscribed_topics)}):")
                        for t in self.subscribed_topics:
                            print(f"  - {t}")
                    else:
                        print("Not subscribed to any topics")
                    print()
                else:
                    print(f"Unknown command: {command}")
                    print("Type 'subscribe <topic>', 'list', or 'quit'")

        except KeyboardInterrupt:
            print("\n✓ Interrupted by user")
        finally:
            self.disconnect()


def main():
    parser = argparse.ArgumentParser(
        description="Subscriber client for secure socket-based pub/sub broker"
    )
    parser.add_argument(
        "--host",
        default=SERVER_HOST,
        help="Broker host or IP (example: 192.168.1.50)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=SERVER_PORT,
        help="Broker TCP port",
    )
    parser.add_argument(
        "topics",
        nargs="*",
        help="Optional list of topics for direct listen mode",
    )
    args = parser.parse_args()

    subscriber = Subscriber(host=args.host, port=args.port)

    if not args.topics:
        subscriber.interactive_mode()
    else:
        topics = args.topics
        subscriber.connect()
        print(f"Subscribing to {len(topics)} topic(s)...")
        for topic in topics:
            subscriber.subscribe(topic)
        print()
        subscriber.running = True
        try:
            subscriber.listen_for_messages()
        except KeyboardInterrupt:
            print("\n✓ Interrupted by user")
        finally:
            subscriber.disconnect()

if __name__ == "__main__":
    main()
