FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && apt-get clean
ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL io.hass.version="${BUILD_VERSION}" io.hass.type="app" io.hass.arch="${BUILD_ARCH}"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY energy_scheduler /app/energy_scheduler
COPY settings.json /app/settings.json
CMD ["python", "-m", "energy_scheduler.run", "--settings", "/app/settings.json", "--api-url", "http://supervisor/core/api", "--data-dir", "/data/observations", "--publish", "--loop"]
