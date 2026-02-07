// Wallet dropdown with stablecoin balances across chains
// Uses public RPCs, no API keys needed

const CHAINS = [
  { id: 'eth',  name: 'Ethereum', rpc: 'https://eth.drpc.org',         chainId: 1,     scan: 'etherscan.io' },
  { id: 'poly', name: 'Polygon',  rpc: 'https://polygon.drpc.org',     chainId: 137,   scan: 'polygonscan.com' },
  { id: 'bnb',  name: 'BNB',      rpc: 'https://bsc.drpc.org',         chainId: 56,    scan: 'bscscan.com' },
  { id: 'base', name: 'Base',     rpc: 'https://base.drpc.org',        chainId: 8453,  scan: 'basescan.org' },
];

const STABLES = {
  eth:  [
    { symbol: 'USDC', addr: '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48', decimals: 6 },
    { symbol: 'USDT', addr: '0xdAC17F958D2ee523a2206206994597C13D831ec7', decimals: 6 },
    { symbol: 'DAI',  addr: '0x6B175474E89094C44Da98b954EedeAC495271d0F', decimals: 18 },
  ],
  poly: [
    { symbol: 'USDC',   addr: '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359', decimals: 6 },
    { symbol: 'USDC.e', addr: '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174', decimals: 6 },
    { symbol: 'USDT',   addr: '0xc2132D05D31c914a87C6611C10748AEb04B58e8F', decimals: 6 },
  ],
  bnb:  [
    { symbol: 'USDC', addr: '0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d', decimals: 18 },
    { symbol: 'USDT', addr: '0x55d398326f99059fF775485246999027B3197955', decimals: 18 },
  ],
  base: [
    { symbol: 'USDC', addr: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', decimals: 6 },
  ],
};

// balanceOf(address) selector
const BAL_SEL = '0x70a08231';

async function fetchBalance(rpc, tokenAddr, wallet, decimals) {
  const data = BAL_SEL + wallet.slice(2).toLowerCase().padStart(64, '0');
  try {
    const resp = await fetch(rpc, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'eth_call', params: [{ to: tokenAddr, data }, 'latest'] }),
    });
    const json = await resp.json();
    if (!json.result || json.result === '0x') return 0;
    return Number(BigInt(json.result)) / (10 ** decimals);
  } catch { return 0; }
}

let _dropdownEl = null;
let _dropdownOpen = false;

function _createDropdown() {
  if (_dropdownEl) return _dropdownEl;

  const div = document.createElement('div');
  div.id = 'walletDropdown';
  div.innerHTML = `
    <div class="wd-header" id="wdHeader"></div>
    <div class="wd-body" id="wdBody"><div class="wd-loading">Loading balances…</div></div>
    <button class="wd-disconnect" onclick="disconnectWallet()">Disconnect</button>
  `;

  const style = document.createElement('style');
  style.textContent = `
    .wallet-wrap { position: relative; }
    #walletDropdown {
      display:none; position:absolute; top:calc(100% + 8px); right:0;
      background:#fff; border-radius:12px; box-shadow:0 8px 32px rgba(0,0,0,0.12);
      border:1px solid #e5e5e5; width:280px; z-index:100; overflow:hidden;
    }
    #walletDropdown.open { display:block; }
    .wd-header {
      padding:14px 16px; border-bottom:1px solid #f0f0f0;
      font-size:13px; font-weight:600; color:#333; font-family:monospace;
    }
    .wd-body { padding:8px 0; max-height:300px; overflow-y:auto; }
    .wd-loading { padding:16px; text-align:center; font-size:13px; color:#999; }
    .wd-chain {
      padding:6px 16px 2px; font-size:11px; font-weight:700; color:#999;
      text-transform:uppercase; letter-spacing:0.5px;
    }
    .wd-row {
      display:flex; justify-content:space-between; align-items:center;
      padding:6px 16px; font-size:14px;
    }
    .wd-symbol { color:#333; font-weight:500; }
    .wd-bal { color:#111; font-weight:600; font-variant-numeric:tabular-nums; }
    .wd-disconnect {
      width:100%; padding:12px; border:none; border-top:1px solid #f0f0f0;
      background:none; color:#dc2626; font-size:13px; font-weight:600;
      cursor:pointer; transition:background 0.15s;
    }
    .wd-disconnect:hover { background:#fef2f2; }
  `;
  document.head.appendChild(style);

  _dropdownEl = div;
  return div;
}

function toggleWalletDropdown() {
  if (!walletAddress) { connectWallet(); return; }
  const dd = _createDropdown();
  // Ensure it's inside the wrapper
  const wrap = document.getElementById('walletWrap');
  if (wrap && !wrap.contains(dd)) wrap.appendChild(dd);

  _dropdownOpen = !_dropdownOpen;
  dd.classList.toggle('open', _dropdownOpen);

  if (_dropdownOpen) {
    document.getElementById('wdHeader').textContent = walletAddress;
    loadBalances();
  }
}

function disconnectWallet() {
  walletAddress = null;
  localStorage.removeItem('walletAddress');
  updateWalletBtn();
  if (_dropdownEl) { _dropdownEl.classList.remove('open'); _dropdownOpen = false; }
}

async function loadBalances() {
  const body = document.getElementById('wdBody');
  body.innerHTML = '<div class="wd-loading">Loading balances…</div>';

  const tasks = [];
  for (const chain of CHAINS) {
    for (const token of STABLES[chain.id]) {
      tasks.push({ chain, token, promise: fetchBalance(chain.rpc, token.addr, walletAddress, token.decimals) });
    }
  }

  const results = await Promise.all(tasks.map(t => t.promise));

  // Group by chain, filter zero balances
  const grouped = {};
  tasks.forEach((t, i) => {
    const bal = results[i];
    if (bal < 0.01) return;
    if (!grouped[t.chain.id]) grouped[t.chain.id] = { chain: t.chain, tokens: [] };
    grouped[t.chain.id].tokens.push({ symbol: t.token.symbol, balance: bal });
  });

  if (Object.keys(grouped).length === 0) {
    body.innerHTML = '<div class="wd-loading">No stablecoin balances</div>';
    return;
  }

  let html = '';
  for (const g of Object.values(grouped)) {
    html += `<div class="wd-chain">${g.chain.name}</div>`;
    for (const t of g.tokens) {
      html += `<div class="wd-row"><span class="wd-symbol">${t.symbol}</span><span class="wd-bal">${t.balance < 1000 ? t.balance.toFixed(2) : t.balance.toLocaleString(undefined, {maximumFractionDigits:2})}</span></div>`;
    }
  }
  body.innerHTML = html;
}

// Close dropdown on outside click
document.addEventListener('click', (e) => {
  if (_dropdownOpen && _dropdownEl && !_dropdownEl.contains(e.target) && e.target.id !== 'walletBtn') {
    _dropdownEl.classList.remove('open');
    _dropdownOpen = false;
  }
});
