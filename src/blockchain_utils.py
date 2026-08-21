from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any
import requests
from web3 import Web3

DEFAULT_RPCS = [
    "https://robinhood-rpc.publicnode.com",
    "https://rpc.mainnet.chain.robinhood.com",
    "https://rpc.arrowrpc.com",
]

def load_rpc_urls(custom_urls: list[str] | None = None) -> list[str]:
    """Load RPC URLs from env, parameter, or public defaults."""
    env_urls = os.getenv("ROBINHOOD_RPC_URLS", "")
    urls = []
    if custom_urls:
        urls.extend(custom_urls)
    elif env_urls.strip():
        urls.extend([u.strip() for u in env_urls.replace(",", "\n").splitlines() if u.strip()])
    else:
        urls.extend(DEFAULT_RPCS)

    cleaned = []
    seen = set()
    for u in urls:
        if u and u not in seen and u.startswith(("http://", "https://")):
            cleaned.append(u)
            seen.add(u)
    return cleaned or DEFAULT_RPCS


def rpc_call(method: str, params: list[Any], rpc_urls: list[str] | None = None, timeout: float = 8.0) -> Any:
    """Execute JSON-RPC call across available endpoints with automatic fallback."""
    urls = load_rpc_urls(rpc_urls)
    payload = {
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000) % 1000000,
        "method": method,
        "params": params,
    }
    last_err = None
    for url in urls:
        try:
            res = requests.post(url, json=payload, timeout=timeout)
            if res.status_code == 200:
                data = res.json()
                if "result" in data:
                    return data["result"]
                if "error" in data:
                    last_err = RuntimeError(f"RPC {url} returned error: {data['error']}")
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"All RPC endpoints failed for {method}: {last_err}")


def get_gas_price(rpc_urls: list[str] | None = None, floor_gwei: float = 2.5) -> int:
    """Get current network gas price with minimum floor in wei."""
    floor_wei = int(floor_gwei * 1e9)
    try:
        gas_hex = rpc_call("eth_gasPrice", [], rpc_urls=rpc_urls)
        network_gas = int(gas_hex, 16)
        return max(network_gas, floor_wei)
    except Exception:
        return floor_wei


def wait_receipt(
    tx_hash: str,
    rpc_urls: list[str] | None = None,
    timeout_seconds: float = 30.0,
    poll_interval: float = 0.8,
) -> dict[str, Any]:
    """Poll for transaction receipt until mined or timeout."""
    start = time.time()
    while time.time() - start < timeout_seconds:
        try:
            receipt = rpc_call("eth_getTransactionReceipt", [tx_hash], rpc_urls=rpc_urls)
            if receipt is not None:
                status = receipt.get("status")
                if status in (1, "0x1", "0x01"):
                    return receipt
                elif status in (0, "0x0", "0x00"):
                    raise RuntimeError(f"Transaction reverted on-chain: {tx_hash}")
                return receipt
        except Exception as e:
            if "reverted" in str(e).lower():
                raise e
        time.sleep(poll_interval)
    raise TimeoutError(f"Timed out waiting for receipt of transaction {tx_hash}")


def send_raw_transaction(
    wallet: dict[str, Any],
    to: str,
    data: str,
    value: int = 0,
    chain_id: int = 4663,
    gas_price_val: int = 2_500_000_000,
    nonce: int = 0,
    gas_limit: int = 200_000,
    rpc_urls: list[str] | None = None,
) -> str:
    """Sign and broadcast a raw transaction."""
    from eth_account import Account

    w3 = Web3()
    tx_dict = {
        "nonce": nonce,
        "gasPrice": gas_price_val,
        "gas": gas_limit,
        "to": Web3.to_checksum_address(to),
        "value": value,
        "data": data,
        "chainId": chain_id,
    }
    signed_tx = Account.sign_transaction(tx_dict, wallet["pk"])
    raw_hex = signed_tx.raw_transaction.hex()
    if not raw_hex.startswith("0x"):
        raw_hex = "0x" + raw_hex

    tx_hash = rpc_call("eth_sendRawTransaction", [raw_hex], rpc_urls=rpc_urls)
    return tx_hash


def load_wallets_from_file(pk_file_path: str | Path) -> list[dict[str, Any]]:
    """Load private keys from file and derive addresses without storing keys in memory beyond objects."""
    from eth_account import Account

    p = Path(pk_file_path)
    if not p.exists():
        raise FileNotFoundError(f"Private key file not found: {pk_file_path}")

    wallets = []
    lines = p.read_text(encoding="utf-8").splitlines()
    for idx, line in enumerate(lines, 1):
        clean = line.strip()
        if not clean or clean.startswith("#"):
            continue
        if not clean.startswith("0x"):
            clean = "0x" + clean
        acc = Account.from_key(clean)
        wallets.append({
            "wallet": idx,
            "address": Web3.to_checksum_address(acc.address),
            "pk": clean,
        })
    return wallets


def owner_of(contract_address: str, token_id: int, rpc_urls: list[str] | None = None) -> str:
    """Read owner of ERC-721 token."""
    w3 = Web3()
    sel = "0x" + w3.keccak(text="ownerOf(uint256)")[:4].hex()
    from eth_abi import encode
    call_data = sel + encode(["uint256"], [token_id]).hex()
    res_hex = rpc_call("eth_call", [{"to": Web3.to_checksum_address(contract_address), "data": call_data}, "latest"], rpc_urls=rpc_urls)
    if res_hex and len(res_hex) >= 66:
        return Web3.to_checksum_address("0x" + res_hex[-40:])
    return "0x0000000000000000000000000000000000000000"
