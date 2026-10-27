"""
    ============================================================================
    SHiP Anchor — tests/test_diagnostics_unit.py      (LAYER 1 + 2, v0.1.22)
    ============================================================================
    
LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
    -----------------------------------------------
    This suite answers one question: *does the "Download diagnostics" button
    hand an engineer a complete picture of SHiP Anchor WITHOUT leaking the
    secrets that guard the radio frames?*
    
We check two things:
   1. Completeness — the bundle carries every section an engineer needs:
      engine summary, binding records, a compiled LEAT cheat-sheet, the
      mesh heartbeat verdict, and the entry data itself.
   2. Sanitization — every cryptographic secret (a key, an HMAC, a nonce, a
      token) is redacted in place with "***", while the routing structure
      (node IEEE identifiers, group ids, routing rows) is preserved so the
      photo is still useful for debugging.
    
LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
    --------------------------------------------
    - We build a fake ``hass`` + persistent store (from conftest), put a real
  ``AnchorCompiler`` on ``hass.data[ship_anchor]``, and run the two HA entry
  points through a one-off event loop (no pytest plugin needed).
    - Secrets are planted in the config entry data and asserted redacted in the
  output. The compiler + LEAT path are exercised for real so the bundle
  matches what the hardware holds.
    """
    
from __future__ import annotations
    
import asyncio
from types import SimpleNamespace
    
from custom_components.ship_anchor import diagnostics
from custom_components.ship_anchor.diagnostics import (
     redact_structure,
     async_get_config_entry_diagnostics,
     async_get_device_diagnostics,
    )
from custom_components.ship_anchor.runtime import engine_summary
from custom_components.ship_anchor.compiler import AnchorCompiler
from tests.conftest import _make_fake_hass, _make_fake_store
    
DOMAIN = "ship_anchor"
    

def _run(coro):
    """ELI5: run one async function to completion in a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
    

def _build_hass_with_runtime(persisted, entry_data):
    """ELI5: stand up a fake hass whose runtime carries a real compiler."""
    hass = _make_fake_hass()
    store = _make_fake_store(persisted)
    stored = dict(persisted) if persisted else {}
    compiler = AnchorCompiler(hass, store, stored)
    hass.data[DOMAIN] = {
        "compiler": compiler,
        "store": store,
        "bindings": stored,
        "engine_summary": engine_summary(),
    }
    entry = SimpleNamespace(domain=DOMAIN, data=entry_data, options={})
    return hass, entry, compiler
    

# ---------------------------------------------------------------------------
# Sanitization (pure, Layer 1)
# ---------------------------------------------------------------------------
def test_redact_structure_scrubs_secrets_but_keeps_routing():
    """ELI5: secrets vanish, routing structure survives."""
    sample = {
        "bindings": [
            {
                "source_ieee": "aa:bb:cc:dd:ee:ff:00:01",
                "target_ieee": "aa:bb:cc:dd:ee:ff:00:02",
                "protocol": "ZIGBEE_ZHA",
                "key_hex": "00112233445566778899aabbccddeeff",
                "nonce": "deadbeef",
                "group_id": 7,
            },
        ],
    }
    sanitized, redacted = redact_structure(sample)
    row = sanitized["bindings"][0]
    assert row["key_hex"] == "***"
    assert row["nonce"] == "***"
    assert row["source_ieee"] == "aa:bb:cc:dd:ee:ff:00:01"
    assert row["target_ieee"] == "aa:bb:cc:dd:ee:ff:00:02"
    assert row["group_id"] == 7
    assert row["protocol"] == "ZIGBEE_ZHA"
    assert any("key_hex" in path for path in redacted)
    assert any("nonce" in path for path in redacted)
    

def test_redact_structure_descends_into_nested_secret_names():
    """ELI5: hmac / token / password names are redacted at any depth."""
    sample = {
        "outer": {
            "inner": {
                "hmac_tag": "abc123",
                "auth_token": "xyz",
                "password": "hunter2",
                "plain_value": 42,
            },
        },
    }
    sanitized, redacted = redact_structure(sample, "root")
    inner = sanitized["outer"]["inner"]
    assert inner["hmac_tag"] == "***"
    assert inner["auth_token"] == "***"
    assert inner["password"] == "***"
    assert inner["plain_value"] == 42
    

def test_redact_structure_is_non_mutating():
    """ELI5: redaction never corrupts the original data it is handed."""
    sample = {"secret_key": "topsecret", "kept": 1}
    redact_structure(sample)
    assert sample["secret_key"] == "topsecret"
    

# ---------------------------------------------------------------------------
# Entry-point bundles (Layer 2 — async, with a real compiler)
# ---------------------------------------------------------------------------
def _entry_with_secrets():
    """ELI5: a config entry that carries the secrets a real binding would."""
    return {
        "source_device_id": "sw_a",
        "source_name": "Hall Switch",
        "protocol": "ZIGBEE_ZHA",
        "source_ieee": "aa:bb:cc:dd:ee:ff:00:01",
        "targets": [{"device_id": "light1", "name": "Kitchen Light", "kind": "light"}],
        "rules": [
            {"gesture": "1x Tap", "brightness": 128},
            {"gesture": "3x Tap", "brightness": 40, "color_temp_mireds": 350},
        ],
        "heartbeats": [
            {"hop_index": 1, "last_counter": 9, "last_seen_ms": 60000},
            {"hop_index": 2, "last_counter": 2, "last_seen_ms": 0},
        ],
        "heartbeat_now_ms": 60000,
        "key_hex": "00112233445566778899aabbccddeeff",
        "nonce": "cafebabe",
    }
    

def test_config_entry_diagnostics_contains_all_sections():
    """ELI5: the integration bundle has every section an engineer needs."""
    persisted = {"bindings": [
        {
            "protocol": "ZIGBEE_ZHA",
            "source_ieee": "aa:bb:cc:dd:ee:ff:00:01",
            "target_ieee": "aa:bb:cc:dd:ee:ff:00:02",
            "clusters": [6, 766],
            "group_id": 1,
        },
    ]}
    hass, entry, _ = _build_hass_with_runtime(persisted, _entry_with_secrets())
    bundle = _run(async_get_config_entry_diagnostics(hass, entry))
    for key in ("integration", "engine", "bindings", "leat", "heartbeat", "sanitization"):
        assert key in bundle, key
    assert isinstance(bundle["bindings"], list)
    assert bundle["bindings"], "binding records should be surfaced"
    assert bundle["leat"]["row_count"] == 2
    assert bundle["heartbeat"]["hop_count"] == 2
    

def test_diagnostics_redact_entry_secrets():
    """ELI5: secrets planted in the entry data are redacted in the output."""
    hass, entry, _ = _build_hass_with_runtime({"bindings": []}, _entry_with_secrets())
    bundle = _run(async_get_config_entry_diagnostics(hass, entry))
    entry_out = bundle["entry"]
    assert entry_out["key_hex"] == "***"
    assert entry_out["nonce"] == "***"
    assert entry_out["source_ieee"] == "aa:bb:cc:dd:ee:ff:00:01"
    assert bundle["sanitization"]["redacted_field_count"] >= 2
    

def test_diagnostics_surfaces_engine_summary():
    """ELI5: the bundle reports which engine is running."""
    hass, entry, _ = _build_hass_with_runtime({"bindings": []}, _entry_with_secrets())
    bundle = _run(async_get_config_entry_diagnostics(hass, entry))
    engine = bundle["engine"]
    assert engine["name"] in ("pure-python", "native")
    assert "operations" in engine
    assert isinstance(engine["operations"], list)
    

def test_diagnostics_heartbeat_verdict():
    """ELI5: a never-seen hop is flagged as needing a re-handshake."""
    hass, entry, _ = _build_hass_with_runtime({"bindings": []}, _entry_with_secrets())
    bundle = _run(async_get_config_entry_diagnostics(hass, entry))
    heartbeat = bundle["heartbeat"]
    assert heartbeat["needs_re_handshake"] is True
    assert heartbeat["hop_count"] == 2
    assert any(hop["state"] == "unknown" for hop in heartbeat["hops"])
    

def test_device_diagnostics_adds_device_card():
    """ELI5: the device-level bundle adds a device identity card."""
    hass, entry, _ = _build_hass_with_runtime({"bindings": []}, _entry_with_secrets())
    device = SimpleNamespace(
        id="dev_1", name_by_user="Hall Switch",
        manufacturer="Aqara", model="WXCJKG11LM",
    )
    bundle = _run(async_get_device_diagnostics(hass, entry, device))
    assert bundle["device"]["id"] == "dev_1"
    assert bundle["device"]["manufacturer"] == "Aqara"
    assert bundle["device"]["model"] == "WXCJKG11LM"
    for key in ("integration", "engine", "bindings", "leat", "heartbeat", "sanitization"):
        assert key in bundle, key
    

def test_diagnostics_handles_empty_runtime_gracefully():
    """ELI5: a bare entry with no runtime still produces a complete bundle."""
    hass = _make_fake_hass()
    entry = SimpleNamespace(domain=DOMAIN, data={"rules": []}, options={})
    bundle = _run(async_get_config_entry_diagnostics(hass, entry))
    for key in ("integration", "engine", "bindings", "leat", "heartbeat", "sanitization"):
        assert key in bundle, key
    assert bundle["engine"]["name"] in ("pure-python", "native")
    assert bundle["leat"]["row_count"] == 0
    assert bundle["heartbeat"]["hop_count"] == 0
    

def test_bundle_is_json_serialisable():
    """ELI5: the whole bundle can be written to a JSON diagnostics file."""
    import json
    hass, entry, _ = _build_hass_with_runtime({"bindings": []}, _entry_with_secrets())
    bundle = _run(async_get_config_entry_diagnostics(hass, entry))
    text = json.dumps(bundle)
    assert "00112233445566778899aabbccddeeff" not in text
    assert "cafebabe" not in text
    