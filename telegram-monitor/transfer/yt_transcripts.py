"""Fetch YouTube transcripts via the installed Chrome (Playwright, channel=chrome). Read-only.

python yt_transcripts.py <videos.json> <out.json> [limit]
Opens each video page, reads the caption track list from the page and downloads
the transcript through the page itself (same cookies), so YouTube's bot check
for plain HTTP clients does not apply.
"""
import asyncio
import html
import json
import re
import sys

from playwright.async_api import async_playwright

JS = r"""
async () => {
  const pr = window.ytInitialPlayerResponse || {};
  const tracks = (((pr.captions || {}).playerCaptionsTracklistRenderer || {}).captionTracks) || [];
  if (!tracks.length) return {tracks: 0, text: ""};
  const t = tracks.find(x => x.languageCode === "ru") || tracks[0];
  const r = await fetch(t.baseUrl + "&fmt=json3");
  const j = await r.json();
  const text = (j.events || []).map(e => (e.segs || []).map(s => s.utf8).join("")).join(" ");
  return {tracks: tracks.length, lang: t.languageCode, text};
}
"""


async def main(src, out, limit):
    videos = json.load(open(src, encoding="utf-8"))[:limit]
    result = {}
    try:
        result = json.load(open(out, encoding="utf-8"))
    except (OSError, ValueError):
        pass
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page(locale="ru-RU")
        for i, v in enumerate(videos):
            if v["id"] in result and result[v["id"]].get("text"):
                continue
            try:
                await page.goto(f"https://www.youtube.com/watch?v={v['id']}&hl=ru", timeout=45000)
                await page.wait_for_timeout(2500)
                data = await page.evaluate(JS)
                desc = await page.evaluate("() => ((window.ytInitialPlayerResponse||{}).videoDetails||{}).shortDescription || ''")
                data["text"] = re.sub(r"\s+", " ", html.unescape(data.get("text", ""))).strip()
                data["description"] = desc
            except Exception as exc:  # noqa: BLE001
                data = {"error": type(exc).__name__, "text": ""}
            result[v["id"]] = {"title": v["title"], **data}
            print(f"{i + 1}/{len(videos)} {v['id']} tr={len(data.get('text', ''))} {data.get('error', '')}", flush=True)
            json.dump(result, open(out, "w", encoding="utf-8"), ensure_ascii=False)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 1000))
