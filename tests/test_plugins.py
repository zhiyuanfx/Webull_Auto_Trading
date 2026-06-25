from pathlib import Path

from strategy_desk.plugins import PluginRegistry


def test_reference_plugin_is_discoverable_and_importable():
    registry = PluginRegistry(Path("strategies"))
    plugins = registry.discover()
    assert [plugin.manifest.id for plugin in plugins] == ["example_momentum"]
    assert registry.load_class(plugins[0]).__name__ == "Strategy"
