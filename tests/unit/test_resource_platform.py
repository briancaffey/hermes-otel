"""#476: ``os.type`` and ``host.arch`` on the Resource, in the OTel spellings."""

from __future__ import annotations

import pytest

from hermes_otel.pipeline_env import host_arch, os_type

OS_WELL_KNOWN = {
    "windows",
    "linux",
    "darwin",
    "freebsd",
    "netbsd",
    "openbsd",
    "dragonflybsd",
    "hpux",
    "aix",
    "solaris",
    "z_os",
    "zos",
}
ARCH_WELL_KNOWN = {"amd64", "arm32", "arm64", "ia64", "ppc32", "ppc64", "s390x", "x86"}


class TestMapping:
    @pytest.mark.parametrize(
        "system,expected",
        [
            ("Darwin", "darwin"),
            ("Linux", "linux"),
            ("Windows", "windows"),
            ("FreeBSD", "freebsd"),
            ("", ""),
        ],
    )
    def test_os_type(self, system, expected):
        assert os_type(system) == expected

    @pytest.mark.parametrize(
        "machine,expected",
        [
            ("arm64", "arm64"),
            ("aarch64", "arm64"),
            ("x86_64", "amd64"),
            ("AMD64", "amd64"),
            ("i686", "x86"),
            ("armv7l", "arm32"),
            ("ppc64le", "ppc64"),
            ("s390x", "s390x"),
            ("riscv64", "riscv64"),  # no well-known value: raw, lower-cased
            ("", ""),
        ],
    )
    def test_host_arch(self, machine, expected):
        assert host_arch(machine) == expected

    def test_this_host_gets_well_known_values(self):
        assert os_type() in OS_WELL_KNOWN
        assert host_arch() in ARCH_WELL_KNOWN


class TestResource:
    def test_resource_carries_both(self, inmemory_otel_setup):
        from hermes_otel.pipeline import build_resource

        _, plugin = inmemory_otel_setup
        attrs = dict(build_resource(plugin.config, plugin.profile_name).attributes)
        assert attrs["os.type"] == os_type()
        assert attrs["host.arch"] == host_arch()

    def test_user_override_wins(self, inmemory_otel_setup):
        from hermes_otel.pipeline import build_resource
        from hermes_otel.plugin_config import HermesOtelConfig

        _, plugin = inmemory_otel_setup
        plugin.config = HermesOtelConfig(
            resource_attributes={"os.type": "z_os", "host.arch": "s390x"}
        )
        attrs = dict(build_resource(plugin.config, plugin.profile_name).attributes)
        assert attrs["os.type"] == "z_os" and attrs["host.arch"] == "s390x"

    def test_unknown_platform_sets_nothing(self, inmemory_otel_setup, monkeypatch):
        import platform

        from hermes_otel.pipeline import build_resource

        monkeypatch.setattr(platform, "system", lambda: "")
        monkeypatch.setattr(platform, "machine", lambda: "")
        _, plugin = inmemory_otel_setup
        attrs = dict(build_resource(plugin.config, plugin.profile_name).attributes)
        assert "os.type" not in attrs and "host.arch" not in attrs
