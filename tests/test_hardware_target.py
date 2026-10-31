import asyncio
import queue
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock,Mock,patch

from pc.bridge import Bridge
from pc.core import Repository
from pc.hardware import require_touch349


class Touch349IsolationTests(unittest.TestCase):
    def test_only_supported_board_is_accepted(self):
        require_touch349({'ok':True,'hardware':'waveshare-touch-lcd-3.49-v2'})
        for state in ({'ok':True},{'ok':True,'hardware':'different-board'},
                      {'ok':False,'hardware':'waveshare-touch-lcd-3.49-v2'},None):
            with self.assertRaises(RuntimeError):require_touch349(state)

    def test_wrong_board_receives_only_state_query_and_cache_stays_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            bridge=object.__new__(Bridge)
            bridge.repository=Repository(folder)
            bridge.repository.configure(device_address='synthetic-device')
            bridge.repository.enqueue_note('keep this offline note')
            before=bridge.repository.snapshot()
            bridge.events=queue.Queue();bridge.stopping=threading.Event()
            client=Mock();client.services.get_service.return_value=object()
            context=Mock();context.__aenter__=AsyncMock(return_value=client)
            context.__aexit__=AsyncMock(return_value=False)
            protocol=Mock();protocol.start=AsyncMock()
            async def state_only(packet):
                bridge.stopping.set()
                return {'ok':True,'hardware':'different-board','notes':[]}
            protocol.request=AsyncMock(side_effect=state_only)
            with patch('pc.bridge.BleakClient',return_value=context),patch('pc.bridge.Protocol',return_value=protocol):
                asyncio.run(bridge.main())
            self.assertEqual([call.args[0] for call in protocol.request.call_args_list],[{'op':'state'}])
            self.assertEqual(bridge.repository.snapshot(),before)
