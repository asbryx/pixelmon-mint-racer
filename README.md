# Pixelmon Mint Racer & Sweep Automation

Credential-free, high-speed automated battle solver, voucher redemption, commit-reveal minter, and multi-wallet token sweeper for Pixelmon on Robinhood Chain.

---

## 1. Overview & Mechanics

Pixelmon (`pixelmon.mom`) features an on-chain commit-reveal mint mechanism gated by a deterministic battle mini-game:

1. **Battle Simulation (`/api/battle/start`)**:
   - The server issues a battle session ID and an initial PRNG `seed`.
   - The battle runs on a deterministic fixed-percentage damage model matching `BattleEngine.sol`.
2. **Deterministic Solver (`solve_seed.mjs`)**:
   - Given the seed, the local solver simulates all battle turns and computes the 100% optimal winning sequence.
3. **Voucher Issuance (`/api/battle/claim`)**:
   - The client posts the winning action log to the API and receives an EIP-712 / typed win voucher signed by the game signer.
4. **On-Chain Voucher Redemption (`redeemWinVoucher`)**:
   - The voucher is redeemed on the smart contract (`0x7dE3B4eC32929c7252276bc1b33e4A9FE724180f`).
5. **Commit Mint (`commitMint`)**:
   - Calls `commitMint()` paying 0.0001 ETH, emitting a `MintCommitted` event with a unique `commitId`.
6. **Finalize & Sweep (`finalizeMint` + `transferFrom`)**:
   - Calls `finalizeMint(commitId)` to mint the ERC-721 token.
   - Automatically sweeps the newly minted token to the configured destination wallet.

---

## 2. Repository Structure

```text
├── .env.example              # Safe environment variable template
├── .gitignore                # Comprehensive ignore rules for keys/state
├── .github/workflows/ci.yml  # Read-only verification CI
├── SECURITY.md               # Credential safety policy
├── requirements.txt          # Python dependencies
├── docs/
│   ├── ARCHITECTURE.md       # Technical flow & state machines
│   └── OPERATIONS.md         # CLI operational guide & runbooks
├── scripts/
│   └── scan_secrets.py       # Deterministic secret scanner
├── src/
│   ├── blockchain_utils.py   # RPC racing, transaction signing, and verification
│   ├── minter.py             # Full batch mint & sweep orchestrator
│   ├── finalizer.py          # Standalone commit finalizer & token sweeper
│   ├── pixelmon_engine.mjs   # Deterministic battle engine implementation
│   └── solve_seed.mjs        # Seed solver CLI wrapper
└── tests/
    ├── test_engine.py        # Battle engine offline simulation tests
    └── test_blockchain_utils.py # RPC & configuration unit tests
```

---

## 3. Installation

### Prerequisites
- Python 3.10+
- Node.js 18+

### Setup
```bash
# Clone the repository
git clone https://github.com/asbryx/pixelmon-mint-racer.git
cd pixelmon-mint-racer

# Install Python dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
```

---

## 4. Configuration

Edit `.env` (never commit this file):

```ini
ROBINHOOD_CHAIN_ID=4663
PIXELMON_CONTRACT_ADDRESS=0x7dE3B4eC32929c7252276bc1b33e4A9FE724180f
PIXELMON_API_BASE_URL=https://pixelmon.mom
ROBINHOOD_RPC_URLS=https://robinhood-rpc.publicnode.com,https://rpc.mainnet.chain.robinhood.com

# Path to private keys file (one per line, 0x...)
PRIVATE_KEY_FILE=/path/to/wallets_private_keys.txt

# Destination wallet to receive swept tokens
SWEEP_DESTINATION_ADDRESS=0xYourPrimaryWalletAddressHere

GAS_PRICE_FLOOR_GWEI=2.5
MAX_WORKERS=6
```

---

## 5. Usage

### Run Full Batch Mint & Sweep
```bash
python -m src.minter --key-file /path/to/keys.txt --destination 0xYourWallet --out results.json
```

### Finalize Pending Commits
If commits were made on-chain but network/API interrupted finalization:
```bash
python -m src.finalizer --key-file /path/to/keys.txt --commits-file pending_commits.json --destination 0xYourWallet
```

---

## 6. Verification & Security

Run deterministic local checks:
```bash
# Credential scan
python scripts/scan_secrets.py

# Python syntax
python -m py_compile src/*.py

# Node.js syntax
node --check src/pixelmon_engine.mjs
node --check src/solve_seed.mjs

# Unit tests
pytest -v tests/
```
