from __future__ import annotations

import argparse
import json
import os
import sys
import time
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

DEFAULT_CONTRACT = "0x7dE3B4eC32929c7252276bc1b33e4A9FE724180f"
DEFAULT_CHAIN_ID = 4663
DEFAULT_GAS_FLOOR_GWEI = 2.5

w3 = Web3()
SEL_COMMITS = "0x" + w3.keccak(text="commits(uint256)")[:4].hex()
SEL_FINALIZE = "0x" + w3.keccak(text="finalizeMint(uint256)")[:4].hex()
SEL_TRANSFER_FROM = "0x" + w3.keccak(text="transferFrom(address,address,uint256)")[:4].hex()
TOPIC_CAPSULE_MINTED = "0x" + w3.keccak(text="CapsuleMinted(address,uint256,uint8)").hex()


def finalize_single_commit(
    commit_id: int,
    wallet: dict[str, Any],
    contract_address: str,
    sweep_destination: str | None,
    chain_id: int = DEFAULT_CHAIN_ID,
    gas_floor_gwei: float = DEFAULT_GAS_FLOOR_GWEI,
    rpc_urls: list[str] | None = None,
) -> dict[str, Any]:
    w_idx = wallet["wallet"]
    addr = wallet["address"]
    contract = Web3.to_checksum_address(contract_address)
    sweep_dst = Web3.to_checksum_address(sweep_destination) if sweep_destination else None

    res: dict[str, Any] = {
        "wallet": w_idx,
        "address": addr,
        "commit_id": commit_id,
        "status": "UNKNOWN",
        "tokenId": None,
        "sweep_tx": None,
        "error": None,
    }

    try:
        call_data = SEL_COMMITS + encode(["uint256"], [commit_id]).hex()
        state_hex = rpc_call("eth_call", [{"to": contract, "data": call_data}, "latest"], rpc_urls=rpc_urls)
        _, _, is_finalized = decode(["address", "uint64", "bool"], bytes.fromhex(state_hex[2:]))

        token_id = None
        if not is_finalized:
            gas_price = max(get_gas_price(rpc_urls=rpc_urls, floor_gwei=gas_floor_gwei), int(gas_floor_gwei * 1e9))
            nonce_hex = rpc_call("eth_getTransactionCount", [addr, "latest"], rpc_urls=rpc_urls)
            nonce = int(nonce_hex, 16)

            print(f"[*] Wallet #{w_idx:02d} ({addr[:8]}...): Finalizing commit #{commit_id} on-chain (nonce={nonce})...", flush=True)
            fin_calldata = SEL_FINALIZE + encode(["uint256"], [commit_id]).hex()
            fin_tx = send_raw_transaction(
                wallet=wallet,
                to=contract,
                data=fin_calldata,
                value=0,
                chain_id=chain_id,
                gas_price_val=gas_price,
                nonce=nonce,
                gas_limit=350_000,
                rpc_urls=rpc_urls,
            )
            fin_rc = wait_receipt(fin_tx, rpc_urls=rpc_urls, timeout_seconds=30)
            for log_entry in fin_rc.get("logs", []):
                topics = log_entry.get("topics", [])
                if topics and topics[0].lower() == TOPIC_CAPSULE_MINTED.lower():
                    token_id = int(topics[2], 16)
                    break

        if token_id is None:
            time.sleep(1.0)
            player_topic = "0x" + addr[2:].lower().rjust(64, "0")
            logs = rpc_call("eth_getLogs", [{
                "address": contract,
                "topics": [TOPIC_CAPSULE_MINTED, player_topic],
                "fromBlock": "latest"
            }], rpc_urls=rpc_urls)
            if logs:
                for l in logs:
                    token_id = int(l["topics"][2], 16)

        if token_id is None:
            raise RuntimeError(f"Could not find tokenId for commit #{commit_id}")

        res["tokenId"] = token_id
        print(f"[+] Wallet #{w_idx:02d} ({addr[:8]}...): Token ID is #{token_id}", flush=True)

        if sweep_dst:
            cur_owner = owner_of(contract, token_id, rpc_urls=rpc_urls)
            if cur_owner.lower() == sweep_dst.lower():
                print(f"[✓] Wallet #{w_idx:02d} ({addr[:8]}...): Token #{token_id} already swept to destination!", flush=True)
                res["status"] = "SUCCESS_ALREADY_SWEPT"
                return res

            if cur_owner.lower() == addr.lower():
                nonce_hex = rpc_call("eth_getTransactionCount", [addr, "latest"], rpc_urls=rpc_urls)
                nonce = int(nonce_hex, 16)
                print(f"[*] Wallet #{w_idx:02d} ({addr[:8]}...): Sweeping Token #{token_id} to {sweep_dst[:8]}... (nonce={nonce})...", flush=True)
                sweep_data = SEL_TRANSFER_FROM + encode(["address", "address", "uint256"], [addr, sweep_dst, token_id]).hex()
                sweep_gas = max(get_gas_price(rpc_urls=rpc_urls, floor_gwei=gas_floor_gwei), int(gas_floor_gwei * 1e9))
                sweep_tx = send_raw_transaction(
                    wallet=wallet,
                    to=contract,
                    data=sweep_data,
                    value=0,
                    chain_id=chain_id,
                    gas_price_val=sweep_gas,
                    nonce=nonce,
                    gas_limit=120_000,
                    rpc_urls=rpc_urls,
                )
                wait_receipt(sweep_tx, rpc_urls=rpc_urls, timeout_seconds=30)
                owner_after = owner_of(contract, token_id, rpc_urls=rpc_urls)
                res["sweep_tx"] = sweep_tx
                res["status"] = "SUCCESS" if owner_after.lower() == sweep_dst.lower() else "SWEEP_FAILED"

    except Exception as e:
        res["status"] = "ERROR"
        res["error"] = str(e)
        print(f"[-] Wallet #{w_idx:02d} ({addr[:8]}...): Error -> {e}", flush=True)

    return res


def main():
    parser = argparse.ArgumentParser(description="Finalize pending Pixelmon commits and sweep tokens")
    parser.add_argument("--key-file", "-k", default=os.getenv("PRIVATE_KEY_FILE"), help="Path to private keys file")
    parser.add_argument("--commits-file", "-f", required=True, help="Path to JSON file containing list of commit objects ({commit_id, player})")
    parser.add_argument("--contract", "-c", default=os.getenv("PIXELMON_CONTRACT_ADDRESS", DEFAULT_CONTRACT), help="Pixelmon contract address")
    parser.add_argument("--destination", "-d", default=os.getenv("SWEEP_DESTINATION_ADDRESS"), help="Sweep destination address")
    parser.add_argument("--chain-id", type=int, default=int(os.getenv("ROBINHOOD_CHAIN_ID", DEFAULT_CHAIN_ID)), help="EVM Chain ID")
    parser.add_argument("--workers", "-w", type=int, default=int(os.getenv("MAX_WORKERS", 6)), help="Concurrent workers")
    parser.add_argument("--gas-floor", type=float, default=float(os.getenv("GAS_PRICE_FLOOR_GWEI", DEFAULT_GAS_FLOOR_GWEI)), help="Minimum gas price floor in Gwei")
    parser.add_argument("--out", "-o", default=None, help="Output JSON result file")

    args = parser.parse_args()

    if not args.key_file:
        print("Error: --key-file argument or PRIVATE_KEY_FILE environment variable required.", file=sys.stderr)
        sys.exit(1)

    wallets = load_wallets_from_file(args.key_file)
    addr_to_wallet = {w["address"].lower(): w for w in wallets}

    commits_data = json.loads(Path(args.commits_file).read_text(encoding="utf-8"))

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = []
        for item in commits_data:
            cid = item.get("commit_id")
            player = item.get("player") or item.get("address")
            if not player or cid is None:
                continue
            w = addr_to_wallet.get(player.lower())
            if not w:
                continue
            futures.append(executor.submit(
                finalize_single_commit,
                commit_id=cid,
                wallet=w,
                contract_address=args.contract,
                sweep_destination=args.destination,
                chain_id=args.chain_id,
                gas_floor_gwei=args.gas_floor,
            ))
        results = [f.result() for f in futures]

    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"Results written to {args.out}")


if __name__ == "__main__":
    main()
