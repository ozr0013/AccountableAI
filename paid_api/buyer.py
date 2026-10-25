"""The buyer: an agent with its own Coinbase wallet that pays $0.01 per vendor-payment check.

    python -m paid_api.buyer INV-KW-0301 [INV-KW-0901 ...]

Its controls, enforced in code before any signature:
  * spend cap per call (X402_BUYER_MAX_PER_CALL, default $0.05): a pricier 402 is refused
  * testnet only: it will only pay on Base Sepolia
The CDP server wallet signs the USDC authorization; the key never leaves Coinbase.
"""

from __future__ import annotations

import asyncio
import json
import sys

import requests
from cdp import CdpClient, EvmLocalAccount
from x402 import x402ClientSync
from x402.http.clients import wrapRequestsWithPayment
from x402.http.utils import decode_payment_response_header
from x402.mechanisms.evm.exact import register_exact_evm_client

from . import config


async def _account():
    async with CdpClient() as cdp:
        return await cdp.evm.get_or_create_account(name=config.BUYER_ACCOUNT)


def make_session() -> tuple[requests.Session, str]:
    server_account = asyncio.run(_account())
    signer = EvmLocalAccount(server_account)          # signs through CDP, key stays server-side
    client = x402ClientSync()
    register_exact_evm_client(client, signer, networks=config.NETWORK)   # Base Sepolia only
    client.set_spend_controls({"max_amount_per_payment": config.MAX_PER_CALL})
    return wrapRequestsWithPayment(requests.Session(), client), server_account.address


def check(session: requests.Session, invoice: str) -> dict:
    r = session.get(f"{config.SERVER_URL}/x402/verify", params={"invoice": invoice}, timeout=60)
    out = {"http": r.status_code}
    header = r.headers.get("PAYMENT-RESPONSE") or r.headers.get("X-PAYMENT-RESPONSE")
    if header:
        settle = decode_payment_response_header(header)
        tx = getattr(settle, "transaction", None)
        out["paid"] = {"success": getattr(settle, "success", None), "tx": tx,
                       "explorer": f"https://sepolia.basescan.org/tx/{tx}" if tx else None}
    try:
        out["verdict"] = r.json()
    except ValueError:
        out["body"] = r.text[:300]
    return out


def main(argv: list[str]) -> int:
    invoices = argv or ["INV-KW-0301"]
    session, address = make_session()
    print(f"buyer wallet {address} · cap {config.MAX_PER_CALL}/call · {config.NETWORK} (testnet)\n")
    for inv in invoices:
        res = check(session, inv)
        v = res.get("verdict", {})
        print(f"{inv}: HTTP {res['http']}  status={v.get('status')}  "
              f"{v.get('vendor', '')}  ${v.get('amount', 0):,.2f}")
        for a in v.get("alerts", [])[:2]:
            print(f"   - {a['headline']}  [cites {', '.join(a['citations'][:3])}]")
        if res.get("paid"):
            print(f"   paid $ {config.PRICE.strip('$')} USDC  {res['paid']['explorer']}")
        print()
    if "--json" in sys.argv:
        print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main([a for a in sys.argv[1:] if not a.startswith("--")]))
