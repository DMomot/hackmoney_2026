"""
Opinion Markets Adapter
Uses Smart Wallet (Gnosis Safe style) controlled by EOA
All transfers done via execTransaction
"""

import os
import time
import json
import logging
import requests
from typing import Dict, Any, Optional
from decimal import Decimal
from web3 import Web3
from eth_account import Account

from .base import BaseAdapter

logger = logging.getLogger(__name__)


class OpinionAdapter(BaseAdapter):
    """Opinion Markets adapter with Smart Wallet support"""

    PLATFORM_ID = 2
    PLATFORM_NAME = "Opinion Markets"
    CHAIN_ID = 56
    DECIMALS = 18  # BSC USDT has 18 decimals

    # Opinion API
    API_BASE = "https://proxy.opinion.trade:8443"
    OPENAPI_BASE = "https://openapi.opinion.trade/openapi"

    # Contracts
    CONDITIONAL_TOKENS = "0xAD1a38cEc043e70E83a3eC30443dB285ED10D774"
    USDT_ADDRESS = "0x55d398326f99059fF775485246999027B3197955"
    MULTISEND = "0x998739BFdAAdde7C933B942a68053933098f9EDa"
    CTF_EXCHANGE = "0x59047B5d5BB568730Eb5462eb1DEeB1fC17126Db"

    def __init__(self, private_key: str, smart_wallet: str, main_relayer_key: str, rpc_url: str = None):
        if not private_key.startswith('0x'):
            private_key = '0x' + private_key
        self.private_key = private_key
        self.smart_wallet = Web3.to_checksum_address(smart_wallet)
        self.account = Account.from_key(private_key)
        self.eoa_address = self.account.address

        if not main_relayer_key.startswith('0x'):
            main_relayer_key = '0x' + main_relayer_key
        self._main_account = Account.from_key(main_relayer_key)
        self._main_relayer_address = self._main_account.address

        self.rpc_url = rpc_url or os.getenv('BSC_RPC_URL', 'https://bsc-dataseed.binance.org')
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))

        self.api_key = os.getenv('OPINION_API_KEY')
        if not self.api_key:
            raise ValueError("OPINION_API_KEY not set")

        self._client = None
        self._authenticated = False

        logger.info(f"Opinion adapter initialized, EOA={self.eoa_address}, SW={self.smart_wallet}")

    def authenticate(self) -> bool:
        try:
            _ = self.client
            self._authenticated = True
            return True
        except Exception as e:
            logger.error(f"Opinion auth failed: {e}")
            return False

    @property
    def client(self):
        if self._client is None:
            from opinion_clob_sdk import Client
            self._client = Client(
                host=self.API_BASE,
                apikey=self.api_key,
                chain_id=self.CHAIN_ID,
                rpc_url=self.rpc_url,
                private_key=self.private_key,
                multi_sig_addr=self.smart_wallet,
                conditional_tokens_addr=self.CONDITIONAL_TOKENS,
                multisend_addr=self.MULTISEND,
                market_cache_ttl=60,
            )
        return self._client

    # --- Order Methods ---

    def place_order(self, token_id: str, market_id: int, amount: float, price: float, side: str) -> Dict[str, Any]:
        from decimal import Decimal, ROUND_DOWN
        from opinion_clob_sdk.chain.py_order_utils.model.sides import BUY, SELL
        from opinion_clob_sdk.chain.py_order_utils.model.order import PlaceOrderDataInput
        from opinion_clob_sdk.chain.py_order_utils.model.order_type import LIMIT_ORDER

        order_side = BUY if side.upper() == 'BUY' else SELL

        if side.upper() == 'BUY':
            order_data = PlaceOrderDataInput(
                marketId=market_id, tokenId=token_id, price=str(price),
                makerAmountInQuoteToken=amount, side=order_side, orderType=LIMIT_ORDER,
            )
            logger.info(f"Opinion BUY: {amount} USDT @ {price}")
        else:
            amount = float(Decimal(str(amount)).quantize(Decimal('0.01'), rounding=ROUND_DOWN))
            order_data = PlaceOrderDataInput(
                marketId=market_id, tokenId=token_id, price=str(price),
                makerAmountInBaseToken=amount, side=order_side, orderType=LIMIT_ORDER,
            )
            logger.info(f"Opinion SELL: {amount} shares @ {price}")

        result = self.client.place_order(order_data)

        if result.errno != 0:
            raise Exception(f"Opinion order failed: {result.errmsg}")

        od = result.result.order_data
        return {
            'orderId': od.order_id,
            'status': 'NEW' if od.status == 1 else 'FILLED',
            'price': od.price,
            'side': side.upper(),
            'outcome': od.outcome,
        }

    # --- Smart Wallet Execution ---

    def exec_transaction(self, to: str, value: int, data: bytes, operation: int = 0) -> str:
        from safe_eth.safe import Safe
        from safe_eth.eth import EthereumClient

        eth_client = EthereumClient(self.rpc_url)
        safe = Safe(self.smart_wallet, eth_client)

        safe_tx = safe.build_multisig_tx(
            to=Web3.to_checksum_address(to), value=value, data=data,
            operation=operation, safe_tx_gas=0, base_gas=0, gas_price=0,
            gas_token=None, refund_receiver=None,
        )
        safe_tx.sign(self.private_key)

        tx_hash, tx = safe_tx.execute(
            tx_sender_private_key=self.private_key,
            tx_gas=150000, tx_gas_price=int(0.05 * 10**9),
        )
        h = '0x' + tx_hash.hex()
        logger.info(f"Safe TX executed: {h}")
        return h

    # --- Transfer Methods ---

    def transfer_usdt_from_user(self, user_address: str, amount_wei: int) -> str:
        """Transfer USDT from user to smart wallet. Main EOA pays gas."""
        user = Web3.to_checksum_address(user_address)
        main_eoa = self._main_relayer_address
        abi = [{"inputs": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"},
                           {"name": "amount", "type": "uint256"}], "name": "transferFrom",
                "outputs": [{"type": "bool"}], "type": "function"}]
        usdt = self.w3.eth.contract(address=self.USDT_ADDRESS, abi=abi)
        tx = usdt.functions.transferFrom(user, self.smart_wallet, amount_wei).build_transaction({
            'from': main_eoa, 'gas': 100000, 'gasPrice': self.w3.eth.gas_price,
            'nonce': self.w3.eth.get_transaction_count(main_eoa), 'chainId': self.CHAIN_ID,
        })
        signed = self._main_account.sign_transaction(tx)
        h = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return '0x' + h.hex()

    def transfer_erc1155_from_user(self, user_address: str, token_id: str, amount_wei: int) -> str:
        """Transfer ERC1155 from user to smart wallet. Main EOA pays gas."""
        user = Web3.to_checksum_address(user_address)
        main_eoa = self._main_relayer_address
        abi = [{"inputs": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"},
                           {"name": "id", "type": "uint256"}, {"name": "amount", "type": "uint256"},
                           {"name": "data", "type": "bytes"}], "name": "safeTransferFrom",
                "outputs": [], "type": "function"}]
        ctf = self.w3.eth.contract(address=Web3.to_checksum_address(self.CONDITIONAL_TOKENS), abi=abi)
        tx = ctf.functions.safeTransferFrom(user, self.smart_wallet, int(token_id), amount_wei, b'').build_transaction({
            'from': main_eoa, 'gas': 150000, 'gasPrice': self.w3.eth.gas_price,
            'nonce': self.w3.eth.get_transaction_count(main_eoa), 'chainId': self.CHAIN_ID,
        })
        signed = self._main_account.sign_transaction(tx)
        h = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return '0x' + h.hex()

    def transfer_usdt_to_user(self, user_address: str, amount_wei: int) -> str:
        """Transfer USDT from smart wallet to user. Main EOA pays gas (needs transferFrom approval)."""
        to_addr = Web3.to_checksum_address(user_address)
        main_eoa = self._main_relayer_address
        abi = [{"inputs": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"},
                           {"name": "amount", "type": "uint256"}], "name": "transferFrom",
                "outputs": [{"type": "bool"}], "type": "function"}]
        usdt = self.w3.eth.contract(address=Web3.to_checksum_address(self.USDT_ADDRESS), abi=abi)
        tx = usdt.functions.transferFrom(self.smart_wallet, to_addr, amount_wei).build_transaction({
            'from': main_eoa, 'gas': 100000, 'gasPrice': self.w3.eth.gas_price,
            'nonce': self.w3.eth.get_transaction_count(main_eoa), 'chainId': self.CHAIN_ID,
        })
        signed = self._main_account.sign_transaction(tx)
        h = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return '0x' + h.hex()

    def transfer_erc1155_to_user(self, user_address: str, token_id: str, amount_wei: int) -> str:
        """Transfer ERC1155 from smart wallet to user. Main EOA pays gas (needs approval)."""
        to_addr = Web3.to_checksum_address(user_address)
        main_eoa = self._main_relayer_address
        abi = [{"inputs": [{"name": "from", "type": "address"}, {"name": "to", "type": "address"},
                           {"name": "id", "type": "uint256"}, {"name": "amount", "type": "uint256"},
                           {"name": "data", "type": "bytes"}], "name": "safeTransferFrom",
                "outputs": [], "type": "function"}]
        ctf = self.w3.eth.contract(address=Web3.to_checksum_address(self.CONDITIONAL_TOKENS), abi=abi)
        tx = ctf.functions.safeTransferFrom(self.smart_wallet, to_addr, int(token_id), amount_wei, b'').build_transaction({
            'from': main_eoa, 'gas': 150000, 'gasPrice': self.w3.eth.gas_price,
            'nonce': self.w3.eth.get_transaction_count(main_eoa), 'chainId': self.CHAIN_ID,
        })
        signed = self._main_account.sign_transaction(tx)
        h = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        return '0x' + h.hex()

    # --- Balance Methods ---

    @property
    def relayer_address(self) -> str:
        return self.smart_wallet

    def get_stablecoin_balance(self, address: str = None) -> int:
        addr = Web3.to_checksum_address(address or self.smart_wallet)
        abi = [{"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf",
                "outputs": [{"type": "uint256"}], "type": "function"}]
        usdt = self.w3.eth.contract(address=Web3.to_checksum_address(self.USDT_ADDRESS), abi=abi)
        return usdt.functions.balanceOf(addr).call()

    def get_usdt_balance(self) -> int:
        return self.get_stablecoin_balance()

    def get_token_balance(self, address: str, token_id: str) -> int:
        addr = Web3.to_checksum_address(address)
        abi = [{"inputs": [{"name": "account", "type": "address"}, {"name": "id", "type": "uint256"}],
                "name": "balanceOf", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"}]
        ctf = self.w3.eth.contract(address=Web3.to_checksum_address(self.CONDITIONAL_TOKENS), abi=abi)
        return ctf.functions.balanceOf(addr, int(token_id)).call()

    def get_shares_balance(self, token_id: str) -> int:
        """ERC1155 balance on smart wallet."""
        return self.get_token_balance(self.smart_wallet, token_id)

    def get_user_shares_balance(self, token_id: str, user_address: str) -> int:
        return self.get_token_balance(user_address, token_id)

    # --- Approval Methods ---

    def check_erc1155_approval(self, owner: str, operator: str) -> bool:
        abi = [{"inputs": [{"name": "account", "type": "address"}, {"name": "operator", "type": "address"}],
                "name": "isApprovedForAll", "outputs": [{"type": "bool"}], "type": "function"}]
        ctf = self.w3.eth.contract(address=Web3.to_checksum_address(self.CONDITIONAL_TOKENS), abi=abi)
        return ctf.functions.isApprovedForAll(
            Web3.to_checksum_address(owner), Web3.to_checksum_address(operator)).call()

    def check_erc20_approval(self, owner: str, spender: str) -> int:
        abi = [{"inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
                "name": "allowance", "outputs": [{"type": "uint256"}], "type": "function"}]
        usdt = self.w3.eth.contract(address=Web3.to_checksum_address(self.USDT_ADDRESS), abi=abi)
        return usdt.functions.allowance(
            Web3.to_checksum_address(owner), Web3.to_checksum_address(spender)).call()

    def set_erc1155_approval(self, owner: str, operator: str) -> str:
        abi = [{"inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}],
                "name": "setApprovalForAll", "outputs": [], "type": "function"}]
        ctf = self.w3.eth.contract(address=Web3.to_checksum_address(self.CONDITIONAL_TOKENS), abi=abi)
        data = ctf.encode_abi('setApprovalForAll', [Web3.to_checksum_address(operator), True])
        return self.exec_transaction(to=self.CONDITIONAL_TOKENS, value=0, data=bytes.fromhex(data[2:]))

    def set_erc20_approval(self, owner: str, spender: str, amount: int = None) -> str:
        if amount is None:
            amount = 2**256 - 1
        abi = [{"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
                "name": "approve", "outputs": [{"type": "bool"}], "type": "function"}]
        usdt = self.w3.eth.contract(address=Web3.to_checksum_address(self.USDT_ADDRESS), abi=abi)
        data = usdt.encode_abi('approve', [Web3.to_checksum_address(spender), amount])
        return self.exec_transaction(to=self.USDT_ADDRESS, value=0, data=bytes.fromhex(data[2:]))

    # --- Orderbook Methods ---

    def get_orderbook(self, token_id: str) -> dict:
        response = self.client.get_orderbook(token_id)
        if response.errno != 0:
            return {"bids": [], "asks": []}
        book = response.result
        bids = sorted([{"price": float(b.price), "size": float(b.size)} for b in (book.bids or [])],
                       key=lambda x: x['price'], reverse=True)
        asks = sorted([{"price": float(a.price), "size": float(a.size)} for a in (book.asks or [])],
                       key=lambda x: x['price'])
        return {"bids": bids, "asks": asks}

    def get_best_offer(self, token_id: str, side: str) -> dict:
        book = self.get_orderbook(token_id)
        if side.upper() == 'BUY':
            if book['asks']:
                return {"price": book['asks'][0]['price'], "size": book['asks'][0]['size'], "side": "BUY"}
            return {"price": 0, "size": 0, "side": "BUY"}
        else:
            if book['bids']:
                return {"price": book['bids'][0]['price'], "size": book['bids'][0]['size'], "side": "SELL"}
            return {"price": 0, "size": 0, "side": "SELL"}

    # --- Order Status ---

    def get_order(self, order_id: str, token_id: str = None) -> dict:
        response = self.client.get_order_by_id(order_id)
        if response.errno != 0:
            raise Exception(f"Failed to get order: {response.errmsg}")
        order = response.result.order_data
        original = int(float(order.order_amount) * 1e18) if order.order_amount else 0
        filled = int(float(order.filled_amount) * 1e18) if order.filled_amount else 0
        filled_shares = int(float(order.filled_shares) * 1e18) if hasattr(order, 'filled_shares') and order.filled_shares else 0
        status_map = {1: "OPEN", 2: "FILLED", 3: "CANCELLED", 4: "EXPIRED"}
        return {
            "order_id": order_id,
            "status": status_map.get(order.status, str(order.status)),
            "original_amount": original,
            "filled_amount": filled,
            "filled_shares": filled_shares,
            "remaining_amount": original - filled,
            "side": order.side_enum if hasattr(order, 'side_enum') else "UNKNOWN",
            "price": float(order.price) if order.price else 0,
        }

    def find_incoming_erc1155(self, token_id: str, expected_amount: int, blocks_back: int = 50) -> dict:
        current_block = self.w3.eth.block_number
        from_block = max(0, current_block - blocks_back)
        sig = '0x' + self.w3.keccak(text="TransferSingle(address,address,address,uint256,uint256)").hex()
        wallet = self.smart_wallet
        logs = self.w3.eth.get_logs({
            'fromBlock': from_block, 'toBlock': 'latest',
            'address': Web3.to_checksum_address(self.CONDITIONAL_TOKENS),
            'topics': [sig, None, None, '0x' + wallet[2:].lower().zfill(64)],
        })
        for log in reversed(logs):
            data = log['data'].hex() if isinstance(log['data'], bytes) else log['data']
            if data.startswith('0x'):
                data = data[2:]
            log_tid = int(data[0:64], 16)
            log_val = int(data[64:128], 16)
            if str(log_tid) == token_id and log_val >= expected_amount * 0.95:
                h = log['transactionHash'].hex() if isinstance(log['transactionHash'], bytes) else log['transactionHash']
                return {"found": True, "tx_hash": '0x' + h if not h.startswith('0x') else h, "amount": log_val, "block": log['blockNumber']}
        return {"found": False, "tx_hash": None, "amount": 0, "block": 0}

    def find_incoming_erc20(self, expected_amount: int, blocks_back: int = 50) -> dict:
        current_block = self.w3.eth.block_number
        from_block = max(0, current_block - blocks_back)
        sig = '0x' + self.w3.keccak(text="Transfer(address,address,uint256)").hex()
        wallet = self.smart_wallet
        logs = self.w3.eth.get_logs({
            'fromBlock': from_block, 'toBlock': 'latest',
            'address': Web3.to_checksum_address(self.USDT_ADDRESS),
            'topics': [sig, None, '0x' + wallet[2:].lower().zfill(64)],
        })
        for log in reversed(logs):
            data = log['data'].hex() if isinstance(log['data'], bytes) else log['data']
            if data.startswith('0x'):
                data = data[2:]
            val = int(data, 16)
            if val >= expected_amount * 0.95:
                h = log['transactionHash'].hex() if isinstance(log['transactionHash'], bytes) else log['transactionHash']
                return {"found": True, "tx_hash": '0x' + h if not h.startswith('0x') else h, "amount": val, "block": log['blockNumber']}
        return {"found": False, "tx_hash": None, "amount": 0, "block": 0}

    def check_user_approval(self, user_address: str) -> Dict[str, bool]:
        main_eoa = self._main_relayer_address
        results = {}
        try:
            results['ctf'] = self.check_erc1155_approval(user_address, main_eoa)
        except:
            results['ctf'] = False
        try:
            results['usdt'] = self.check_erc20_approval(user_address, main_eoa) > 0
        except:
            results['usdt'] = False
        return results

    def setup_approvals(self) -> Dict[str, str]:
        """Setup approvals for main EOA to spend tokens from smart wallet."""
        results = {}
        main_eoa = self._main_relayer_address
        try:
            results['usdt'] = self.set_erc20_approval(self.smart_wallet, main_eoa)
        except Exception as e:
            results['usdt'] = f"error: {e}"
        try:
            results['ctf'] = self.set_erc1155_approval(self.smart_wallet, main_eoa)
        except Exception as e:
            results['ctf'] = f"error: {e}"
        return results
