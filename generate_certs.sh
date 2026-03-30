#!/bin/bash

# SSL Certificate Generation Script for Pub-Sub Server
# Generates self-signed certificates for lab/testing environment

echo "==================================="
echo "SSL Certificate Generation Script"
echo "==================================="

# Create certs directory if it doesn't exist
mkdir -p certs

# Generate private key and self-signed certificate
echo "Generating server private key and certificate..."

openssl req -x509 -newkey rsa:4096 -nodes \
    -keyout certs/server.key \
    -out certs/server.crt \
    -days 365 \
    -subj "/C=IN/ST=Karnataka/L=Bangalore/O=CN_Lab/OU=PubSub/CN=localhost"

if [ $? -eq 0 ]; then
    echo "✓ Certificate generated successfully!"
    echo ""
    echo "Generated files:"
    echo "  - certs/server.key (Private Key)"
    echo "  - certs/server.crt (Certificate)"
    echo ""
    echo "Certificate details:"
    openssl x509 -in certs/server.crt -noout -subject -dates
    echo ""
    echo "These certificates are valid for 365 days."
    echo "Ready to use with server.py!"
else
    echo "✗ Error generating certificates"
    exit 1
fi
