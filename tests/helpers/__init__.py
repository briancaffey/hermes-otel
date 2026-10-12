"""Shared test scaffolding (#406). Importable as ``helpers.*`` because pytest's
``pythonpath`` includes ``tests/``. Fixtures defined here are registered by the
tier ``conftest.py`` files; fakes and builders are imported where used.

* ``dashboard`` — adapter isolation, ``FakeHttp``, ``adapter()``, the contract app
* ``live``      — live-store row builders and the ``store`` fixture
* ``turns``     — fake Hermes hook payloads and ``run_turn`` / ``api_call``
* ``tracer``    — the ``mock_tracer`` / ``disabled_tracer`` fixtures
"""
