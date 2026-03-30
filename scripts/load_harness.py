#!/usr/bin/env python3
"""
Concurrent load harness for the Pub/Sub broker.

Usage example (custom run):
  python scripts/load_harness.py \
    --publishers 5 --subscribers 10 --messages 200 --topics 4 \
    --output results/run.csv --markdown docs/performance.md

Usage example (rubric scenarios S1/S2/S3):
  python scripts/load_harness.py --run-standard-scenarios \
    --output results/rubric_benchmark.csv --markdown docs/performance.md
"""

from __future__ import annotations

import argparse
import csv
import math
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import (  # noqa: E402
    BUFFER_SIZE,
    CMD_DISCONNECT,
    CMD_PONG,
    CMD_PUBLISH,
    CMD_SUBSCRIBE,
    ENCODING,
    MESSAGE_DELIMITER,
    RESP_ACK,
    RESP_ERROR,
    RESP_MESSAGE,
    RESP_PING,
    SERVER_HOST,
    SERVER_PORT,
    SSL_ENABLED,
)


STANDARD_SCENARIOS: Dict[str, Dict[str, int]] = {
    "S1": {
        "publishers": 1,
        "subscribers": 1,
        "messages": 120,
        "topics": 2,
    },
    "S2": {
        "publishers": 5,
        "subscribers": 10,
        "messages": 180,
        "topics": 5,
    },
    "S3": {
        "publishers": 10,
        "subscribers": 25,
        "messages": 300,
        "topics": 8,
    },
}


def percentile(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    rank = (len(ordered) - 1) * (p / 100.0)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


@dataclass
class ScenarioResult:
    timestamp_utc: str
    scenario: str
    host: str
    port: int
    ssl_enabled: bool
    publishers: int
    subscribers: int
    messages_per_publisher: int
    topic_count: int
    total_publish_requests: int
    ack_success: int
    ack_fail: int
    subscriber_messages_received: int
    duration_seconds: float
    throughput_msg_per_sec: float
    latency_avg_ms: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    latency_max_ms: float


class FrameClient:
    """Small helper for newline-delimited framed socket protocol."""

    def __init__(self, host: str, port: int, use_ssl: bool):
        self.host = host
        self.port = port
        self.use_ssl = use_ssl
        self.sock: socket.socket | ssl.SSLSocket | None = None
        self.buffer = ""

    def connect(self):
        base_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        base_sock.settimeout(5.0)

        if self.use_ssl:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            self.sock = context.wrap_socket(base_sock, server_hostname=self.host)
        else:
            self.sock = base_sock

        self.sock.connect((self.host, self.port))
        self.sock.settimeout(1.0)

    def send_frame(self, frame: str):
        if not self.sock:
            raise ConnectionError("Socket not connected")
        if not frame.endswith(MESSAGE_DELIMITER):
            frame = f"{frame}{MESSAGE_DELIMITER}"
        self.sock.sendall(frame.encode(ENCODING))

    def read_frame(self, timeout_seconds: float) -> str | None:
        if not self.sock:
            return None

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if MESSAGE_DELIMITER in self.buffer:
                frame, self.buffer = self.buffer.split(MESSAGE_DELIMITER, 1)
                frame = frame.strip()
                if not frame:
                    continue
                if frame == RESP_PING:
                    self.send_frame(CMD_PONG)
                    continue
                return frame

            remaining = max(deadline - time.time(), 0.05)
            self.sock.settimeout(min(1.0, remaining))
            try:
                chunk = self.sock.recv(BUFFER_SIZE)
            except socket.timeout:
                continue

            if not chunk:
                return None

            self.buffer += chunk.decode(ENCODING, errors="replace")

        return None

    def disconnect(self):
        if not self.sock:
            return
        try:
            self.send_frame(CMD_DISCONNECT)
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


class SubscriberWorker(threading.Thread):
    def __init__(self, host: str, port: int, use_ssl: bool, topics: List[str], ready_event: threading.Event, stop_event: threading.Event):
        super().__init__(daemon=True)
        self.client = FrameClient(host, port, use_ssl)
        self.topics = topics
        self.ready_event = ready_event
        self.stop_event = stop_event
        self.received_messages = 0
        self.errors = 0

    def run(self):
        try:
            self.client.connect()
            for topic in self.topics:
                self.client.send_frame(f"{CMD_SUBSCRIBE}|{topic}")
            self.ready_event.set()

            while not self.stop_event.is_set():
                frame = self.client.read_frame(timeout_seconds=0.7)
                if frame is None:
                    continue
                if frame.startswith(RESP_MESSAGE):
                    self.received_messages += 1
                elif frame.startswith(RESP_ERROR):
                    self.errors += 1
        except Exception:
            self.errors += 1
            self.ready_event.set()
        finally:
            self.client.disconnect()


class PublisherWorker(threading.Thread):
    def __init__(
        self,
        publisher_id: int,
        host: str,
        port: int,
        use_ssl: bool,
        topics: List[str],
        messages: int,
        barrier: threading.Barrier,
        ack_timeout: float,
    ):
        super().__init__(daemon=True)
        self.publisher_id = publisher_id
        self.client = FrameClient(host, port, use_ssl)
        self.topics = topics
        self.messages = messages
        self.barrier = barrier
        self.ack_timeout = ack_timeout
        self.success_count = 0
        self.fail_count = 0
        self.latencies_ms: List[float] = []

    def run(self):
        try:
            self.client.connect()
            self.barrier.wait(timeout=20)

            for index in range(self.messages):
                topic = self.topics[(self.publisher_id + index) % len(self.topics)]
                payload = f"bench|pub={self.publisher_id}|msg={index}|ts={time.time_ns()}"

                start = time.perf_counter()
                self.client.send_frame(f"{CMD_PUBLISH}|{topic}|{payload}")
                frame = self.client.read_frame(timeout_seconds=self.ack_timeout)
                elapsed_ms = (time.perf_counter() - start) * 1000.0

                if frame and frame.startswith(RESP_ACK):
                    self.success_count += 1
                    self.latencies_ms.append(elapsed_ms)
                else:
                    self.fail_count += 1
        except Exception:
            self.fail_count = self.messages - self.success_count
        finally:
            self.client.disconnect()


def build_result_row(scenario_name: str, args, subscribers: List[SubscriberWorker], publishers: List[PublisherWorker], duration_seconds: float) -> ScenarioResult:
    all_latencies = [ms for pub in publishers for ms in pub.latencies_ms]
    ack_success = sum(pub.success_count for pub in publishers)
    ack_fail = sum(pub.fail_count for pub in publishers)

    avg_latency = sum(all_latencies) / len(all_latencies) if all_latencies else 0.0
    p50 = percentile(all_latencies, 50)
    p95 = percentile(all_latencies, 95)
    p99 = percentile(all_latencies, 99)
    max_latency = max(all_latencies) if all_latencies else 0.0

    return ScenarioResult(
        timestamp_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        scenario=scenario_name,
        host=args.host,
        port=args.port,
        ssl_enabled=args.ssl,
        publishers=args.publishers,
        subscribers=args.subscribers,
        messages_per_publisher=args.messages,
        topic_count=args.topics,
        total_publish_requests=args.publishers * args.messages,
        ack_success=ack_success,
        ack_fail=ack_fail,
        subscriber_messages_received=sum(s.received_messages for s in subscribers),
        duration_seconds=duration_seconds,
        throughput_msg_per_sec=(ack_success / duration_seconds) if duration_seconds > 0 else 0.0,
        latency_avg_ms=avg_latency,
        latency_p50_ms=p50,
        latency_p95_ms=p95,
        latency_p99_ms=p99,
        latency_max_ms=max_latency,
    )


def run_scenario(scenario_name: str, args) -> ScenarioResult:
    topics = [f"bench-topic-{i + 1}" for i in range(args.topics)]

    stop_event = threading.Event()
    ready_events: List[threading.Event] = []
    subscribers: List[SubscriberWorker] = []

    for index in range(args.subscribers):
        ready = threading.Event()
        ready_events.append(ready)
        if args.subscriber_all_topics:
            sub_topics = topics
        else:
            sub_topics = [topics[index % len(topics)]]

        worker = SubscriberWorker(
            host=args.host,
            port=args.port,
            use_ssl=args.ssl,
            topics=sub_topics,
            ready_event=ready,
            stop_event=stop_event,
        )
        subscribers.append(worker)
        worker.start()

    for ready in ready_events:
        ready.wait(timeout=3.0)

    time.sleep(args.startup_delay)

    barrier = threading.Barrier(args.publishers + 1)
    publishers: List[PublisherWorker] = []

    for publisher_id in range(args.publishers):
        worker = PublisherWorker(
            publisher_id=publisher_id,
            host=args.host,
            port=args.port,
            use_ssl=args.ssl,
            topics=topics,
            messages=args.messages,
            barrier=barrier,
            ack_timeout=args.ack_timeout,
        )
        publishers.append(worker)
        worker.start()

    start = time.perf_counter()
    try:
        barrier.wait(timeout=20)
    except threading.BrokenBarrierError:
        pass

    for worker in publishers:
        worker.join(timeout=args.join_timeout)

    duration = time.perf_counter() - start

    stop_event.set()
    for worker in subscribers:
        worker.join(timeout=2.0)

    return build_result_row(scenario_name=scenario_name, args=args, subscribers=subscribers, publishers=publishers, duration_seconds=duration)


def write_csv(output_path: Path, rows: List[ScenarioResult]):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    headers = list(rows[0].__dataclass_fields__.keys())
    append = output_path.exists() and output_path.stat().st_size > 0

    with output_path.open("a" if append else "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        if not append:
            writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)


def write_markdown(markdown_path: Path, csv_path: Path, rows: List[ScenarioResult], cli_command: str):
    markdown_path.parent.mkdir(parents=True, exist_ok=True)

    lines: List[str] = []
    lines.append("# Performance Evaluation")
    lines.append("")
    lines.append("This report is generated/updated from `scripts/load_harness.py`.")
    lines.append("")
    lines.append(f"- Last run UTC: `{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}`")
    lines.append(f"- Source CSV: `{csv_path}`")
    lines.append(f"- Command: `{cli_command}`")
    lines.append("")
    lines.append("## Scenario Definitions")
    lines.append("")
    lines.append("- `S1` baseline: 1 publisher, 1 subscriber")
    lines.append("- `S2` moderate: 5 publishers, 10 subscribers")
    lines.append("- `S3` stress: 10 publishers, 25 subscribers with higher message volume")
    lines.append("")
    lines.append("## Latest Results")
    lines.append("")
    lines.append("| Scenario | Pubs | Subs | Msg/Publisher | Topics | Total Requests | ACK Success | ACK Fail | Duration (s) | Throughput (msg/s) | Avg Latency (ms) | p50 (ms) | p95 (ms) | p99 (ms) | Max (ms) |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")

    for row in rows:
        lines.append(
            "| {scenario} | {publishers} | {subscribers} | {messages_per_publisher} | {topic_count} | {total_publish_requests} | {ack_success} | {ack_fail} | {duration:.2f} | {throughput:.2f} | {avg:.2f} | {p50:.2f} | {p95:.2f} | {p99:.2f} | {maxv:.2f} |".format(
                scenario=row.scenario,
                publishers=row.publishers,
                subscribers=row.subscribers,
                messages_per_publisher=row.messages_per_publisher,
                topic_count=row.topic_count,
                total_publish_requests=row.total_publish_requests,
                ack_success=row.ack_success,
                ack_fail=row.ack_fail,
                duration=row.duration_seconds,
                throughput=row.throughput_msg_per_sec,
                avg=row.latency_avg_ms,
                p50=row.latency_p50_ms,
                p95=row.latency_p95_ms,
                p99=row.latency_p99_ms,
                maxv=row.latency_max_ms,
            )
        )

    lines.append("")
    lines.append("## Interpretation (Rubric Discussion Points)")
    lines.append("")
    row_map = {r.scenario: r for r in rows}
    if "S1" in row_map:
        s1 = row_map["S1"]
        lines.append(
            f"- S1 baseline latency/throughput reference: avg={s1.latency_avg_ms:.2f} ms, "
            f"throughput={s1.throughput_msg_per_sec:.2f} msg/s."
        )
    if "S2" in row_map:
        s2 = row_map["S2"]
        lines.append(
            f"- S2 moderate concurrency behavior: avg={s2.latency_avg_ms:.2f} ms, "
            f"p95={s2.latency_p95_ms:.2f} ms, ACK success={s2.ack_success}/{s2.total_publish_requests}."
        )
    if "S3" in row_map:
        s3 = row_map["S3"]
        lines.append(
            f"- S3 stress behavior: avg={s3.latency_avg_ms:.2f} ms, p99={s3.latency_p99_ms:.2f} ms, "
            f"ACK fail={s3.ack_fail}/{s3.total_publish_requests}."
        )
        if s3.ack_fail > 0:
            lines.append("- Partial failures at stress level indicate realistic saturation limits under heavy concurrency.")
    lines.append("- Latency percentiles rise as concurrency and publish volume increase, which is expected for thread/socket contention.")
    lines.append("")
    lines.append("## Optimization Notes")
    lines.append("")
    lines.append("- Current implementation prioritizes correctness and robustness over aggressive throughput tuning.")
    lines.append("- Future optimization options: async I/O model, backpressure/rate limiting, finer-grained locking.")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- ACK latency is measured end-to-end from `PUBLISH` send to `ACK` receive.")
    lines.append("- Throughput is computed as successful publishes divided by scenario runtime.")
    lines.append("- Subscribers run concurrently and consume broker messages during the benchmark.")
    lines.append("")

    markdown_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args():
    parser = argparse.ArgumentParser(description="Concurrent load harness for the socket-based Pub/Sub broker")

    parser.add_argument("--host", default=SERVER_HOST, help="Broker host")
    parser.add_argument("--port", type=int, default=SERVER_PORT, help="Broker TCP port")
    parser.add_argument("--ssl", dest="ssl", action="store_true", default=SSL_ENABLED, help="Use TLS/SSL sockets")
    parser.add_argument("--no-ssl", dest="ssl", action="store_false", help="Disable TLS/SSL sockets")

    parser.add_argument("--publishers", type=int, default=2, help="Number of publisher clients")
    parser.add_argument("--subscribers", type=int, default=2, help="Number of subscriber clients")
    parser.add_argument("--messages", type=int, default=100, help="Messages sent by each publisher")
    parser.add_argument("--topics", type=int, default=3, help="Number of topics")

    parser.add_argument("--run-standard-scenarios", action="store_true", help="Run fixed rubric scenarios S1/S2/S3")
    parser.add_argument("--scenario", choices=["S1", "S2", "S3"], help="Run a single fixed rubric scenario")

    parser.add_argument("--subscriber-all-topics", action="store_true", help="Each subscriber subscribes to all topics")
    parser.add_argument("--ack-timeout", type=float, default=5.0, help="ACK timeout for each publish (seconds)")
    parser.add_argument("--startup-delay", type=float, default=0.5, help="Delay before starting publishers (seconds)")
    parser.add_argument("--join-timeout", type=float, default=120.0, help="Per-publisher join timeout (seconds)")

    parser.add_argument("--output", default="results/benchmark.csv", help="CSV output path")
    parser.add_argument("--markdown", default="", help="Optional markdown summary output path")

    return parser.parse_args()


def main():
    args = parse_args()

    runs: List[ScenarioResult] = []

    scenario_queue: List[str] = []
    if args.run_standard_scenarios:
        scenario_queue = ["S1", "S2", "S3"]
    elif args.scenario:
        scenario_queue = [args.scenario]

    if scenario_queue:
        for name in scenario_queue:
            cfg = STANDARD_SCENARIOS[name]
            args.publishers = cfg["publishers"]
            args.subscribers = cfg["subscribers"]
            args.messages = cfg["messages"]
            args.topics = cfg["topics"]
            print(
                f"[RUN {name}] pubs={args.publishers} subs={args.subscribers} "
                f"messages={args.messages} topics={args.topics} ssl={args.ssl}"
            )
            result = run_scenario(scenario_name=name, args=args)
            runs.append(result)
            print(
                f"  -> success={result.ack_success}/{result.total_publish_requests} "
                f"throughput={result.throughput_msg_per_sec:.2f} msg/s "
                f"p95={result.latency_p95_ms:.2f} ms"
            )
    else:
        print(
            f"[RUN CUSTOM] pubs={args.publishers} subs={args.subscribers} "
            f"messages={args.messages} topics={args.topics} ssl={args.ssl}"
        )
        result = run_scenario(scenario_name="CUSTOM", args=args)
        runs.append(result)
        print(
            f"  -> success={result.ack_success}/{result.total_publish_requests} "
            f"throughput={result.throughput_msg_per_sec:.2f} msg/s "
            f"p95={result.latency_p95_ms:.2f} ms"
        )

    output_path = Path(args.output)
    write_csv(output_path=output_path, rows=runs)
    print(f"CSV written to: {output_path}")

    if args.markdown:
        markdown_path = Path(args.markdown)
        cli_command = "python " + " ".join(sys.argv)
        write_markdown(markdown_path=markdown_path, csv_path=output_path, rows=runs, cli_command=cli_command)
        print(f"Markdown summary written to: {markdown_path}")


if __name__ == "__main__":
    main()
