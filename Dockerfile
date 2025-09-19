FROM pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime
COPY --from=ghcr.io/astral-sh/uv:0.8.18 /uv /uvx /bin/

RUN apt-get update && apt-get install -y \
    build-essential \
    rabbitmq-server \
    supervisor \
    && apt-get clean

# Install fonts
# Not all PDFs will render without this
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        fontconfig \
        fonts-noto-core \
        fonts-noto-ui-core \
        fonts-noto-extra \
        fonts-noto-cjk \
        fonts-noto-mono \
        fonts-noto-color-emoji \
        fonts-liberation \
        fonts-croscore \
        fonts-freefont-ttf \
        fonts-symbola \
        fonts-ipafont \
        fonts-nanum \
        fonts-indic \
        fonts-kacst \
        fonts-thai-tlwg \
        && fc-cache -f -v && \
        apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /inference

COPY uv.lock pyproject.toml /inference/

RUN uv sync --group server --group worker --no-cache
ENV PATH="/inference/.venv/bin:$PATH"
# torch is pinned to 2.8 in pyproject so we can do this
RUN uv pip install https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.3/flash_attn-2.8.3+cu12torch2.8cxx11abiTRUE-cp313-cp313-linux_x86_64.whl

# Build models into container
RUN python -c "from transformers.utils import is_flash_attn_2_available; print(is_flash_attn_2_available())"
RUN python -c "from marker.models import create_model_dict; create_model_dict()"
RUN python -c "from marker.util import download_font; download_font()"

COPY inference/ /inference/inference/
COPY supervisord.conf /inference/supervisord.conf
COPY run.sh /inference/run.sh

ENV PYTHONUNBUFFERED=1

# Add directories for output and data
RUN mkdir /output
RUN mkdir /data

# Make run.sh executable and expose port 8000
RUN chmod +x /inference/run.sh
EXPOSE 8000

CMD ["/inference/run.sh"]
