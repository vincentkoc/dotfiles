#!/bin/bash

# Usage:
#   check_certificate_chain.sh [server/ip] [port]

SERVER="$1"
PORT="$2"

if [[ -z "$SERVER" || -z "$PORT" ]]; then
    echo "Usage:"
    echo "  check_certificate_chain.sh [server/ip] [port]"
    exit 1
fi

# Fetch the certificate chain
echo | openssl s_client -showcerts -servername "$SERVER" -connect "$SERVER:$PORT" 2>/dev/null

