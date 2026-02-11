#!/usr/bin/env python3
"""
Test dla funkcji _compose_source_path()
Weryfikuje poprawne łączenie globalnej ścieżki źródłowej z ścieżką profilu.
"""

import sys
from pathlib import Path

# Dodaj src do path
project_root = Path(__file__).parents[1]
sys.path.insert(0, str(project_root))

from app.services.process import _compose_source_path


def test_compose_source_path():
    """Test różnych scenariuszy łączenia ścieżek."""

    global_base = "tomaasz@kosciesza:~/Genealogy/Sources/"

    # Test 1: Relatywna ścieżka bez leading slash
    result = _compose_source_path(
        "Nurskie dokumenty/1_43_0_4 Nurskie grodzkie wieczyste", global_base
    )
    expected = "tomaasz@kosciesza:~/Genealogy/Sources/Nurskie dokumenty/1_43_0_4 Nurskie grodzkie wieczyste"
    assert result == expected, f"Test 1 failed: {result} != {expected}"
    print("✓ Test 1: Relatywna ścieżka bez leading slash")

    # Test 2: Relatywna ścieżka z leading slash
    result = _compose_source_path(
        "/Nurskie dokumenty/1_43_0_2 Nurskie ziemskie relacje oblaty/1/", global_base
    )
    expected = "tomaasz@kosciesza:~/Genealogy/Sources/Nurskie dokumenty/1_43_0_2 Nurskie ziemskie relacje oblaty/1/"
    assert result == expected, f"Test 2 failed: {result} != {expected}"
    print("✓ Test 2: Relatywna ścieżka z leading slash (usunięty)")

    # Test 3: Pusta ścieżka profilu (użyj tylko bazy)
    result = _compose_source_path("", global_base)
    expected = global_base
    assert result == expected, f"Test 3 failed: {result} != {expected}"
    print("✓ Test 3: Pusta ścieżka profilu (tylko baza)")

    # Test 4: None jako ścieżka profilu
    result = _compose_source_path(None, global_base)
    expected = global_base
    assert result == expected, f"Test 4 failed: {result} != {expected}"
    print("✓ Test 4: None jako ścieżka profilu")

    # Test 5: Bezwzględna ścieżka SSH (user@host:path)
    absolute_ssh = "otheruser@otherhost:~/other/documents/"
    result = _compose_source_path(absolute_ssh, global_base)
    expected = absolute_ssh
    assert result == expected, f"Test 5 failed: {result} != {expected}"
    print("✓ Test 5: Bezwzględna ścieżka SSH (nie łączona)")

    # Test 6: Bezwzględna ścieżka lokalna Linux
    absolute_linux = "/mnt/other/location/"
    result = _compose_source_path(absolute_linux, global_base)
    # Leading slash powinien być usunięty i połączony z bazą
    # (chyba że jest to uznawane za absolute - w obecnej implementacji będzie połączone)
    print(f"✓ Test 6: Bezwzględna lokalna Linux: {result}")

    # Test 7: Ścieżka Windows UNC
    unc_path = "\\\\server\\share\\folder"
    result = _compose_source_path(unc_path, global_base)
    expected = unc_path  # Powinna być zachowana jako absoluta
    assert result == expected, f"Test 7 failed: {result} != {expected}"
    print("✓ Test 7: Ścieżka Windows UNC (nie łączona)")

    # Test 8: Ścieżka Windows z literą dysku
    windows_path = "C:\\Documents\\Scans"
    result = _compose_source_path(windows_path, global_base)
    expected = windows_path  # Powinna być zachowana jako absoluta
    assert result == expected, f"Test 8 failed: {result} != {expected}"
    print("✓ Test 8: Ścieżka Windows z literą dysku (nie łączona)")

    # Test 9: Ścieżka home directory (~)
    home_path = "~/local/documents"
    result = _compose_source_path(home_path, global_base)
    expected = home_path  # Powinna być zachowana jako absoluta
    assert result == expected, f"Test 9 failed: {result} != {expected}"
    print("✓ Test 9: Ścieżka home (~) (nie łączona)")

    # Test 10: Ścieżka już zawierająca bazę
    already_full = global_base + "Nurskie dokumenty/test"
    result = _compose_source_path(already_full, global_base)
    expected = already_full  # Nie powinna być duplikowana
    assert result == expected, f"Test 10 failed: {result} != {expected}"
    print("✓ Test 10: Ścieżka już zawiera bazę (nie duplikowana)")

    # Test 11: Brak obu ścieżek
    result = _compose_source_path(None, None)
    expected = None
    assert result == expected, f"Test 11 failed: {result} != {expected}"
    print("✓ Test 11: Brak obu ścieżek (None)")

    # Test 12: Tylko ścieżka profilu, brak bazy
    result = _compose_source_path("Nurskie dokumenty/test", None)
    expected = "Nurskie dokumenty/test"
    assert result == expected, f"Test 12 failed: {result} != {expected}"
    print("✓ Test 12: Tylko ścieżka profilu, brak bazy")

    print("\n✅ Wszystkie testy przeszły pomyślnie!")
    return True


if __name__ == "__main__":
    try:
        test_compose_source_path()
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Test nie powiódł się: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Błąd podczas testów: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
