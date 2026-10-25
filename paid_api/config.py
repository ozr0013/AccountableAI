"""Shared settings. Everything sensitive comes from .env, never from code."""

from __future__ import annotations

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

NETWORK = "eip155:84532"                       # Base Sepolia (testnet)
CDP_NETWORK = "base-sepolia"
FACILITATOR = os.getenv("X402_FACILITATOR", "https://x402.org/facilitator")
PRICE = os.getenv("X402_PRICE", "$0.01")
SERVER_URL = os.getenv("X402_SERVER_URL", "http://127.0.0.1:4021")
MAX_PER_CALL = os.getenv("X402_BUYER_MAX_PER_CALL", "$0.05")   # buyer refuses anything pricier
SELLER_ACCOUNT = "accountableai-seller"
BUYER_ACCOUNT = "accountableai-buyer"


def pay_to() -> str:
    addr = os.getenv("X402_PAY_TO")
    if not addr:
        raise SystemExit("X402_PAY_TO is not set. Run `python -m paid_api.wallets` and add the "
                         "seller address it prints to .env.")
    return addr
