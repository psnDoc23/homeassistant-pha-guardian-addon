#!/usr/bin/with-contenv bashio

# Get the IP from the user's config options
export GUARDIAN_IP=$(bashio::config 'guardian_ip')

bashio::log.info "Starting PHA Guardian with device at ${GUARDIAN_IP}..."

# Check if we are in dev mode (optional, but helpful based on your Python code)
if bashio::config.has_value 'dev_mode'; then
    export DEV_MODE=$(bashio::config 'dev_mode')
fi

# IMPORTANT: 'exec' ensures Python becomes PID 1. 
# This allows the Supervisor to send stop/restart signals directly to your app
# and ensures the environment variables from bashio flow into the Python process.
exec python3 /app/server.py

