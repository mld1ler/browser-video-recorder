#!/bin/sh
set -e

mkdir -p /app/uploads
chown -R www-data:www-data /app/uploads

exec apache2ctl -D FOREGROUND
