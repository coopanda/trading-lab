"""Deployment gate: syntax, document structure, model and paper execution checks."""
import datetime as dt
import json
import pathlib
import re
import subprocess
import unittest
from lab.server import normalize_intraday, CSP

ROOT = pathlib.Path(__file__).resolve().parents[1]

class IntradayDataTests(unittest.TestCase):
    def fixture(self):
        start = int(dt.datetime(2026, 10, 19, 13, 30, tzinfo=dt.timezone.utc).timestamp())
        stamps = [start + d * 86400 + i * 300 for d in range(2) for i in range(78)]
        quote = {'open': [100.] * 156, 'high': [102.] * 156, 'low': [99.] * 156, 'close': [101.] * 156, 'volume': [1000] * 156}
        end = start + 86400 + 23400
        payload = {'chart': {'result': [{'meta': {'symbol': 'TSLA', 'currency': 'USD', 'exchangeTimezoneName': 'America/New_York', 'currentTradingPeriod': {'regular': {'start': start + 86400, 'end': end}}}, 'timestamp': stamps, 'indicators': {'quote': [quote]}}]}}
        return payload, dt.datetime.fromtimestamp(end + 3600, dt.timezone.utc)

    def test_completed_candles(self):
        p, now = self.fixture()
        d = normalize_intraday(p, now)
        self.assertEqual(len(d['bars']), 156)
        self.assertEqual(d['interval'], '5m')
        self.assertEqual(d['timezone'], 'America/New_York')

    def test_market_closed_next_session(self):
        p, now = self.fixture()
        d = normalize_intraday(p, now)
        self.assertEqual(d['mode'], 'next-session')
        self.assertEqual(d['horizon'], 78)
        self.assertIsNone(d['sessionClose'])

    def test_open_session_remaining_count(self):
        p, _ = self.fixture()
        start = p['chart']['result'][0]['meta']['currentTradingPeriod']['regular']['start']
        now = dt.datetime.fromtimestamp(start + 3000, dt.timezone.utc)
        d = normalize_intraday(p, now)
        self.assertEqual(d['mode'], 'to-close')
        self.assertEqual(d['horizon'], 68)
        self.assertEqual(len(d['bars']), 88)

    def test_incomplete_candle_hidden(self):
        p, _ = self.fixture()
        start = p['chart']['result'][0]['meta']['currentTradingPeriod']['regular']['start']
        now = dt.datetime.fromtimestamp(start + 3001, dt.timezone.utc)
        d = normalize_intraday(p, now)
        self.assertLessEqual(d['bars'][-1]['t'] + 300, now.timestamp())

    def test_bad_candle_filtered(self):
        p, now = self.fixture()
        p['chart']['result'][0]['indicators']['quote'][0]['low'][0] = 999
        self.assertEqual(len(normalize_intraday(p, now)['bars']), 155)

    def test_missing_candle_filtered(self):
        p, now = self.fixture()
        p['chart']['result'][0]['indicators']['quote'][0]['close'][0] = None
        self.assertEqual(len(normalize_intraday(p, now)['bars']), 155)

    def test_no_fake_provider_fallback(self):
        with self.assertRaises(ValueError):
            normalize_intraday({'chart': {'error': {'description': 'No data'}}})

    def test_no_timezone_guess(self):
        p, now = self.fixture()
        p['chart']['result'][0]['meta']['exchangeTimezoneName'] = 'Europe/London'
        with self.assertRaises(ValueError):
            normalize_intraday(p, now)

    def test_minimum_history(self):
        p, now = self.fixture()
        p['chart']['result'][0]['timestamp'] = [1]
        with self.assertRaises(ValueError):
            normalize_intraday(p, now)

    def test_csp_enables_real_model(self):
        self.assertIn('https://cdn.jsdelivr.net', CSP)
        self.assertIn('https://huggingface.co', CSP)
        self.assertIn('wasm-unsafe-eval', CSP)
        self.assertIn("frame-ancestors 'self'", CSP)


def verify_js():
    html = (ROOT / 'index.html').read_text()
    for tag in ('html', 'body'):
        assert len(re.findall(r'<' + tag + r'(?:\s[^>]*)?>', html, re.I)) == 1, 'Duplicate ' + tag
        assert html.lower().count('</' + tag + '>') == 1, 'Duplicate closing ' + tag
    assert html.rstrip().endswith('</script></body></html>'), 'Unexpected trailing document'
    js = r'''
const fs=require('fs'),vm=require('vm');
const html=fs.readFileSync('index.html','utf8');
const scripts=[...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/gi)].map(m=>m[1]);
for(let i=0;i<scripts.length;i++)new vm.Script(scripts[i],{filename:'index-inline-'+i});
new vm.Script(fs.readFileSync('lab/beginner.js','utf8'),{filename:'beginner.js'});
console.log('JavaScript syntax: all inline scripts and beginner.js OK');
const base=scripts.find(s=>s.includes('function labRng'));
const helper='function labRng'+base.split('function labRng')[1].split('function labExit')[0];
const context=vm.createContext({console});vm.runInContext(helper,context);
vm.runInContext(fs.readFileSync('lab/beginner.js','utf8'),context);
const out=vm.runInContext('intraTests()',context);console.log(out.join('\n'));console.log(out.length+' / '+out.length+' intraday/paper model tests passed');
'''
    subprocess.run(['node', '-e', js], cwd=ROOT, check=True)
    print('Single HTML/body and final closing structure OK', flush=True)

if __name__ == '__main__':
    verify_js()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(IntradayDataTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
