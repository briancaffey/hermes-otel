# Maple Local is shipped as a single binary (brew / curl installer), not as a
# container image. This wraps the GitHub release tarball so it runs like the
# other stacks here. Licence: FSL-1.1-ALv2 (source-available, converts to
# Apache-2.0 after two years), not OSI open source.
FROM debian:bookworm-slim
ARG MAPLE_VERSION=v0.0.23
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/* \
 && case "$TARGETARCH" in amd64) t=x86_64-unknown-linux-gnu ;; arm64) t=aarch64-unknown-linux-gnu ;; *) echo "unsupported arch $TARGETARCH" && exit 1 ;; esac \
 && curl -fsSL "https://github.com/MapleTechLabs/maple/releases/download/${MAPLE_VERSION}/maple-${MAPLE_VERSION}-${t}.tar.gz" -o /tmp/maple.tgz \
 && mkdir -p /tmp/maple && tar -xzf /tmp/maple.tgz -C /tmp/maple \
 && mkdir -p /root/.maple/bin \
 && install -m 0755 "$(find /tmp/maple -type f -name maple | head -1)" /root/.maple/bin/maple \
 && install -m 0644 "$(find /tmp/maple -type f -name libchdb.so | head -1)" /root/.maple/bin/libchdb.so \
 && rm -rf /tmp/maple /tmp/maple.tgz
# The binary dlopens libchdb.so from beside itself (or ~/.maple/bin), the same
# two-file layout the upstream installer uses.
ENV PATH=/root/.maple/bin:$PATH
EXPOSE 4318
VOLUME ["/root/.maple/data"]
ENTRYPOINT ["maple"]
CMD ["start"]
