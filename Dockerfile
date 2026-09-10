FROM python:3.13-alpine AS dependencies

WORKDIR /build
COPY /Pipfile /build/
COPY /Pipfile.lock /build/

RUN python -m pip install --no-cache-dir pipenv \
    && PIPENV_DONT_LOAD_ENV=1 pipenv requirements --hash > requirements.txt \
    && python -m pip install --no-cache-dir --ignore-installed --prefix=/install --require-hashes --requirement requirements.txt

FROM python:3.13-alpine

RUN apk upgrade --no-cache \
    && python -m pip uninstall --yes pip

WORKDIR /app
COPY --from=dependencies /install /usr/local
COPY /models/ /app/models/
COPY /static/ /app/static/
COPY /templates/ /app/templates/
COPY /api.py /app/
COPY /app.py /app/
COPY /alogin.py /app/
COPY /asetup.py /app/
COPY /asite.py /app/
COPY /LICENSE.md /app/
COPY /Pipfile /app/
COPY /Pipfile.lock /app/
COPY /README.md /app/
COPY /.dockerignore /app/
COPY /Dockerfile /app/
RUN mkdir /app/mods
VOLUME /app/mods
ENV APP_HOST=0.0.0.0
ENV APP_PORT=5000
ENV APP_DEBUG=false
EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --quiet --tries=1 --output-document=- http://127.0.0.1:5000/api/ | grep --quiet '"api":"solder.py"' || exit 1

CMD ["sh", "-c", "exec gunicorn -w 1 --threads 8 -b 0.0.0.0:5000 --forwarded-allow-ips=\"${PROXY_IP:-127.0.0.1}\" app:app"]
