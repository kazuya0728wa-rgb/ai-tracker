/**
 * AI Tracker — Discord Interaction Handler (Cloudflare Worker)
 *
 * 「詳しく」ボタンがクリックされたら、詳細情報をエフェメラルメッセージで返す。
 * 環境変数: DISCORD_PUBLIC_KEY, GITHUB_REPO
 */

const INTERACTION_PING = 1;
const INTERACTION_COMPONENT = 3;
const RESPONSE_PONG = 1;
const RESPONSE_MESSAGE = 4;
const FLAG_EPHEMERAL = 64;

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("Method Not Allowed", { status: 405 });
    }

    // Discord 署名検証
    const { isValid, body } = await verifyRequest(request, env.DISCORD_PUBLIC_KEY);
    if (!isValid) {
      return new Response("Invalid signature", { status: 401 });
    }

    const interaction = JSON.parse(body);

    // PING（Discord がエンドポイント登録時に送信）
    if (interaction.type === INTERACTION_PING) {
      return jsonResponse({ type: RESPONSE_PONG });
    }

    // ボタンクリック
    if (interaction.type === INTERACTION_COMPONENT) {
      const customId = interaction.data.custom_id;

      if (customId.startsWith("detail_")) {
        const index = parseInt(customId.split("_")[1], 10);
        return await handleDetailButton(index, env);
      }
    }

    return new Response("Unknown interaction", { status: 400 });
  },
};

async function handleDetailButton(index, env) {
  try {
    // GitHub からニュース詳細を取得
    const rawUrl = `https://raw.githubusercontent.com/${env.GITHUB_REPO}/master/data/latest.json`;
    const resp = await fetch(rawUrl, {
      headers: { "User-Agent": "AITracker-Worker/1.0" },
      cf: { cacheTtl: 300 },
    });

    if (!resp.ok) {
      return ephemeral("詳細データの取得に失敗しました。");
    }

    const data = await resp.json();
    const item = data.details?.[index];

    if (!item) {
      return ephemeral("この記事の詳細情報が見つかりませんでした。");
    }

    return jsonResponse({
      type: RESPONSE_MESSAGE,
      data: {
        embeds: [
          {
            title: item.headline,
            description: item.detail,
            color: 7168255,
            fields: [
              {
                name: "出典",
                value: item.url ? `[${item.source || "リンク"}](${item.url})` : item.source || "不明",
                inline: true,
              },
            ],
            footer: { text: "📡 AI Tracker" },
          },
        ],
        flags: FLAG_EPHEMERAL,
      },
    });
  } catch (e) {
    return ephemeral(`エラーが発生しました: ${e.message}`);
  }
}

function ephemeral(text) {
  return jsonResponse({
    type: RESPONSE_MESSAGE,
    data: { content: text, flags: FLAG_EPHEMERAL },
  });
}

function jsonResponse(data) {
  return new Response(JSON.stringify(data), {
    headers: { "Content-Type": "application/json" },
  });
}

// ── Ed25519 署名検証 ────────────────────────────────────────────────────────
async function verifyRequest(request, publicKey) {
  const signature = request.headers.get("x-signature-ed25519");
  const timestamp = request.headers.get("x-signature-timestamp");
  const body = await request.text();

  if (!signature || !timestamp) {
    return { isValid: false, body };
  }

  try {
    const key = await crypto.subtle.importKey(
      "raw",
      hexToUint8Array(publicKey),
      { name: "NODE-ED25519", namedCurve: "NODE-ED25519" },
      true,
      ["verify"]
    );

    const isValid = await crypto.subtle.verify(
      "NODE-ED25519",
      key,
      hexToUint8Array(signature),
      new TextEncoder().encode(timestamp + body)
    );

    return { isValid, body };
  } catch {
    return { isValid: false, body };
  }
}

function hexToUint8Array(hex) {
  return new Uint8Array(hex.match(/.{1,2}/g).map((b) => parseInt(b, 16)));
}
