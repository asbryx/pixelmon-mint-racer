# Operations & Troubleshooting Runbook

## Preflight Checks

1. Ensure Python dependencies are installed:
   ```bash
   pip install -r requirements.txt
   ```
2. Verify Node.js solver functions correctly:
   ```bash
   node src/solve_seed.mjs 12345
   ```
3. Verify wallet balance:
   Each wallet requires at least `0.0001 ETH` for the commit mint + ~`0.00005 ETH` for gas fees.

## Recovery Procedures

### Scenario A: Voucher Redeemed but Commit Failed
- Re-run `minter.py` or execute manual `commitMint()` call. The contract allows committing anytime once the voucher has been redeemed.

### Scenario B: Commit Succeeded but Finalize Timed Out
- Run `finalizer.py` with the extracted commit IDs:
  ```bash
  python -m src.finalizer --key-file keys.txt --commits-file commits.json --destination 0xDestinationWallet
  ```

### Scenario C: Token Minted but Sweep Transfer Failed
- Verify ownership using `blockchain_utils.owner_of(contract, token_id)` and retry `transferFrom` with bumped gas price.
