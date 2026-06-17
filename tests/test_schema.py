from modchef import schema


def test_slug_normalizes():
    assert schema.slug("SciPy-bundle/2025.05") == "scipy-bundle-2025-05"


def test_full_module_name_with_toolchain():
    assert schema.full_module_name("SAMtools", "1.22", "GCC", "14.3.0", "") == \
        "SAMtools/1.22-GCC-14.3.0"


def test_full_module_name_system_toolchain():
    assert schema.full_module_name("Python", "3.11.3", "system", "system", "") == \
        "Python/3.11.3"


def test_full_module_name_with_versionsuffix():
    assert schema.full_module_name("R-bundle-CRAN", "2025.05", "foss", "2025a",
                                   "-R-4.5.0") == \
        "R-bundle-CRAN/2025.05-foss-2025a-R-4.5.0"


def test_uri_builders_are_namespaced():
    assert str(schema.module_uri("SAMtools/1.22-GCC-14.3.0")).startswith(str(schema.RES))
    assert str(schema.software_uri("samtools")).startswith(str(schema.RES))
