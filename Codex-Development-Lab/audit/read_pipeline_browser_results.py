import json
from html.parser import HTMLParser
from pathlib import Path

class Result(HTMLParser):
    def __init__(self):
        super().__init__()
        self.reading = False
        self.text = ''
    def handle_starttag(self, tag, attrs):
        if tag == 'pre' and dict(attrs).get('id') == 'smoke-result': self.reading = True
    def handle_endtag(self, tag):
        if tag == 'pre': self.reading = False
    def handle_data(self, data):
        if self.reading: self.text += data

folder = Path(__file__).resolve().parent/'pipeline-preview'
results = {}
for size in ('desktop','mobile'):
    parser = Result()
    parser.feed((folder/f'{size}-dom.html').read_text(encoding='utf-8-sig'))
    assert parser.text, f'{size}: no browser result'
    results[size] = json.loads(parser.text)
    assert results[size]['ok'], results[size]
(folder/'validation.json').write_text(json.dumps(results,indent=2)+'\n',encoding='utf-8')
print(json.dumps(results))
