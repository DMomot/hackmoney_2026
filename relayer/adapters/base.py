"""Base Adapter - unified interface for all trading platforms"""
from abc import ABC, abstractmethod
from typing import Dict, Any


class BaseAdapter(ABC):
    """
    Base adapter for all trading platforms.
    All adapters must implement these methods.
    """

    # Platform metadata (override in child classes)
    PLATFORM_ID: int = 0
    PLATFORM_NAME: str = "Unknown"
    CHAIN_ID: int = 0
    DECIMALS: int = 18

    # Auth state
    _authenticated: bool = False

    @property
    def decimals(self) -> int:
        return self.DECIMALS

    # --- Auth Methods ---

    @abstractmethod
    def authenticate(self) -> bool:
        """Authenticate with the platform API"""
        pass

    def ensure_authenticated(self):
        """Ensure we're authenticated before API calls"""
        if not self._authenticated:
            self.authenticate()

    # --- Order Methods ---

    @abstractmethod
    def place_order(
        self,
        token_id: str,
        market_id: int,
        amount: float,
        price: float,
        side: str,
    ) -> Dict[str, Any]:
        """
        Place order on the platform

        Args:
            token_id: Token/outcome ID
            market_id: Market ID
            amount: Amount (shares for SELL, cost for BUY)
            price: Price per share (0.0 - 1.0)
            side: "BUY" or "SELL"

        Returns:
            Order response with orderId, status, etc.
        """
        pass

    # --- Platform Data ---

    @property
    @abstractmethod
    def relayer_address(self) -> str:
        """Relayer wallet address"""
        pass

    # --- Balance Methods ---

    @abstractmethod
    def get_stablecoin_balance(self, address: str = None) -> int:
        """Get USDC/USDT balance in wei"""
        pass

    @abstractmethod
    def get_token_balance(self, address: str, token_id: str) -> int:
        """Get ERC1155 token balance in wei"""
        pass

    # --- Transfer Methods ---

    @abstractmethod
    def transfer_usdt_from_user(self, user_address: str, amount_wei: int) -> str:
        """Transfer USDC/USDT from user to relayer"""
        pass

    @abstractmethod
    def transfer_erc1155_from_user(self, user_address: str, token_id: str, amount_wei: int) -> str:
        """Transfer ERC1155 from user to relayer"""
        pass

    @abstractmethod
    def transfer_usdt_to_user(self, user_address: str, amount_wei: int) -> str:
        """Transfer USDC/USDT to user"""
        pass

    @abstractmethod
    def transfer_erc1155_to_user(self, user_address: str, token_id: str, amount_wei: int) -> str:
        """Transfer ERC1155 to user"""
        pass

    # --- Approval Methods ---

    @abstractmethod
    def check_erc1155_approval(self, owner: str, operator: str) -> bool:
        """Check if operator is approved for ERC1155"""
        pass

    @abstractmethod
    def check_erc20_approval(self, owner: str, spender: str) -> int:
        """Check ERC20 allowance"""
        pass

    @abstractmethod
    def set_erc1155_approval(self, owner: str, operator: str) -> str:
        """Approve operator for ERC1155"""
        pass

    @abstractmethod
    def set_erc20_approval(self, owner: str, spender: str, amount: int = None) -> str:
        """Approve spender for ERC20"""
        pass

    # --- Orderbook Methods ---

    @abstractmethod
    def get_orderbook(self, token_id: str) -> Dict[str, Any]:
        """
        Get full orderbook for token

        Returns:
            {"bids": [{"price": float, "size": float}, ...],
             "asks": [{"price": float, "size": float}, ...]}
        """
        pass

    @abstractmethod
    def get_best_offer(self, token_id: str, side: str) -> Dict[str, Any]:
        """
        Get best price and liquidity for BUY or SELL

        Returns:
            {"price": float, "size": float, "side": str}
        """
        pass

    # --- Order Status Methods ---

    @abstractmethod
    def get_order(self, order_id: str, token_id: str = None) -> Dict[str, Any]:
        """
        Get order status by order ID

        Returns:
            {"order_id": str, "status": str, "original_amount": int,
             "filled_amount": int, "remaining_amount": int,
             "side": str, "price": float}
        """
        pass

    @abstractmethod
    def find_incoming_erc1155(
        self, token_id: str, expected_amount: int, blocks_back: int = 50
    ) -> Dict[str, Any]:
        """
        Find incoming ERC1155 transfer in recent blocks (for BUY orders)

        Returns:
            {"found": bool, "tx_hash": str, "amount": int, "block": int}
        """
        pass

    @abstractmethod
    def find_incoming_erc20(
        self, expected_amount: int, blocks_back: int = 50
    ) -> Dict[str, Any]:
        """
        Find incoming ERC20 transfer in recent blocks (for SELL orders)

        Returns:
            {"found": bool, "tx_hash": str, "amount": int, "block": int}
        """
        pass

    def check_user_approval(self, user_address: str) -> Dict[str, bool]:
        """Check if user approved relayer for token transfers"""
        return {"ctf": False, "usdt": False}

    def setup_approvals(self) -> Dict[str, str]:
        """Setup approvals for relayer to spend tokens from platform wallet"""
        return {}
