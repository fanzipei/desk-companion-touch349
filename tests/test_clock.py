# Copyright 2026 fanzipei
# SPDX-License-Identifier: Apache-2.0
from datetime import datetime,timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock
from pc.clock_sync import ClockSync,INTERVAL
from pc.protocol import Protocol


class ClockSyncTests(unittest.IsolatedAsyncioTestCase):

    async def test_syncs_on_connect_and_approximately_every_four_hours(self):
        protocol=type("Protocol",(),{})()
        protocol.request=AsyncMock(return_value={"ok":True})
        sync=ClockSync()
        self.assertTrue(await sync.update(protocol,0,1792810000))
        self.assertIsNone(await sync.update(protocol,INTERVAL-1,1792820000))
        self.assertTrue(await sync.update(protocol,INTERVAL,1792824400))
        self.assertEqual(protocol.request.call_count,2)
        self.assertEqual(protocol.request.call_args.args[0],{"op":"time_sync","unix":1792824400})
        self.assertTrue(await ClockSync().update(protocol,INTERVAL+10,1792824410))

    async def test_failed_sync_retries_later(self):
        protocol=type("Protocol",(),{})()
        protocol.request=AsyncMock(side_effect=[{"ok":False},{"ok":True}])
        sync=ClockSync()
        self.assertFalse(await sync.update(protocol,0,1792810000))
        self.assertIsNone(await sync.update(protocol,59,1792810059))
        self.assertTrue(await sync.update(protocol,60,1792810060))
