//! Dot sets for the animated cat's 26-step transition cycle.

pub(super) const STARTING_DOTS: [&[i16]; 12] = [
    &[],
    &[6, 7, 15, 19],
    &[5, 8, 14, 16, 18, 20],
    &[4, 6, 7, 14, 17, 20],
    &[3, 5, 10, 11, 12, 14, 20],
    &[3, 5, 9, 13, 14, 16, 18, 20],
    &[3, 5, 8, 13, 17, 21],
    &[3, 6, 7, 8, 11, 14, 15, 16, 18, 19, 20],
    &[4, 5, 8, 12, 17, 19],
    &[6, 7, 8, 13, 18, 20],
    &[9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
    &[],
];

pub(super) struct Transition {
    pub remove: &'static [(i16, i16)],
    pub add: &'static [(i16, i16)],
}

const QUEUE_RIGHT_TO_MID_REMOVE: &[(i16, i16)] = &[
    (6, 1),
    (7, 1),
    (8, 2),
    (4, 3),
    (6, 3),
    (7, 3),
    (4, 8),
    (5, 8),
];
const QUEUE_RIGHT_TO_MID_ADD: &[(i16, i16)] = &[
    (4, 1),
    (3, 2),
    (3, 3),
    (5, 3),
    (5, 7),
    (3, 8),
    (4, 9),
    (5, 9),
];
const QUEUE_MID_TO_LEFT_REMOVE: &[(i16, i16)] = &[
    (4, 1),
    (5, 2),
    (3, 3),
    (5, 3),
    (5, 7),
    (3, 8),
    (4, 9),
    (5, 9),
];
const QUEUE_MID_TO_LEFT_ADD: &[(i16, i16)] = &[
    (1, 1),
    (2, 1),
    (0, 2),
    (1, 3),
    (2, 3),
    (4, 3),
    (4, 8),
    (5, 8),
];

const HEAD_RIGHT_REMOVE: &[(i16, i16)] = &[(16, 5), (18, 5), (17, 6)];
const HEAD_RIGHT_ADD: &[(i16, i16)] = &[(17, 5), (19, 5), (18, 6)];
const HEAD_DOWN_REMOVE: &[(i16, i16)] = &[
    (15, 1),
    (19, 1),
    (14, 2),
    (16, 2),
    (18, 2),
    (20, 2),
    (17, 3),
    (17, 5),
    (19, 5),
    (13, 6),
    (18, 6),
    (21, 6),
    (14, 7),
    (15, 7),
    (16, 7),
    (19, 7),
    (20, 7),
];
const HEAD_DOWN_ADD: &[(i16, i16)] = &[
    (15, 2),
    (19, 2),
    (16, 3),
    (18, 3),
    (17, 4),
    (14, 6),
    (17, 6),
    (19, 6),
    (20, 6),
    (13, 7),
    (18, 7),
    (21, 7),
    (14, 8),
    (15, 8),
    (16, 8),
    (18, 8),
    (20, 8),
];
const HEAD_UP_ADD: &[(i16, i16)] = &[
    (15, 1),
    (19, 1),
    (14, 2),
    (16, 2),
    (18, 2),
    (20, 2),
    (17, 3),
    (17, 5),
    (19, 5),
    (13, 6),
    (18, 6),
    (21, 6),
    (14, 7),
    (15, 7),
    (16, 7),
    (18, 7),
    (19, 7),
    (20, 7),
];

const BLINK_HIGH: &[(i16, i16)] = &[(16, 5), (18, 5)];
const BLINK_LOW: &[(i16, i16)] = &[(17, 6), (19, 6)];
const EMPTY: &[(i16, i16)] = &[];

pub(super) const TRANSITIONS: &[Transition] = &[
    Transition {
        remove: BLINK_HIGH,
        add: EMPTY,
    },
    Transition {
        remove: EMPTY,
        add: BLINK_HIGH,
    },
    Transition {
        remove: EMPTY,
        add: EMPTY,
    },
    Transition {
        remove: QUEUE_RIGHT_TO_MID_REMOVE,
        add: QUEUE_RIGHT_TO_MID_ADD,
    },
    Transition {
        remove: HEAD_RIGHT_REMOVE,
        add: HEAD_RIGHT_ADD,
    },
    Transition {
        remove: EMPTY,
        add: EMPTY,
    },
    Transition {
        remove: QUEUE_MID_TO_LEFT_REMOVE,
        add: QUEUE_MID_TO_LEFT_ADD,
    },
    Transition {
        remove: EMPTY,
        add: EMPTY,
    },
    Transition {
        remove: QUEUE_MID_TO_LEFT_ADD,
        add: QUEUE_MID_TO_LEFT_REMOVE,
    },
    Transition {
        remove: EMPTY,
        add: EMPTY,
    },
    Transition {
        remove: HEAD_DOWN_REMOVE,
        add: HEAD_DOWN_ADD,
    },
    Transition {
        remove: EMPTY,
        add: EMPTY,
    },
    Transition {
        remove: QUEUE_RIGHT_TO_MID_ADD,
        add: QUEUE_RIGHT_TO_MID_REMOVE,
    },
    Transition {
        remove: BLINK_LOW,
        add: EMPTY,
    },
    Transition {
        remove: EMPTY,
        add: BLINK_LOW,
    },
    Transition {
        remove: EMPTY,
        add: EMPTY,
    },
    Transition {
        remove: QUEUE_RIGHT_TO_MID_REMOVE,
        add: QUEUE_RIGHT_TO_MID_ADD,
    },
    Transition {
        remove: EMPTY,
        add: EMPTY,
    },
    Transition {
        remove: QUEUE_MID_TO_LEFT_REMOVE,
        add: QUEUE_MID_TO_LEFT_ADD,
    },
    Transition {
        remove: EMPTY,
        add: EMPTY,
    },
    Transition {
        remove: HEAD_DOWN_ADD,
        add: HEAD_UP_ADD,
    },
    Transition {
        remove: EMPTY,
        add: EMPTY,
    },
    Transition {
        remove: QUEUE_MID_TO_LEFT_ADD,
        add: QUEUE_MID_TO_LEFT_REMOVE,
    },
    Transition {
        remove: HEAD_RIGHT_ADD,
        add: HEAD_RIGHT_REMOVE,
    },
    Transition {
        remove: EMPTY,
        add: EMPTY,
    },
    Transition {
        remove: QUEUE_RIGHT_TO_MID_ADD,
        add: QUEUE_RIGHT_TO_MID_REMOVE,
    },
];

// ── LeChonk frames (from pixilart, 26×15, 26 transitions) ──

pub(super) const LECHONK_STARTING_DOTS: [&[i16]; 15] = [
    &[],
    &[],
    &[],
    &[],
    &[11, 12, 13, 14, 15, 17, 21],
    &[10, 16, 18, 20, 22],
    &[9, 16, 19, 22],
    &[8, 16, 22],
    &[6, 7, 8, 15, 18, 20, 23],
    &[5, 8, 15, 19, 23],
    &[4, 6, 7, 8, 16, 17, 18, 20, 21, 22],
    &[2, 3, 4, 6, 7, 14, 15, 18, 21],
    &[1, 5, 7, 16, 19, 22],
    &[
        2, 3, 4, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22,
    ],
    &[],
];

const LECHONK_T00_REMOVE: &[(i16, i16)] = &[(18, 8), (20, 8), (19, 9)];
const LECHONK_T00_ADD: &[(i16, i16)] = &[];

const LECHONK_T01_REMOVE: &[(i16, i16)] = &[];
const LECHONK_T01_ADD: &[(i16, i16)] = &[(18, 8), (20, 8), (19, 9)];

const LECHONK_T02_REMOVE: &[(i16, i16)] = &[];
const LECHONK_T02_ADD: &[(i16, i16)] = &[];

const LECHONK_T03_REMOVE: &[(i16, i16)] = &[
    (5, 9),
    (2, 11),
    (3, 11),
    (4, 11),
    (6, 11),
    (1, 12),
    (5, 12),
    (2, 13),
    (3, 13),
    (4, 13),
];
const LECHONK_T03_ADD: &[(i16, i16)] = &[
    (1, 8),
    (2, 8),
    (3, 8),
    (4, 8),
    (5, 8),
    (0, 9),
    (1, 10),
    (2, 10),
    (3, 10),
    (5, 10),
];

const LECHONK_T04_REMOVE: &[(i16, i16)] = &[(18, 8), (20, 8), (19, 9)];
const LECHONK_T04_ADD: &[(i16, i16)] = &[(19, 8), (21, 8), (20, 9)];

const LECHONK_T05_REMOVE: &[(i16, i16)] = &[];
const LECHONK_T05_ADD: &[(i16, i16)] = &[];

const LECHONK_T06_REMOVE: &[(i16, i16)] = &[
    (1, 8),
    (2, 8),
    (3, 8),
    (5, 8),
    (0, 9),
    (1, 10),
    (2, 10),
    (3, 10),
    (4, 10),
    (5, 10),
];
const LECHONK_T06_ADD: &[(i16, i16)] = &[
    (2, 5),
    (3, 5),
    (4, 5),
    (1, 6),
    (5, 6),
    (2, 7),
    (3, 7),
    (4, 7),
    (6, 7),
    (5, 9),
];

const LECHONK_T07_REMOVE: &[(i16, i16)] = &[];
const LECHONK_T07_ADD: &[(i16, i16)] = &[];

const LECHONK_T08_REMOVE: &[(i16, i16)] = &[
    (2, 5),
    (3, 5),
    (4, 5),
    (1, 6),
    (5, 6),
    (2, 7),
    (3, 7),
    (4, 7),
    (6, 7),
    (5, 9),
];
const LECHONK_T08_ADD: &[(i16, i16)] = &[
    (1, 8),
    (2, 8),
    (3, 8),
    (5, 8),
    (0, 9),
    (1, 10),
    (2, 10),
    (3, 10),
    (4, 10),
    (5, 10),
];

const LECHONK_T09_REMOVE: &[(i16, i16)] = &[];
const LECHONK_T09_ADD: &[(i16, i16)] = &[];

const LECHONK_T10_REMOVE: &[(i16, i16)] = &[
    (17, 4),
    (21, 4),
    (18, 5),
    (20, 5),
    (22, 5),
    (19, 6),
    (15, 8),
    (19, 8),
    (21, 8),
    (23, 8),
    (20, 9),
    (16, 10),
    (17, 10),
    (18, 10),
    (21, 10),
    (22, 10),
];
const LECHONK_T10_ADD: &[(i16, i16)] = &[
    (17, 5),
    (21, 5),
    (18, 6),
    (20, 6),
    (19, 7),
    (16, 8),
    (22, 8),
    (19, 9),
    (21, 9),
    (15, 10),
    (23, 10),
    (16, 11),
    (17, 11),
    (20, 11),
    (22, 11),
];

const LECHONK_T11_REMOVE: &[(i16, i16)] = &[];
const LECHONK_T11_ADD: &[(i16, i16)] = &[];

const LECHONK_T12_REMOVE: &[(i16, i16)] = &[
    (1, 8),
    (2, 8),
    (3, 8),
    (4, 8),
    (5, 8),
    (0, 9),
    (1, 10),
    (2, 10),
    (3, 10),
    (5, 10),
];
const LECHONK_T12_ADD: &[(i16, i16)] = &[
    (5, 9),
    (2, 11),
    (3, 11),
    (4, 11),
    (6, 11),
    (1, 12),
    (5, 12),
    (2, 13),
    (3, 13),
    (4, 13),
];

const LECHONK_T13_REMOVE: &[(i16, i16)] = &[(19, 9), (21, 9), (20, 10)];
const LECHONK_T13_ADD: &[(i16, i16)] = &[];

const LECHONK_T14_REMOVE: &[(i16, i16)] = &[];
const LECHONK_T14_ADD: &[(i16, i16)] = &[(19, 9), (21, 9), (20, 10)];

const LECHONK_T15_REMOVE: &[(i16, i16)] = &[];
const LECHONK_T15_ADD: &[(i16, i16)] = &[];

const LECHONK_T16_REMOVE: &[(i16, i16)] = &[
    (5, 9),
    (2, 11),
    (3, 11),
    (4, 11),
    (6, 11),
    (1, 12),
    (5, 12),
    (2, 13),
    (3, 13),
    (4, 13),
];
const LECHONK_T16_ADD: &[(i16, i16)] = &[
    (1, 8),
    (2, 8),
    (3, 8),
    (4, 8),
    (5, 8),
    (0, 9),
    (1, 10),
    (2, 10),
    (3, 10),
    (5, 10),
];

const LECHONK_T17_REMOVE: &[(i16, i16)] = &[];
const LECHONK_T17_ADD: &[(i16, i16)] = &[];

const LECHONK_T18_REMOVE: &[(i16, i16)] = &[
    (1, 8),
    (2, 8),
    (3, 8),
    (5, 8),
    (0, 9),
    (1, 10),
    (2, 10),
    (3, 10),
    (4, 10),
    (5, 10),
];
const LECHONK_T18_ADD: &[(i16, i16)] = &[
    (2, 5),
    (3, 5),
    (4, 5),
    (1, 6),
    (5, 6),
    (2, 7),
    (3, 7),
    (4, 7),
    (6, 7),
    (5, 9),
];

const LECHONK_T19_REMOVE: &[(i16, i16)] = &[];
const LECHONK_T19_ADD: &[(i16, i16)] = &[];

const LECHONK_T20_REMOVE: &[(i16, i16)] = &[
    (17, 5),
    (21, 5),
    (18, 6),
    (20, 6),
    (19, 7),
    (16, 8),
    (22, 8),
    (19, 9),
    (21, 9),
    (15, 10),
    (23, 10),
    (16, 11),
    (17, 11),
    (20, 11),
    (22, 11),
];
const LECHONK_T20_ADD: &[(i16, i16)] = &[
    (17, 4),
    (21, 4),
    (18, 5),
    (20, 5),
    (22, 5),
    (19, 6),
    (15, 8),
    (19, 8),
    (21, 8),
    (23, 8),
    (20, 9),
    (16, 10),
    (17, 10),
    (18, 10),
    (21, 10),
    (22, 10),
];

const LECHONK_T21_REMOVE: &[(i16, i16)] = &[];
const LECHONK_T21_ADD: &[(i16, i16)] = &[];

const LECHONK_T22_REMOVE: &[(i16, i16)] = &[
    (2, 5),
    (3, 5),
    (4, 5),
    (1, 6),
    (5, 6),
    (2, 7),
    (3, 7),
    (4, 7),
    (6, 7),
    (5, 9),
];
const LECHONK_T22_ADD: &[(i16, i16)] = &[
    (1, 8),
    (2, 8),
    (3, 8),
    (5, 8),
    (0, 9),
    (1, 10),
    (2, 10),
    (3, 10),
    (4, 10),
    (5, 10),
];

const LECHONK_T23_REMOVE: &[(i16, i16)] = &[(19, 8), (21, 8), (20, 9)];
const LECHONK_T23_ADD: &[(i16, i16)] = &[(18, 8), (20, 8), (19, 9)];

const LECHONK_T24_REMOVE: &[(i16, i16)] = &[];
const LECHONK_T24_ADD: &[(i16, i16)] = &[];

const LECHONK_T25_REMOVE: &[(i16, i16)] = &[
    (1, 8),
    (2, 8),
    (3, 8),
    (4, 8),
    (5, 8),
    (0, 9),
    (1, 10),
    (2, 10),
    (3, 10),
    (5, 10),
];
const LECHONK_T25_ADD: &[(i16, i16)] = &[
    (5, 9),
    (2, 11),
    (3, 11),
    (4, 11),
    (6, 11),
    (1, 12),
    (5, 12),
    (2, 13),
    (3, 13),
    (4, 13),
];

pub(super) const LECHONK_TRANSITIONS: &[Transition] = &[
    Transition {
        remove: LECHONK_T00_REMOVE,
        add: LECHONK_T00_ADD,
    },
    Transition {
        remove: LECHONK_T01_REMOVE,
        add: LECHONK_T01_ADD,
    },
    Transition {
        remove: LECHONK_T02_REMOVE,
        add: LECHONK_T02_ADD,
    },
    Transition {
        remove: LECHONK_T03_REMOVE,
        add: LECHONK_T03_ADD,
    },
    Transition {
        remove: LECHONK_T04_REMOVE,
        add: LECHONK_T04_ADD,
    },
    Transition {
        remove: LECHONK_T05_REMOVE,
        add: LECHONK_T05_ADD,
    },
    Transition {
        remove: LECHONK_T06_REMOVE,
        add: LECHONK_T06_ADD,
    },
    Transition {
        remove: LECHONK_T07_REMOVE,
        add: LECHONK_T07_ADD,
    },
    Transition {
        remove: LECHONK_T08_REMOVE,
        add: LECHONK_T08_ADD,
    },
    Transition {
        remove: LECHONK_T09_REMOVE,
        add: LECHONK_T09_ADD,
    },
    Transition {
        remove: LECHONK_T10_REMOVE,
        add: LECHONK_T10_ADD,
    },
    Transition {
        remove: LECHONK_T11_REMOVE,
        add: LECHONK_T11_ADD,
    },
    Transition {
        remove: LECHONK_T12_REMOVE,
        add: LECHONK_T12_ADD,
    },
    Transition {
        remove: LECHONK_T13_REMOVE,
        add: LECHONK_T13_ADD,
    },
    Transition {
        remove: LECHONK_T14_REMOVE,
        add: LECHONK_T14_ADD,
    },
    Transition {
        remove: LECHONK_T15_REMOVE,
        add: LECHONK_T15_ADD,
    },
    Transition {
        remove: LECHONK_T16_REMOVE,
        add: LECHONK_T16_ADD,
    },
    Transition {
        remove: LECHONK_T17_REMOVE,
        add: LECHONK_T17_ADD,
    },
    Transition {
        remove: LECHONK_T18_REMOVE,
        add: LECHONK_T18_ADD,
    },
    Transition {
        remove: LECHONK_T19_REMOVE,
        add: LECHONK_T19_ADD,
    },
    Transition {
        remove: LECHONK_T20_REMOVE,
        add: LECHONK_T20_ADD,
    },
    Transition {
        remove: LECHONK_T21_REMOVE,
        add: LECHONK_T21_ADD,
    },
    Transition {
        remove: LECHONK_T22_REMOVE,
        add: LECHONK_T22_ADD,
    },
    Transition {
        remove: LECHONK_T23_REMOVE,
        add: LECHONK_T23_ADD,
    },
    Transition {
        remove: LECHONK_T24_REMOVE,
        add: LECHONK_T24_ADD,
    },
    Transition {
        remove: LECHONK_T25_REMOVE,
        add: LECHONK_T25_ADD,
    },
];
