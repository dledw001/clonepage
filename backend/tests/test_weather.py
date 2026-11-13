import asyncio
import copy
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import home
import main
from weather import WeatherGateway, location_query, normalize


def payload(temp='10'):
    return {'nearest_area':[{'areaName':[{'value':'Shirlington'}], 'region':[{'value':'Virginia'}], 'country':[{'value':'United States of America'}]}],
            'current_condition':[{'temp_C':temp, 'temp_F':'50', 'FeelsLikeC':'7', 'FeelsLikeF':'44', 'weatherCode':'116',
                                  'weatherDesc':[{'value':'Partly Cloudy '}], 'windspeedKmph':'0', 'windspeedMiles':'0', 'winddir16Point':'N', 'humidity':'0'}],
            'weather':[{'date':'2026-11-13', 'maxtempC':'10', 'maxtempF':'50', 'mintempC':'4', 'mintempF':'40',
                        'hourly':[{'time':'0','weatherCode':'122','weatherDesc':[{'value':'Overcast'}],'chanceofrain':'0'},
                                  {'time':'1200','weatherCode':'116','weatherDesc':[{'value':'Partly cloudy'}],'chanceofrain':'65'}]}]}


class WeatherNormalizationTests(unittest.TestCase):
    def test_zero_values_and_peak_hour_probability_are_preserved(self):
        result=normalize(payload('0'))
        self.assertEqual(result['current']['temperature']['c'],0)
        self.assertEqual(result['current']['wind_mph'],0)
        self.assertEqual(result['current']['humidity'],0)
        self.assertEqual(result['forecast'][0]['rain_chance'],65)
        self.assertEqual(result['forecast'][0]['description'],'Partly cloudy')
        self.assertEqual(result['place'],'Shirlington, Virginia')

    def test_missing_optional_values_are_unknown_not_zero(self):
        result=normalize({'current_condition':[{'temp_C':'-5'}]})
        self.assertEqual(result['current']['temperature']['f'],23)
        self.assertIsNone(result['current']['feels_like']['c'])
        self.assertIsNone(result['current']['humidity'])
        self.assertIsNone(result['current']['wind_mph'])
        self.assertEqual(result['forecast'],[])

    def test_invalid_current_data_is_rejected_and_invalid_forecast_days_are_skipped(self):
        for data in [None,{}, {'current_condition':[]},{'current_condition':[{'temp_C':'NaN','temp_F':'Infinity'}]}]:
            with self.assertRaises(ValueError):normalize(data)
        data=payload();day=copy.deepcopy(data['weather'][0]);data['weather']=[{'date':'bad'},day,day,{'date':'2026-11-14','hourly':None}]
        self.assertEqual([day['date'] for day in normalize(data)['forecast']],['2026-11-13','2026-11-14'])

    def test_us_zip_codes_are_qualified_without_changing_explicit_cities(self):
        self.assertEqual(location_query('22206'),'22206, United States')
        self.assertEqual(location_query('22206-1234'),'22206-1234, United States')
        self.assertEqual(location_query('London, UK'),'London, UK')


class WeatherGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_requests_share_one_fixed_host_request_and_cache(self):
        calls=[];now=[0]
        async def handle(request):
            calls.append(request);await asyncio.sleep(.01)
            return httpx.Response(200,json=payload())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            gateway=WeatherGateway(client,lambda:now[0])
            results=await asyncio.gather(*(gateway.snapshot('22206') for _ in range(5)))
            self.assertEqual(len(calls),1)
            self.assertEqual(calls[0].url.host,'wttr.in')
            self.assertEqual(calls[0].url.path,'/22206, United States')
            self.assertEqual(calls[0].url.params['format'],'j1')
            self.assertTrue(all(not result['stale'] for result in results))
            now[0]=899;await gateway.snapshot('22206');self.assertEqual(len(calls),1)
            now[0]=900;await gateway.snapshot('22206');self.assertEqual(len(calls),2)

    async def test_failures_retain_previous_data_timestamp_and_back_off(self):
        now=[0];calls=[];fail=[False]
        def handle(request):
            calls.append(request)
            return httpx.Response(429 if fail[0] else 200,json={} if fail[0] else payload())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            gateway=WeatherGateway(client,lambda:now[0]);first=await gateway.snapshot('22206')
            now[0]=901;fail[0]=True;stale=await gateway.snapshot('22206')
            self.assertTrue(stale['stale']);self.assertIsNotNone(stale['error'])
            self.assertEqual(stale['fetched_at'],first['fetched_at'])
            self.assertEqual(stale['current'],first['current'])
            await gateway.snapshot('22206');self.assertEqual(len(calls),2)
            now[0]=962;fail[0]=False;self.assertFalse((await gateway.snapshot('22206'))['stale'])

    async def test_first_failure_does_not_invent_data_and_locations_do_not_mix(self):
        def handle(request):return httpx.Response(503) if 'Paris' in request.url.path else httpx.Response(200,json=payload())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            gateway=WeatherGateway(client);await gateway.snapshot('22206');result=await gateway.snapshot('Paris, France')
            self.assertIsNone(result['current']);self.assertIsNone(result['fetched_at']);self.assertEqual(result['location'],'Paris, France')
            self.assertTrue(result['stale']);self.assertIsNotNone(result['error'])

    async def test_bad_json_timeouts_and_oversized_responses_are_handled(self):
        for mode in ['json','timeout','size','redirect']:
            def handle(request):
                if mode=='timeout':raise httpx.ReadTimeout('timeout',request=request)
                if mode=='size':return httpx.Response(200,content=b'x'*(2*1024*1024+1))
                if mode=='redirect':return httpx.Response(302,headers={'Location':'http://localhost/'})
                return httpx.Response(200,text='<html>Unavailable</html>')
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle),follow_redirects=False) as client:
                result=await WeatherGateway(client).snapshot('22206')
                self.assertTrue(result['stale'],mode);self.assertIsNone(result['current'],mode)

    async def test_location_cache_is_bounded(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=payload()))) as client:
            gateway=WeatherGateway(client)
            for index in range(10):await gateway.snapshot('City '+str(index))
            self.assertEqual(len(gateway.cache),8)
            self.assertNotIn('City 0',gateway.cache)


class WeatherSettingsTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        folder=self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(main,'DB_PATH',Path(folder)/'reading.sqlite3'))
        self.client=self.stack.enter_context(TestClient(main.app))

    def test_existing_layout_migration_preserves_order_visibility_and_runs_once(self):
        snapshot=self.client.get('/api/home/layout').json();legacy=copy.deepcopy(snapshot['settings'])
        legacy.pop('weather');legacy['sections']=[section for section in legacy['sections'] if section['id']!='weather'];legacy['sections'].reverse()
        legacy['sections'][0]['visible']=False;legacy['previews']['reading']=10
        with main.database() as db:db.execute('UPDATE home_settings SET payload=?,revision=4 WHERE id=1',(json.dumps(legacy),))
        home.initialize_home();migrated=self.client.get('/api/home/layout').json()
        self.assertEqual(migrated['revision'],5)
        self.assertEqual(migrated['settings']['weather'],{'location':'22206','units':'F'})
        self.assertEqual([section for section in migrated['settings']['sections'] if section['id']!='weather'],legacy['sections'])
        self.assertEqual(migrated['settings']['previews']['reading'],10)
        home.initialize_home();self.assertEqual(self.client.get('/api/home/layout').json(),migrated)
        # Preserve weather preferences too if only its section needs restoring.
        partial=copy.deepcopy(migrated['settings']);partial['weather']={'location':'London, UK','units':'C'}
        partial['sections']=[section for section in partial['sections'] if section['id']!='weather']
        with main.database() as db:db.execute('UPDATE home_settings SET payload=?,revision=8 WHERE id=1',(json.dumps(partial),))
        home.initialize_home();restored=self.client.get('/api/home/layout').json()
        self.assertEqual(restored['revision'],9)
        self.assertEqual(restored['settings']['weather'],partial['weather'])
        self.assertTrue(any(section['id']=='weather' for section in restored['settings']['sections']))

    def test_weather_preferences_validate_and_persist_with_other_layout_choices(self):
        snapshot=self.client.get('/api/home/layout').json();snapshot['settings']['weather']={'location':' London, UK ','units':'C'}
        result=self.client.put('/api/home/layout',json=snapshot)
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['settings']['weather'],{'location':'London, UK','units':'C'})
        for value in [{'location':'','units':'F'},{'location':'http://localhost','units':'F'},{'location':'22206?format=3','units':'F'},{'location':'22206','units':'K'}]:
            bad=self.client.get('/api/home/layout').json();bad['settings']['weather']=value
            self.assertEqual(self.client.put('/api/home/layout',json=bad).status_code,422)

    def test_endpoint_uses_saved_location_and_units_and_does_not_cache_in_browser(self):
        snapshot=self.client.get('/api/home/layout').json();snapshot['settings']['weather']={'location':'London, UK','units':'C'}
        self.client.put('/api/home/layout',json=snapshot)
        async def weather_snapshot(location):return {'location':location,'current':None,'forecast':[],'stale':True}
        with patch.object(main.app.state.weather,'snapshot',side_effect=weather_snapshot) as gateway:
            result=self.client.get('/api/home/weather')
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['location'],'London, UK');self.assertEqual(result.json()['units'],'C')
        self.assertEqual(result.headers['Cache-Control'],'no-store');gateway.assert_called_once_with('London, UK')
