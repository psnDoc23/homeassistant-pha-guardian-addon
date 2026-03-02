ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:3.19
FROM $BUILD_FROM

# 1. Install system dependencies
RUN apk add --no-cache python3 py3-pip

# 2. Set the working directory
WORKDIR /app

# 3. Install Python requirements
# We copy this first to leverage Docker's build cache
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# 4. COPY EVERYTHING ELSE (This is the fix!)
# This grabs logging_config.py, server.py, and any other files in the folder
COPY . .

# 5. Setup the run script
# Note: Since we used 'COPY . .', run.sh is already in /app
RUN chmod +x /app/run.sh

# 6. Start the add-on using the bashio entrypoint
# This is the bridge that injects SUPERVISOR_TOKEN into your script
# CMD [ "/usr/bin/bashio", "/app/run.sh" ]

#6 the claude way
CMD [ "/bin/bash", "/app/run.sh" ]
