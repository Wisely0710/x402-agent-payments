// Behaviour tests for the browser client's x402 v2 flow.
//
// They drive the built artifact (dist/) exactly as a consumer loads it: real
// fetch, a local node:http mock seller, and a stubbed window.ethereum wallet.
// No network access, no chain, no runtime dependencies.

import assert from "node:assert/strict";
import { createServer } from "node:http";
import { afterEach, describe, it } from "node:test";

import { PaymentRequiredError, X402Client, X402Error } from "../dist/index.js";

const TOKEN = "0x3333333333333333333333333333333333333333";
const MIXED_CASE_TOKEN = "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238";
const PAY_TO = "0x4444444444444444444444444444444444444444";
const USER = "0x5555555555555555555555555555555555555555";
const SIGNATURE = `0x${"ab".repeat(65)}`;

const REQUIREMENT = {
  scheme: "exact",
  network: "eip155:31337",
  amount: "100",
  asset: TOKEN,
  payTo: PAY_TO,
  maxTimeoutSeconds: 120,
  extra: { name: "Mock USD", version: "2" },
};

/** Build a PaymentRequired challenge body for the given requirements. */
function challenge(...accepts) {
  return { x402Version: 2, resource: { url: "/paid", description: "paid resource" }, accepts };
}

/** Encode a value the way the wire encodes the PAYMENT-REQUIRED header. */
function encodeWireHeader(value) {
  return Buffer.from(JSON.stringify(value), "utf8").toString("base64");
}

/** Encode raw text as a PAYMENT-REQUIRED header value. */
function encodeRawWireHeader(text) {
  return Buffer.from(text, "utf8").toString("base64");
}

/** Decode the PAYMENT-SIGNATURE header of a recorded request. */
function decodePaymentSignature(request) {
  return JSON.parse(Buffer.from(request.headers["payment-signature"], "base64").toString("utf8"));
}

/** Stub window.ethereum and record every wallet call. */
function stubWallet({ signature = SIGNATURE, fail } = {}) {
  const calls = [];
  globalThis.window = {
    ethereum: {
      request: async (args) => {
        calls.push(args);
        if (fail) {
          throw fail;
        }
        return signature;
      },
    },
  };
  return calls;
}

/** Start a mock seller on an ephemeral port and record every request it receives. */
async function startSeller(t, handler) {
  const requests = [];
  const server = createServer((req, res) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      const request = {
        method: req.method,
        url: req.url,
        headers: req.headers,
        body: raw === "" ? null : JSON.parse(raw),
      };
      requests.push(request);
      handler(request, res);
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(
    () =>
      new Promise((resolve) => {
        server.closeAllConnections();
        server.close(resolve);
      }),
  );
  return { url: `http://127.0.0.1:${server.address().port}`, requests };
}

/** Reply 402 with a PAYMENT-REQUIRED header and/or a JSON body. */
function reply402(res, { header, body } = {}) {
  const headers = { "content-type": "application/json" };
  if (header !== undefined) {
    headers["PAYMENT-REQUIRED"] = header;
  }
  res.writeHead(402, headers);
  res.end(body === undefined ? "" : JSON.stringify(body));
}

/** Reply 200 with a JSON result. */
function reply200(res, result = { status: "verified" }) {
  res.writeHead(200, { "content-type": "application/json" });
  res.end(JSON.stringify(result));
}

/** Await a promise and return the rejection reason instead of throwing. */
async function rejection(promise) {
  return promise.then(
    () => null,
    (error) => error,
  );
}

afterEach(() => {
  delete globalThis.window;
});

describe("challenge, sign, retry", () => {
  it("signs the offered requirement and retries with a PAYMENT-SIGNATURE header", async (t) => {
    const seller = await startSeller(t, (request, res) => {
      if (request.headers["payment-signature"] === undefined) {
        // The JSON body advertises a different price: the canonical header must win.
        reply402(res, {
          header: encodeWireHeader(challenge(REQUIREMENT)),
          body: challenge({ ...REQUIREMENT, amount: "999" }),
        });
        return;
      }
      reply200(res, { status: "verified", payment_hash: "0xdead", amount: "100" });
    });
    const wallet = stubWallet();

    const client = new X402Client({ providerUrl: seller.url, retryDelayMs: 0 });
    const result = await client.payWithX402("/paid", {
      userAddress: USER,
      body: { resource: "report" },
    });

    assert.deepEqual(result, { status: "verified", payment_hash: "0xdead", amount: "100" });
    assert.equal(seller.requests.length, 2);

    const [first, second] = seller.requests;
    assert.equal(first.method, "POST");
    assert.equal(first.url, "/paid");
    assert.equal(first.headers["content-type"], "application/json");
    assert.deepEqual(first.body, { resource: "report" });
    assert.equal(
      first.headers["payment-signature"],
      undefined,
      "the first attempt must not carry a payment",
    );

    assert.equal(wallet.length, 1);
    assert.equal(wallet[0].method, "eth_signTypedData_v4");
    assert.equal(wallet[0].params[0], USER);

    const typedData = JSON.parse(wallet[0].params[1]);
    assert.equal(typedData.primaryType, "TransferWithAuthorization");
    assert.deepEqual(typedData.types.EIP712Domain, [
      { name: "name", type: "string" },
      { name: "version", type: "string" },
      { name: "chainId", type: "uint256" },
      { name: "verifyingContract", type: "address" },
    ]);
    assert.deepEqual(typedData.types.TransferWithAuthorization, [
      { name: "from", type: "address" },
      { name: "to", type: "address" },
      { name: "value", type: "uint256" },
      { name: "validAfter", type: "uint256" },
      { name: "validBefore", type: "uint256" },
      { name: "nonce", type: "bytes32" },
    ]);
    assert.deepEqual(typedData.domain, {
      name: "Mock USD",
      version: "2",
      chainId: 31337,
      verifyingContract: TOKEN,
    });
    assert.equal(typedData.message.from, USER);
    assert.equal(typedData.message.to, PAY_TO);
    assert.equal(typedData.message.value, "100");
    assert.match(typedData.message.nonce, /^0x[0-9a-f]{64}$/);
    assert.equal(typedData.message.validBefore - typedData.message.validAfter, 120);

    const signed = decodePaymentSignature(second);
    assert.equal(signed.x402Version, 2);
    assert.deepEqual(signed.accepted, REQUIREMENT);
    assert.equal(signed.payload.signature, SIGNATURE);
    assert.deepEqual(signed.payload.authorization, typedData.message);
  });

  it("falls back to the JSON body when the challenge header is absent", async (t) => {
    const seller = await startSeller(t, (request, res) => {
      if (request.headers["payment-signature"] === undefined) {
        reply402(res, { body: challenge(REQUIREMENT) });
        return;
      }
      reply200(res);
    });
    stubWallet();

    const client = new X402Client({ providerUrl: seller.url, retryDelayMs: 0 });
    const result = await client.payWithX402("/paid", { userAddress: USER });

    assert.equal(result.status, "verified");
    assert.deepEqual(seller.requests[0].body, {}, "an omitted body is sent as an empty object");
    assert.deepEqual(decodePaymentSignature(seller.requests[1]).accepted, REQUIREMENT);
  });

  it("falls back to the JSON body when the challenge header is not valid JSON", async (t) => {
    const seller = await startSeller(t, (request, res) => {
      if (request.headers["payment-signature"] === undefined) {
        reply402(res, {
          header: encodeRawWireHeader("not json {"),
          body: challenge(REQUIREMENT),
        });
        return;
      }
      reply200(res);
    });
    stubWallet();

    const client = new X402Client({ providerUrl: seller.url, retryDelayMs: 0 });
    const result = await client.payWithX402("/paid", { userAddress: USER });

    assert.equal(result.status, "verified");
    assert.deepEqual(decodePaymentSignature(seller.requests[1]).accepted, REQUIREMENT);
  });

  it("rejects when the 402 carries no payable challenge", async (t) => {
    const seller = await startSeller(t, (_request, res) => reply402(res));
    const wallet = stubWallet();

    const client = new X402Client({ providerUrl: seller.url, retryDelayMs: 0 });
    const error = await rejection(client.payWithX402("/paid", { userAddress: USER }));

    assert.ok(error instanceof X402Error);
    assert.equal(error.code, "X402_ERROR");
    assert.equal(wallet.length, 0);
    assert.equal(seller.requests.length, 1);
  });
});

describe("requirement selection", () => {
  it("skips schemes and networks it cannot pay", async (t) => {
    const seller = await startSeller(t, (request, res) => {
      if (request.headers["payment-signature"] === undefined) {
        reply402(res, {
          header: encodeWireHeader(
            challenge(
              { ...REQUIREMENT, scheme: "upto" },
              { ...REQUIREMENT, network: "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp" },
              REQUIREMENT,
            ),
          ),
        });
        return;
      }
      reply200(res);
    });
    stubWallet();

    const client = new X402Client({ providerUrl: seller.url, retryDelayMs: 0 });
    await client.payWithX402("/paid", { userAddress: USER });

    assert.deepEqual(decodePaymentSignature(seller.requests[1]).accepted, REQUIREMENT);
  });

  it("rejects with the challenge when nothing is payable", async (t) => {
    const offered = challenge(
      { ...REQUIREMENT, scheme: "upto" },
      { ...REQUIREMENT, network: "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp" },
    );
    const seller = await startSeller(t, (_request, res) =>
      reply402(res, { header: encodeWireHeader(offered) }),
    );
    const wallet = stubWallet();

    const client = new X402Client({ providerUrl: seller.url, retryDelayMs: 0 });
    const error = await rejection(client.payWithX402("/paid", { userAddress: USER }));

    assert.ok(error instanceof PaymentRequiredError);
    assert.equal(error.code, "X402_PAYMENT_REQUIRED");
    assert.deepEqual(error.challenge, offered);
    assert.equal(wallet.length, 0);
    assert.equal(seller.requests.length, 1);
  });
});

describe("retry budget", () => {
  it("does not sign when the retry budget is exhausted", async (t) => {
    const seller = await startSeller(t, (_request, res) =>
      reply402(res, { header: encodeWireHeader(challenge(REQUIREMENT)) }),
    );
    const wallet = stubWallet();

    const client = new X402Client({ providerUrl: seller.url, maxRetries: 0, retryDelayMs: 0 });
    const error = await rejection(client.payWithX402("/paid", { userAddress: USER }));

    assert.ok(error instanceof PaymentRequiredError);
    assert.equal(wallet.length, 0);
    assert.equal(seller.requests.length, 1);
  });

  it("signs a new authorization for every retry", async (t) => {
    const seller = await startSeller(t, (_request, res) => {
      if (seller.requests.length <= 2) {
        reply402(res, { header: encodeWireHeader(challenge(REQUIREMENT)) });
        return;
      }
      reply200(res);
    });
    const wallet = stubWallet();

    const client = new X402Client({ providerUrl: seller.url, maxRetries: 2, retryDelayMs: 0 });
    await client.payWithX402("/paid", { userAddress: USER });

    assert.equal(seller.requests.length, 3);
    assert.equal(wallet.length, 2);
    const [firstName, secondName] = seller.requests
      .slice(1)
      .map((request) => decodePaymentSignature(request).payload.authorization.nonce);
    assert.notEqual(firstName, secondName, "a retry must not reuse the previous nonce");
  });
});

describe("wallet failures", () => {
  it("rejects without retrying when there is no account to sign with", async (t) => {
    const seller = await startSeller(t, (_request, res) =>
      reply402(res, { header: encodeWireHeader(challenge(REQUIREMENT)) }),
    );
    const wallet = stubWallet();

    const client = new X402Client({ providerUrl: seller.url, retryDelayMs: 0 });
    const error = await rejection(client.payWithX402("/paid", { userAddress: "" }));

    assert.ok(error instanceof X402Error);
    assert.equal(wallet.length, 0);
    assert.equal(seller.requests.length, 1);
  });

  it("rejects when the page has no injected wallet", async (t) => {
    const seller = await startSeller(t, (_request, res) =>
      reply402(res, { header: encodeWireHeader(challenge(REQUIREMENT)) }),
    );
    delete globalThis.window;

    const client = new X402Client({ providerUrl: seller.url, retryDelayMs: 0 });
    const error = await rejection(client.payWithX402("/paid", { userAddress: USER }));

    assert.ok(error instanceof X402Error);
    assert.equal(error.code, "X402_ERROR");
    assert.equal(seller.requests.length, 1);
  });

  it("surfaces a wallet rejection", async (t) => {
    const seller = await startSeller(t, (_request, res) =>
      reply402(res, { header: encodeWireHeader(challenge(REQUIREMENT)) }),
    );
    stubWallet({ fail: new Error("user rejected the request") });

    const client = new X402Client({ providerUrl: seller.url, retryDelayMs: 0 });
    const error = await rejection(client.payWithX402("/paid", { userAddress: USER }));

    assert.match(error.message, /user rejected the request/);
    assert.equal(seller.requests.length, 1);
  });
});

describe("EIP-712 domain", () => {
  it("uses tokenMetadata for an asset the requirement does not describe", async (t) => {
    const asset = MIXED_CASE_TOKEN;
    const requirement = {
      scheme: "exact",
      network: "eip155:31337",
      amount: "100",
      asset,
      payTo: PAY_TO,
    };
    const seller = await startSeller(t, (request, res) => {
      if (request.headers["payment-signature"] === undefined) {
        reply402(res, { header: encodeWireHeader(challenge(requirement)) });
        return;
      }
      reply200(res);
    });
    const wallet = stubWallet();

    const client = new X402Client({
      providerUrl: seller.url,
      retryDelayMs: 0,
      tokenMetadata: { [asset.toLowerCase()]: { name: "Test USD", version: "9", decimals: 6 } },
    });
    await client.payWithX402("/paid", { userAddress: USER });

    const typedData = JSON.parse(wallet[0].params[1]);
    assert.deepEqual(typedData.domain, {
      name: "Test USD",
      version: "9",
      chainId: 31337,
      verifyingContract: asset,
    });
  });

  it("falls back to USDC defaults and a 60s window when nothing describes the asset", async (t) => {
    const requirement = {
      scheme: "exact",
      network: "eip155:31337",
      amount: "100",
      asset: TOKEN,
      payTo: PAY_TO,
    };
    const seller = await startSeller(t, (request, res) => {
      if (request.headers["payment-signature"] === undefined) {
        reply402(res, { header: encodeWireHeader(challenge(requirement)) });
        return;
      }
      reply200(res);
    });
    const wallet = stubWallet();

    const client = new X402Client({ providerUrl: seller.url, retryDelayMs: 0 });
    await client.payWithX402("/paid", { userAddress: USER });

    const typedData = JSON.parse(wallet[0].params[1]);
    assert.deepEqual(typedData.domain, {
      name: "USD Coin",
      version: "2",
      chainId: 31337,
      verifyingContract: TOKEN,
    });
    assert.equal(typedData.message.validBefore - typedData.message.validAfter, 60);
  });
});

describe("provider failures", () => {
  it("rejects with the status and body of a non-402 failure", async (t) => {
    const seller = await startSeller(t, (_request, res) => {
      res.writeHead(500, { "content-type": "text/plain" });
      res.end("boom");
    });
    const wallet = stubWallet();

    const client = new X402Client({ providerUrl: seller.url, retryDelayMs: 0 });
    const error = await rejection(client.payWithX402("/paid", { userAddress: USER }));

    assert.ok(error instanceof X402Error);
    assert.match(error.message, /500/);
    assert.match(error.message, /boom/);
    assert.equal(wallet.length, 0);
    assert.equal(seller.requests.length, 1);
  });
});

describe("construction", () => {
  it("requires a providerUrl", () => {
    assert.throws(() => new X402Client({}), X402Error);
    assert.throws(() => new X402Client({ providerUrl: "" }), X402Error);
  });
});
