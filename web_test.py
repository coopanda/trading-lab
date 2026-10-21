"""Offline integration tests for the independent web application."""
import json
import re
import threading
import unittest
import urllib.request
from unittest.mock import patch
import app


class WebTests(unittest.TestCase):
    def test_symbols(self):
        self.assertEqual(app.symbol(' brk-b '),'BRK-B')
        self.assertEqual(app.symbol('^gspc'),'^GSPC')
        for bad in ['../secret','https://x','AAPL;whoami','', 'A'*30]:
            with self.assertRaises(ValueError):
                app.symbol(bad)

    def test_dates(self):
        self.assertEqual(str(app.day('2024-02-29')), '2024-02-29')
        with self.assertRaises(ValueError):
            app.day('2023-02-29')
        with self.assertRaises(ValueError):
            app.prices('AAPL','2024-06-01','2024-01-01')

    def test_eleven_original_engines(self):
        engines=app.engine_list()
        self.assertEqual(len(engines),11)
        self.assertTrue(all((app.ROOT/('trader-'+e['id'])).is_dir() for e in engines))
        self.assertTrue(all(e['scripts'] for e in engines), 'Every engine must expose an entry point')

    def test_live_commands_blocked(self):
        for args in [['execute'],['simulate','--live'],['draft','--config','/tmp/a'],['check','../state'],['report','--api-key','secret']]:
            e=app.engine_list()[0]
            with self.assertRaises(ValueError):
                app.engine_run(e['id'],e['scripts'][0],args)

    def test_unknown_entrypoints_blocked(self):
        with self.assertRaises(ValueError):
            app.engine_run('budget','../../app.py',['--help'])
        with self.assertRaises(ValueError):
            app.engine_run('not-a-skill','app.py',['--help'])

    def test_price_data_no_fake_fallback(self):
        with patch('app.jfetch',side_effect=ValueError('blocked')), patch('app.fetch',side_effect=ValueError('blocked')):
            with self.assertRaisesRegex(ValueError,'no usable bars'):
                app.prices('AAPL','2024-01-01','2024-02-01')

    def test_yahoo_parse_and_null_filter(self):
        data={'chart':{'result':[{'timestamp':[1704205800,1704292200],'meta':{'currency':'USD','exchangeTimezoneName':'America/New_York'},'indicators':{'quote':[{'open':[100,None],'high':[105,None],'low':[98,None],'close':[102,None],'volume':[1000,None]}]}}]}}
        with patch('app.jfetch',return_value=data):
            out=app.prices('AAPL','2024-01-01','2024-02-01')
        self.assertEqual(len(out['bars']),1)
        self.assertEqual(out['bars'][0]['c'],102)
        self.assertEqual(out['currency'],'USD')

    def test_filing_rows(self):
        rows=app.filing_rows({'form':['8-K'],'filingDate':['2024-01-02'],'accessionNumber':['0001-24-0002'],'primaryDocument':['report.htm']})
        self.assertEqual(rows[0]['form'],'8-K')
        self.assertEqual(rows[0]['acceptanceDateTime'],'')

    def test_markup_referenced_ids_exist(self):
        html=(app.ROOT/'index.html').read_text()
        ids=set(re.findall(r'\bid="([^"]+)"',html))
        refs=set(re.findall(r"\$\('([^']+)'\)",html))
        self.assertEqual(refs-ids,set())
        self.assertIn('replayCanvas',ids)
        self.assertIn('testOutput',ids)

    def test_http_health_and_root(self):
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        try:
            base='http://127.0.0.1:'+str(server.server_port)
            with urllib.request.urlopen(base+'/health') as r:
                data=json.load(r)
                self.assertEqual(data['status'],'ok')
                self.assertEqual(data['engines'],11)
                self.assertEqual(data['mode'],'paper-only')
            with urllib.request.urlopen(base+'/') as r:
                self.assertIn(b'Time Machine',r.read())
                self.assertEqual(r.headers['X-Frame-Options'],'DENY')
        finally:
            server.shutdown();server.server_close();t.join()


if __name__=='__main__':
    unittest.main()
