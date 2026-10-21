#!/usr/bin/env python3
"""Trading Lab: independent, public-data research terminal. No real orders.
MIT; upstream trader-skill engines remain unchanged in their original directories.
Run: python app.py   Tests: python -m unittest web_test
"""
import csv
import datetime as dt
import io
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse as up
import urllib.request as ur
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SKILLS = ['copycat','news','budget','pelosi','analyst','screener','risk','broker','tax','alerts','rebalance']
CACHE = {}
LOCK = threading.Lock()
GATE = threading.BoundedSemaphore(6)
UA = os.getenv('SEC_USER_AGENT', 'TradingLab/1.0 (public research; github.com/coopanda/trading-lab)')


def fetch(url, ttl=300, binary=False):
    with LOCK:
        hit = CACHE.get(url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    req = ur.Request(url, headers={'User-Agent': UA if 'sec.gov' in url else 'Mozilla/5.0 TradingLab/1.0', 'Accept': '*/*'})
    with ur.urlopen(req, timeout=18) as r:
        raw = r.read(12000001)
    if len(raw) > 12000000:
        raise ValueError('Provider response too large')
    val = raw if binary else raw.decode('utf-8-sig')
    with LOCK:
        if len(CACHE) > 300:
            CACHE.clear()
        CACHE[url] = (time.time(), val)
    return val


def jfetch(url, ttl=300):
    return json.loads(fetch(url, ttl))


def symbol(value):
    s = value.strip().upper()
    if not re.fullmatch(r'[A-Z0-9^][A-Z0-9.\-^]{0,14}', s):
        raise ValueError('Use a ticker such as AAPL, BRK-B, or ^GSPC')
    return s


def day(value):
    return dt.date.fromisoformat(value)


def prices(s, start, end):
    s = symbol(s)
    a, b = day(start), day(end)
    if a >= b or (b-a).days > 15000:
        raise ValueError('Select an increasing date range of at most 40 years')
    def epoch(d):
        return int(dt.datetime.combine(d, dt.time(), dt.timezone.utc).timestamp())
    why = []
    for host in ['query1.finance.yahoo.com', 'query2.finance.yahoo.com']:
        try:
            url = 'https://' + host + '/v8/finance/chart/' + up.quote(s) + '?' + up.urlencode({'period1':epoch(a), 'period2':epoch(b+dt.timedelta(days=1)), 'interval':'1d', 'events':'splits,div'})
            result = jfetch(url)['chart']['result'][0]
            q = result['indicators']['quote'][0]
            rows = []
            for i,t in enumerate(result.get('timestamp', [])):
                vals = [q[k][i] for k in ['open','high','low','close']]
                if any(v is None or not math.isfinite(v) or v <= 0 for v in vals):
                    continue
                rows.append(dict(t=t, date=dt.datetime.fromtimestamp(t, dt.timezone.utc).date().isoformat(), o=vals[0],h=vals[1],l=vals[2],c=vals[3],v=(q.get('volume') or [0]*len(result['timestamp']))[i] or 0))
            if rows:
                return {'symbol':s,'bars':rows,'source':'Yahoo Finance unofficial daily OHLC','currency':result.get('meta',{}).get('currency','unknown'),'timezone':result.get('meta',{}).get('exchangeTimezoneName','unknown'),'note':'Daily bars, not streaming quotes. Provider may delay, revise, or adjust historical prices. No dividend return, commissions or slippage modeled.'}
        except Exception as e:
            why.append(type(e).__name__)
    try:
        if s.startswith('^'):
            raise ValueError('No Stooq index fallback')
        url = 'https://stooq.com/q/d/l/?' + up.urlencode({'s':s.lower()+'.us','i':'d','d1':a.strftime('%Y%m%d'),'d2':b.strftime('%Y%m%d')})
        rows = []
        for r in csv.DictReader(io.StringIO(fetch(url))):
            rows.append({'date':r['Date'],'t':epoch(day(r['Date'])),'o':float(r['Open']),'h':float(r['High']),'l':float(r['Low']),'c':float(r['Close']),'v':float(r.get('Volume') or 0)})
        if rows:
            return {'symbol':s,'bars':rows,'source':'Stooq daily OHLC fallback','currency':'USD (US-symbol feed)','timezone':'Date-only bars; no intraday timestamps','note':'Yahoo unavailable; Stooq daily history used. Adjustment methodology may differ. Not live or investment advice.'}
    except Exception as e:
        why.append(type(e).__name__)
    raise ValueError('Price providers returned no usable bars for this symbol/range ('+', '.join(why)+'). Try another ticker or range; no synthetic prices are substituted.')


def cik_for(s):
    s = symbol(s)
    data = jfetch('https://www.sec.gov/files/company_tickers.json', 86400)
    for row in data.values():
        if row['ticker'].upper().replace('.', '-') == s.replace('.', '-'):
            return str(row['cik_str']).zfill(10)
    raise ValueError('Ticker not in SEC company index; filings/financials only cover SEC-reporting issuers')


def submissions(cik):
    if not re.fullmatch(r'\d{1,10}', cik):
        raise ValueError('Invalid CIK')
    return jfetch('https://data.sec.gov/submissions/CIK'+cik.zfill(10)+'.json', 3600)


def filing_rows(data):
    out = []
    for i,form in enumerate(data.get('form', [])):
        out.append({k: data.get(k, ['']*len(data['form']))[i] for k in ['form','filingDate','accessionNumber','primaryDocument','acceptanceDateTime']})
    return out


def timeline(s, start, end):
    a,b = day(start),day(end)
    events, notes = [],[]
    try:
        cik = cik_for(s)
        sub = submissions(cik)
        fs = filing_rows(sub['filings']['recent'])
        old = [f for f in sub['filings'].get('files', []) if f['filingFrom'] <= end and f['filingTo'] >= start]
        for f in old[:4]:
            fs += filing_rows(jfetch('https://data.sec.gov/submissions/'+f['name'],86400))
        if len(old)>4:
            notes.append('SEC historical pagination capped at four archives; this timeline is incomplete.')
        for f in fs:
            if f['form'] not in ['8-K','8-K/A'] or not start <= f['filingDate'] <= end:
                continue
            stamp = f.get('acceptanceDateTime')
            if not stamp:
                stamp = (day(f['filingDate'])+dt.timedelta(days=1)).isoformat()+'T00:00:00Z'
            acc = f['accessionNumber'].replace('-','')
            events.append({'time':stamp,'date':stamp[:10],'type':'SEC 8-K','title':s+' '+f['form']+' filed '+f['filingDate'],'url':'https://www.sec.gov/Archives/edgar/data/'+str(int(cik))+'/'+acc+'/'+f['primaryDocument']})
        notes.append('SEC events use acceptance timestamps when supplied; date-only filings are revealed on the following date to reduce look-ahead. Daily replay cannot model exact intraday arrival.')
    except Exception as e:
        notes.append('SEC filings unavailable: '+str(e)[:180]+'. Set SEC_USER_AGENT to an identifying app/contact user-agent if SEC blocks requests.')
    key = os.getenv('ALPHAVANTAGE_API_KEY')
    try:
        if key:
            url = 'https://www.alphavantage.co/query?'+up.urlencode({'function':'NEWS_SENTIMENT','tickers':s,'time_from':a.strftime('%Y%m%d')+'T0000','time_to':(b+dt.timedelta(days=1)).strftime('%Y%m%d')+'T0000','limit':1000,'apikey':key})
            feed = jfetch(url).get('feed', [])
            for item in feed:
                t = dt.datetime.strptime(item['time_published'],'%Y%m%dT%H%M%S').replace(tzinfo=dt.timezone.utc).isoformat()
                events.append({'time':t,'date':t[:10],'type':'News','title':item['title'],'url':item['url']})
            notes.append('Alpha Vantage historical headlines; provider limits/coverage apply (maximum 1000 records).')
        else:
            xml = ET.fromstring(fetch('https://feeds.finance.yahoo.com/rss/2.0/headline?s='+up.quote(s)+'&region=US&lang=en-US'))
            from email.utils import parsedate_to_datetime
            for item in xml.findall('.//item'):
                stamp = parsedate_to_datetime(item.findtext('pubDate')).astimezone(dt.timezone.utc).isoformat()
                if start <= stamp[:10] <= end:
                    events.append({'time':stamp,'date':stamp[:10],'type':'News','title':item.findtext('title'),'url':item.findtext('link')})
            notes.append('Recent Yahoo RSS only: NOT a complete historical news archive. Older ranges may have no headlines. Add ALPHAVANTAGE_API_KEY for provider-supported history or import timestamped headlines in the replay.')
    except Exception as e:
        notes.append('News source unavailable: '+str(e)[:120]+'. Headlines are not fabricated.')
    events = list({(e['type'],e['url']):e for e in events}.values())
    return {'events':sorted(events,key=lambda e:e['time']), 'notes':notes}


def earnings(s):
    cik = cik_for(s)
    data = jfetch('https://data.sec.gov/api/xbrl/companyfacts/CIK'+cik+'.json',3600)
    facts = data['facts'].get('us-gaap',{})
    groups = {'Revenue':['RevenueFromContractWithCustomerExcludingAssessedTax','Revenues','SalesRevenueNet'], 'Net income':['NetIncomeLoss','ProfitLoss'], 'EPS diluted':['EarningsPerShareDiluted'], 'Operating income':['OperatingIncomeLoss']}
    result = {}
    for label,tags in groups.items():
        for tag in tags:
            fact = facts.get(tag)
            if not fact:
                continue
            units = fact.get('units',{})
            unit = 'USD/shares' if 'EPS' in label else 'USD'
            records = units.get(unit, [])
            records = [r for r in records if r.get('form') in ['10-K','10-Q']]
            if records:
                seen=set(); out=[]
                for r in sorted(records,key=lambda r:(r.get('end',''),r.get('filed','')),reverse=True):
                    k=(r.get('start'),r.get('end'))
                    if k in seen:
                        continue
                    seen.add(k)
                    out.append({'value':r['val'],'unit':unit,'start':r.get('start'),'end':r.get('end'),'filed':r.get('filed'),'form':r.get('form')})
                result[label]=out[:12]
                break
    if not result:
        raise ValueError('No supported US-GAAP facts found; IFRS issuers may not be covered')
    return {'name':data.get('entityName',s),'metrics':result,'source':'https://www.sec.gov/edgar/browse/?CIK='+cik,'note':'Latest SEC XBRL disclosures, not point-in-time replay data. Annual, quarterly and YTD durations are labeled; compare like periods only.'}


def whales(cik):
    sub = submissions(cik)
    fs = [f for f in filing_rows(sub['filings']['recent']) if f['form']=='13F-HR']
    if not fs:
        raise ValueError('No recent 13F-HR filing found')
    f=fs[0]; base='https://www.sec.gov/Archives/edgar/data/'+str(int(cik))+'/'+f['accessionNumber'].replace('-','')+'/'
    index=jfetch(base+'index.json',3600)
    docs=[i['name'] for i in index['directory']['item'] if i['name'].endswith('.xml')]
    holdings=[]
    for name in docs[:8]:
        tree=ET.fromstring(fetch(base+name,3600))
        for node in tree.iter():
            if node.tag.split('}')[-1]!='infoTable':
                continue
            vals={n.tag.split('}')[-1]:n.text for n in node.iter()}
            holdings.append({'name':vals.get('nameOfIssuer'),'cusip':vals.get('cusip'),'class':vals.get('titleOfClass'),'reportedValue':float(vals.get('value') or 0),'shares':vals.get('sshPrnamt'),'putCall':vals.get('putCall','')})
    if not holdings:
        raise ValueError('13F found but information table could not be parsed; open original filing')
    holdings.sort(key=lambda r:r['reportedValue'],reverse=True)
    return {'name':sub.get('name'),'filed':f['filingDate'],'holdings':holdings,'source':base+f['primaryDocument'],'note':'Delayed, partial long US holdings; not a live portfolio or buy recommendation. Value is the filing-reported unit (new filings generally USD; older filings may be thousands). Options and confidential omissions affect interpretation.'}


def congress(year, name):
    y=int(year)
    if not 2012 <= y <= dt.date.today().year:
        raise ValueError('House disclosure year must be between 2012 and the current year')
    raw=fetch('https://disclosures-clerk.house.gov/public_disc/financial-pdfs/'+str(y)+'FD.zip',3600,True)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names=[n for n in z.namelist() if n.lower().endswith('.xml')]
        if not names or z.getinfo(names[0]).file_size > 20000000:
            raise ValueError('House archive XML unavailable')
        tree=ET.fromstring(z.read(names[0]))
    rows=[]
    for member in tree:
        r={n.tag.split('}')[-1]:n.text or '' for n in member}
        person=(r.get('First','')+' '+r.get('Last','')).strip()
        if r.get('FilingType')!='P' or name.lower() not in person.lower():
            continue
        doc=r.get('DocID','')
        rows.append({'name':person,'date':r.get('FilingDate'),'state':r.get('StateDst'),'url':'https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/'+str(y)+'/'+doc+'.pdf','id':doc})
    return {'disclosures':rows[-200:][::-1],'source':'https://disclosures-clerk.house.gov/','note':'Official House periodic transaction report links, not real-time trades. PDFs contain transaction dates, assets and broad value ranges. No claim that a disclosed trade remains held. Senate reports are not included.'}


def engine_list():
    out=[]
    for skill in SKILLS:
        folder=ROOT/('trader-'+skill)
        scripts=[]
        for p in sorted(folder.glob('*.py')):
            if '__main__' in p.read_text(errors='replace'):
                scripts.append(p.name)
        docs=next(iter(folder.glob('README*')),None)
        out.append({'id':skill,'scripts':scripts,'docs':docs.read_text(errors='replace')[:18000] if docs else 'See upstream directory documentation.'})
    return out


def engine_run(skill, script, args):
    if skill not in SKILLS or not re.fullmatch(r'[\w\-]+\.py',script):
        raise ValueError('Invalid engine')
    p=ROOT/('trader-'+skill)/script
    if not p.is_file() or p.is_symlink():
        raise ValueError('Unknown engine entry point')
    if len(args)>20 or any(not isinstance(a,str) or len(a)>100 for a in args):
        raise ValueError('Invalid arguments')
    allowed={'holdings','latest','managers','search','filings','fetch','quote','screen','scan','analyze','summary','status','check','list','report','session','size','simulate','draft','--help','-h'}
    if not args or args[0] not in allowed:
        raise ValueError('Only read-only, calculation, draft and paper-simulation commands are exposed. Use --help and the source docs.')
    forbidden={'--live','--execute','--approve','--confirm','--account','--config','--output','--out','--file','--state','--ledger','--token','--api-key','--write','--save','--notify','--webhook'}
    for a in args:
        if a.split('=')[0] in forbidden or '/' in a or '\\' in a or '..' in a:
            raise ValueError('Live actions, credentials, file paths and writes are disabled in the public engine console')
    env={'PATH':os.getenv('PATH',''),'PYTHONIOENCODING':'utf-8','PYTHONDONTWRITEBYTECODE':'1','SEC_USER_AGENT':UA,'HOME':'/tmp'}
    with tempfile.TemporaryDirectory(prefix='trading-lab-') as tmp:
        r=subprocess.run([sys.executable,str(p)]+args,cwd=tmp,env=env,capture_output=True,text=True,timeout=25)
    return {'exitCode':r.returncode,'output':(r.stdout+'\n'+r.stderr)[:65000],'mode':'Sandboxed upstream CLI; no credentials, no live execution, temporary state only.'}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Never log query parameters (which could contain private research).
        print(self.command, self.path.split('?')[0], flush=True)

    def send(self, val, code=200, content='application/json; charset=utf-8'):
        data=val if isinstance(val,bytes) else json.dumps(val,allow_nan=False).encode()
        self.send_response(code)
        self.send_header('Content-Type',content)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        u=up.urlparse(self.path); q={k:v[0] for k,v in up.parse_qs(u.query).items()}
        if u.path=='/':
            return self.send((ROOT/'index.html').read_bytes(),content='text/html; charset=utf-8')
        if u.path=='/favicon.ico':
            return self.send(b'',204)
        if u.path=='/health':
            return self.send({'status':'ok','app':'Trading Lab','mode':'paper-only','engines':len(SKILLS),'historicalNews':bool(os.getenv('ALPHAVANTAGE_API_KEY'))})
        if not u.path.startswith('/api/'):
            return self.send({'error':'Not found'},404)
        if not GATE.acquire(blocking=False):
            return self.send({'error':'Research queue busy; retry shortly'},429)
        try:
            s=q.get('s','AAPL')
            if u.path=='/api/prices':
                val=prices(s,q['start'],q['end'])
            elif u.path=='/api/timeline':
                val=timeline(symbol(s),q['start'],q['end'])
            elif u.path=='/api/earnings':
                val=earnings(symbol(s))
            elif u.path=='/api/whales':
                val=whales(q.get('cik','1067983'))
            elif u.path=='/api/congress':
                val=congress(q.get('year',str(dt.date.today().year)),q.get('name','Pelosi'))
            elif u.path=='/api/engines':
                val={'engines':engine_list()}
            elif u.path=='/api/engine':
                val=engine_run(q['skill'],q['script'],json.loads(q.get('args','["--help"]')))
            elif u.path=='/api/screen':
                tickers=list(dict.fromkeys(symbol(v) for v in q.get('symbols','AAPL,MSFT,NVDA,SPY').split(',')))
                if len(tickers)>8:
                    raise ValueError('Screen at most 8 tickers per request')
                now=dt.date.today()
                def one(t):
                    try:
                        return prices(t,(now-dt.timedelta(days=400)).isoformat(),now.isoformat())
                    except Exception as e:
                        return {'symbol':t,'error':str(e)}
                with ThreadPoolExecutor(max_workers=2) as pool:
                    val={'results':list(pool.map(one,tickers))}
            else:
                return self.send({'error':'Not found'},404)
            self.send(val)
        except subprocess.TimeoutExpired:
            self.send({'error':'Upstream engine exceeded 25 seconds; no action was executed.'},504)
        except Exception as e:
            self.send({'error':str(e)[:500] or type(e).__name__},400)
        finally:
            GATE.release()


if __name__=='__main__':
    port=int(os.getenv('PORT','10000'))
    print('Trading Lab paper-only server on',port,flush=True)
    ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()
