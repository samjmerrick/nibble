# Builds the bee-database server with the Nibble plugin copied in. The server code comes from a
# second build context named "bee" (see docker-compose.yml), so Nibble never carries a copy of it.
FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATA_DIR=/data PORT=8000 PLUGIN_PATH=/plugins
WORKDIR /app
COPY --from=bee requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=bee *.py ./
COPY --from=bee plugins/ ./plugins/
COPY nibble/ /plugins/nibble/
RUN useradd -u 1000 -m app && mkdir -p /data && chown app /data
USER app
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["python", "app.py"]
