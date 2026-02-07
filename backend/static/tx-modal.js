// Transaction status modal
// Steps: approving → approved → relaying → sent → bridging → done / failed

const TX_STEPS = ['approving','approved','relaying','sent','bridging','done'];

const TX_LABELS = {
  approving: 'Approving USDC…',
  approved:  'USDC Approved',
  relaying:  'Relaying to Router…',
  sent:      'Transaction Sent',
  bridging:  'Bridging via LiFi…',
  done:      'Complete',
  failed:    'Failed',
};

// Which steps can have scan links
const SCAN_STEPS = ['approved','sent','done'];

let _modalEl = null;
let _stepLinks = {}; // step -> { url, label }

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

function openTxModal() {
  const m = _createModal();
  _stepLinks = {};
  const stepsHtml = TX_STEPS.map(s =>
    `<div class="txm-step" data-step="${s}">
      <div class="txm-dot"></div>
      <span class="txm-label">${TX_LABELS[s]}</span>
      <a class="txm-scan" id="txmScan_${s}" href="#" target="_blank">${_scanIcon}</a>
    </div>`
  ).join('');
  document.getElementById('txmSteps').innerHTML = stepsHtml;
  document.getElementById('txmMsg').textContent = '';
  m.classList.add('open');
}

function setTxStep(step) {
  const steps = document.querySelectorAll('.txm-step');
  const idx = TX_STEPS.indexOf(step);
  steps.forEach((el, i) => {
    el.classList.remove('active','done','failed');
    if (i < idx) el.classList.add('done');
    else if (i === idx) el.classList.add('active');
  });
}

function setTxFailed(atStep, msg) {
  const steps = document.querySelectorAll('.txm-step');
  const idx = TX_STEPS.indexOf(atStep);
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

// Set scan link for a specific step
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
