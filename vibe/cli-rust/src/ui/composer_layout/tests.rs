use super::*;

#[test]
fn capped_height_counts_like_the_full_layout_up_to_the_cap() {
    let bodies = [
        "",
        "one line",
        "a\n\nb\n",
        "a long sentence that wraps over several rows of a narrow composer box",
        "tabs\tand\twide 日本語 text\nthen a second line that also wraps around",
    ];
    for body in bodies {
        for marker in ["> ", "! "] {
            for width in [1, 4, 12, 40] {
                let full = ComposerLayout::new(body, marker, 0, width).height();
                for cap in [1, 2, 3, 5, u16::MAX] {
                    let capped = capped_height(body, marker, width, cap);
                    assert_eq!(
                        capped,
                        full.min(cap),
                        "{body:?} {marker:?} w={width} cap={cap}"
                    );
                }
            }
        }
    }
}
