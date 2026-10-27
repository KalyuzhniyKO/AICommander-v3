"""Bounded public documentation GETs. No credentials, sessions or browser access."""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urljoin,urlsplit
import urllib.error
import urllib.request

CONFIG = Path(__file__).resolve().parents[3]/'config'/'chatgpt-web-sources.json'
MAX_DOCUMENT = 1024*1024


class SourceError(Exception):
    pass


def allowed(url):
    try:
        if not isinstance(url,str) or len(url.encode('utf-8'))>2048 or any(ord(c)<33 or ord(c)==127 for c in url):return False
        parsed=urlsplit(url)
        paths={'help.openai.com':'/en/articles/','learn.chatgpt.com':'/docs/','developers.openai.com':('/api/docs/','/codex/')}
        return (parsed.scheme=='https' and parsed.hostname in paths and parsed.port in (None,443)
                and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
                and parsed.path.startswith(paths[parsed.hostname]))
    except (ValueError,TypeError,UnicodeError):return False


def config():
    try:
        raw=CONFIG.read_bytes()
        if len(raw)>16384:raise ValueError
        value=json.loads(raw)
    except (OSError,ValueError,UnicodeError):raise SourceError('source_config_invalid') from None
    if not isinstance(value,dict) or set(value)!={'product','source_url','chat_url','cache_seconds','chatgpt_web_model_policy','preferred_effort'}:raise SourceError('source_config_invalid')
    if value.get('product')!='Chat' or not allowed(value.get('source_url','')) or value.get('chat_url')!='https://chatgpt.com/' or value.get('cache_seconds')!=86400 or value.get('chatgpt_web_model_policy')!='latest_available' or value.get('preferred_effort')!='High':
        raise SourceError('source_config_invalid')
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def fetch_public(url):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    for _ in range(4):
        if not allowed(url):raise SourceError('source_url_not_allowed')
        try:
            request=urllib.request.Request(url,headers={'Accept':'text/html','User-Agent':'AICommander-Public-Docs/1.0'},method='GET')
            with opener.open(request,timeout=5) as response:
                raw=response.read(MAX_DOCUMENT+1)
            if len(raw)>MAX_DOCUMENT:raise SourceError('source_too_large')
            return raw,url
        except urllib.error.HTTPError as error:
            status,location=error.code,error.headers.get('Location') if error.headers else None
            error.close()
            if status in {301,302,303,307,308} and location:
                url=urljoin(url,location)
                continue
            raise SourceError('source_unavailable') from None
        except (OSError,ValueError):raise SourceError('source_unavailable') from None
    raise SourceError('source_redirect_limit')


class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.headings=[];self.rows=[];self.heading=None;self.row=None;self.cell=None;self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in {'script','style'}:self.skip+=1
        if self.skip:return
        if tag=='h1':self.heading=[]
        elif tag=='tr':self.row=[]
        elif tag in {'td','th'} and self.row is not None:self.cell=[]
    def handle_endtag(self,tag):
        if tag in {'script','style'}:
            self.skip=max(0,self.skip-1);return
        if self.skip:return
        if tag=='h1' and self.heading is not None:
            self.headings.append(' '.join(' '.join(self.heading).split()));self.heading=None
        elif tag in {'td','th'} and self.cell is not None:
            self.row.append(' '.join(' '.join(self.cell).split()));self.cell=None
        elif tag=='tr' and self.row is not None:
            self.rows.append(self.row);self.row=None
    def handle_data(self,value):
        if self.skip:return
        if self.heading is not None:self.heading.append(value)
        if self.cell is not None:self.cell.append(value)


def parse_public(raw,url):
    if not allowed(url) or len(raw)>MAX_DOCUMENT:raise SourceError('source_invalid')
    document=Document()
    try:document.feed(raw.decode('utf-8'))
    except (ValueError,UnicodeError):raise SourceError('source_format_unknown') from None
    families=set()
    for title in document.headings:
        title=title.replace('\u2011','-').replace('\u2010','-')
        match=re.fullmatch(r'(GPT-[0-9]+(?:\.[0-9]+)*) (?:and other models in ChatGPT|in ChatGPT)',title,flags=re.IGNORECASE)
        if match:families.add(match[1].upper())
    if len(families)!=1:raise SourceError('source_format_unknown')
    eligibility={}
    for row in document.rows:
        if len(row)!=4:continue
        plan=row[0].casefold()
        if plan not in {'plus','pro','business','enterprise','free and go'}:continue
        flags=[{'included':True,'not included':False}.get(cell.casefold()) for cell in row[1:]]
        plans=('free','go') if plan=='free and go' else (plan,)
        for name in plans:eligibility[name]={'High':flags[0],'Medium':flags[0],'Extra High':flags[1],'Pro':flags[2]}
    return {'announced_model':next(iter(families)),'product':'Chat','eligibility':eligibility,
            'source_url':url,'source_sha256':hashlib.sha256(raw).hexdigest()}
