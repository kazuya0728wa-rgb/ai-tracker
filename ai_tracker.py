"""AI最新情報ダイジェスト — GitHub Actions用スタンドアロン版

RSS + Google News で直近2日のAIニュースを収集してDiscordに送信する。
依存ライブラリなし（stdlib のみ）。
"""

import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# ── 設定 ──────────────────────────────────────────────────────────────────────
WEBHOOK = os.environ["DISCORD_WEBHOOK"]
JST = timezone(timedelta(hours=9))
TWO_DAYS_AGO = datetime.now(timezone.utc) - timedelta(days=2)

DISCORD_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "DiscordBot (https://example.com, 1.0)",
}
WEB_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AITracker/1.0; +https://github.com)"}

# ── RSS フィード一覧 ───────────────────────────────────────────────────────────
RSS_FEEDS = [
    # Google News（英語）
    "https://news.google.com/rss/search?q=AI+new+model+launched+OR+released+OR+announced&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=Claude+OR+ChatGPT+OR+Gemini+OR+Grok+OR+DeepSeek+update+OR+release&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=Cursor+OR+Windsurf+OR+Devin+OR+Copilot+AI+coding&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=Midjourney+OR+Sora+OR+Runway+OR+Kling+OR+ElevenLabs+new&hl=en-US&gl=US&ceid=US:en",
    # Google News（日本語）—— クエリ部分をURLエンコード
    "https://news.google.com/rss/search?q=" + urllib.parse.quote("生成AI リリース OR 新機能 OR 公開") + "&hl=ja&gl=JP&ceid=JP:ja",
    "https://news.google.com/rss/search?q=" + urllib.parse.quote("AI ツール 新サービス 2026") + "&hl=ja&gl=JP&ceid=JP:ja",
    # テックメディア
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.theverge.com/rss/index.xml",
]

# ── カテゴリ定義 ───────────────────────────────────────────────────────────────
CATEGORIES = {
    ("🧠", "LLM"): [
        "claude", "chatgpt", "gpt-5", "gpt-4o", "gemini", "grok", "deepseek",
        "llama", "mistral", "qwen", "openai", "anthropic", "google ai", "xai",
        "large language model", "llm",
    ],
    ("💻", "コーディング"): [
        "github copilot", "claude code", "cursor", "windsurf", "codeium",
        "devin", "replit", "bolt.new", " v0 ", "coding ai", "code generation",
        "agentic coding",
    ],
    ("🎨", "画像/動画"): [
        "midjourney", "dall-e", "stable diffusion", "flux", "sora", "veo",
        "runway", "kling", "pika", "imagen", "firefly", "image generation",
        "video generation",
    ],
    ("🎵", "音声"): [
        "elevenlabs", "suno", "udio", "notebooklm", "whisper",
        "voice ai", "music ai", "audio ai", "text to speech",
    ],
}


# ── ユーティリティ ─────────────────────────────────────────────────────────────
def fetch_rss(url: str) -> list[dict]:
    """RSS/Atom フィードを取得してアイテムリストを返す。"""
    try:
        req = urllib.request.Request(url, headers=WEB_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
    except Exception as e:
        print(f"[SKIP] {url[:60]}... → {e}")
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = []

    # RSS 2.0
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = item.findtext("pubDate") or ""
        try:
            pub_dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
        except Exception:
            pub_dt = datetime.now(timezone.utc)
        if title and pub_dt >= TWO_DAYS_AGO:
            items.append({"title": title, "url": link, "date": pub_dt})

    # Atom
    for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
        title = (entry.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
        link_el = entry.find("{http://www.w3.org/2005/Atom}link")
        link = (link_el.get("href") or "") if link_el is not None else ""
        pub = entry.findtext("{http://www.w3.org/2005/Atom}updated") or ""
        try:
            pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
        except Exception:
            pub_dt = datetime.now(timezone.utc)
        if title and pub_dt >= TWO_DAYS_AGO:
            items.append({"title": title, "url": link, "date": pub_dt})

    return items


def categorize(title: str) -> tuple[str, str] | None:
    """タイトルからカテゴリを判定。該当なしは None。"""
    t = title.lower()
    for (emoji, name), keywords in CATEGORIES.items():
        if any(kw in t for kw in keywords):
            return emoji, name
    return None


def deduplicate(items: list[dict]) -> list[dict]:
    """タイトルの正規化で重複排除。"""
    seen = set()
    result = []
    for item in items:
        key = re.sub(r"[^\w]", "", item["title"].lower())[:40]
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def send_discord(content: str) -> None:
    """Discordに送信（2000文字超えは分割）。"""
    chunks = [content[i:i+1990] for i in range(0, len(content), 1990)]
    for chunk in chunks:
        data = json.dumps({"content": chunk}).encode()
        req = urllib.request.Request(WEBHOOK, data=data, headers=DISCORD_HEADERS)
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"Discord: {r.status}")


# ── メイン ─────────────────────────────────────────────────────────────────────
def main():
    now = datetime.now(JST)
    next_run = (now + timedelta(days=2)).strftime("%Y-%m-%d 09:00 JST")

    print(f"収集開始: {now.strftime('%Y-%m-%d %H:%M JST')}")

    # フィード取得
    all_items = []
    for url in RSS_FEEDS:
        items = fetch_rss(url)
        print(f"  {len(items)}件 ← {url[:60]}")
        all_items.extend(items)

    all_items = deduplicate(all_items)
    print(f"重複排除後: {len(all_items)}件")

    # カテゴリ分類
    buckets: dict[str, list] = {
        "🧠 LLM": [], "💻 コーディング": [], "🎨 画像/動画": [],
        "🎵 音声": [], "🆕 その他": [],
    }
    for item in all_items:
        cat = categorize(item["title"])
        if cat:
            key = f"{cat[0]} {cat[1]}"
            buckets[key].append(item)
        else:
            buckets["🆕 その他"].append(item)

    # Discord メッセージ構築
    header = f"📡 **AI最新情報ダイジェスト** — {now.strftime('%Y-%m-%d')}\n"
    body_lines = []

    total = 0
    for label, items in buckets.items():
        if not items:
            continue
        body_lines.append(f"\n**{label}**")
        for item in items[:8]:  # カテゴリ上限8件
            title = item["title"][:80]
            url = item["url"]
            body_lines.append(f"• [{title}]({url})")
            total += 1

    footer = f"\n\n📊 合計 {total} 件 | 次回: {next_run}"

    if total == 0:
        message = header + "\n（直近2日間の新着ニュースはありませんでした）" + footer
    else:
        message = header + "\n".join(body_lines) + footer

    send_discord(message)
    print("完了")


if __name__ == "__main__":
    main()
