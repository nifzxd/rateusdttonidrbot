import os
import json
import math
import asyncio
import logging
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from telegram import LinkPreviewOptions
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError
from telegram.ext import Application, ContextTypes

# --- KONFIGURASI ---
BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("BOT_CONFIG", BASE_DIR / "config.json"))

with CONFIG_PATH.open(encoding="utf-8") as f:
    CONFIG = json.load(f)

TOKEN_BOT = CONFIG.get("bot_token", "")
ID_CHANNEL = CONFIG["channel_id"]
INTERVAL_DETIK = int(CONFIG["interval_detik"])
KIRIM_HANYA_JIKA_BERUBAH = bool(CONFIG["kirim_hanya_jika_berubah"])
ZONA_WAKTU = ZoneInfo(CONFIG["zona_waktu"])
TIMEOUT_HTTP = float(CONFIG["timeout_http"])
MAKS_PERCOBAAN = int(CONFIG["maks_percobaan"])

COIN_TON = CONFIG["coins"]["ton"]
COIN_USDT = CONFIG["coins"]["usdt"]
VS_CURRENCY = CONFIG["vs_currency"]

if not TOKEN_BOT or TOKEN_BOT.startswith("ISI_TOKEN"):
    raise SystemExit("bot_token di config.json belum diisi.")

# --- API COINGECKO ---
URL_API = "https://api.coingecko.com/api/v3/simple/price"
PARAMS = {
    "ids": f"{COIN_TON},{COIN_USDT}",
    "vs_currencies": VS_CURRENCY,
    "include_24hr_change": "true",
}

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def _valid_harga(x) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x) and x > 0


def _valid_persen(x) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


async def ambil_harga(client: httpx.AsyncClient):
    """Ambil harga TON & USDT dari CoinGecko, dengan retry & backoff."""
    for percobaan in range(1, MAKS_PERCOBAAN + 1):
        try:
            r = await client.get(URL_API, params=PARAMS)
            if r.status_code == 429:
                logger.warning("CoinGecko rate limit (429), tunggu sebentar...")
                await asyncio.sleep(10 * percobaan)
                continue
            r.raise_for_status()
            data = r.json()

            ton = data.get(COIN_TON, {})
            usdt = data.get(COIN_USDT, {})
            harga_ton = ton.get(VS_CURRENCY)
            harga_usdt = usdt.get(VS_CURRENCY)

            if not (_valid_harga(harga_ton) and _valid_harga(harga_usdt)):
                raise ValueError(f"Data tidak valid: {data}")

            return {
                "ton": {"harga": harga_ton, "chg": ton.get(f"{VS_CURRENCY}_24h_change")},
                "usdt": {"harga": harga_usdt, "chg": usdt.get(f"{VS_CURRENCY}_24h_change")},
            }
        except (httpx.HTTPError, ValueError) as e:
            logger.warning(f"Percobaan {percobaan}/{MAKS_PERCOBAAN} gagal: {e}")
            await asyncio.sleep(2 * percobaan)

    return None


def emoji_arah(harga_baru, harga_lama):
    """Emoji berdasarkan pergerakan harga dibanding update terakhir yang berhasil terkirim."""
    if harga_lama is None:
        return "⚪"
    if harga_baru > harga_lama:
        return "🟢"
    if harga_baru < harga_lama:
        return "🔴"
    return "⚪"


def format_rupiah(nilai: float) -> str:
    """Format rupiah gaya Indonesia: Rp 16.250"""
    return "Rp " + f"{nilai:,.0f}".replace(",", ".")


def format_perubahan(persen) -> str:
    """Format persentase 24 jam."""
    if not _valid_persen(persen):
        return ""
    tanda = "+" if persen >= 0 else ""
    return f" ({tanda}{persen:.2f}% 24j)"


def build_pesan(harga_ton, harga_usdt, ton, usdt, terakhir) -> str:
    waktu = dt.datetime.now(ZONA_WAKTU).strftime("%d-%m-%Y %H:%M:%S WIB")
    return (
        f"📊 <b>Update Harga Crypto</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{emoji_arah(harga_ton, terakhir.get('ton'))} <b>TON / IDR</b>\n"
        f"    {format_rupiah(harga_ton)}{format_perubahan(ton['chg'])}\n\n"
        f"{emoji_arah(harga_usdt, terakhir.get('usdt'))} <b>USDT / IDR</b>\n"
        f"    {format_rupiah(harga_usdt)}{format_perubahan(usdt['chg'])}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🕒 {waktu}\n"
        f"⏱️ Update otomatis tiap {INTERVAL_DETIK} detik\n"
        f"📈 Sumber: CoinGecko"
    )


async def kirim_update(context: ContextTypes.DEFAULT_TYPE):
    """Job yang dijalankan setiap interval."""
    data = await ambil_harga(context.bot_data["http"])
    if data is None:
        logger.error("Update dilewati: data harga tidak tersedia")
        return

    harga_ton = data["ton"]["harga"]
    harga_usdt = data["usdt"]["harga"]
    terakhir = context.bot_data.get("terakhir", {})

    if (
        KIRIM_HANYA_JIKA_BERUBAH
        and terakhir.get("ton") == harga_ton
        and terakhir.get("usdt") == harga_usdt
    ):
        logger.info("Harga tidak berubah, pesan dilewati")
        return

    pesan = build_pesan(harga_ton, harga_usdt, data["ton"], data["usdt"], terakhir)

    try:
        await context.bot.send_message(
            chat_id=ID_CHANNEL,
            text=pesan,
            parse_mode=ParseMode.HTML,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except RetryAfter as e:
        logger.warning(f"Kena flood limit Telegram, coba lagi setelah {e.retry_after}s")
        return
    except TelegramError as e:
        logger.error(f"Gagal kirim ke channel: {e}")
        return

    # State hanya diperbarui jika pesan berhasil terkirim
    context.bot_data["terakhir"] = {"ton": harga_ton, "usdt": harga_usdt}
    logger.info(f"Terkirim: TON={harga_ton:,.0f} | USDT={harga_usdt:,.0f}")


async def post_init(app: Application):
    app.bot_data["http"] = httpx.AsyncClient(
        timeout=TIMEOUT_HTTP,
        headers={"accept": "application/json"},
    )
    app.bot_data["terakhir"] = {}


async def post_shutdown(app: Application):
    client = app.bot_data.get("http")
    if client:
        await client.aclose()


def main():
    app = (
        Application.builder()
        .token(TOKEN_BOT)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    if app.job_queue is None:
        logger.critical(
            "JobQueue TIDAK tersedia! Install: "
            "pip install 'python-telegram-bot[job-queue]'"
        )
        raise SystemExit(1)

    app.job_queue.run_repeating(
        kirim_update,
        interval=INTERVAL_DETIK,
        first=5,
        name="crypto_update",
    )

    logger.info("Bot channel-only berjalan...")
    app.run_polling(allowed_updates=[])


if __name__ == "__main__":
    main()