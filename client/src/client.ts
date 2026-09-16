import { PaymentRequiredError, X402Error } from "./models";
import type {
  X402ClientOptions,
  X402PaymentPayload,
  X402PaymentRequired,
  X402PaymentRequirement,
  X402PaymentResult,
  X402PaymentSignature,
  X402TokenMetadata,
  X402TransferAuthorization,
} from "./types";

declare global {
  interface Window {
    ethereum?: {
      request: (args: { method: string; params?: unknown[] }) => Promise<unknown>;
    };
  }
}

const DEFAULT_RETRY_DELAY = 1200;
/** authorization 有效窗口預設值（秒），當 requirement 未指定 maxTimeoutSeconds 時使用 */
const DEFAULT_TIMEOUT_SECONDS = 60;
const DEFAULT_TOKEN_METADATA: X402TokenMetadata = {
  name: "USD Coin",
  version: "2",
  decimals: 6,
};

/** 將 UTF-8 字串編碼為標準 base64（瀏覽器 API，不依賴第三方套件）。 */
function utf8ToBase64(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

/** 將標準 base64 解碼為 UTF-8 字串（瀏覽器 API，不依賴第三方套件）。 */
function base64ToUtf8(base64: string): string {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new TextDecoder().decode(bytes);
}

/** 產生 32-byte 隨機 nonce（0x 前綴 + 64 hex chars）。 */
function generateNonce(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  let hex = "";
  for (let i = 0; i < bytes.length; i++) {
    hex += bytes[i].toString(16).padStart(2, "0");
  }
  return `0x${hex}`;
}

export class X402Client {
  /** 紀錄目前的初始化選項 */
  private readonly options: X402ClientOptions;

  constructor(options: X402ClientOptions) {
    if (!options.providerUrl) {
      throw new X402Error("Missing providerUrl option");
    }
    this.options = {
      maxRetries: 1,
      retryDelayMs: DEFAULT_RETRY_DELAY,
      ...options,
      tokenMetadata: options.tokenMetadata || {},
    };
  }

  /** 提供給前端的主要 API，封裝 x402 支付流程 */
  async payWithX402(endpoint: string, payload: X402PaymentPayload): Promise<X402PaymentResult> {
    const url = `${this.options.providerUrl}${endpoint}`;
    const body: Record<string, unknown> = payload.body ?? {};

    return this.sendRequest(url, body, payload.userAddress, 0);
  }

  /** 處理 402 邏輯與重試流程 */
  private async sendRequest(
    url: string,
    body: Record<string, unknown>,
    userAddress: string,
    attempt: number,
    extraHeaders: Record<string, string> = {},
  ): Promise<X402PaymentResult> {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...extraHeaders,
      },
      body: JSON.stringify(body),
    });

    if (response.status === 402) {
      const required = await this.parsePaymentRequired(response);
      if (attempt >= (this.options.maxRetries ?? 0)) {
        throw new PaymentRequiredError("x402 payment required", required);
      }
      if (!userAddress) {
        throw new X402Error("missing userAddress: cannot sign the x402 request");
      }
      const requirement = this.selectExactRequirement(required);
      const signedPayment = await this.signChallenge(requirement, userAddress);
      await this.delay(this.options.retryDelayMs ?? DEFAULT_RETRY_DELAY);
      return this.sendRequest(url, body, userAddress, attempt + 1, {
        ...extraHeaders,
        "PAYMENT-SIGNATURE": utf8ToBase64(JSON.stringify(signedPayment)),
      });
    }

    if (!response.ok) {
      const detail = await response.text();
      throw new X402Error(`provider returned status ${response.status}: ${detail}`);
    }

    return (await response.json()) as X402PaymentResult;
  }

  /** 從 402 回應解析官方 PaymentRequired（優先 canonical PAYMENT-REQUIRED header，其次 JSON body）。 */
  private async parsePaymentRequired(response: Response): Promise<X402PaymentRequired> {
    const header = response.headers.get("PAYMENT-REQUIRED");
    if (header) {
      try {
        return JSON.parse(base64ToUtf8(header)) as X402PaymentRequired;
      } catch {
        // header 無法解碼時 fallthrough 到 body
      }
    }
    const rawBody = await response.text();
    if (!rawBody) {
      throw new X402Error("x402 402 回應缺少 payment requirement");
    }
    return JSON.parse(rawBody) as X402PaymentRequired;
  }

  /** 選取相容的 exact EVM payment requirement。 */
  private selectExactRequirement(required: X402PaymentRequired): X402PaymentRequirement {
    const requirement = required.accepts?.find(
      (r) => r.scheme === "exact" && this.parseEip155ChainId(r.network) !== null,
    );
    if (!requirement) {
      throw new PaymentRequiredError("no compatible exact EVM payment requirement", required);
    }
    return requirement;
  }

  /** 解析 CAIP-2 ``eip155:<chain_id>`` grammar；不符合回傳 null（僅接受 ``eip155:`` 前綴＋數字）。 */
  private parseEip155ChainId(network: string): number | null {
    const match = /^eip155:(\d+)$/.exec(network);
    return match ? Number(match[1]) : null;
  }

  /** 解析 CAIP-2 network（僅接受 ``eip155:<chain_id>``）為 EVM chain id。 */
  private parseChainId(network: string): number {
    const chainId = this.parseEip155ChainId(network);
    if (chainId === null) {
      throw new X402Error(`unsupported CAIP-2 network: ${network}`);
    }
    return chainId;
  }

  /** 解析 EIP-712 domain name/version：優先 requirement.extra，其次 tokenMetadata fallback。 */
  private resolveTokenMetadata(requirement: X402PaymentRequirement): X402TokenMetadata {
    const extra = requirement.extra;
    if (extra?.name && extra?.version) {
      return { name: extra.name, version: extra.version, decimals: 6 };
    }
    return this.options.tokenMetadata?.[requirement.asset.toLowerCase()] ?? DEFAULT_TOKEN_METADATA;
  }

  /** 產生真實的 EIP-3009 TransferWithAuthorization 簽名並組成官方 PaymentPayload。 */
  private async signChallenge(
    requirement: X402PaymentRequirement,
    userAddress: string,
  ): Promise<X402PaymentSignature> {
    if (typeof window === "undefined" || !window.ethereum) {
      throw new X402Error("找不到 window.ethereum，請確認已連接錢包");
    }

    const metadata = this.resolveTokenMetadata(requirement);
    const chainId = this.parseChainId(requirement.network);
    const now = Math.floor(Date.now() / 1000);
    const maxTimeoutSeconds = requirement.maxTimeoutSeconds ?? DEFAULT_TIMEOUT_SECONDS;
    const validAfter = now;
    const validBefore = now + maxTimeoutSeconds;
    const nonce = generateNonce();
    // 授權物件一次建構，同時供 EIP-712 簽名與 PAYMENT-SIGNATURE payload 使用，避免兩處結構化重複漂移
    const authorization: X402TransferAuthorization = {
      from: userAddress,
      to: requirement.payTo,
      value: requirement.amount,
      validAfter,
      validBefore,
      nonce,
    };

    const typedData = {
      types: {
        EIP712Domain: [
          { name: "name", type: "string" },
          { name: "version", type: "string" },
          { name: "chainId", type: "uint256" },
          { name: "verifyingContract", type: "address" },
        ],
        TransferWithAuthorization: [
          { name: "from", type: "address" },
          { name: "to", type: "address" },
          { name: "value", type: "uint256" },
          { name: "validAfter", type: "uint256" },
          { name: "validBefore", type: "uint256" },
          { name: "nonce", type: "bytes32" },
        ],
      },
      domain: {
        name: metadata.name,
        version: metadata.version,
        chainId,
        verifyingContract: requirement.asset,
      },
      primaryType: "TransferWithAuthorization",
      message: authorization,
    };

    const signature = (await window.ethereum.request({
      method: "eth_signTypedData_v4",
      params: [userAddress, JSON.stringify(typedData)],
    })) as string;

    return {
      x402Version: 2,
      accepted: requirement,
      payload: {
        signature,
        authorization,
      },
    };
  }

  /** 簡單的 sleep 工具，確保重試節奏一致 */
  private async delay(ms: number): Promise<void> {
    await new Promise((resolve) => setTimeout(resolve, ms));
  }
}
