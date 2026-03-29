"""AI最新情報ダイジェスト — GitHub Actions版（統合版）

情報収集:
  1位: DuckDuckGo site:x.com 検索（バズっているX投稿）
  2位: 公式ブログ（Anthropic / OpenAI / Google AI 等）
  3位: TechCrunch / The Verge RSS

差分検出（SHA256ハッシュ）で既出ニュースを除外
DeepSeek API で Top5 厳選・日本語要約 → Discord Embed + ボタン送信
"""

import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from openai import OpenAI
from duckduckgo_search import DDGS

# ── 設定 ──────────────────────────────────────────────────────────────────────
WEBHOOK          = os.environ["DISCORD_WEBHOOK"]
BOT_TOKEN        = os.environ.get("DISCORD_BOT_TOKEN", "")
CHANNEL_ID       = os.environ.get("DISCORD_CHANNEL_ID", "")
DEEPSEEK_KEY     = os.environ["DEEPSEEK_API_KEY"]
DATA_DIR         = os.path.join(os.path.dirname(__file__), "data")
HISTORY_FILE     = os.path.join(DATA_DIR, "history.json")
HISTORY_RETENTION_DAYS = 30
JST              = timezone(timedelta(hours=9))
ONE_DAY_AGO      = datetime.now(timezone.utc) - timedelta(days=1)
TOP_N            = 5

# ── 最優先サービス（普段使っているツール）──────────────────────────────────────
PRIORITY_SERVICES = [
    "Claude", "Claude Code", "ChatGPT", "Gemini", "Manus", "DeepSeek",
]

# ── カテゴリ定義 ──────────────────────────────────────────────────────────────
CATEGORIES = [
    {"name": "LLM",       "emoji": "\U0001f9e0",
     "services": ["Claude", "ChatGPT", "GPT-5", "GPT-4o", "Gemini", "Grok",
                   "DeepSeek", "Llama", "Mistral", "Command R", "Phi", "Qwen"]},
    {"name": "コーディング", "emoji": "\U0001f4bb",
     "services": ["GitHub Copilot", "Claude Code", "Cursor", "Windsurf",
                   "Codeium", "Devin", "Replit Agent", "Bolt", "v0"]},
    {"name": "画像/動画",  "emoji": "\U0001f3a8",
     "services": ["Midjourney", "DALL-E", "Stable Diffusion", "Imagen",
                   "Adobe Firefly", "Flux", "Sora", "Veo", "Runway", "Kling", "Pika"]},
    {"name": "音声",       "emoji": "\U0001f3b5",
     "services": ["ElevenLabs", "Suno", "Udio", "NotebookLM", "Whisper"]},
    {"name": "新規/その他", "emoji": "\U0001f195", "services": []},
]

DISCORD_HEADERS  = {
    "Content-Type": "application/json",
    "User-Agent":   "DiscordBot (https://example.com, 1.0)",
}
WEB_HEADERS      = {
    "User-Agent": "Mozilla/5.0 (compatible; AITracker/1.0)"
}

# ── 1位: X(Twitter) 検索クエリ ────────────────────────────────────────────────
X_QUERIES = [
    # 最優先: 普段使っているサービス
    'site:x.com "Claude Code" OR "claude code" new OR update OR release OR feature',
    'site:x.com @AnthropicAI OR "Claude" new OR update OR release 2026',
    'site:x.com ChatGPT new OR update OR feature OR release 2026',
    'site:x.com Gemini Google AI new OR update OR release 2026',
    'site:x.com Manus AI agent OR update OR release 2026',
    'site:x.com DeepSeek new OR update OR model OR release 2026',
    # 一般AI
    'site:x.com AI "just released" OR "just launched" OR "announcing"',
    'site:x.com AI "game changer" OR "breakthrough" OR "mind-blowing"',
    'site:x.com 生成AI リリース OR 公開 OR 新機能',
]

# ── 2位: 公式ブログ ────────────────────────────────────────────────────────────
OFFICIAL_BLOGS = [
    ("Anthropic",  "https://www.anthropic.com/news"),
    ("OpenAI",     "https://openai.com/blog"),
    ("Google AI",  "https://blog.google/technology/ai/"),
    ("xAI",        "https://x.ai/blog"),
    ("Mistral",    "https://mistral.ai/news/"),
    ("Meta AI",    "https://ai.meta.com/blog/"),
]

# ── 3位: RSS フィード ──────────────────────────────────────────────────────────
RSS_FEEDS = [
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/rss/index.xml",
    "https://news.google.com/rss/search?q=" + urllib.parse.quote("AI new model release 2026") + "&hl=en-US&gl=US&ceid=US:en",
]


# ── 収集ユーティリティ ─────────────────────────────────────────────────────────
def search_x(query: str, max_results: int = 8) -> list[dict]:
    """DuckDuckGo で site:x.com を検索。"""
    try:
        results = DDGS().text(query, max_results=max_results)
        items = []
        for r in (results or []):
            items.append({
                "title":  r.get("title", "").strip(),
                "url":    r.get("href", "").strip(),
                "body":   r.get("body", "").strip(),
                "source": "X (Twitter)",
                "priority": 1,
            })
        return items
    except Exception as e:
        print(f"[DDG SKIP] {query[:50]}... → {e}")
        return []


def scrape_blog(name: str, url: str) -> list[dict]:
    """公式ブログの記事タイトル・URLを取得（簡易スクレイピング）。"""
    try:
        req = urllib.request.Request(url, headers=WEB_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        # <a href="...">タイトル</a> を雑に抽出
        pattern = r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>\s*([^<]{10,120})\s*</a>'
        links = re.findall(pattern, html)
        items = []
        for href, title in links[:15]:
            title = re.sub(r'\s+', ' ', title).strip()
            if len(title) < 10:
                continue
            full_url = href if href.startswith("http") else urllib.parse.urljoin(url, href)
            items.append({
                "title":    title,
                "url":      full_url,
                "body":     "",
                "source":   name,
                "priority": 2,
            })
        return items
    except Exception as e:
        print(f"[BLOG SKIP] {name} → {e}")
        return []


def fetch_rss(url: str) -> list[dict]:
    """RSS フィードを取得。"""
    try:
        req = urllib.request.Request(url, headers=WEB_HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
    except Exception as e:
        print(f"[RSS SKIP] {url[:50]}... → {e}")
        return []

    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link  = (item.findtext("link")  or "").strip()
        pub   = item.findtext("pubDate") or ""
        try:
            pub_dt = parsedate_to_datetime(pub).astimezone(timezone.utc)
        except Exception:
            pub_dt = datetime.now(timezone.utc)
        if title and pub_dt >= ONE_DAY_AGO:
            items.append({
                "title":    title,
                "url":      link,
                "body":     (item.findtext("description") or "")[:200],
                "source":   url.split("/")[2],
                "priority": 3,
            })
    return items


def deduplicate(items: list[dict]) -> list[dict]:
    seen, result = set(), []
    for item in items:
        key = re.sub(r"[^\w]", "", item["title"].lower())[:40]
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result


# ── 差分検出（履歴ベース） ────────────────────────────────────────────────────
def _make_hash(title: str, url: str = "") -> str:
    """ニュースアイテムの一意ハッシュキー"""
    normalized = re.sub(r"[^\w\s]", "", title.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    domain = ""
    try:
        domain = urlparse(url).netloc
    except Exception:
        pass
    key = normalized + domain
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def load_history() -> dict:
    if not os.path.exists(HISTORY_FILE):
        return {"last_run": None, "items": {}}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"last_run": None, "items": {}}


def save_history(history: dict) -> None:
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def filter_new_items(items: list[dict], history: dict) -> list[dict]:
    """既出ニュースを除外し、新規のみ返す。historyも更新する。"""
    now = datetime.now(JST).isoformat()
    new_items = []
    for item in items:
        h = _make_hash(item.get("title", ""), item.get("url", ""))
        if h in history["items"]:
            history["items"][h]["last_seen"] = now
        else:
            history["items"][h] = {
                "title": item.get("title", ""),
                "first_seen": now,
                "last_seen": now,
            }
            new_items.append(item)
    history["last_run"] = now
    return new_items


def cleanup_history(history: dict) -> int:
    """古いアイテムを削除。削除件数を返す。"""
    cutoff = (datetime.now(JST) - timedelta(days=HISTORY_RETENTION_DAYS)).isoformat()
    to_remove = [h for h, item in history["items"].items()
                 if item.get("last_seen", "") < cutoff]
    for h in to_remove:
        del history["items"][h]
    return len(to_remove)


# ── DeepSeek API で厳選・要約 ─────────────────────────────────────────────────
def curate_with_claude(items: list[dict], now: datetime) -> list[dict]:
    """Claude API に候補を渡してTop5を選定・日本語要約させる。"""

    # 候補テキスト生成（優先度順）
    candidates = ""
    for i, item in enumerate(items[:60], 1):
        body = f"\n   概要: {item['body'][:150]}" if item["body"] else ""
        candidates += (
            f"{i}. [{item['source']}] {item['title']}\n"
            f"   URL: {item['url']}{body}\n\n"
        )

    prompt = f"""あなたはAI業界のトレンドを追うプロのキュレーターです。
今日は {now.strftime('%Y年%m月%d日')} です。

以下はAI関連ニュースの候補リストです（優先度: X投稿 > 公式ブログ > メディア記事）。

{candidates}

この中から **最も重要なニュースをTop{TOP_N}件** 選び、以下のJSON形式のみで回答してください。
コードブロック・説明文は不要です。JSONだけ出力してください。

[
  {{
    "rank": 1,
    "headline": "見出し（日本語・35文字以内）",
    "summary": "要約（日本語・2〜3文・60〜100文字）",
    "detail": "詳細解説（日本語・5〜8文・200〜400文字。背景・影響・技術的なポイントを含む）",
    "url": "元記事URL（候補リストのURLをそのまま使用）",
    "source": "情報源名"
  }}
]

選定基準（優先度順）:
1. 【最優先】Claude / Claude Code / ChatGPT / Gemini / Manus / DeepSeek に関するニュースは、小さなアップデートでも必ず含める
2. X公式アカウントの発表・バズ投稿を優先
3. 新モデル・新機能・価格変更など実際の行動に影響するニュース
4. 同じニュースの重複は1件にまとめる
5. 日本語圏のAI実務者にとって有益かどうかで判断

上記サービスのニュースが5件以上あれば、すべてそれで埋めてよい。"""

    client = OpenAI(api_key=DEEPSEEK_KEY, base_url="https://api.deepseek.com")
    resp = client.chat.completions.create(
        model="deepseek-chat",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.choices[0].message.content.strip()

    # JSONパース
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # コードブロックが含まれていた場合の fallback
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise


# ── Discord 送信 ───────────────────────────────────────────────────────────────
def send_via_bot(payload: dict) -> None:
    """Bot APIでインタラクティブボタン付きメッセージを送信。"""
    url  = f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages"
    hdrs = {
        "Authorization":  f"Bot {BOT_TOKEN}",
        "Content-Type":   "application/json",
        "User-Agent":     "DiscordBot (https://example.com, 1.0)",
    }
    data = json.dumps(payload, ensure_ascii=False).encode()
    req  = urllib.request.Request(url, data=data, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"Discord Bot API: {r.status}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        print(f"Discord Bot ERROR {e.code}: {body}")
        if e.code == 400 and "components" in payload:
            print("ボタン除去して再送...")
            payload.pop("components", None)
            send_via_bot(payload)


def send_via_webhook(payload: dict) -> None:
    """Webhook でフォールバック送信（ボタンなし）。"""
    payload.pop("components", None)
    data = json.dumps(payload, ensure_ascii=False).encode()
    req  = urllib.request.Request(WEBHOOK, data=data, headers=DISCORD_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"Discord Webhook: {r.status}")


def send_discord(payload: dict) -> None:
    """Bot API 優先、失敗時は Webhook フォールバック。"""
    if BOT_TOKEN and CHANNEL_ID:
        send_via_bot(payload)
    else:
        print("BOT_TOKEN/CHANNEL_ID 未設定 → Webhookで送信（ボタンなし）")
        send_via_webhook(payload)


def save_details(top_items: list[dict]) -> None:
    """詳細データを data/latest.json に保存（Worker が参照する）。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    out = {"details": top_items}
    path = os.path.join(DATA_DIR, "latest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"詳細データ保存: {path}")


# ── メイン ─────────────────────────────────────────────────────────────────────
def main():
    now      = datetime.now(JST)
    next_run = (now + timedelta(days=1)).strftime("%Y-%m-%d 09:00 JST")
    print(f"収集開始: {now.strftime('%Y-%m-%d %H:%M JST')}")

    all_items: list[dict] = []

    # 1位: X検索
    print("── X(Twitter) 検索 ──")
    for q in X_QUERIES:
        results = search_x(q, max_results=8)
        print(f"  {len(results)}件 ← {q[:55]}")
        all_items.extend(results)
        time.sleep(1)  # DDG レート制限対策

    # 2位: 公式ブログ
    print("── 公式ブログ ──")
    for name, url in OFFICIAL_BLOGS:
        results = scrape_blog(name, url)
        print(f"  {len(results)}件 ← {name}")
        all_items.extend(results)

    # 3位: RSS
    print("── RSS ──")
    for url in RSS_FEEDS:
        results = fetch_rss(url)
        print(f"  {len(results)}件 ← {url[:50]}")
        all_items.extend(results)

    all_items = deduplicate(all_items)
    print(f"\n重複排除後: {len(all_items)}件")

    # 差分検出: 既出ニュースを除外
    history = load_history()
    new_items = filter_new_items(all_items, history)
    removed = cleanup_history(history)
    save_history(history)
    print(f"差分検出: {len(new_items)}件が新規 / {len(all_items) - len(new_items)}件が既出 / {removed}件を履歴から削除")

    if not new_items:
        print("新規ニュースなし → 送信スキップ")
        return

    print(f"→ DeepSeek APIでTop{TOP_N}厳選中...")

    # DeepSeek APIで厳選・要約・詳細生成
    top5 = curate_with_claude(new_items, now)
    print(f"厳選完了: {len(top5)}件")

    # 詳細データを保存（Cloudflare Worker が参照）
    save_details(top5)

    # Embed フィールド構築
    numbers = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧"]
    fields  = []
    for item in top5:
        n      = numbers[item["rank"] - 1]
        source = item.get("source", "")
        fields.append({
            "name":   f"{n}  {item['headline']}",
            "value":  f"{item['summary']}\n*出典: {source}*",
            "inline": False,
        })

    # インタラクティブボタン（「詳しく」→ Worker が応答）
    buttons = []
    for item in top5:
        rank = item["rank"]
        if rank <= len(numbers):
            buttons.append({
                "type":      2,
                "style":     1,  # Primary (blurple)
                "label":     f"{numbers[rank-1]} 詳しく",
                "custom_id": f"detail_{rank - 1}",
            })
    buttons = buttons[:5]

    payload = {
        "embeds": [{
            "title":       "📡 AI最新情報ダイジェスト",
            "description": f"{now.strftime('%Y-%m-%d')}  ·  厳選 **{len(top5)}件** / 収集 {len(all_items)}件中",
            "color":       7168255,
            "fields":      fields,
            "footer":      {"text": f"次回: {next_run}"},
            "timestamp":   now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }],
        "components": [{"type": 1, "components": buttons}] if buttons else [],
    }

    send_discord(payload)
    print("完了")


if __name__ == "__main__":
    main()
