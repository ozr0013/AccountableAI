"""AccountableAI as a paid service for agents (Coinbase x402, Payment Agents track).

    python -m paid_api.wallets          # create the seller + buyer CDP wallets, fund the buyer
    uvicorn paid_api.server:app --port 4021
    python -m paid_api.buyer INV-KW-0301

See paid_api/README.md. Testnet only (Base Sepolia); payments are real testnet transfers,
the companies are simulated.
"""
