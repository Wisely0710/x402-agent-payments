"""Payment signing — official x402 EVM signer (EthAccountSigner, eth-account).

For local testing only: the private key comes from the X402_AGENT_PRIVATE_KEY
environment variable. A production deployment keeps the key out of the service
process and delegates signing to a separate signing service or the user's own
wallet.
"""

from __future__ import annotations

import os

from eth_account import Account
from eth_account.signers.local import LocalAccount
from x402.mechanisms.evm import EthAccountSigner


class MissingPrivateKeyError(RuntimeError):
    """The X402_AGENT_PRIVATE_KEY environment variable is not set."""


def load_account_from_env() -> tuple[EthAccountSigner, LocalAccount]:
    """Build the official signer from the environment.

    Returns (EthAccountSigner, account).
    """
    key = os.environ.get("X402_AGENT_PRIVATE_KEY")
    if not key:
        raise MissingPrivateKeyError(
            "set X402_AGENT_PRIVATE_KEY (local testing only)"
        )
    account = Account.from_key(key)
    return EthAccountSigner(account), account
