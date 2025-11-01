#!/usr/bin/env python3
"""
poll_latest_tx_and_balance.py

- Polls Etherscan v2 tokentx (chainid=56) for ASTER transfers to WALLET.
- Prints ONLY the newest incoming ASTER transfer when it changes.
- When printing a new tx, also fetches wallet ASTER balance via Web3 (BSC RPC)
  and the ASTER/USD price via CoinGecko to print total USD value.
- Posts a human-readable update to X (Twitter) when new tx is found.
  (No emojis; uses Tweepy v2 Client.create_tweet)
"""

import time
import requests
from web3 import Web3
from web3.middleware import geth_poa_middleware
import os
import tweepy

# ---------------- CONFIG ----------------
API_KEY = os.getenv("ETHERSCAN_API_KEY", "BE95FE2S1AYSR7MKTAB7KB5KVYVSERXN7D")  # Etherscan
CHAIN_ID = "56"
API_BASE = "https://api.etherscan.io/v2/api"

WALLET = os.getenv("WATCH_WALLET", "0xE307F534EEc7256331C347Ad73E7A08446F1d7a7")
ASTER_CONTRACT = os.getenv("ASTER_CONTRACT", "0x000Ae314E2A2172a039B26378814C252734f556A")
BSC_RPC = os.getenv("BSC_RPC", "https://bsc-dataseed.binance.org/")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "60"))
RATE_LIMIT_RETRY = int(os.getenv("RATE_LIMIT_RETRY", "5"))
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "50"))
COINGECKO_ID = os.getenv("COINGECKO_ID", "aster-2")

# X (Twitter) credentials - prefer environment variables for safety
X_API_KEY = os.getenv("X_API_KEY", "")
X_API_SECRET = os.getenv("X_API_SECRET", "")
X_ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN", "")
X_ACCESS_TOKEN_SECRET = os.getenv("X_ACCESS_TOKEN_SECRET", "")
# ----------------------------------------

# checksum addresses using Web3 standard helper
WALLET = Web3.to_checksum_address(WALLET)
ASTER_CONTRACT = Web3.to_checksum_address(ASTER_CONTRACT)

# web3 setup for balanceOf
w3 = None
try:
    w3 = Web3(Web3.HTTPProvider(BSC_RPC, request_kwargs={"timeout": 10}))
    w3.middleware_onion.inject(geth_poa_middleware, layer=0)
    if not w3.is_connected():
        print("Warning: could not connect to BSC RPC at", BSC_RPC)
        w3 = None
except Exception as e:
    print("Warning: Web3 init error:", e)
    w3 = None

ERC20_MIN_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals",
     "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol",
     "outputs": [{"name": "", "type": "string"}], "type": "function"},
]

token_contract = None
if w3:
    try:
        token_contract = w3.eth.contract(address=ASTER_CONTRACT, abi=ERC20_MIN_ABI)
    except Exception as e:
        print("Warning: failed to create token contract:", e)
        token_contract = None

last_seen_hash = None  # hash of the latest incoming tx we've printed

# ---------------- Tweepy (v2) Setup ----------------
def init_twitter_client():
    """
    Initialize tweepy.Client for v2 endpoints using user context tokens.
    Requires consumer_key, consumer_secret, access_token, access_token_secret.
    Returns tweepy.Client or None if credentials missing.
    """
    if not (X_API_KEY and X_API_SECRET and X_ACCESS_TOKEN and X_ACCESS_TOKEN_SECRET):
        print("X credentials not fully set. Skipping posting to X.")
        return None
    try:
        client = tweepy.Client(
            consumer_key=X_API_KEY,
            consumer_secret=X_API_SECRET,
            access_token=X_ACCESS_TOKEN,
            access_token_secret=X_ACCESS_TOKEN_SECRET,
            wait_on_rate_limit=True,
        )
        # small test: we won't call any endpoint here, let create_tweet surface permission errors
        return client
    except Exception as e:
        print("Could not initialize tweepy.Client:", e)
        return None


twitter_client = init_twitter_client()


def post_to_x_v2(message: str):
    """Post a plain-text message to X using tweepy.Client.create_tweet."""
    if not twitter_client:
        return
    try:
        resp = twitter_client.create_tweet(text=message)
        tid = None
        if resp and getattr(resp, "data", None):
            # resp.data is typically dict-like with 'id'
            tid = resp.data.get("id") if isinstance(resp.data, dict) else getattr(resp.data, "id", None)
        print("Posted update to X. tweet id:", tid)
    except Exception as e:
        # print full exception text to help debugging permissions (403 / 453)
        print("Could not post to X:", e)
        # print the message we tried to post for debugging
        print("Message was:\n", message)


# ---------------- helper functions ----------------
def fetch_tokentx_retry():
    params = {
        "apikey": API_KEY,
        "chainid": CHAIN_ID,
        "module": "account",
        "action": "tokentx",
        "contractaddress": ASTER_CONTRACT,
        "address": WALLET,
        "page": 1,
        "offset": PAGE_SIZE,
        "sort": "desc",
    }
    while True:
        try:
            r = requests.get(API_BASE, params=params, timeout=20)
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            print("Network/HTTP error:", e)
            print(f"Retrying in {RATE_LIMIT_RETRY} seconds...")
            time.sleep(RATE_LIMIT_RETRY)
            continue

        status = str(data.get("status", ""))
        result = data.get("result")
        if status == "0" and isinstance(result, str):
            print("Etherscan message:", result)
            print(f"Retrying in {RATE_LIMIT_RETRY} seconds...")
            time.sleep(RATE_LIMIT_RETRY)
            continue
        if isinstance(result, list):
            return result
        return []


def find_newest_incoming(txs):
    for tx in txs:
        if (tx.get("to") or "").lower() == WALLET.lower():
            return tx
    return None


def human_amount(value, decimals):
    try:
        return int(value) / (10 ** int(decimals))
    except Exception:
        try:
            return float(value or 0)
        except Exception:
            return 0.0


def get_aster_price_usd():
    try:
        resp = requests.get("https://api.coingecko.com/api/v3/simple/price",
                            params={"ids": COINGECKO_ID, "vs_currencies": "usd"}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if COINGECKO_ID in data and "usd" in data[COINGECKO_ID]:
            return float(data[COINGECKO_ID]["usd"])
        else:
            return None
    except Exception as e:
        print("Could not fetch ASTER price:", e)
        return None


def get_wallet_aster_balance():
    if not token_contract:
        return None, None, None
    try:
        decimals = token_contract.functions.decimals().call()
    except Exception:
        decimals = 18
    try:
        symbol = token_contract.functions.symbol().call()
    except Exception:
        symbol = "ASTER"
    try:
        raw = token_contract.functions.balanceOf(WALLET).call()
        bal = raw / (10 ** decimals)
        return bal, decimals, symbol
    except Exception as e:
        print("Could not fetch token balance via RPC:", e)
        return (None, decimals, symbol)


def format_datetime_from_timestamp(ts):
    try:
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(ts)))
    except Exception:
        return str(ts)


def compose_plain_update(tx, price, wallet_balance, symbol):
    """
    Compose a human-readable plain-text update (no emojis).
    Fields: date, amount ASTER, USD value of that amount, current holdings (ASTER + USD), bscscan link.
    """
    amt = human_amount(tx.get("value", "0"), tx.get("tokenDecimal", "18"))
    tx_usd = None
    if price is not None:
        tx_usd = amt * price

    bal = wallet_balance
    bal_usd = None
    if bal is not None and price is not None:
        bal_usd = bal * price

    ts = tx.get("timeStamp", "")
    dt = format_datetime_from_timestamp(ts)
    txhash = tx.get("hash", "")

    lines = []
    lines.append(f"New ASTER incoming transfer detected")
    lines.append(f"Date: {dt}")
    lines.append(f"Amount: {amt:,.6f} {symbol}")
    if tx_usd is not None:
        lines.append(f"Value: ${tx_usd:,.2f} (ASTER @ ${price:.6f}/token)")
    else:
        lines.append("Value: USD price unavailable")
    if bal is not None:
        if bal_usd is not None:
            lines.append(f"Wallet holdings: {bal:,.6f} {symbol} (~${bal_usd:,.2f})")
        else:
            lines.append(f"Wallet holdings: {bal:,.6f} {symbol} (USD price unavailable)")
    else:
        lines.append("Wallet holdings: unavailable")
    lines.append(f"Tx: https://bscscan.com/tx/{txhash}")

    return "\n".join(lines)


def print_latest_tx_and_wallet(tx):
    amt = human_amount(tx.get("value", "0"), tx.get("tokenDecimal", "18"))
    sym = tx.get("tokenSymbol", "ASTER")
    blk = tx.get("blockNumber")
    txhash = tx.get("hash")
    frm = tx.get("from")
    to = tx.get("to")

    print("\n--- Latest ASTER incoming transfer ---")
    print(f"[{blk}] +{amt:.6f} {sym}")
    print("From:", frm)
    print("To:  ", to)
    print("Tx:  ", f"https://bscscan.com/tx/{txhash}")

    try:
        ts = int(tx.get("timeStamp", 0))
        print("Time:", format_datetime_from_timestamp(ts))
    except Exception:
        pass

    price = get_aster_price_usd()
    if price is not None:
        tx_usd = amt * price
        print(f"Tx value: ${tx_usd:,.2f} (ASTER @ ${price:.6f})")
    else:
        print("Tx value: (price unavailable)")

    bal, decimals, symbol = get_wallet_aster_balance()
    if bal is not None:
        if price is not None:
            bal_usd = bal * price
            print(f"Wallet total: {bal:,.6f} {symbol}  (~${bal_usd:,.2f})")
        else:
            print(f"Wallet total: {bal:,.6f} {symbol}")
    else:
        print("Wallet total: (unavailable)")

    print("--------------------------------------\n")

    # Compose plain text update and post to X
    update_text = compose_plain_update(tx, price, bal, symbol)
    post_to_x_v2(update_text)


# ---------------- main loop ----------------
def main():
    global last_seen_hash

    print(f"Watching latest ASTER incoming to {WALLET}")
    print(f"Polling every {POLL_INTERVAL} seconds\n")

    txs = fetch_tokentx_retry()
    newest = find_newest_incoming(txs)
    if newest:
        print_latest_tx_and_wallet(newest)
        last_seen_hash = newest.get("hash")

    while True:
        time.sleep(POLL_INTERVAL)
        try:
            txs = fetch_tokentx_retry()
            newest = find_newest_incoming(txs)
            if newest:
                newest_hash = newest.get("hash")
                if newest_hash != last_seen_hash:
                    print_latest_tx_and_wallet(newest)
                    last_seen_hash = newest_hash
                else:
                    print(f"No new tx in last {POLL_INTERVAL} seconds")
            else:
                print(f"No new tx in last {POLL_INTERVAL} seconds")
        except Exception as e:
            print("Unexpected error:", e)
            time.sleep(RATE_LIMIT_RETRY)


if __name__ == "__main__":
    main()
