# personal-agent-host-sandbox: the baseline image every host_exec job can use.
# Tags are deliberately versioned. Update them only with a corresponding image rebuild.
FROM ghcr.io/astral-sh/uv:0.12.8@sha256:d1cbaeadc234fe19c0d93daabcf5e98738cd93c6d1dd4918ef6aa30735feb23a AS uv

FROM ubuntu:24.04@sha256:224a1869083a311ef3f13648a154ba79832fbef6364d31493642ca03082da254
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8
LABEL org.personal-agent.profile="base" \
      org.personal-agent.features='["python","git","uv","ripgrep"]'
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git build-essential python3 python3-venv ripgrep jq rsync unzip \
 && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
# Keep the declared baseline features executable at image-build time.
RUN python3 --version && git --version && uv --version && rg --version
WORKDIR /workspace
CMD ["/bin/sh"]
