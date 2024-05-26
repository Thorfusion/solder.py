FROM python:3.10-alpine

WORKDIR /app
COPY /models/ /app/models/
COPY /static/ /app/static/
COPY /templates/ /app/templates/
COPY /api.py /app/
COPY /app.py /app/
COPY /LICENSE.md /app/
COPY /Pipfile /app/
COPY /Pipfile.lock /app/
COPY /README.md /app/
COPY /.dockerignore /app/
COPY /Dockerfile /app/
RUN mkdir /app/mods
VOLUME /app/mods
ENV APP_URL=0.0.0.0
ENV APP_PORT=5000
ENV APP_DEBUG=false
EXPOSE 5000
RUN python -m pip install --upgrade pip
RUN pip install gunicorn
RUN pip install pipenv

RUN pipenv install --system --deploy --ignore-pipfile

CMD ["pipenv", "run", "gunicorn", "-w", "1", "--threads", "4", "-b", "0.0.0.0:5000", "app:app"]
