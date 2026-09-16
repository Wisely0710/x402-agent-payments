export class X402Error extends Error {
  /** 類型標識，方便前端判斷錯誤 */
  public readonly code: string;

  constructor(message: string, code = "X402_ERROR") {
    super(message);
    this.code = code;
  }
}

export class PaymentRequiredError extends X402Error {
  /** 伺服器返回的 402 資訊 */
  public readonly challenge: unknown;

  constructor(message: string, challenge: unknown) {
    super(message, "X402_PAYMENT_REQUIRED");
    this.challenge = challenge;
  }
}
