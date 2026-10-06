"""Adapters expose normalized evidence. They never assign recommendation scores."""
from __future__ import annotations
import json
import os
import time
import urllib.error
import urllib.request
from .core import EngineResponse, Invalid, digest

class EngineError(Exception):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward an Authorization header to an unexpected host.

def post_json(url, payload, key, timeout):
    request = urllib.request.Request(url, json.dumps(payload).encode(),
        {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}, method='POST')
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Provider error bodies may echo requests or secrets. Keep status only.
        raise EngineError(f'Provider HTTP {exc.code}', exc.code in (408, 429) or exc.code >= 500) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        raise EngineError('Provider connection or timeout error', True) from None
    except (ValueError, UnicodeError):
        raise EngineError('Provider returned invalid JSON') from None

def normalize(raw, elapsed=0):
    parts, citations = [], []
    for item in raw.get('output', []):
        if item.get('type') == 'message':
            for part in item.get('content', []):
                if part.get('type') in ('output_text', 'refusal'):
                    parts.append(part.get('text', part.get('refusal', '')))
                    for citation in part.get('annotations', []):
                        if citation.get('type') == 'url_citation':
                            citations.append(citation)
        elif item.get('type') == 'search_results':
            citations.extend(dict(c, type='retrieved_source') for c in item.get('results', []))
    text = '\n'.join(parts)
    status = raw.get('status', 'unknown')
    if not text.strip() and status == 'completed':
        status = 'unusable'
    return EngineResponse(text=text, raw=raw, model=raw.get('model'), request_id=raw.get('id'),
                          citations=citations, usage=raw.get('usage') or {}, status=status, latency_ms=elapsed)

class Adapter:
    def __init__(self, config, timeout=60):
        self.config, self.timeout = config, timeout

    def capabilities(self):
        return {'history': 'full_transcript', 'citations': True, 'location': 'prompt_only', 'provider_seed': False}

    def start_session(self):
        return []

    def close_session(self, session):
        session.clear()

    def request(self, history):
        e = self.config
        payload = {'input': history, 'max_output_tokens': e.get('max_output_tokens', 1200)}
        if e.get('preset'):
            payload['preset'] = e['preset']
        else:
            payload['model'] = e['model']
            if e.get('search'):
                payload['tools'] = [{'type': 'web_search'}]
            if 'temperature' in e:
                payload['temperature'] = e['temperature']
        if e['adapter'] == 'openai':
            payload['store'] = False
        return payload

    def check_credentials(self):
        if self.config['adapter'] == 'fake':
            return
        env = self.config.get('api_key_env', 'OPENAI_API_KEY' if self.config['adapter'] == 'openai' else 'PERPLEXITY_API_KEY')
        if not os.environ.get(env):
            raise Invalid(f'Missing {env}; set it locally before a live run')

    def send_message(self, history, execution_key):
        e = self.config
        env = e.get('api_key_env', 'OPENAI_API_KEY' if e['adapter'] == 'openai' else 'PERPLEXITY_API_KEY')
        endpoint = 'https://api.openai.com/v1/responses' if e['adapter'] == 'openai' else 'https://api.perplexity.ai/v1/agent'
        start = time.monotonic()
        raw = post_json(endpoint, self.request(history), os.environ[env], self.timeout)
        return normalize(raw, int((time.monotonic() - start) * 1000))

class FakeAdapter(Adapter):
    """Synthetic responses, deliberately isolated from live benchmark claims."""
    def request(self, history):
        return {'input': history, 'seed': self.config.get('seed', 0), 'synthetic': True}

    def send_message(self, history, execution_key):
        turn = sum(m['role'] == 'user' for m in history)
        n = int(digest([execution_key, self.config.get('seed', 0), turn])[:8], 16)
        names = ['Demo Coastal Kitchen', 'Harbor Table', 'Palm Courtyard']
        first = names[n % 3]
        second = names[(n + 1) % 3]
        if n % 11 == 0:
            text = 'I cannot identify a suitable option from the information available.'
        elif turn >= 3:
            text = f'My top pick is **{first}** for your preferences. You could also consider **{second}**.'
        else:
            text = f'I recommend **{first}** for your dinner. You could consider **{second}** as another option.'
        raw = {'id': 'fake_' + digest([execution_key, turn])[:12], 'model': 'synthetic-v1',
               'status': 'completed', 'synthetic': True, 'output': [{'type': 'message',
               'content': [{'type': 'output_text', 'text': text, 'annotations': []}]}]}
        return normalize(raw)

def adapter(config, timeout=60):
    return (FakeAdapter if config['adapter'] == 'fake' else Adapter)(config, timeout)
