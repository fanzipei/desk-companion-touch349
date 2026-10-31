import queue
import tempfile
import unittest
from pc.bridge import Bridge
from pc.core import Repository


class TouchDisplaySyncTests(unittest.TestCase):
    def test_screen_changes_persist_to_pc_desired_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge=object.__new__(Bridge)
            bridge.repository=Repository(directory)
            bridge.events=queue.Queue()
            wakeups=[]
            bridge.wake=lambda:wakeups.append(True)
            bridge.event({'event':'display_changed','theme':'dark','font_size':24,
                          'line_spacing':4,'show_hints':True,'visible_lines':4})
            config=Repository(directory).snapshot()['config']
            self.assertEqual(config['screen_theme'],'dark')
            self.assertEqual(config['screen_font_size'],24)
            self.assertEqual(config['screen_line_spacing'],4)
            self.assertTrue(config['screen_show_hints'])
            self.assertEqual(bridge.events.get_nowait(),('changed',None))
            self.assertEqual(wakeups,[True])


if __name__=='__main__': unittest.main()
