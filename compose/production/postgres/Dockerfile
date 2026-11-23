FROM docker.io/postgres:18

# Pull Debian security fixes the upstream tag hasn't been rebuilt with yet
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt/lists/*

COPY ./compose/production/postgres/maintenance /usr/local/bin/
RUN chmod +x /usr/local/bin/*
