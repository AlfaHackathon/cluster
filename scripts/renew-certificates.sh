#!/bin/sh
set -eu
cluster_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
docker run --rm \
    -v "$cluster_dir/letsencrypt:/etc/letsencrypt" \
    -v "$cluster_dir/acme-webroot:/var/www/certbot" \
    certbot/certbot:latest renew --non-interactive --no-random-sleep-on-renew --webroot \
    --webroot-path /var/www/certbot "$@"
docker exec nginx nginx -t
docker exec nginx nginx -s reload
