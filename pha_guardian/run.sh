#!/usr/bin/with-contenv bashio

# https://gemini.google.com/app/1ee4bb599a9fa8e7?pli=1
# Use bashio's built-in logging for that official HA look
bashio::log.info "Starting PHA Guardian add-on..."

# Check if we are in dev mode (optional, but helpful based on your Python code)
if bashio::config.has_value 'dev_mode'; then
    export DEV_MODE=$(bashio::config 'dev_mode')
fi

# IMPORTANT: 'exec' ensures Python becomes PID 1. 
# This allows the Supervisor to send stop/restart signals directly to your app
# and ensures the environment variables from bashio flow into the Python process.
exec python3 /app/server.py

