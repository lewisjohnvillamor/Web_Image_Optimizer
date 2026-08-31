"""The AI layer is optional, so the important behaviour is that it degrades
cleanly and never lets a bad API response corrupt a run."""
import json
import os
import types

import pytest

from image_optimizer import ai
from image_optimizer.engine import FileResult


class FakeUsage:
    input_tokens = 1200
    output_tokens = 90


class FakeBlock:
    type = 'text'

    def __init__(self, text):
        self.text = text


class FakeResponse:
    def __init__(self, payload, stop_reason='end_turn'):
        self.content = [FakeBlock(json.dumps(payload))]
        self.stop_reason = stop_reason
        self.usage = FakeUsage()


class FakeClient:
    """Records the request so we can assert on the wire shape."""

    def __init__(self, payload=None, error=None, stop_reason='end_turn'):
        self.payload = payload or {
            'alt_text': 'A blue rounded logo with a yellow circle',
            'caption': 'The product logo',
            'seo_filename': 'Blue Product Logo',
            'is_decorative': False,
            'detected_text': '',
        }
        self.error = error
        self.stop_reason = stop_reason
        self.requests = []
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        return FakeResponse(self.payload, self.stop_reason)


@pytest.fixture
def image_path(tmp_path, flat_graphic):
    path = tmp_path / 'logo.png'
    flat_graphic.save(path)
    return str(path)


def test_slugify_produces_url_safe_names():
    assert ai.slugify('  Red Running Shoes!! ') == 'red-running-shoes'
    assert ai.slugify('a///b') == 'a-b'
    assert ai.slugify('***', fallback='image') == 'image'
    assert len(ai.slugify('x' * 200)) <= 80


def test_availability_reports_a_missing_key(monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('ANTHROPIC_AUTH_TOKEN', raising=False)
    monkeypatch.setattr(ai, 'sdk_available', lambda: True)
    hint = ai.availability_hint(ai.AISettings())
    assert hint and 'API key' in hint


def test_availability_reports_a_missing_sdk(monkeypatch):
    monkeypatch.setattr(ai, 'sdk_available', lambda: False)
    assert 'pip install anthropic' in ai.availability_hint(ai.AISettings())


def test_an_inline_key_counts_as_credentials(monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.delenv('ANTHROPIC_AUTH_TOKEN', raising=False)
    assert ai.credentials_available(ai.AISettings(api_key='sk-ant-x'))


def test_describe_batch_refuses_to_run_without_credentials(monkeypatch, image_path):
    monkeypatch.setattr(ai, 'sdk_available', lambda: False)
    with pytest.raises(ai.AIUnavailable):
        ai.describe_batch([image_path], ai.AISettings(enabled=True))


def test_vision_payload_is_downscaled(image_path):
    payload, media_type = ai.encode_for_vision(image_path)
    assert media_type == 'image/jpeg'
    assert len(payload) > 0
    assert len(payload) < 400_000        # bounded, whatever the source size


def test_describe_image_parses_a_good_response(image_path):
    client = FakeClient()
    meta = ai.describe_image(image_path, ai.AISettings(), client=client)
    assert meta.error is None
    assert meta.alt_text.startswith('A blue rounded logo')
    assert meta.seo_filename == 'blue-product-logo'      # slugified
    assert meta.input_tokens == 1200


def test_the_request_carries_the_image_and_the_schema(image_path):
    client = FakeClient()
    ai.describe_image(image_path, ai.AISettings(model='claude-opus-5'), client=client)
    request = client.requests[0]
    assert request['model'] == 'claude-opus-5'
    assert request['output_config']['format']['type'] == 'json_schema'
    assert request['output_config']['format']['schema']['required']
    blocks = request['messages'][0]['content']
    assert blocks[0]['type'] == 'image'
    assert blocks[0]['source']['type'] == 'base64'
    assert 'WCAG' in request['system']


def test_site_context_is_passed_through(image_path):
    client = FakeClient()
    ai.describe_image(image_path,
                      ai.AISettings(context='We sell handmade ceramics'),
                      client=client)
    text = client.requests[0]['messages'][0]['content'][1]['text']
    assert 'handmade ceramics' in text


def test_decorative_images_get_empty_alt_text(image_path):
    client = FakeClient(payload={
        'alt_text': 'a decorative swirl', 'caption': '', 'seo_filename': 'swirl',
        'is_decorative': True, 'detected_text': '',
    })
    meta = ai.describe_image(image_path, ai.AISettings(), client=client)
    assert meta.is_decorative
    assert meta.alt_text == ''      # empty alt is correct for decorative images


def test_a_refusal_is_reported_not_raised(image_path):
    meta = ai.describe_image(image_path, ai.AISettings(),
                             client=FakeClient(stop_reason='refusal'))
    assert meta.error and 'declined' in meta.error


def test_malformed_json_is_reported_not_raised(image_path, monkeypatch):
    client = FakeClient()
    client._create = lambda **kw: FakeResponse.__new__(FakeResponse)
    broken = types.SimpleNamespace(
        messages=types.SimpleNamespace(
            create=lambda **kw: types.SimpleNamespace(
                content=[FakeBlock('not json at all')],
                stop_reason='end_turn', usage=None)))
    meta = ai.describe_image(image_path, ai.AISettings(), client=broken)
    assert meta.error == 'could not parse the model response'


def test_api_errors_become_readable_messages(image_path):
    class RateLimitError(Exception):
        pass

    meta = ai.describe_image(image_path, ai.AISettings(),
                             client=FakeClient(error=RateLimitError('slow down')))
    assert 'rate limited' in meta.error


def test_metadata_is_applied_to_results(tmp_path):
    result = FileResult(source='/a/b.png', ok=True)
    metadata = {'/a/b.png': ai.AIMetadata(source='/a/b.png', alt_text='A cat',
                                          seo_filename='a-cat')}
    ai.apply_to_results([result], metadata)
    assert result.alt_text == 'A cat' and result.seo_filename == 'a-cat'


def test_failed_metadata_is_not_applied():
    result = FileResult(source='/a/b.png', ok=True)
    metadata = {'/a/b.png': ai.AIMetadata(source='/a/b.png', alt_text='junk',
                                          error='rate limited')}
    ai.apply_to_results([result], metadata)
    assert result.alt_text is None


def test_ai_settings_never_serialise_the_key():
    assert 'api_key' not in ai.AISettings(api_key='sk-ant-x').to_dict()
    assert ai.AISettings.from_dict({'api_key': 'sk-ant-x'}).api_key == ''


def test_the_default_model_is_a_known_choice():
    assert ai.DEFAULT_MODEL in [value for value, _ in ai.MODEL_CHOICES]
