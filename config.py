"""
Configuration file for Publish-Subscribe Notification Service
Contains all constants and configuration parameters
"""

import os

# Server Configuration
SERVER_HOST = '127.0.0.1'  # Localhost for testing
SERVER_PORT = 9999         # Server port

# SSL/TLS Configuration
SSL_ENABLED = True
CERT_DIR = 'certs'
SERVER_CERT = os.path.join(CERT_DIR, 'server.crt')
SERVER_KEY = os.path.join(CERT_DIR, 'server.key')

# Protocol Configuration
BUFFER_SIZE = 4096         # Socket buffer size in bytes
MESSAGE_DELIMITER = '\n'   # Protocol message delimiter
ENCODING = 'utf-8'        # Character encoding

# Server Limits
MAX_CLIENTS = 100         # Maximum concurrent clients
MAX_TOPICS = 1000         # Maximum number of topics

# Message History (new feature)
MESSAGE_HISTORY_SIZE = 20  # Number of past messages to replay to new subscribers per topic

# Timeout Configuration (seconds)
SOCKET_TIMEOUT = 300      # 5 minutes socket timeout
CONNECTION_TIMEOUT = 10   # Connection establishment timeout
PING_INTERVAL = 30        # How often server sends PING to clients (seconds)
PING_TIMEOUT = 10         # How long to wait for PONG before dropping client

# Logging Configuration
LOG_DIR = 'logs'
LOG_FILE = os.path.join(LOG_DIR, 'server.log')
LOG_LEVEL = 'INFO'        # DEBUG, INFO, WARNING, ERROR, CRITICAL

# Protocol Commands
CMD_PUBLISH = 'PUBLISH'
CMD_SUBSCRIBE = 'SUBSCRIBE'
CMD_UNSUBSCRIBE = 'UNSUBSCRIBE'
CMD_LIST_TOPICS = 'LIST_TOPICS'
CMD_STATS = 'STATS'
CMD_LIST_CLIENTS = 'LIST_CLIENTS'
CMD_DISCONNECT = 'DISCONNECT'
CMD_PING = 'PING'
CMD_PONG = 'PONG'

# Response Types
RESP_ACK = 'ACK'
RESP_ERROR = 'ERROR'
RESP_MESSAGE = 'MESSAGE'
RESP_TOPICS = 'TOPICS'
RESP_PING = 'PING'
RESP_STATS = 'STATS'
RESP_CLIENTS = 'CLIENTS'

# Client Types
CLIENT_TYPE_PUBLISHER = 'publisher'
CLIENT_TYPE_SUBSCRIBER = 'subscriber'
CLIENT_TYPE_UNDEFINED = 'undefined'

# Dashboard / Monitoring API
DASHBOARD_ENABLED = True
DASHBOARD_HOST = '127.0.0.1'
DASHBOARD_PORT = 8088
DASHBOARD_FILE = 'dashboard.html'

# Broker observability
BROKER_EVENT_BUFFER_SIZE = 500
HOT_TOPIC_COUNT = 5
EWMA_DECAY = 0.25
