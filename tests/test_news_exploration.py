import json
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import Mock,patch

from pc.core import Repository
from pc.app import App
from pc.bridge import Bridge
from pc.windows import Hotkeys
from pc.protocol import Protocol
from pc.news_exploration import ExplorationPlanner,same_event
from pc.news_preferences import NewsPreferences
from pc.news_recommender import NewsRecommender
from tests.test_news_learning import article,feedback,NOW


def story(n,title,publisher='test'):
    return {**article(n),'title':title,'publisher':publisher,'time':NOW}


class ReminderRemovalTests(unittest.TestCase):
    def test_retired_hotkey_is_never_registered_and_sync_commands_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Repository(directory).snapshot()['config'];config.update(reminder_hotkey='Ctrl+Alt+L',screenshot_enabled=True)
            with patch('pc.windows.user32') as user,patch('pc.windows.kernel32'):
                user.GetMessageW.return_value=0;user.RegisterHotKey.return_value=True
                Hotkeys(config,queue.Queue()).run()
                self.assertEqual([c.args[1] for c in user.RegisterHotKey.call_args_list],[1,2,3])
        bridge=object.__new__(Bridge);bridge.commands=queue.Queue();bridge.wake=Mock()
        for op in ('remind','schedule_get','schedule_begin'):
            with self.assertRaises(ValueError):bridge.command({'op':op})
        self.assertTrue(bridge.commands.empty());bridge.wake.assert_not_called()

    def test_legacy_settings_and_files_are_ignored_without_changing_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            notes=[{'id':'keep','text':'保留的便签','pinned':True}]
            (root/'state.json').write_text(json.dumps({'notes':notes,'config':{'reminder_hotkey':'Ctrl+Alt+L'}}))
            reminder=root/'reminders.json';reminder.write_text('[{"old":"preserve"}]')
            repo=Repository(root)
            self.assertNotIn('reminder_hotkey',repo.snapshot()['config'])
            self.assertEqual(repo.snapshot()['notes'],notes)
            self.assertEqual(reminder.read_text(),'[{"old":"preserve"}]')
            self.assertFalse(hasattr(App,'start_reminder'));self.assertFalse(hasattr(Protocol,'sync_reminders'))

    def test_stale_reminder_events_do_not_open_a_window(self):
        app=object.__new__(App);app.events=queue.Queue();app.events.put(('reminder',None))
        app.closed=True;app.root=Mock();app.last_command=10**30
        with patch('pc.app.DATA',Path(tempfile.gettempdir())):app.poll()
        app.root.assert_not_called()


class ExplorationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.planner=ExplorationPlanner(self.root)
        self.repo=Repository(self.root);self.config=self.repo.snapshot()['config']
        self.config.update(news_limit=10,news_exploration_percent=30)
        self.items=[story(1,'AI model improves robot training','A'),story(2,'NASA telescope finds a distant planet','B'),
                    story(3,'Solar battery expands energy storage','C'),story(4,'Cancer vaccine genetic study','D'),
                    story(5,'Central bank interest rate decision','E'),story(6,'Quantum physics breakthrough','F'),
                    story(7,'European election results','G'),story(8,'New chip processor announced','H')]
    def plan(self,items=None,events=None):
        return self.planner.plan(items or self.items,self.config,self.repo.snapshot(),events or [],NOW)

    def test_explicit_quota_diversity_rotation_and_repeated_push_not_new_exposure(self):
        plan=self.plan();self.assertEqual(plan['budget'],3)
        reserved=[i for i in plan['pool'] if i['id'] in plan['reserved']]
        self.assertEqual(len({i['recommendation_topic'] for i in reserved}),3)
        chosen=self.planner.mix(reserved,plan['reserved'],10)
        self.planner.remember(plan,chosen,NOW)
        next_plan=self.plan();self.assertTrue(set(next_plan['reserved']).isdisjoint(plan['reserved']))
        shown=sum(i['shown'] for i in self.planner.state['topics'].values())
        self.planner.remember(next_plan,chosen,NOW+900)
        self.assertEqual(sum(i['shown'] for i in self.planner.state['topics'].values()),shown)

    def test_small_negative_sample_never_bans_the_topic_and_repeated_actions_count_once(self):
        item=self.items[1];event={**feedback(1,'deleted',2),'title':item['title']}
        plan=self.plan([item],[event,event])
        self.assertEqual(plan['reserved'],[item['id']])
        self.assertEqual(plan['state']['topics']['space']['deleted'],1)

    def test_upgrade_seeds_only_delivered_exposure_and_moves_beyond_old_topics(self):
        old=[story(n,'AI model and robot training news '+str(n),'A') for n in range(30,50)]
        snapshot=self.repo.snapshot();snapshot['news']=old
        snapshot['news_history']=[story(51,'NASA planet discovery','B')]
        plan=self.planner.plan(self.items,self.config,snapshot,[],NOW)
        self.assertEqual(plan['state']['topics']['ai']['shown'],20)
        self.assertNotIn(str(article(51)['id']),plan['state']['articles'])
        reserved=[i for i in plan['pool'] if i['id'] in plan['reserved']]
        self.assertNotIn('ai',{i['recommendation_topic'] for i in reserved})

    def test_event_duplicates_stale_items_and_promotions_are_removed(self):
        a=story(20,'NASA telescope discovers distant planet with water atmosphere','A')
        b=story(21,'NASA telescope discovers distant planet with water atmosphere today','B')
        self.assertTrue(same_event(a,b))
        stale={**self.items[0],'time':NOW-49*3600}
        ad=story(22,'Weekend shoe sale: buy one get one free')
        plan=self.plan([a,b,stale,ad]);self.assertEqual(len(plan['pool']),1)

    def test_exploration_feedback_keeps_provenance_and_twenty_action_threshold(self):
        plan=self.plan();reserved=[i for i in plan['pool'] if i['id'] in plan['reserved']]
        self.planner.remember(plan,self.planner.mix(reserved,plan['reserved'],10),NOW)
        item=reserved[0];prefs=NewsPreferences(self.root)
        prefs.record({**feedback(1,'pinned'),'id':item['id'],'title':item['title']},'test')
        self.assertEqual(prefs.state['events'][0]['recommendation_lane'],'explore')
        self.assertEqual(prefs.learning_count(),1)
        ai=Mock();self.assertFalse(prefs.evolve(ai,{}));ai.complete.assert_not_called()

    def test_missing_exploration_is_translated_independently_and_interleaved(self):
        repo=self.repo;repo.configure(news_personalized=True,news_limit=4)
        r=NewsRecommender(repo)
        def complete(config,system,content):
            data=json.loads(content)
            rows=data['headlines']
            if 'exploration_ids' in data:
                # Deliberately ignore exploration; host must repair the omission.
                rows=[i for i in rows if i['id'] not in data['exploration_ids']][:2]
            return json.dumps({'selected':[{'id':i['id'],'title_zh':'测试标题：'+str(i['id']),'category':'technology'} for i in rows]})
        r.ai.complete=Mock(side_effect=complete)
        with patch('pc.news_exploration.time.time',return_value=NOW):result=r.select(self.items,repo.snapshot()['config'])
        self.assertEqual(r.ai.complete.call_count,2)
        self.assertEqual(sum(i['recommendation_lane']=='explore' for i in result),2)
        repair=json.loads(r.ai.complete.call_args.args[2]);self.assertNotIn('skill',repair)
        self.assertTrue(all('body' not in i for i in repair['headlines']))

    def test_percentage_validated_and_marker_sent_without_new_device_protocol(self):
        for percent in (0,9,61,True):
            with self.assertRaises(ValueError):self.repo.configure(news_exploration_percent=percent)
        self.repo.configure(news_personalized=True)
        item={**self.items[0],'title_zh':'测试探索标题','summary':'正文','recommendation_lane':'explore'}
        self.repo.publish_recommendations([item],[item],NOW)
        self.assertIn('探索',self.repo.news_packet()['items'][0]['title'])

    def test_empty_interest_response_cannot_suppress_valid_exploration(self):
        self.repo.configure(news_personalized=True,news_limit=4)
        r=NewsRecommender(self.repo)
        def complete(config,system,content):
            data=json.loads(content)
            if 'exploration_ids' in data:return '{"selected":[]}'
            return json.dumps({'selected':[{'id':row['id'],'title_zh':'新领域标题','category':'technology'} for row in data['headlines']]})
        r.ai.complete=Mock(side_effect=complete)
        with patch('pc.news_exploration.time.time',return_value=NOW):result=r.select(self.items,self.repo.snapshot()['config'])
        self.assertEqual(len(result),2);self.assertTrue(all(i['recommendation_lane']=='explore' for i in result))


if __name__=='__main__':unittest.main()
