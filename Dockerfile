FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    GRADIO_ANALYTICS_ENABLED=False \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/mplconfig

RUN useradd --create-home --uid 10001 app \
 && mkdir -p /tmp/mplconfig && chown 10001:10001 /tmp/mplconfig
WORKDIR /app

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --default-timeout=120 --retries 10 -r requirements.txt

COPY --chown=10001:10001 clair_client.py app.py ui_style.py modes_guide.py i18n.py plots_mpl.py analytics_adapter.py sim_bridge.py modes_help*.json ./
COPY --chown=10001:10001 assets/ ./assets/
COPY --chown=10001:10001 locales/ ./locales/

RUN chmod -R a+rX /app

USER app
# Porta/host: GRADIO_SERVER_PORT ou PORT (padrao 7860) e GRADIO_SERVER_NAME (padrao 0.0.0.0)
EXPOSE 7860
CMD ["python", "app.py"]
