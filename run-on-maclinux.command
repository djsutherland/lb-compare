#!/bin/sh
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

uv run streamlit run streamlit_app.py \
  --server.address localhost \
  --browser.gatherUsageStats false
