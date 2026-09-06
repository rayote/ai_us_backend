FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

ARG UID=1000
ARG GID=1000

RUN groupadd -g "${GID}" python \
  && useradd --create-home --no-log-init -u "${UID}" -g "${GID}" python

WORKDIR /home/python

COPY --chown=python:python requirements.txt requirements.txt
RUN pip3 install --no-cache-dir -r requirements.txt

USER python:python
COPY --chown=python:python . .

EXPOSE 5000

CMD ["./deploy/start.sh"]