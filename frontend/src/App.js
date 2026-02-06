import React, { useState, useEffect, useRef } from 'react';
import { BrowserProvider, formatUnits, parseUnits } from 'ethers';

// Source chains
const CHAINS = {
  eth: { id: 1, name: 'Ethereum', hex: '0x1' },
  base: { id: 8453, name: 'Base', hex: '0x2105' },
  bsc: { id: 56, name: 'BNB Chain', hex: '0x38' }
};

// Token addresses per chain
const TOKENS = {
  USDC: {
    eth: { address: '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48', decimals: 6 },
    base: { address: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', decimals: 6 },
    bsc: { address: '0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d', decimals: 18 }
  },
  USDT: {
    eth: { address: '0xdAC17F958D2ee523a2206206994597C13D831ec7', decimals: 6 },
    base: { address: '0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2', decimals: 6 },
    bsc: { address: '0x55d398326f99059fF775485246999027B3197955', decimals: 18 }
  }
};

// Destination: Polygon USDC.e
const DEST_CHAIN = { id: 137, name: 'Polygon' };
const DEST_TOKEN = { address: '0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174', symbol: 'USDC.e', decimals: 6 };

const ERC20_ABI = [
  'function balanceOf(address) view returns (uint256)',
  'function decimals() view returns (uint8)',
  'function approve(address spender, uint256 amount) returns (bool)'
];

export default function App() {
  const [wallet, setWallet] = useState(null);
  const [provider, setProvider] = useState(null);
  const [walletChainId, setWalletChainId] = useState(null);
  
  const [fromChain, setFromChain] = useState('base');
  const [fromToken, setFromToken] = useState('USDC');
  const [amount, setAmount] = useState('');
  const [balance, setBalance] = useState('0');
  
  const [routes, setRoutes] = useState([]);
  const [selectedRoute, setSelectedRoute] = useState(null);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('');
  
  const debounceRef = useRef(null);

  // Create fresh provider
  const createProvider = () => new BrowserProvider(window.ethereum);

  // Connect wallet
  const connect = async () => {
    if (!window.ethereum) return alert('Install MetaMask');
    const p = createProvider();
    const signer = await p.getSigner();
    const addr = await signer.getAddress();
    const network = await p.getNetwork();
    setProvider(p);
    setWallet(addr);
    setWalletChainId(Number(network.chainId));

    window.ethereum.on('chainChanged', (newChainId) => {
      setWalletChainId(Number(newChainId));
      setProvider(createProvider());
    });
  };

  // Switch chain
  const switchChain = async (chainKey) => {
    const chain = CHAINS[chainKey];
    try {
      await window.ethereum.request({
        method: 'wallet_switchEthereumChain',
        params: [{ chainId: chain.hex }]
      });
    } catch (e) {
      console.log('Switch chain error:', e);
    }
  };

  // Fetch balance
  useEffect(() => {
    if (!wallet || !provider) return;
    const chain = CHAINS[fromChain];
    if (walletChainId !== chain.id) return;
    
    const fetchBalance = async () => {
      try {
        const { Contract } = await import('ethers');
        const tokenInfo = TOKENS[fromToken][fromChain];
        const contract = new Contract(tokenInfo.address, ERC20_ABI, provider);
        const bal = await contract.balanceOf(wallet);
        setBalance(formatUnits(bal, tokenInfo.decimals));
      } catch (e) {
        console.log('Balance error:', e.message);
        setBalance('0');
      }
    };
    fetchBalance();
  }, [wallet, provider, walletChainId, fromChain, fromToken]);

  // Auto-search routes with debounce
  useEffect(() => {
    if (!wallet || !amount || parseFloat(amount) <= 0) {
      setRoutes([]);
      setSelectedRoute(null);
      return;
    }
    
    if (debounceRef.current) clearTimeout(debounceRef.current);
    
    debounceRef.current = setTimeout(() => {
      searchRoutes();
    }, 1000);
    
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [amount, fromChain, fromToken, wallet]);

  // Search routes
  const searchRoutes = async () => {
    if (!wallet || !amount || parseFloat(amount) <= 0) return;
    
    setLoading(true);
    setRoutes([]);
    setSelectedRoute(null);
    setStatus('Searching routes...');
    
    try {
      const tokenInfo = TOKENS[fromToken][fromChain];
      const amountWei = parseUnits(amount, tokenInfo.decimals).toString();
      
      // Get bridges
      const toolsResp = await fetch('https://li.quest/v1/tools');
      const toolsData = await toolsResp.json();
      const bridges = toolsData.bridges?.map(b => b.key) || [];
      
      setStatus(`Checking ${bridges.length} bridges...`);
      
      const baseUrl = 'https://li.quest/v1/quote';
      const params = new URLSearchParams({
        fromChain: String(CHAINS[fromChain].id),
        toChain: String(DEST_CHAIN.id),
        fromToken: tokenInfo.address,
        toToken: DEST_TOKEN.address,
        fromAddress: wallet,
        toAddress: wallet,
        fromAmount: amountWei,
        slippage: '0.03'
      });

      // Query bridges in parallel
      const results = await Promise.all(
        bridges.map(async (bridge) => {
          try {
            const bridgeParams = new URLSearchParams(params);
            bridgeParams.set('allowBridges', bridge);
            const resp = await fetch(`${baseUrl}?${bridgeParams}`);
            const data = await resp.json();
            if (data.transactionRequest) return data;
          } catch (e) {}
          return null;
        })
      );

      // Filter and sort
      const seen = new Set();
      const uniqueRoutes = results
        .filter(r => r && !seen.has(r.tool) && seen.add(r.tool))
        .sort((a, b) => {
          const amtA = BigInt(a.estimate?.toAmount || '0');
          const amtB = BigInt(b.estimate?.toAmount || '0');
          return amtB > amtA ? 1 : -1;
        });

      if (uniqueRoutes.length > 0) {
        setRoutes(uniqueRoutes);
        setSelectedRoute(uniqueRoutes[0]);
        setStatus(`Found ${uniqueRoutes.length} routes`);
      } else {
        setStatus('No routes found');
      }
    } catch (e) {
      setStatus('Error: ' + e.message);
    }
    setLoading(false);
  };

  // Approve
  const approveToken = async () => {
    if (!selectedRoute) return;
    setLoading(true);
    setStatus('Approving...');
    try {
      const { Contract } = await import('ethers');
      const signer = await provider.getSigner();
      const tokenInfo = TOKENS[fromToken][fromChain];
      const contract = new Contract(tokenInfo.address, ERC20_ABI, signer);
      const tx = await contract.approve(
        selectedRoute.estimate.approvalAddress,
        parseUnits(amount, tokenInfo.decimals)
      );
      await tx.wait();
      setStatus('Approved!');
    } catch (e) {
      setStatus('Approve failed: ' + e.message);
    }
    setLoading(false);
  };

  // Execute bridge
  const executeBridge = async () => {
    if (!selectedRoute) return;
    setLoading(true);
    setStatus('Sending...');
    try {
      const signer = await provider.getSigner();
      const tx = await signer.sendTransaction({
        to: selectedRoute.transactionRequest.to,
        data: selectedRoute.transactionRequest.data,
        value: selectedRoute.transactionRequest.value,
        gasLimit: selectedRoute.transactionRequest.gasLimit
      });
      setStatus(`Tx: ${tx.hash}`);
      await tx.wait();
      setStatus('Success! ' + tx.hash);
    } catch (e) {
      setStatus('Failed: ' + e.message);
    }
    setLoading(false);
  };

  const isCorrectChain = walletChainId === CHAINS[fromChain]?.id;

  return (
    <div style={styles.container}>
      <h1 style={styles.title}>→ Polygon USDC.e</h1>
      
      {!wallet ? (
        <button style={styles.btn} onClick={connect}>Connect Wallet</button>
      ) : (
        <div style={styles.card}>
          <p style={styles.wallet}>
            {wallet.slice(0, 6)}...{wallet.slice(-4)}
          </p>
          
          <div style={styles.row}>
            <label>From Chain:</label>
            <select 
              value={fromChain} 
              onChange={e => setFromChain(e.target.value)} 
              style={styles.select}
            >
              <option value="eth">Ethereum</option>
              <option value="base">Base</option>
              <option value="bsc">BNB Chain</option>
            </select>
          </div>

          <div style={styles.row}>
            <label>Token:</label>
            <select 
              value={fromToken} 
              onChange={e => setFromToken(e.target.value)} 
              style={styles.select}
            >
              <option value="USDC">USDC</option>
              <option value="USDT">USDT</option>
            </select>
          </div>

          <div style={styles.row}>
            <label>Amount:</label>
            <input
              type="number"
              value={amount}
              onChange={e => setAmount(e.target.value)}
              placeholder="0.00"
              style={styles.input}
            />
          </div>

          <p style={styles.balance}>
            Balance: {parseFloat(balance).toFixed(2)} {fromToken}
            {!isCorrectChain && <span style={{color: '#f59e0b'}}> (switch chain)</span>}
          </p>

          <div style={styles.destBox}>
            <span style={styles.destLabel}>Destination</span>
            <div style={styles.destInfo}>
              <b>Polygon</b> → <span style={{color: '#4ade80'}}>USDC.e</span>
            </div>
          </div>

          {!isCorrectChain && (
            <button style={styles.btn} onClick={() => switchChain(fromChain)}>
              Switch to {CHAINS[fromChain].name}
            </button>
          )}

          {loading && <p style={styles.status}>{status}</p>}

          {routes.length > 0 && (
            <>
              <div style={styles.routesHeader}>
                {routes.length} routes found
              </div>
              
              {routes.map((route, idx) => {
                const toAmt = route.estimate?.toAmount || '0';
                const isSelected = selectedRoute === route;
                const gas = route.estimate?.gasCosts?.[0]?.amountUSD || '0';
                const time = route.estimate?.executionDuration || 60;
                
                return (
                  <div 
                    key={idx} 
                    style={{
                      ...styles.routeCard,
                      border: isSelected ? '2px solid #667eea' : '1px solid #0f3460'
                    }}
                    onClick={() => setSelectedRoute(route)}
                  >
                    <div style={styles.routeCardHeader}>
                      <div style={styles.routeCardLeft}>
                        {idx === 0 && <span style={styles.bestBadge}>BEST</span>}
                        <span style={styles.toolBadge}>
                          {route.toolDetails?.name || route.tool}
                        </span>
                      </div>
                      {isSelected && <span style={{color: '#4ade80'}}>✓</span>}
                    </div>
                    
                    <div style={styles.routeCardBody}>
                      <div>
                        <span style={{color: '#4ade80', fontSize: 18}}>
                          <b>{parseFloat(formatUnits(toAmt, 6)).toFixed(2)}</b>
                        </span>
                        <span style={{color: '#888', fontSize: 12}}> USDC.e</span>
                      </div>
                      <div style={styles.routeMeta}>
                        <span>⛽ ${parseFloat(gas).toFixed(2)}</span>
                        <span>⏱️ ~{Math.ceil(time / 60)}m</span>
                      </div>
                    </div>
                  </div>
                );
              })}

              {selectedRoute && isCorrectChain && (
                <div style={styles.btnRow}>
                  <button style={styles.btnSecondary} onClick={approveToken} disabled={loading}>
                    1. Approve
                  </button>
                  <button style={styles.btn} onClick={executeBridge} disabled={loading}>
                    2. Bridge
                  </button>
                </div>
              )}
            </>
          )}

          {status && !loading && <p style={styles.status}>{status}</p>}
        </div>
      )}
    </div>
  );
}

const styles = {
  container: {
    maxWidth: 420,
    margin: '40px auto',
    padding: 24,
    fontFamily: 'system-ui, sans-serif',
    background: '#1a1a2e',
    minHeight: '100vh',
    color: '#fff'
  },
  title: {
    textAlign: 'center',
    fontSize: 24,
    marginBottom: 24
  },
  card: {
    background: '#16213e',
    borderRadius: 16,
    padding: 24
  },
  wallet: {
    textAlign: 'center',
    color: '#888',
    marginBottom: 20
  },
  row: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16
  },
  select: {
    padding: '10px 16px',
    borderRadius: 8,
    border: 'none',
    background: '#0f3460',
    color: '#fff',
    fontSize: 16
  },
  input: {
    padding: '10px 16px',
    borderRadius: 8,
    border: 'none',
    background: '#0f3460',
    color: '#fff',
    fontSize: 16,
    width: 140,
    textAlign: 'right'
  },
  balance: {
    textAlign: 'right',
    color: '#888',
    fontSize: 14,
    marginBottom: 16
  },
  destBox: {
    background: '#0f3460',
    borderRadius: 12,
    padding: 16,
    marginBottom: 16,
    textAlign: 'center'
  },
  destLabel: {
    fontSize: 11,
    color: '#888',
    textTransform: 'uppercase'
  },
  destInfo: {
    fontSize: 18,
    marginTop: 8
  },
  btn: {
    width: '100%',
    padding: '14px 20px',
    borderRadius: 12,
    border: 'none',
    background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
    color: '#fff',
    fontSize: 16,
    fontWeight: 600,
    cursor: 'pointer'
  },
  btnSecondary: {
    flex: 1,
    padding: '12px 16px',
    borderRadius: 12,
    border: '1px solid #667eea',
    background: 'transparent',
    color: '#667eea',
    fontSize: 14,
    fontWeight: 600,
    cursor: 'pointer',
    marginRight: 8
  },
  btnRow: {
    display: 'flex',
    marginTop: 16
  },
  routesHeader: {
    marginTop: 20,
    marginBottom: 12,
    fontSize: 14,
    color: '#888'
  },
  routeCard: {
    background: '#0f3460',
    borderRadius: 12,
    padding: 14,
    marginBottom: 10,
    cursor: 'pointer'
  },
  routeCardHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 10
  },
  routeCardLeft: {
    display: 'flex',
    alignItems: 'center',
    gap: 8
  },
  bestBadge: {
    background: '#4ade80',
    color: '#000',
    padding: '2px 6px',
    borderRadius: 4,
    fontSize: 10,
    fontWeight: 700
  },
  toolBadge: {
    background: '#1a1a2e',
    padding: '4px 8px',
    borderRadius: 6,
    fontSize: 12
  },
  routeCardBody: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-end'
  },
  routeMeta: {
    display: 'flex',
    gap: 12,
    fontSize: 12,
    color: '#888'
  },
  status: {
    marginTop: 16,
    padding: 12,
    background: '#0f3460',
    borderRadius: 8,
    fontSize: 13,
    wordBreak: 'break-all'
  }
};
