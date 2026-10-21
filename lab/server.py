"""Trading Lab isolated five-minute data extension. No broker or credentials."""
import datetime as dt
import json
import math
import os
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
            data = data.replace('</body>', '<script src="/beginner.js" defer></script></body>')
            return self.send(data.encode(), content='text/html; charset=utf-8')
        if u.path == '/beginner.js':
            return self.send((app.ROOT / 'lab' / 'beginner.js').read_bytes(), content='text/javascript; charset=utf-8')
        if u.path == '/api/intraday':
            if not app.GATE.acquire(blocking=False):
                return self.send({'error': 'Data queue busy; retry shortly'}, 429)
            try:
                return self.send(intraday(q.get('s', 'TSLA')))
            except Exception as e:
                return self.send({'error': str(e)[:300], 'mode': 'paper-only', 'fabricated': False}, 400)
            finally:
                app.GATE.release()
        if u.path == '/health':
            return self.send({'status': 'ok', 'app': 'Trading Lab', 'mode': 'paper-only', 'engines': len(app.SKILLS), 'historicalNews': bool(os.getenv('ALPHAVANTAGE_API_KEY')), 'version': 'beginner-intraday-v1', 'intraday': '5m', 'paperExecution': 'local-simulated-next-open', 'ai': 'browser-finbert'})
        return super().do_GET()

if __name__ == '__main__':
    port = int(os.getenv('PORT', '10000'))
    print('Trading Lab beginner five-minute paper-only server on', port, flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()
