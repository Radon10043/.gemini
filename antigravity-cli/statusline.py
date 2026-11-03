#!/usr/bin/env python3
import sys
import os
import re
import json
import sqlite3

def get_statusline():
    # 1. Read standard input (Antigravity sends current runtime state as JSON via stdin)
    raw_input = ""
    try:
        if not sys.stdin.isatty():
            raw_input = sys.stdin.read()
    except Exception:
        pass

    data = {}
    if raw_input.strip():
        try:
            data = json.loads(raw_input)
        except Exception:
            pass

    app_data = os.getenv(
        "ANTIGRAVITY_APP_DATA_DIR",
        os.path.expanduser("~/.gemini/antigravity-cli")
    )

    # 2. Extract current working directory
    cwd = (
        data.get("workspace", {}).get("current_dir")
        or data.get("cwd")
        or os.getcwd()
    )

    # 3. Extract thinking effort / budget (displayed as lowercase, e.g., low, medium, high)
    effort = (
        data.get("effort")
        or data.get("thinking_effort")
        or data.get("reasoning_effort")
        or ""
    )

    model_info = data.get("model") or {}
    model_name = model_info.get("display_name") or model_info.get("id") or ""

    # If effort not in data, try extracting from model name or settings.json
    if not effort:
        if not model_name:
            settings_path = os.path.join(app_data, "settings.json")
            if os.path.exists(settings_path):
                try:
                    with open(settings_path, "r", encoding="utf-8") as f:
                        s_data = json.load(f)
                        model_name = s_data.get("model", "")
                        effort = s_data.get("effort", "")
                except Exception:
                    pass

        if model_name:
            m = re.search(r'\((Low|Medium|High|XHigh|Max)\)', model_name, re.IGNORECASE)
            if m:
                effort = m.group(1)

    if not effort:
        effort = os.getenv("ANTIGRAVITY_EFFORT", "high")

    effort = effort.lower()

    # 4. Extract context window usage percentage
    ctx_info = data.get("context_window") or {}
    pct = ctx_info.get("used_percentage")

    if pct is None or pct == 0:
        total_in = ctx_info.get("total_input_tokens") or 0
        total_out = ctx_info.get("total_output_tokens") or 0
        window_size = ctx_info.get("context_window_size") or 0
        if window_size > 0:
            pct = round((total_in + total_out) * 100 / window_size)
        else:
            pct = 0
    else:
        try:
            pct = round(float(pct))
        except (ValueError, TypeError):
            pct = 0

    # 5. Extract session title
    cid = (
        data.get("conversation_id")
        or data.get("session_id")
        or os.getenv("ANTIGRAVITY_CONVERSATION_ID", "")
    )

    title = ""

    # Strategy A: Read title quickly from annotations/<cid>.pbtxt
    if cid:
        pbtxt_path = os.path.join(app_data, "annotations", f"{cid}.pbtxt")
        if os.path.exists(pbtxt_path):
            try:
                with open(pbtxt_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    m = re.search(r'title:\s*"([^"]*)"', content)
                    if m:
                        title = m.group(1).strip()
            except Exception:
                pass

    # Strategy B: Query conversation_summaries.db database
    if cid and not title:
        db_path = os.path.join(app_data, "conversation_summaries.db")
        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                cur = conn.cursor()
                cur.execute(
                    "SELECT title FROM conversation_summaries WHERE conversation_id=? LIMIT 1",
                    (cid,)
                )
                row = cur.fetchone()
                if row and row[0]:
                    title = row[0]
                conn.close()
            except Exception:
                pass

    # Strategy C: Fallback to the latest active conversation title
    if not title:
        db_path = os.path.join(app_data, "conversation_summaries.db")
        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                cur = conn.cursor()
                cur.execute(
                    "SELECT title FROM conversation_summaries WHERE title != '' ORDER BY last_modified_time DESC LIMIT 1"
                )
                row = cur.fetchone()
                if row and row[0]:
                    title = row[0]
                conn.close()
            except Exception:
                pass

    if not title:
        title = "new session"
    else:
        title = title.strip().strip('"').strip("'")

    # 6. ANSI 256-color palette
    COLOR_CWD = "\033[38;5;75m"       # Light blue
    COLOR_THINK = "\033[38;5;114m"    # Mint green
    COLOR_CTX = "\033[38;5;214m"      # Amber yellow
    COLOR_TITLE = "\033[38;5;177m"    # Lavender / magenta
    COLOR_SEP = "\033[38;5;242m"      # Dim gray
    RESET = "\033[0m"

    sep = f" {COLOR_SEP}·{RESET} "

    # Format: /vol/workdir-SyzAster · medium · ctx 30% used · session title
    statusline = (
        f"{COLOR_CWD}{cwd}{RESET}"
        f"{sep}"
        f"{COLOR_THINK}{effort}{RESET}"
        f"{sep}"
        f"{COLOR_CTX}ctx {pct}% used{RESET}"
        f"{sep}"
        f"{COLOR_TITLE}{title}{RESET}"
    )

    return statusline

if __name__ == "__main__":
    print(get_statusline())
