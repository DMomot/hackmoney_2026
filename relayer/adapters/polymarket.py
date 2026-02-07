"""
Polymarket trading adapter
Handles token purchases on Polymarket CLOB using EOA directly
"""
import logging
from decimal import Decimal
from typing import Dict, Any
from web3 import Web3
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    MarketOrderArgs,
    PartialCreateOrderOptions,
    BalanceAllowanceParams,
    AssetType,
    OrderType,
)
from py_clob_client.order_builder.constants import BUY, SELL

from .base import BaseAdapter

logger = logging.getLogger(__name__)


class PolymarketAdapter(BaseAdapter):
    """
    Polymarket trading adapter for EOA integration

    Uses EOA directly with signature_type=0 for trading
    on both Regular and NegRisk markets
    """

    PLATFORM_ID = 1
    PLATFORM_NAME = "Polymarket"
    CHAIN_ID = 137  # Polygon
    DECIMALS = 6  # USDC

    API_BASE = "https://clob.polymarket.com"
    USE_EOA_DIRECTLY = True

    # Contract addresses
    CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
    CTF_EXCHANGE_REGULAR = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
    CTF_EXCHANGE_NEGRISK = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
    NEG_RISK_EXECUTOR = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"
    USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

    def __init__(self, private_key: str, proxy_wallet: str, rpc_url: str = None):
        if not private_key.startswith("0x"):
            private_key = "0x" + private_key
        self.private_key = private_key
        self.proxy_wallet = proxy_wallet
        self.rpc_url = rpc_url

        if rpc_url:
            self.w3 = Web3(Web3.HTTPProvider(rpc_url))
            self.account = self.w3.eth.account.from_key(private_key)
        else:
            self.w3 = None
            self.account = None

        self._client = None
        self._authenticated = False

    # --- Auth ---

    def authenticate(self) -> bool:
        try:
            self._client = self._create_client()
            self._authenticated = True
            return True
        except Exception as e:
            logger.error(f"Polymarket auth failed: {e}")
            return False

    @property
    def client(self) -> ClobClient:
        if self._client is None:
            self.authenticate()
        return self._client

    def _create_client(self) -> ClobClient:
        temp_client = ClobClient(
            host=self.API_BASE,
            key=self.private_key,
            chain_id=self.CHAIN_ID,
        )
        api_creds = temp_client.create_or_derive_api_creds()

        if self.USE_EOA_DIRECTLY:
            client = ClobClient(
                host=self.API_BASE,
                key=self.private_key,
                chain_id=self.CHAIN_ID,
                creds=api_creds,
            )
        else:
            client = ClobClient(
                host=self.API_BASE,
                key=self.private_key,
                chain_id=self.CHAIN_ID,
                creds=api_creds,
                funder=self.proxy_wallet,
                signature_type=2,
            )

        try:
            client.update_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
        except Exception:
            pass

        return client

    # --- Approvals ---

    def ensure_approvals(self, neg_risk: bool = False) -> Dict[str, bool]:
        if not self.w3 or not self.account:
            logger.warning("Web3 not initialized, cannot check approvals")
            return {}

        ctf_abi = [
            {"inputs": [{"name": "account", "type": "address"}, {"name": "operator", "type": "address"}], "name": "isApprovedForAll", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "view", "type": "function"},
            {"inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}], "name": "setApprovalForAll", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
        ]
        erc20_abi = [
            {"inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "name": "allowance", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
            {"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "approve", "outputs": [{"type": "bool"}], "type": "function"},
        ]

        ctf = self.w3.eth.contract(address=Web3.to_checksum_address(self.CTF_ADDRESS), abi=ctf_abi)
        usdc = self.w3.eth.contract(address=Web3.to_checksum_address(self.USDC_ADDRESS), abi=erc20_abi)

        approvals = {}
        contracts = [
            ("Regular Exchange", self.CTF_EXCHANGE_REGULAR),
            ("NegRisk Exchange", self.CTF_EXCHANGE_NEGRISK),
        ]
        if neg_risk:
            contracts.append(("NegRisk Executor", self.NEG_RISK_EXECUTOR))

        max_uint = 2**256 - 1

        # USDC approvals
        for name, addr in contracts:
            try:
                allowance = usdc.functions.allowance(
                    Web3.to_checksum_address(self.account.address),
                    Web3.to_checksum_address(addr),
                ).call()
                if allowance < 10**12:
                    logger.warning(f"USDC allowance for {name} low, approving...")
                    nonce = self.w3.eth.get_transaction_count(self.account.address, "pending")
                    tx = usdc.functions.approve(Web3.to_checksum_address(addr), max_uint).build_transaction({
                        "from": self.account.address, "nonce": nonce, "gas": 100000,
                        "gasPrice": self.w3.eth.gas_price, "chainId": self.CHAIN_ID,
                    })
                    signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
                    tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
                    receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
                    approvals[f"USDC-{name}"] = receipt["status"] == 1
                else:
                    approvals[f"USDC-{name}"] = True
            except Exception as e:
                logger.error(f"USDC approval for {name} failed: {e}")
                approvals[f"USDC-{name}"] = False

        # CTF approvals
        for name, addr in contracts:
            try:
                ok = ctf.functions.isApprovedForAll(
                    Web3.to_checksum_address(self.account.address),
                    Web3.to_checksum_address(addr),
                ).call()
                if not ok:
                    logger.warning(f"{name} not approved, approving...")
                    nonce = self.w3.eth.get_transaction_count(self.account.address, "pending")
                    tx = ctf.functions.setApprovalForAll(Web3.to_checksum_address(addr), True).build_transaction({
                        "from": self.account.address, "nonce": nonce, "gas": 100000,
                        "gasPrice": self.w3.eth.gas_price, "chainId": self.CHAIN_ID,
                    })
                    signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
                    tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
                    receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
                    approvals[name] = receipt["status"] == 1
                else:
                    approvals[name] = True
            except Exception as e:
                logger.error(f"CTF approval for {name} failed: {e}")
                approvals[name] = False

        return approvals

    # --- Place Order ---

    def place_order(
        self,
        token_id: str,
        market_id: int,
        amount: float,
        price: float,
        side: str,
        condition_id: str = None,
    ) -> Dict[str, Any]:
        client = self.client

        neg_risk = False
        try:
            neg_risk = client.get_neg_risk(token_id)
            logger.info(f"Token {token_id[:20]}... neg_risk: {neg_risk}")
        except Exception as e:
            logger.warning(f"Failed to get neg_risk: {e}")

        try:
            approvals = self.ensure_approvals(neg_risk=neg_risk)
            logger.info(f"Approvals: {approvals}")
        except Exception as e:
            logger.warning(f"Failed to ensure approvals: {e}")

        # Update COLLATERAL balance cache
        try:
            client.update_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=0)
            )
            bal = client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=0)
            )
            logger.info(f"COLLATERAL balance: {int(bal.get('balance', 0)) / 1e6:.4f} USDC")
        except Exception as e:
            logger.warning(f"Failed to update COLLATERAL balance: {e}")

        # For SELL: update CONDITIONAL balance
        if side.upper() == "SELL":
            try:
                client.update_balance_allowance(
                    BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=token_id, signature_type=0)
                )
            except Exception as e:
                logger.warning(f"Failed to update CONDITIONAL balance: {e}")

        options = PartialCreateOrderOptions(neg_risk=neg_risk)

        import math
        if side.upper() == "BUY":
            # Floor to 2 decimals — USDC cents, avoid exceeding balance
            order_amount = math.floor(float(Decimal(str(amount)) * Decimal(str(price))) * 100) / 100
        else:
            order_amount = math.floor(amount * 100) / 100

        order_args = MarketOrderArgs(
            token_id=token_id,
            amount=order_amount,
            price=price,
            side=BUY if side.upper() == "BUY" else SELL,
            order_type=OrderType.FOK,
        )

        logger.info(f"Market Order (FOK): side={side.upper()}, price={price}, amount={order_amount}, neg_risk={neg_risk}")
        logger.info(f"Token: {token_id[:40]}...")

        signed_order = client.create_market_order(order_args, options=options)
        response = client.post_order(signed_order, orderType=OrderType.FOK)

        order_id = response.get("orderID") or response.get("orderId")
        logger.info(f"Order placed: id={order_id}, status={response.get('status')}")

        # Extract signed order params
        order_data = signed_order.dict() if hasattr(signed_order, 'dict') else (
            signed_order.__dict__ if hasattr(signed_order, '__dict__') else {}
        )
        response["_params"] = {
            "salt": order_data.get("salt"),
            "maker": order_data.get("maker"),
            "signer": order_data.get("signer"),
            "taker": order_data.get("taker"),
            "tokenId": order_data.get("tokenId") or order_data.get("token_id") or token_id,
            "makerAmount": order_data.get("makerAmount") or order_data.get("maker_amount"),
            "takerAmount": order_data.get("takerAmount") or order_data.get("taker_amount"),
            "expiration": order_data.get("expiration"),
            "nonce": order_data.get("nonce"),
            "feeRateBps": order_data.get("feeRateBps") or order_data.get("fee_rate_bps"),
            "side": order_data.get("side"),
            "signatureType": order_data.get("signatureType") or order_data.get("signature_type"),
            "signature": order_data.get("signature"),
            "neg_risk": neg_risk,
            "market_id": market_id,
            "price": price,
            "amount": amount,
            "order_amount": order_amount,
        }
        return response

    # --- Market Info ---

    def get_market_info(self, condition_id: str) -> Dict[str, Any]:
        return self.client.get_market(condition_id)

    def check_token_type(self, token_id: str) -> bool:
        client = ClobClient(host=self.API_BASE, key=self.private_key, chain_id=self.CHAIN_ID)
        return client.get_neg_risk(token_id)

    # --- BaseAdapter Properties ---

    @property
    def relayer_address(self) -> str:
        return Web3.to_checksum_address(self.proxy_wallet)

    # --- Balances ---

    def get_stablecoin_balance(self, address: str = None) -> int:
        if not self.w3:
            raise RuntimeError("Web3 not initialized")
        addr = Web3.to_checksum_address(address or self.proxy_wallet)
        abi = [{"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"type": "uint256"}], "type": "function"}]
        usdc = self.w3.eth.contract(address=Web3.to_checksum_address(self.USDC_ADDRESS), abi=abi)
        return usdc.functions.balanceOf(addr).call()

    def get_token_balance(self, address: str, token_id: str) -> int:
        if not self.w3:
            raise RuntimeError("Web3 not initialized")
        abi = [{"inputs": [{"name": "account", "type": "address"}, {"name": "id", "type": "uint256"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"}]
        ctf = self.w3.eth.contract(address=Web3.to_checksum_address(self.CTF_ADDRESS), abi=abi)
        return ctf.functions.balanceOf(Web3.to_checksum_address(address), int(token_id)).call()

    # --- Transfers ---

    def _send_tx(self, tx) -> str:
        signed = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return "0x" + tx_hash.hex()

    def transfer_erc1155_to_user(self, user_address: str, token_id: str, amount_wei: int) -> str:
        if not self.w3:
            raise RuntimeError("Web3 not initialized")
        proxy = Web3.to_checksum_address(self.proxy_wallet)
        abi = [{"inputs": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"}, {"name": "id", "type": "uint256"}, {"name": "amount", "type": "uint256"}, {"name": "data", "type": "bytes"}], "name": "safeTransferFrom", "outputs": [], "type": "function"}]
        ctf = self.w3.eth.contract(address=Web3.to_checksum_address(self.CTF_ADDRESS), abi=abi)
        tx = ctf.functions.safeTransferFrom(proxy, Web3.to_checksum_address(user_address), int(token_id), amount_wei, b"").build_transaction({
            "from": proxy, "gas": 150000, "gasPrice": self.w3.eth.gas_price,
            "nonce": self.w3.eth.get_transaction_count(proxy), "chainId": self.CHAIN_ID,
        })
        return self._send_tx(tx)

    def transfer_usdt_to_user(self, user_address: str, amount_wei: int) -> str:
        if not self.w3:
            raise RuntimeError("Web3 not initialized")
        proxy = Web3.to_checksum_address(self.proxy_wallet)
        abi = [{"inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "transfer", "outputs": [{"type": "bool"}], "type": "function"}]
        usdc = self.w3.eth.contract(address=Web3.to_checksum_address(self.USDC_ADDRESS), abi=abi)
        tx = usdc.functions.transfer(Web3.to_checksum_address(user_address), amount_wei).build_transaction({
            "from": proxy, "gas": 100000, "gasPrice": self.w3.eth.gas_price,
            "nonce": self.w3.eth.get_transaction_count(proxy), "chainId": self.CHAIN_ID,
        })
        return self._send_tx(tx)

    def transfer_usdt_from_user(self, user_address: str, amount_wei: int) -> str:
        if not self.w3:
            raise RuntimeError("Web3 not initialized")
        proxy = Web3.to_checksum_address(self.proxy_wallet)
        abi = [{"inputs": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "transferFrom", "outputs": [{"type": "bool"}], "type": "function"}]
        usdc = self.w3.eth.contract(address=Web3.to_checksum_address(self.USDC_ADDRESS), abi=abi)
        tx = usdc.functions.transferFrom(Web3.to_checksum_address(user_address), proxy, amount_wei).build_transaction({
            "from": proxy, "gas": 100000, "gasPrice": self.w3.eth.gas_price,
            "nonce": self.w3.eth.get_transaction_count(proxy), "chainId": self.CHAIN_ID,
        })
        return self._send_tx(tx)

    def transfer_erc1155_from_user(self, user_address: str, token_id: str, amount_wei: int) -> str:
        if not self.w3:
            raise RuntimeError("Web3 not initialized")
        proxy = Web3.to_checksum_address(self.proxy_wallet)
        abi = [{"inputs": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"}, {"name": "id", "type": "uint256"}, {"name": "amount", "type": "uint256"}, {"name": "data", "type": "bytes"}], "name": "safeTransferFrom", "outputs": [], "type": "function"}]
        ctf = self.w3.eth.contract(address=Web3.to_checksum_address(self.CTF_ADDRESS), abi=abi)
        tx = ctf.functions.safeTransferFrom(Web3.to_checksum_address(user_address), proxy, int(token_id), amount_wei, b"").build_transaction({
            "from": proxy, "gas": 200000, "gasPrice": self.w3.eth.gas_price,
            "nonce": self.w3.eth.get_transaction_count(proxy), "chainId": self.CHAIN_ID,
        })
        return self._send_tx(tx)

    # --- Approvals (on-chain) ---

    def check_erc1155_approval(self, owner: str, operator: str) -> bool:
        if not self.w3:
            raise RuntimeError("Web3 not initialized")
        abi = [{"inputs": [{"name": "account", "type": "address"}, {"name": "operator", "type": "address"}], "name": "isApprovedForAll", "outputs": [{"name": "", "type": "bool"}], "type": "function"}]
        ctf = self.w3.eth.contract(address=Web3.to_checksum_address(self.CTF_ADDRESS), abi=abi)
        return ctf.functions.isApprovedForAll(Web3.to_checksum_address(owner), Web3.to_checksum_address(operator)).call()

    def check_erc20_approval(self, owner: str, spender: str) -> int:
        if not self.w3:
            raise RuntimeError("Web3 not initialized")
        abi = [{"inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"}]
        usdc = self.w3.eth.contract(address=Web3.to_checksum_address(self.USDC_ADDRESS), abi=abi)
        return usdc.functions.allowance(Web3.to_checksum_address(owner), Web3.to_checksum_address(spender)).call()

    def set_erc1155_approval(self, owner: str, operator: str) -> str:
        if not self.w3:
            raise RuntimeError("Web3 not initialized")
        proxy = Web3.to_checksum_address(self.proxy_wallet)
        abi = [{"inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}], "name": "setApprovalForAll", "outputs": [], "type": "function"}]
        ctf = self.w3.eth.contract(address=Web3.to_checksum_address(self.CTF_ADDRESS), abi=abi)
        tx = ctf.functions.setApprovalForAll(Web3.to_checksum_address(operator), True).build_transaction({
            "from": proxy, "gas": 100000, "gasPrice": self.w3.eth.gas_price,
            "nonce": self.w3.eth.get_transaction_count(proxy), "chainId": self.CHAIN_ID,
        })
        return self._send_tx(tx)

    def set_erc20_approval(self, owner: str, spender: str, amount: int = None) -> str:
        if not self.w3:
            raise RuntimeError("Web3 not initialized")
        proxy = Web3.to_checksum_address(self.proxy_wallet)
        if amount is None:
            amount = 2**256 - 1
        abi = [{"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "approve", "outputs": [{"type": "bool"}], "type": "function"}]
        usdc = self.w3.eth.contract(address=Web3.to_checksum_address(self.USDC_ADDRESS), abi=abi)
        tx = usdc.functions.approve(Web3.to_checksum_address(spender), amount).build_transaction({
            "from": proxy, "gas": 100000, "gasPrice": self.w3.eth.gas_price,
            "nonce": self.w3.eth.get_transaction_count(proxy), "chainId": self.CHAIN_ID,
        })
        return self._send_tx(tx)

    # --- Balance & Transfer ---

    def get_shares_balance(self, token_id: str) -> int:
        """Return raw CTF balance (6 decimals) for token_id on relayer wallet."""
        if not self.w3:
            raise RuntimeError("Web3 not initialized")
        abi = [{"inputs": [{"name": "account", "type": "address"}, {"name": "id", "type": "uint256"}],
                "name": "balanceOf", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"}]
        ctf = self.w3.eth.contract(address=Web3.to_checksum_address(self.CTF_ADDRESS), abi=abi)
        return ctf.functions.balanceOf(self.account.address, int(token_id)).call()

    def get_user_shares_balance(self, token_id: str, user_address: str) -> int:
        """Return raw CTF balance for token_id on any address."""
        if not self.w3:
            raise RuntimeError("Web3 not initialized")
        abi = [{"inputs": [{"name": "account", "type": "address"}, {"name": "id", "type": "uint256"}],
                "name": "balanceOf", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"}]
        ctf = self.w3.eth.contract(address=Web3.to_checksum_address(self.CTF_ADDRESS), abi=abi)
        return ctf.functions.balanceOf(Web3.to_checksum_address(user_address), int(token_id)).call()

    def get_usdc_balance(self) -> int:
        """Return raw USDC.e balance (6 decimals) on relayer wallet."""
        if not self.w3:
            raise RuntimeError("Web3 not initialized")
        abi = [{"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf",
                "outputs": [{"type": "uint256"}], "type": "function"}]
        usdc = self.w3.eth.contract(address=Web3.to_checksum_address(self.USDC_ADDRESS), abi=abi)
        return usdc.functions.balanceOf(self.account.address).call()

    def transfer_shares(self, token_id: str, to_address: str, amount: int) -> dict:
        """Transfer ERC1155 shares from relayer to user. Returns {tx_hash, success}."""
        if not self.w3 or not self.account:
            raise RuntimeError("Web3 not initialized")
        abi = [{"inputs": [
            {"name": "from", "type": "address"}, {"name": "to", "type": "address"},
            {"name": "id", "type": "uint256"}, {"name": "amount", "type": "uint256"},
            {"name": "data", "type": "bytes"}],
            "name": "safeTransferFrom", "outputs": [], "stateMutability": "nonpayable", "type": "function"}]
        ctf = self.w3.eth.contract(address=Web3.to_checksum_address(self.CTF_ADDRESS), abi=abi)
        gas_price = self.w3.eth.gas_price
        tx = ctf.functions.safeTransferFrom(
            self.account.address,
            Web3.to_checksum_address(to_address),
            int(token_id), amount, b"",
        ).build_transaction({
            "from": self.account.address,
            "nonce": self.w3.eth.get_transaction_count(self.account.address, "pending"),
            "gas": 100000,
            "maxFeePerGas": int(gas_price * 1.3),
            "maxPriorityFeePerGas": min(int(gas_price * 0.3), int(30e9)),
            "chainId": self.CHAIN_ID,
        })
        signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        h = "0x" + tx_hash.hex() if not tx_hash.hex().startswith("0x") else tx_hash.hex()
        logger.info(f"Transfer shares tx={h}, status={receipt['status']}")
        return {"tx_hash": h, "success": receipt["status"] == 1}

    # --- Orderbook ---

    def get_orderbook(self, token_id: str) -> dict:
        book = self.client.get_order_book(token_id)
        bids = sorted(
            [{"price": float(b.price), "size": float(b.size)} for b in (book.bids or [])],
            key=lambda x: x["price"], reverse=True,
        )
        asks = sorted(
            [{"price": float(a.price), "size": float(a.size)} for a in (book.asks or [])],
            key=lambda x: x["price"],
        )
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
        order = self.client.get_order(order_id)
        original = float(order.get("original_size", 0))
        matched = float(order.get("size_matched", 0))
        return {
            "order_id": order.get("id"),
            "status": order.get("status"),
            "original_amount": int(original * 1e6),
            "filled_amount": int(matched * 1e6),
            "remaining_amount": int((original - matched) * 1e6),
            "side": order.get("side"),
            "price": float(order.get("price", 0)),
            "raw": order,
        }

    # --- Find Incoming Transfers ---

    def find_incoming_erc1155(self, token_id: str, expected_amount: int, blocks_back: int = 50) -> dict:
        if not self.w3:
            raise RuntimeError("Web3 not initialized")
        current = self.w3.eth.block_number
        from_block = max(0, current - blocks_back)
        sig = "0x" + self.w3.keccak(text="TransferSingle(address,address,address,uint256,uint256)").hex()
        proxy = Web3.to_checksum_address(self.proxy_wallet)

        logs = self.w3.eth.get_logs({
            "fromBlock": from_block, "toBlock": "latest",
            "address": Web3.to_checksum_address(self.CTF_ADDRESS),
            "topics": [sig, None, None, "0x" + proxy[2:].lower().zfill(64)],
        })
        for log in reversed(logs):
            data = log["data"].hex() if isinstance(log["data"], bytes) else log["data"]
            if data.startswith("0x"):
                data = data[2:]
            log_tid = int(data[0:64], 16)
            log_val = int(data[64:128], 16)
            if str(log_tid) == token_id and log_val >= expected_amount * 0.95:
                tx_hash = log["transactionHash"].hex() if isinstance(log["transactionHash"], bytes) else log["transactionHash"]
                if not tx_hash.startswith("0x"):
                    tx_hash = "0x" + tx_hash
                return {"found": True, "tx_hash": tx_hash, "amount": log_val, "block": log["blockNumber"]}
        return {"found": False, "tx_hash": None, "amount": 0, "block": 0}

    def find_incoming_erc20(self, expected_amount: int, blocks_back: int = 50) -> dict:
        if not self.w3:
            raise RuntimeError("Web3 not initialized")
        current = self.w3.eth.block_number
        from_block = max(0, current - blocks_back)
        sig = "0x" + self.w3.keccak(text="Transfer(address,address,uint256)").hex()
        proxy = Web3.to_checksum_address(self.proxy_wallet)

        logs = self.w3.eth.get_logs({
            "fromBlock": from_block, "toBlock": "latest",
            "address": Web3.to_checksum_address(self.USDC_ADDRESS),
            "topics": [sig, None, "0x" + proxy[2:].lower().zfill(64)],
        })
        for log in reversed(logs):
            data = log["data"].hex() if isinstance(log["data"], bytes) else log["data"]
            if data.startswith("0x"):
                data = data[2:]
            log_val = int(data, 16)
            if log_val >= expected_amount * 0.95:
                tx_hash = log["transactionHash"].hex() if isinstance(log["transactionHash"], bytes) else log["transactionHash"]
                if not tx_hash.startswith("0x"):
                    tx_hash = "0x" + tx_hash
                return {"found": True, "tx_hash": tx_hash, "amount": log_val, "block": log["blockNumber"]}
        return {"found": False, "tx_hash": None, "amount": 0, "block": 0}

    # --- User Approval Check ---

    def check_user_approval(self, user_address: str) -> Dict[str, bool]:
        main_eoa = self.relayer_address
        results = {}
        try:
            results["ctf"] = self.check_erc1155_approval(user_address, main_eoa)
        except Exception as e:
            logger.error(f"CTF approval check failed: {e}")
            results["ctf"] = False
        try:
            allowance = self.check_erc20_approval(user_address, main_eoa)
            results["usdc"] = allowance > 0
            results["usdc_allowance"] = allowance
        except Exception as e:
            logger.error(f"USDC approval check failed: {e}")
            results["usdc"] = False
            results["usdc_allowance"] = 0
        logger.info(f"User {user_address[:10]}... approvals: CTF={results['ctf']}, USDC={results['usdc']}")
        return results
