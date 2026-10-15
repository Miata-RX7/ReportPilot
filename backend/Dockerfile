FROM python:3.11-slim

WORKDIR /code

# 中文字体：图表中文标签必需。
# fonts-wqy-microhei 仅 +1.6MB（覆盖 GB2312 + 常用简中）；
# 若实测出现生僻字豆腐块，可换 fonts-noto-cjk（+56MB 下载，解压约 200MB），
# 见 docs/technical-reference.md「图表渲染」小节。--no-install-recommends + 清 apt 缓存控体积。
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-wqy-microhei \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/mplconfig

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
