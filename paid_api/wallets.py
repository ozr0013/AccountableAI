"""Create (or reuse) the two Coinbase server wallets and fund the buyer with test USDC.

    python -m paid_api.wallets            # prints both addresses, tops up the buyer if empty

Needs CDP_API_KEY_ID, CDP_API_KEY_SECRET, CDP_WALLET_SECRET in .env. The wallets are named,
so running this twice returns the same addresses. Base Sepolia only: faucet money, no value.
"""

from __future__ import annotations

import asyncio

from cdp import CdpClient

from . import config

USDC_BASE_SEPOLIA = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"


async def usdc_balance(cdp: CdpClient, address: str) -> float:
    res = await cdp.evm.list_token_balances(address, config.CDP_NETWORK)
    for b in res.balances:
        if b.token.contract_address.lower() == USDC_BASE_SEPOLIA.lower():
            return b.amount.amount / 10 ** b.amount.decimals
    return 0.0


async def main() -> None:
    async with CdpClient() as cdp:
        seller = await cdp.evm.get_or_create_account(name=config.SELLER_ACCOUNT)
        buyer = await cdp.evm.get_or_create_account(name=config.BUYER_ACCOUNT)
        bal = await usdc_balance(cdp, buyer.address)
        print(f"seller (receives payments): {seller.address}")
        print(f"buyer  (pays for checks):   {buyer.address}   USDC {bal:.2f}")
        if bal < 0.5:
            tx = await cdp.evm.request_faucet(address=buyer.address, network=config.CDP_NETWORK,
                                              token="usdc")
            print(f"requested test USDC from the CDP faucet: https://sepolia.basescan.org/tx/{tx}")
            print("it lands in about 10 seconds")
        print(f"\nAdd this line to .env:\nX402_PAY_TO={seller.address}")


if __name__ == "__main__":
    asyncio.run(main())
