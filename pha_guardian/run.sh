#!/usr/bin/with-contenv bashio

# Get the IP from the user's config options
export GUARDIAN_IP=$(bashio::config 'guardian_ip')

bashio::log.info "Starting PHA Guardian with device at ${GUARDIAN_IP}..."

# Check if we are in dev mode (optional, but helpful based on your Python code)
if bashio::config.has_value 'dev_mode'; then
    export DEV_MODE=$(bashio::config 'dev_mode')
fi

# Read automation IDs list and export as comma-separated string
if bashio::config.has_value 'automation_ids'; then
    export AUTOMATION_IDS=$(bashio::config 'automation_ids' | tr '\n' ',' | sed 's/,$//')
fi

# Add this after the automation_ids block, before the exec line:
if bashio::config.has_value 'api_token'; then
    export API_TOKEN=$(bashio::config 'api_token')
fi

# Where to push detected issues (defaults to prophetdata.net in server.py if unset)
if bashio::config.has_value 'push_url'; then
    export PROPHETDATA_PUSH_URL=$(bashio::config 'push_url')
fi

exec python3 /app/server.py
