from __future__ import annotations

import argparse
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from eth_abi import encode, decode
from web3 import Web3

from .blockchain_utils import (
    get_gas_price,
    load_rpc_urls,
    load_wallets_from_file,
    owner_of,
    rpc_call,
    send_raw_transaction,
    wait_receipt,
)

# Defaults and Constants
DEFAULT_CONTRACT = "0x7dE3B4eC32929c7252276bc1b33e4A9FE724180f"
DEFAULT_API_BASE = "https://pixelmon.mom"
DEFAULT_CHAIN_ID = 4663
DEFAULT_GAS_FLOOR_GWEI = 2.5
DEFAULT_COMMIT_PRICE_WEI = 100_000_000_000_000  # 0.0001 ETH

w3 = Web3()
SEL_REDEEM = "0x" + w3.keccak(text="redeemWinVoucher((address,bytes32,uint64),bytes)")[:4].hex()
SEL_COMMIT = "0x" + w3.keccak(text="commitMint()")[:4].hex()
SEL_COMMITS = "0x" + w3.keccak(text="commits(uint256)")[:4].hex()
SEL_FINALIZE = "0x" + w3.keccak(text="finalizeMint(uint256)")[:4].hex()
SEL_TRANSFER_FROM = "0x" + w3.keccak(text="transferFrom(address,address,uint256)")[:4].hex()

TOPIC_MINT_COMMITTED = "0x" + w3.keccak(text="MintCommitted(uint256,address,uint64)").hex()
TOPIC_CAPSULE_MINTED = "0x" + w3.keccak(text="CapsuleMinted(address,uint256,uint8)").hex()

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def api_post(endpoint: str, payload: dict[str, Any], base_url: str = DEFAULT_API_BASE, timeout: float = 15.0) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{endpoint}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def solve_seed_node(seed: int, solver_script_path: Path | str | None = None) -> dict[str, Any]:
    if solver_script_path is None:
        solver_script_path = Path(__file__).parent / "solve_seed.mjs"
    cmd = ["node", str(solver_script_path), str(seed)]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(res.stdout.strip())


def process_wallet_mint_sweep(
    wallet: dict[str, Any],
    contract_address: str,
    sweep_destination: str | None,
    chain_id: int = DEFAULT_CHAIN_ID,
    api_base: str = DEFAULT_API_BASE,
    gas_floor_gwei: float = DEFAULT_GAS_FLOOR_GWEI,
    commit_price_wei: int = DEFAULT_COMMIT_PRICE_WEI,
    rpc_urls: list[str] | None = None,
) -> dict[str, Any]:
    w_idx = wallet["wallet"]
    addr = wallet["address"]
    contract = Web3.to_checksum_address(contract_address)
    sweep_dst = Web3.to_checksum_address(sweep_destination) if sweep_destination else None

    res: dict[str, Any] = {
        "wallet": w_idx,
        "address": addr,
        "status": "UNKNOWN",
        "battle_session": None,
        "redeem_tx_hash": None,
        "commit_tx_hash": None,
        "commit_id": None,
        "finalize_tx_hash": None,
        "minted_token_ids": [],
        "transfers": [],
        "error": None,
    }

    t0 = time.time()
    try:
        # Step 1: Start battle session
        print(f"[*] Wallet #{w_idx:02d} ({addr[:8]}...): Starting battle session...", flush=True)
        start_payload = {
            "wallet": addr,
            "team": [
                {"speciesId": 3, "slots": [0, 3]},
                {"speciesId": 2, "slots": [0, 1]},
                {"speciesId": 1, "slots": [0, 2]},
            ],
            "difficulty": 3,
            "balance": "tuned",
        }
        start_res = api_post("/api/battle/start", start_payload, base_url=api_base)
        session_id = start_res["sessionId"]
        seed = start_res["seed"]
        res["battle_session"] = session_id

        # Step 2: Solve deterministic battle
        battle_sol = solve_seed_node(seed)
        if battle_sol.get("outcome") != "PLAYER_WIN":
            raise RuntimeError(f"Battle simulation failed with outcome: {battle_sol.get('outcome')}")

        actions = battle_sol["actions"]
        print(f"[+] Wallet #{w_idx:02d} ({addr[:8]}...): Battle won in {battle_sol['turns']} turns ({len(actions)} actions)", flush=True)

        # Step 3: Claim battle win & get signed voucher
        claim_payload = {
            "sessionId": session_id,
            "wallet": addr,
            "actions": actions,
        }
        claim_res = api_post("/api/battle/claim", claim_payload, base_url=api_base)
        voucher = claim_res["voucher"]
        signature = bytes.fromhex(claim_res["signature"][2:])

        # Step 4: Redeem win voucher on-chain
        gas_price = max(get_gas_price(rpc_urls=rpc_urls, floor_gwei=gas_floor_gwei), int(gas_floor_gwei * 1e9))
        nonce_hex = rpc_call("eth_getTransactionCount", [addr, "latest"], rpc_urls=rpc_urls)
        current_nonce = int(nonce_hex, 16)

        voucher_tuple = (
            Web3.to_checksum_address(voucher["player"]),
            bytes.fromhex(voucher["voucherId"][2:]),
            voucher["deadline"],
        )
        redeem_calldata = SEL_REDEEM + encode(
            ["(address,bytes32,uint64)", "bytes"],
            [voucher_tuple, signature],
        ).hex()

        print(f"[*] Wallet #{w_idx:02d} ({addr[:8]}...): Redeeming voucher on-chain (nonce={current_nonce})...", flush=True)
        redeem_tx = send_raw_transaction(
            wallet=wallet,
            to=contract,
            data=redeem_calldata,
            value=0,
            chain_id=chain_id,
            gas_price_val=gas_price,
            nonce=current_nonce,
            gas_limit=200_000,
            rpc_urls=rpc_urls,
        )
        res["redeem_tx_hash"] = redeem_tx
        wait_receipt(redeem_tx, rpc_urls=rpc_urls, timeout_seconds=30)
        current_nonce += 1

        # Step 5: Commit Mint on-chain
        print(f"[*] Wallet #{w_idx:02d} ({addr[:8]}...): Committing mint (nonce={current_nonce}, value={commit_price_wei} wei)...", flush=True)
        commit_tx = send_raw_transaction(
            wallet=wallet,
            to=contract,
            data=SEL_COMMIT,
            value=commit_price_wei,
            chain_id=chain_id,
            gas_price_val=gas_price,
            nonce=current_nonce,
            gas_limit=200_000,
            rpc_urls=rpc_urls,
        )
        res["commit_tx_hash"] = commit_tx
        commit_receipt = wait_receipt(commit_tx, rpc_urls=rpc_urls, timeout_seconds=30)
        current_nonce += 1

        commit_id = None
        for log_entry in commit_receipt.get("logs", []):
            topics = log_entry.get("topics", [])
            if topics and topics[0].lower() == TOPIC_MINT_COMMITTED.lower():
                commit_id = int(topics[1], 16)
                break

        if commit_id is None:
            raise RuntimeError(f"MintCommitted event not found in tx {commit_tx}")
        res["commit_id"] = commit_id
        print(f"[+] Wallet #{w_idx:02d} ({addr[:8]}...): Mint committed! Commit ID: {commit_id}", flush=True)

        # Step 6: Finalize Mint
        print(f"[*] Wallet #{w_idx:02d} ({addr[:8]}...): Calling /api/mint/finalize for commit #{commit_id}...", flush=True)
        token_id = None
        try:
            finalize_res = api_post("/api/mint/finalize", {"commitId": commit_id}, base_url=api_base)
            token_id = finalize_res.get("tokenId")
        except Exception:
            pass

        if token_id is None:
            time.sleep(1.0)
            fin_calldata = SEL_FINALIZE + encode(["uint256"], [commit_id]).hex()
            fin_tx = send_raw_transaction(
                wallet=wallet,
                to=contract,
                data=fin_calldata,
                value=0,
                chain_id=chain_id,
                gas_price_val=gas_price,
                nonce=current_nonce,
                gas_limit=250_000,
                rpc_urls=rpc_urls,
            )
            res["finalize_tx_hash"] = fin_tx
            fin_receipt = wait_receipt(fin_tx, rpc_urls=rpc_urls, timeout_seconds=30)
            current_nonce += 1
            for log_entry in fin_receipt.get("logs", []):
                topics = log_entry.get("topics", [])
                if topics and topics[0].lower() == TOPIC_CAPSULE_MINTED.lower():
                    token_id = int(topics[2], 16)
                    break

        if token_id is None:
            time.sleep(1.5)
            logs_hex = rpc_call("eth_getLogs", [{
                "address": contract,
                "topics": [TOPIC_CAPSULE_MINTED, "0x" + addr[2:].lower().rjust(64, '0')],
                "fromBlock": "latest"
            }], rpc_urls=rpc_urls)
            if logs_hex and len(logs_hex) > 0:
                token_id = int(logs_hex[-1]["topics"][2], 16)

        if token_id is None:
            raise RuntimeError(f"Could not determine minted tokenId for commit #{commit_id}")

        res["minted_token_ids"] = [token_id]
        print(f"[+] Wallet #{w_idx:02d} ({addr[:8]}...): Minted Token ID #{token_id}!", flush=True)

        # Step 7: Sweep to destination wallet if configured
        if sweep_dst and sweep_dst.lower() != addr.lower():
            print(f"[*] Wallet #{w_idx:02d} ({addr[:8]}...): Sweeping Token #{token_id} to {sweep_dst[:8]}... (nonce={current_nonce})...", flush=True)
            sweep_data = SEL_TRANSFER_FROM + encode(["address", "address", "uint256"], [addr, sweep_dst, token_id]).hex()
            sweep_gas = max(get_gas_price(rpc_urls=rpc_urls, floor_gwei=gas_floor_gwei), int(gas_floor_gwei * 1e9))
            sweep_tx = send_raw_transaction(
                wallet=wallet,
                to=contract,
                data=sweep_data,
                value=0,
                chain_id=chain_id,
                gas_price_val=sweep_gas,
                nonce=current_nonce,
                gas_limit=120_000,
                rpc_urls=rpc_urls,
            )
            wait_receipt(sweep_tx, rpc_urls=rpc_urls, timeout_seconds=30)
            owner_now = owner_of(contract, token_id, rpc_urls=rpc_urls)
            sweep_ok = (owner_now.lower() == sweep_dst.lower())

            res["transfers"].append({
                "token_id": token_id,
                "transfer_tx_hash": sweep_tx,
                "owner_after": owner_now,
                "success": sweep_ok,
            })
            print(f"[+] Wallet #{w_idx:02d} ({addr[:8]}...): Token #{token_id} swept! Owner: {owner_now} (Verified: {sweep_ok})", flush=True)

        res["status"] = "SUCCESS"
    except Exception as exc:
        res["status"] = "ERROR"
        res["error"] = str(exc)
        print(f"[-] Wallet #{w_idx:02d} ({addr[:8]}...): Error -> {exc}", flush=True)

    res["elapsed_ms"] = round((time.time() - t0) * 1000, 2)
    return res


def main():
    parser = argparse.ArgumentParser(description="Pixelmon Battle Minter & Sweeper")
    parser.add_argument("--key-file", "-k", default=os.getenv("PRIVATE_KEY_FILE"), help="Path to private keys file")
    parser.add_argument("--contract", "-c", default=os.getenv("PIXELMON_CONTRACT_ADDRESS", DEFAULT_CONTRACT), help="Pixelmon contract address")
    parser.add_argument("--destination", "-d", default=os.getenv("SWEEP_DESTINATION_ADDRESS"), help="Sweep destination address")
    parser.add_argument("--api", default=os.getenv("PIXELMON_API_BASE_URL", DEFAULT_API_BASE), help="Pixelmon API base URL")
    parser.add_argument("--chain-id", type=int, default=int(os.getenv("ROBINHOOD_CHAIN_ID", DEFAULT_CHAIN_ID)), help="EVM Chain ID")
    parser.add_argument("--workers", "-w", type=int, default=int(os.getenv("MAX_WORKERS", 6)), help="Concurrent workers")
    parser.add_argument("--gas-floor", type=float, default=float(os.getenv("GAS_PRICE_FLOOR_GWEI", DEFAULT_GAS_FLOOR_GWEI)), help="Minimum gas price floor in Gwei")
    parser.add_argument("--out", "-o", default=None, help="Output JSON result file")

    args = parser.parse_args()

    if not args.key_file:
        print("Error: --key-file argument or PRIVATE_KEY_FILE environment variable required.", file=sys.stderr)
        sys.exit(1)

    wallets = load_wallets_from_file(args.key_file)
    print(f"Loaded {len(wallets)} wallets. Starting Pixelmon Batch Mint & Sweep...", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(
                process_wallet_mint_sweep,
                w,
                contract_address=args.contract,
                sweep_destination=args.destination,
                chain_id=args.chain_id,
                api_base=args.api,
                gas_floor_gwei=args.gas_floor,
            )
            for w in wallets
        ]
        results = [f.result() for f in futures]

    success_count = sum(1 for r in results if r["status"] == "SUCCESS")
    print(f"\nCompleted: {success_count}/{len(wallets)} succeeded.")

    if args.out:
        out_path = Path(args.out)
        out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"Results written to {out_path}")


if __name__ == "__main__":
    main()
