export interface X402PaymentPayload {
  /** 簽署 EIP-712 authorization 的付款人地址 */
  userAddress: string;
  /** 呼叫端自行提供的 request body；未提供時送出空物件 */
  body?: Record<string, unknown>;
}

export interface X402PaymentResult {
  /** Provider 返回的最終狀態 */
  status: string;
  /** 可選文字訊息，方便 UI 顯示 */
  message?: string;
  /** 鏈上交易哈希（若支援，代理模式下由前端發送） */
  tx_hash?: string | null;
  /** 付款哈希 */
  payment_hash?: string;
  /** 付款 ID */
  payment_id?: string;
  /** 金額（字符串格式） */
  amount?: string;
  /** 代幣地址 */
  token?: string;
  /** 用戶地址 */
  user_address?: string;
  /** 接收者地址 */
  recipient?: string;
  /** 鏈 ID */
  chain_id?: number;
  /** 交易上下文（代理模式） */
  tx_context?: Record<string, unknown>;
  /** 區塊號（若支援） */
  block_number?: number;
  /** 任何額外欄位（向後兼容） */
  [key: string]: unknown;
}

/**
 * 官方 x402 v2 ``accepts`` 中單一可接受的 payment requirement。
 * 欄位依官方 wire：``network`` 為 CAIP-2（如 ``eip155:84532``）、
 * ``amount`` 為最小單位原子量、``asset`` 為 ERC-20 地址、``payTo`` 為收款地址。
 */
export interface X402PaymentRequirement {
  /** 付款 scheme（本客戶端使用 "exact"） */
  scheme: string;
  /** CAIP-2 network 識別符，例如 "eip155:84532" */
  network: string;
  /** 最小單位的原子金額 */
  amount: string;
  /** ERC-20 代幣合約地址 */
  asset: string;
  /** 收款地址 */
  payTo: string;
  /** authorization 有效窗口（秒） */
  maxTimeoutSeconds?: number;
  /** scheme 特定 extras（EIP-712 domain name/version） */
  extra?: {
    name?: string;
    version?: string;
    assetTransferMethod?: string;
  };
}

/** 官方 x402 v2 HTTP 402 回應的 ``PaymentRequired`` body／``PAYMENT-REQUIRED`` header。 */
export interface X402PaymentRequired {
  /** 官方 x402 wire 版本（2） */
  x402Version: number;
  /** 受保護的資源 */
  resource?: {
    url?: string;
    description?: string;
    mimeType?: string;
  };
  /** 可接受的 payment requirements */
  accepts: X402PaymentRequirement[];
  /** 可選的協議層錯誤訊息 */
  error?: string;
}

/** EIP-3009 TransferWithAuthorization 的 authorization 參數。 */
export interface X402TransferAuthorization {
  from: string;
  to: string;
  value: string;
  validAfter: number;
  validBefore: number;
  /** 32-byte hex nonce（0x + 64 hex chars） */
  nonce: string;
}

/** 官方 x402 v2 ``PAYMENT-SIGNATURE`` header 承載的 ``PaymentPayload``。 */
export interface X402PaymentSignature {
  x402Version: number;
  /** 選定的 payment requirement（echo 自 402 回應的 accepts） */
  accepted: X402PaymentRequirement;
  payload: {
    /** EIP-712 TransferWithAuthorization 簽名 */
    signature: string;
    authorization: X402TransferAuthorization;
  };
}

export interface X402TokenMetadata {
  name: string;
  version: string;
  decimals: number;
}

export interface X402ClientOptions {
  /** 供前端呼叫的 Provider URL（例如 http://127.0.0.1:8000） */
  providerUrl: string;
  /** 請求失敗時的最大重試次數 */
  maxRetries?: number;
  /** 重新發送 402 請求前的等待時間（毫秒） */
  retryDelayMs?: number;
  /** 各 token 位址對應的 EIP-712 設定（當 402 回應未附 extra 時作為 fallback） */
  tokenMetadata?: Record<string, X402TokenMetadata>;
}
