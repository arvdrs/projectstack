# ProjectStack: the web app and API, the pstack CLI, and JMeter 5.6 with the plugins it is
# used with. docker-compose.yml builds it and starts it next to ClickHouse:
#
#   docker compose up -d --build        then open http://localhost:6292
#
# README → Run with Docker explains the setup. Versions are pinned so every build matches the
# machine ProjectStack is developed on: Python packages (requirements.lock), JMeter and its
# jars (deploy/docker/jmeter-libs.txt, checked by SHA), Node and Java.

# ── Web UI ────────────────────────────────────────────────────────────────────
FROM node:18.20.8-bookworm-slim AS ui
WORKDIR /ui
COPY projectstack/ui-web/package.json projectstack/ui-web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY projectstack/ui-web/ ./
RUN npm run build

# ── JMeter 5.6 and its plugins ────────────────────────────────────────────────
FROM debian:bookworm-slim AS jmeter
ARG JMETER_VERSION=5.6
ARG JMETER_SHA512=a7ebf74d6b5aa60d94cac61e0f25f9eaae146dcc35ff1ae0224c987d800bbfa683a45676bcc27d47cd945652c8296a31d911caa63919b3d0b0595eda4fd2978c
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /opt
RUN curl -fsSL --retry 5 --retry-delay 5 -o jmeter.tgz \
      "https://archive.apache.org/dist/jmeter/binaries/apache-jmeter-${JMETER_VERSION}.tgz" \
 && echo "${JMETER_SHA512}  jmeter.tgz" | sha512sum -c - \
 && tar xzf jmeter.tgz \
 && rm jmeter.tgz \
 && mv "apache-jmeter-${JMETER_VERSION}" jmeter \
 && rm -rf jmeter/docs jmeter/printable_docs
COPY deploy/docker/jmeter-libs.txt /tmp/jmeter-libs.txt
RUN tr -d '\r' < /tmp/jmeter-libs.txt | grep -vE '^[[:space:]]*(#|$)' | while read -r dir sha url; do \
      file="jmeter/$dir/${url##*/}"; \
      curl -fsSL --retry 5 --retry-delay 5 -o "$file" "$url" \
        && echo "$sha  $file" | sha256sum -c --quiet - \
        || exit 1; \
    done
# The two settings the reference install changes: an 8 GB heap ceiling (HEAP in the environment
# still overrides it) and 30 minutes, not 5, to generate the HTML report of a long run
RUN sed -i '/^: "\${HEAP:=/s/-Xms1g -Xmx1g -XX:MaxMetaspaceSize=256m/-Xms1g -Xmx8g -XX:MaxMetaspaceSize=512m/' jmeter/bin/jmeter \
 && grep -q -- '-Xmx8g' jmeter/bin/jmeter \
 && echo 'generate_report_ui.generation_timeout=1800000' >> jmeter/bin/user.properties

# ── ProjectStack ──────────────────────────────────────────────────────────────
FROM python:3.10-slim-bookworm
LABEL org.opencontainers.image.title="ProjectStack" \
      org.opencontainers.image.source="https://github.com/arvdrs/projectstack"

# HOME=/data: settings, sessions, reports, uploads and the recorder's certificate live in one volume
ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    JAVA_HOME=/opt/java/openjdk \
    HOME=/data \
    PSTACK_IN_DOCKER=1
ENV PATH="/opt/jmeter/bin:${JAVA_HOME}/bin:${PATH}"

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements.lock ./
# Exactly the versions in requirements.lock, which lists every dependency, without re-resolving:
# pip's resolver rejects two pins that work in practice (mitmproxy 11.0.2, the last release for
# Python 3.10, asks for older typing-extensions and h11 than the AI clients use). The check after
# it stops the build if requirements.txt asks for something the lock does not provide.
RUN pip install --no-deps -r requirements.lock \
 && python -c "from importlib.metadata import version; from packaging.requirements import Requirement; \
reqs = [Requirement(l) for l in open('requirements.txt') if l.strip() and not l.startswith('#')]; \
bad = [str(r) for r in reqs if not r.specifier.contains(version(r.name), prereleases=True)]; \
assert not bad, f'requirements.lock does not satisfy {bad}: run scripts/lock_requirements.py'"

# Java 11, as on the reference machine (JMeter's older WebSocket plugin predates Java 17)
COPY --from=eclipse-temurin:11-jre /opt/java/openjdk /opt/java/openjdk
COPY --from=jmeter /opt/jmeter /opt/jmeter

COPY . .
COPY --from=ui /ui/dist projectstack/ui-web/dist
# The python image ships its standard library without bytecode; compiling it (and the app) once
# here halves the start-up time of every process: the server, the CLI, the recorder
RUN pip install --no-deps -e . \
 && python -m compileall -q -j 0 /usr/local/lib/python3.10 /app/projectstack > /dev/null \
 && useradd --uid 1000 --user-group --home-dir /data --no-create-home --shell /bin/bash pstack \
 && mkdir -p /data /workspace \
 && chown pstack:pstack /data /workspace \
 && sed 's/\r$//' deploy/docker/entrypoint.sh > /usr/local/bin/pstack-entrypoint \
 && chmod 755 /usr/local/bin/pstack-entrypoint \
 && cd /tmp && jmeter --version | grep -q '5\.6' && rm -f /tmp/jmeter.log

# The CLI's working folder: ./workspace on your computer
WORKDIR /workspace
EXPOSE 6292 8082

# Starts as root to match the pstack user to the owner of ./workspace, then runs everything as pstack
ENTRYPOINT ["pstack-entrypoint"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:6292/api/health', timeout=4)"

CMD ["uvicorn", "projectstack.api.main:app", "--host", "0.0.0.0", "--port", "6292", "--log-level", "warning"]
