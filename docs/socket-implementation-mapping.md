# Socket Programming Implementation Mapping

This note maps the rubric requirement to exact locations in the codebase.

## Rubric Requirement Coverage

### 1) Socket Creation
- Server creates TCP socket: `socket.socket(AF_INET, SOCK_STREAM)` in `server.py` at line `127`.
- Publisher creates TCP socket: `socket.socket(AF_INET, SOCK_STREAM)` in `publisher.py` at line `42`.
- Subscriber creates TCP socket: `socket.socket(AF_INET, SOCK_STREAM)` in `subscriber.py` at line `39`.
//this is your IPC, there are these .py files running as processes and they connect via the sockets made

### 2) Binding and Listening (Server Side)
- Bind host/port: `self.server_socket.bind((self.host, self.port))` in `server.py` at line `129`.
- Listen for connections: `self.server_socket.listen(MAX_CLIENTS)` in `server.py` at line `130`.
- Accept loop: `client_socket, address = self.server_socket.accept()` in `server.py` at line `150`.

### 3) Connection Handling
- Per-client concurrent handling: new thread per accepted socket in `server.py` lines `175-181`.
- Client handler entry point: `handle_client(...)` in `server.py` line `433`.
- Graceful disconnect cleanup: `disconnect_client(...)` in `server.py` line `808`.
- Publisher connect/disconnect:
  - Connect: `publisher.py` lines `39-53`
  - Disconnect: `publisher.py` lines `71-81`
- Subscriber connect/disconnect:
  - Connect: `subscriber.py` lines `36-50`
  - Disconnect: `subscriber.py` lines `64-75`

### 4) Data Transmission (Low-Level send/recv)
- Server receives raw bytes: `client_socket.recv(BUFFER_SIZE)` in `server.py` line `450`.
- Server sends framed responses: `client_socket.send(...)` used across handlers (`ACK/ERROR/MESSAGE`) in `server.py` (examples: lines `572`, `836`, `842`).
- Publisher sends frames: `self.socket.send(frame.encode(...))` in `publisher.py` line `137`.
- Subscriber receives frames: `self.socket.recv(BUFFER_SIZE)` in `subscriber.py` line `148`.
- Subscriber sends subscribe/unsubscribe/list commands:
  - `subscriber.py` lines `87-88`, `104-105`, `120-121`.

### 5) Explicit Protocol and Framing
- Newline-delimited framing (`MESSAGE_DELIMITER`) with manual buffer splitting:
  - Server framing loop: `server.py` lines `456-458`.
  - Publisher framing/buffer queue: `publisher.py` lines `99-118`, `162-175`.
  - Subscriber framing loop: `subscriber.py` lines `156-159`.
- Command parser using low-level string frames (no RPC framework):
  - `process_command(...)` in `server.py` lines `478-533`.

### 6) Proper Connection Robustness
- Socket timeouts:
  - Server client timeout setup: `server.py` line `435`.
  - Timeout handling path: `server.py` lines `466-468`.
- Keepalive:
  - Server PING loop: `keepalive_loop(...)` in `server.py` lines `779-801`.
  - Client PONG handling:
    - Publisher: `publisher.py` lines `110-114`.
    - Subscriber: `subscriber.py` lines `163-168`.
- TLS wrapping (secure sockets):
  - Server wraps accepted socket: `server.py` line `154`.
  - Publisher wraps client socket: `publisher.py` line `48`.
  - Subscriber wraps client socket: `subscriber.py` line `45`.

## Why This Qualifies as Low-Level Socket Handling
- Uses direct `socket` API (`socket()`, `bind()`, `listen()`, `accept()`, `connect()`, `send()`, `recv()`, `close()`).
- Manual protocol framing and parsing over raw byte streams.
- No high-level networking frameworks (no FastAPI/Flask/Django channels/gRPC/websocket abstractions for broker protocol).
