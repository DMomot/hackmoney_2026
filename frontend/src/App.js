import React, { useState, useEffect } from 'react';
import { BrowserProvider, formatUnits, parseUnits } from 'ethers';

const CHAINS = {
  base: { id: 8453, name: 'Base', rpc: 'https://mainnet.base.org' },
  polygon: { id: 137, name: 'Polygon' },
  bsc: { id: 56, name: 'BNB Chain' }
};

const TOKENS = {
  USDC: {
    base: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
    polygon: '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359',
    bsc: '0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d'
  },
  USDT: {
    base: '0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2',
    polygon: '0xc2132D05D31c914a87C6611C10748AEb04B58e8F',
    bsc: '0x55d398326f99059fF775485246999027B3197955'
  }
};

const ERC20_ABI = [
  'function balanceOf(address) view returns (uint256)',
  'function decimals() view returns (uint8)',
  'function approve(address spender, uint256 amount) returns (bool)',
  'function allowance(address owner, address spender) view returns (uint256)'
];

export default function App() {
  const [wallet, setWallet] = useState(null);
  const [provider, setProvider] = useState(null);
  const [chainId, setChainId] = useState(null);
  const [token, setToken] = useState('USDC');
  const [toChain, setToChain] = useState('polygon');
  const [amount, setAmount] = useState('');
  const [balance, setBalance] = useState('0');
  const [routes, setRoutes] = useState([]);
  const [selectedRoute, setSelectedRoute] = useState(null);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('');
  const [customRecipient, setCustomRecipient] = useState('');

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
    setChainId(Number(network.chainId));

    // Listen for chain changes - recreate provider
    window.ethereum.on('chainChanged', (newChainId) => {
      setChainId(Number(newChainId));
      setProvider(createProvider());
    });
  };

  // Switch to Base
  const switchToBase = async () => {
    try {
      await window.ethereum.request({
        method: 'wallet_switchEthereumChain',
        params: [{ chainId: '0x2105' }]
      });
    } catch (e) {
      if (e.code === 4902) {
        await window.ethereum.request({
          method: 'wallet_addEthereumChain',
          params: [{
            chainId: '0x2105',
            chainName: 'Base',
            rpcUrls: ['https://mainnet.base.org'],
            nativeCurrency: { name: 'ETH', symbol: 'ETH', decimals: 18 },
            blockExplorerUrls: ['https://basescan.org']
          }]
        });
      }
    }
    // chainChanged event will handle the rest
  };

  // Fetch balance
  useEffect(() => {
    if (!wallet || !provider || chainId !== 8453) return;
    const fetchBalance = async () => {
      try {
        const { Contract } = await import('ethers');
        const tokenAddr = TOKENS[token].base;
        const contract = new Contract(tokenAddr, ERC20_ABI, provider);
        const bal = await contract.balanceOf(wallet);
        const dec = await contract.decimals();
        setBalance(formatUnits(bal, dec));
      } catch (e) {
        console.log('Balance fetch error:', e.message);
      }
    };
    fetchBalance();
  }, [wallet, provider, chainId, token]);

  // Get routes from LI.FI (multiple quotes with different bridges)
  const getRoutes = async () => {
    if (!amount || parseFloat(amount) <= 0) return;
    setLoading(true);
    setRoutes([]);
    setSelectedRoute(null);
    setStatus('Fetching available bridges...');
    
    try {
      const decimals = 6;
      const amountWei = parseUnits(amount, decimals).toString();
      
      // First get list of all bridges
      const toolsResp = await fetch('https://li.quest/v1/tools');
      const toolsData = await toolsResp.json();
      const allBridges = toolsData.bridges?.map(b => b.key) || [];
      
      setStatus(`Fetching quotes from ${allBridges.length} bridges...`);
      
      const baseUrl = 'https://li.quest/v1/quote';
      const params = new URLSearchParams({
        fromChain: '8453',
        toChain: String(CHAINS[toChain].id),
        fromToken: TOKENS[token].base,
        toToken: TOKENS[token][toChain],
        fromAddress: wallet,
        toAddress: customRecipient || wallet,
        fromAmount: amountWei,
        slippage: '0.03'
      });

      // Query all bridges in parallel
      const bridgePromises = allBridges.map(async (bridge) => {
        try {
          const bridgeParams = new URLSearchParams(params);
          bridgeParams.set('allowBridges', bridge);
          const resp = await fetch(`${baseUrl}?${bridgeParams}`);
          const data = await resp.json();
          if (data.transactionRequest) {
            return data;
          }
        } catch (e) {
          // Ignore failed bridge requests
        }
        return null;
      });

      const bridgeResults = await Promise.all(bridgePromises);
      const validRoutes = bridgeResults.filter(r => r !== null);

      // Remove duplicates by tool and sort by output amount
      const seen = new Set();
      const uniqueRoutes = validRoutes
        .filter(r => {
          if (seen.has(r.tool)) return false;
          seen.add(r.tool);
          return true;
        })
        .sort((a, b) => {
          const amountA = BigInt(a.estimate?.toAmount || '0');
          const amountB = BigInt(b.estimate?.toAmount || '0');
          return amountB > amountA ? 1 : -1;
        });

      console.log('Routes found:', uniqueRoutes.length, uniqueRoutes);
      
      if (uniqueRoutes.length > 0) {
        setRoutes(uniqueRoutes);
        setSelectedRoute(uniqueRoutes[0]);
        setStatus(`Found ${uniqueRoutes.length} routes`);
      } else {
        setStatus('No routes available for this pair');
      }
    } catch (e) {
      setStatus('Error: ' + e.message);
    }
    setLoading(false);
  };

  // Approve token
  const approveToken = async () => {
    if (!selectedRoute) return;
    setLoading(true);
    setStatus('Approving...');
    try {
      const { Contract } = await import('ethers');
      const signer = await provider.getSigner();
      const tokenAddr = TOKENS[token].base;
      const contract = new Contract(tokenAddr, ERC20_ABI, signer);
      const spender = selectedRoute.estimate.approvalAddress;
      const tx = await contract.approve(spender, parseUnits(amount, 6));
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
    setStatus('Sending transaction...');
    try {
      const signer = await provider.getSigner();
      
      if (!selectedRoute.transactionRequest) {
        throw new Error('No transaction data');
      }
      
      const tx = await signer.sendTransaction({
        to: selectedRoute.transactionRequest.to,
        data: selectedRoute.transactionRequest.data,
        value: selectedRoute.transactionRequest.value,
        gasLimit: selectedRoute.transactionRequest.gasLimit
      });
      
      setStatus(`Tx sent: ${tx.hash}`);
      await tx.wait();
      setStatus('Success! Bridge completed. Tx: ' + tx.hash);
    } catch (e) {
      setStatus('Failed: ' + e.message);
    }
    setLoading(false);
  };

  const isBase = chainId === 8453;

  return (
    <div style={styles.container}>
      <h1 style={styles.title}>Base → Polygon/BNB Bridge</h1>
      
      {!wallet ? (
        <button style={styles.btn} onClick={connect}>Connect Wallet</button>
      ) : (
        <div style={styles.card}>
          <p style={styles.wallet}>
            {wallet.slice(0, 6)}...{wallet.slice(-4)}
            <br/>
            <span style={{fontSize: 12}}>
              Chain: {chainId} {isBase ? '✅ Base' : '⚠️ Not Base'}
            </span>
          </p>
          
          {!isBase ? (
            <button style={styles.btn} onClick={switchToBase}>Switch to Base</button>
          ) : (
            <>
              <div style={styles.row}>
                <label>Token:</label>
                <select value={token} onChange={e => setToken(e.target.value)} style={styles.select}>
                  <option value="USDC">USDC</option>
                  <option value="USDT">USDT</option>
                </select>
              </div>

              <div style={styles.row}>
                <label>To Chain:</label>
                <select value={toChain} onChange={e => setToChain(e.target.value)} style={styles.select}>
                  <option value="polygon">Polygon</option>
                  <option value="bsc">BNB Chain</option>
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

              <p style={styles.balance}>Balance: {parseFloat(balance).toFixed(2)} {token}</p>

              <div style={styles.row}>
                <label>To address:</label>
                <input
                  type="text"
                  value={customRecipient}
                  onChange={e => setCustomRecipient(e.target.value)}
                  placeholder="Same as sender"
                  style={{...styles.input, width: 180, fontSize: 12}}
                />
              </div>

              <button style={styles.btn} onClick={getRoutes} disabled={loading}>
                {loading ? 'Loading...' : 'Get Routes'}
              </button>

              {routes.length > 0 && (
                <>
                  <div style={styles.routesHeader}>
                    <span>{routes.length} routes found</span>
                  </div>
                  
                  {routes.map((route, idx) => {
                    const toAmount = route.estimate?.toAmount || '0';
                    const toAmountMin = route.estimate?.toAmountMin || '0';
                    const isSelected = selectedRoute === route;
                    const gasCost = route.estimate?.gasCosts?.[0]?.amountUSD || '0';
                    const execTime = route.estimate?.executionDuration || 60;
                    
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
                            <div style={styles.routeTools}>
                              <span style={styles.toolBadge}>
                                {route.toolDetails?.name || route.tool}
                              </span>
                              {route.includedSteps?.length > 1 && route.includedSteps.slice(1).map((s, i) => (
                                <span key={i} style={styles.toolBadge}>
                                  → {s.toolDetails?.name || s.tool}
                                </span>
                              ))}
                            </div>
                          </div>
                          <div style={styles.routeCardRight}>
                            {isSelected && <span style={{color: '#4ade80'}}>✓</span>}
                          </div>
                        </div>
                        
                        <div style={styles.routeCardBody}>
                          <div style={styles.routeAmount}>
                            <span style={{color: '#4ade80', fontSize: 18}}>
                              <b>{parseFloat(formatUnits(toAmount || '0', 6)).toFixed(2)}</b>
                            </span>
                            <span style={{color: '#888', fontSize: 12}}> {token}</span>
                          </div>
                          <div style={styles.routeMeta}>
                            <span>⛽ ${parseFloat(gasCost).toFixed(2)}</span>
                            <span>⏱️ ~{Math.ceil(execTime / 60)}min</span>
                          </div>
                        </div>
                      </div>
                    );
                  })}

                  {selectedRoute && (
                    <div style={styles.selectedDetails}>
                      <div style={styles.addressBox}>
                        <div style={styles.rateRow}>
                          <span>📤 From</span>
                          <span>{wallet?.slice(0,6)}...{wallet?.slice(-4)} (Base)</span>
                        </div>
                        <div style={styles.rateRow}>
                          <span>📥 To</span>
                          <span>{(customRecipient || wallet)?.slice(0,6)}...{(customRecipient || wallet)?.slice(-4)} ({CHAINS[toChain].name})</span>
                        </div>
                        <div style={styles.rateRow}>
                          <span>🪙 Token</span>
                          <span>{selectedRoute.action?.toToken?.symbol || token}</span>
                        </div>
                        <div style={styles.rateRow}>
                          <span>💰 Receive</span>
                          <span style={{color: '#4ade80'}}>{parseFloat(formatUnits(selectedRoute.estimate?.toAmount || '0', 6)).toFixed(2)} {token}</span>
                        </div>
                      </div>
                      
                      <div style={styles.btnRow}>
                        <button style={styles.btnSecondary} onClick={approveToken} disabled={loading}>
                          1. Approve
                        </button>
                        <button style={styles.btn} onClick={executeBridge} disabled={loading}>
                          2. Bridge
                        </button>
                      </div>
                    </div>
                  )}
                </>
              )}

              {status && <p style={styles.status}>{status}</p>}
            </>
          )}
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
    marginBottom: 16
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
  quoteBox: {
    marginTop: 20,
    padding: 16,
    background: '#0f3460',
    borderRadius: 12
  },
  routeHeader: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    marginBottom: 16,
    paddingBottom: 12,
    borderBottom: '1px solid #1a1a2e'
  },
  toolLogo: {
    width: 32,
    height: 32,
    borderRadius: 8
  },
  routeType: {
    marginLeft: 8,
    padding: '2px 8px',
    background: '#667eea',
    borderRadius: 4,
    fontSize: 11
  },
  rateBox: {
    background: '#1a1a2e',
    borderRadius: 8,
    padding: 12,
    marginBottom: 12
  },
  rateRow: {
    display: 'flex',
    justifyContent: 'space-between',
    padding: '6px 0',
    fontSize: 14
  },
  feesBox: {
    background: '#1a1a2e',
    borderRadius: 8,
    padding: 12,
    marginBottom: 12
  },
  stepsBox: {
    color: '#888',
    textAlign: 'center',
    marginBottom: 12
  },
  addressBox: {
    background: '#1a1a2e',
    borderRadius: 8,
    padding: 12,
    marginBottom: 12
  },
  addressText: {
    fontFamily: 'monospace',
    fontSize: 13
  },
  link: {
    color: '#667eea',
    textDecoration: 'none',
    fontFamily: 'monospace',
    fontSize: 13
  },
  tokenInfoBox: {
    background: '#1a1a2e',
    borderRadius: 8,
    padding: 12,
    marginBottom: 12,
    border: '1px solid #4ade80'
  },
  tokenHeader: {
    fontSize: 11,
    color: '#888',
    marginBottom: 8,
    textTransform: 'uppercase'
  },
  tokenRow: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    marginBottom: 12
  },
  tokenLogo: {
    width: 36,
    height: 36,
    borderRadius: 18
  },
  tokenName: {
    display: 'flex',
    alignItems: 'center',
    gap: 8
  },
  tokenChain: {
    fontSize: 11,
    padding: '2px 6px',
    background: '#0f3460',
    borderRadius: 4,
    color: '#888'
  },
  tokenFullName: {
    fontSize: 12,
    color: '#888'
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
    cursor: 'pointer',
    transition: 'all 0.2s'
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
  routeCardRight: {
    fontSize: 18
  },
  bestBadge: {
    background: '#4ade80',
    color: '#000',
    padding: '2px 6px',
    borderRadius: 4,
    fontSize: 10,
    fontWeight: 700
  },
  routeTools: {
    display: 'flex',
    gap: 4,
    flexWrap: 'wrap'
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
  routeAmount: {
    display: 'flex',
    alignItems: 'baseline'
  },
  routeMeta: {
    display: 'flex',
    gap: 12,
    fontSize: 12,
    color: '#888'
  },
  selectedDetails: {
    marginTop: 16
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
