import json

from image_optimizer import config as config_module
from image_optimizer.engine import MODE_LOSSLESS, OptimizeSettings


def test_defaults_load_when_there_is_no_file(tmp_path):
    loaded = config_module.load(str(tmp_path / 'missing.json'))
    assert isinstance(loaded.settings, OptimizeSettings)
    assert not loaded.ai.enabled


def test_a_corrupt_config_falls_back_to_defaults(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text('{ this is not json')
    assert config_module.load(str(path)).settings.output_format == 'auto'


def test_config_round_trips(tmp_path):
    config = config_module.load(str(tmp_path / 'c.json'))
    config.settings.widths = (1200, 600)
    config.settings.target = 'high'
    config.base_url = '/assets'
    path = config_module.save(config, str(tmp_path / 'c.json'))
    restored = config_module.load(path)
    assert restored.settings.widths == (1200, 600)
    assert restored.settings.target == 'high'
    assert restored.base_url == '/assets'


def test_the_api_key_is_never_written_to_disk(tmp_path):
    config = config_module.load(str(tmp_path / 'c.json'))
    config.ai.api_key = 'sk-ant-secret-value'
    path = config_module.save(config, str(tmp_path / 'c.json'))
    raw = open(path).read()
    assert 'sk-ant-secret-value' not in raw
    assert 'api_key' not in json.loads(raw)['ai']


def test_saving_is_atomic_and_leaves_no_temp_file(tmp_path):
    config = config_module.load(str(tmp_path / 'c.json'))
    config_module.save(config, str(tmp_path / 'c.json'))
    assert not (tmp_path / 'c.json.tmp').exists()


def test_every_builtin_preset_applies_cleanly(tmp_path):
    config = config_module.load(str(tmp_path / 'c.json'))
    for name in config_module.BUILTIN_PRESETS:
        assert config.apply_preset(name), name
        assert config.settings.mode in ('smart', 'fixed', 'lossless')
        assert 0 < config.settings.quality <= 100
        assert config.settings.effort in range(0, 7)


def test_presets_describe_themselves():
    for name, preset in config_module.BUILTIN_PRESETS.items():
        assert len(preset['description']) > 20, name


def test_an_unknown_preset_is_rejected(tmp_path):
    assert not config_module.load(str(tmp_path / 'c.json')).apply_preset('nope')


def test_applying_a_preset_clears_previous_state(tmp_path):
    config = config_module.load(str(tmp_path / 'c.json'))
    config.apply_preset('E-commerce product shots')
    assert config.settings.widths
    config.apply_preset('Maximum compression')
    assert not config.settings.widths          # not inherited from the last preset


def test_a_preset_keeps_run_scoped_toggles(tmp_path):
    config = config_module.load(str(tmp_path / 'c.json'))
    config.settings.recursive = False
    config.settings.skip_existing = True
    config.apply_preset('Thumbnails')
    assert config.settings.recursive is False
    assert config.settings.skip_existing is True


def test_user_presets_survive_a_save(tmp_path):
    config = config_module.load(str(tmp_path / 'c.json'))
    config.settings.target = 'maximum'
    config.save_preset('House style', 'our default')
    path = config_module.save(config, str(tmp_path / 'c.json'))
    restored = config_module.load(path)
    assert 'House style' in restored.all_presets()
    assert restored.apply_preset('House style')
    assert restored.settings.target == 'maximum'


def test_lossless_preset_keeps_metadata(tmp_path):
    config = config_module.load(str(tmp_path / 'c.json'))
    config.apply_preset('Lossless / archival')
    assert config.settings.mode == MODE_LOSSLESS
    assert not config.settings.strip_metadata
