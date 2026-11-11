FROM python:3.11-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        apache2 \
        apache2-dev \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir flask psycopg2-binary mod-wsgi

RUN mod_wsgi-express module-config > /tmp/wsgi-module.conf \
    && head -n 1 /tmp/wsgi-module.conf > /etc/apache2/mods-available/wsgi.load \
    && tail -n +2 /tmp/wsgi-module.conf > /etc/apache2/mods-available/wsgi.conf \
    && a2enmod wsgi \
    && echo "ServerName localhost" >> /etc/apache2/apache2.conf

COPY web_service.py web_service.wsgi apply_zoom.py docker-entrypoint.sh /app/
COPY apache/000-default.conf /etc/apache2/sites-available/000-default.conf

RUN mkdir -p /app/uploads \
    && chmod +x /app/docker-entrypoint.sh

EXPOSE 80

ENTRYPOINT ["/app/docker-entrypoint.sh"]
