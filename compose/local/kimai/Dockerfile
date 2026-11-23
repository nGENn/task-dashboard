FROM docker.io/kimai/kimai2:stable

# Pull Debian security fixes the upstream tag hasn't been rebuilt with yet
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*
