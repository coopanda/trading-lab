"""Trading Lab UI and public data extension. No broker or credentials."""
import csv
import datetime as dt
import io
import json
import math
import os
import re
import urllib.parse as up
from zoneinfo import ZoneInfo
from http.server import ThreadingHTTPServer
import app

CSP = "default-src 'self'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' https://cdn.jsdelivr.net https://huggingface.co https://*.huggingface.co https://*.hf.co https://*.xethub.hf.co; worker-src 'self' blob:; frame-ancestors 'self'; base-uri 'none'"

def normalize_intraday(payload, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Timestamp must include timezone')
    root = payload.get('chart', {})
    if root.get('error'):
        raise ValueError('Five-minute provider rejected this symbol')
    result = root.get('result') or []
    if not result:
        raise ValueError('No five-minute candles supplied')
    result = result[0]
    meta = result.get('meta', {})
    tzname = meta.get('exchangeTimezoneName', '')
    if tzname != 'America/New_York':
        raise ValueError('Intraday model currently supports US regular-session equities only')
    tz = ZoneInfo(tzname)
    quote = (result.get('indicators', {}).get('quote') or [{}])[0]
    stamps = result.get('timestamp') or []
    bars = []
    for i, stamp in enumerate(stamps):
        if not isinstance(stamp, (int, float)) or stamp + 300 > now.timestamp():
            continue
        local = dt.datetime.fromtimestamp(stamp, tz)
        if local.weekday() >= 5 or not (570 <= local.hour * 60 + local.minute < 960):
            continue
        values = [quote.get(k, [])[i] if i < len(quote.get(k, [])) else None for k in ('open', 'high', 'low', 'close')]
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in values):
            continue
        o, h, l, c = values
        if h < max(o, c) or l > min(o, c):
            continue
        if bars and stamp <= bars[-1]['t']:
            continue
        volumes = quote.get('volume', [])
        volume = volumes[i] if i < len(volumes) else 0
        bars.append({'t': stamp, 'date': local.isoformat(), 'session': local.date().isoformat(), 'o': o, 'h': h, 'l': l, 'c': c, 'v': volume or 0})
    if len(bars) < 80:
        raise ValueError('At least 80 valid completed five-minute candles are needed')
    last = bars[-1]
    regular = meta.get('currentTradingPeriod', {}).get('regular', {})
    start, end = regular.get('start'), regular.get('end')
    same_session = bool(start and end and start <= last['t'] < end and now.timestamp() < end)
    remaining = max(0, int((end - last['t'] - 300) // 300)) if same_session else 78
    mode = 'to-close' if same_session and remaining else 'next-session'
    if remaining == 0:
        remaining = 78
    return {'symbol': meta.get('symbol', ''), 'currency': meta.get('currency', 'USD'), 'bars': bars, 'interval': '5m', 'timezone': tzname, 'asOf': last['date'], 'fetchedAt': now.isoformat(), 'sessionClose': dt.datetime.fromtimestamp(end, tz).isoformat() if mode == 'to-close' else None, 'horizon': min(78, remaining), 'mode': mode, 'source': 'Yahoo Finance unofficial five-minute OHLC', 'note': 'Completed regular-session candles only. Quotes can be delayed or revised. No stale quote is called live. Closed market projects a standard next session; holidays/early-close schedules for future sessions are not modeled.'}

def intraday(symbol):
    symbol = app.symbol(symbol)
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/' + up.quote(symbol) + '?range=1mo&interval=5m&includePrePost=false'
    return normalize_intraday(json.loads(app.fetch(url)))

def stock_directory():
    """Official public Nasdaq symbol-directory files, not a holdings/ranking feed."""
    found = {}
    for file, symbol_key in (('nasdaqlisted.txt', 'Symbol'), ('otherlisted.txt', 'ACT Symbol')):
        raw = app.fetch('https://www.nasdaqtrader.com/dynamic/SymDir/' + file, 21600)
        if isinstance(raw, bytes):
            raw = raw.decode('utf-8-sig')
        rows = csv.DictReader(io.StringIO(raw), delimiter='|')
        for row in rows:
            symbol = (row.get(symbol_key) or '').strip()
            if not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,9}', symbol):
                continue
            if row.get('Test Issue') != 'N' or row.get('ETF') != 'N':
                continue
            name = (row.get('Security Name') or symbol).strip()
            found[symbol] = {'symbol': symbol, 'name': name}
    if len(found) < 100:
        raise ValueError('Public stock directory did not supply a usable listing set')
    return {'stocks': sorted(found.values(), key=lambda x: x['symbol']), 'source': 'Nasdaq Trader public symbol directory', 'note': 'US listed securities excluding ETFs and test issues; may include preferred shares, warrants or units. Six-hour provider cache. Not every listing has chart coverage.'}

def stock_chart(symbol, frame):
    """Price display only. Does not change the daily or five-minute paper models."""
    symbol = app.symbol(symbol)
    if frame not in ('all', '1d', '1h', 'live'):
        raise ValueError('Unknown chart timeframe')
    interval, period = ('1mo', 'max') if frame == 'all' else ('1m', '5d')
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/' + up.quote(symbol) + '?' + up.urlencode({'range': period, 'interval': interval, 'includePrePost': 'false'})
    root = json.loads(app.fetch(url, 60 if frame == 'live' else 300)).get('chart', {})
    if root.get('error') or not root.get('result'):
        raise ValueError('Public chart provider did not supply this timeframe')
    result = root['result'][0]
    meta = result.get('meta', {})
    tzname = meta.get('exchangeTimezoneName', 'America/New_York')
    tz = ZoneInfo(tzname)
    quote = (result.get('indicators', {}).get('quote') or [{}])[0]
    bars = []
    for i, stamp in enumerate(result.get('timestamp') or []):
        values = [quote.get(k, [])[i] if i < len(quote.get(k, [])) else None for k in ('open', 'high', 'low', 'close')]
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in values):
            continue
        o, h, l, c = values
        if h < max(o, c) or l > min(o, c):
            continue
        local = dt.datetime.fromtimestamp(stamp, tz)
        volumes = quote.get('volume', [])
        v = volumes[i] if i < len(volumes) else 0
        bars.append({'t': stamp, 'date': local.date().isoformat() if frame == 'all' else local.isoformat(), 'session': local.date().isoformat(), 'o': o, 'h': h, 'l': l, 'c': c, 'v': v or 0})
    if not bars:
        raise ValueError('No valid OHLC bars in supplied chart')
    if frame != 'all':
        latest_session = bars[-1]['session']
        bars = [b for b in bars if b['session'] == latest_session]
        if frame == '1h':
            cutoff = bars[-1]['t'] - 3600
            bars = [b for b in bars if b['t'] > cutoff]
    note = 'All available provider history, monthly OHLC candles; not every daily candle.' if frame == 'all' else 'Latest supplied regular session, one-minute candles. May be delayed or incomplete; most recent bar can still develop.'
    if frame == '1h':
        note += ' Most recent supplied hour, not necessarily the current clock hour.'
    if frame == 'live':
        note += ' Live mode polls every 60 seconds while visible; not an exchange-live streaming feed.'
    return {'symbol': meta.get('symbol', symbol), 'currency': meta.get('currency', 'USD'), 'bars': bars, 'interval': interval, 'timezone': tzname, 'source': 'Yahoo Finance unofficial chart OHLC', 'note': note, 'fetchedAt': dt.datetime.now(dt.timezone.utc).isoformat()}

class Handler(app.Handler):
    def send_header(self, name, value):
        if name.lower() == 'content-security-policy':
            value = CSP
        value = 'SAMEORIGIN' if name.lower() == 'x-frame-options' else value; super().send_header(name, value)

    def do_GET(self):
        u = up.urlparse(self.path)
        q = {k: v[0] for k, v in up.parse_qs(u.query).items()}
        if u.path == '/':
            data = (app.ROOT / 'index.html').read_text()
            scripts = '<script src="/beginner.js" defer></script><script src="/stocks.js" defer></script><script src="/settings.js" defer></script>'
            data = data.replace('</body>', scripts + '</body>')
            return self.send(data.encode(), content='text/html; charset=utf-8')
        if u.path in ('/beginner.js', '/stocks.js', '/settings.js'):
            return self.send((app.ROOT / 'lab' / u.path.lstrip('/')).read_bytes(), content='text/javascript; charset=utf-8')
        if u.path in ('/api/intraday', '/api/stocks', '/api/chart'):
            if not app.GATE.acquire(blocking=False):
                return self.send({'error': 'Data queue busy; retry shortly'}, 429)
            try:
                if u.path == '/api/stocks':
                    return self.send(stock_directory())
                if u.path == '/api/chart':
                    return self.send(stock_chart(q.get('s', 'AAPL'), q.get('frame', 'all')))
                return self.send(intraday(q.get('s', 'TSLA')))
            except Exception as e:
                return self.send({'error': str(e)[:300], 'mode': 'paper-only', 'fabricated': False}, 400)
            finally:
                app.GATE.release()
        if u.path == '/health':
            return self.send({'status': 'ok', 'app': 'Trading Lab', 'mode': 'paper-only', 'engines': len(app.SKILLS), 'historicalNews': bool(os.getenv('ALPHAVANTAGE_API_KEY')), 'version': 'stocks-news-settings-v2', 'intraday': '5m', 'paperExecution': 'local-simulated-next-open', 'ai': 'browser-finbert', 'ui': 'stocks-news-time-machine', 'commit': os.getenv('RENDER_GIT_COMMIT', '')})
        return super().do_GET()

if __name__ == '__main__':
    port = int(os.getenv('PORT', '10000'))
    print('Trading Lab Stocks / News / Time Machine paper-only server on', port, flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()
