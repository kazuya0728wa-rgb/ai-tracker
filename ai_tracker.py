"""AI最新情報ダイジェスト — GitHub Actions版

情報収集:
  1位: DuckDuckGo site:x.com 検索（バズっているX投稿）
  2位: 公式ブログ（Anthropic / OpenAI / Google AI 等）
  3位: TechCrunch / The Verge RSS

Claude API で Top5 厳選・日本語要約 → Discord Embed + ボタン送信
"""

import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from openai import OpenAI
from duckduckgo_search import DDGS

# ── 設定 ──────────────────────────────────────────────────────────────────────
WEBHOOK          = os.environ["DISCORD_WEBHOOK"]
DEEPSEEK_KEY     = os.environ["DEEPSEEK_API_KEY"]
JST              = timezone(timedelta(hours=9))
ONE_DAY_AGO      = datetime.now(timezone.utc) - timedelta(days=1)
TOP_N            = 5

# ── 最優先サービス（普段使っているツール）──────────────────────────────────────
PRIORITY_SERVICES = [
    "Claude", "Claude Code", "ChatGPT", "Gemini", "Manus", "DeepSeek",
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


# ── Claude API で厳選・要約 ────────────────────────────────────────────────────
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
        max_tokens=1500,
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
def send_discord(payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode()
    req  = urllib.request.Request(WEBHOOK, data=data, headers=DISCORD_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"Discord: {r.status}")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        print(f"Discord ERROR {e.code}: {body}")
        # ボタンが原因の可能性 → Embedだけ再送
        if e.code == 400 and "components" in payload:
            print("ボタン除去して再送...")
            payload.pop("components", None)
            data2 = json.dumps(payload, ensure_ascii=False).encode()
            req2  = urllib.request.Request(WEBHOOK, data=data2, headers=DISCORD_HEADERS)
            with urllib.request.urlopen(req2, timeout=30) as r2:
                print(f"Discord (no buttons): {r2.status}")


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
    print(f"\n重複排除後: {len(all_items)}件 → Claude APIでTop{TOP_N}厳選中...")

    # Claude APIで厳選・要約
    top5 = curate_with_claude(all_items, now)
    print(f"厳選完了: {len(top5)}件")

    # Embed フィールド構築
    numbers = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧"]
    fields  = []
    for item in top5:
        n       = numbers[item["rank"] - 1]
        source  = item.get("source", "")
        fields.append({
            "name":   f"{n}  {item['headline']}",
            "value":  f"{item['summary']}\n*出典: {source}*",
            "inline": False,
        })

    # ボタン行（URL ボタン、最大5個）
    buttons = []
    for item in top5:
        url = item.get("url", "").strip()
        if url.startswith("http") and len(url) <= 512:
            rank = item["rank"]
            if rank <= len(numbers):
                buttons.append({
                    "type":  2,
                    "style": 5,
                    "label": f"{numbers[rank-1]} 詳細を見る",
                    "url":   url,
                })
    buttons = buttons[:5]  # Discord上限

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
