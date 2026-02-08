// Transaction status modal
// Buy cross-chain: approving → approved → relaying → sent → bridging → trading → settling → done
// Buy same-chain:  approving → approved → relaying → trading → settling → done
// Sell cross-chain: approving_sell → pulling → selling → settling_sell → bridging_back → done
// Sell same-chain:  approving_sell → pulling → selling → settling_sell → done
// Batch sell:       batch_approving → batch_selling → batch_settling → batch_bridging → done

const BUY_STEPS_BRIDGE = ['approving','approved','relaying','sent','bridging','trading','settling','done'];
const BUY_STEPS_DIRECT = ['approving','approved','relaying','trading','settling','done'];
const SELL_STEPS_BRIDGE = ['approving_sell','pulling','selling','settling_sell','bridging_back','done'];
const SELL_STEPS_DIRECT = ['approving_sell','pulling','selling','settling_sell','done'];
const BATCH_SELL_STEPS = ['batch_approving','batch_selling','batch_settling','batch_bridging','done'];

const LABELS_BRIDGE = {
  approving: 'Approving USDC…',
  approved:  'USDC Approved',
  relaying:  'Relaying to Router…',
  sent:      'Transaction Sent',
  bridging:  'Bridging via LiFi…',
  trading:   'Placing Orders…',
  settling:  'Delivering Shares…',
  done:      'Complete',
  failed:    'Failed',
  approving_sell: 'Approving Relayer…',
  pulling:        'Pulling Shares…',
  selling:        'Selling on Market…',
  settling_sell:  'Waiting for USDC…',
  bridging_back:  'Bridging to Base…',
  batch_approving: 'Approving on chains…',
  batch_selling:   'Selling on platforms…',
  batch_settling:  'Waiting for proceeds…',
  batch_bridging:  'Bridging to target…',
};

const LABELS_DIRECT = {
  approving: 'Approving USDC…',
  approved:  'USDC Approved',
  relaying:  'Transferring USDC…',
  trading:   'Placing Orders…',
  settling:  'Delivering Shares…',
  done:      'Complete',
  failed:    'Failed',
  approving_sell: 'Approving Relayer…',
  pulling:        'Pulling Shares…',
  selling:        'Selling on Market…',
  settling_sell:  'Transferring USDC…',
};

let ALL_LABELS = LABELS_BRIDGE;

let _modalEl = null;
let _activeSteps = BUY_STEPS_BRIDGE;

function _createModal() {
  if (_modalEl) return _modalEl;
  const div = document.createElement('div');
  div.id = 'txModal';
  div.innerHTML = `
    <div class="txm-overlay"></div>
    <div class="txm-box">
      <button class="txm-close" onclick="closeTxModal()">&times;</button>
      <div class="txm-title">Transaction Status</div>
      <div class="txm-steps" id="txmSteps"></div>
      <div class="txm-msg" id="txmMsg"></div>
    </div>
  `;
  document.body.appendChild(div);

  const style = document.createElement('style');
  style.textContent = `
    #txModal { display:none; position:fixed; inset:0; z-index:9999; }
    #txModal.open { display:flex; align-items:center; justify-content:center; }
    .txm-overlay { position:absolute; inset:0; background:rgba(0,0,0,0.4); }
    .txm-box {
      position:relative; background:#fff; border-radius:16px; padding:32px 28px 24px;
      width:400px; max-width:90vw; box-shadow:0 20px 60px rgba(0,0,0,0.15);
    }
    .txm-close {
      position:absolute; top:12px; right:16px; background:none; border:none;
      font-size:22px; cursor:pointer; color:#999; line-height:1;
    }
    .txm-close:hover { color:#333; }
    .txm-title { font-size:18px; font-weight:700; margin-bottom:20px; color:#111; }
    .txm-steps { display:flex; flex-direction:column; gap:12px; margin-bottom:16px; }
    .txm-step {
      display:flex; align-items:center; gap:10px; font-size:14px; color:#bbb;
      transition: color 0.3s;
    }
    .txm-step.active { color:#2563eb; font-weight:600; }
    .txm-step.done { color:#16a34a; }
    .txm-step.failed { color:#dc2626; }
    .txm-dot {
      width:24px; height:24px; border-radius:50%; border:2px solid #ddd;
      display:flex; align-items:center; justify-content:center; font-size:12px;
      flex-shrink:0; transition: all 0.3s;
    }
    .txm-step.done .txm-dot { border-color:#16a34a; background:#16a34a; color:#fff; }
    .txm-step.active .txm-dot { border-color:#2563eb; background:#2563eb; color:#fff; }
    .txm-step.failed .txm-dot { border-color:#dc2626; background:#dc2626; color:#fff; }
    @keyframes txSpin { to { transform:rotate(360deg); } }
    .txm-step.active .txm-dot::after { content:''; width:10px; height:10px; border:2px solid #fff; border-top-color:transparent; border-radius:50%; animation:txSpin 0.8s linear infinite; }
    .txm-step.done .txm-dot::after { content:'\\2713'; animation:none; }
    .txm-step.failed .txm-dot::after { content:'\\2717'; animation:none; }
    .txm-label { flex:1; }
    .txm-scan {
      display:inline-flex; align-items:center; gap:3px;
      font-size:12px; color:#2563eb; text-decoration:none; opacity:0;
      pointer-events:none; transition: opacity 0.3s;
    }
    .txm-scan.visible { opacity:1; pointer-events:auto; }
    .txm-scan:hover { text-decoration:underline; }
    .txm-scan svg { width:14px; height:14px; }
    .txm-msg { font-size:13px; color:#666; min-height:18px; }
  `;
  document.head.appendChild(style);

  _modalEl = div;
  return div;
}

const _scanIcon = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>`;

function openTxModal(mode, sameChain, chainName) {
  const m = _createModal();
  const cname = chainName || 'Base';
  if (mode === 'batch_sell') {
    _activeSteps = BATCH_SELL_STEPS;
    ALL_LABELS = {...LABELS_BRIDGE};
    ALL_LABELS.batch_bridging = `Bridging to ${cname}…`;
  } else if (sameChain) {
    _activeSteps = mode === 'sell' ? SELL_STEPS_DIRECT : BUY_STEPS_DIRECT;
    ALL_LABELS = {...LABELS_DIRECT};
  } else {
    _activeSteps = mode === 'sell' ? SELL_STEPS_BRIDGE : BUY_STEPS_BRIDGE;
    ALL_LABELS = {...LABELS_BRIDGE};
    if (mode === 'buy') ALL_LABELS.bridging = `Bridging to ${cname}…`;
    if (mode === 'sell') ALL_LABELS.bridging_back = `Bridging to ${cname}…`;
  }
  const stepsHtml = _activeSteps.map(s =>
    `<div class="txm-step" data-step="${s}">
      <div class="txm-dot"></div>
      <span class="txm-label">${ALL_LABELS[s]}</span>
      <a class="txm-scan" id="txmScan_${s}" href="#" target="_blank">${_scanIcon}</a>
    </div>`
  ).join('');
  document.getElementById('txmSteps').innerHTML = stepsHtml;
  document.getElementById('txmMsg').textContent = '';
  m.classList.add('open');
}

function setTxStep(step) {
  const steps = document.querySelectorAll('.txm-step');
  const idx = _activeSteps.indexOf(step);
  steps.forEach((el, i) => {
    el.classList.remove('active','done','failed');
    if (i < idx) el.classList.add('done');
    else if (i === idx) el.classList.add('active');
  });
}

function setTxFailed(atStep, msg) {
  const steps = document.querySelectorAll('.txm-step');
  const idx = _activeSteps.indexOf(atStep);
  steps.forEach((el, i) => {
    el.classList.remove('active','done','failed');
    if (i < idx) el.classList.add('done');
    else if (i === idx) el.classList.add('failed');
  });
  document.getElementById('txmMsg').textContent = msg || 'Transaction failed';
}

function setTxDone(msg) {
  const steps = document.querySelectorAll('.txm-step');
  steps.forEach(el => {
    el.classList.remove('active','failed');
    el.classList.add('done');
  });
  document.getElementById('txmMsg').textContent = msg || '';
}

function setTxScanLink(step, url) {
  const el = document.getElementById('txmScan_' + step);
  if (!el) return;
  el.href = url;
  el.classList.add('visible');
}

function setTxMsg(msg) {
  document.getElementById('txmMsg').textContent = msg;
}

function closeTxModal() {
  if (_modalEl) _modalEl.classList.remove('open');
}

// --- Chain selection modal ---
const CHAINS = [
  { id: 8453, name: 'Base',    token: 'USDC',   icon: '/public/base.png',    stable: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', decimals: 6,  rpc: 'https://mainnet.base.org' },
  { id: 137,  name: 'Polygon', token: 'USDC.e',  icon: '/public/polygon.png', stable: '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174', decimals: 6,  rpc: 'https://polygon-bor-rpc.publicnode.com' },
  { id: 56,   name: 'BSC',     token: 'USDT',    icon: '/public/bsc.png',     stable: '0x55d398326f99059fF775485246999027B3197955', decimals: 18, rpc: 'https://bsc-rpc.publicnode.com' },
];

let _chainModalEl = null;
let _chainStyleInjected = false;

function _injectChainStyles() {
  if (_chainStyleInjected) return;
  _chainStyleInjected = true;
  const style = document.createElement('style');
  style.textContent = `
    #chainModal { display:none; position:fixed; inset:0; z-index:9998; }
    #chainModal.open { display:flex; align-items:center; justify-content:center; }
    .csm-overlay { position:absolute; inset:0; background:rgba(0,0,0,0.45); backdrop-filter:blur(4px); }
    .csm-box {
      position:relative; background:#1a1b23; border-radius:20px; padding:24px;
      width:380px; max-width:90vw; box-shadow:0 24px 80px rgba(0,0,0,0.5);
      border:1px solid rgba(255,255,255,0.08);
    }
    .csm-header { display:flex; align-items:center; justify-content:space-between; margin-bottom:20px; }
    .csm-title { font-size:16px; font-weight:600; color:#fff; }
    .csm-close {
      width:28px; height:28px; border-radius:8px; border:none; background:rgba(255,255,255,0.08);
      color:#888; font-size:16px; cursor:pointer; display:flex; align-items:center; justify-content:center;
      transition: background 0.15s, color 0.15s;
    }
    .csm-close:hover { background:rgba(255,255,255,0.15); color:#fff; }
    .csm-list { display:flex; flex-direction:column; gap:4px; }
    .csm-item {
      display:flex; align-items:center; gap:12px; padding:12px 14px;
      border-radius:12px; cursor:pointer; background:transparent;
      border:1px solid transparent; transition: background 0.15s, border-color 0.15s;
    }
    .csm-item:hover { background:rgba(255,255,255,0.06); border-color:rgba(255,255,255,0.1); }
    .csm-icon {
      width:40px; height:40px; border-radius:50%; overflow:hidden; flex-shrink:0;
      background:rgba(255,255,255,0.05);
    }
    .csm-icon img { width:100%; height:100%; object-fit:cover; display:block; }
    .csm-info { flex:1; min-width:0; }
    .csm-name { font-size:15px; font-weight:600; color:#fff; line-height:1.3; }
    .csm-token { font-size:13px; color:rgba(255,255,255,0.4); line-height:1.3; }
    .csm-bal { text-align:right; flex-shrink:0; min-width:70px; }
    .csm-bal-value { font-size:15px; font-weight:600; color:#fff; line-height:1.3; }
    .csm-bal-label { font-size:11px; color:rgba(255,255,255,0.35); line-height:1.3; }
    .csm-bal-loading { font-size:13px; color:rgba(255,255,255,0.25); }
    @keyframes csmPulse { 0%,100% { opacity:0.3; } 50% { opacity:0.8; } }
    .csm-bal-loading { animation: csmPulse 1.2s ease-in-out infinite; }
  `;
  document.head.appendChild(style);
}

function _createChainModal() {
  if (_chainModalEl) return _chainModalEl;
  _injectChainStyles();
  const div = document.createElement('div');
  div.id = 'chainModal';
  div.innerHTML = `
    <div class="csm-overlay" onclick="closeChainModal()"></div>
    <div class="csm-box">
      <div class="csm-header">
        <div class="csm-title" id="chainModalTitle">Select Chain</div>
        <button class="csm-close" onclick="closeChainModal()">&times;</button>
      </div>
      <div class="csm-list" id="chainModalList"></div>
    </div>
  `;
  document.body.appendChild(div);
  _chainModalEl = div;
  return div;
}

async function _fetchChainBalance(chain, wallet) {
  if (!wallet) return null;
  const data = '0x70a08231' + wallet.slice(2).toLowerCase().padStart(64, '0');
  try {
    const resp = await fetch(chain.rpc, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'eth_call', params: [{ to: chain.stable, data }, 'latest'] }),
    });
    const res = await resp.json();
    const hex = res.result;
    if (!hex || hex === '0x' || hex.length <= 2) return 0;
    return Number(BigInt(hex)) / (10 ** chain.decimals);
  } catch { return null; }
}

function openChainSelectModal(mode, callback) {
  const m = _createChainModal();
  document.getElementById('chainModalTitle').textContent =
    mode === 'buy' ? 'Pay with' : 'Receive on';

  document.getElementById('chainModalList').innerHTML = CHAINS.map(c => `
    <div class="csm-item" onclick="selectChain(${c.id})">
      <div class="csm-icon"><img src="${c.icon}" alt="${c.name}"></div>
      <div class="csm-info">
        <div class="csm-name">${c.name}</div>
        <div class="csm-token">${c.token}</div>
      </div>
      <div class="csm-bal" id="csmBal_${c.id}">
        <div class="csm-bal-loading">loading…</div>
      </div>
    </div>
  `).join('');

  window._chainSelectCallback = callback;
  m.classList.add('open');

  // Fetch balances in parallel via public RPCs
  const wallet = window.walletAddress || null;
  CHAINS.forEach(c => {
    _fetchChainBalance(c, wallet).then(bal => {
      const el = document.getElementById('csmBal_' + c.id);
      if (!el) return;
      if (bal === null) {
        el.innerHTML = '<div class="csm-bal-loading">—</div>';
      } else {
        el.innerHTML = `<div class="csm-bal-value">$${bal.toFixed(2)}</div><div class="csm-bal-label">${c.token}</div>`;
      }
    });
  });
}

function selectChain(chainId) {
  closeChainModal();
  if (window._chainSelectCallback) {
    window._chainSelectCallback(chainId);
    window._chainSelectCallback = null;
  }
}

function closeChainModal() {
  if (_chainModalEl) _chainModalEl.classList.remove('open');
}
