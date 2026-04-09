import os
import re
import httpx
import logging
import json
import asyncio
from typing import Dict, Optional
from dotenv import load_dotenv

# ── Load Environment ─────────────────────────────────────────────────────────
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID", "").strip()
ALLOWED  = os.getenv("TELEGRAM_ALLOWED_USERNAME", "").strip().lstrip("@")
AGENT_URL = os.getenv("AGENT_GATEWAY_URL", "http://127.0.0.1:8000/invoke")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(name)-15s | %(message)s'
)
logger = logging.getLogger("SovereignBot")

# ── Regex patterns for receipt parsing ────────────────────────────────────────
_TXID_RE   = re.compile(r'\b([0-9a-f]{64})\b', re.IGNORECASE)
_ADDR_RE   = re.compile(r'(tb1p[a-z0-9]{39,})', re.IGNORECASE)
_AMOUNT_RE = re.compile(r'(\d[\d,]*)\s*sats?', re.IGNORECASE)
_MSG_RE    = re.compile(r"[Mm]essage[:\s*`]*['\"]?([^'\"\n`*]{1,80})['\"]?")
_ERR_RE    = re.compile(r'(PolicyViolation|TRAP|Error|Rejected|failed)', re.IGNORECASE)
_BAL_RE    = re.compile(r'[Bb]alance[:\s*]*([0-9,]+)\s*sats?')
_ALLOW_RE  = re.compile(r'[Aa]llowance[:\s*]*([0-9,]+)\s*sats?')

def _extract_txids(text: str):
    return list(dict.fromkeys(m.group(1).lower() for m in _TXID_RE.finditer(text)))

def _parse_structured_receipt(reply: str) -> Optional[dict]:
    m = re.search(r'<<<RECEIPT>>>(.*?)<<<END>>>', reply, re.DOTALL | re.IGNORECASE)
    if not m: return None
    block = m.group(1)
    result = {"txids": [], "outputs": [], "op_return": None, "balance": None}
    txid_m = re.search(r'TXID:\s*([0-9a-f ,]+)', block, re.IGNORECASE)
    if txid_m:
        result["txids"] = [t.strip().lower() for t in re.findall(r'[0-9a-f]{64}', txid_m.group(1))]
    out_m = re.search(r'OUTPUTS:\s*(.+)', block, re.IGNORECASE)
    if out_m:
        for part in out_m.group(1).split(','):
            kv = part.strip().split('=', 1)
            if len(kv) == 2:
                addr = kv[0].strip()
                amt_raw = re.sub(r'[^\d]', '', kv[1])
                if addr.startswith('tb1p') and amt_raw:
                    result["outputs"].append((addr, int(amt_raw)))
    op_m = re.search(r'OP_RETURN:\s*(.+)', block, re.IGNORECASE)
    if op_m:
        val = op_m.group(1).strip()
        result["op_return"] = None if val.upper() == "NONE" else val
    bal_m = re.search(r'BALANCE:\s*([\d,]+)', block, re.IGNORECASE)
    if bal_m: result["balance"] = bal_m.group(1).strip()
    return result if result["txids"] else None

def _build_receipt_html(reply: str, txids: list) -> tuple[str, Optional[dict]]:
    is_error = bool(_ERR_RE.search(reply))
    if is_error and not txids:
        clean = re.sub(r'[#*`_~]', '', reply).strip()[:900]
        return f"🚨 <b>ENCLAVE REJECTED</b>\n━━━━━━━━━━━━━━━━━━━━\n<pre>{clean}</pre>", None

    structured = _parse_structured_receipt(reply)
    if structured:
        canonical_txids = structured["txids"] or txids
        out_lines = "".join(f"  ├ <code>{a[:12]}…{a[-8:]}</code> → <b>{amt:,} sats</b>\n" for a, amt in structured["outputs"])
        op_return_msg = structured["op_return"]
        balance = structured["balance"]
    else:
        canonical_txids = txids
        addrs = _ADDR_RE.findall(reply)
        amounts = _AMOUNT_RE.findall(reply)
        unique_addrs = list(dict.fromkeys(addrs))
        out_lines = "".join(f"  ├ <code>{a[:12]}…{a[-8:]}</code> → <b>{amounts[i] if i < len(amounts) else '?'} sats</b>\n" for i, a in enumerate(unique_addrs))
        op_return_msg = _MSG_RE.search(reply).group(1).strip() if _MSG_RE.search(reply) else None
        balance = _BAL_RE.search(reply).group(1) if _BAL_RE.search(reply) else None

    txid_lines = "".join(f"<code>{t[:16]}…{t[-8:]}</code>\n" for t in canonical_txids)
    op_line = f"\n📝 <b>OP_RETURN:</b> <code>{op_return_msg}</code>" if op_return_msg else ""
    footer = f"\n\n💰 Balance: <code>{balance} sats</code>" if balance else ""

    html = f"🏛️ <b>SOVEREIGN RECEIPT</b>\n━━━━━━━━━━━━━━━━━━━━\n✅ <b>BROADCASTED</b>\n\n📤 <b>Outputs:</b>\n{out_lines}{op_line}\n\n🔗 <b>TXID(s):</b>\n{txid_lines}{footer}"
    buttons = [[{"text": "🔍 View on Mutinynet", "url": f"https://mutinynet.com/tx/{txid}"}] for txid in canonical_txids[:3]]
    return html, {"inline_keyboard": buttons} if buttons else None

class SovereignBot:
    def __init__(self):
        self.api_url = f"https://api.telegram.org/bot{TOKEN}"
        self.enabled = bool(TOKEN and CHAT_ID)

    async def send_message(self, text: str, reply_markup: dict = None):
        if not self.enabled: return
        payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_markup: payload["reply_markup"] = json.dumps(reply_markup)
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{self.api_url}/sendMessage", json=payload)

    async def send_photo(self, photo_path: str, caption: str = ""):
        if not (self.enabled and os.path.exists(photo_path)): return
        async with httpx.AsyncClient(timeout=30) as client:
            with open(photo_path, 'rb') as f:
                await client.post(f"{self.api_url}/sendPhoto", data={'chat_id': CHAT_ID, 'caption': caption, 'parse_mode': 'HTML'}, files={'photo': f})

    async def send_typing(self):
        if not self.enabled: return
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(f"{self.api_url}/sendChatAction", json={"chat_id": CHAT_ID, "action": "typing"})

    async def start_polling(self):
        if not self.enabled:
            logger.error("🚫 Bot disabled: TOKEN or CHAT_ID missing.")
            return
        
        logger.info(f"⏳ [BOOT] Sovereign Bot Hub active. Monitoring @{ALLOWED or 'ALL'}")
        offset = 0
        get_updates_url = f"{self.api_url}/getUpdates"

        async with httpx.AsyncClient(timeout=180) as client:
            while True:
                try:
                    resp = await client.get(get_updates_url, params={"offset": offset, "timeout": 30}, timeout=35)
                    updates = resp.json().get("result", [])
                    for u in updates:
                        offset = u["update_id"] + 1
                        msg = u.get("message", {})
                        text = msg.get("text")
                        cid  = msg.get("chat", {}).get("id")
                        user = msg.get("from", {}).get("username", "").lower().lstrip("@")

                        if str(cid) != str(CHAT_ID):
                            logger.warning(f"🚫 Unauthorized chat_id: {cid}")
                            continue
                        if ALLOWED and user != ALLOWED:
                            logger.warning(f"🚫 Unauthorized user: @{user}")
                            continue
                        if not text: continue

                        logger.info(f"📥 From @{user}: {text[:60]}...")
                        await self.send_typing()

                        try:
                            agent_resp = await client.post(AGENT_URL, json={"prompt": text}, timeout=180)
                            data = agent_resp.json()
                            reply = data.get("reply", "No response.")
                            txids = _extract_txids(reply)
                            if txids:
                                html, markup = _build_receipt_html(reply, txids)
                                await self.send_message(html, reply_markup=markup)
                                photo = data.get("photo")
                                if photo: await self.send_photo(photo, "📋 Governance Auth QR")
                            else:
                                clean = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', reply)
                                clean = re.sub(r'`([^`]+)`', r'<code>\1</code>', clean).replace('*', '')
                                await self.send_message(f"🏛️ <b>Sovereign Agent</b>\n━━━━━━━━━━━━━━━━━━━━\n\n{clean[:3800]}")
                        except Exception as e:
                            await self.send_message(f"🚨 <b>Agent Timeout</b>\n<code>{str(e)[:300]}</code>")

                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.error(f"❌ Polling error: {e}")
                    await asyncio.sleep(5)

if __name__ == "__main__":
    bot = SovereignBot()
    try:
        asyncio.run(bot.start_polling())
    except KeyboardInterrupt:
        logger.info("👋 Shutdown.")
