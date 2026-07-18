FROM python:3.11-slim

# Install Node.js (for Claude CLI)
RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Claude CLI + Codex CLI
RUN npm install -g @anthropic-ai/claude-code @openai/codex

# docker.sock is bind-mounted in (docker-compose.yml) so this container can
# reach the host's Docker daemon, but nothing here was ever installed to
# actually talk to it — handle_mcp_action() below shells out to
# `docker`/`docker compose` for docker-type MCP servers' start/stop/restart
# and the Docker-config UI, and those calls fail with "command not found"
# without a client. Copying just the CLI (+ compose v2 plugin) from the
# official image is much lighter than installing the full Docker Engine.
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker:27-cli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose

# Same story as docker above: the "fetch"/"sqlite" preset MCP servers
# (main.py, below) are registered as `uvx mcp-server-fetch` / `uvx
# mcp-server-sqlite ...`, but uvx was never installed — both fail with
# "command not found". Official Astral pattern for including uv in an
# image: copy the prebuilt binaries straight from their image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# nvidia-cublas-cu12/nvidia-cudnn-cu12（requirements.txt，語音辨識 GPU 加速
# 用）把 .so 檔案裝在 site-packages 底下，不在動態連結器預設會找的路徑——
# 沒有這行，ctranslate2 偵測到 GPU 也會直接載入失敗（實測過："Library
# libcublas.so.12 is not found or cannot be loaded"）。只有疊加
# docker-compose.gpu.yml 才會真的把 GPU 傳進 container；沒有 GPU 的機器
# 就算路徑存在但用不到，不影響 CPU fallback。
ENV LD_LIBRARY_PATH="/usr/local/lib/python3.11/site-packages/nvidia/cublas/lib:/usr/local/lib/python3.11/site-packages/nvidia/cudnn/lib:${LD_LIBRARY_PATH}"
# main.py imports helpers/session_pool/memory_agent/message_bus and the routes/
# package at startup — COPY was missing all of these, so this image never
# actually booted (ModuleNotFoundError on the first `python main.py`).
# mcp_sync.py 和 engines/ package（可插拔 Claude/Codex 引擎）是後來加的，
# 同樣的理由要一起 COPY，不然這個 image 一樣開機就死。
COPY main.py database.py helpers.py session_pool.py memory_agent.py message_bus.py agency_agents_importer.py mcp_sync.py codex_usage.py resource_sync.py stt.py dir_cache.py ./
COPY docker-entrypoint.sh ./
COPY routes/ ./routes/
COPY engines/ ./engines/
COPY presets/ ./presets/

# 健檢第二輪修復：容器原本預設用 root 執行，疊加 docker.sock + 使用者
# ~/.claude 憑證掛載，一旦後端有 RCE 就等於直接拿到 host 等級的存取權。
# 改用非 root 使用者。docker.sock 是 host 的 bind mount，容器內权限檢查
# 只看 GID 數字（跟名稱無關），所以用一個 GID 跟 host docker 群組相同的
# 群組把 appuser 加進去即可存取 —— 各 host 的實際 GID 不同，預設用常見值
# 999，需要的話用 `docker build --build-arg DOCKER_GID=<實際值>` 覆寫
# （`getent group docker` 可查詢 host 上的實際 GID）。
ARG DOCKER_GID=999
RUN existing_group="$(getent group ${DOCKER_GID} | cut -d: -f1)"; \
    if [ -z "$existing_group" ]; then \
        groupadd -g ${DOCKER_GID} dockerhost; \
        existing_group=dockerhost; \
    fi; \
    useradd -m -u 1000 -G "$existing_group" appuser && \
    mkdir -p /home/appuser/.codex /home/appuser/.agents/skills && \
    chown -R appuser:appuser /app /home/appuser/.codex /home/appuser/.agents

USER appuser

EXPOSE 8765
CMD ["sh", "/app/docker-entrypoint.sh"]
