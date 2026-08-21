# Architecture & Execution Flow

```text
+-------------------+      1. Start Battle (/api/battle/start)      +--------------------+
|                   | --------------------------------------------> |                    |
|   minter.py       | <-------------------------------------------- |   pixelmon.mom     |
|   (Orchestrator)  |      Returns sessionId & PRNG seed            |   Game API Server  |
|                   |                                               +--------------------+
+---------+---------+
          |
          | 2. Solve Deterministic Battle (Node.js)
          v
+-------------------+
|  solve_seed.mjs   | -> Simulates fixed-% damage engine
|  (Local Engine)   | -> Returns 100% winning action sequence
+---------+---------+
          |
          | 3. Claim Win (/api/battle/claim)
          v
+-------------------+
|  Signed Voucher   | -> EIP-712 / typed struct + signature
+---------+---------+
          |
          | 4. On-Chain Transaction Sequence
          v
+----------------------------------------------------------------------------------------+
| Robinhood Chain Contract (0x7dE3B4eC32929c7252276bc1b33e4A9FE724180f)                  |
|                                                                                        |
|  [Tx 1: redeemWinVoucher] -> validates voucher & marks player eligible                |
|  [Tx 2: commitMint]       -> commits 0.0001 ETH & emits commitId                       |
|  [Tx 3: finalizeMint]     -> mints Capsule token ID to minter                          |
|  [Tx 4: transferFrom]     -> sweeps token ID to SWEEP_DESTINATION_ADDRESS              |
+----------------------------------------------------------------------------------------+
```

## Security & Nonce Safety

- **Sequential Nonces per Wallet**: All 4 transactions per wallet are signed with sequential nonces (`nonce`, `nonce+1`, `nonce+2`, `nonce+3`) after waiting for receipts, preventing state collisions.
- **Concurrent Wallet Scaling**: Multi-wallet execution uses `ThreadPoolExecutor` where separate wallets run concurrently across isolated workers.
- **Top-of-Block Gas Pricing**: Priority gas fee floor (2.50 Gwei) prevents transaction drops and out-of-gas reverts in contested blocks.
