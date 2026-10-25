"""The seller: AccountableAI's vendor-payment check behind an x402 paywall.

    uvicorn paid_api.server:app --port 4021

GET /x402/verify?invoice=INV-KW-0301   $0.01 USDC on Base Sepolia, returns the verdict
GET /invoices                          free: which invoices the demo world knows
GET /health                            free

An unpaid request gets HTTP 402 with the price; the buyer's wallet signs a USDC
authorization, retries, the facilitator settles it on chain, and only then does the
handler run. Money only flows in: nothing in AccountableAI can pay anyone.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query
from x402.http import FacilitatorConfig, HTTPFacilitatorClient, PaymentOption
from x402.http.middleware.fastapi import PaymentMiddlewareASGI
from x402.http.types import RouteConfig
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.server import x402ResourceServer

from . import config
from .verdict import invoices, verdict

app = FastAPI(title="AccountableAI paid verification API",
              description="Did the management company pay this vendor? $0.01 per check (testnet).")

server = x402ResourceServer(HTTPFacilitatorClient(FacilitatorConfig(url=config.FACILITATOR)))
server.register(config.NETWORK, ExactEvmServerScheme())

routes = {
    "GET /x402/verify": RouteConfig(
        accepts=[PaymentOption(scheme="exact", pay_to=config.pay_to(), price=config.PRICE,
                               network=config.NETWORK)],
        mime_type="application/json",
        description="Vendor payment verdict with cited evidence (simulated demo world)",
    ),
}
app.add_middleware(PaymentMiddlewareASGI, routes=routes, server=server)


@app.on_event("startup")
def _warm() -> None:
    invoices()          # replay the world once so the first paid call is fast


@app.get("/health")
def health() -> dict:
    return {"ok": True, "network": config.NETWORK, "price": config.PRICE,
            "pay_to": config.pay_to(), "mode": "testnet, simulated companies"}


@app.get("/invoices")
def list_invoices() -> dict:
    return {"invoices": invoices(), "try": "/x402/verify?invoice=INV-KW-0301"}


@app.get("/x402/verify")
def verify(invoice: str = Query(..., examples=["INV-KW-0301"])) -> dict:
    v = verdict(invoice.strip().upper())
    if v is None:
        raise HTTPException(404, f"unknown invoice {invoice}")
    return v
