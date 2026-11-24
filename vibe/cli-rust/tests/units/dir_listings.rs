//! `@` path listings: read slots, cache eviction and filtered reads of directories past the entry cap.

use std::path::{Path, PathBuf};

use vibe_rs::dir_complete::{
    self, DirListings, Entry, Listing, Plan, ReadKey, MAX_CACHED, MAX_IN_FLIGHT,
};

fn key(dir: &str, prefix: &str) -> ReadKey {
    ReadKey {
        dir: PathBuf::from(dir),
        prefix: prefix.into(),
    }
}

fn listing(names: &[&str], truncated: bool) -> Option<Listing> {
    Some(Listing {
        entries: names
            .iter()
            .map(|name| Entry {
                name: (*name).into(),
                is_dir: false,
            })
            .collect(),
        truncated,
    })
}

fn names(plan: &Plan) -> Vec<&str> {
    plan.show
        .iter()
        .flat_map(|listing| &listing.entries)
        .map(|entry| entry.name.as_str())
        .collect()
}

#[test]
fn a_directory_already_being_read_takes_no_second_slot() {
    let mut listings = DirListings::default();
    assert!(listings.claim(&key("/a/", "")));
    assert!(!listings.claim(&key("/a/", "")));
}

#[test]
fn reads_past_the_in_flight_cap_wait_until_a_slot_frees() {
    let mut listings = DirListings::default();
    for index in 0..MAX_IN_FLIGHT {
        assert!(listings.claim(&key(&format!("/{index}/"), "")));
    }
    assert!(!listings.claim(&key("/late/", "")));
    listings.store(key("/0/", ""), listing(&[], false));
    assert!(listings.claim(&key("/late/", "")));
}

#[test]
fn the_oldest_listing_is_evicted_past_the_cache_cap() {
    let mut listings = DirListings::default();
    for index in 0..=MAX_CACHED {
        listings.store(key(&format!("/{index}/"), ""), listing(&["x"], false));
    }
    assert_eq!(
        listings.plan(Path::new("/0/"), "").read,
        Some(key("/0/", ""))
    );
    assert_eq!(listings.plan(Path::new("/1/"), "").read, None);
}

#[test]
fn a_complete_directory_needs_one_read_whatever_the_prefix() {
    let mut listings = DirListings::default();
    let plan = listings.plan(Path::new("/a/"), "x");
    assert_eq!(plan.read, Some(key("/a/", "")));
    listings.store(key("/a/", ""), listing(&["xy"], false));
    let plan = listings.plan(Path::new("/a/"), "x");
    assert_eq!((names(&plan), plan.read.clone()), (vec!["xy"], None));
}

#[test]
fn a_truncated_directory_reads_the_typed_prefix_and_shows_it_first() {
    let mut listings = DirListings::default();
    listings.store(key("/big/", ""), listing(&["aa"], true));
    let plan = listings.plan(Path::new("/big/"), "Zz");
    assert_eq!(
        (names(&plan), plan.read.clone()),
        (vec!["aa"], Some(key("/big/", "zz")))
    );
    listings.store(key("/big/", "zz"), listing(&["zz1"], false));
    let plan = listings.plan(Path::new("/big/"), "zz1");
    assert_eq!((names(&plan), plan.read.clone()), (vec!["zz1", "aa"], None));
    assert_eq!(listings.plan(Path::new("/big/"), "").read, None);
}

#[test]
fn a_truncated_prefix_read_is_narrowed_by_a_longer_prefix() {
    let mut listings = DirListings::default();
    listings.store(key("/big/", ""), listing(&[], true));
    listings.store(key("/big/", "a"), listing(&["ab"], true));
    assert_eq!(listings.plan(Path::new("/big/"), "a").read, None);
    assert_eq!(
        listings.plan(Path::new("/big/"), "ab").read,
        Some(key("/big/", "ab"))
    );
}

#[test]
fn prefix_reads_are_evicted_before_whole_directories() {
    let mut listings = DirListings::default();
    listings.store(key("/big/", ""), listing(&[], true));
    listings.store(key("/big/", "a"), listing(&["a1"], false));
    for index in 0..MAX_CACHED - 1 {
        listings.store(key(&format!("/{index}/"), ""), listing(&[], false));
    }
    assert_eq!(listings.plan(Path::new("/big/"), "").read, None);
    assert_eq!(
        listings.plan(Path::new("/big/"), "a").read,
        Some(key("/big/", "a"))
    );
}

#[test]
fn a_directory_past_the_cap_is_truncated_and_a_prefix_read_reaches_its_tail() {
    let dir = tempfile::tempdir().expect("tempdir");
    for name in ["a1", "a2", "a3", "École.md"] {
        std::fs::write(dir.path().join(name), "").expect("file");
    }
    let whole = dir_complete::list_capped(dir.path(), "", 2).expect("readable");
    assert!(whole.truncated);
    assert_eq!(whole.entries.len(), 2);
    let filtered = dir_complete::list_capped(dir.path(), "éc", 2).expect("readable");
    assert!(!filtered.truncated);
    assert_eq!(filtered.entries[0].name, "École.md");
}

#[test]
fn a_longer_prefix_waits_for_the_read_already_running_for_a_shorter_one() {
    let mut listings = DirListings::default();
    listings.store(key("/big/", ""), listing(&[], true));
    assert!(listings.claim(&key("/big/", "a")));
    assert_eq!(listings.plan(Path::new("/big/"), "ab").read, None);
    listings.store(key("/big/", "a"), listing(&["ab1"], true));
    assert_eq!(
        listings.plan(Path::new("/big/"), "ab").read,
        Some(key("/big/", "ab"))
    );
}

#[test]
fn the_closest_prefix_read_shows_until_the_longer_one_lands() {
    let mut listings = DirListings::default();
    listings.store(key("/big/", ""), listing(&["x"], true));
    listings.store(key("/big/", "a"), listing(&["ab1", "ac1"], true));
    let plan = listings.plan(Path::new("/big/"), "ab");
    assert_eq!(names(&plan), vec!["ab1", "ac1", "x"]);
}

#[test]
fn a_prefix_read_never_evicts_the_whole_listing_of_its_directory() {
    let mut listings = DirListings::default();
    listings.store(key("/big/", ""), listing(&[], true));
    for index in 0..MAX_CACHED - 1 {
        listings.store(key(&format!("/{index}/"), ""), listing(&[], false));
    }
    listings.store(key("/big/", "z"), listing(&["z1"], false));
    let plan = listings.plan(Path::new("/big/"), "z");
    assert_eq!((names(&plan), plan.read.clone()), (vec!["z1"], None));
}

#[test]
fn a_failed_prefix_read_waits_for_the_next_keystroke_and_a_failed_directory_stays() {
    let mut listings = DirListings::default();
    listings.store(key("/big/", ""), listing(&[], true));
    listings.store(key("/big/", "a"), None);
    assert_eq!(listings.plan(Path::new("/big/"), "a").read, None);
    listings.store(key("/gone/", ""), None);
    listings.retry_failed();
    assert_eq!(
        listings.plan(Path::new("/big/"), "a").read,
        Some(key("/big/", "a"))
    );
    assert_eq!(listings.plan(Path::new("/gone/"), "").read, None);
}

#[test]
fn a_failed_prefix_read_evicts_no_cached_listing() {
    let mut listings = DirListings::default();
    listings.store(key("/big/", ""), listing(&[], true));
    listings.store(key("/big/", "a"), listing(&["ab1"], true));
    for index in 0..MAX_CACHED - 2 {
        listings.store(key(&format!("/{index}/"), ""), listing(&[], false));
    }
    listings.store(key("/big/", "ab"), None);
    let plan = listings.plan(Path::new("/big/"), "ab");
    assert_eq!((names(&plan), plan.read.clone()), (vec!["ab1"], None));
}

#[test]
fn a_running_prefix_read_keeps_its_whole_listing_cached() {
    let mut listings = DirListings::default();
    listings.store(key("/big/", ""), listing(&[], true));
    assert!(listings.claim(&key("/big/", "z")));
    for index in 0..MAX_CACHED {
        listings.store(key(&format!("/{index}/"), ""), listing(&[], false));
    }
    listings.store(key("/big/", "z"), listing(&["z1"], false));
    let plan = listings.plan(Path::new("/big/"), "z");
    assert_eq!((names(&plan), plan.read.clone()), (vec!["z1"], None));
}
