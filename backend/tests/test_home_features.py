import copy
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import main
from wud import ContainerSnapshot, normalize_container

VERSION='2026-11-13T12:00:00+00:00'


class HomeFeaturesTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        folder=self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(main,'DB_PATH',Path(folder)/'reading.sqlite3'))
        self.stack.enter_context(patch('main.fetch_title',return_value='Article'))
        self.client=self.stack.enter_context(TestClient(main.app))
        self.readings=[]
        self.wud=ContainerSnapshot(configured=False)
        self.stack.enter_context(patch.object(main.app.state.availability,'snapshot',side_effect=lambda:{'readings':self.readings,'checking':False}))
        async def wud_snapshot():return self.wud
        self.stack.enter_context(patch.object(main.app.state.wud,'snapshot',side_effect=wud_snapshot))

    def article(self,kind='html'):
        count=len(self.client.get('/api/reading').json())
        article=self.client.post('/api/reading',json={'url':f'https://example.com/article-{count}','title':f'Article {count}'}).json()
        with main.database() as db:
            db.execute("UPDATE articles SET copy_format=?,copy_saved_at=?,copy_status='ready' WHERE id=?",(kind,VERSION,article['id']))
        return article['id']

    def progress(self,article_id,revision=0,position=None,version=VERSION):
        return self.client.post(f'/api/reading/{article_id}/article-progress',json={'copy_version':version,'revision':revision,'position':position})

    def test_home_settings_seed_once_and_persist_reordered_hidden_sections(self):
        initial=self.client.get('/api/home/layout').json()
        settings=copy.deepcopy(initial['settings']);settings['sections'].reverse();settings['sections'][0]['visible']=False
        settings['show_clock']=False;settings['previews']['reading']=10;settings['hacker_news']={'view':'discussed','window':'7d'}
        response=self.client.put('/api/home/layout',json={'revision':initial['revision'],'settings':settings})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['revision'],1)
        self.assertEqual(self.client.get('/api/home/layout').json()['settings'],settings)
        import home
        home.initialize_home()
        self.assertEqual(self.client.get('/api/home/layout').json()['settings'],settings)

    def test_old_layout_revision_cannot_overwrite_another_device(self):
        initial=self.client.get('/api/home/layout').json()
        self.assertEqual(self.client.put('/api/home/layout',json=initial).status_code,200)
        self.assertEqual(self.client.put('/api/home/layout',json=initial).status_code,409)
        self.assertEqual(self.client.get('/api/home/layout').json()['revision'],1)

    def test_layout_rejects_duplicate_missing_unknown_sections_and_bad_preview_limits(self):
        initial=self.client.get('/api/home/layout').json()
        for mode in ('duplicate','missing','unknown','count','boolean'):
            update=copy.deepcopy(initial)
            if mode=='duplicate':update['settings']['sections'][1]=update['settings']['sections'][0]
            if mode=='missing':update['settings']['sections'].pop()
            if mode=='unknown':update['settings']['sections'][0]['id']='surprise'
            if mode=='count':update['settings']['previews']['feeds']=1000
            if mode=='boolean':update['settings']['show_clock']='false'
            with self.subTest(mode=mode):self.assertEqual(self.client.put('/api/home/layout',json=update).status_code,422)

    def test_article_position_round_trips_and_is_exposed_in_reading_list(self):
        article_id=self.article()
        position={'fraction':.43,'anchor':12,'offset':.25}
        self.assertIsNone(self.client.get(f'/api/reading/{article_id}/article-progress').json()['position'])
        response=self.progress(article_id,position=position)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['revision'],1)
        restored=self.client.get(f'/api/reading/{article_id}/article-progress').json()['position']
        self.assertEqual({key:restored[key] for key in position},position)
        article=self.client.get('/api/reading').json()[0]
        self.assertEqual(article['html_reading_progress']['fraction'],.43)
        self.assertNotIn('html_progress_revision',article)
        self.assertIsNone(article['reading_progress'])

    def test_an_older_article_tab_cannot_overwrite_or_restore_cleared_progress(self):
        article_id=self.article();position={'fraction':.5,'anchor':3,'offset':.1}
        self.assertEqual(self.progress(article_id,position=position).status_code,200)
        self.assertEqual(self.progress(article_id,position=position).status_code,409)
        self.assertEqual(self.progress(article_id,revision=1).status_code,200)
        self.assertEqual(self.progress(article_id,revision=1,position=position).status_code,409)
        self.assertIsNone(self.client.get(f'/api/reading/{article_id}/article-progress').json()['position'])

    def test_replacing_copy_invalidates_prior_article_position(self):
        article_id=self.article();position={'fraction':.5,'anchor':3,'offset':.1}
        self.progress(article_id,position=position)
        with main.database() as db:db.execute('UPDATE articles SET copy_saved_at=? WHERE id=?',('new-version',article_id))
        self.assertIsNone(self.client.get(f'/api/reading/{article_id}/article-progress').json()['position'])
        response=self.progress(article_id,revision=1,position=position)
        self.assertEqual(response.status_code,409)
        self.assertEqual(response.json()['detail']['reason'],'copy_changed')
        self.assertEqual(self.client.get('/api/home/continue').json()['items'],[])

    def test_article_progress_is_separate_from_pdf_progress_and_validated(self):
        pdf=self.article('pdf')
        self.assertEqual(self.client.get(f'/api/reading/{pdf}/article-progress').status_code,409)
        article_id=self.article()
        self.assertEqual(self.client.get(f'/api/reading/{article_id}/progress').status_code,409)
        for position in ({'fraction':2,'anchor':1,'offset':0},{'fraction':.5,'anchor':-1,'offset':0},{'fraction':.5,'anchor':1,'offset':-1},{'fraction':.5,'anchor':1.5,'offset':0}):
            self.assertEqual(self.progress(article_id,position=position).status_code,422)
        self.assertEqual(self.client.get('/api/reading/missing/article-progress').status_code,404)

    def test_continue_reading_includes_articles_and_last_page_pdf_until_marked_read(self):
        html=self.article();pdf=self.article('pdf')
        self.progress(html,position={'fraction':.42,'anchor':4,'offset':0})
        response=self.client.post(f'/api/reading/{pdf}/progress',json={'copy_version':VERSION,'revision':0,'position':{'page':5,'page_count':5,'left':0,'top':-.2}})
        self.assertEqual(response.status_code,200,response.text)
        items=self.client.get('/api/home/continue').json()['items']
        self.assertEqual({item['id'] for item in items},{html,pdf})
        self.assertEqual(next(item['label'] for item in items if item['id']==html),'42% through')
        self.client.patch('/api/reading/'+pdf,json={'is_read':True})
        self.progress(html,revision=1,position={'fraction':1,'anchor':10,'offset':1})
        self.assertEqual(self.client.get('/api/home/continue').json()['items'],[])

    def test_archived_articles_are_not_suggested_for_continue_reading(self):
        article_id=self.article()
        self.progress(article_id,position={'fraction':.5,'anchor':3,'offset':0})
        self.client.patch('/api/reading/'+article_id,json={'archived':True})
        self.assertEqual(self.client.get('/api/home/continue').json()['items'],[])

    def container(self,error=None,update=True):
        value={'id':'container-1','name':'sonarr','watcher':'local','status':'running',
               'labels':{'com.docker.compose.project':'media','com.docker.compose.service':'sonarr'},
               'image':{'name':'linuxserver/sonarr','tag':{'value':'latest','semver':False},'digest':{'watch':True,'repo':'old'}},
               'result':{'tag':'latest','digest':'new'},'updateAvailable':update}
        if error:value['error']={'message':error}
        return normalize_container(value)

    def test_healthy_attention_summary_is_quiet(self):
        response=self.client.get('/api/home/attention')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['total'],0)
        self.assertEqual(response.headers['cache-control'],'no-store')

    def test_attention_keeps_outages_updates_and_failed_checks_distinct(self):
        self.readings=[{'id':'qbit','value':'https://qbit.example.com','state':'unreachable','stale':False,'status_code':None,'detail':'No connection'}]
        self.wud=ContainerSnapshot(configured=True,containers=[self.container()])
        result=self.client.get('/api/home/attention').json()
        self.assertEqual(result['counts']['service'],1)
        self.assertEqual(result['counts']['update'],1)
        self.assertEqual(result['counts']['check'],0)
        self.assertTrue(next(item for item in result['items'] if item['kind']=='update')['href'].startswith('/infrastructure?monitor='))
        self.wud=ContainerSnapshot(configured=True,containers=[self.container(error='private-canary')])
        result=self.client.get('/api/home/attention').json()
        self.assertEqual(result['counts']['update'],0)
        self.assertEqual(result['counts']['check'],1)
        self.assertNotIn('private-canary',str(result))

    def test_stale_checks_are_unknown_and_old_wud_updates_are_not_reported_as_current(self):
        self.readings=[{'id':'qbit','value':'https://qbit.example.com','state':'unreachable','stale':True,'status_code':None,'detail':'Old failure'}]
        self.wud=ContainerSnapshot(configured=True,containers=[self.container()],stale=True,error='WUD could not be reached.')
        result=self.client.get('/api/home/attention').json()
        self.assertEqual(result['counts']['service'],0)
        self.assertEqual(result['counts']['update'],0)
        self.assertEqual(result['counts']['check'],2)

    def test_pending_first_checks_do_not_show_a_false_healthy_summary(self):
        self.readings=[{'id':'qbit','value':'https://qbit.example.com','state':'unknown','stale':True,'status_code':None,'detail':'Waiting'}]
        result=self.client.get('/api/home/attention').json()
        self.assertEqual(result['total'],1)
        self.assertEqual(result['items'][0]['kind'],'check')

    def test_feed_and_copy_failures_have_useful_links_and_archived_copies_are_ignored(self):
        article_id=self.article()
        with main.database() as db:
            db.execute("UPDATE articles SET copy_status='failed',copy_error='Origin unavailable' WHERE id=?",(article_id,))
            db.execute('INSERT INTO feed_subscriptions(id,url,title,next_check_at,error) VALUES (?,?,?,?,?)',('local','https://example.com/feed','Local news',VERSION,'Feed unavailable'))
        result=self.client.get('/api/home/attention').json()
        self.assertEqual(result['counts']['feed'],1);self.assertEqual(result['counts']['copy'],1)
        self.assertEqual(next(item['href'] for item in result['items'] if item['kind']=='copy'),'/reading/'+article_id)
        self.assertIn('section=feeds',next(item['href'] for item in result['items'] if item['kind']=='feed'))
        self.client.patch('/api/reading/'+article_id,json={'archived':True})
        self.assertEqual(self.client.get('/api/home/attention').json()['counts']['copy'],0)


if __name__=='__main__':unittest.main()
