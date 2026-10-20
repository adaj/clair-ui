FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    GRADIO_ANALYTICS_ENABLED=False \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/mplconfig

RUN useradd --create-home --uid 10001 app \
 && mkdir -p /tmp/mplconfig && chown 10001:10001 /tmp/mplconfig
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY clair_client.py app.py ui_style.py modes_guide.py i18n.py plots_mpl.py analytics_adapter.py sim_bridge.py modes_help*.json ./
COPY assets/ ./assets/
COPY locales/ ./locales/

USER app
# Porta/host: GRADIO_SERVER_PORT ou PORT (padrao 7860) e GRADIO_SERVER_NAME (padrao 0.0.0.0)
EXPOSE 7860
CMD ["python", "app.py"]
