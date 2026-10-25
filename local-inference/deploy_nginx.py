"""Add an isolated inference vhost through an existing read-only nginx bind mount."""
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import sys

CLUSTER = Path('/root/alfa-tatari/cluster')
MAIN = CLUSTER / 'nginx/nginx.prod.conf'
DIRECTORY = CLUSTER / 'acme-webroot/.local-inference'
EXTRA = DIRECTORY / 'nginx.conf'
INCLUDE = 'include /var/www/certbot/.local-inference/nginx.conf;'
HOST = 'inference.cheremha.ru'
CERT = CLUSTER / 'letsencrypt/live' / HOST

BOOTSTRAP = '''# Managed by tools/LocalInference/vps/deploy_nginx.py.
server {
    listen 80;
    server_name inference.cheremha.ru;
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/certbot;
        default_type text/plain;
    }
    location / { return 503; }
}
'''

TLS = '''# Managed by tools/LocalInference/vps/deploy_nginx.py.
upstream inference_pc {
    server unix:/var/www/certbot/.local-inference/api.sock;
}
server {
    listen 80;
    server_name inference.cheremha.ru;
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/certbot;
        default_type text/plain;
    }
    location / { return 308 https://inference.cheremha.ru$request_uri; }
}
server {
    listen 443 ssl;
    http2 on;
    server_name inference.cheremha.ru;
    ssl_certificate /etc/letsencrypt/live/inference.cheremha.ru/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/inference.cheremha.ru/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:INFERENCE_TLS:1m;
    ssl_session_timeout 1d;
    client_max_body_size 10m;

    location ~ ^/v1/(models|embeddings|rerank)$ {
        proxy_pass http://inference_pc;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header Authorization $http_authorization;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_cache off;
        proxy_connect_timeout 5s;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }
    location / { return 404; }
}
'''


def command(*args):
    subprocess.run(args, check=True)


def main():
    mode = sys.argv[1]
    assert mode in ('bootstrap', 'tls')
    if mode == 'tls':
        assert (CERT / 'fullchain.pem').is_file() and (CERT / 'privkey.pem').is_file()
    DIRECTORY.mkdir(exist_ok=True)
    old_main = MAIN.read_bytes()
    old_extra = EXTRA.read_bytes() if EXTRA.exists() else None
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = CLUSTER / 'local-inference/backups' / stamp
    backup.mkdir(parents=True)
    shutil.copy2(MAIN, backup / 'nginx.prod.conf')
    if old_extra is not None:
        shutil.copy2(EXTRA, backup / 'inference.conf')
    new_main = old_main if INCLUDE.encode() in old_main else old_main + b'\n' + INCLUDE.encode() + b'\n'
    assert MAIN.read_bytes() == old_main, 'nginx configuration changed concurrently'
    try:
        # Keep the inode: nginx.prod.conf is mounted as a single file in the running container.
        EXTRA.write_text(TLS if mode == 'tls' else BOOTSTRAP)
        MAIN.write_bytes(new_main)
        command('docker', 'exec', 'nginx', 'nginx', '-t')
        command('docker', 'exec', 'nginx', 'nginx', '-s', 'reload')
    except BaseException:
        MAIN.write_bytes(old_main)
        if old_extra is None:
            EXTRA.unlink(missing_ok=True)
        else:
            EXTRA.write_bytes(old_extra)
        command('docker', 'exec', 'nginx', 'nginx', '-t')
        command('docker', 'exec', 'nginx', 'nginx', '-s', 'reload')
        raise
    print(f'Inference vhost {mode} deployed; rollback copies in {backup}')


if __name__ == '__main__':
    main()
