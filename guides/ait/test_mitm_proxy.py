#!/usr/bin/env python3
"""
Test script to verify MITM proxy functionality for OneShot Bench

Usage: python test_mitm_proxy.py
"""

import requests
import time
import sqlite3
import json
from pathlib import Path

def test_proxy_health():
    """Test basic proxy connectivity"""
    try:
        # Test that proxy is running by making a request through it
        # Use httpbin.org as a test target - this will work even without certificate trust
        response = requests.get(
            'http://httpbin.org/get',
            proxies={
                'http': 'http://localhost:18080',
                'https': 'http://localhost:18080'
            },
            timeout=5
        )

        if response.status_code == 200:
            print("✓ Proxy is running and can handle HTTP traffic")
            return True
        else:
            print(f"✗ Proxy test failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ Proxy connectivity test failed: {e}")
        return False

def test_openai_api_via_proxy():
    """Test OpenAI API connectivity through proxy"""
    try:
        response = requests.get(
            'https://api.openai.com/v1/models',
            headers={'Authorization': 'Bearer dummy-token'},
            proxies={
                'http': 'http://localhost:18080',
                'https': 'http://localhost:18080'
            },
            verify=False,  # Disable SSL verification for this test
            timeout=10
        )
        # We expect 401 (unauthorized) but NOT connection errors
        if response.status_code == 401:
            print("✓ OpenAI API proxy test passed (certificate not trusted but proxy working)")
            return True
        else:
            print(f"✗ OpenAI API proxy test failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ OpenAI API proxy connection failed: {e}")
        return False

def test_trace_database():
    """Test that trace databases are accessible"""
    trace_dir = Path("data/traces/v3")

    if not trace_dir.exists():
        print("✗ Trace directory not found")
        return False

    # Check raw database
    raw_db = trace_dir / "raw_synth_ai.db" / "traces.sqlite3"
    if raw_db.exists():
        try:
            conn = sqlite3.connect(str(raw_db))
            conn.close()
            print("✓ Raw trace database accessible")
        except Exception as e:
            print(f"✗ Raw trace database error: {e}")
            return False
    else:
        print("! Raw trace database not yet created (will be created on first use)")

    # Check clean database
    clean_db = trace_dir / "clean_synth_ai.db" / "traces.sqlite3"
    if clean_db.exists():
        try:
            conn = sqlite3.connect(str(clean_db))
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = cursor.fetchall()
            conn.close()
            print(f"✓ Clean trace database accessible (tables: {tables})")
            return True
        except Exception as e:
            print(f"✗ Clean trace database error: {e}")
            return False
    else:
        print("! Clean trace database not yet created (will be created on first use)")

    return True

def test_certificate_trust():
    """Test that MITM certificate is properly trusted"""
    try:
        # First test without SSL verification to see if proxy works
        response_no_verify = requests.get(
            'https://httpbin.org/get',
            proxies={
                'http': 'http://localhost:18080',
                'https': 'http://localhost:18080'
            },
            verify=False,
            timeout=5
        )

        if response_no_verify.status_code != 200:
            print("✗ Proxy HTTPS interception failed")
            return False

        # Now test with SSL verification
        response = requests.get(
            'https://httpbin.org/get',
            proxies={
                'http': 'http://localhost:18080',
                'https': 'http://localhost:18080'
            },
            verify=True,
            timeout=5
        )
        if response.status_code == 200:
            print("✓ Certificate trust test passed")
            return True
        else:
            print(f"✗ Certificate trust test failed: {response.status_code}")
            return False
    except requests.exceptions.SSLError as e:
        if "certificate verify failed" in str(e):
            print("✗ Certificate not trusted - visit http://mitm.it or import ~/.mitmproxy/mitmproxy-ca-cert.pem")
            print("  Note: Proxy is working (HTTPS interception active) but certificate needs to be trusted")
            return False
        else:
            print(f"✗ SSL Error: {e}")
            return False
    except Exception as e:
        print(f"✗ Certificate trust test error: {e}")
        return False

def main():
    """Run all proxy tests"""
    print("=== MITM Proxy Tests for OneShot Bench ===\n")

    tests = [
        test_proxy_health,
        test_openai_api_via_proxy,
        test_trace_database,
        test_certificate_trust
    ]

    passed = 0
    total = len(tests)

    for test in tests:
        print(f"Running {test.__name__}...")
        if test():
            passed += 1
        print()

    print(f"=== Results: {passed}/{total} tests passed ===")

    if passed == total:
        print("🎉 All proxy tests passed! Ready for OneShot Bench.")
        return True
    else:
        print("⚠️  Some tests failed. Fix issues before proceeding:")
        print("   1. Ensure proxy is running: bash scripts/start_synth_workers.sh")
        print("   2. Trust MITM certificate: visit http://mitm.it")
        print("   3. Check firewall/proxy settings")
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
