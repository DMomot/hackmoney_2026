"""Test placing a small order on Polymarket via adapter"""
import os, sys, logging, time
from dotenv import load_dotenv
from web3 import Web3
from eth_account import Account

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

POLYGON_RPC = "https://polygon-bor-rpc.publicnode.com"
PK = os.getenv("USER_PRIVATE_KEY")
USER_ADDR = Account.from_key(PK).address
w3 = Web3(Web3.HTTPProvider(POLYGON_RPC))
from web3.middleware import ExtraDataToPOAMiddleware
w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
account = Account.from_key(PK)

USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
EXCHANGE_REGULAR = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
EXCHANGE_NEGRISK = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
NEG_RISK_EXECUTOR = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

TOKEN_ID = "4394372887385518214471608448209527405727552777602031099972143344338178308080"
MARKET_ID = 558934
MAX_UINT = 2**256 - 1

print(f"User: {USER_ADDR}")
bal = w3.eth.get_balance(USER_ADDR)
print(f"MATIC: {bal / 1e18:.4f}")

erc20_abi = [
    {"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"type": "uint256"}], "type": "function"},
    {"inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "name": "allowance", "outputs": [{"type": "uint256"}], "type": "function"},
    {"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "approve", "outputs": [{"type": "bool"}], "type": "function"},
]
ctf_abi = [
    {"inputs": [{"name": "account", "type": "address"}, {"name": "operator", "type": "address"}], "name": "isApprovedForAll", "outputs": [{"name": "", "type": "bool"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}], "name": "setApprovalForAll", "outputs": [], "stateMutability": "nonpayable", "type": "function"},
]

usdc = w3.eth.contract(address=Web3.to_checksum_address(USDC_E), abi=erc20_abi)
ctf = w3.eth.contract(address=Web3.to_checksum_address(CTF), abi=ctf_abi)

usdc_bal = usdc.functions.balanceOf(USER_ADDR).call()
print(f"USDC.e: {usdc_bal / 1e6:.4f}")

def send_tx(tx_data):
    """Send tx with proper gas and wait for receipt"""
    gas_price = w3.eth.gas_price
    tx_data["maxFeePerGas"] = int(gas_price * 2)
    tx_data["maxPriorityFeePerGas"] = int(gas_price)
    signed = account.sign_transaction(tx_data)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"  tx: 0x{tx_hash.hex()}")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    print(f"  status: {'OK' if receipt['status']==1 else 'FAILED'}")
    return receipt

# Step 1: USDC approvals
contracts = [
    ("Regular Exchange", EXCHANGE_REGULAR),
    ("NegRisk Exchange", EXCHANGE_NEGRISK),
    ("NegRisk Executor", NEG_RISK_EXECUTOR),
]

for name, addr in contracts:
    allowance = usdc.functions.allowance(USER_ADDR, Web3.to_checksum_address(addr)).call()
    if allowance < 10**12:
        print(f"Approving USDC for {name}...")
        tx = usdc.functions.approve(Web3.to_checksum_address(addr), MAX_UINT).build_transaction({
            "from": USER_ADDR, "nonce": w3.eth.get_transaction_count(USER_ADDR, "pending"),
            "gas": 60000, "chainId": 137,
        })
        send_tx(tx)
        time.sleep(2)  # wait for nonce to propagate
    else:
        print(f"USDC approved for {name} (allowance: {allowance})")

# Step 2: CTF approvals
for name, addr in contracts:
    ok = ctf.functions.isApprovedForAll(USER_ADDR, Web3.to_checksum_address(addr)).call()
    if not ok:
        print(f"Approving CTF for {name}...")
        tx = ctf.functions.setApprovalForAll(Web3.to_checksum_address(addr), True).build_transaction({
            "from": USER_ADDR, "nonce": w3.eth.get_transaction_count(USER_ADDR, "pending"),
            "gas": 60000, "chainId": 137,
        })
        send_tx(tx)
        time.sleep(2)
    else:
        print(f"CTF approved for {name}")

print("\n--- All approvals done, placing order ---\n")

# Step 3: Place order via adapter (skip ensure_approvals since we did it manually)
from adapters.polymarket import PolymarketAdapter

adapter = PolymarketAdapter(
    private_key=PK,
    proxy_wallet=USER_ADDR,
    rpc_url=POLYGON_RPC,
)
adapter.authenticate()

best = adapter.get_best_offer(TOKEN_ID, "BUY")
price = best["price"]
amount = max(1.0, 1.1 / price)
cost = amount * price
print(f"Best ask: {price}, buying {amount:.2f} shares = ${cost:.4f}")

resp = adapter.place_order(
    token_id=TOKEN_ID,
    market_id=MARKET_ID,
    amount=amount,
    price=price,
    side="BUY",
)
print(f"\nOrder response: {resp}")
