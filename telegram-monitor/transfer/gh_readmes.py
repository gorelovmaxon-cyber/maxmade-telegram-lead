"""Print the start of READMEs for selected repos (read-only research)."""
import base64
import json
import sys
import time
import urllib.request

REPOS = sys.argv[1:] or [
    "Leadgram-team/awesome-telegram-lead-generation", "PyRusAdmin/Parsing_Telegram_Message_GPT_bot",
    "ivucicev/redsignal", "eracle/OpenOutreach", "dancolta/subscope", "dannguyen9x/community-signal-radar-local",
    "getaero-io/gtm-signal-scoring", "mishapushka/tg-leads-parser", "TweetyDMG/LeadGrep", "kabirjaipal/knivik",
]


def call(url):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "maxmade-research"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


for repo in REPOS:
    print("\n########", repo)
    try:
        text = base64.b64decode(call(f"https://api.github.com/repos/{repo}/readme")["content"]).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        print("error", type(exc).__name__)
        continue
    lines = [l for l in text.splitlines() if l.strip() and not l.strip().startswith(("<img", "<p", "</p", "[![", "<a", "<source", "<picture", "</picture"))]
    print("\n".join(lines)[:2600])
    time.sleep(1)
