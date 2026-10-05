//! Version parsing and ordering parity with Python's gateway and `_parse_version`.

use vibe_rs::update_notifier::pep440::parse_pep440_version;
use vibe_rs::update_notifier::version::parse_version;

#[test]
fn numeric_ordering_compares_segment_wise() {
    let older = parse_version("2.25.6").unwrap();
    let newer = parse_version("2.25.10").unwrap();
    assert!(older < newer);
    assert!(parse_version("2.25.10").unwrap() > parse_version("2.25.9").unwrap());
    assert!(parse_version("3.0.0").unwrap() > parse_version("2.99.99").unwrap());
}

#[test]
fn missing_trailing_segments_pad_with_zero() {
    assert_eq!(
        parse_version("2.25").unwrap(),
        parse_version("2.25.0").unwrap()
    );
    assert_eq!(
        parse_version("2.25.0").unwrap(),
        parse_version("2.25").unwrap()
    );
    assert!(parse_version("2.25.1").unwrap() > parse_version("2.25").unwrap());
    assert!(parse_version("2.26").unwrap() > parse_version("2.25.9").unwrap());
}

/// Python `test_replaces_hyphens_with_plus_signs_in_latest_version_to_conform_with_PEP_440`:
/// the use-case's `-`-to-`+` hack keeps `1.6.1-jetbrains` parsable — and, like
/// Python, the resulting local version outranks the bare release.
#[test]
fn replaces_hyphens_with_plus_signs_in_latest_version_to_conform_with_pep_440() {
    assert!(parse_version("1.6.1-jetbrains").unwrap() > parse_version("1.6.1").unwrap());
    assert!(parse_version("1.6.1-jetbrains").unwrap() > parse_version("1.0.0").unwrap());
    assert!(parse_version("2.26.0-rc1").unwrap() > parse_version("2.26.0").unwrap());
    assert_eq!(parse_version("2.26.0-rc1").unwrap().as_str(), "2.26.0+rc1");
    assert!(parse_version("2.25.6-rc1").unwrap() < parse_version("2.26.0").unwrap());
}

/// Python `packaging.Version`: explicit build metadata is a local version,
/// which outranks the bare release instead of being ignored.
#[test]
fn explicit_build_metadata_sorts_above_the_release() {
    assert!(parse_version("2.25.6+build.1").unwrap() > parse_version("2.25.6").unwrap());
}

#[test]
fn invalid_inputs_parse_as_none() {
    for raw in [
        "",
        "abc",
        "2..3",
        "2.x.3",
        "2.25.6.7.8.x",
        "-2.25",
        "2.-3",
        "2.25.6+",
        "2.25.6-",
        "1-2-3",
    ] {
        assert_eq!(parse_version(raw), None, "{raw:?} must not parse");
    }
}

#[test]
fn canonical_str_joins_segments_with_dots() {
    assert_eq!(parse_version("2.25.6").unwrap().as_str(), "2.25.6");
    assert_eq!(parse_version("2.25.06").unwrap().as_str(), "2.25.6");
    assert_eq!(parse_version("2.0.0").unwrap().as_str(), "2.0.0");
}

/// Python `packaging.Version`: a prerelease sorts below its release.
#[test]
fn prereleases_sort_below_their_release() {
    assert!(parse_pep440_version("2.26.0-rc1").unwrap() < parse_pep440_version("2.26.0").unwrap());
    assert!(parse_pep440_version("2.26.0rc1").unwrap() < parse_pep440_version("2.26.0").unwrap());
    assert_eq!(
        parse_pep440_version("2.26.0-rc1").unwrap(),
        parse_pep440_version("2.26.0rc1").unwrap()
    );
    let order = ["2.26.0a1", "2.26.0b2", "2.26.0rc3", "2.26.0"];
    for pair in order.windows(2) {
        assert!(
            parse_pep440_version(pair[0]).unwrap() < parse_pep440_version(pair[1]).unwrap(),
            "{} < {}",
            pair[0],
            pair[1]
        );
    }
    // A prerelease of a NEWER release still outranks an older stable release.
    assert!(parse_pep440_version("2.25.6-rc1").unwrap() < parse_pep440_version("2.26.0").unwrap());
    assert!(parse_pep440_version("2.26.0rc1").unwrap() > parse_pep440_version("2.25.9").unwrap());
}

/// Python `packaging.Version`: post releases parse and sort above the release.
#[test]
fn post_releases_parse_and_sort_above_the_release() {
    assert!(
        parse_pep440_version("2.26.0.post1").unwrap() > parse_pep440_version("2.26.0").unwrap()
    );
    assert_eq!(
        parse_pep440_version("2.26.0-1").unwrap(),
        parse_pep440_version("2.26.0.post1").unwrap()
    );
    assert!(
        parse_pep440_version("2.26.0.post1").unwrap() < parse_pep440_version("2.26.1").unwrap()
    );
}

#[test]
fn dev_releases_sort_below_prereleases_and_releases() {
    assert!(
        parse_pep440_version("2.26.0.dev1").unwrap() < parse_pep440_version("2.26.0a1").unwrap()
    );
    assert!(
        parse_pep440_version("2.26.0rc1.dev1").unwrap()
            < parse_pep440_version("2.26.0rc1").unwrap()
    );
    assert!(
        parse_pep440_version("2.26.0.post2.dev1").unwrap()
            < parse_pep440_version("2.26.0.post2").unwrap()
    );
}

#[test]
fn zero_padded_segments_normalize_and_compare_equal() {
    assert_eq!(
        parse_pep440_version("2.25.06").unwrap(),
        parse_pep440_version("2.25.6").unwrap()
    );
    assert_eq!(parse_pep440_version("2.25.06").unwrap().as_str(), "2.25.6");
    assert_eq!(
        parse_pep440_version("2.25").unwrap(),
        parse_pep440_version("2.25.0").unwrap()
    );
    assert!(parse_pep440_version("2.25.1").unwrap() > parse_pep440_version("2.25").unwrap());
}

#[test]
fn epochs_parse_and_outrank_same_release() {
    assert!(parse_pep440_version("1!2.26.0").unwrap() > parse_pep440_version("2.26.0").unwrap());
    assert_eq!(
        parse_pep440_version("1!2.26.0").unwrap().as_str(),
        "1!2.26.0"
    );
}

#[test]
fn local_segments_sort_above_the_bare_release() {
    assert!(
        parse_pep440_version("1.6.1+jetbrains").unwrap() > parse_pep440_version("1.6.1").unwrap()
    );
    assert!(parse_pep440_version("1.0+2").unwrap() > parse_pep440_version("1.0+a").unwrap());
    assert!(parse_pep440_version("1.0+a").unwrap() < parse_pep440_version("1.0+a.1").unwrap());
    assert_ne!(
        parse_pep440_version("2.25.6+build.1").unwrap(),
        parse_pep440_version("2.25.6").unwrap()
    );
}

/// Python `str(Version)`: the gateway emits the normalized form.
#[test]
fn pep440_str_is_the_normalized_form() {
    for (raw, normalized) in [
        ("2.26.0-rc1", "2.26.0rc1"),
        ("2.26.0.RC1", "2.26.0rc1"),
        ("2.26.0alpha3", "2.26.0a3"),
        ("2.26.0preview4", "2.26.0rc4"),
        ("2.26.0pre", "2.26.0rc0"),
        ("2.26.0-1", "2.26.0.post1"),
        ("2.26.0-r2", "2.26.0.post2"),
        ("2.26.0post04", "2.26.0.post4"),
        ("2.26.0.dev1", "2.26.0.dev1"),
        ("2.26.0-DEV.2", "2.26.0.dev2"),
        ("2.26.0+Ubuntu_1.02-Beta", "2.26.0+ubuntu.1.2.beta"),
        ("2.26.0+5.001", "2.26.0+5.1"),
        ("01.2.03", "1.2.3"),
        ("v2.26.0", "2.26.0"),
        ("0!2.26.0", "2.26.0"),
        ("2.26.0rc1.post2", "2.26.0rc1.post2"),
        ("  2.26.0  ", "2.26.0"),
    ] {
        assert_eq!(
            parse_pep440_version(raw).unwrap().as_str(),
            normalized,
            "{raw:?}"
        );
    }
}

#[test]
fn pep440_invalid_inputs_parse_as_none() {
    for raw in [
        "",
        "abc",
        "1..2",
        "1.2.",
        "2.26.0.",
        "2.26.0+",
        "2.26.0-ubuntu-1",
        "2.26.0_1",
        "2.26.0-ab1",
        "1.0.0++x",
        "2.26.0.post.rev3",
        "2.26.0.dev1.post1",
    ] {
        assert_eq!(parse_pep440_version(raw), None, "{raw:?} must not parse");
    }
}

/// Cross-check of parsing and `str()` normalization against Python
/// `packaging.version.Version`, entry by entry.
#[test]
fn pep440_matches_python_ground_truth() {
    for (raw, expected) in [
        ("2.26.0-rc1", Some("2.26.0rc1")),
        ("2.26.0rc1", Some("2.26.0rc1")),
        ("2.26.0.RC1", Some("2.26.0rc1")),
        ("2.26.0-RC.1", Some("2.26.0rc1")),
        ("2.26.0a1", Some("2.26.0a1")),
        ("2.26.0b2", Some("2.26.0b2")),
        ("2.26.0alpha3", Some("2.26.0a3")),
        ("2.26.0c.2", Some("2.26.0rc2")),
        ("2.26.0preview4", Some("2.26.0rc4")),
        ("2.26.0pre", Some("2.26.0rc0")),
        ("2.26.0a", Some("2.26.0a0")),
        ("2.26.0.post1", Some("2.26.0.post1")),
        ("2.26.0-post1", Some("2.26.0.post1")),
        ("2.26.0-1", Some("2.26.0.post1")),
        ("2.26.0-r2", Some("2.26.0.post2")),
        ("2.26.0.rev3", Some("2.26.0.post3")),
        ("2.26.0post04", Some("2.26.0.post4")),
        ("2.26.0.post.rev3", None),
        ("2.26.0.post", Some("2.26.0.post0")),
        ("2.26.0.dev1", Some("2.26.0.dev1")),
        ("2.26.0dev", Some("2.26.0.dev0")),
        ("2.26.0-DEV.2", Some("2.26.0.dev2")),
        ("2.26.0.Dev1", Some("2.26.0.dev1")),
        ("2.26.0+build.1", Some("2.26.0+build.1")),
        ("2.26.0+Ubuntu_1.02-Beta", Some("2.26.0+ubuntu.1.2.beta")),
        ("2.26.0+5.001", Some("2.26.0+5.1")),
        ("2.26.0+rc1", Some("2.26.0+rc1")),
        ("2.26.0+", None),
        ("1!2.26.0", Some("1!2.26.0")),
        ("0!2.26.0", Some("2.26.0")),
        ("01.2.03", Some("1.2.3")),
        ("2.25", Some("2.25")),
        ("2.25.0", Some("2.25.0")),
        ("2.25.06", Some("2.25.6")),
        ("1..2", None),
        ("1.2.", None),
        ("2.26.0.", None),
        ("2.26.0rc.", Some("2.26.0rc0")),
        ("2.26.0-ubuntu-1", None),
        ("abc", None),
        ("2.26.0-rc1-1", Some("2.26.0rc1.post1")),
        ("2.26.0rc1.post2", Some("2.26.0rc1.post2")),
        ("2.26.0.post2.dev1", Some("2.26.0.post2.dev1")),
        ("2.26.0rc1.dev1", Some("2.26.0rc1.dev1")),
        ("2.26.0.dev1.post1", None),
        ("v2.26.0", Some("2.26.0")),
        ("V2.26.0", Some("2.26.0")),
        ("  2.26.0  ", Some("2.26.0")),
        ("2.26.0a1.post1.dev2", Some("2.26.0a1.post1.dev2")),
        ("2.26.0-ab1", None),
        ("2.26.0b-1", Some("2.26.0b1")),
        ("2.26.0-c1", Some("2.26.0rc1")),
        ("2.26.0.5", Some("2.26.0.5")),
        ("2.26.0.0.0", Some("2.26.0.0.0")),
        ("1.0.0+local.1", Some("1.0.0+local.1")),
        ("1.0.0++x", None),
        ("2!0", Some("2!0")),
        ("2.26.0-post-1", Some("2.26.0.post1")),
        ("2.26.0_1", None),
        ("2.26.0.1-2", Some("2.26.0.1.post2")),
    ] {
        let parsed = parse_pep440_version(raw).map(|version| version.as_str());
        assert_eq!(parsed.as_deref(), expected, "input {raw:?}");
    }
}

/// Python `sorted(valid_versions, reverse=True)`: stable, highest first.
#[test]
fn pep440_descending_sort_matches_python() {
    let raw = [
        "1.0.dev1",
        "1.0a1",
        "1.0a1.dev1",
        "1.0a2",
        "1.0b1",
        "1.0rc1",
        "1.0",
        "1.0.post1",
        "1.0.post1.dev1",
        "1.0.post2",
        "1.0+local",
        "1.0+local.1",
        "1.0+2",
        "1.0+a",
        "1.1",
        "1.1.0",
        "1.1.post0",
        "1!1.0",
        "0.9.99",
        "1.0.0.1",
        "2.25",
        "2.25.0",
        "2.25.6+build.1",
    ];
    let mut parsed = raw
        .iter()
        .map(|raw| parse_pep440_version(raw).unwrap())
        .collect::<Vec<_>>();
    parsed.sort_by(|left, right| right.cmp(left));
    let sorted = parsed
        .iter()
        .map(|version| version.as_str())
        .collect::<Vec<_>>();
    let expected = [
        "1!1.0",
        "2.25.6+build.1",
        "2.25",
        "2.25.0",
        "1.1.post0",
        "1.1",
        "1.1.0",
        "1.0.0.1",
        "1.0.post2",
        "1.0.post1",
        "1.0.post1.dev1",
        "1.0+2",
        "1.0+local.1",
        "1.0+local",
        "1.0+a",
        "1.0",
        "1.0rc1",
        "1.0b1",
        "1.0a2",
        "1.0a1",
        "1.0a1.dev1",
        "1.0.dev1",
        "0.9.99",
    ];
    assert_eq!(sorted, expected.to_vec());
}
