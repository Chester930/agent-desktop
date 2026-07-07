FROM python:3.11-slim

# Install Node.js (for Claude CLI)
RUN apt-get update && apt-get install -y curl && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Claude CLI
RUN npm install -g @anthropic-ai/claude-code

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# main.py imports helpers/session_pool/memory_agent/message_bus and the routes/
# package at startup — COPY was missing all of these, so this image never
# actually booted (ModuleNotFoundError on the first `python main.py`).
COPY main.py database.py helpers.py session_pool.py memory_agent.py message_bus.py agency_agents_importer.py ./
COPY routes/ ./routes/
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
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8765
CMD ["python", "main.py"]
