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

    # 3. Extract model name & thinking effort of CURRENT conversation
    # Priority: Read the active model of the CURRENT conversation from stdin first,
    # ensuring multi-session environments display each session's own model rather
    # than being overwritten by the globally shared settings.json.
    model_info = data.get("model")
    model_name = ""
    effort = ""

    if isinstance(model_info, dict):
        model_name = model_info.get("display_name") or model_info.get("id") or ""
        effort = model_info.get("effort") or ""
    elif isinstance(model_info, str):
        model_name = model_info

    if not effort:
        effort = (
            data.get("effort")
            or data.get("thinking_effort")
            or data.get("reasoning_effort")
            or ""
        )

    # Fallback to settings.json ONLY IF model_name is not provided via stdin (e.g. manual CLI invocation)
    if not model_name:
        settings_path = os.path.join(app_data, "settings.json")
        if os.path.exists(settings_path):
            try:
                with open(settings_path, "r", encoding="utf-8") as f:
                    s_data = json.load(f)
                    model_name = s_data.get("model", "")
                    if not effort:
                        effort = s_data.get("effort", "")
            except Exception:
                pass

    # Extract effort if embedded in model string like "(High)", "(Medium)", "(Thinking)"
    if model_name:
        m = re.search(r'\((Low|Medium|High|XHigh|Max|Thinking)\)', model_name, re.IGNORECASE)
        if m:
            effort = m.group(1)

    if not effort:
        effort = os.getenv("ANTIGRAVITY_EFFORT", "medium")

    effort = effort.lower()

    # Clean model display name (strip effort suffix like "(High)" / "(Medium)")
    model_display = re.sub(
        r'\s*\((Low|Medium|High|XHigh|Max|Thinking)\)', '', model_name, flags=re.IGNORECASE
    ).strip()
    if not model_display:
        model_display = "Gemini 3.8 Flash"

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
    pct = max(0, min(100, pct))

    # 5. Extract 5h and 7d quota usage percentages based on selected model
    quota_data = data.get("quota") or {}
    model_lower = model_display.lower()

    is_argon = "argon" in model_lower or "gemini 4" in model_lower or "gemini-4" in model_lower
    is_gemini = "gemini" in model_lower and not is_argon
    # 3P models: Claude, GPT, or any non-Gemini model

    b_5h = None
    b_7d = None

    if is_argon:
        # Argon uses gemini-argon-wtu-weekly for 7d/weekly quota
        b_7d = quota_data.get("gemini-argon-wtu-weekly")
        if not b_7d:
            for k, v in quota_data.items():
                if "argon" in k.lower():
                    b_7d = v
                    break
        # Argon shares gemini-5h for 5h quota unless a dedicated bucket exists
        b_5h = (
            quota_data.get("gemini-argon-5h")
            or quota_data.get("argon-5h")
            or quota_data.get("gemini-5h")
        )
    elif is_gemini:
        # Standard Gemini models (Gemini 3.x Flash, Gemini Pro, etc.)
        b_5h = quota_data.get("gemini-5h")
        b_7d = quota_data.get("gemini-weekly")
    else:
        # 3rd-party models (Claude, GPT, etc.)
        b_5h = quota_data.get("3p-5h")
        b_7d = quota_data.get("3p-weekly")

    # Generic fallbacks if target bucket not found directly
    if not b_5h:
        for k, v in quota_data.items():
            if "5h" in k.lower():
                b_5h = v
                break

    if not b_7d:
        for k, v in quota_data.items():
            if "week" in k.lower() or "7d" in k.lower():
                b_7d = v
                break

    pct_5h = round((1.0 - b_5h.get("remaining_fraction", 1.0)) * 100) if b_5h else 0
    pct_7d = round((1.0 - b_7d.get("remaining_fraction", 1.0)) * 100) if b_7d else 0
    pct_5h = max(0, min(100, pct_5h))
    pct_7d = max(0, min(100, pct_7d))

    # 6. Extract session title
    title = data.get("conversation_title") or ""

    cid = (
        data.get("conversation_id")
        or data.get("session_id")
        or os.getenv("ANTIGRAVITY_CONVERSATION_ID", "")
    )

    # Strategy A: Read title quickly from annotations/<cid>.pbtxt
    if not title and cid:
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
    if not title and cid:
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

    # 7. ANSI 256-color palette
    COLOR_CWD   = "\033[38;5;75m"       # Light blue
    COLOR_MODEL = "\033[38;5;111m"      # Soft sky blue
    COLOR_THINK = "\033[38;5;114m"      # Mint green
    COLOR_CTX   = "\033[38;5;214m"      # Amber yellow
    COLOR_Q5H   = "\033[38;5;79m"       # Sea green / aqua
    COLOR_Q7D   = "\033[38;5;216m"      # Soft peach / salmon
    COLOR_TITLE = "\033[38;5;177m"      # Lavender / magenta
    COLOR_SEP   = "\033[38;5;242m"      # Dim gray
    RESET       = "\033[0m"

    sep = f" {COLOR_SEP}·{RESET} "

    # Format:
    # cwd · model · effort · ctx · 5h X% used · 7d Y% used · title
    statusline = (
        f"{COLOR_CWD}{cwd}{RESET}"
        f"{sep}"
        f"{COLOR_MODEL}{model_display}{RESET}"
        f"{sep}"
        f"{COLOR_THINK}{effort}{RESET}"
        f"{sep}"
        f"{COLOR_CTX}ctx {pct}% used{RESET}"
        f"{sep}"
        f"{COLOR_Q5H}5h {pct_5h}% used{RESET}"
        f"{sep}"
        f"{COLOR_Q7D}7d {pct_7d}% used{RESET}"
        f"{sep}"
        f"{COLOR_TITLE}{title}{RESET}"
    )

    return statusline

if __name__ == "__main__":
    print(get_statusline())
