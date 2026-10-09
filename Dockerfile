# 画面（Web UI）のコンテナ。Cloudflare Containers（cloudflare/、docs/cloudflare.md）で使うほか、
# Docker が動く所ならどこでも起動できる:
#   docker build -t eclipsecalc . && docker run --rm -p 8080:8080 eclipsecalc   → http://localhost:8080/
FROM python:3.12-slim

WORKDIR /app
RUN useradd --system --no-create-home --home-dir /app eclipsecalc

# JPL 暦（de440s = 1849〜2150 年、de440 = 1550〜2650 年）を入れておき、起動のたびのダウンロードをなくす
ADD --chmod=644 https://ssd.jpl.nasa.gov/ftp/eph/planets/bsp/de440s.bsp data/de440s.bsp
ADD --chmod=644 https://ssd.jpl.nasa.gov/ftp/eph/planets/bsp/de440.bsp data/de440.bsp

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY run.py cli.py ./
COPY eclipsecalc eclipsecalc
COPY static static
# バイトコードを先に作って起動を速くする。data/cache は CelesTrak・SSCWeb の一覧の保存先
RUN python -m compileall -q eclipsecalc run.py cli.py && mkdir -p data/cache && chown eclipsecalc data/cache

USER eclipsecalc
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8080", "--no-browser"]
