from modchef import suggest


def test_closest_finds_near_miss():
    cands = ["samtools", "bcftools", "bwa", "zlib"]
    assert suggest.closest("samtool", cands) == ["samtools"]


def test_closest_returns_empty_when_nothing_close():
    assert suggest.closest("xyzzy", ["samtools", "bcftools"]) == []


def test_closest_is_case_insensitive_but_preserves_candidate_case():
    cands = ["SAMtools/1.18-GCC-12.3.0", "BWA/0.7.18-GCC-13.2.0"]
    # a version typo should map back to the original-cased candidate
    assert suggest.closest("SAMtools/1.18-GCC-12.3.1", cands) == \
        ["SAMtools/1.18-GCC-12.3.0"]


def test_closest_limits_results():
    cands = ["pandas", "pandes", "pandus", "pandos"]
    assert len(suggest.closest("panda", cands, n=2)) <= 2
