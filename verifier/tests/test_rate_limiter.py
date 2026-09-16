"""RateLimiter 滑動窗口預算／重置與 Retry-After 延遲的單元測試。

時序完全確定：以 fake clock 替換 ``x402_v2.rate_limiter`` 模組內的 ``time``
binding 驅動窗口前進，不 sleep。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import x402_v2.rate_limiter as rate_limiter_module  # noqa: E402
from x402_v2.rate_limiter import RateLimiter  # noqa: E402


class _FakeClock:
    """``time`` module 的最小替身：實作 limiter 用到的 ``time()``。"""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RateLimiterTest(unittest.TestCase):
    """RateLimiter 窗口預算／重置與 retry delay（無 sleep、時序確定）。"""

    def test_window_budget_blocks_after_max_requests_within_window(self) -> None:
        """同一窗口內第 max_requests+1 次請求被擋；被擋請求不消耗額外預算。"""
        clock = _FakeClock(1_000.0)
        with patch.object(rate_limiter_module, "time", clock):
            limiter = RateLimiter(max_requests=2, window_seconds=60)
            self.assertTrue(limiter.is_allowed("k"))
            self.assertTrue(limiter.is_allowed("k"))
            self.assertFalse(limiter.is_allowed("k"))
            # count／first_request 皆未被 blocked 請求變動 → 仍被擋
            self.assertFalse(limiter.is_allowed("k"))

    def test_retry_after_ceils_remaining_window_seconds(self) -> None:
        """blocked key 的 Retry-After = ceil(窗口剩餘秒數)，恆為正整數。"""
        clock = _FakeClock(1_000.0)
        with patch.object(rate_limiter_module, "time", clock):
            limiter = RateLimiter(max_requests=1, window_seconds=60)
            self.assertTrue(limiter.is_allowed("k"))  # window 起點 = 1000
            clock.advance(29.2)  # 剩 60 - 29.2 = 30.8s → ceil 31
            self.assertFalse(limiter.is_allowed("k"))
            self.assertEqual(limiter.retry_after_seconds("k"), 31)
            clock.advance(30.0)  # 剩 0.8s → ceil 1（最小正整數）
            self.assertEqual(limiter.retry_after_seconds("k"), 1)

    def test_window_resets_after_elapse(self) -> None:
        """窗口完全經過後預算重置：下一請求放行並重新開始完整新窗口。"""
        clock = _FakeClock(1_000.0)
        with patch.object(rate_limiter_module, "time", clock):
            limiter = RateLimiter(max_requests=1, window_seconds=60)
            self.assertTrue(limiter.is_allowed("k"))
            self.assertFalse(limiter.is_allowed("k"))
            clock.advance(60.0)  # 窗口完全經過
            self.assertTrue(limiter.is_allowed("k"))
            self.assertFalse(limiter.is_allowed("k"))  # 新窗口預算再次用盡
            self.assertEqual(limiter.retry_after_seconds("k"), 60)

    def test_retry_after_zero_without_active_entry(self) -> None:
        """無 active entry（從未出現／窗口已過期）→ 0；query 不得建立 entry。"""
        clock = _FakeClock(1_000.0)
        with patch.object(rate_limiter_module, "time", clock):
            limiter = RateLimiter(max_requests=1, window_seconds=60)
            # 從未出現的 key → 0（read-only：不建立 entry、不消耗預算）
            self.assertEqual(limiter.retry_after_seconds("ghost"), 0)
            self.assertTrue(limiter.is_allowed("ghost"))  # 首次請求仍放行
            # 窗口已過期但尚未再請求的 entry → 0
            clock.advance(60.0)
            self.assertEqual(limiter.retry_after_seconds("ghost"), 0)
            self.assertTrue(limiter.is_allowed("ghost"))  # 過期後請求照常放行

    def test_keys_are_independent(self) -> None:
        """不同 key 各自獨立預算與窗口。"""
        clock = _FakeClock(1_000.0)
        with patch.object(rate_limiter_module, "time", clock):
            limiter = RateLimiter(max_requests=1, window_seconds=60)
            self.assertTrue(limiter.is_allowed("a"))
            self.assertTrue(limiter.is_allowed("b"))
            self.assertFalse(limiter.is_allowed("a"))
            self.assertEqual(limiter.retry_after_seconds("a"), 60)
            self.assertEqual(limiter.retry_after_seconds("b"), 60)


if __name__ == "__main__":
    unittest.main()
