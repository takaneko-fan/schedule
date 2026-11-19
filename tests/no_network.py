"""テストから外部に通信させない（どのテストファイルも最初にこれを import する）。

テストの中で外部（AI・X API・公式サイトなど）への接続をしようとすると、接続は失敗させたうえで、そのテストを失敗にする。
プログラムは通信の失敗を握りつぶして続けることが多いので、接続を止めるだけでは気づけないため。
"""

import socket
import unittest

ATTEMPTS = []  # (接続先, テスト名)
_current = [None]


def _blocked(address):
    ATTEMPTS.append((address, _current[0]))
    raise OSError(f"テストから外部に通信しようとしました: {address}")


def _connect(self, address):
    _blocked(address)


def _create_connection(address, *args, **kwargs):
    _blocked(address)


socket.socket.connect = _connect
socket.create_connection = _create_connection

_run = unittest.TestCase.run


def _run_without_network(self, result=None):
    _current[0] = self.id()
    start = len(ATTEMPTS)
    out = _run(self, result)
    tried = [a for a, _ in ATTEMPTS[start:]]
    if tried and result is not None:
        message = f"テストから外部に通信しようとしました（AI や API の呼び出しを置き換えてください）: {tried}"
        result.addFailure(self, (AssertionError, AssertionError(message), None))
    return out


unittest.TestCase.run = _run_without_network
