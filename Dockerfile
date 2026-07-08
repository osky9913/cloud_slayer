FROM python:3.12-slim

RUN pip install --no-cache-dir cloudslayer

# Pricing cache lives here; mount a volume to persist between runs
VOLUME ["/root/.cloudslayer"]

WORKDIR /workspace
ENTRYPOINT ["cloudslayer"]
CMD ["--help"]
