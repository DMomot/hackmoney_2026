"""
Limitless Exchange Adapter
On Base chain (8453), uses USDC (6 decimals), EOA direct.
Uses limitless-sdk for signing, direct HTTP for API calls.
"""

import asyncio
import logging
import os
import requests as req_lib
from typing import Dict, Any
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct

from .base import BaseAdapter

logger = logging.getLogger(__name__)

API_BASE = "https://api.limitless.exchange"



class LimitlessAdapter(BaseAdapter):
    """Limitless Exchange adapter, Base chain, EOA direct."""

    PLATFORM_ID = 3
    PLATFORM_NAME = "Limitless"
    CHAIN_ID = 8453  # Base
    DECIMALS = 6  # USDC

    USDC_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    CTF_ADDRESS = "0xC9c98965297Bc527861c898329Ee280632B76e18"

    def __init__(self, private_key: str, rpc_url: str = None):
        if not private_key.startswith("0x"):
            private_key = "0x" + private_key
        self.private_key = private_key
        self.rpc_url = rpc_url or "https://mainnet.base.org"
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        self.account = Account.from_key(private_key)
        self._client = None
        self._authenticated = False
        self._owner_id = None
        self._auth_headers = {}
        self.api_key = os.getenv("LIMITLESS_API_KEY", "")
        logger.info(f"Limitless adapter initialized, EOA={self.account.address}")

    # --- Auth ---

    def _login(self):
        """Login to Limitless API, get owner_id and session cookies."""
        msg_resp = req_lib.get(f"{API_BASE}/auth/signing-message", timeout=10)
        msg = msg_resp.text
        signed = self.account.sign_message(encode_defunct(text=msg))
        sig = "0x" + signed.signature.hex()
        msg_hex = "0x" + msg.encode().hex()
        self._auth_headers = {
            "x-account": self.account.address,
            "x-signature": sig,
            "x-signing-message": msg_hex,
        }
        # Use persistent session for cookies (no API key — conflicts with session auth)
        self._session = req_lib.Session()
        resp = self._session.post(f"{API_BASE}/auth/login",
            json={"client": "eoa"},
            headers={**self._auth_headers, "Content-Type": "application/json"},
            timeout=10)
        if resp.status_code != 200:
            raise Exception(f"Limitless login failed: {resp.status_code} {resp.text}")
        data = resp.json()
        self._owner_id = data.get("id")
        logger.info(f"Limitless login OK, owner_id={self._owner_id}")

    def authenticate(self) -> bool:
        try:
            self._login()
            _ = self.client
            self._authenticated = True
            return True
        except Exception as e:
            logger.error(f"Limitless auth failed: {e}")
            return False

    @property
    def client(self):
        if self._client is None:
            from limitless_sdk import LimitlessClient
            self._client = LimitlessClient(
                private_key=self.private_key,
                api_key=self.api_key if self.api_key else None,
                additional_headers=self._auth_headers,
            )
        return self._client

    # --- Order Methods ---

    # Exchange contract on Base (from API error response)
    CTF_EXCHANGE = "0x5a38afc17F7E97ad8d6C547ddb837E40B4aEDfC6"

    def _sign_order_eip712(self, order_data: dict, exchange_address: str = None) -> str:
        """Sign order using EIP-712 with Limitless exchange contract."""
        from eth_account.messages import encode_typed_data

        contract = exchange_address or self.CTF_EXCHANGE
        domain = {
            "name": "Limitless CTF Exchange",
            "version": "1",
            "chainId": self.CHAIN_ID,
            "verifyingContract": contract,
        }
        types = {
            "Order": [
                {"name": "salt", "type": "uint256"},
                {"name": "maker", "type": "address"},
                {"name": "signer", "type": "address"},
                {"name": "taker", "type": "address"},
                {"name": "tokenId", "type": "uint256"},
                {"name": "makerAmount", "type": "uint256"},
                {"name": "takerAmount", "type": "uint256"},
                {"name": "expiration", "type": "uint256"},
                {"name": "nonce", "type": "uint256"},
                {"name": "feeRateBps", "type": "uint256"},
                {"name": "side", "type": "uint8"},
                {"name": "signatureType", "type": "uint8"},
            ]
        }
        encoded = encode_typed_data(domain, types, order_data)
        signed = self.account.sign_message(encoded)
        sig = signed.signature.hex()
        return "0x" + sig if not sig.startswith("0x") else sig

    def place_order(self, token_id: str, market_id: int, amount: float, price: float, side: str) -> Dict[str, Any]:
        """Place order on Limitless. Fully sync, no SDK async. market_id = slug."""
        import random
        market_slug = str(market_id)
        side_int = 0 if side.upper() == "BUY" else 1

        if not self._owner_id:
            self._login()

        # Fetch market data (sync)
        market = self._session.get(f"{API_BASE}/markets/{market_slug}", timeout=10).json()
        tokens = market.get("tokens", {})
        real_token_id = str(tokens.get("yes") if str(tokens.get("yes")) == str(token_id) else tokens.get("no"))

        # Get exchange address for this market (nested in venue.exchange)
        venue = market.get("venue") or {}
        exchange_addr = venue.get("exchange") or self.CTF_EXCHANGE
        logger.info(f"Limitless market {market_slug}: exchange={exchange_addr}")

        # Ensure approvals for this exchange contract
        if exchange_addr.lower() != self.CTF_EXCHANGE.lower():
            try:
                allowance = self.check_erc20_approval(self.account.address, exchange_addr)
                if allowance <= 0:
                    self.set_erc20_approval(self.account.address, exchange_addr)
                    logger.info(f"Approved USDC for exchange {exchange_addr}")
            except Exception as e:
                logger.warning(f"Failed to approve exchange {exchange_addr}: {e}")

        salt = random.randint(1, 2**32 - 1)

        is_fok = True  # Using FOK orders

        if side_int == 0:  # BUY: amount = USDC to spend
            usdc_raw = int(amount * 1e6)
            if is_fok:
                maker_amount, taker_amount = usdc_raw, 1
            else:
                shares_raw = int((amount / price) * 1e6)
                shares_raw = (shares_raw // 1000) * 1000
                usdc_raw = int(shares_raw * price)
                maker_amount, taker_amount = usdc_raw, shares_raw
        else:  # SELL: amount = shares to sell
            raw_shares = (int(amount * 1e6) // 1000) * 1000
            if is_fok:
                maker_amount, taker_amount = raw_shares, 1
            else:
                maker_amount = raw_shares
                taker_amount = int(raw_shares * price)
            logger.info(f"SELL amounts: shares={maker_amount}, taker={taker_amount}")

        order_data = {
            "salt": salt, "maker": self.account.address, "signer": self.account.address,
            "taker": "0x0000000000000000000000000000000000000000",
            "tokenId": int(real_token_id), "makerAmount": maker_amount,
            "takerAmount": taker_amount, "expiration": 0, "nonce": 0,
            "feeRateBps": 300, "side": side_int, "signatureType": 0,
        }
        signature = self._sign_order_eip712(order_data, exchange_addr)

        payload = {
            "order": {
                "salt": salt, "maker": self.account.address, "signer": self.account.address,
                "taker": "0x0000000000000000000000000000000000000000",
                "tokenId": real_token_id, "makerAmount": maker_amount,
                "takerAmount": taker_amount, "expiration": "0", "nonce": 0,
                "feeRateBps": 300, "side": side_int, "signature": signature,
                "signatureType": 0,
            },
            "ownerId": self._owner_id, "orderType": "FOK", "marketSlug": market_slug,
        }

        logger.info(f"Limitless {side} {amount} @ {price}, slug={market_slug}")

        # Submit order via authenticated session
        resp = self._session.post(f"{API_BASE}/orders", json=payload, timeout=15)
        if resp.status_code not in (200, 201):
            raise Exception(f"Limitless order failed: {resp.status_code} {resp.text}")

        result = resp.json()
        order_data = result.get("order", result)
        logger.info(f"Limitless order placed: id={order_data.get('id')}")
        return {
            "orderId": order_data.get("id"),
            "status": "MATCHED" if result.get("makerMatches") else "NEW",
            "price": price,
            "side": side.upper(),
        }

    # --- Properties ---

    @property
    def relayer_address(self) -> str:
        return self.account.address

    # --- Balance Methods ---

    def get_stablecoin_balance(self, address: str = None) -> int:
        addr = Web3.to_checksum_address(address or self.account.address)
        abi = [{"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf",
                "outputs": [{"type": "uint256"}], "type": "function"}]
        usdc = self.w3.eth.contract(address=Web3.to_checksum_address(self.USDC_ADDRESS), abi=abi)
        return usdc.functions.balanceOf(addr).call()

    def get_usdc_balance(self) -> int:
        return self.get_stablecoin_balance()

    def get_token_balance(self, address: str, token_id: str) -> int:
        addr = Web3.to_checksum_address(address)
        abi = [{"inputs": [{"name": "account", "type": "address"}, {"name": "id", "type": "uint256"}],
                "name": "balanceOf", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"}]
        ctf = self.w3.eth.contract(address=Web3.to_checksum_address(self.CTF_ADDRESS), abi=abi)
        return ctf.functions.balanceOf(addr, int(token_id)).call()

    def get_shares_balance(self, token_id: str) -> int:
        return self.get_token_balance(self.account.address, token_id)

    def get_user_shares_balance(self, token_id: str, user_address: str) -> int:
        return self.get_token_balance(user_address, token_id)

    # --- Transfer Methods ---

    def _send_tx(self, tx) -> str:
        signed = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return "0x" + tx_hash.hex()

    def transfer_usdt_from_user(self, user_address: str, amount_wei: int) -> str:
        """TransferFrom USDC from user to relayer on Base."""
        abi = [{"inputs": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"},
                           {"name": "amount", "type": "uint256"}], "name": "transferFrom",
                "outputs": [{"type": "bool"}], "type": "function"}]
        usdc = self.w3.eth.contract(address=Web3.to_checksum_address(self.USDC_ADDRESS), abi=abi)
        tx = usdc.functions.transferFrom(
            Web3.to_checksum_address(user_address), self.account.address, amount_wei
        ).build_transaction({
            "from": self.account.address, "gas": 100000,
            "maxFeePerGas": self.w3.eth.gas_price * 2,
            "maxPriorityFeePerGas": self.w3.eth.max_priority_fee,
            "nonce": self.w3.eth.get_transaction_count(self.account.address, "pending"),
            "chainId": self.CHAIN_ID,
        })
        return self._send_tx(tx)

    def transfer_erc1155_from_user(self, user_address: str, token_id: str, amount_wei: int) -> str:
        abi = [{"inputs": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"},
                           {"name": "id", "type": "uint256"}, {"name": "amount", "type": "uint256"},
                           {"name": "data", "type": "bytes"}], "name": "safeTransferFrom",
                "outputs": [], "type": "function"}]
        ctf = self.w3.eth.contract(address=Web3.to_checksum_address(self.CTF_ADDRESS), abi=abi)
        tx = ctf.functions.safeTransferFrom(
            Web3.to_checksum_address(user_address), self.account.address, int(token_id), amount_wei, b""
        ).build_transaction({
            "from": self.account.address, "gas": 200000,
            "maxFeePerGas": self.w3.eth.gas_price * 2,
            "maxPriorityFeePerGas": self.w3.eth.max_priority_fee,
            "nonce": self.w3.eth.get_transaction_count(self.account.address, "pending"),
            "chainId": self.CHAIN_ID,
        })
        return self._send_tx(tx)

    def transfer_usdt_to_user(self, user_address: str, amount_wei: int) -> str:
        abi = [{"inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}],
                "name": "transfer", "outputs": [{"type": "bool"}], "type": "function"}]
        usdc = self.w3.eth.contract(address=Web3.to_checksum_address(self.USDC_ADDRESS), abi=abi)
        tx = usdc.functions.transfer(Web3.to_checksum_address(user_address), amount_wei).build_transaction({
            "from": self.account.address, "gas": 100000,
            "maxFeePerGas": self.w3.eth.gas_price * 2,
            "maxPriorityFeePerGas": self.w3.eth.max_priority_fee,
            "nonce": self.w3.eth.get_transaction_count(self.account.address, "pending"),
            "chainId": self.CHAIN_ID,
        })
        return self._send_tx(tx)

    def transfer_erc1155_to_user(self, user_address: str, token_id: str, amount_wei: int) -> str:
        abi = [{"inputs": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"},
                           {"name": "id", "type": "uint256"}, {"name": "amount", "type": "uint256"},
                           {"name": "data", "type": "bytes"}], "name": "safeTransferFrom",
                "outputs": [], "type": "function"}]
        ctf = self.w3.eth.contract(address=Web3.to_checksum_address(self.CTF_ADDRESS), abi=abi)
        tx = ctf.functions.safeTransferFrom(
            self.account.address, Web3.to_checksum_address(user_address), int(token_id), amount_wei, b""
        ).build_transaction({
            "from": self.account.address, "gas": 200000,
            "maxFeePerGas": self.w3.eth.gas_price * 2,
            "maxPriorityFeePerGas": self.w3.eth.max_priority_fee,
            "nonce": self.w3.eth.get_transaction_count(self.account.address, "pending"),
            "chainId": self.CHAIN_ID,
        })
        return self._send_tx(tx)

    def transfer_shares(self, token_id: str, to_address: str, amount: int) -> dict:
        """Transfer ERC1155 and return {tx_hash, success}."""
        h = self.transfer_erc1155_to_user(to_address, token_id, amount)
        receipt = self.w3.eth.wait_for_transaction_receipt(h, timeout=120)
        return {"tx_hash": h, "success": receipt["status"] == 1}

    # --- Approval Methods ---

    def check_erc1155_approval(self, owner: str, operator: str) -> bool:
        abi = [{"inputs": [{"name": "account", "type": "address"}, {"name": "operator", "type": "address"}],
                "name": "isApprovedForAll", "outputs": [{"type": "bool"}], "type": "function"}]
        ctf = self.w3.eth.contract(address=Web3.to_checksum_address(self.CTF_ADDRESS), abi=abi)
        return ctf.functions.isApprovedForAll(
            Web3.to_checksum_address(owner), Web3.to_checksum_address(operator)).call()

    def check_erc20_approval(self, owner: str, spender: str) -> int:
        abi = [{"inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
                "name": "allowance", "outputs": [{"type": "uint256"}], "type": "function"}]
        usdc = self.w3.eth.contract(address=Web3.to_checksum_address(self.USDC_ADDRESS), abi=abi)
        return usdc.functions.allowance(
            Web3.to_checksum_address(owner), Web3.to_checksum_address(spender)).call()

    def set_erc1155_approval(self, owner: str, operator: str) -> str:
        abi = [{"inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}],
                "name": "setApprovalForAll", "outputs": [], "type": "function"}]
        ctf = self.w3.eth.contract(address=Web3.to_checksum_address(self.CTF_ADDRESS), abi=abi)
        tx = ctf.functions.setApprovalForAll(Web3.to_checksum_address(operator), True).build_transaction({
            "from": self.account.address, "gas": 100000,
            "maxFeePerGas": self.w3.eth.gas_price * 2,
            "maxPriorityFeePerGas": self.w3.eth.max_priority_fee,
            "nonce": self.w3.eth.get_transaction_count(self.account.address, "pending"),
            "chainId": self.CHAIN_ID,
        })
        return self._send_tx(tx)

    def set_erc20_approval(self, owner: str, spender: str, amount: int = None) -> str:
        if amount is None:
            amount = 2**256 - 1
        abi = [{"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
                "name": "approve", "outputs": [{"type": "bool"}], "type": "function"}]
        usdc = self.w3.eth.contract(address=Web3.to_checksum_address(self.USDC_ADDRESS), abi=abi)
        tx = usdc.functions.approve(Web3.to_checksum_address(spender), amount).build_transaction({
            "from": self.account.address, "gas": 100000,
            "maxFeePerGas": self.w3.eth.gas_price * 2,
            "maxPriorityFeePerGas": self.w3.eth.max_priority_fee,
            "nonce": self.w3.eth.get_transaction_count(self.account.address, "pending"),
            "chainId": self.CHAIN_ID,
        })
        return self._send_tx(tx)

    # --- Orderbook ---

    def get_orderbook(self, token_id: str) -> dict:
        """Get orderbook. token_id = market slug for Limitless. Fully sync."""
        resp = req_lib.get(f"{API_BASE}/markets/{token_id}/orderbook", timeout=10)
        data = resp.json()
        divisor = 10 ** self.DECIMALS
        bids = sorted(
            [{"price": float(b["price"]), "size": float(b["size"]) / divisor} for b in data.get("bids", [])],
            key=lambda x: x["price"], reverse=True)
        asks = sorted(
            [{"price": float(a["price"]), "size": float(a["size"]) / divisor} for a in data.get("asks", [])],
            key=lambda x: x["price"])
        return {"bids": bids, "asks": asks}

    def get_best_offer(self, token_id: str, side: str) -> dict:
        book = self.get_orderbook(token_id)
        if side.upper() == "BUY":
            if book["asks"]:
                return {"price": book["asks"][0]["price"], "size": book["asks"][0]["size"], "side": "BUY"}
            return {"price": 0, "size": 0, "side": "BUY"}
        else:
            if book["bids"]:
                return {"price": book["bids"][0]["price"], "size": book["bids"][0]["size"], "side": "SELL"}
            return {"price": 0, "size": 0, "side": "SELL"}

    # --- Order Status ---

    def get_order(self, order_id: str, token_id: str = None) -> dict:
        """Get order status. Fully sync."""
        try:
            resp = self._session.get(f"{API_BASE}/orders/{order_id}", timeout=10)
            if resp.status_code == 200:
                o = resp.json()
                return {
                    "order_id": order_id,
                    "status": o.get("status", "UNKNOWN"),
                    "original_amount": 0, "filled_amount": 0, "remaining_amount": 0,
                    "side": o.get("side", "UNKNOWN"),
                    "price": float(o.get("price", 0)),
                }
        except Exception:
            pass
        return {"order_id": order_id, "status": "UNKNOWN"}

    # --- Find Incoming Transfers ---

    def find_incoming_erc1155(self, token_id: str, expected_amount: int, blocks_back: int = 50) -> dict:
        current = self.w3.eth.block_number
        from_block = max(0, current - blocks_back)
        sig = "0x" + self.w3.keccak(text="TransferSingle(address,address,address,uint256,uint256)").hex()
        wallet = self.account.address
        logs = self.w3.eth.get_logs({
            "fromBlock": from_block, "toBlock": "latest",
            "address": Web3.to_checksum_address(self.CTF_ADDRESS),
            "topics": [sig, None, None, "0x" + wallet[2:].lower().zfill(64)],
        })
        for log in reversed(logs):
            data = log["data"].hex() if isinstance(log["data"], bytes) else log["data"]
            if data.startswith("0x"):
                data = data[2:]
            log_tid = int(data[0:64], 16)
            log_val = int(data[64:128], 16)
            if str(log_tid) == token_id and log_val >= expected_amount * 0.95:
                h = log["transactionHash"].hex() if isinstance(log["transactionHash"], bytes) else log["transactionHash"]
                return {"found": True, "tx_hash": "0x" + h if not h.startswith("0x") else h, "amount": log_val, "block": log["blockNumber"]}
        return {"found": False, "tx_hash": None, "amount": 0, "block": 0}

    def find_incoming_erc20(self, expected_amount: int, blocks_back: int = 50) -> dict:
        current = self.w3.eth.block_number
        from_block = max(0, current - blocks_back)
        sig = "0x" + self.w3.keccak(text="Transfer(address,address,uint256)").hex()
        wallet = self.account.address
        logs = self.w3.eth.get_logs({
            "fromBlock": from_block, "toBlock": "latest",
            "address": Web3.to_checksum_address(self.USDC_ADDRESS),
            "topics": [sig, None, "0x" + wallet[2:].lower().zfill(64)],
        })
        for log in reversed(logs):
            data = log["data"].hex() if isinstance(log["data"], bytes) else log["data"]
            if data.startswith("0x"):
                data = data[2:]
            val = int(data, 16)
            if val >= expected_amount * 0.95:
                h = log["transactionHash"].hex() if isinstance(log["transactionHash"], bytes) else log["transactionHash"]
                return {"found": True, "tx_hash": "0x" + h if not h.startswith("0x") else h, "amount": val, "block": log["blockNumber"]}
        return {"found": False, "tx_hash": None, "amount": 0, "block": 0}

    def check_user_approval(self, user_address: str) -> Dict[str, bool]:
        eoa = self.account.address
        results = {}
        try:
            results["ctf"] = self.check_erc1155_approval(user_address, eoa)
        except:
            results["ctf"] = False
        try:
            results["usdc"] = self.check_erc20_approval(user_address, eoa) > 0
        except:
            results["usdc"] = False
        return results

    def setup_approvals(self) -> Dict[str, str]:
        """Approve Limitless CTF Exchange to spend USDC and CTF from relayer."""
        results = {}
        try:
            results["usdc"] = self.set_erc20_approval(self.account.address, self.CTF_EXCHANGE)
        except Exception as e:
            results["usdc"] = f"error: {e}"
        try:
            results["ctf"] = self.set_erc1155_approval(self.account.address, self.CTF_EXCHANGE)
        except Exception as e:
            results["ctf"] = f"error: {e}"
        return results
