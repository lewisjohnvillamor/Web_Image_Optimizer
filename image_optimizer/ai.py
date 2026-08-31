"""Optional Claude-powered metadata: alt text, captions, SEO filenames.

Compression makes images *lighter*. This makes them *findable and usable* -
the two things a web image needs that no amount of encoder tuning provides.
Alt text is a WCAG requirement and a real accessibility gap on most sites,
and it cannot be derived from pixels by heuristics.

This module is entirely optional. Nothing else in the package imports it at
module scope, and every entry point degrades to "no AI metadata" when the
SDK or credentials are missing.

Privacy note: enabling this uploads a downscaled copy of each image to the
Anthropic API. It is off by default and the UI says so.
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from io import BytesIO
from typing import Callable, Dict, List, Optional, Sequence

from PIL import Image

DEFAULT_MODEL = 'claude-opus-5'

# Bulk captioning is a place where users legitimately want to trade capability
# for cost, so the choice is theirs and explicit.
MODEL_CHOICES = (
    ('claude-opus-5', 'Opus 5 - best descriptions'),
    ('claude-sonnet-5', 'Sonnet 5 - balanced'),
    ('claude-haiku-4-5', 'Haiku 4.5 - cheapest, high volume'),
)

# What we send to the API. Plenty for description; keeps tokens and upload low.
VISION_MAX_DIM = 768

SYSTEM_PROMPT = """You write image metadata for websites.

Alt text rules (WCAG 2.2):
- Describe what the image conveys in context, not "image of" or "picture of".
- 125 characters or fewer, one sentence, no trailing period needed.
- Include text that appears in the image if it carries meaning.
- If the image is purely decorative (spacers, background textures, ornamental
  flourishes), set is_decorative true and return an empty alt_text - an empty
  alt attribute is correct for decorative images.
- Never guess at identities, brands, locations, or numbers you cannot read.
  Describe only what is visibly present.

The seo_filename is lowercase kebab-case, 2-6 words, no file extension, no
stop words padding it out. It should read like a useful URL slug."""

RESPONSE_SCHEMA = {
    'type': 'object',
    'properties': {
        'alt_text': {'type': 'string'},
        'caption': {'type': 'string'},
        'seo_filename': {'type': 'string'},
        'is_decorative': {'type': 'boolean'},
        'detected_text': {'type': 'string'},
    },
    'required': ['alt_text', 'caption', 'seo_filename', 'is_decorative', 'detected_text'],
    'additionalProperties': False,
}


@dataclass
class AISettings:
    enabled: bool = False
    model: str = DEFAULT_MODEL
    generate_alt: bool = True
    generate_filenames: bool = False
    rename_outputs: bool = False
    context: str = ''            # "what this site is about", steers wording
    language: str = 'English'
    concurrency: int = 4
    api_key: str = ''            # blank => resolve from the environment

    def to_dict(self) -> Dict[str, object]:
        d = dict(self.__dict__)
        d.pop('api_key', None)   # never persisted to disk
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> 'AISettings':
        known = {k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__}
        known.pop('api_key', None)
        return cls(**known)


@dataclass
class AIMetadata:
    source: str
    alt_text: str = ''
    caption: str = ''
    seo_filename: str = ''
    is_decorative: bool = False
    detected_text: str = ''
    error: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0


class AIUnavailable(RuntimeError):
    """Raised when AI metadata was asked for but cannot run."""


def sdk_available() -> bool:
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def credentials_available(settings: Optional[AISettings] = None) -> bool:
    if settings and settings.api_key.strip():
        return True
    return bool(os.environ.get('ANTHROPIC_API_KEY') or
                os.environ.get('ANTHROPIC_AUTH_TOKEN'))


def availability_hint(settings: Optional[AISettings] = None) -> Optional[str]:
    """Human-readable reason AI metadata cannot run, or None if it can."""
    if not sdk_available():
        return ('The Anthropic SDK is not installed. Run `pip install anthropic` '
                'to enable alt-text generation.')
    if not credentials_available(settings):
        return ('No API key found. Set the ANTHROPIC_API_KEY environment variable '
                'or paste a key in the AI tab.')
    return None


def _client(settings: AISettings):
    import anthropic
    if settings.api_key.strip():
        return anthropic.Anthropic(api_key=settings.api_key.strip())
    return anthropic.Anthropic()


def encode_for_vision(path: str) -> tuple[str, str]:
    """Downscaled base64 payload plus its media type."""
    with Image.open(path) as img:
        img.load()
        work = img.convert('RGB')
        work.thumbnail((VISION_MAX_DIM, VISION_MAX_DIM), Image.Resampling.LANCZOS)
        buf = BytesIO()
        work.save(buf, 'JPEG', quality=82, optimize=True)
    return base64.standard_b64encode(buf.getvalue()).decode('ascii'), 'image/jpeg'


def slugify(value: str, fallback: str = 'image') -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', (value or '').lower()).strip('-')
    slug = re.sub(r'-{2,}', '-', slug)
    return slug[:80] or fallback


def describe_image(path: str, settings: AISettings, client=None) -> AIMetadata:
    """Ask Claude to describe one image. Never raises; errors land on the result."""
    meta = AIMetadata(source=path)
    try:
        client = client or _client(settings)
        payload, media_type = encode_for_vision(path)

        instructions = [
            f'Filename: {os.path.basename(path)}',
            f'Write the alt text and caption in {settings.language}.',
        ]
        if settings.context.strip():
            instructions.append(
                f'Site context (use it to judge what matters in the image): '
                f'{settings.context.strip()}')

        response = client.messages.create(
            model=settings.model,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            output_config={
                'effort': 'low',
                'format': {'type': 'json_schema', 'schema': RESPONSE_SCHEMA},
            },
            messages=[{
                'role': 'user',
                'content': [
                    {'type': 'image',
                     'source': {'type': 'base64', 'media_type': media_type,
                                'data': payload}},
                    {'type': 'text', 'text': '\n'.join(instructions)},
                ],
            }],
        )

        if response.stop_reason == 'refusal':
            meta.error = 'the model declined to describe this image'
            return meta

        text = next((b.text for b in response.content if b.type == 'text'), '')
        data = json.loads(text)
        meta.alt_text = '' if data.get('is_decorative') else (data.get('alt_text') or '').strip()
        meta.caption = (data.get('caption') or '').strip()
        meta.is_decorative = bool(data.get('is_decorative'))
        meta.detected_text = (data.get('detected_text') or '').strip()
        meta.seo_filename = slugify(
            data.get('seo_filename') or '',
            fallback=slugify(os.path.splitext(os.path.basename(path))[0]))
        if response.usage:
            meta.input_tokens = response.usage.input_tokens or 0
            meta.output_tokens = response.usage.output_tokens or 0
    except ImportError:
        meta.error = 'the anthropic package is not installed'
    except json.JSONDecodeError:
        meta.error = 'could not parse the model response'
    except Exception as exc:
        meta.error = _friendly_error(exc)
    return meta


def _friendly_error(exc: Exception) -> str:
    name = type(exc).__name__
    if name == 'AuthenticationError':
        return 'API key rejected - check ANTHROPIC_API_KEY'
    if name == 'RateLimitError':
        return 'rate limited by the API - lower the concurrency and retry'
    if name == 'PermissionDeniedError':
        return 'this API key is not allowed to use that model'
    if name == 'NotFoundError':
        return 'unknown model id'
    if name == 'APIConnectionError':
        return 'could not reach the API - check the network'
    return f'{name}: {exc}'


def describe_batch(paths: Sequence[str], settings: AISettings,
                   progress: Optional[Callable[[int, int, AIMetadata], None]] = None,
                   cancel: Optional[threading.Event] = None) -> Dict[str, AIMetadata]:
    """Describe many images concurrently. Returns a map keyed by source path."""
    hint = availability_hint(settings)
    if hint:
        raise AIUnavailable(hint)

    client = _client(settings)
    results: Dict[str, AIMetadata] = {}
    total = len(paths)
    done = 0

    with ThreadPoolExecutor(max_workers=max(1, min(8, settings.concurrency))) as pool:
        futures = {pool.submit(describe_image, p, settings, client): p for p in paths}
        for future in as_completed(futures):
            meta = future.result()
            results[meta.source] = meta
            done += 1
            if progress:
                progress(done, total, meta)
            if cancel is not None and cancel.is_set():
                for pending in futures:
                    pending.cancel()
                break
    return results


def apply_to_results(file_results, metadata: Dict[str, AIMetadata]) -> None:
    """Copy AI metadata onto FileResult objects in place."""
    for result in file_results:
        meta = metadata.get(result.source)
        if not meta or meta.error:
            continue
        result.alt_text = meta.alt_text
        result.seo_filename = meta.seo_filename
