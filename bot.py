import logging
import requests
from telegram.ext import Application, ContextTypes
from telegram.constants import ParseMode

# --- KONFIGURASI ---
TOKEN_BOT = "8843649809:AAGmXnDcyfJNqQbMZ8go83PfkCrt664qoyc"
ID_CHANNEL = "@ratetonusdtidr"  # atau -100xxxxxxxxxx
INTERVAL_DETIK = 60

# --- API COINGECKO ---
URL_API = "https://api.coingecko.com/api/v3/simple/price"
PARAMS = {
    "ids": "the-open-network,tether",
    "vs_currencies": "idr",
    "include_24hr_change": "true"  # bonus: perubahan 24 jam
}

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# State harga terakhir untuk menentukan emoji naik/turun
harga_terakhir = {"ton": None, "usdt": None}


def ambil_harga():
    """Ambil harga TON & USDT dalam IDR dari CoinGecko."""
    try:
        r = requests.get(URL_API, params=PARAMS, timeout=10)
        r.raise_for_status()
        data = r.json()
        return {
            "ton": data.get("the-open-network", {}),
            "usdt": data.get("tether", {})
        }
    except Exception as e:
        logger.error(f"Gagal ambil data CoinGecko: {e}")
        return None


def emoji_arah(harga_baru, harga_lama):
    """Tentukan emoji berdasarkan pergerakan harga."""
    if harga_lama is None:
        return "⚪"
    if harga_baru > harga_lama:
        return "🟢"
    if harga_baru < harga_lama:
        return "🔴"
    return "⚪"


def format_perubahan(persen):
    """Format persentase 24 jam."""
    if persen is None:
        return ""
    tanda = "+" if persen >= 0 else ""
    return f" ({tanda}{persen:.2f}% 24j)"


async def kirim_update(context: ContextTypes.DEFAULT_TYPE):
    """Job yang dijalankan setiap interval."""
    global harga_terakhir
    data = ambil_harga()
    if data is None:
        return

    ton = data["ton"]
    usdt = data["usdt"]
    harga_ton = ton.get("idr")
    harga_usdt = usdt.get("idr")

    if harga_ton is None or harga_usdt is None:
        return

    emoji_ton = emoji_arah(harga_ton, harga_terakhir["ton"])
    emoji_usdt = emoji_arah(harga_usdt, harga_terakhir["usdt"])

    chg_ton = format_perubahan(ton.get("idr_24h_change"))
    chg_usdt = format_perubahan(usdt.get("idr_24h_change"))

    pesan = (
        f"📊 <b>Update Harga Crypto</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{emoji_ton} <b>TON / IDR</b>\n"
        f"    Rp {harga_ton:,.0f}{chg_ton}\n\n"
        f"{emoji_usdt} <b>USDT / IDR</b>\n"
        f"    Rp {harga_usdt:,.0f}{chg_usdt}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ Update otomatis tiap {INTERVAL_DETIK} detik\n"
        f"📈 Sumber: CoinGecko"
    )

    try:
        await context.bot.send_message(
            chat_id=ID_CHANNEL,
            text=pesan,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
        harga_terakhir["ton"] = harga_ton
        harga_terakhir["usdt"] = harga_usdt
        logger.info(f"Terkirim: TON={harga_ton:,.0f} | USDT={harga_usdt:,.0f}")
    except Exception as e:
        logger.error(f"Gagal kirim ke channel: {e}")


def main():
    app = Application.builder().token(TOKEN_BOT).build()

    # Jadwalkan job, TIDAK ada handler pesan user
    app.job_queue.run_repeating(
        kirim_update,
        interval=INTERVAL_DETIK,
        first=5  # kirim pertama setelah 5 detik
    )

    logger.info("Bot channel-only berjalan...")
    # allowed_updates=[] → bot tidak menerima update dari chat pribadi/group
    app.run_polling(allowed_updates=[])