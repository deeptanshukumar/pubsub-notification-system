#!/usr/bin/env python3
"""
Publisher Client for Pub-Sub System
Publishes messages to topics via the broker.

Improvements over original:
  - Proper message buffering (handles multi-message TCP frames)
  - Responds to PING keepalive from server (sends PONG back)
  - Timestamps shown in CLI output
"""

import socket
import ssl
import sys
import threading
import argparse
import time
from collections import deque
from datetime import datetime
from config import *


class Publisher:
    """Publisher client that sends messages to topics."""

    def __init__(self, host=SERVER_HOST, port=SERVER_PORT):
        self.host = host
        self.port = port
        self.socket = None
        self.connected = False
        self._recv_buffer = ""
        self._lock = threading.Lock()
        self._pending_frames = deque()

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

            # Start background thread to handle PING from server
            ping_thread = threading.Thread(target=self._ping_listener, daemon=True)
            ping_thread.start()

        except ConnectionRefusedError:
            print(f"✗ Error: Could not connect to broker at {self.host}:{self.port}")
            print("  Make sure the server is running!")
            sys.exit(1)
        except Exception as e:
            print(f"✗ Connection error: {e}")
            sys.exit(1)

    def disconnect(self):
        """Disconnect from broker."""
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
    # Keepalive responder
    # -------------------------------------------------------------------------

    def _ping_listener(self):
        """
        Background thread: reads frames from the server.
        If the server sends a PING, reply with PONG immediately.
        """
        while self.connected:
            try:
                data = self.socket.recv(BUFFER_SIZE)
                if not data:
                    self.connected = False
                    break

                with self._lock:
                    self._recv_buffer += data.decode(ENCODING)

                    while MESSAGE_DELIMITER in self._recv_buffer:
                        frame, self._recv_buffer = self._recv_buffer.split(
                            MESSAGE_DELIMITER, 1
                        )
                        frame = frame.strip()
                        if not frame:
                            continue

                        if frame == RESP_PING:
                            try:
                                pong = f"{CMD_PONG}{MESSAGE_DELIMITER}"
                                self.socket.send(pong.encode(ENCODING))
                            except Exception:
                                pass
                        else:
                            # Keep non-PING frames for publish() response handling.
                            self._pending_frames.append(frame)

            except Exception:
                if self.connected:
                    self.connected = False
                break

    # -------------------------------------------------------------------------
    # Publish
    # -------------------------------------------------------------------------

    def publish(self, topic, message):
        """Publish a message to a topic and wait for ACK."""
        if not self.connected:
            print("✗ Not connected to broker")
            return False

        try:
            frame = f"{CMD_PUBLISH}|{topic}|{message}{MESSAGE_DELIMITER}"
            self.socket.send(frame.encode(ENCODING))

            # Read response — may need to pull from buffer first
            response = self._read_next_frame()

            if response.startswith(RESP_ACK):
                parts = response.split('|', 1)
                ack_msg = parts[1] if len(parts) > 1 else "OK"
                ts = datetime.now().strftime('%H:%M:%S')
                print(f"[{ts}] ✓ {ack_msg}")
                return True
            elif response.startswith(RESP_ERROR):
                parts = response.split('|', 1)
                error_msg = parts[1] if len(parts) > 1 else "Unknown error"
                print(f"✗ Error: {error_msg}")
                return False
            else:
                print(f"✗ Unexpected response: {response}")
                return False

        except Exception as e:
            print(f"✗ Error publishing message: {e}")
            self.connected = False
            return False

    def _read_next_frame(self):
        """
        Return the next complete newline-delimited frame.
        Frames are collected by the single background listener thread.
        """
        while True:
            with self._lock:
                if self._pending_frames:
                    return self._pending_frames.popleft()

            if not self.connected:
                raise ConnectionError("Broker closed connection")

            time.sleep(0.01)

    # -------------------------------------------------------------------------
    # Interactive mode
    # -------------------------------------------------------------------------

    def interactive_mode(self):
        """Run publisher in interactive mode."""
        print("=" * 60)
        print("Publisher Client - Interactive Mode")
        print("=" * 60)
        print("Commands:")
        print("  publish <topic> <message>  - Publish a message")
        print("  quit                       - Exit")
        print("=" * 60)
        print()

        self.connect()

        try:
            while True:
                try:
                    user_input = input("publisher> ").strip()
                except EOFError:
                    break

                if not user_input:
                    continue

                parts = user_input.split(' ', 2)
                command = parts[0].lower()

                if command in ('quit', 'exit'):
                    break
                elif command == 'publish':
                    if len(parts) < 3:
                        print("Usage: publish <topic> <message>")
                        continue
                    self.publish(parts[1], parts[2])
                else:
                    print(f"Unknown command: {command}")
                    print("Type 'publish <topic> <message>' or 'quit'")

        except KeyboardInterrupt:
            print("\n✓ Interrupted by user")
        finally:
            self.disconnect()


def main():
    parser = argparse.ArgumentParser(
        description="Publisher client for secure socket-based pub/sub broker"
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
        "topic",
        nargs="?",
        help="Topic name for one-shot publish mode",
    )
    parser.add_argument(
        "message",
        nargs=argparse.REMAINDER,
        help="Message content for one-shot publish mode",
    )
    args = parser.parse_args()

    publisher = Publisher(host=args.host, port=args.port)

    if not args.topic:
        publisher.interactive_mode()
    else:
        message = " ".join(args.message).strip()
        if not message:
            print("Usage:")
            print(f"  {sys.argv[0]} [--host HOST] [--port PORT]                    - Interactive mode")
            print(f"  {sys.argv[0]} [--host HOST] [--port PORT] <topic> <message>  - Publish single message")
            print()
            print("LAN example:")
            print(f"  {sys.argv[0]} --host 192.168.1.50 --port 9999 alerts hello-team")
            sys.exit(1)
        publisher.connect()
        success = publisher.publish(args.topic, message)
        publisher.disconnect()
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
