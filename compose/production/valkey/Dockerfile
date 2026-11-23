FROM docker.io/valkey/valkey:9.0.6

# Pull Debian security fixes the upstream tag hasn't been rebuilt with yet
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
