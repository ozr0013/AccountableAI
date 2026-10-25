# AccountableAI paid API: vendor-payment checks for agents (Coinbase x402)

**Coinbase track: Payment Agents (primary).** An agent that **sells a service**: any company's agent can ask "did the management company actually pay this vendor?" and pay $0.01 in USDC per answer through x402. No account, no API key, no invoice.

## Who it's for

Owners who pay vendors *through* a management company (rental owners, and anyone who pays through a general contractor, staffing agency or freight broker), and the finance agents that work for them. Before releasing the next funding, their agent buys a verdict: **paid**, **unexplained**, or **explained** (an allowed holdback, missing paperwork, not yet due), with the records behind it.

## What it uses

| Piece | Coinbase tool |
|---|---|
| Paid endpoint `GET /x402/verify` | **x402** FastAPI middleware (`x402[fastapi,evm]`), testnet facilitator `x402.org/facilitator` |
| Seller wallet (receives USDC) | **CDP Server Wallet** (`cdp-sdk`), named account `accountableai-seller` |
| Buyer agent wallet (pays) | **CDP Server Wallet** + `EvmLocalAccount` signer; key never leaves Coinbase |
| Test funds | **CDP faucet** (`request_faucet`, USDC on Base Sepolia) |
| The answer itself | AccountableAI's own workflow, replayed in rules mode (no model calls per request) |

## Architecture

```
 Buyer agent (CDP wallet)                         AccountableAI seller (FastAPI)
 ─────────────────────────                        ──────────────────────────────
 GET /x402/verify?invoice=INV-KW-0301  ───────▶   x402 middleware: no payment?
                                       ◀───────   402 Payment Required ($0.01, Base Sepolia, pay_to)
 spend cap check ($0.05 max/call)
 CDP wallet signs USDC authorization
 retry with PAYMENT-SIGNATURE          ───────▶   facilitator verifies + settles on Base Sepolia
                                       ◀───────   200 + verdict JSON + PAYMENT-RESPONSE (tx hash)
```

The verdict comes from the same deterministic workflow the AccountableAI dashboard runs: source-trust rules, delay cause, and alerts that passed the verifier, each citing its records.

## Controls

- **Money only flows in.** AccountableAI's core rule ("nothing can move money") still holds: the seller only receives. The buyer is a separate demo agent.
- **Buyer spend cap:** refuses any 402 above `X402_BUYER_MAX_PER_CALL` (default $0.05), enforced by the x402 client before signing.
- **Testnet only:** both sides are pinned to Base Sepolia (`eip155:84532`).
- **No secrets in code:** CDP keys and the wallet secret live in `.env` (git-ignored).

## Run it (about 5 minutes)

```bash
pip install -e .                       # adds x402[fastapi,evm,requests] and cdp-sdk
# .env: CDP_API_KEY_ID, CDP_API_KEY_SECRET, CDP_WALLET_SECRET  (portal.cdp.coinbase.com)
python -m paid_api.wallets             # creates both wallets, funds the buyer from the faucet
# add the printed X402_PAY_TO=0x... line to .env
uvicorn paid_api.server:app --port 4021
python -m paid_api.buyer INV-KW-0301 INV-KW-0501 INV-KW-0101
```

Expected: `INV-KW-0301` unexplained (statement says paid, bank and vendor say no), `INV-KW-0501` paid, `INV-KW-0101` partially paid, explained (a 10% holdback the agreement allows). Each line prints the Base Sepolia transaction for the $0.01 payment.

## Known limitations

- **Simulated companies.** The verdicts come from AccountableAI's fictional demo world (Oakline Homes, Keyway Property Management, 20 vendors). The USDC payments are real **testnet** transfers.
- One price for every check; no per-customer pricing or volume discounts.
- No authentication beyond payment: anyone who pays can query the demo world. A real deployment would scope answers to the payer's own vendors.

## Builder feedback for Coinbase

*(fill in honestly before submitting)*
- **Biggest blocker:**
- **How we worked around it:**
- **The one capability we'd most want:**
