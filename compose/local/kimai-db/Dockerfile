FROM docker.io/mysql:8.4

# Pull Oracle Linux security fixes the upstream tag hasn't been rebuilt with yet.
# MySQL Shell isn't used by the server or entrypoint; its bundled Python libs
# (certifi, cryptography, urllib3, pyOpenSSL) carry unpatched CVEs.
RUN microdnf -y update && microdnf -y remove mysql-shell && microdnf clean all
